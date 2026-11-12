"""
LoTUS-BF backend — Location-or-Term Unified Search for Brain Functions.

Endpoints (unchanged URLs, so the existing frontend/proxy keep working):
  GET /                                   health check
  GET /help                               API documentation (JSON)
  GET /terms                              all terms
  GET /terms/<term>                       related terms (co-occurrence / Jaccard)
  GET /query/<q>/studies                  matching studies   (?r, ?limit, ?offset)
  GET /query/<q>/locations                coordinates of matching studies
  GET /query/<q>/nii                      meta-analytic map (NIfTI, MNI152 2 mm)
  GET /query/<q>/parse                    NEW: how the query was interpreted

Query language
  * terms:        emotion   "working memory"   emot*  (prefix)
  * locations:    [x,y,z]  in MNI mm, matched within radius r (default 6 mm)
  * operators:    AND  OR  NOT  ( )     — case-insensitive; adjacency means AND
  * precedence:   NOT > AND > OR
  Terms and locations can be freely mixed in ONE boolean expression, e.g.
      ([-22,-4,-18] OR amygdala) AND NOT pain

Key changes vs. the original version (see Validation Report):
  1. One recursive-descent parser builds a single AST for terms *and* locations,
     so OR / parentheses / NOT work across both kinds of operands.
     (Before: the first OR was silently turned into AND, parentheses around
     terms were dropped, and "[x,y,z] OR term" was computed as an intersection.)
  2. Words that merely *start* with and/or/not (orbitofrontal, orientation,
     notion, android …) are no longer split into operators (was: HTTP 500).
  3. All user values are passed as bound SQL parameters; tsquery special
     characters are stripped from terms.
  4. Malformed queries return HTTP 400 with a readable JSON error instead of
     an HTML 500 page.
  5. DB URL is read from the environment (DB_URL / DATABASE_URL) instead of
     being hard-coded in source.
  6. Parameters are validated/clamped (r, fwhm, limit, offset); 'uniform'
     kernel is implemented; the module exposes `app` and can be run directly.
"""
from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, Flask, jsonify, request, send_file
from flask_cors import CORS
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------
_engine = None


def get_engine():
    """Create (once) the SQLAlchemy engine from DB_URL / DATABASE_URL."""
    global _engine
    if _engine is not None:
        return _engine
    db_url = os.environ.get("DB_URL") or os.environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError(
            "Missing DB_URL (or DATABASE_URL) environment variable, e.g. "
            "export DB_URL='postgresql://user:password@host:5432/dbname'")
    if db_url.startswith("postgres://"):
        db_url = "postgresql+psycopg2://" + db_url[len("postgres://"):]
    elif db_url.startswith("postgresql://"):
        db_url = "postgresql+psycopg2://" + db_url[len("postgresql://"):]
    elif "://" not in db_url:
        db_url = "postgresql+psycopg2://" + db_url
    _engine = create_engine(db_url, pool_pre_ping=True, future=True)
    return _engine


PREFIX = "terms_abstract_tfidf__"

# Safety limits for request parameters
R_DEFAULT, R_MAX = 6.0, 30.0
FWHM_DEFAULT, FWHM_MAX = 10.0, 40.0
LIMIT_MAX = 10000
MAX_QUERY_OPERANDS = 64


class QueryError(ValueError):
    """Raised for user-facing query syntax errors (→ HTTP 400)."""


# --------------------------------------------------------------------------
# Query language: tokenizer → parser (AST) → SQL
# --------------------------------------------------------------------------
@dataclass
class Tok:
    kind: str   # 'LP' 'RP' 'AND' 'OR' 'NOT' 'TERM' 'POINT'
    value: Any
    pos: int


_TOKEN_RE = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<point>\[[^\]]*\])
  | (?P<dq>"[^"]*")
  | (?P<sq>'[^']*')
  | (?P<lp>\()
  | (?P<rp>\))
  | (?P<word>[^\s()\[\]"]+)
    """,
    re.VERBOSE,
)
_OPS = {"AND", "OR", "NOT"}
_NUM = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"
_POINT_RE = re.compile(rf"^\[\s*({_NUM})\s*,\s*({_NUM})\s*,\s*({_NUM})\s*\]$")


def tokenize(qs: str) -> List[Tok]:
    s = (qs or "").strip()
    toks: List[Tok] = []
    i = 0
    while i < len(s):
        m = _TOKEN_RE.match(s, i)
        if not m:
            ch = s[i]
            if ch == "[":
                raise QueryError(f"Unclosed '[' at position {i}. Locations look like [x,y,z].")
            if ch in "\"'":
                raise QueryError(f"Unclosed quote at position {i}.")
            raise QueryError(f"Unexpected character {ch!r} at position {i}.")
        kind = m.lastgroup
        val = m.group(0)
        if kind == "point":
            pm = _POINT_RE.match(val)
            if not pm:
                raise QueryError(f"Invalid coordinate {val}. Use three numbers, e.g. [-22,-4,18].")
            toks.append(Tok("POINT", tuple(float(v) for v in pm.groups()), i))
        elif kind in ("dq", "sq"):
            inner = val[1:-1].strip()
            if inner:
                toks.append(Tok("TERM", inner, i))
        elif kind == "lp":
            toks.append(Tok("LP", "(", i))
        elif kind == "rp":
            toks.append(Tok("RP", ")", i))
        elif kind == "word":
            if val.upper() in _OPS:
                toks.append(Tok(val.upper(), val.upper(), i))
            else:
                toks.append(Tok("TERM", val, i))
        i = m.end()
    return toks


# AST nodes are tuples:  ('term', str) | ('point', (x,y,z)) | ('not', n) | ('and', a, b) | ('or', a, b)
class _Parser:
    def __init__(self, toks: List[Tok]):
        self.toks, self.i = toks, 0

    def peek(self) -> Optional[Tok]:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def take(self) -> Tok:
        t = self.toks[self.i]
        self.i += 1
        return t

    def parse(self):
        if not self.toks:
            raise QueryError("Empty query. Enter a term (e.g. emotion) or a location (e.g. [-22,-4,18]).")
        node = self.parse_or()
        t = self.peek()
        if t is not None:
            if t.kind == "RP":
                raise QueryError(f"Unmatched ')' at position {t.pos}.")
            raise QueryError(f"Unexpected {t.value!r} at position {t.pos}.")
        return node

    def parse_or(self):
        left = self.parse_and()
        while (t := self.peek()) is not None and t.kind == "OR":
            self.take()
            left = ("or", left, self.parse_and())
        return left

    def parse_and(self):
        left = self.parse_unary()
        while (t := self.peek()) is not None:
            if t.kind == "AND":
                self.take()
            elif t.kind not in ("TERM", "POINT", "LP", "NOT"):
                break                       # implicit AND only before an operand
            left = ("and", left, self.parse_unary())
        return left

    def parse_unary(self):
        t = self.peek()
        if t is not None and t.kind == "NOT":
            self.take()
            return ("not", self.parse_unary())
        return self.parse_primary()

    def parse_primary(self):
        t = self.peek()
        if t is None:
            raise QueryError("Query is incomplete: a term or location is expected at the end.")
        if t.kind == "LP":
            self.take()
            inner = self.parse_or()
            close = self.peek()
            if close is None or close.kind != "RP":
                raise QueryError(f"Missing ')' for '(' at position {t.pos}.")
            self.take()
            return inner
        if t.kind == "TERM":
            self.take()
            return ("term", t.value)
        if t.kind == "POINT":
            self.take()
            return ("point", t.value)
        raise QueryError(f"Unexpected {t.value!r} at position {t.pos}; expected a term or location.")


def parse_query(qs: str):
    return _Parser(tokenize(qs)).parse()


def ast_to_str(node) -> str:
    """Fully-parenthesised canonical form (shown to users as 'interpreted as')."""
    k = node[0]
    if k == "term":
        v = node[1]
        return f'"{v}"' if " " in v else v
    if k == "point":
        return "[" + ",".join(f"{c:g}" for c in node[1]) + "]"
    if k == "not":
        return f"NOT {ast_to_str(node[1])}"
    return f"({ast_to_str(node[1])} {k.upper()} {ast_to_str(node[2])})"


def term_to_tsquery(term: str) -> str:
    """Turn one user term/phrase into a safe tsquery string.

    Words are joined with the phrase operator (<->), so "working memory" and
    "self-referential" match adjacent words. A trailing * means prefix match.
    All tsquery metacharacters (&|!:()<> etc.) are removed.
    """
    prefix = term.endswith("*")
    words = re.findall(r"[A-Za-z0-9]+", term)
    if not words:
        raise QueryError(f"Term {term!r} has no searchable letters or digits.")
    if prefix:
        words[-1] += ":*"
    return " <-> ".join(words)


@dataclass
class CompiledQuery:
    ast: Any
    where_sql: str           # boolean SQL over alias m (ns.metadata)
    cte_sql: str             # optional "pts AS (...), hits AS (...)" (no WITH keyword)
    params: Dict[str, Any]
    terms: List[str]
    points: List[Tuple[float, float, float]]


def compile_query(qs: str, r: float) -> CompiledQuery:
    ast = parse_query(qs)
    params: Dict[str, Any] = {}
    terms: List[str] = []
    points: List[Tuple[float, float, float]] = []
    point_idx: Dict[Tuple[float, float, float], int] = {}

    def emit(node) -> str:
        k = node[0]
        if k == "term":
            n = len(terms)
            terms.append(node[1])
            params[f"q{n}"] = term_to_tsquery(node[1])
            return (f"COALESCE(m.fts @@ to_tsquery('english', :q{n})"
                    f" OR to_tsvector('english', COALESCE(m.title,'')) @@ to_tsquery('english', :q{n}), FALSE)")
        if k == "point":
            p = node[1]
            if p not in point_idx:
                point_idx[p] = len(points) + 1
                points.append(p)
            idx = point_idx[p]
            return f"EXISTS (SELECT 1 FROM hits h WHERE h.study_id = m.study_id AND h.idx = {idx})"
        if k == "not":
            return f"(NOT {emit(node[1])})"
        return f"({emit(node[1])} {k.upper()} {emit(node[2])})"

    where_sql = emit(ast)
    if len(terms) + len(points) > MAX_QUERY_OPERANDS:
        raise QueryError(f"Query too long (max {MAX_QUERY_OPERANDS} terms/locations).")

    cte_sql = ""
    if points:
        values = []
        for i, (x, y, z) in enumerate(points, start=1):
            params[f"x{i}"], params[f"y{i}"], params[f"z{i}"] = x, y, z
            values.append(f"({i}, ST_MakePoint(CAST(:x{i} AS float8), CAST(:y{i} AS float8), CAST(:z{i} AS float8)))")
        params["r"] = r
        cte_sql = f"""
          pts(idx, geom) AS (VALUES {", ".join(values)}),
          hits AS (
            SELECT DISTINCT c.study_id, p.idx
            FROM ns.coordinates c
            JOIN pts p ON ST_3DDistance(ST_SetSRID(c.geom, 0), ST_SetSRID(p.geom, 0)) < :r
          )"""
    return CompiledQuery(ast, where_sql, cte_sql, params, terms, points)


def _with(cq: CompiledQuery, extra: str = "") -> str:
    parts = [p for p in (cq.cte_sql.strip(), extra.strip()) if p]
    return ("WITH " + ",\n".join(parts)) if parts else ""


# --------------------------------------------------------------------------
# Request parameter helpers
# --------------------------------------------------------------------------
def _float_arg(name: str, default: float, lo: float, hi: float) -> float:
    raw = request.args.get(name)
    if raw is None or raw == "":
        return default
    try:
        v = float(raw)
    except ValueError:
        raise QueryError(f"Parameter {name} must be a number (got {raw!r}).")
    if v != v:  # NaN
        raise QueryError(f"Parameter {name} must be a number.")
    return max(lo, min(hi, v))


def _limit_offset() -> Tuple[Optional[int], int]:
    """limit only applies if given; offset defaults to 0. Invalid values are ignored."""
    limit: Optional[int] = None
    raw = request.args.get("limit")
    if raw not in (None, ""):
        try:
            limit = max(1, min(int(raw), LIMIT_MAX))
        except ValueError:
            limit = None
    try:
        offset = max(0, int(request.args.get("offset", 0)))
    except ValueError:
        offset = 0
    return limit, offset


def _limit_clause(limit: Optional[int], offset: int) -> Tuple[str, Dict[str, Any]]:
    clause, p = "", {}
    if limit is not None:
        clause, p["limit"] = "LIMIT :limit", limit
    if offset:
        clause += " OFFSET :offset"
        p["offset"] = offset
    return clause, p


def _r_arg() -> float:
    return _float_arg("r", R_DEFAULT, 0.5, R_MAX)


# --------------------------------------------------------------------------
# NIfTI map generation
# --------------------------------------------------------------------------
MNI_SHAPE = (91, 109, 91)
MNI_ZOOMS = (2.0, 2.0, 2.0)
MNI_AFFINE = [
    [-2.0, 0.0, 0.0, 90.0],
    [0.0, 2.0, 0.0, -126.0],
    [0.0, 0.0, 2.0, -72.0],
    [0.0, 0.0, 0.0, 1.0],
]


def _make_nii_from_coords(coords_by_study, fwhm: float = 10.0, kernel: str = "gauss"):
    """Accumulate foci on the MNI152 2 mm grid and smooth.

    Units: with the Gaussian kernel the map is scaled so a single isolated focus
    has a peak of 1; with the uniform kernel each voxel holds the number of foci
    inside the box. Either way a value of 3 ≈ "three foci reported nearby".

    Returns (path_to_nii_gz, n_foci_inside_grid). Both qform and sform are set
    to the MNI152 2 mm affine, matching static/mni_2mm.nii.gz in the frontend.
    """
    import nibabel as nib
    import numpy as np
    from scipy.ndimage import gaussian_filter, uniform_filter

    aff = np.array(MNI_AFFINE, dtype=float)
    inv = np.linalg.inv(aff)
    vol = np.zeros(MNI_SHAPE, dtype=np.float32)
    n = 0
    for pts in coords_by_study.values():
        for (x, y, z) in pts:
            i, j, k = np.round(inv @ np.array([x, y, z, 1.0]))[:3].astype(int)
            if 0 <= i < MNI_SHAPE[0] and 0 <= j < MNI_SHAPE[1] and 0 <= k < MNI_SHAPE[2]:
                vol[i, j, k] += 1.0
                n += 1

    if fwhm > 0:
        if kernel == "uniform":
            size = max(1, int(round(fwhm / MNI_ZOOMS[0])))
            # uniform_filter averages; multiply by box volume to keep total mass
            vol = uniform_filter(vol, size=size, mode="constant") * (size ** 3)
        else:
            sigma_mm = fwhm / (2.0 * np.sqrt(2.0 * np.log(2.0)))
            sigma = [sigma_mm / z for z in MNI_ZOOMS]
            vol = gaussian_filter(vol, sigma=sigma, mode="constant", truncate=3.0)
            # rescale so one isolated focus has peak 1 → values read as "number of nearby foci"
            delta = np.zeros((41, 41, 41), dtype=np.float32)
            delta[20, 20, 20] = 1.0
            vol /= float(gaussian_filter(delta, sigma=sigma, mode="constant", truncate=3.0).max())

    img = nib.Nifti1Image(vol.astype(np.float32), aff)
    hdr = img.header
    hdr.set_xyzt_units("mm", "sec")
    hdr.set_zooms(MNI_ZOOMS)
    img.set_sform(aff, code=4)
    img.set_qform(aff, code=4)
    outdir = tempfile.mkdtemp(prefix="lotus_nii_")
    path = os.path.join(outdir, "query_mixed_map.nii.gz")
    nib.save(img, path)
    return path, n


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------
bp = Blueprint("lotus_api", __name__)


@bp.errorhandler(QueryError)
def _query_error(e):
    return jsonify({"error": str(e)}), 400


@bp.errorhandler(DBAPIError)
def _db_error(e):
    msg = str(getattr(e, "orig", e)).strip().splitlines()[0] if str(getattr(e, "orig", e)).strip() else "database error"
    return jsonify({"error": f"Database could not evaluate the query: {msg}"}), 400


def _set_search_path(conn):
    conn.execute(text("SET search_path TO ns, public;"))


@bp.get("/")
def index():
    return "Server working!"


@bp.get("/help")
def help():  # noqa: A001 - keep URL name
    return jsonify(_lotus_help_payload())


@bp.get("/debug/nii_meta")
def debug_nii_meta():
    return jsonify({"shape": MNI_SHAPE, "zooms": MNI_ZOOMS, "qform_code": 4, "sform_code": 4,
                    "affine": MNI_AFFINE}), 200


def _term_pairs_sql(conn) -> str:
    """SQL yielding (study_id, term) pairs, from whichever annotation table exists.

    The HW08 loader (create_db.py) builds ns.annotations_terms (sparse, one row per
    study/term) and only builds ns.annotations_json with --enable-json, so both
    layouts are supported.
    """
    if conn.execute(text("SELECT to_regclass('ns.annotations_json')")).scalar():
        return ("SELECT aj.study_id, k.term FROM ns.annotations_json aj "
                "JOIN LATERAL jsonb_object_keys(aj.terms) AS k(term) ON TRUE")
    if conn.execute(text("SELECT to_regclass('ns.annotations_terms')")).scalar():
        return "SELECT study_id, term FROM ns.annotations_terms"
    raise QueryError("No annotation table found (expected ns.annotations_json or ns.annotations_terms).")


_STRIP_PREFIX = ("CASE WHEN {c} LIKE 'terms\\_abstract\\_tfidf\\_\\_%' "
                 "THEN SUBSTRING({c} FROM char_length('terms_abstract_tfidf__')+1) ELSE {c} END")


@bp.get("/terms")
@bp.get("/terms/<path:term>")
def terms(term: Optional[str] = None):
    """GET /terms → all terms;  GET /terms/<term> → related terms (Jaccard)."""
    eng = get_engine()
    with eng.begin() as conn:
        _set_search_path(conn)
        pairs = _term_pairs_sql(conn)
        if not term:
            rows = conn.execute(text(f"""
                SELECT DISTINCT {_STRIP_PREFIX.format(c="p.term")} AS term
                FROM (SELECT DISTINCT term FROM ({pairs}) x) p
                ORDER BY term
            """)).fetchall()
            out = [r.term for r in rows if r.term and not re.match(r"^\d", r.term)]
            return jsonify({"terms": out, "count": len(out)}), 200

        limit, offset = _limit_offset()
        if limit is None:
            limit = 50          # related-terms list: sensible default instead of thousands of rows
        lc, lp = _limit_clause(limit, offset)
        rows = conn.execute(text(f"""
            WITH st AS ({pairs}),
            a AS (SELECT DISTINCT study_id FROM st WHERE term IN (:t, :tp)),
            co AS (
              SELECT st.term, COUNT(DISTINCT st.study_id) AS co_count
              FROM st WHERE st.study_id IN (SELECT study_id FROM a) AND st.term NOT IN (:t, :tp)
              GROUP BY st.term
            ),
            a_cnt AS (SELECT COUNT(*) AS n FROM a),
            b_cnt AS (
              SELECT st.term, COUNT(DISTINCT st.study_id) AS n
              FROM st WHERE st.term IN (SELECT term FROM co)
              GROUP BY st.term
            )
            SELECT {_STRIP_PREFIX.format(c="co.term")} AS term,
                   co.co_count,
                   (co.co_count::float) / NULLIF((a_cnt.n + b_cnt.n - co.co_count), 0) AS jaccard
            FROM co CROSS JOIN a_cnt JOIN b_cnt ON b_cnt.term = co.term
            ORDER BY jaccard DESC NULLS LAST, co_count DESC, term ASC
            {lc}
        """), {"t": term, "tp": PREFIX + term, **lp}).mappings().all()
        rel = [dict(r) for r in rows if r["term"] and not re.match(r"^\d", r["term"])]
        return jsonify({"term": term, "related": rel}), 200


@bp.get("/query/<path:query_string>/parse")
def query_parse(query_string: str):
    """Show how a query is interpreted (no database access)."""
    cq = compile_query(query_string, _r_arg())
    return jsonify({
        "query": query_string,
        "interpreted": ast_to_str(cq.ast),
        "terms": cq.terms,
        "locations": [list(p) for p in cq.points],
        "r": cq.params.get("r"),
    }), 200


@bp.get("/query/<path:query_string>/studies")
def query_studies(query_string: str):
    r = _r_arg()
    cq = compile_query(query_string, r)
    limit, offset = _limit_offset()
    lc, lp = _limit_clause(limit, offset)
    eng = get_engine()
    with eng.begin() as conn:
        _set_search_path(conn)
        rows = conn.execute(text(f"""
            {_with(cq)}
            SELECT m.* FROM ns.metadata m
            WHERE {cq.where_sql}
            ORDER BY m.study_id
            {lc}
        """), {**cq.params, **lp}).mappings().all()
    out = []
    for row in rows:
        d = dict(row)
        d.pop("fts", None)
        out.append(d)
    return jsonify({
        "count": len(out),
        "interpreted": ast_to_str(cq.ast),
        "applied": {"locations": bool(cq.points), "terms": bool(cq.terms), "r": r if cq.points else None},
        "results": out,
    }), 200


def _collect_coords(conn, cq: CompiledQuery) -> Dict[str, List[Tuple[float, float, float]]]:
    rows = conn.execute(text(f"""
        {_with(cq, f"matched AS (SELECT m.study_id FROM ns.metadata m WHERE {cq.where_sql})")}
        SELECT c.study_id, ST_X(c.geom)::float AS x, ST_Y(c.geom)::float AS y, ST_Z(c.geom)::float AS z
        FROM ns.coordinates c JOIN matched USING (study_id)
    """), cq.params).mappings().all()
    out: Dict[str, List[Tuple[float, float, float]]] = {}
    for row in rows:
        out.setdefault(row["study_id"], []).append((row["x"], row["y"], row["z"]))
    return out


@bp.get("/query/<path:query_string>/locations")
def query_locations(query_string: str):
    r = _r_arg()
    cq = compile_query(query_string, r)
    eng = get_engine()
    with eng.begin() as conn:
        _set_search_path(conn)
        coords_by_study = _collect_coords(conn, cq)
    rows = [{"study_id": sid, "x": x, "y": y, "z": z}
            for sid, pts in coords_by_study.items() for (x, y, z) in pts]
    rows.sort(key=lambda d: (str(d["study_id"]), d["x"], d["y"], d["z"]))
    limit, offset = _limit_offset()
    if offset:
        rows = rows[offset:]
    if limit is not None:
        rows = rows[:limit]
    return jsonify({
        "location_counts": len(rows),
        "study_counts": len({d["study_id"] for d in rows}),
        "interpreted": ast_to_str(cq.ast),
        "results": rows,
    }), 200


@bp.get("/query/<path:query_string>/nii")
def query_nii(query_string: str):
    r = _r_arg()
    fwhm = _float_arg("fwhm", FWHM_DEFAULT, 0.0, FWHM_MAX)
    kernel = (request.args.get("kernel") or "gauss").lower()
    if kernel not in ("gauss", "uniform"):
        raise QueryError("kernel must be 'gauss' or 'uniform'.")
    # 'voxel' is accepted for backward compatibility; the grid is fixed to the
    # MNI152 2 mm template so the map always aligns with the frontend background.
    cq = compile_query(query_string, r)
    eng = get_engine()
    with eng.begin() as conn:
        _set_search_path(conn)
        coords_by_study = _collect_coords(conn, cq)
    path, n = _make_nii_from_coords(coords_by_study, fwhm=fwhm, kernel=kernel)
    resp = send_file(path, as_attachment=True, download_name="query_mixed_map.nii.gz",
                     mimetype="application/octet-stream")
    resp.headers["X-LoTUS-Studies"] = str(len(coords_by_study))
    resp.headers["X-LoTUS-Foci"] = str(n)
    resp.headers["Access-Control-Expose-Headers"] = "X-LoTUS-Studies, X-LoTUS-Foci"
    return resp


def _lotus_help_payload():
    return {
        "service": {"name": "LoTUS-BF", "tagline": "Location-or-Term Unified Search for Brain Functions"},
        "ok": True,
        "endpoints": [
            "/terms", "/terms/<term>",
            "/query/<query_string>/studies", "/query/<query_string>/locations",
            "/query/<query_string>/nii", "/query/<query_string>/parse",
        ],
        "docs": {
            "syntax": {
                "operators": ["AND", "OR", "NOT", "(", ")"],
                "precedence": "NOT > AND > OR; adjacent operands are joined by AND",
                "locations": "[x,y,z] in MNI mm, matched within r mm (default 6)",
                "terms": "bare words, \"quoted phrases\", or prefix* ; terms and locations can be mixed freely",
            },
            "params": {
                "r": f"sphere radius around [x,y,z] in mm (default {R_DEFAULT}, max {R_MAX})",
                "fwhm": f"smoothing FWHM in mm for /nii (default {FWHM_DEFAULT}, 0 = none)",
                "kernel": "gauss | uniform (default gauss)",
                "limit, offset": "pagination for /studies and /locations",
            },
            "examples": [
                "/query/[-2,50,-6] NOT \"ventromedial prefrontal\"/studies",
                "/query/([30,-60,50] OR [40,-50,45]) AND \"default mode\"/locations?r=8",
                "/query/(pain OR emotion) AND memory/studies",
                "/query/[-2,50,-6] AND reward/nii?fwhm=8&kernel=gauss",
            ],
        },
    }


def create_app() -> Flask:
    app = Flask(__name__)
    app.json.sort_keys = False
    CORS(app, resources={r"/*": {"origins": os.environ.get("CORS_ORIGINS", "*")}})
    app.register_blueprint(bp)

    # Serve the built React frontend (npm run build -> dist/) at /lotus-bf/,
    # so a single web service (e.g. on Render) hosts both the UI and the API.
    from flask import redirect, send_from_directory
    dist = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dist")

    @app.get("/lotus-bf")
    def _ui_slash():
        return redirect("/lotus-bf/", code=301)

    @app.get("/lotus-bf/")
    @app.get("/lotus-bf/<path:path>")
    def _ui(path="index.html"):
        if not os.path.isdir(dist):
            return jsonify({"error": "Frontend not built. Run: npm install && npm run build"}), 404
        full = os.path.join(dist, path)
        if os.path.isfile(full):
            return send_from_directory(dist, path)
        return send_from_directory(dist, "index.html")

    @app.errorhandler(404)
    def _not_found(_e):
        return jsonify({"error": "Not found. See /help for available endpoints."}), 404

    @app.errorhandler(500)
    def _server_error(e):
        return jsonify({"error": f"Internal server error: {getattr(e, 'original_exception', e)}"}), 500

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
