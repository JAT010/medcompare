# Canonical Schema — V1

Comprehensive but controlled. The schema is the **target** the pipeline maps into; it is not a rigid
output contract. Dynamic adaptation (§7) decides which categories and fields actually appear in the
final workbook.

Conventions:

- **Data type** — the Python type used in the Pydantic model.
- **Required** — `R` = must be present for a meaningful record; `O` = optional.
- **Normalization rule** — what the processing layer does. Always: whitespace collapse, HTML artifact
  removal, unicode-space normalization (see `utils/text.py`), URL normalization (see `utils/urls.py`).
- **Provenance** — every record carries `source_url`, `page_title`, `extracted_at` (UTC), and an
  optional `context` snippet. This satisfies "where did this value come from?".
- **Missing data**: fields are `None` (not extracted). Records/fields with no meaningful data are
  dropped from output. Values are never guessed.

---

## 1. Hospital Overview

One record per run.

| Field | Type | Description | Req | Normalization | Provenance |
|---|---|---|---|---|---|
| `name` | str | Hospital display name | R | Trim, collapse; keep as published | hospital profile page |
| `hospital_type` | str | e.g. "super-speciality", "tertiary care" | O | Trim; lowercase unless proper noun | About Us / JSON-LD |
| `description` | str | About text | O | Strip HTML, collapse, dedupe sequential clones | About Us section |
| `parent_organization` | str | e.g. "Gujarmal Modi Hospital & Research Centre..." | O | Trim | About Us text |
| `established_year` | int | Founded year | O | Parse 4-digit year only when clearly stated | About/JSON-LD |
| `status` | str | e.g. "operational", "24/7" | O | Trim | FAQ/JSON-LD |
| `bed_count` | int | Published beds | O | Parse leading int of "650+ bedded"; record `+` in context | About Us |
| `doctor_count` | int | Published doctor count | O | Parse int of "680+ specialists"; context preserved | About Us |
| `staff_count` | int | Published staff count | O | Parse int when explicitly stated | About Us |
| `speciality_count` | int | Published speciality count | O | Parse int when stated | About Us/JSON-LD |
| `department_count` | int | Published department count | O | Parse int when stated | — |
| `website` | str | Canonical website URL | O | `validate_url` | input |

## 2. Location & Contact

| Field | Type | Description | Req | Normalization | Provenance |
|---|---|---|---|---|---|
| `address` | str | Street address | O | Trim; join multi-line address | `LocalBusiness`/`hospital` JSON-LD |
| `city` | str | Locality | O | Trim; title-case only when safe | JSON-LD |
| `state` | str | Region/state | O | Trim | JSON-LD |
| `country` | str | Country | O | Keep as published (e.g. "IN") | JSON-LD |
| `postal_code` | str | ZIP/postal | O | Trim; keep leading zeros | JSON-LD |
| `phone` | str | Main phone | O | Trim; keep as published (no forced reformat) | JSON-LD/HTML |
| `emergency_phone` | str | Emergency/ambulance phone | O | Trim | Emergency page/FAQ |
| `appointment_phone` | str | Appointment line | O | Trim | contact JSON-LD `contactType` |
| `email` | str | Contact email | O | Lowercase; trim | HTML |
| `emergency_email` | str | Emergency email | O | Lowercase; trim | — |
| `maps_url` | str | Maps/location URL | O | `normalize_url` | links |
| `latitude` / `longitude` | float | Geo coords | O | float parse | `GeoCoordinates` JSON-LD |

## 3. Capacity & Infrastructure

| Field | Type | Description | Req | Normalization | Provenance |
|---|---|---|---|---|---|
| `total_beds` | int | Total beds | O | Parse leading int of e.g. "650+"; `+` stays in context | About Us |
| `icu_beds` | int | ICU beds | O | Parse int when published | About Us |
| `operation_theatres` | int | Operation theatres | O | Parse int of "25 high-end modular operation theatres" | About Us |
| `cath_labs` | int | Cath labs | O | Parse int when published | About Us/tech section |
| `critical_care_beds` | int | Critical care beds | O | Parse int of "170+ critical care beds" | About Us |
| `patient_rooms` | int | Patient rooms | O | Parse int when published | — |
| `other_capacity` | str | Other published capacity, verbatim | O | Trim; no parsing | About Us |

Numeric fields are `None` unless a confident integer parse succeeds. Unparseable figures go verbatim
into `other_capacity`. Conflicts (same metric, different sources) are preserved and flagged in
Data Quality, never silently resolved.

## 4. Facilities

| Field | Type | Description | Req | Normalization | Provenance |
|---|---|---|---|---|---|
| `name` | str | Facility name (ICU, OPD, OT, pharmacy, lab, parking...) | R | Trim; title-case when safe | FAQ/About |
| `category` | str | e.g. "critical care", "diagnostics", "support" | O | Controlled category dictionary | classifier |
| `availability` | str | e.g. "24/7", "available" | O | Trim | FAQ/JSON-LD |
| `description` | str | Description | O | Strip HTML, collapse | section text |

## 5. Departments

| Field | Type | Description | Req | Normalization | Provenance |
|---|---|---|---|---|---|
| `name` | str | Department name | R | Trim | headings/links |
| `description` | str | Description | O | Strip HTML, collapse | section text |

## 6. Specialities

| Field | Type | Description | Req | Normalization | Provenance |
|---|---|---|---|---|---|
| `name` | str | Speciality name (display form, e.g. "Cardiac Sciences") | R | Trim; display name wins over slug | H1/JSON-LD |
| `category` | str | Grouping (e.g. "cardiac", "oncology") | O | Controlled mapping; low confidence → omit | classifier |
| `description` | str | Overview | O | Strip HTML, collapse | speciality page |

## 7. Doctors

Shallow only — see extraction boundaries.

| Field | Type | Description | Req | Normalization | Provenance |
|---|---|---|---|---|---|
| `name` | str | Full name as published | R | Trim; collapse; keep honorifics | card `h4`/`Physician` |
| `speciality` | str | Comma-joined specialities as published | O | Trim; preserve order; do NOT merge "Cardiology"/"Cardiac Sciences" blindly | card line / `medicalSpecialty` |
| `department` | str | Department when distinct from speciality | O | Trim | profile/JSON-LD |
| `designation` | str | e.g. "Principal Director - Obstetrics & Gynaecology" | O | Trim | card `<p>` |
| `experience` | str | Free text as published ("45+ Years") | O | Trim; **no** int conversion (preserve "+") | card `Experience:` |
| `gender` | str | Male/Female | O | Title-case | card/`Physician` |
| `opd_hours` | str | OPD timings (opt-in profile fetch) | O | Trim | profile `hospitalAffiliation` |
| `profile_url` | str | Doctor profile URL | R | `normalize_url` | card link |

Deduplication: exact profile-URL match first; otherwise conservative name + speciality match with low
confidence. When uncertain, keep both records.

## 8. Treatments / Conditions

| Field | Type | Description | Req | Normalization | Provenance |
|---|---|---|---|---|---|
| `name` | str | Condition/treatment name | R | Trim | H1 |
| `speciality` | str | Owning speciality | O | Trim; derived from URL path when present | breadcrumb/URL |
| `description` | str | Short overview | O | Strip HTML; truncate to sane length | first content block |

## 9. Procedures

Same shape as Treatments (`name`, `speciality`, `description`).

## 10. Medical Technology

| Field | Type | Description | Req | Normalization | Provenance |
|---|---|---|---|---|---|
| `name` | str | Technology/equipment name | R | Trim | H3/tech cards |
| `category` | str | e.g. "imaging", "robotics" | O | Controlled mapping | classifier |
| `description` | str | Description | O | Strip HTML, collapse | tech page |

## 11. Emergency

| Field | Type | Description | Req | Normalization | Provenance |
|---|---|---|---|---|---|
| `name` | str | Service name (e.g. "Ambulance - Delhi") | R | Trim | H1/FAQ |
| `availability` | str | e.g. "24/7" | O | Trim | FAQ |
| `contact` | str | Phone/helpline | O | Trim; keep as published | emergency page/FAQ |
| `description` | str | Description | O | Strip HTML | section text |

## 12. Patient Services

| Field | Type | Description | Req | Normalization | Provenance |
|---|---|---|---|---|---|
| `name` | str | Service name (pharmacy, lab, rehab, virtual consult...) | R | Trim | FAQ/sections |
| `category` | str | e.g. "diagnostics", "support" | O | Controlled mapping | classifier |
| `availability` | str | Availability | O | Trim | FAQ |
| `description` | str | Description | O | Strip HTML | section text |

## 13. Insurance & Payment

| Field | Type | Description | Req | Normalization | Provenance |
|---|---|---|---|---|---|
| `name` | str | Provider/TPA/scheme name | R | Trim | FAQ/TPA list |
| `tpa` | bool | Is a TPA | O | True when labelled TPA | FAQ/TPA list |
| `cashless` | bool | Cashless offered | O | True when stated | FAQ |
| `government_scheme` | bool | Govt scheme (e.g. CGHS) | O | True when stated | FAQ |
| `description` | str | Notes | O | Strip HTML | FAQ |

`None` (unknown) is distinct from `False`; only explicit statements set flags.

## 14. Accreditations

| Field | Type | Description | Req | Normalization | Provenance |
|---|---|---|---|---|---|
| `name` | str | Accreditation name | R | Trim | awards/accreditations page |
| `organization` | str | Issuing body | O | Trim | page text |
| `status` | str | e.g. "current" | O | Trim | — |
| `year` | int | Year | O | Parse 4-digit year | text |
| `description` | str | Description | O | Strip HTML | — |

## 15. Awards

| Field | Type | Description | Req | Normalization | Provenance |
|---|---|---|---|---|---|
| `name` | str | Award name | R | Trim; strip trailing "(YYYY)" into `year` | awards page |
| `organization` | str | Awarding body | O | Trim | awards page |
| `year` | int | Year | O | Parse "(YYYY)" | award text |
| `description` | str | Description | O | Strip HTML | — |

## 16. Programs

| Field | Type | Description | Req | Normalization | Provenance |
|---|---|---|---|---|---|
| `name` | str | Program name (health check-up, international care...) | R | Trim | FAQ/sections |
| `category` | str | e.g. "wellness", "international" | O | Controlled mapping | classifier |
| `description` | str | Description | O | Strip HTML | — |

## 17. Visiting / Admission Information

| Field | Type | Description | Req | Normalization | Provenance |
|---|---|---|---|---|---|
| `visiting_hours` | str | Visiting hours as published | O | Trim | FAQ |
| `opd_hours` | str | OPD hours | O | Trim | JSON-LD `openingHours`/FAQ |
| `appointment_information` | str | How to book | O | Strip HTML, truncate | FAQ |
| `registration_information` | str | Registration steps | O | Strip HTML, truncate | FAQ |
| `admission_information` | str | Admission guidance | O | Strip HTML, truncate | FAQ |
| `discharge_information` | str | Discharge guidance | O | Strip HTML, truncate | FAQ |

## 18. Other Hospital Information (controlled fallback)

| Field | Type | Description | Req | Normalization | Provenance |
|---|---|---|---|---|---|
| `label` | str | Fact label | R | Trim | — |
| `value` | str | Fact value | R | Strip HTML, truncate | — |

Used only for genuinely hospital-level facts that fit no canonical category. Prefer a canonical entity.

---

## 7. Schema Adaptation

The canonical schema is a target, not a straitjacket. It adapts along four axes.

### 7.1 Adaptation mechanism

1. **Category discovery** — after extraction, only categories with ≥1 meaningful record are kept
   (`schema/discovery.py`). No category → no worksheet. E.g. a hospital site without awards produces no
   Awards sheet.
2. **Field discovery** — within a category, only fields with non-`None` values are emitted.
3. **Mapping confidence** — each `website field → canonical field` mapping is tagged:

| Confidence | Meaning | Action |
|---|---|---|
| `high` | Direct structured signal (JSON-LD `@type`/property, semantic HTML element) | Map directly |
| `medium` | Convention-based (H1/heading, breadcrumb, URL slug, card class) | Map with normalization |
| `low` | Free-text/heuristic match | Map only if value is clearly hospital-level; else drop or route to "Other" |

4. **Fallback behavior**:
   - Unknown-but-relevant → `Other Hospital Information`.
   - Terminology differences → synonym dictionary (e.g. "Specialities" ≈ "Departments" ≈ "Services" per
     site; "Conditions" ≈ "Treatments") at `medium` confidence; never blindly merged.
   - Different HTML structure → extraction strategy ladder (JSON-LD → semantic HTML → tables → cards →
     headings → visible text) re-orders automatically per page.

### 7.2 Example — two hospitals, one schema

| | Hospital A (Max Saket) | Hospital B (elsewhere) |
|---|---|---|
| Doctors | name, speciality, department, designation, experience, profile_url | name, speciality, profile_url |
| Output | Doctors sheet with 6 data columns | Doctors sheet with 3 data columns |
| Awards | present → sheet | absent → no sheet |
| Technology | present → sheet | absent → no sheet |
| Visiting | FAQ-based → sheet | no visiting info → no sheet |

### 7.3 Mapping table template

```
canonical field  | website field / signal        | confidence | fallback
-----------------+-------------------------------+------------+-----------------------
Doctor.name      | Physician.name / card h4      | high       | drop record
Doctor.designation| card <p> after h4             | medium     | None
Hospital.bed_count| "NN+ bedded" in About Us      | medium     | None (no guess)
```

The mapper (`schema/mapper.py`) is the single place where these rules live; extraction layers never
know the canonical schema directly.
