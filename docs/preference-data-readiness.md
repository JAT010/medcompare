# Preference Data Readiness

Status: Milestone 2A deliverable. This document maps the twelve MedCompare
preference criteria to the data the extractor actually produces today. It is the
authority for which criteria may be scored, which may only be scored from a
narrow proxy signal, and which must remain unsupported until new extraction work
is completed.

## Scope and rules

- Only `HospitalDataset` and its entity records are considered. The repository
  has no hospital registry, pricing database, review store or insurer network.
- A criterion is `supported` only when an existing extracted field can produce a
  meaningful, evidence-backed score.
- A criterion is `partially_supported` when only a narrow proxy signal exists. A
  score is produced only when that signal is present; absence is treated as
  unknown, not as a negative finding.
- A criterion is `unsupported` when no extracted field exists. The request schema
  accepts the criterion for API completeness, but it is never replaced with a
  placeholder value. Unsupported weight stays in the evidence-coverage
  denominator and receives no suitability weight.
- A criterion is `requires_clarification` when the data could be scored but the
  request omitted a required input. It also stays in coverage with zero coverage
  until the caller supplies the missing input.
- If no preference is scorable, the service returns
  `scoring_available=False`, empty results and explicit warnings instead of
  inventing a score or silently dropping the preference.
- Every record inherits `EntityRecord` provenance: `source_url`, `page_url`,
  `page_title`, `extracted_at`, `context`, `extraction_method`,
  `source_strength` and `confidence`. Recommendation evidence references these
  fields.
- `DataQuality.category_coverage` records `found`, `not_found`,
  `not_applicable` or `failed` per category. Recommendation code treats
  `found` as proof that the category was actually published; only then may a
  non-match be scored as zero rather than unknown.

## Summary matrix

| # | Criterion | Support | Existing source fields | Availability / type |
|---|-----------|---------|------------------------|---------------------|
| 1 | Affordability | unsupported | none | no price/tariff/package/cost field |
| 2 | Location | supported | `Contact.address`, `locality`, `city`, `state`, `postal_code`, `latitude`, `longitude` | optional strings and floats |
| 3 | Doctor experience | partially_supported | `Doctor.experience`, `Doctor.hospital_association` | free text, association label |
| 4 | Department expertise | partially_supported | `Department.name`, `Speciality.name`, `Doctor.department`, `Doctor.speciality` | optional strings |
| 5 | Infrastructure | supported | `Infrastructure.total_beds`, `icu_beds`, `operation_theatres`, `cath_labs` | optional integers |
| 6 | Emergency services | partially_supported | `Contact.emergency_phone`, `emergency_email`, `Facility.name` | optional strings |
| 7 | Doctor availability | partially_supported | `Doctor.opd_hours`, `Doctor.location` | free text |
| 8 | Patient reviews | unsupported | none | no rating/review/testimonial field |
| 9 | Diagnostic facilities | partially_supported | `Facility.name`, `Technology.name`, `Procedure.name`, `DataQuality.category_coverage` | optional strings and coverage labels |
| 10 | Insurance compatibility | unsupported | none | no insurer/TPA/cashless field |
| 11 | Accessibility | partially_supported | `Facility.name`, `Facility.description`, `Technology.name` | free text keyword signal |
| 12 | Treatment facilities | supported | `Treatment.name`, `Procedure.name`, `Technology.name`, `Speciality.name`, `Department.name` | optional strings |

## Criterion detail

### 1. Affordability - unsupported

- Existing data: none. No price, tariff, package, currency or cost field exists
  in any model.
- Evidence/provenance: not applicable.
- Normalization: not possible without inventing values.
- Missing fields / extraction required: structured price, tariff or package
  records with currency, procedure scope and validity date.
- Reliability limits: affordability cannot be compared today. The request schema
  accepts `affordability` and `MandatoryConstraints.max_affordability_cost`, but
  any affordability mandatory constraint evaluates to `unknown` and therefore
  can never be satisfied.

### 2. Location - supported

- Existing data: `Contact.city`, `state`, `postal_code`, `locality`, `address`,
  `latitude`, `longitude`.
- Evidence/provenance: contact record provenance, with the compared field and
  candidate/requested values in the evidence reference.
- Normalization: exact case-folded text equality per requested field; coverage is
  the share of requested fields that the candidate actually published. When
  `radius_km` and an origin coordinate are supplied, great-circle distance is
  computed with the haversine formula.
- Missing fields / extraction required: none for the supported behavior;
  structured locality normalization would improve recall.
- Reliability limits: exact text matching misses spelling variants and
  abbreviations. Radius matching is only possible when both the candidate and
  the request provide coordinates.

### 3. Doctor experience - partially_supported

- Existing data: `Doctor.experience` is free text as published by the website
  (for example "25 years" or "15+ Years"). `Doctor.hospital_association` is
  `confirmed`, `probable`, `uncertain` or `not_associated`.
- Evidence/provenance: doctor record provenance with the published experience
  string.
- Normalization: parse an explicit year count with a conservative regex; score is
  the highest parsed year count divided by the configured reference (default 30
  years, capped at 1.0). Coverage is the share of associated doctors whose
  experience could be parsed.
- Missing fields / extraction required: structured years-of-experience per
  doctor; current free text often omits a number.
- Reliability limits: the highest single published value is a proxy, not a
  department-level quality measure. Doctors marked `not_associated` are excluded.

### 4. Department expertise - partially_supported

- Existing data: `Department.name`, `Speciality.name`, `Doctor.department`,
  `Doctor.speciality`.
- Evidence/provenance: department, speciality or doctor record provenance with
  the matched term.
- Normalization: requested department/speciality terms are matched against
  normalized names and descriptions. Score is matched terms divided by requested
  terms. When `DataQuality.category_coverage` proves departments/specialities
  were published (`found`), a non-match is scored as zero with full coverage;
  otherwise a non-match stays unknown.
- Missing fields / extraction required: per-department quality, volume or outcome
  data for actual expertise comparison.
- Reliability limits: the criterion measures presence, not clinical quality.

### 5. Infrastructure - supported

- Existing data: `Infrastructure.total_beds`, `icu_beds`,
  `operation_theatres`, `cath_labs` as optional integers.
- Evidence/provenance: infrastructure record provenance with the field and
  published value.
- Normalization: each available metric is divided by a configurable saturation
  reference (defaults: 1000 beds, 200 ICU beds, 30 operation theatres, 10 cath
  labs), capped at 1.0. Score is the mean over available metrics; coverage is the
  share of the four metrics that are present.
- Missing fields / extraction required: none for the supported behavior.
- Reliability limits: published capacity is not utilization, staffing or quality.
  Partial capacity reporting reduces coverage and may trigger
  `insufficient_evidence`.

### 6. Emergency services - partially_supported

- Existing data: `Contact.emergency_phone`, `Contact.emergency_email` and
  `Facility.name`/`category`/`description` containing emergency, casualty or
  trauma terms.
- Evidence/provenance: the matching contact or facility record.
- Normalization: explicit positive evidence yields score 1.0 with full coverage.
  No evidence yields unknown, never a negative finding.
- Missing fields / extraction required: explicit emergency/casualty service
  declarations, including 24x7 status.
- Reliability limits: absence of extracted evidence does not prove absence of an
  emergency department.

### 7. Doctor availability - partially_supported

- Existing data: `Doctor.opd_hours`, `Doctor.location`.
- Evidence/provenance: doctor record provenance with the published OPD hours.
- Normalization: explicit OPD hours yield score 1.0; coverage is the share of
  associated doctors with published hours. No hours yields unknown.
- Missing fields / extraction required: structured appointment/OPD schedules per
  doctor, including days and time ranges.
- Reliability limits: free-text OPD hours are not a live availability signal.

### 8. Patient reviews - unsupported

- Existing data: none. No rating, review, testimonial or patient-feedback field
  exists.
- Evidence/provenance: not applicable.
- Normalization: not possible without inventing values.
- Missing fields / extraction required: verified patient review records with
  source, date and rating scale.
- Reliability limits: reviews cannot be compared today and must not be proxied by
  hospital size or accreditation.

### 9. Diagnostic facilities - partially_supported

- Existing data: `Facility.name`, `Technology.name`, `Procedure.name` and
  `DataQuality.category_coverage` for Facilities, Medical Technology and
  Procedures.
- Evidence/provenance: the matching facility, technology or procedure record.
- Normalization: requested diagnostic terms are matched to normalized names and
  descriptions. Score is matched terms divided by requested terms. When coverage
  proves the relevant category was published (`found`), a non-match is scored as
  zero with full coverage; otherwise it stays unknown.
- Missing fields / extraction required: a normalized diagnostic service catalog
  with modality and availability.
- Reliability limits: keyword matching can conflate a service name with a
  marketing mention. No modality-level capability model exists.

### 10. Insurance compatibility - unsupported

- Existing data: none. Insurer, TPA and cashless-network pages may be crawled for
  relevance, but no structured record is extracted.
- Evidence/provenance: not applicable.
- Normalization: not possible without inventing values.
- Missing fields / extraction required: insurer/TPA network records with plan,
  cashless status and validity.
- Reliability limits: `MandatoryConstraints.required_insurance_providers` is
  accepted but always evaluates to `unknown`, so it can never be satisfied.

### 11. Accessibility - partially_supported

- Existing data: `Facility.name`, `Facility.description`, `Technology.name` and
  the hospital description, matched against explicit accessibility keywords
  (wheelchair, ramp, accessible, barrier free, lift, elevator, disabled,
  braille).
- Evidence/provenance: the matching facility, technology or hospital record.
- Normalization: explicit positive evidence yields score 1.0 with full coverage.
  No evidence yields unknown, never a negative finding.
- Missing fields / extraction required: structured accessibility feature
  declarations.
- Reliability limits: keyword matching cannot distinguish a full accessibility
  program from a single mentioned feature.

### 12. Treatment facilities - supported

- Existing data: `Treatment.name`, `Procedure.name`, `Technology.name`,
  `Speciality.name`, `Department.name`.
- Evidence/provenance: the matching treatment, procedure, technology, speciality
  or department record.
- Normalization: requested treatment terms are matched to normalized names and
  descriptions. Score is matched terms divided by requested terms. When coverage
  proves treatments/procedures were published (`found`), a non-match is scored as
  zero with full coverage; otherwise it stays unknown.
- Missing fields / extraction required: none for the supported behavior; a
  normalized treatment catalog would improve recall.
- Reliability limits: presence of a treatment name is not evidence of outcome
  quality or volume.

## Existing-data gaps that affect all criteria

- There is no price, rating, review, insurance, TPA, cashless or structured
  accessibility model.
- The 25-field intake stores boolean answers as `yes`, `no` or `unknown`.
  `MasterIntake.boolean_state()` and `unknown_boolean_keys()` make that tri-state
  explicit, and exports use them instead of silently treating a missing key as
  `no`. The default submission policy is now `UNKNOWN_KEEP`, so unresolved
  capabilities stay `UNKNOWN`. Legacy consumers that require `TRUE`/`FALSE` must
  explicitly select `UNKNOWN_FALSE`; each conversion is recorded in the quality
  report. The projection itself is never mutated by either policy.
- Many fields are optional and may be missing for a given site. The
  recommendation layer therefore separates suitability from evidence coverage and
  marks poorly supported candidates as `insufficient_evidence`.
- `HospitalDataset` carries no registry identifier. The caller must supply a
  stable `hospital_id` through `HospitalCandidate`.

## Scoring and coverage contract

- Mandatory constraints are evaluated before any scoring. `unknown` is never
  treated as satisfied.
- Every positive requested weight receives a `PreferenceAssessment` with a static
  support level and a request-time state. The response exposes
  `support_states`, `unsupported_criteria`, `clarification_required` and
  `partially_supported_criteria`.
- Scorable weights are normalized to 100 for suitability. Unsupported and
  clarification-required criteria receive no suitability weight.
- Evidence coverage uses a second basis: all active preference weights, including
  unsupported and clarification-required criteria. Those contribute weight but
  zero coverage, so a high-priority unsupported preference cannot be hidden.
- Suitability is the weighted mean of usable criterion scores, scaled to 0-100.
- Evidence coverage is the weighted share of active criteria that has usable
  evidence. It is reported separately from suitability.
- Candidates below the request's `minimum_evidence_coverage` (default 0.5) are
  categorised as `insufficient_evidence`.
- Confidence is conservative: `insufficient` for insufficient evidence, capped at
  `low` when unsupported/clarification preferences are active, and capped at
  `medium` when only partial proxy signals support the result.
- If no preference is scorable, the response has `scoring_available=False`,
  empty results and warnings.
- Ties are broken by evidence coverage descending, then `hospital_id` ascending,
  so results are reproducible.

## Integration requirements

- The existing backend must implement `HospitalAuthorizer.scope_for(user_id)`
  using its authoritative permission checks and return the hospitals that user
  may see. `DenyAllAuthorizer` is the fail-closed default; `StaticAuthorizer` is a
  test/dev double only.
- The backend must supply `HospitalCandidate` objects containing a stable
  `hospital_id` and a `HospitalDataset`.
- No frontend, API contract, `overall_score`, Jinja/HTMX UI or existing
  extraction behavior is changed by this foundation.

## Test fixtures

All recommendation tests use synthetic fixtures in
`tests/recommendation_fixtures.py`. Every hospital, doctor, value and URL there is
fictional and exists only to exercise deterministic behavior.
