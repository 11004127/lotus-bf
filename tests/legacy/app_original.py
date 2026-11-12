"""ORIGINAL (pre-improvement) LoTUS-BF backend, kept only for regression comparison in the
validation notebook. Only change: the DB URL is read from $DB_URL instead of being hard-coded."""
import re
import os
from sqlalchemy import text
import re
import urllib.parse
from typing import List, Tuple, Dict, Any, Optional
from flask_cors import CORS

from flask import Blueprint, Flask, after_this_request, jsonify, request, send_file
from sqlalchemy import create_engine, text

# ------------ DB engine -------------
_engine = None
def _normalize_query(expr: str) -> str:
    if not expr:
        return expr
    expr = re.sub(r'(?i)\b(NOT|AND|OR)(?=[^\s()])', r'\1 ', expr)
    expr = re.sub(r'%(?![0-9A-Fa-f]{2})', ' ', expr)
    expr = re.sub(r'\s+', ' ', expr).strip()
    return expr

def get_engine():
    global _engine
    if _engine is not None:
        return _engine
    db_url = os.environ.get("DB_URL", "")
    if db_url.startswith("postgresql://"): db_url = "postgresql+psycopg2://" + db_url[13:]
    if not db_url:
        raise RuntimeError("Missing DB_URL (or DATABASE_URL) environment variable.")
    # Normalize URL
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql+psycopg2://", 1)
    elif "postgresql" not in db_url and "://" not in db_url:
        db_url = "postgresql+psycopg2://" + db_url
    _engine = create_engine(db_url, pool_pre_ping=True, future=True)
    return _engine

# ------------ Helpers -------------

PREFIX = "terms_abstract_tfidf__"
BOOL_WORDS = {"AND", "OR", "NOT"}

def _limit_offset() -> Tuple[Optional[int], int]:
    """limit is only applied if explicitly provided; otherwise return all rows (no LIMIT)."""
    raw = request.args.get("limit", None)
    limit: Optional[int] = None
    if raw is not None:
        try:
            val = int(raw)
            limit = max(1, min(val, 10000))
        except Exception:
            limit = None
    try:
        offset = int(request.args.get("offset", 0))
        offset = max(0, offset)
    except Exception:
        offset = 0
    return limit, offset

def _make_limit_clause(limit: Optional[int], offset: int) -> Tuple[str, Dict[str, Any]]:
    clause = ""
    params: Dict[str, Any] = {}
    if limit is not None:
        clause = "LIMIT :limit"
        params["limit"] = limit
        if offset:
            clause += " OFFSET :offset"
            params["offset"] = offset
    else:
        if offset:
            clause = "OFFSET :offset"
            params["offset"] = offset
    return clause, params

def _simple_terms_expr_to_sql(expr: str) -> Tuple[str, Dict[str, Any]]:
    """
    Convert simplified boolean expression (bare terms + AND/OR/NOT/+parens) into a SQL snippet.
    Prefix-aware: each term T expands to ((terms ? :tN) OR (terms ? :tpN))
    Supports implicit AND: "A NOT B" => "A AND NOT B", "A (B OR C)" => "A AND (B OR C)"
    """
    tokens = re.findall(r"\(|\)|\"[^\"]+\"|'[^']+'|[^\s()]+", expr)
    params: Dict[str, Any] = {}
    parts: List[str] = []
    term_index = 0

    prev_kind = None  # None | 'term' | 'op' | '(' | ')'

    def add_implicit_and_if_needed(next_kind: str):
        nonlocal prev_kind
        if prev_kind in ('term', ')') and next_kind in ('term', 'not', '('):
            parts.append('AND')

    for tok in tokens:
        if tok in ("(", ")"):
            if tok == "(":
                add_implicit_and_if_needed('(')
                parts.append("(")
                prev_kind = "("
            else:
                parts.append(")")
                prev_kind = ")"
            continue

        upper = tok.upper()
        if upper in BOOL_WORDS:
            if upper == "NOT":
                add_implicit_and_if_needed('not')
                parts.append("NOT")
                prev_kind = 'op'
            else:
                parts.append(upper)
                prev_kind = 'op'
            continue

        # term
        add_implicit_and_if_needed('term')
        if (tok.startswith("'") and tok.endswith("'")) or (tok.startswith('"') and tok.endswith('"')):
            val = tok[1:-1]
        else:
            val = tok
        k_raw = f"t{term_index}"
        k_pref = f"tp{term_index}"
        term_index += 1
        params[k_raw] = val
        params[k_pref] = PREFIX + val
        parts.append(f"((terms ? :{k_raw}) OR (terms ? :{k_pref}))")
        prev_kind = 'term'

    return " ".join(parts), params

def _build_terms_where_and_params(qs: str) -> Tuple[str, Dict[str, Any]]:
    decoded = urllib.parse.unquote(qs).strip()
    # Always parse user bare terms (no JSONB syntax from users per your spec)
    return _simple_terms_expr_to_sql(decoded)


# --- FTS NOT firewall helpers (Plan A) ---
def _build_neg_tsquery(expr: str) -> str:
    """
    Build a Postgres tsquery string that represents everything the user negated
    in `expr`. If there is no NOT, return empty string.
    Features:
      - implicit AND ("A NOT B" == "A AND NOT B")
      - quoted tokens remain single lexemes (no phrase semantics)
      - '*' suffix becomes ':*' for tsquery prefix search
    """
    if not expr or ("NOT" not in expr and "not" not in expr):
        return ""

    tokens = re.findall(r"\(|\)|\"[^\"]+\"|'[^']+'|[^\s()]+", expr)
    norm = []
    for t in tokens:
        tu = t.upper()
        norm.append(tu if tu in ("AND","OR","NOT","(",")") else t)

    i = 0
    negatives = []

    def starts_term(tok):
        return (tok is not None) and (tok not in (")","OR"))

    def term_token(tok: str) -> str:
        raw = tok
        if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
            raw = raw[1:-1]
        if raw.endswith("*"):
            base = raw[:-1]
            return f"{base}:*"
        return raw

    def parse_primary():
        nonlocal i
        tok = norm[i] if i < len(norm) else None
        if tok is None:
            raise ValueError("Unexpected end of input")
        if tok == "(":
            i += 1
            inner = parse_or()
            if i >= len(norm) or norm[i] != ")":
                raise ValueError("Expected ')'")
            i += 1
            return f"({inner})"
        if tok in ("AND","OR","NOT",")"):
            raise ValueError(f"Unexpected token: {tok}")
        i += 1
        return term_token(tok)

    def parse_unary():
        nonlocal i, negatives
        tok = norm[i] if i < len(norm) else None
        if tok == "NOT":
            i += 1
            operand = parse_unary()
            negatives.append(operand)
            return operand
        return parse_primary()

    def parse_and():
        left = parse_unary()
        while True:
            tok = norm[i] if i < len(norm) else None
            if tok == "AND":
                i += 1
                right = parse_unary()
                left = f"({left}) & ({right})"
            elif starts_term(tok):
                right = parse_unary()
                left = f"({left}) & ({right})"
            else:
                break
        return left

    def parse_or():
        left = parse_and()
        while True:
            tok = norm[i] if i < len(norm) else None
            if tok == "OR":
                i += 1
                right = parse_and()
                left = f"({left}) | ({right})"
            else:
                break
        return left

    try:
        _ = parse_or()
    except Exception:
        negatives = []

    negatives = [n for n in dict.fromkeys([n for n in negatives if n])]
    if not negatives:
        return ""
    return " | ".join([f"({n})" if (" " in n or "&" in n or "|" in n) else n for n in negatives])
def _points_from_query_string(qs: str) -> List[Tuple[float,float,float]]:
    """
    Parse query strings like: [30,-60,50] AND [40,-50,45]
    Currently supports AND-combinations of points.
    """
    s = urllib.parse.unquote(qs).strip()
    pts = re.findall(r"\[([^\]]+)\]", s)
    coords: List[Tuple[float,float,float]] = []
    for p in pts:
        parts = [c.strip() for c in p.split(",")]
        if len(parts) != 3:
            raise ValueError(f"Invalid coordinate triple: [{p}]")
        try:
            x, y, z = float(parts[0]), float(parts[1]), float(parts[2])
        except Exception:
            raise ValueError(f"Coordinate elements must be numeric: [{p}]")
        coords.append((x,y,z))
    if not coords:
        raise ValueError("No coordinates found. Use [x,y,z] AND/OR/NOT [x2,y2,z2] ...")
    return coords

def _parse_location_boolean_expr(qs: str):
    """
    Parse location boolean expression into (points, expr_sql). Supports AND/OR/NOT and parentheses,
    with implicit AND (e.g., "[a] NOT [b]" => "[a] AND NOT [b]").
    - points: list of (x,y,z) floats in encounter order (unique)
    - expr_sql: SQL boolean expression using EXISTS over 'hits' and alias 's' for studies table.
    """
    s = urllib.parse.unquote(qs).strip()
    token_re = re.compile(r"\[[^\]]+\]|\(|\)|\bAND\b|\bOR\b|\bNOT\b", re.IGNORECASE)
    tokens = token_re.findall(s)
    if not tokens:
        pts = _points_from_query_string(qs)
        idxs = list(range(1, len(pts)+1))
        parts = [f"(EXISTS (SELECT 1 FROM hits h WHERE h.study_id = s.study_id AND h.idx = {i}))" for i in idxs]
        return pts, " AND ".join(parts)

    points: List[Tuple[float,float,float]] = []
    point_to_idx: Dict[Tuple[float,float,float], int] = {}

    def parse_point(tok: str) -> int:
        inside = tok.strip()[1:-1]  # remove [ ]
        parts = [c.strip() for c in inside.split(",")]
        if len(parts) != 3:
            raise ValueError(f"Invalid coordinate triple: {tok}")
        x, y, z = float(parts[0]), float(parts[1]), float(parts[2])
        key = (x, y, z)
        if key not in point_to_idx:
            point_to_idx[key] = len(points) + 1
            points.append(key)
        return point_to_idx[key]

    expr_parts: List[str] = []
    prev_kind: Optional[str] = None  # 'point' | 'op' | '(' | ')'
    for tok in tokens:
        up = tok.upper()
        if tok.startswith("["):
            if prev_kind in ("point", ")"):
                expr_parts.append("AND")
            idx = parse_point(tok)
            expr_parts.append(f"(EXISTS (SELECT 1 FROM hits h WHERE h.study_id = s.study_id AND h.idx = {idx}))")
            prev_kind = "point"
        elif up in ("AND", "OR"):
            expr_parts.append(up)
            prev_kind = "op"
        elif up == "NOT":
            if prev_kind in ("point", ")"):
                expr_parts.append("AND")
            expr_parts.append("NOT")
            prev_kind = "op"
        elif tok == "(":
            if prev_kind in ("point", ")"):
                expr_parts.append("AND")
            expr_parts.append("(")
            prev_kind = "("
        elif tok == ")":
            expr_parts.append(")")
            prev_kind = ")"
        else:
            # ignore
            pass

    expr_sql = " ".join(expr_parts).strip()
    if not expr_sql:
        raise ValueError("Empty boolean expression after parsing.")

    return points, expr_sql

# ------------ Blueprint with the endpoints -------------


def _parse_env_template_grid():
    """Return (shape, affine, zooms, qform_code, sform_code) from env if provided,
    otherwise use FSL MNI152 2mm defaults. Accepted env vars:
      - MNI_SHAPE: '91,109,91'
      - MNI_SROW_X: 'a11,a12,a13,a14'  (same for MNI_SROW_Y, MNI_SROW_Z)
        OR MNI_AFFINE: 'a11,a12,...,a34' 12 comma-separated numbers row-major 3x4
      - MNI_ZOOMS: '2,2,2'
      - MNI_QFORM_CODE / MNI_SFORM_CODE: integers (e.g., 4)
    """
    import os, numpy as np
    # Defaults (FSL MNI152 2mm)
    shape = (91, 109, 91)
    affine = np.array([
        [-2.0,  0.0,  0.0,  90.0],
        [ 0.0,  2.0,  0.0, -126.0],
        [ 0.0,  0.0,  2.0, -72.0],
        [ 0.0,  0.0,  0.0,   1.0],
    ], dtype=float)
    zooms = (2.0, 2.0, 2.0)
    qform_code = 4
    sform_code = 4

    def _parse_tuple(s):
        return tuple(float(x.strip()) for x in s.split(',') if x.strip())

    shp = os.environ.get('MNI_SHAPE')
    if shp:
        parts = [int(float(x.strip())) for x in shp.split(',') if x.strip()]
        if len(parts) == 3:
            shape = tuple(parts)

    if os.environ.get('MNI_AFFINE'):
        vals = _parse_tuple(os.environ['MNI_AFFINE'])
        if len(vals) == 12:
            affine = np.array([vals[0:4], vals[4:8], vals[8:12], [0,0,0,1.0]], dtype=float)
    else:
        sx = os.environ.get('MNI_SROW_X')
        sy = os.environ.get('MNI_SROW_Y')
        sz = os.environ.get('MNI_SROW_Z')
        if sx and sy and sz:
            ax = _parse_tuple(sx); ay = _parse_tuple(sy); az = _parse_tuple(sz)
            if len(ax) == 4 and len(ay) == 4 and len(az) == 4:
                affine = np.array([ax, ay, az, [0,0,0,1.0]], dtype=float)

    z = os.environ.get('MNI_ZOOMS')
    if z:
        vals = _parse_tuple(z)
        if len(vals) == 3:
            zooms = tuple(vals)

    try:
        qform_code = int(os.environ.get('MNI_QFORM_CODE', qform_code))
    except Exception:
        pass
    try:
        sform_code = int(os.environ.get('MNI_SFORM_CODE', sform_code))
    except Exception:
        pass

    return shape, affine, zooms, qform_code, sform_code

bp = Blueprint("dissociate_api", __name__)

@bp.get("/debug/nii_meta")
def debug_nii_meta():
    shape, aff, zooms, qcode, scode = _parse_env_template_grid()
    return jsonify({
        "shape": shape,
        "zooms": zooms,
        "qform_code": qcode,
        "sform_code": scode,
        "affine": [[float(x) for x in aff[i,:]] for i in range(4)]
    }), 200

@bp.get("/terms")
@bp.get("/terms/<term>")
def terms(term: str=None):
    """
    - GET /terms -> return all terms (strip 'terms_abstract_tfidf__' prefix)
    - GET /terms/<term> -> return related terms (co-occurrence/Jaccard)
    """
    eng = get_engine()
    with eng.begin() as conn:
        conn.execute(text("SET search_path TO ns, public;"))
        if not term:
            sql = text("""
                SELECT DISTINCT
                  CASE WHEN key LIKE 'terms_abstract_tfidf__%%'
                       THEN SUBSTRING(key FROM char_length('terms_abstract_tfidf__')+1)
                       ELSE key
                  END AS term
                FROM (
                  SELECT jsonb_object_keys(terms) AS key
                  FROM ns.annotations_json
                ) j
                ORDER BY term
            """)
            rows = conn.execute(sql).fetchall()
            terms = [r.term for r in rows if not re.match(r'^\d', (r.term or ""))]
            return jsonify({"terms": terms}), 200
        else:
            limit, offset = _limit_offset()
            limit_clause, lp = _make_limit_clause(limit, offset)
            sql = text(f"""
                WITH a AS (
                  SELECT DISTINCT study_id
                  FROM ns.annotations_json
                  WHERE (terms ? :t) OR (terms ? :tp)
                ),
                co AS (
                  SELECT k.term, COUNT(DISTINCT aj.study_id) AS co_count
                  FROM ns.annotations_json aj
                  JOIN LATERAL jsonb_object_keys(aj.terms) AS k(term) ON TRUE
                  WHERE aj.study_id IN (SELECT study_id FROM a)
                    AND k.term NOT IN (:t, :tp)
                  GROUP BY k.term
                ),
                a_cnt AS (SELECT COUNT(*) AS n FROM a),
                b_cnt AS (
                  SELECT k.term, COUNT(DISTINCT aj.study_id) AS n
                  FROM ns.annotations_json aj
                  JOIN LATERAL jsonb_object_keys(aj.terms) AS k(term) ON TRUE
                  GROUP BY k.term
                )
                SELECT
                  CASE WHEN co.term LIKE 'terms_abstract_tfidf__%%'
                       THEN SUBSTRING(co.term FROM char_length('terms_abstract_tfidf__')+1)
                       ELSE co.term
                  END AS term,
                  co.co_count,
                  (co.co_count::float) / NULLIF((a_cnt.n + b_cnt.n - co.co_count), 0) AS jaccard
                FROM co
                CROSS JOIN a_cnt
                JOIN b_cnt ON b_cnt.term = co.term
                ORDER BY jaccard DESC NULLS LAST, co_count DESC, term ASC
                {limit_clause}
            """)
            rows = conn.execute(sql, {"t": term, "tp": PREFIX + term, **lp}).mappings().all()
            rel = [dict(r) for r in rows]
            rel = [d for d in rel if not re.match(r'^\d', (d.get("term") or ""))]
            return jsonify({"term": term, "related": rel}), 200


@bp.get("/query/<path:query_string>/studies")
def mixed_query_studies(query_string: str):
    r = request.args.get("r", default=6.0, type=float)
    terms_expr, loc_expr = _split_mixed_query(query_string)
    has_terms, has_loc = terms_expr is not None, loc_expr is not None
    if not (has_terms or has_loc):
        return jsonify({"error": "Query must include at least a term or a [x,y,z] coordinate."}), 400
    eng = get_engine()
    with eng.begin() as conn:
        conn.execute(text("SET search_path TO ns, public;"))
        limit, offset = _limit_offset()
        limit_clause, lp = _make_limit_clause(limit, offset)

        ctes, params = [], {}

        if has_terms:
            ts_full = _build_full_tsquery(terms_expr)
            ctes.append(f"""
            matched_terms AS (
              SELECT m.study_id
              FROM ns.metadata m
              WHERE (
                m.fts @@ to_tsquery('english', :full_tsquery)
                OR to_tsvector('english', COALESCE(m.title,'')) @@ to_tsquery('english', :full_tsquery)
              )
            )""")
            params['full_tsquery'] = ts_full


        if has_loc:
            points, loc_sql = _parse_location_boolean_expr(loc_expr)
            pts_rows = "\nUNION ALL\n".join([f"SELECT {i+1} AS idx, ST_MakePoint({x}, {y}, {z}) AS geom" for i,(x,y,z) in enumerate(points)])
            ctes.append(f"""
            pts AS ( {pts_rows} ),
            hits AS (
              SELECT c.study_id, p.idx
              FROM ns.coordinates c
              JOIN pts p
                ON ST_3DDistance(ST_SetSRID(c.geom,0), ST_SetSRID(p.geom,0)) < :r
              GROUP BY c.study_id, p.idx
            ),
            studies AS ( SELECT DISTINCT study_id FROM ns.coordinates ),
            matched_loc AS ( SELECT s.study_id FROM studies s WHERE {loc_sql} )
            """)
            params["r"] = r

        if has_loc and has_terms:
            matched_select = "SELECT study_id FROM matched_loc INTERSECT SELECT study_id FROM matched_terms"
        elif has_loc:
            matched_select = "SELECT study_id FROM matched_loc"
        else:
            matched_select = "SELECT study_id FROM matched_terms"
        if 'neg_tsquery' in params:
            matched_select = matched_select + " EXCEPT SELECT study_id FROM neg_fts"

        ctes_sql = ",\n".join([c.strip() for c in ctes]) if ctes else ""
        with_matched = f"WITH {ctes_sql},\nmatched AS ( {matched_select} )" if ctes_sql else f"WITH matched AS ( {matched_select} )"

        sql = text(f"""
            {with_matched}
            SELECT m.*
            FROM ns.metadata m
            JOIN matched x USING (study_id)
            ORDER BY m.study_id
            {limit_clause}
        """)
        rows = conn.execute(sql, {**params, **lp}).mappings().all()
        out = []
        for r_ in rows:
            d = dict(r_); d.pop('fts', None); out.append(d)
        return jsonify({"count": len(out), "applied": {"locations": bool(has_loc), "terms": bool(has_terms), "r": r if has_loc else None}, "results": out}), 200

@bp.get("/query/<path:query_string>/locations")
def mixed_query_locations(query_string: str):
    r = request.args.get("r", default=6.0, type=float)
    eng = get_engine()
    with eng.begin() as conn:
        conn.execute(text("SET search_path TO ns, public;"))
        try:
            coords_by_study = _collect_coords_for_mixed(conn, query_string, r=r)
        except Exception as e:
            return jsonify({"error": str(e)}), 400
        limit, offset = _limit_offset()
        rows = []
        for sid, coords in coords_by_study.items():
            for (x,y,z) in coords:
                rows.append({"study_id": sid, "x": x, "y": y, "z": z})
        rows.sort(key=lambda d: (d["study_id"], d["x"], d["y"], d["z"]))
        if offset: rows = rows[offset:]
        if limit is not None: rows = rows[:limit]
        return jsonify({"location_counts": len(rows), "study_counts": len({d['study_id'] for d in rows}), "results": rows}), 200


# --- Create a NIfTI map from coordinate dict {study_id: [(x,y,z), ...]} ---
def _make_nii_from_coords(coords_by_study, voxel: float = 2.0, fwhm: float = 10.0, kernel: str = "gauss"):
    """Meta-analytic map generator with a SINGLE canonical default header embedded from anatomical.nii.gz.
    At runtime no files are read; both qform and sform are set identically to this default.
    """
    import os, tempfile, numpy as np
    try:
        import nibabel as nib
    except Exception as e:
        raise RuntimeError("nibabel is required to build NIfTI: " + str(e))
    try:
        from scipy.ndimage import gaussian_filter
        has_scipy = True
    except Exception:
        has_scipy = False

    shape = (91, 109, 91)
    zooms = (2, 2, 2)
    template_aff = np.array([
        [-2, 0, 0, 90],
        [0, 2, 0, -126],
        [0, 0, 2, -72],
        [0.0, 0.0, 0.0, 1.0]
    ], dtype=float)

    vol = np.zeros(shape, dtype=np.float32)
    inv_aff = np.linalg.inv(template_aff)

    n = 0
    for sid, pts in coords_by_study.items():
        for (x, y, z) in pts:
            ijk_h = inv_aff @ np.array([float(x), float(y), float(z), 1.0], dtype=np.float64)
            i, j, k = np.round(ijk_h[:3]).astype(int)
            if 0 <= i < shape[0] and 0 <= j < shape[1] and 0 <= k < shape[2]:
                vol[i, j, k] += 1.0
                n += 1

    if has_scipy and kernel.lower() == "gauss" and fwhm and fwhm > 0:
        denom = 2.0 * np.sqrt(2.0 * np.log(2.0))
        sigma_vox = tuple((fwhm / denom) / max(1e-6, z) for z in zooms)
        vol = gaussian_filter(vol, sigma=sigma_vox, mode="constant", truncate=3.0)

    out_img = nib.Nifti1Image(vol, template_aff)
    out_img.set_data_dtype(np.float32)
    hdr = out_img.header
    hdr.set_data_shape(shape)
    hdr.set_xyzt_units('mm', 'sec')
    hdr.set_zooms(zooms)

    # Enforce identical qform/sform and write srow_* explicitly for total consistency
    out_img.set_sform(template_aff, code=4)
    out_img.set_qform(template_aff, code=4)
    hdr['srow_x'] = template_aff[0]
    hdr['srow_y'] = template_aff[1]
    hdr['srow_z'] = template_aff[2]

    outdir = tempfile.mkdtemp(prefix="lotus_nii_")
    outpath = os.path.join(outdir, "query_mixed_map.nii.gz")
    nib.save(out_img, outpath)
    return outpath, n


@bp.get("/query/<path:query_string>/nii")
def mixed_query_nii(query_string: str):
    voxel  = request.args.get("voxel", default=2.0, type=float)
    fwhm   = request.args.get("fwhm",  default=10.0, type=float)
    kernel = request.args.get("kernel", default="gauss", type=str).lower()
    r      = request.args.get("r",      default=6.0,  type=float)
    eng = get_engine()
    with eng.begin() as conn:
        conn.execute(text("SET search_path TO ns, public;"))
        try:
            coords_by_study = _collect_coords_for_mixed(conn, query_string, r=r)
            path, n = _make_nii_from_coords(coords_by_study, voxel=voxel, fwhm=fwhm, kernel=kernel)
        except Exception as e:
            return jsonify({"error": str(e)}), 400
    filename = "query_mixed_map.nii.gz"
    return send_file(path, as_attachment=True, download_name=filename, mimetype="application/octet-stream")

def _split_mixed_query(qs: str):
    s = urllib.parse.unquote(qs).strip()
    token_re = re.compile(r"""\[[^\]]+\]|"([^"\\]|\\.)+"|'([^'\\]|\\.)+'|\bAND\b|\bOR\b|\bNOT\b|\(|\)|[^()\s]+""", re.IGNORECASE)
    toks = [m.group(0) for m in re.finditer(token_re, s)]
    # -------- build location expr (only [], (), AND/OR/NOT considered) --------
    loc_parts = []
    prev_was_operand = False  # operand = [...] or ')'
    N = len(toks)
    def _next_is_loc_operand(j):
        k = j
        while k < N:
            t = toks[k]
            up = t.upper()
            if t.startswith('[') and t.endswith(']'):
                return True
            if up in ('(',')'):
                return up == '('
            if up in ('AND','OR'):
                return False
            if up == 'NOT':
                k += 1
                if k < N and (toks[k].startswith('[') and toks[k].endswith(']') or toks[k].upper()=='('):
                    return True
                return False
            k += 1
        return False

    i = 0
    while i < N:
        t = toks[i]
        up = t.upper()
        if t.startswith('[') and t.endswith(']'):
            loc_parts.append(t)
            prev_was_operand = True
        elif up in ('(',')'):
            loc_parts.append(up)
            prev_was_operand = (up == ')')
        elif up in ('AND','OR'):
            if prev_was_operand and _next_is_loc_operand(i+1):
                loc_parts.append(up)
                prev_was_operand = False
        elif up == 'NOT':
            if _next_is_loc_operand(i+1):
                if prev_was_operand and (not loc_parts or loc_parts[-1] != 'AND'):
                    loc_parts.append('AND')
                loc_parts.append('NOT')
                prev_was_operand = False
        i += 1
    loc_expr = " ".join(loc_parts).strip() or None

    # -------- build terms expr --------
    terms_parts, phrase = [], []
    def flush():
        nonlocal phrase
        if phrase:
            # Insert implicit AND between adjacent words, so: a b c -> a AND b AND c
            for j, w in enumerate(phrase):
                if j:
                    terms_parts.append('AND')
                terms_parts.append(w)
            phrase = []
    i = 0
    while i < N:
        t = toks[i]
        up = t.upper()
        if t.startswith('[') and t.endswith(']'):
            flush()
            i += 1
            continue
        if up in ('AND','OR'):
            if terms_parts:
                flush(); terms_parts.append(up)
            else:
                flush()
            i += 1
            continue
        if up in ('(',')'):
            flush()
            i += 1
            continue
        if up == 'NOT':
            # include NOT in terms only if next token is not a location operand ('[' or '(')
            nxt = toks[i+1] if i+1 < N else None
            if not (nxt and (nxt.startswith('[') and nxt.endswith(']') or (isinstance(nxt, str) and nxt.upper()=='('))):
                flush(); terms_parts.append('NOT')
            else:
                flush()
            i += 1
            continue
        if (t.startswith('"') and t.endswith('"')) or (t.startswith("'") and t.endswith("'")):
            flush(); terms_parts.append(t)
        else:
            phrase.append(t)
        i += 1
    flush()
    terms_expr = " ".join(terms_parts).strip() or None

    def _empty(expr: str) -> bool:
        s = expr.replace('(', '').replace(')', '').replace('AND','').replace('OR','').replace('NOT','').strip()
        return s == ''
    if terms_expr is not None and _empty(terms_expr): terms_expr=None
    if loc_expr is not None and _empty(loc_expr): loc_expr=None
    return terms_expr, loc_expr

def _collect_coords_for_mixed(conn, query_string: str, r: float):
    terms_expr, loc_expr = _split_mixed_query(query_string)
    ctes, params = [], {}
    if terms_expr:
        ts_full = _build_full_tsquery(terms_expr)
        ctes.append(f"""
        matched_terms AS (
          SELECT m.study_id
          FROM ns.metadata m
          WHERE (
            m.fts @@ to_tsquery('english', :full_tsquery)
            OR to_tsvector('english', COALESCE(m.title,'')) @@ to_tsquery('english', :full_tsquery)
          )
        )
        """)
        params['full_tsquery'] = ts_full
    if loc_expr:
        points, loc_sql = _parse_location_boolean_expr(loc_expr)
        pts_rows = "\nUNION ALL\n".join([f"SELECT {i+1} AS idx, ST_MakePoint({x},{y},{z}) AS geom" for i,(x,y,z) in enumerate(points)])
        ctes.append(f"""
        pts AS ( {pts_rows} ),
        hits AS (
          SELECT c.study_id, p.idx
          FROM ns.coordinates c
          JOIN pts p
            ON ST_3DDistance(ST_SetSRID(c.geom,0), ST_SetSRID(p.geom,0)) < :r
          GROUP BY c.study_id, p.idx
        ),
        studies AS ( SELECT DISTINCT study_id FROM ns.coordinates ),
        matched_loc AS ( SELECT s.study_id FROM studies s WHERE {loc_sql} )
        """)
        params["r"] = r
    if loc_expr and terms_expr:
        matched_select = "SELECT study_id FROM matched_loc INTERSECT SELECT study_id FROM matched_terms"
        if 'neg_tsquery' in params:
            matched_select = matched_select + " EXCEPT SELECT study_id FROM neg_fts"
    elif loc_expr:
        matched_select = "SELECT study_id FROM matched_loc"
        if 'neg_tsquery' in params:
            matched_select = matched_select + " EXCEPT SELECT study_id FROM neg_fts"
    elif terms_expr:
        matched_select = "SELECT study_id FROM matched_terms"
        if 'neg_tsquery' in params:
            matched_select = matched_select + " EXCEPT SELECT study_id FROM neg_fts"
    else:
        raise ValueError("Query must contain at least a term or a [x,y,z] coordinate.")
    ctes_sql = ",\n".join([c.strip() for c in ctes])
    sql = text(f"""
        WITH
        {ctes_sql},
        matched AS ( {matched_select} )
        SELECT c.study_id,
               ST_X(c.geom)::float AS x,
               ST_Y(c.geom)::float AS y,
               ST_Z(c.geom)::float AS z
        FROM ns.coordinates c
        JOIN matched x USING (study_id)
    """)
    rows = conn.execute(sql, params).mappings().all()
    out = {}
    for r_ in rows:
        out.setdefault(r_["study_id"], []).append((r_["x"], r_["y"], r_["z"]))
    return out

def create_app():
    app = Flask(__name__)
    CORS(app, resources={r"/*": {"origins": "*"}})  # 開發用：全開放
    app.register_blueprint(bp)
    return app

def _lotus_help_payload():
    return {
        "service": {"name": "LoTUS-BF", "tagline": "Location-or-Term Unified Search for Brain Functions"},
        "ok": True,
        "endpoints": [
            "/terms",
            "/terms/<term>",
            "/query/<query_string>/studies",
            "/query/<query_string>/locations",
            "/query/<query_string>/nii"
        ],
        "notes": "Use /query/<mixed>/(studies|locations|nii). 'mixed' supports coordinates like [x,y,z] and boolean terms with AND/OR/NOT.",
        "docs": {
            "syntax": {
                "mixed_query": "/query/<mixed_query>/(studies|locations|nii)",
                "operators": ["AND","OR","NOT","(",")"],
                "locations": "[x,y,z] with optional ?r=float (default 6.0)",
                "terms": "bare words auto-grouped into phrases; or use quotes for exact phrases"
            },
            "params": {
                "r": "sphere radius around [x,y,z] (default 6.0)",
                "voxel": "voxel size for NIfTI (default 2.0)",
                "fwhm": "smoothing FWHM (default 10.0)",
                "kernel": "gauss | uniform (default gauss)",
                "limit_offset": "limit, offset for pagination on studies/locations"
            },
            "examples": [
                "/query/[-2,50,-6] NOT \"ventromedial prefrontal\"/studies",
                "/query/([30,-60,50] OR [40,-50,45]) AND default mode/locations?r=8",
                "/query/[-2,50,-6] AND reward/nii?voxel=2&fwhm=8&kernel=gauss"
            ]
        }
    }

@bp.get("/")
def index():
    return "Server working!"

@bp.get("/help")
def help():
    return jsonify(_lotus_help_payload())


_TOKEN_AND="AND";_TOKEN_OR="OR";_TOKEN_NOT="NOT"
_TOKEN_RE=re.compile(r'\s*(\(|\)|"(?:\\.|[^"\\])*"|\bAND\b|\bOR\b|\bNOT\b|[^\s()]+)',re.IGNORECASE)
def _build_full_tsquery(expr:str)->str:
    expr=_normalize_query(expr)
    if not expr: return ""
    toks=[m.group(1) for m in _TOKEN_RE.finditer(expr)]
    toks=[t if t in("(",")") else (t.upper() if t.upper() in(_TOKEN_AND,_TOKEN_OR,_TOKEN_NOT) else t) for t in toks]
    i=0
    def starts_term(tok): return tok is not None and tok not in(")",_TOKEN_OR)
    def term_tok(tok:str)->str:
        raw = tok
        quoted = False
        if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
            quoted = True
            raw = raw[1:-1]
        parts = raw.split()
        def norm(p: str) -> str:
            return (p[:-1] + ":*") if p.endswith("*") else p
        if quoted and len(parts) > 1:
            # Phrase query -> adjacent terms
            return "(" + " <-> ".join(norm(p) for p in parts) + ")"
        if len(parts) > 1:
            # Safety: multiple terms without quotes -> AND them
            return "(" + " & ".join(norm(p) for p in parts) + ")"
        return norm(parts[0]) if parts else ""
    def parse_primary():
        nonlocal i
        tok=toks[i] if i<len(toks) else None
        if tok is None: raise ValueError("Unexpected end of input")
        if tok=="(":
            i+=1
            inner=parse_or()
            if i>=len(toks) or toks[i]!=")": raise ValueError("Expected ')'")
            i+=1
            return f"({inner})"
        if tok in(_TOKEN_AND,_TOKEN_OR,_TOKEN_NOT,")"): raise ValueError(f"Unexpected token: {tok}")
        i+=1
        return term_tok(tok)
    def parse_unary():
        nonlocal i
        tok=toks[i] if i<len(toks) else None
        if tok==_TOKEN_NOT:
            i+=1
            op=parse_unary()
            return f"!({op})"
        return parse_primary()
    def parse_and():
        nonlocal i
        left=parse_unary()
        while True:
            tok=toks[i] if i<len(toks) else None
            if tok==_TOKEN_AND:
                i+=1
                right=parse_unary()
                left=f"({left}) & ({right})"
            elif starts_term(tok):
                right=parse_unary()
                left=f"({left}) & ({right})"
            else: break
        return left
    def parse_or():
        nonlocal i
        left=parse_and()
        while True:
            tok=toks[i] if i<len(toks) else None
            if tok==_TOKEN_OR:
                i+=1
                right=parse_and()
                left=f"({left}) | ({right})"
            else: break
        return left
    return parse_or()
