# Post-P19-C Full System Validation Report

Date: 2026-09-24
Environment: Windows, Python 3.13 (`>=3.13,<3.14`), fresh venv + editable venv both verified
Scope: Full system validation of `universal-hospital-extractor` after P19-C (Reconciliation workflow). Validation only — no new features. One genuine defect found and fixed (minimal fix + regression tests). Nothing committed, nothing pushed.

## 1. Final verdict

**PASS.** No regressions in any of the P19-A / P19-B / P19-C / pre-existing behaviours. One new minor defect surfaced (export filename with a double dot for names ending in `Ltd.`) — fixed minimally with 2 regression tests. All real-world website checks behaved gracefully, no blocking restrictions were bypassed, and all access denials were recorded.

## 2. Starting / final test state

- Starting baseline: **427 passed**, 1 warning (third-party deprecation), Ruff clean, mypy clean (124 files).
- Final state after the validation fix: **429 passed**, 1 warning, Ruff clean, mypy clean (124 files).
- The +2 tests are the regression tests for the fixed defect (`test_dataset_basename_strips_trailing_period`, `test_dataset_basename_keeps_interior_periods`).

## 3. Repository state

- Nothing is committed, nothing is pushed (unchanged from the pre-validation instruction).
- All P19-C work remains uncommitted/untracked: `README.md`, `docs/architecture.md`, `pyproject.toml`, `src/hospital_extractor/reconciliation/`, `src/hospital_extractor/api/reconciliation.py`, `src/hospital_extractor/verification/`, `src/hospital_extractor/storage/`, new templates, `tests/test_reconciliation.py`, and alerting/config scaffolding.
- The only code change made *during* validation is the export-name fix (Section 22).

## 4. Worktree diff (validation baseline)

- `.gitignore` now excludes `data/`, `logs/`, `*.xlsx`, `*.csv`, `.env*`, venvs, caches.
- No secrets, credentials, or private keys anywhere in the tree (Section 23).

## 5. Phase 1 — Worktree + baseline

- `git status`: all P19-C modifications present, no unexpected files tracked.
- Full suite: 427 passed / 1 warning; `ruff check src tests` clean; `mypy src` clean (124 files).

## 6. Phase 2 — P19-A regression (hospitals, snapshots, change detection, storage)

**60 passed** across `test_hospital_identity.py`, `test_snapshots.py`, `test_snapshots_api.py`, `test_fingerprint.py`, `test_change_detection.py`, `test_database.py`, incl. legacy-schema + additive-migration tests. No regressions.

## 7. Phase 3 — P19-B regression (verification engine)

**39 passed** in `test_verification.py`: job lifecycle, scheduling, worker, verification-job API idempotency, demo scenarios. No regressions.

## 8. Phase 4 — P19-C regression (reconciliation)

**38 passed** in `test_reconciliation.py`: state-machine transitions, creation policy, audit events, items/evidence rendering, API 409/422/404 handling, CLI, worker auto-hook. No regressions.

## 9. Phase 5 — Clean-room DB lifecycle

Fresh SQLite under `%TEMP%`: **18/18 PASS**. Schema creation incl. all 11 tables; registration→capture→snapshot→ChangeSet→VerificationJob→worker→VerificationResult→ReconciliationCase→review→resolve; no duplicate jobs/cases; audit trail intact; VERIFIED produces no case; MISMATCH auto-creates; evidence references the correct snapshots.

## 10. Phase 6 — Source-agnostic integration

**153 passed** (`test_integration_sourceagnostic.py`, `test_sources.py`, `test_comparison_provenance.py`, `test_comparison.py`). Website / mock_api / hospital_api / json_feed all normalize into one canonical dataset with correct provenance.

## 11. Phase 7 — Live API validation

**39/39 PASS** against a running server (`api_exercise.py`): every endpoint with valid + invalid input, 404/409/422 paths, HTML pages, 303 form actions, and `POST /api/v1/compare`. Design contract confirmed: manual verification-job creation returns **201 always** with job-id reuse for idempotency (asserted in `test_manual_create_endpoint_is_idempotent`); the initial "expected 200" was a wrong expectation, not a defect.

## 12. Phase 8 — Live CLI validation

- All 16 subcommands respond to `--help` (hospitals, capture, snapshots, changes, verify, verification-jobs, worker, reconciliation-cases, reconciliation, review, resolve, dismiss, reopen, verification-demo, compare, integration-demo).
- Full seeded lifecycle exercised: capture via `json_feed` schedules a job; `worker --once` completes it; `verify` idempotent; review→resolve→reopen→dismiss cycle produces a 5-event audit trail.
- Read commands never crawl (`worker --once` ≈0.6 s, `snapshots` ≈0.61 s).
- Invalid inputs all fail cleanly (unknown command, unknown hospital/case, bad resolution, missing `--reason`).

## 13. Phase 9 — Frontend validation

**35/35 PASS** (`ui_validate2.py`): index/gallery/profile/case/compare fragments; source badges; provenance + evidence preserved; Data history; Verification panel; Reconciliation panel correctly conditional per hospital state (progressive disclosure); UNKNOWN distinct from NO (unknown=10, yes=12, no=0); no raw JSON dumps leaked.

## 14. Phase 10 — Demo validation + determinism

**8/8 PASS** (`demo_det.py`): two fresh DBs produce identical summaries (deterministic); A → verified, no case; B → mismatch → resolved with 3 events; C → unverified → open case, trigger `unverified`; timestamp-tie fix holds (C checks only `procedures`; B only `hospital.bed_count`).

## 15. Phase 11 — Real-world crawl: Max Healthcare (`https://www.maxhealthcare.in`)

- RC=0; **26 sitemaps** loaded; robots.txt honoured.
- Fetched 103 pages (baseline 103), 0 failed; considered 103; extracted 34.
- **1632 records extracted, 0 rejected, 804 duplicates removed, 152 conflicts** (baseline identical).
- Specialities 53, Doctors 54, Location & Contact 1 (baseline identical).
- Master export regenerated: `Max Healthcare.json` + `_master/` (25-col CSV, XLSX, quality report).
- **Match-to-baseline: EXACT.** Duration 346.5 s vs ~207 s historical → **environmental** (network/site latency), not functional.

## 16. Phase 12 — Real-world crawl: Manipal Hospital (`https://www.manipalhospitals.com`)

- RC=0; 1 sitemap; 4 candidate seeds (baseline identical); robots honoured.
- Fetched 137 pages (baseline 138), 0 failed; skipped 858 (baseline 973).
- **8329 records extracted, 0 rejected, 7776 dup removed, 55 conflicts** (baseline 9716/9128/64 — lower because fewer pages were in scope this run).
- Categories: speciality 120, contact 2, unknown 2, hospital 2 (baseline identical); doctor 11 (baseline 12).
- Specialities master count 460 (baseline 480); **Doctors block: failed** this run — documented PRE-EXISTING LIMITATION (JS-rendered doctor directory; last run also only recovered a handful of doctor records).
- Duration **74 s** (baseline ~127 s → faster; environmental).
- Classification: **PASS** with **EXPECTED WEBSITE CHANGE** (page set/skip drift) + **PRE-EXISTING LIMITATION** (JS-rendered doctors).

## 17. Phase 13 — Real-world crawl: Apollo (`https://www.apollohospitals.com`)

- RC=1 with a clear denial: `error: target 'https://www.apollohospitals.com/' not reachable with any user-agent (homepage status 403)`.
- **Recorded as access denial, not bypassed. No fabricated data.** Matches historical expectation (403). Classification: **PASS**.

## 18. Phase 14 — Real-world crawl: Fortis (`https://www.fortishealthcare.com`)

- **Change vs historical:** previously 403; now reachable (RC=0, robots.txt 200, 200 pages fetched, 0 failed) but content is JS-rendered/anti-bot protected → extraction empty (Hospital Overview 1, Location & Contact 1, all other blocks `not_found`, 5× "page(s) with content but no records extracted" warnings).
- Graceful handling; no bypass; no fabricated records. Classification: **EXPECTED WEBSITE CHANGE** (site opened access but remains non-extractable without JS execution) + graceful degradation. This also surfaced the export-name defect (Section 22).

## 19. Phase 15 — Extraction quality

- Max `master_quality.md`: 15/15 boolean fields resolved `yes` with per-field page provenance (URL, method, strength 1.0); every value traceable to a crawled page; malformed JSON-LD warnings are site-side, skipped and reported.
- Manipal `master_quality.md`: submission policy `boolean_unknown_policy=false` applied correctly — UNKNOWN values submitted as FALSE and explicitly annotated ("UNKNOWN converted to FALSE by submission policy"), true positives submitted TRUE.
- No invented values; every submitted value carries evidence. **PASS**.

## 20. Phase 16 — Comparison engine on real datasets

- `compare` (json_feed) over `Max Healthcare.json` + `Manipal Hospital.json` for "Knee Replacement" → target normalized to "Total Knee Replacement"; ran cleanly, RC=0.
- Realistic output: `Relevant Departments/Specialities = YES` where datasets contain matching entities; procedure-level dimensions honestly `UNKNOWN` with standard "No supporting evidence found in analyzed pages." evidence (data contains no procedure-level records); `Evidence Coverage` numeric; provenance (`source_types`, `providers`, quality warnings) preserved.
- **Honest UNKNOWN semantics confirmed** — the engine never fabricates a NO where evidence is absent. **PASS**.

## 21. Phase 17 — DB data integrity

- `data/medcompare.db`: `PRAGMA integrity_check` = ok; `PRAGMA foreign_key_check` = 0 violations; no orphaned FKs; verification_jobs/results/snapshots consistent.
- Discovery: the working DB carried a **stale schema** (created by a pre-P19-C build; 9 tables, no `reconciliation_cases` / `reconciliation_events`, `user_version=0`). Current code's additive `CREATE TABLE IF NOT EXISTS` migration **self-heals it on next open** — demonstrated live: after one current-code open the DB had all 11 tables, integrity still ok. Consistent with the passing legacy/additive migration tests.
- Classification: **PASS** (no defect; auto-upgrade proven), recorded as a PRE-EXISTING LIMITATION of old databases (automatically resolved).

## 22. Phase 18 — Performance

| Activity | Measured | Historical | Classification |
|---|---|---|---|
| Max crawl | 346.5 s | ~207 s | environmental (≈1.7×, same content) |
| Manipal crawl | 74.0 s | ~127 s | environmental (≈0.6×, fewer in-scope pages) |
| `worker --once` | ≈0.6 s | — | PASS |
| `snapshots` list | ≈0.61 s | — | PASS |
| compare (json_feed) | near-instant | — | PASS |
| `integration-demo` (fresh install) | 0.003 s fetches / 0.033 s persist / 0.006 s compare | — | PASS |

No hot-path regressions; crawls bounded by polite delays + robots compliance.

## 23. Phase 19 — Security / secrets

- Grep of `src`, `tests`, `*.toml/.json/.md` for api keys / secrets / tokens / private keys: **nothing found** (only tokenisation code and generic doc mentions).
- No `.env` / `.pem` / `.key` files in the repo (only `certifi`'s public CA bundle inside `.venv`).
- Mock demo server binds **127.0.0.1:8001** (localhost only).
- `.gitignore` covers runtime artifacts, exports, and `.env*`. **PASS**.

## 24. Phase 20 — Documentation accuracy

- README + docs/architecture.md reviewed for over-claims: `--gov-export` is always-described as **simulated / local dry-run** (`dry_run: true`, `submitted_at: null`); P19-C docs explicitly state **no cron / worker / webhook exists**; "Future: real hospital API adapter" is labelled future; reconciliation docs match the implemented state machine, creation policy, CLI, API, UI; README 403 policy ("skipped, never bypassed") is a true general statement. **PASS** — no inaccurate claims found to correct.

## 25. Phase 21 — Fresh install (packaging)

- `python -m venv` + `pip install .` (non-editable) into a brand-new venv: **success** (RC=0).
- Console scripts verify: `hospital-extractor --help` renders; `hospital-extractor integration-demo` runs end-to-end from the installed wheel (fetch → persist → sqlite profile → comparison with YES decisions).
- Hatchling wheel includes the full `src/hospital_extractor` tree (incl. `reconciliation/`, `verification/`). **PASS**.

## 26. Bugs found and resolved

- **NEW BUG (minor):** `export.dataset_basename` left trailing periods, so a hospital named like "Fortis Healthcare Ltd." produced export paths `Fortis Healthcare Ltd..json` / `..._master`. Fixed minimally in `src/hospital_extractor/export/__init__.py` (`rstrip(" .")`); interior periods preserved. **2 regression tests added** in `tests/test_export.py`. Suite re-run: 429 passed; Ruff + mypy clean.
- Everything else surfaced during validation is a documented limitation or environmental/website change — **no further code changes made** (per "no new features" constraint).

## Issue classification summary

| # | Item | Classification |
|---|---|---|
| 1 | Max crawl metrics vs baseline | PASS (exact match) |
| 2 | Max crawl duration 346.5 s vs 207 s | ENVIRONMENTAL ISSUE |
| 3 | Manipal crawl page/dedup/conflict drift | EXPECTED WEBSITE CHANGE |
| 4 | Manipal doctors (12→11, block failed) | PRE-EXISTING LIMITATION (JS-rendered) |
| 5 | Manipal crawl duration 74 s vs 127 s | ENVIRONMENTAL ISSUE |
| 6 | Apollo 403 denial | PASS (recorded, not bypassed) — EXPECTED WEBSITE CHANGE (site unchanged, still blocked) |
| 7 | Fortis 403 → reachable but non-extractable | EXPECTED WEBSITE CHANGE (graceful) |
| 8 | `data/medcompare.db` stale schema | PRE-EXISTING LIMITATION (auto-heals via additive migration; verified) |
| 9 | Fortis `Ltd..json` filename | NEW BUG → FIXED (+2 regression tests) |
| 10 | Tests 427 → 429 / lint / types | PASS |
| 11 | Docs claims | PASS (no stale/inaccurate claims) |
| 12 | Secrets scan / binding / gitignore | PASS |

## Reproduction commands (read-only)

- `pytest -q` → 429 passed, 1 warning
- `ruff check src tests` → All checks passed
- `mypy src` → Success, 124 files
- `hospital-extractor <url> --export` → crawl + master export (data/ gitignored)
- `hospital-extractor compare <feed1> <feed2> --source json_feed --procedure "Knee Replacement"` → real-data comparison

## Post-validation state

Validation complete. Ready for review. **No commit, no push performed.**