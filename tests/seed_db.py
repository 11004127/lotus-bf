"""Create a small synthetic Neurosynth-like database (schema `ns`) for validating LoTUS-BF.

Tables mirror what app.py expects:
  ns.metadata(study_id, title, journal, year, authors, abstract, fts tsvector)
  ns.coordinates(study_id, geom geometry(PointZ))
  ns.annotations_json(study_id, terms jsonb)   -- keys prefixed with terms_abstract_tfidf__
  ns.annotations_terms(study_id, contrast_id, term, weight)  -- HW08 create_db.py layout
"""
import json
import random
import sys

import psycopg2

DSN = sys.argv[1] if len(sys.argv) > 1 else "dbname=lotus user=postgres host=/tmp/pg port=5433"
PREFIX = "terms_abstract_tfidf__"

# Hand-crafted studies whose correct answers are known in advance
CORE = [
    ("90000001", "Orbitofrontal cortex encodes reward value", "NeuroImage", 2015,
     "Smith J; Lee K", "Reward valuation in the orbitofrontal cortex.",
     [(-2, 50, -6), (30, -60, 50)], ["reward", "orbitofrontal", "value"]),
    ("90000002", "Amygdala responses to emotion faces", "J Neurosci", 2012,
     "Chen Y", "Emotion processing of fearful faces in the amygdala.",
     [(-22, -4, -18)], ["emotion", "amygdala", "faces"]),
    ("90000003", "Working memory load in dorsolateral prefrontal cortex", "Cereb Cortex", 2018,
     "Wang L; Hsu P", "Working memory maintenance.",
     [(40, 30, 30)], ["working memory", "memory", "prefrontal"]),
    ("90000004", "Pain and emotion interactions", "Pain", 2019,
     "Lin C", "Pain modulates emotion.",
     [(-22, -4, -18), (40, 30, 30)], ["pain", "emotion"]),
    ("90000005", "Default mode network at rest", "PNAS", 2010,
     "Raichle M", "The default mode network during rest.",
     [(0, -52, 26)], ["default mode", "rest"]),
    ("90000006", "Memory retrieval and pain", "Brain", 2016,
     "Ito T", "Episodic memory retrieval under pain.",
     [(0, -52, 26)], ["memory", "pain"]),
    ("90000007", "Orientation of visual attention", "Neuron", 2011,
     "Corbetta M", "Spatial orientation of attention in parietal cortex.",
     [(30, -60, 50)], ["attention", "orientation"]),
    ("90000008", "Emotion regulation and reward", "Emotion", 2020,
     "Gross J", "Regulating emotion engages reward circuits.",
     [(-2, 50, -6)], ["emotion", "reward"]),
]

FILLER_WORDS = ["language", "motor", "visual", "auditory", "learning", "decision", "language",
                "semantic", "speech", "movement", "vision", "sound"]


def main():
    random.seed(42)
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("CREATE EXTENSION IF NOT EXISTS postgis;")
    cur.execute("DROP SCHEMA IF EXISTS ns CASCADE; CREATE SCHEMA ns;")
    cur.execute("""
      CREATE TABLE ns.metadata (
        study_id text PRIMARY KEY, title text, journal text, year int,
        authors text, abstract text, fts tsvector);
      CREATE TABLE ns.coordinates (study_id text, geom geometry(PointZ));
      CREATE TABLE ns.annotations_json (study_id text, terms jsonb);
      CREATE TABLE ns.annotations_terms (study_id text, contrast_id text, term text, weight float8);
    """)
    studies = list(CORE)
    # 300 filler studies with random coordinates (these never mention the core terms)
    for k in range(300):
        w = random.sample(FILLER_WORDS, 2)
        coords = [(random.randint(-60, 60), random.randint(-90, 60), random.randint(-40, 70))
                  for _ in range(random.randint(1, 6))]
        studies.append((str(80000000 + k), f"Study of {w[0]} and {w[1]}", "Filler J", 2000 + k % 24,
                        "Anon A", f"{w[0]} {w[1]} task.", coords, w))
    for sid, title, journal, year, authors, abstract, coords, terms in studies:
        cur.execute(
            "INSERT INTO ns.metadata VALUES (%s,%s,%s,%s,%s,%s, to_tsvector('english', %s))",
            (sid, title, journal, year, authors, abstract, f"{title} {abstract}"))
        for x, y, z in coords:
            cur.execute("INSERT INTO ns.coordinates VALUES (%s, ST_MakePoint(%s,%s,%s))", (sid, x, y, z))
        cur.execute("INSERT INTO ns.annotations_json VALUES (%s, %s)",
                    (sid, json.dumps({PREFIX + t: 0.1 for t in terms})))
        for t in terms:   # layout produced by HW08 create_db.py (without --enable-json)
            cur.execute("INSERT INTO ns.annotations_terms VALUES (%s, '1', %s, 0.1)", (sid, PREFIX + t))
    cur.execute("SELECT count(*) FROM ns.metadata"); n1 = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM ns.coordinates"); n2 = cur.fetchone()[0]
    print(f"seeded {n1} studies, {n2} coordinates")


if __name__ == "__main__":
    main()
