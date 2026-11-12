# LoTUS-BF (improved)

Location-or-Term Unified Search for Brain Functions: search Neurosynth studies with boolean
queries that mix terms and MNI coordinates, e.g. `([-22,-4,-18] OR amygdala) AND NOT pain`.

## Structure
```
app.py                    Flask backend (port 5000)
src/                      React frontend (Vite, served at /lotus-bf/ on port 3000)
  components/             Terms, QueryBuilder, Studies, Locations, NiiViewer, Table
  hooks/  lib/            URL state, sorting, API helpers
public/static/            MNI152 2 mm background template
tests/                    pytest suite, synthetic DB generator, Playwright UI check, original backend (for comparison)
docs/                     screenshots and UI test results used in the validation report
```

## Quick start on hpc.psy.ntu.edu.tw
```bash
unzip lotus-bf-improved.zip && cd lotus-bf
pip install --user -r requirements.txt
export DB_URL='postgresql://...'      # Render "External Database URL" of your HW08 database
bash start.sh                         # prints your URL when everything is up
```

## Run on the server (manual)
```bash
# backend
pip install flask flask-cors sqlalchemy psycopg2-binary nibabel scipy numpy
export DB_URL='postgresql://USER:PASSWORD@HOST:5432/DBNAME'   # never commit credentials
nohup python app.py > backend.log 2>&1 &

# frontend
nvm install --lts
npm install
nohup npm run dev > frontend.log 2>&1 &        # http://hpc.psy.ntu.edu.tw:3000/lotus-bf/
# production build instead:  npm run build && npm run preview
```
The Vite dev server proxies `/terms`, `/query` and `/help` to `http://hpc.psy.ntu.edu.tw:5000`.
To use another backend: `LOTUS_API=http://localhost:5000 npm run dev`.

## API
| endpoint | description |
|---|---|
| `GET /terms`, `GET /terms/<term>` | all terms; related terms (Jaccard) |
| `GET /query/<q>/studies?r=6` | matching studies |
| `GET /query/<q>/locations?r=6` | coordinates of matching studies |
| `GET /query/<q>/nii?fwhm=10&kernel=gauss` | meta-analytic map (MNI152 2 mm) |
| `GET /query/<q>/parse` | how the query is interpreted (no DB access) |

Syntax: terms (`emotion`, `"working memory"`, `emot*`), locations `[x,y,z]`, operators `AND OR NOT ( )`.
Precedence NOT > AND > OR; a space means AND. Malformed queries return HTTP 400 with `{"error": …}`.

## Tests
```bash
pip install pytest psycopg2-binary
python tests/seed_db.py "dbname=lotus user=postgres host=localhost"      # synthetic DB with known answers (needs PostGIS)
DB_URL=postgresql://postgres@localhost/lotus pytest -q tests/             # 38 tests
```
