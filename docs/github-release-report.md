# GitHub Release Report — MedCompare

**Project:** MedCompare
**Owner/Author:** Devansh Dhama
**Target repository:** `https://github.com/JAT010/medcompare`
**Branch:** `main` (single fresh initial commit)
**History:** fresh — no history from any previous repository was carried over. Earlier
local `.git` directories were preserved aside as `.git-old*` (never pushed).

## 1. Audit scope

Inspected before publishing: working tree status and diff, branch, remotes, tracked and
untracked files, backend (`src/`, `tests/`, `config/`, `pyproject.toml`, `uv.lock`,
`Dockerfile`, `.dockerignore`, `.github/workflows/ci.yml`), frontend (`frontend/` with
`package.json` + `package-lock.json`), docs (`README.md`, `docs/*.md`) and demo fixtures
(`tests/fixtures/medcompare_demo/*.yaml`).

## 1b. Previous-identity cleanup

Searched every project file for old repository/ownership context and updated only
identity-related references (no application behaviour changed):

| Found | Location | Action |
| --- | --- | --- |
| Old repo URL in crawler `User-Agent` | `src/hospital_extractor/crawler/crawler.py` | → `https://github.com/JAT010/medcompare` |
| Old repo URL in fallback `User-Agent` | `src/hospital_extractor/crawler/context.py` | → `https://github.com/JAT010/medcompare` |
| Old repository name as README title | `README.md` | → `# MedCompare` + owner/repository line |
| Old repository name in doc titles | `docs/system-summary.md`, `docs/demo-system-summary.md` | → `MedCompare` |
| No author/repository metadata | `pyproject.toml` | → `authors = [{name = "Devansh Dhama"}]`, `urls` |

No other old username, author name, badge, package metadata or CI reference remained
(third-party dependency links in `package-lock.json` were left untouched).

## 2. Validation results (run locally, this machine)

| Check | Result | Command |
| --- | --- | --- |
| Backend tests | **912 passed** (1 warning from `fastapi.testclient`) | `.venv\Scripts\python.exe -m pytest -q` |
| Lint | **0 issues** | `.venv\Scripts\python.exe -m ruff check src tests` |
| Types | **0 issues in 179 files** | `.venv\Scripts\python.exe -m mypy src` |
| Package build | **ok** (`universal_hospital_extractor-0.1.0` wheel + sdist) | `.venv\Scripts\python.exe -m build` |
| Frontend tests | **29 passed (6 files)** | `npm run test` |
| Frontend types | **0 issues** | `npm run typecheck` |
| Frontend lint | **0 issues, 0 warnings** | `npm run lint` |
| Frontend production build | **ok** (315.89 kB / 95.04 kB gzip) | `npm run build` |

No application code was changed to make any check pass. `.gitignore` was extended only to
keep local/session artifacts out of the repository.

**Not verified:** GitHub Actions CI was not observed running on GitHub (the workflow file
is committed; no remote run was executed or claimed). Docker image build/run was not tested
(Docker daemon not available on this machine). Live external hospital API integrations were
not tested — all demo data is synthetic.

## 3. Secret safety

Scanned all 281 staged files plus the full working project for API keys, passwords, bearer
tokens, GitHub tokens, private keys, cloud credentials, database connection strings and
hardcoded demo secrets.

* No critical hits: no demo user passwords, no JWT/bearer values, no private keys, no
  cloud/GitHub tokens, no `.token` files, no `.env`.
* The only password literals are synthetic fixture values inside test and benchmark code
  (`tests/test_security.py`, `tests/test_demo_env.py`, `tests/test_deployment.py`,
  `src/hospital_extractor/benchmark/runner.py`) — locally created test users, not credentials.
* Demo credentials are supplied through environment variables at runtime
  (`MEDCOMPARE_DEMO_*_PASSWORD`); no value is written into the repository.

## 4. Intentionally excluded from the commit

| Excluded | Reason |
| --- | --- |
| `.venv/`, `venv/` | virtual environment |
| `frontend/node_modules/`, `frontend/dist/`, `*.tsbuildinfo` | JS dependencies and build output |
| `__pycache__/`, `*.pyc`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/` | caches |
| `data/` (incl. `data/*.db`, `data/**/*.db`, crawl corpus) | local/demo SQLite databases and scraping artifacts; recreatable from seeds/fixtures |
| `dist/`, `build/` | Python build artifacts |
| `demo-evidence/` (36 screenshots, `browser-report.json`, probe scripts, Vite logs) | session/demo evidence, generated locally |
| `.env`, `.env.*`, `*.token`, `*.pem`, `*.key` | secrets |
| `.vscode/`, `.idea/`, `.DS_Store`, `Thumbs.db`, `desktop.ini`, `.git-old*/` | editor/OS junk and the retired histories |

Demo data is recreatable from `src/hospital_extractor/demo/seed.py`,
`tests/fixtures/medcompare_demo/` and the docs in `docs/demo-runbook.md`.

## 5. Reproducing the demo

Backend: `.venv\Scripts\python.exe -m hospital_extractor.cli serve --db data/demo/medcompare-demo.db --host 127.0.0.1 --port 8000`
with `MEDCOMPARE_REQUIRE_AUTH=1`, `MEDCOMPARE_AUTH_MODE=development` and
`MEDCOMPARE_CORS_ORIGINS=http://localhost:5173`.

Frontend: `npm run dev` in `frontend/` (Vite proxies `/api`, `/health`, `/ready` to
`http://127.0.0.1:8000`).

Demo users: `admin@medcompare.demo`, `user@medcompare.demo`, `reviewer@medcompare.demo`
(passwords from the operator's environment; see `docs/demo-runbook.md`).
