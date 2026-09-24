# Final Validation Report — Research Benchmarking & System Validation (P26)

**Date:** 2026-09-24  
**Environment:** Windows 11, Python 3.13.15 (uv-managed `.venv`), package `universal-hospital-extractor` 0.1.0  
**Scope:** Final planned phase P26 — research benchmarking harness, CLI, fixtures, tests, final research documentation, hygiene.  
**Prior baseline:** P25 = **838 passed** (ruff clean, mypy clean on 170 files).  
**This report is the FINAL planned phase.** No P27 is planned.

---

## 1. Research questions and claims status

| Claim area | Status |
|---|---|
| Deterministic fixture **TEST CORRECTNESS** (A–U) | **MEASURED** in this harness |
| Real-world site results | **OBSERVED** from prior documented runs (2026-09-24) — not re-executed, never re-labeled as model accuracy |
| Full human-labeled live-site gold accuracy | **NOT MEASURED** (no gold set exists) |
| Production HTTP p95 / cloud SLA | **NOT APPLICABLE** (no production deployment claimed) |
| Docker image runtime | **NOT MEASURED** (daemon unavailable on this host) |
| Remote GitHub Actions green | **NOT MEASURED** (workflow not executed on GitHub here) |
| Real hospital API / gov credentials | **NOT INCLUDED** — requires authorized source configuration and credentials |
| Composite overall score | **NEVER COMPUTED** (by design) |

**Disclaimer:** Real hospital API/feed integrations require an authorized source configuration and credentials and are not included by default.

---

## 2. Methodology

- Deterministic in-process fixtures with explicit ground truth (`src/hospital_extractor/benchmark/fixtures.py`).
- Each metric is decomposed with raw counts (`numerator` / `denominator`) when available.
- Metric status vocabulary: `AVAILABLE` / `NOT_MEASURED` / `NOT_APPLICABLE`.
- Failures are recorded in the report; never hidden; never averaged into a single score.
- Real-world observations reuse documented prior runs only (Max / Manipal / Apollo / Fortis).
- Performance timings use `time.perf_counter` over in-process workloads (labeled as such — not production latency).

---

## 3. Benchmark package inventory

| Path | Role |
|---|---|
| `src/hospital_extractor/benchmark/models.py` | Typed models: `BenchmarkRun`, `BenchmarkCase`, `BenchmarkResult`, `MetricResult`, `MetricStatus`, `BenchmarkSummary`, `EnvironmentInfo`, `RealWorldObservation` |
| `src/hospital_extractor/benchmark/fixtures.py` | Deterministic fixtures A–U + ground-truth dict |
| `src/hospital_extractor/benchmark/_snap.py` | Snapshot helper for change-detection cases |
| `src/hospital_extractor/benchmark/runner.py` | 13 executable cases + 3 observation-only cases + real-world observations + limitations |
| `src/hospital_extractor/benchmark/formatters.py` | `format_benchmark_json`, `format_benchmark_markdown` |
| `src/hospital_extractor/benchmark/__init__.py` | Public API re-exports |
| CLI | `hospital-extractor benchmark --format json\|markdown [--output PATH] [--case ID]` |
| Tests | `tests/test_benchmark.py` (35 tests) |

---

## 4. Case matrix (executable + observation)

| Case ID | Category | Status |
|---|---|---|
| extraction | extraction | pass |
| change_detection | change_detection | pass |
| verification | verification | pass |
| reconciliation | reconciliation | pass |
| monitoring | monitoring | pass |
| connectors | connectors | pass |
| search | search | pass |
| comparison | comparison | pass |
| quality | quality | pass |
| security | security | pass |
| deployment | deployment | pass |
| reliability | reliability | pass |
| performance | performance | **observation** (in-process timings) |
| real_world_live_revalidation | real_world | **observation** (`NOT_MEASURED`) |
| docker_runtime | deployment | **observation** (`NOT_MEASURED` — no daemon) |
| remote_ci | deployment | **observation** (`NOT_MEASURED` — not run on GitHub) |

Full run summary (representative, this host): **16 cases — 12 pass, 0 fail, 4 observation, 0 error**; metrics available≈53, not_measured≈5, not_applicable≈1; **no composite score**.

---

## 5. Fixture ground truth (synthetic, controlled)

- Unchanged bed_count = 300; modified = 450; unknown bed_count = `None`.
- Treatments: base 1 → added 2 → removed 1; verified claim includes Cardiac Surgery; mismatch website beds = 420 vs claim 300.
- Search hospitals: 3 (alpha/beta/gamma); gamma UNKNOWN bed_count preserved.
- Reconciliation transitions match the implemented `ALLOWED_TRANSITIONS` table (open / under_review / resolved / reopened / dismissed).
- Ground truth is the construction of the fixture itself — not “the test passed.”

---

## 6. Quality metrics (P23 formulas exercised)

From `build_dataset_quality_report` on stored rows only:

- `hospital_field_coverage_rate` = populated / tracked (raw counts kept)
- `unknown_field_rate`, `duplicate_rate`, `conflict_rate`, `freshness_age_days`
- **`overall_score` remains `null`** on both dataset and pipeline reports

---

## 7. Security matrix (benchmarked locally)

| Control | Result |
|---|---|
| RBAC: every `Role` in `PERMISSIONS_BY_ROLE` | pass |
| VIEWER lacks `CAPTURE_SOURCE` | pass |
| ADMIN has `MANAGE_USERS` | pass |
| Audit event persisted via `record_audit` / `list_audit` | pass |
| Password not stored plaintext (scrypt digest) | pass |
| Production + `require_auth=false` → `StartupConfigError` | pass |
| Unauthorized connector policy → fail closed, no network | pass |
| Allowlisted HTTPS host passes local `assert_safe_url` | pass |
| `CredentialRef.__repr__` exposes env var name only | pass |
| Unset credential env → `None` | pass |

---

## 8. Deployment / ops smoke (this host)

| Check | Result |
|---|---|
| `verify-startup` | ok (development mode) |
| `GET /health` | 200 `{"status":"ok","check":"liveness"}` |
| `GET /ready` | 200 (config + DB + templates + static) |
| `GET /api/v1/health` | 200 |
| `worker --once` | exit 0, 0 due jobs |
| Console scripts | `hospital-extractor` entry point declared and working |
| Wheel + sdist build | `universal_hospital_extractor-0.1.0-py3-none-any.whl` + `.tar.gz` |
| Clean-install (isolated 3.13 venv) | installs; `verify-startup` OK; benchmark extraction case pass |
| Docker runtime | **NOT RUN** — `docker` not on PATH / no daemon |
| Remote CI | **NOT RUN** on GitHub — workflow present only |

---

## 9. Real-world observations (prior documented runs only)

Evidence: `docs/validation-report-post-p19c.md` (Phases 11–14) and `docs/validation-report-post-p19d.md`.

| Site | Classification | Raw counts (prior) |
|---|---|---|
| Max Healthcare | exact_baseline_match | 103 pages, 1632 records, 804 dups, 152 conflicts, 53 specialities, 54 doctors |
| Manipal Hospitals | expected change + known JS limitation | 137 pages, 8329 records, 7776 dups, 55 conflicts, specialities 460 (baseline 480) |
| Apollo Hospitals | access_denied_403 | HTTP 403, exit 1, recorded not bypassed |
| Fortis Healthcare | reachable but JS-rendered near-empty | 200-class pages fetched historically, 0 structured records extracted |

These are **observations**, not accuracy metrics. Site drift is expected. Apollo 403 and Fortis JS rendering are site limitations, not model accuracy claims.

---

## 10. Explicit limitations

1. Fixture metrics = **TEST CORRECTNESS**, not clinical or real-world accuracy.
2. No full human-labeled gold standard for live sites → `real_world_field_accuracy` is **NOT MEASURED**.
3. Performance numbers are in-process micro timings, **not** production HTTP p95.
4. Real-world results are prior observations (2026-09-24), not re-executed in this run.
5. No government or hospital API credentials used; connector success = local allowlist only.
6. Docker runtime and remote GitHub CI not executed here.
7. No composite overall score is defined or reported.

---

## 11. Full validation (this phase)

| Gate | Result |
|---|---|
| `pytest -q` | **873 passed** (838 baseline + 35 new `tests/test_benchmark.py`) |
| `ruff check src tests` | **All checks passed** |
| `mypy src` | **0 issues / 176 files** |
| `database.py` SHA256 | **`A07EB6E893A2ADC3A89855313430FC6D148D283C00E5B973A4EF20CC56C27F6D` unchanged** |
| Benchmark CLI JSON | exit 0 (Windows stdout encoding-safe) |
| Benchmark CLI Markdown | exit 0 |
| Unknown `--case` | exit 2 with stderr message |
| Secrets scan (src/tests) | no AWS/private-key/live-password patterns; fixture password only in benchmark AuthStore smoke |
| Worktree | **nothing committed, nothing pushed** (HEAD `a651c14`) |

---

## 12. Documentation deliverables

| Doc | Status |
|---|---|
| `docs/final-validation-report.md` | this file (final research report) |
| `docs/validation-report-post-p25.md` | retained (prior phase) |
| `docs/deployment.md` | retained |
| `docs/architecture.md` | retained; no over-claims found for composite scores |
| README | no opaque overall score language for quality/benchmark |

---

## 13. Quality review findings

| Finding | Classification | Action |
|---|---|---|
| `database.py.corrupted-p19e-backup` | stale artifact | **Deleted** after SHA of live `database.py` verified + full suite green |
| `orchestration/service.py:553` `TODO: Implement proper resume logic` | pre-existing intentional backlog | **left as-is** (not a P26 defect; resume semantics out of scope) |
| `# noqa: BLE001` in benchmark runner | intentional — record exceptions as case errors, never hide | **kept** |
| `type: ignore` elsewhere (settings `replace`, API middleware) | pre-existing, ruff/mypy clean | **kept** |
| Windows cp1252 stdout on `→` / em-dash | genuine CLI defect | **fixed** (UTF-8-safe write in `_run_benchmark`) |
| Relative `pyproject.toml` path in deployment case | genuine portability defect | **fixed** (anchored to package parents) |
| Fixture `HospitalOverview(source_id=...)` invalid field | genuine fixture defect | **fixed** (removed; provenance uses `source_url`) |
| `ComparisonResult.dimensions` did not exist | genuine case bug | **fixed** (dimensions live on `HospitalComparison`) |

No secrets, temp files, or fabricated metrics introduced.

---

## 14. What is NOT claimed

- Live cloud deployment, Kubernetes, managed DB, SLA  
- Remote GitHub Actions green (workflow present; not executed on GitHub here)  
- Docker image build/run success (daemon unavailable)  
- Hospital API/feed agreements, credentials, or government integration  
- Autonomous medical decisions  
- Real-world extraction accuracy percentages  
- Any composite / overall score  

**Disclaimer:** Real hospital API/feed integrations require an authorized source configuration and credentials and are not included by default.

---

## 15. Implementation vs mock / authorization

| Capability | Classification |
|---|---|
| Website crawl / extract / export (local) | IMPLEMENTED |
| Mock / JSON feed connectors | IMPLEMENTED (local, offline) |
| Authorized hospital API connector | IMPLEMENTED boundary + fail-closed policy; **requires authorized config + credentials** |
| Government submission exporter | **SIMULATED** local dry-run only (`dry_run: true`, never uploaded) |
| Benchmark harness + CLI | IMPLEMENTED |
| Docker runtime / remote CI execution | **NOT EXECUTED** here |

---

## 16. Failure analysis (honest)

| Issue | Phase | Outcome |
|---|---|---|
| Docker daemon unavailable | P25/P26 | Reported NOT MEASURED; static Dockerfile checks only |
| Windows console cp1252 vs Unicode arrows | P26 | Detected during CLI smoke; fixed; regression covered by CLI tests |
| Apollo 403 / Fortis JS-rendered | prior real-world | Expected site limitations; recorded, not bypassed |
| Live accuracy without gold labels | P26 | Explicitly NOT MEASURED rather than invented |

---

## 17. Test inventory delta

- Baseline (P25): **838**
- New P26: **+35** (`tests/test_benchmark.py`)
- Final: **873**
- No existing tests deleted or weakened.
- Ruff/mypy suppressions not increased for product code beyond intentional `noqa: BLE001` error-recording paths already required by “record, never hide.”

---

## 18. Reproducibility

```text
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check src tests
.venv\Scripts\python.exe -m mypy src
.venv\Scripts\hospital-extractor.exe benchmark --format json
.venv\Scripts\hospital-extractor.exe benchmark --format markdown --output bench.md
.venv\Scripts\hospital-extractor.exe verify-startup
.venv\Scripts\hospital-extractor.exe worker --once
```

Environment fields captured on every run: Python version, OS, package version, implementation.

---

## 19. Git / hygiene state

- HEAD: `a651c14` — **no commit, no push** this phase.
- `src/hospital_extractor/storage/database.py` SHA frozen and verified.
- Stale backup `database.py.corrupted-p19e-backup` **removed** after verification.
- `dist/` build artifacts present locally from validation builds (untracked; not required for runtime).

---

## 20. Final verdict

**PASS — P26 complete; FINAL planned phase.**

- Research benchmark package, fixtures, runner, formatters, and CLI implemented and tested.
- Full suite green: **873 / ruff 0 / mypy 0 (176 files) / SHA unchanged**.
- Honesty invariants preserved: no composite score, no fabricated accuracy, no remote/Docker success claims, TEST CORRECTNESS ≠ REAL-WORLD ACCURACY.
- **Stop.** No P27. Nothing committed. Nothing pushed.
