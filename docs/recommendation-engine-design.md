# Recommendation Engine Design

Status: Milestone 2A deliverable. This document records the deterministic
contract for the standalone recommendation package. It complements
`preference-data-readiness.md`, which maps each criterion to extracted data.

## Scope

- The package lives in `hospital_extractor.recommendation` and is
  framework-agnostic. It performs no network, database, LLM or UI work.
- The caller supplies a stable `hospital_id`, a `HospitalDataset` and an
  authoritative `HospitalAuthorizer`.
- Mandatory constraints are evaluated before suitability scoring. An `unknown`
  mandatory result is never treated as satisfied.
- The engine never invents a criterion score. Missing evidence stays unknown and
  is reflected in coverage and confidence.

## Preference support states

Every positive requested weight is assessed into exactly one state:

| State | Meaning | Scorable |
|-------|---------|----------|
| `supported` | Extracted data can score the criterion for this request | yes |
| `partially_supported` | Only a narrow proxy signal exists | yes |
| `unsupported` | No extracted field exists | no |
| `requires_clarification` | Data exists but the request omitted a required input | no |

The static data support comes from `criteria.py`. The request-time state adds
clarification checks:

| Criterion | Requires clarification when |
|-----------|-----------------------------|
| `location` | No location requirement, or coordinates without `radius_km` |
| `department_expertise` | No selected departments or specialities |
| `diagnostic_facilities` | No selected diagnostics |
| `treatment_facilities` | No selected treatments |

Unsupported preferences are never dropped silently. They remain active and are
reported through `unsupported_criteria`, `support_states` and the per-preference
`PreferenceAssessment`.

## Two weight bases

The engine deliberately keeps two normalized bases:

- **Suitability weights** normalize only scorable preferences to 100. They drive
  the weighted mean of usable criterion scores.
- **Coverage weights** normalize every active preference to 100, including
  unsupported and clarification-required preferences. Those preferences carry
  weight but zero coverage.

This prevents a high-priority unsupported preference from disappearing. For
example, if location is 60% and affordability is 40%, a location-only score has
suitability 100 but evidence coverage 0.60. The candidate is therefore capped at
`good_match` at best and confidence is `low`.

If no preference is scorable, the service returns `scoring_available=False`,
empty results, the support assessments and explicit warnings. It does not raise
for a request that is merely unsupported or missing a required input.

## Evidence states

Each `CriterionScore` exposes both `preference_state` and `evidence_state`:

| Evidence state | Meaning |
|----------------|---------|
| `verified_available` | Positive evidence with full criterion coverage |
| `verified_unavailable` | Published category proves a non-match with full coverage |
| `partial` | Some supporting evidence, but criterion coverage is below 100% |
| `unknown` | No usable evidence |
| `unsupported` | The criterion has no extracted data source |
| `requires_clarification` | The request omitted a required input |

## Match categories and confidence

Match categories remain conservative and coverage-aware:

- `insufficient_evidence`: no suitability score or coverage below the request
  threshold.
- `strong_match`: suitability and coverage both meet the strong thresholds.
- `good_match`: suitability and coverage both meet the good thresholds.
- `possible_match`: suitability meets the possible threshold.
- `weak_match`: everything else.

Confidence is separate from the match category:

- `insufficient` when the match category is `insufficient_evidence`.
- `low` when any unsupported or clarification-required preference is active.
- `medium` when a `strong_match` rests on partially supported criteria, or when
  the match category is `good_match`.
- `high` only for a `strong_match` with no unavailable or partially supported
  preferences.

## Response contract

`RecommendationResponse` carries:

- `scoring_available`
- `weights_used` (suitability basis)
- `coverage_weights` (all active preferences)
- `unsupported_criteria`
- `clarification_required`
- `partially_supported_criteria`
- `support_states`
- `preference_assessments`
- `warnings`
- `results`, `excluded`, `candidates_considered`, `eligible_count`,
  `returned_count`

Each `RecommendationResult` carries `suitability_score`, `match_category`,
`confidence`, criterion scores with preference/evidence states, evidence
coverage and deterministic explanations.

## Tri-state intake integrity

The 25-field intake uses `yes`, `no` and `unknown`. Milestone 2A makes the
distinction explicit:

- `MasterIntake.boolean_state(key)` returns the resolved tri-state value and
  defaults a missing key to `unknown`, never `no`.
- `MasterIntake.unknown_boolean_keys()` lists every boolean field still
  unresolved.
- CSV and XLSX exports read through these helpers.
- The default `boolean_unknown_policy` is `UNKNOWN_KEEP`, so unresolved
  capabilities are exported as `UNKNOWN` rather than verified absence.
- `UNKNOWN_FALSE` remains available as an explicit legacy compatibility mode for
  downstream consumers that only accept `TRUE`/`FALSE`. It can be selected in
  `config/default.toml` or with `--boolean-unknown-policy false`; each conversion
  is recorded in the quality report.
- Export never mutates the projection's resolved values or evidence trail.

## Ranking and determinism

- Candidates are ranked by suitability descending, then evidence coverage
  descending, then `hospital_id` ascending.
- All ordering is explicit and stable; repeated runs produce identical output.
- Explanations are template-based and contain no model inference.

## Limitations

- Unsupported criteria cannot be compared until new extraction fields exist.
- Partially supported criteria measure presence or narrow proxy signals, not
  clinical quality or outcomes.
- Text matching is exact/normalized keyword matching, not semantic matching.
- Published capacity is not utilization, staffing or quality.
- Missing evidence does not prove absence; it lowers coverage and confidence.
- The caller remains responsible for authorization and for supplying a stable
  hospital identifier.

## Validation

The Milestone 2A suite covers unsupported-only requests, clarification-required
requests, unsupported high-weight preferences, partial proxy signals, equal
suitability with different coverage, conflicting mandatory constraints, empty
candidate sets and tri-state intake/export behavior.

Commands:

```text
uv run --frozen pytest -q
uv run --frozen ruff check .
uv run --frozen mypy src
```
