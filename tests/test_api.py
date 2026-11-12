"""Automated tests for the LoTUS-BF backend.

Run:   pip install pytest && pytest -q tests/
DB tests run only when DB_URL is set (e.g. the synthetic DB from tests/seed_db.py
or the real course database). Parser tests need no database.
"""
import math
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as lotus  # noqa: E402

# ---------------------------------------------------------------- parser (no DB)
PARSE_CASES = [
    ("emotion", "emotion"),
    ("pain OR emotion", "(pain OR emotion)"),
    ("(pain OR emotion) AND memory", "((pain OR emotion) AND memory)"),
    ("pain OR emotion AND memory", "(pain OR (emotion AND memory))"),      # AND binds tighter
    ("reward NOT emotion", "(reward AND NOT emotion)"),                    # implicit AND
    ("[-22,-4,-18] OR memory", "([-22,-4,-18] OR memory)"),
    ("[ -22 , -4 , -18 ] not pain", "([-22,-4,-18] AND NOT pain)"),        # spaces, lowercase op
    ('"default mode" [0,-52,26]', '("default mode" AND [0,-52,26])'),
    ("orbitofrontal", "orbitofrontal"),                                    # was split into 'or bitofrontal'
    ("orientation notion android", "((orientation AND notion) AND android)"),
    ("NOT(pain)", "NOT pain"),
]


@pytest.mark.parametrize("q,expected", PARSE_CASES)
def test_parse_canonical(q, expected):
    assert lotus.ast_to_str(lotus.parse_query(q)) == expected


@pytest.mark.parametrize("q", ["", "emotion AND", "(pain", "pain)", "[1,2]", "[1,2,3", "\"abc", "AND pain", "NOT"])
def test_parse_errors(q):
    with pytest.raises(lotus.QueryError):
        lotus.parse_query(q)


def test_tsquery_sanitised():
    assert lotus.term_to_tsquery("working memory") == "working <-> memory"
    assert lotus.term_to_tsquery("emot*") == "emot:*"
    assert lotus.term_to_tsquery("a&b|c:!") == "a <-> b <-> c"
    with pytest.raises(lotus.QueryError):
        lotus.term_to_tsquery("&&&")


# ---------------------------------------------------------------- API (needs DB)
needs_db = pytest.mark.skipif(not os.environ.get("DB_URL"), reason="DB_URL not set")


@pytest.fixture(scope="module")
def client():
    return lotus.create_app().test_client()


def ids(client, q, **params):
    r = client.get(f"/query/{q}/studies", query_string=params)
    assert r.status_code == 200, r.get_json()
    return {row["study_id"] for row in r.get_json()["results"]}


@pytest.fixture(scope="module")
def all_coords():
    from sqlalchemy import text
    with lotus.get_engine().connect() as c:
        rows = c.execute(text("SELECT study_id, ST_X(geom) x, ST_Y(geom) y, ST_Z(geom) z FROM ns.coordinates")).all()
        universe = {r[0] for r in c.execute(text("SELECT study_id FROM ns.metadata")).all()}
    return rows, universe


def near(all_coords, p, r=6.0):
    rows, _ = all_coords
    return {sid for sid, x, y, z in rows if math.dist((x, y, z), p) < r}


@needs_db
def test_single_terms(client):
    # ground truth from the hand-built studies in seed_db.py
    assert ids(client, "emotion") == {"90000002", "90000004", "90000008"}
    assert ids(client, "pain") == {"90000004", "90000006"}
    assert ids(client, "memory") == {"90000003", "90000006"}
    assert ids(client, "orbitofrontal") == {"90000001"}
    assert ids(client, "orientation") == {"90000007"}
    assert ids(client, '"default mode"') == {"90000005"}


@needs_db
@pytest.mark.parametrize("p", [(-22, -4, -18), (-2, 50, -6), (30, -60, 50), (0, -52, 26)])
@pytest.mark.parametrize("r", [3.0, 6.0, 12.0])
def test_location_matches_euclidean(client, all_coords, p, r):
    q = "[" + ",".join(str(v) for v in p) + "]"
    assert ids(client, q, r=r) == near(all_coords, p, r)


@needs_db
def test_boolean_algebra(client, all_coords):
    _, U = all_coords
    E, P, M, R = (ids(client, t) for t in ("emotion", "pain", "memory", "reward"))
    A = near(all_coords, (-22, -4, -18))
    cases = {
        "pain OR emotion": P | E,
        "(pain OR emotion) AND memory": (P | E) & M,
        "pain OR emotion AND memory": P | (E & M),
        "reward NOT emotion": R - E,
        "NOT emotion": U - E,
        "[-22,-4,-18] OR memory": A | M,
        "[-22,-4,-18] AND emotion": A & E,
        "[-22,-4,-18] NOT pain": A - P,
        "([-22,-4,-18] OR reward) AND NOT pain": (A | R) - P,
    }
    for q, expected in cases.items():
        assert ids(client, q) == expected, q


@needs_db
def test_errors_are_json_400(client):
    for q in ["emotion AND", "(pain", "[1,2]", "&&&"]:
        r = client.get(f"/query/{q}/studies")
        assert r.status_code == 400 and "error" in r.get_json(), q


@needs_db
def test_locations_and_nii(client, all_coords):
    import gzip, io
    import nibabel as nib
    r = client.get("/query/emotion/locations").get_json()
    assert r["study_counts"] == 3
    nii = client.get("/query/emotion/nii?fwhm=0")
    assert nii.status_code == 200
    img = nib.Nifti1Image.from_bytes(gzip.decompress(nii.data))
    assert img.shape == (91, 109, 91)
    # with fwhm=0 the map is a pure count of foci → total equals number of locations
    assert int(img.get_fdata().sum()) == r["location_counts"]
    # peak of amygdala study [-22,-4,-18] lands on the right voxel
    i, j, k = (90 - (-22)) // 2, (-4 + 126) // 2, (-18 + 72) // 2   # MNI → voxel
    assert img.get_fdata()[i, j, k] >= 2   # studies 2 and 4 both report it


@needs_db
def test_terms_endpoints(client):
    t = client.get("/terms").get_json()
    assert "emotion" in t["terms"] and t["count"] == len(t["terms"])
    rel = client.get("/terms/emotion").get_json()["related"]
    assert any(d["term"] == "reward" for d in rel)


@needs_db
def test_terms_from_annotations_terms_table(client, monkeypatch):
    """HW08 databases may only have ns.annotations_terms (no --enable-json)."""
    json_terms = client.get("/terms").get_json()["terms"]
    json_rel = client.get("/terms/emotion").get_json()["related"]
    monkeypatch.setattr(lotus, "_term_pairs_sql", lambda conn: "SELECT study_id, term FROM ns.annotations_terms")
    assert client.get("/terms").get_json()["terms"] == json_terms
    assert client.get("/terms/emotion").get_json()["related"] == json_rel
