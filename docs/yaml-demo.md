# YAML Data Collection + Validation Demo

A self-contained, offline demonstration that the MedCompare pipeline logic
works end-to-end on synthetic data: collection → provenance → YES/NO/UNKNOWN
→ baseline vs current diff → conflict detection → quality metrics → automatic
PASS/FAIL.

**Disclaimer:** This is a DEMO/TEST fixture, not real hospital data. Fictional
`*.example.test` domains only. No credentials, API keys, tokens, or patient
data. Real hospital API/feed integrations require an authorized source
configuration and credentials and are not included by default. Search queries
are not persistently stored.

---

## 1. What the fixture represents

Directory: `tests/fixtures/medcompare_demo/`

| File | Role |
|---|---|
| `baseline.yaml` | First snapshot of 3 fictional hospitals (`demo_a`, `demo_b`, `demo_c`) |
| `current.yaml` | Second snapshot: `demo_a` modified, `demo_b`/`demo_c` unchanged, `demo_d` added |
| `provenance.yaml` | Where field values came from (`json_ld`, `semantic_html`, `card`, `heading`, `not_found`) |
| `conflict.yaml` | Two disagreeing phone numbers for `demo_b` |
| `expected.yaml` | Outcomes the validator must match (never used to compute them) |

Hospital fields use the MedCompare-shaped schema: `hospital_id`,
`hospital_name`, `city`, `source_url`, `beds`, `emergency`, `cardiology`,
`orthopaedics`, `cath_lab`, `nabh`, `jci`, `specialities`, `facilities`.

Tri-state values are **quoted strings** so YAML never coerces them to booleans:

```yaml
emergency: "YES"     # not bare yes/YAML 1.1 true
jci: "NO"
cath_lab: "UNKNOWN"
beds: 500            # int
beds: "UNKNOWN"      # string when unpublished
```

## 2. Baseline vs current snapshots

**Baseline** (3 hospitals):

- `demo_a` — Delhi, 500 beds, emergency/cardiology/orthopaedics YES,
  cath_lab **UNKNOWN**, NABH YES, JCI NO
- `demo_b` — Noida, 300 beds, orthopaedics NO, cath_lab YES, NABH **UNKNOWN**
- `demo_c` — Ghaziabad, beds/cardiology/cath_lab **UNKNOWN**, emergency NO

**Current** (4 hospitals):

- `demo_a` modified: beds 500→550, cath_lab UNKNOWN→YES, specialities +Oncology,
  facilities +MRI
- `demo_b`, `demo_c` byte-for-byte semantic copies of baseline
- `demo_d` added: Surat, 250 beds, orthopaedics + NABH **UNKNOWN**

## 3. Provenance

Each provenance record carries `hospital_id`, `field`, `source_url`,
`extraction_method`, `confidence`, `captured_at`. At least one example per
method, including **`not_found`** for values observed as UNKNOWN (the field
was sought and not published).

## 4. Conflict example

`demo_b.phone` has two values from two extraction paths:

- `+91-120-40000002` (json_ld)
- `+91-120-49999999` (semantic_html)

Expected semantic result: **CONFLICT**. The demo never silently picks a
winner; it builds the existing
`hospital_extractor.models.conflict.Conflict` model with both values and
sources, `resolution_status=unresolved`.

## 5. How changes are calculated

```
YAML hospital record
  → HospitalDataset (canonical models: HospitalOverview, Contact, Facility,
    Speciality, Accreditation)          # hospital_to_dataset()
  → snapshots.change_detection.compute_changes(baseline_ds, current_ds)
  → non-empty changes? MODIFIED : NO_CHANGE
```

Hospital-level membership is pure set arithmetic:

- `added` = current IDs − baseline IDs
- `removed` = baseline IDs − current IDs

**The validator does not reimplement change detection.** It reuses the same
`compute_changes` service the live pipeline uses. Mapping (demo-only,
one-way):

| YAML field | Canonical home |
|---|---|
| `hospital_name`, `beds` (int) | `HospitalOverview.name` / `.bed_count` |
| `city`, `phone` | `Contact` |
| `emergency`, `cardiology`, `orthopaedics`, `cath_lab` | `Facility(name=…, availability=tri-state)` |
| `nabh`, `jci` | `Accreditation(status=tri-state)` |
| `specialities`, `facilities` lists | canonical collections |

## 6. How UNKNOWN is handled

- `UNKNOWN` is a distinct string token — never rewritten to `NO`, `null`, or
  `False`.
- Change detection sees `UNKNOWN` → `YES` as a real field change
  (`demo_a.cath_lab`).
- Quality metrics count UNKNOWN as its own rate (`unknown_rate`), separate
  from empty/missing coverage.
- The test asserts `UNKNOWN != "NO"` and `UNKNOWN != False`.

## 7. How to run the test

```powershell
.venv\Scripts\python.exe -m pytest tests/test_medcompare_demo.py -q
```

Or via the existing `demo` CLI subcommand (no second CLI framework):

```powershell
.venv\Scripts\python.exe -m hospital_extractor.cli demo check
.venv\Scripts\python.exe -m hospital_extractor.cli demo check --json
# after install also: hospital-extractor demo check
```

Exit code `0` = PASS, `1` = FAIL (or missing fixtures).

## 8. Example output

```
MedCompare Demo Validation
==========================

Baseline hospitals : 3
Current hospitals  : 4

Added:
  demo_d

Modified:
  demo_a

Unchanged:
  demo_b
  demo_c

Removed:
  none

UNKNOWN values (current): 6
UNKNOWN values (baseline): 5
Provenance records: 9

Conflict:
  demo_b.phone -> CONFLICT (+91-120-40000002 / +91-120-49999999)

Quality (independent dimensions):
  field_coverage        = 0.770833
  unknown_rate          = 0.139535
  provenance_coverage   = 1.0
  conflict_present      = True
  overall_score         = null (never computed)

Result:
  PASS
```

Every number above is derived from the YAML at runtime — nothing is hardcoded
in the renderer.

## 9. Why the data is synthetic

- Hospitals, cities-with-fake-names, phones, and URLs are invented for this
  fixture (`demo-a.example.test` and friends — RFC 2606 reserved domain).
- No live crawl, no network, no randomness, no wall-clock values in expected
  outcomes → same YAML + same code ⇒ same results (determinism is asserted
  by running the validator twice).
- Keeps the demo safe to commit, run in CI, and delete.

## 10. Quality checks (no overall score)

Computed dimensions (P23 invariant preserved):

- `field_coverage` — populated scalar slots / total slots
- `unknown_rate` — UNKNOWN scalars / all scalars
- `provenance_unknown_coverage` — UNKNOWN fields with a provenance record
- `conflict_present` / `duplicate_values_detected`
- **no `overall_score` key exists**; the note states metrics are independent
  dimensions

## 11. Files touched by this demo

| Path | Purpose |
|---|---|
| `tests/fixtures/medcompare_demo/*.yaml` | Five fixtures |
| `tests/test_medcompare_demo.py` | Automatic validator test (PASS/FAIL) |
| `src/hospital_extractor/demo/yaml_demo.py` | Loader + adapter + computed validation |
| `src/hospital_extractor/cli.py` | `demo check` subcommand (existing parser) |
| `docs/yaml-demo.md` | This document |
| `pyproject.toml` | Added `pyyaml>=6.0` (inspected: not previously a dependency) |

Core extraction architecture and `storage/database.py` are **not** modified.
