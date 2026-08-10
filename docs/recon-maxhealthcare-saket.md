# Phase 1 — Reconnaissance & Architecture Proposal

Target: **Max Healthcare (maxhealthcare.in)** — Max Smart Super Speciality Hospital, Saket (first integration test case)

Date: 2026-08-10 · Status: read-only reconnaissance only, no crawler implemented yet

---

## 1. Access & Anti-Bot Profile

Observed HTTP behavior (tested 2026-08-10):

| Request | Result |
|---|---|
| `https://www.maxhealthcare.in/` with default curl UA (`curl/8.x`) | 200 (537 KB HTML) |
| Same URL with full Chrome UA | 403 `Access Denied` from `errors.edgesuite.net` (Akamai) |
| `https://maxhealthcare.in/` (no www) | 301 redirect |
| `robots.txt` / `sitemap.xml` | 200 with default curl UA, 403 with Chrome UA |
| Doctor listing/profile pages | 200 with default curl UA |

Findings:

- The site sits behind **Akamai** and its bot manager behaves *inconsistently* (at recon time it served plain `curl` UAs but rejected a full browser UA). This must be treated as an environment variable, not a stable rule.
- The crawler must make the **HTTP client user-agent configurable** and should probe `robots.txt` and the homepage first to detect a bot wall before crawling.
- **No login, CAPTCHA bypass, or access-control circumvention** is needed or attempted — content is public.
- All relevant pages returned **complete server-rendered HTML** (350 KB–900 KB per page). **No JavaScript rendering required for V1.**

## 2. robots.txt Summary

`https://www.maxhealthcare.in/robots.txt` exists. Key rules for `User-agent: *`:

- Disallowed: `/hr-compliances`, `/for-healthcare-professionals/`, `/disease`, `/public/`, `/book-an-appointment/doctor`, `/conditions-treatments?allof=*`, `*?speciality=*`, `*?doctor=*`, several `*-package-*` URLs, `/covid-vaccination-*`, `/hi/` (allow `/blogs/hi/`).
- Note: `*?speciality=*` and `*?doctor=*` are **query-string disallows only**; path-based pages (`/our-specialities/...`, `/doctor/...`) are allowed.
- `GPTBot`, `ChatGPT-User`, `ClaudeBot`, `Google-Extended` are explicitly allowed `Allow: /`.

Crawler implications:

- Stay on path-based URLs; do not use `?speciality=`, `?doctor=`, or `?allof=` query forms.
- Do not crawl `/book-an-appointment/doctor` booking links (also out of scope — booking is not hospital-level info).
- Respect the disallow list with a robots-aware URL filter.

## 3. Sitemap Inventory

`/sitemap.xml` is a sitemap index with 25 child sitemaps. All fetched successfully.

| Sitemap | Count | Contents |
|---|---|---|
| `hospital-network-sitemap.xml` | 18 | `/hospital-network/<slug>` hospital profile pages (network-wide) |
| `speciality-sitemap.xml` | 118 | `/our-specialities/<slug>` |
| `procedures-sitemap.xml` | 275 | `/procedures/<slug>` |
| `conditions-treatments-sitemap.xml` | 690 | `/our-specialities/<spec>/conditions-treatments/<slug>` |
| `technology-sitemap.xml` | 115 | `/technology/<slug>` |
| `doctors-profiles-sitemap.xml` | 558 | `/doctor/<slug>` individual doctor profiles (network-wide) |
| `doctors-listing-saket-sitemap.xml` | 86 | `/doctor/<speciality>-in-saket` listing pages (Saket-specific) |
| `dotors-listing-sitemap.xml` (typo, real) | 45 | mixed speciality + location listing pages |
| `doctors-listing-delhi-sitemap.xml` | 115 | `/doctor/<speciality>-in-delhi` |
| other `doctors-listing-<city>-sitemap.xml` | ~9 | per-city listing pages |
| `blogs-sitemap.xml`, `faqs-sitemap.xml`, `directors-sitemap.xml`, `leadership-sitemap.xml`, `testimonials-sitemap.xml` | — | blogs, FAQs, leadership, testimonials (mostly out of hospital-level scope; FAQs can support Visiting/Emergency) |

Observation: the sitemap index is the single most reliable **page-discovery** source for this site.

## 4. URL Patterns

Clean, hierarchical, predictable slugs:

```
/hospital-network/max-smart-super-speciality-hospital-saket     # hospital profile
/our-specialities/<slug>                                        # speciality
/procedures/<slug>                                              # procedure
/technology/<slug>                                              # technology
/our-specialities/<spec>/conditions-treatments/<slug>           # condition/treatment
/doctor/<speciality>-in-saket | -in-delhi | ...                 # doctor listing (per speciality+location)
/doctor/<doctor-slug>                                           # doctor profile
/find-a-doctor                                                  # landing page (thin)
/hospitals-in-india                                             # hospital directory
/award-accreditations                                           # awards & accreditations (per-hospital)
/tpa-panel                                                      # network TPA panel
/ambulance/emergency-ambulance-in-delhi                         # city-level emergency/ambulance
/contact-us                                                     # per-hospital contact JSON-LD
/book-an-appointment/doctor/<uuid>                              # booking (robots-disallowed; skip)
```

## 5. Page-by-Page Findings

### 5.1 Homepage `/`
- Title/desc: “Best Hospitals in Delhi NCR… 5000+ doctors across 21 hospitals, 84+ specialties”.
- 2 JSON-LD blocks: `WebPage` (empty shell) and `MedicalOrganization` (name, description, logo, `sameAs` only — no address/phone at org level).
- 751 links; 692 internal; nav links to `/hospitals-in-india`, `/find-a-doctor`, `/conditions-treatments`, `/our-specialities/*`, `/procedures/*`, `/technologies`, `/award-accreditations`, `/emergency-air-ambulance`, `/tpa-panel`, `/contact-us`.
- External links to `maxathome.in`, `maxlab.co.in`, `blkmaxhospital.com`, `nanavatimaxhospital.org`, `education.maxhealthcare.in`, socials — **crawler must not follow external domains**.

### 5.2 Hospital profile `/hospital-network/max-smart-super-speciality-hospital-saket`
The key page for the target hospital. Structure:

- **H1**: `Max Smart Super Speciality Hospital, Saket` (appears twice — dedup needed).
- **JSON-LD** (3 blocks):
  1. `WebPage` (shell)
  2. `MedicalOrganization` (network-wide boilerplate, identical on every page — must be recognized and not treated as hospital-specific)
  3. `LocalBusiness` — hospital-specific: name, `@id`, url, `telephone` `+91 926 888 0303`, `address` (street `12, Press Enclave Marg, Saket Institutional Area, Saket`, locality `New Delhi`, postalCode `110017`, country `IN`), `geo`, `openingHoursSpecification` (24/7).
- **About Us** text (single paragraph, duplicated into a hidden “show more” clone — boilerplate pattern):
  > “…(a unit of **Gujarmal Modi Hospital & Research Centre for Medical Sciences**) is a **650+ bedded** tertiary care facility equipped with **25 high-end modular operation theatres**, an emergency resuscitation and observation unit, **170+ critical care beds**, a dedicated endoscopy unit, an advanced dialysis unit, and a state-of-the-art Onco Day Care unit… across multiple medical specialties, including Cardiac Sciences, Orthopaedics, Urology, Kidney Transplant, Neurology, Neurosurgery, Paediatrics, Obstetrics, and Gynaecology. With the support of **680+ leading specialists**…”
- **H2 sections**: About Us, Specialities & Procedures, Looking for an Expert, Our Medical Experts (doctors), Our Technological Advancements (technology cards), Real Patients Real Stories (testimonials — out of scope), Max Updates/Health Blogs (out of scope), **FAQ accordion (26 Q&A pairs)**.
- **H3 technology names**: CT Scanner, MRI 3.0 Tesla, Cathlab, 4D ECHO, OCT, EEG, CRRT, etc. (the Technological Advancements section).
- **Inline counts**: 268 unique `/doctor/` links, 11 `/our-specialities/` links, 14 `/procedures/` links on this page.
- **FAQ accordion** (`.collapse` divs, `div.site-content` answers): emergency services (yes, 24/7), working hours (24/7), visiting hours (Open 24/7), insurance (empanelled; “check with your TPA”), CGHS (answer defers to `cghs.nic.in`), payment options (cash/cards/insurance), facilities (ICU, Cashless Insurance, OPD, OT), pharmacy on-site, lab, health check-up packages, rehab, international patient services, parking, virtual consultation, second opinion, oncology services, travel assistance, multi-family booking. **Some FAQ answers are template-generated and near-empty (e.g. address answer is “located at , , , .”) — an example of boilerplate that cleaning must drop.**
- Maps are **not** embedded as iframe/Google Maps link; geo comes from JSON-LD.

Mapping to canonical schema: Hospital Overview, Location & Contact, Capacity & Infrastructure, Facilities, Departments/Specialities, Doctors, Procedures, Medical Technology, Emergency, Patient Services, Insurance & Payment, Visiting/Admission Information.

### 5.3 Doctor listing `/doctor/<speciality>-in-saket` (e.g. `obstetricians-and-gynaecologists-in-saket`)
- Title pattern: “Gynaecologist in Saket, Delhi | Book Appointment…”. H2 “Doctors Available”.
- Server-rendered cards in `div.doctor-card-new`. Card fields:
  - Name (`h4 > a`), Profile URL (`/doctor/<slug>`)
  - Designation (`<p>` after h4, e.g. “Principal Director - Obstetrics & Gynaecology”)
  - Specialities (`p.text-truncate-line-3`, comma-separated)
  - `Experience: 45+ Years` (`p > strong`)
  - `Gender`
  - Hidden JSON snippet with per-hospital availability keyed by **hospital slug** (e.g. `max-smart-super-speciality-hospital-saket`) — proves a card can span multiple hospitals.
- **JSON-LD `@graph` of `Physician` entries** on the page: name, image, url, `medicalSpecialty`, gender, telephone, priceRange, address (address fields mostly empty/template).
- BreadcrumbList JSON-LD present.
- **No pagination** (`rel=next` absent, no page links); each listing shows all matching doctors. 53 unique doctor profiles on the gyn listing. Filtering UI is a GET form (`gender[]` checkboxes) but the default page is complete.
- Doctor profile links also appear as absolute `https://www.maxhealthcare.in/doctor/<slug>`.

### 5.4 Doctor profile `/doctor/dr-nitin-s-walia`
- **JSON-LD `Physician`**: name, `medicalSpecialty`, url, image, **`hospitalAffiliation[]`** with per-hospital name + `openingHours` (OPD timings) — the reliable signal for which hospital a doctor belongs to.
- H1: name. H2 sections: designation, “Select Hospital”, About, **Work Experience** (e.g. “20 Years as Dermatologist”), Education & Training, Memberships, Awards, Speciality Interest, FAQs.
- Shallow extraction only: name, speciality, department/designation, experience (when clearly stated), profile URL, affiliation. **Do not** extract education/memberships/publications in depth.

### 5.5 Speciality `/our-specialities/cardiology`
- H1 = display name **“Cardiac Sciences”** (slug `cardiology` differs — normalization example).
- H2s: Overview, Cardiac Treatments at Max Hospitals, … Conditions & Treatments, Our Medical Experts (114 doctor links), Technological Advancements, Top Procedures.
- JSON-LD `@graph`: a `Hospital` node (name “Max Hospital, Saket”, address, telephone) + `Physician` nodes with `worksFor` → hospital.

### 5.6 Procedure `/procedures/<slug>` and Condition/Treatment `/our-specialities/<spec>/conditions-treatments/<slug>`
- Server-rendered with WebPage/MedicalOrganization/BreadcrumbList JSON-LD; H1 title; content sections; sidebar links to related procedures.
- 275 procedure + 690 condition pages network-wide. **Per-hospital relevance is weak** — treat as network-level catalog for a single-hospital run (see §8 scope decision).

### 5.7 Technology `/technology/<slug>`, hub `/technologies`
- Hub H1 “Technologies”, card grid. 115 technology pages network-wide. Hospital profile page carries its own per-hospital technology list (H3 names + descriptions) — prefer that for hospital-specific Technology data.

### 5.8 Awards & Accreditations `/award-accreditations`
- H2 “Recognised for Excellence”; per-hospital sections; award items in `.awards-content ul li` (e.g. “Lokmat Times Excellence in Healthcare Awards (2025)”, “ET Healthcare Summit Award (2024)”, “Best Hospital”).
- Filter form (hospital + speciality selects) drives client-side tab switching — award text is present in initial HTML.

### 5.9 Contact `/contact-us`
- 16 JSON-LD blocks of type `hospital`, one per network hospital, each with name, `@id` (hospital-network URL), `contactPoint` (telephone + “Appointment call” type), address (street/locality/region/postalCode/country) and sometimes landline.
- Saket entry (Max Super Speciality Hospital, Saket): appointment phone `8860444888`, landline `011-40554055`, street “No. 1, 2, Press Enclave Road, Mandir Marg, Saket Institutional Area”.

### 5.10 Other pages
- `/hospitals-in-india`: 17 hospital-network links (sitemap has 18 — one minor divergence; treat sitemap as authoritative).
- `/tpa-panel`: “List of Associated TPA” — network-wide TPA panel.
- `/ambulance/emergency-ambulance-in-delhi`: H1 “Ambulance Services in Delhi (Call 01140554055)”, types, pricing, FAQ — city-level Emergency data.
- `/top-procedures`: “Choose from 275 Procedures”.
- `/find-a-doctor`: thin landing page; actual listings are the `/doctor/*-in-*` pages.

## 6. Two Saket Hospitals (disambiguation)

The network has **two** distinct Saket entries:

- `max-smart-super-speciality-hospital-saket` — Max Smart Super Speciality Hospital, Saket (650+ beds; the one analyzed above).
- `max-super-speciality-hospital-saket` — Max Super Speciality Hospital, Saket (appointment 8860444888 / 011-40554055).

The extractor must treat the **input URL as the source of truth** and never merge the two. Hospital-level info must be scoped to the hospital slug in the input URL (e.g. filter doctor affiliations and contact JSON-LD by the matching hospital name/slug).

## 7. Duplicate / Boilerplate Patterns (cleaning targets)

- Identical `MedicalOrganization` JSON-LD + global header/footer on every page.
- H1 hospital name duplicated (header + hero).
- About Us paragraph cloned into a hidden “show more” block.
- Template-generated FAQ answers with empty fields (“located at , , , .”).
- Doctor profile links repeated (cards render link 3×: image, name, “View Profile”).
- Doctors may appear on multiple speciality/location listing pages (dedup by profile URL + conservative name matching).
- Absolute vs relative URL forms for the same page.
- `javascript:void(0)` hrefs (35 on homepage) — drop.
- `lazy` images (`.lazy` class) — irrelevant to text extraction.

## 8. Scope Decision for a Single-Hospital Run

Per the “hospital-level breadth, controlled entity depth” principle, a run for one hospital URL should extract:

- **Hospital profile page** (name, type, description, parent org, beds, OTs, critical-care beds, specialists count, address, phones, geo) — authoritative.
- **Doctors at this hospital** via `/doctor/*-in-<location>` listing pages (name, designation, speciality, experience, gender, profile URL), plus (optionally) profile fetch to filter by `hospitalAffiliation` and capture OPD hours.
- **Specialities, Procedures, Conditions/Treatments, Technology** surfaced from the hospital profile page (its “Specialities & Procedures” and “Technological Advancements” sections) rather than the full network sitemaps.
- **Facilities, Emergency, Patient Services, Insurance & Payment, Visiting Information** from the hospital profile page FAQ + About Us + JSON-LD.
- **Awards & Accreditations** for the hospital name from `/award-accreditations`.
- **Contact** enrichment from `/contact-us` JSON-LD for the matching hospital entry.
- **Sources** sheet recording URL, page title, entity/category, timestamp, context.

Network-wide catalogs (all 118 specialities, 275 procedures, 690 conditions, 115 technologies, TPA panel) should be **available as an opt-in/separate mode** but not the default for a single-hospital run — otherwise the workbook is flooded with data not scoped to the hospital.

## 9. Extraction Strategy Priority (deterministic-first)

1. **JSON-LD** (`LocalBusiness`/`hospital` for hospital+contact; `Physician` + `hospitalAffiliation` for doctors; `@graph` lists for listings).
2. Semantic HTML (H1/H2 sections, About Us `div.site-content`, card classes, accordion Q&A).
3. Tables/lists (award `ul li`, TPA list).
4. Text patterns for figures (beds/OTs/ICU) inside About Us paragraph.
5. Rendered browser content — **not needed for V1** (all pages server-rendered); keep Playwright behind a flag for other sites.

## 10. Public API Endpoints

No JSON API endpoints are exposed in normal page operation (no `/api/` calls found in page scripts; site is server-rendered Laravel with `mix-manifest.json`). Doctor availability is embedded as a hidden JSON blob in listing cards (not an API). → V1 needs **no API layer**.

## 11. Proposed Architecture

Reuse the existing empty module scaffold. Pipeline:

```
cli.py (input: URL) 
  → config.py (limits, delays, UA, output dir; pyproject pydantic-settings)
  → crawler/discovery.py   (sitemap index → sitemaps; robots parse; URL normalization/dedup)
  → crawler/url_filter.py  (same-domain, robots allow-list, path-based only, drop assets/booking)
  → crawler/crawler.py     (httpx async fetch; retries, timeouts, delays, graceful stop)
  → crawler/browser.py     (Playwright facade, used only when HTTP insufficient)
  → storage/raw.py         (cache raw HTML under data/raw/<run>/<hash>.html)
  → extraction/page_analyzer.py (title, headings, JSON-LD block extraction)
  → extraction/jsonld.py   (normalize schema.org types)
  → extraction/hospital.py, doctors.py, specialities.py, procedures.py, treatments.py,
    technology.py, facilities.py, services.py, contact.py, departments.py
  → models/*.py            (Pydantic: Hospital, Doctor, Facility, Department, Speciality,
    Treatment, Procedure, Technology, Service, Source, …)
  → processing/normalize.py, deduplicate.py, validate.py (conflict detection, quality flags)
  → schema/canonical.py    (canonical categories + fields), schema/discovery.py,
    schema/mapper.py       (entity→category, dynamic field selection)
  → storage/json_store.py  (intermediate JSON dataset — the primary internal model)
  → export/excel.py, export/csv.py
  → utils/urls.py, text.py, hashing.py, logging.py
```

Design rules:

- **URL is the only required input.** Everything else derived.
- Dynamic schema: only create worksheets with ≥1 meaningful record; fields present only if data exists.
- Missing-data tri-state: `found / not_found / unclear`, plus `conflicting` preserved with sources (never silently pick).
- Every record carries `source_url`, `page_title`, `extracted_at`, `entity`, and context text snippet.
- Deterministic extraction first; LLM layer (optional/isolated) out of V1.
- No external services; local files only.

## 12. Safety / Resource Controls

- Same-domain enforcement; robots-aware filter; max pages, max depth, per-request timeout, retry limit w/ backoff, configurable delay; URL dedup (normalized + hash); HTML-asset exclusions (`*.jpg|png|pdf|…`, lazy images); graceful Ctrl-C/stop; structured logging; no external-site crawling.

## 13. Testing Strategy

- **Fixtures** (no live site): canned HTML for hospital profile, doctor listing, doctor profile, speciality, contact, awards, robots.txt, sitemap index; plus malformed/empty/no-JSON-LD pages.
- Unit tests per §20 of the master prompt: URL validation/normalization, domain restriction, robots filtering, duplicate URLs, HTML parsing, JSON-LD parsing, page classification, entity extraction, schema mapping, normalization, deduplication, conflict detection, Excel generation, empty/missing fields, HTTP-error handling.
- **Integration tests** (opt-in, live, marked `@pytest.mark.integration`) run against `maxhealthcare.in` Saket; requires network and tolerant assertions.

## 14. Resolved Decisions

1. **Default integration target**: `max-smart-super-speciality-hospital-saket` (the hospital fully analyzed here).
2. **Doctor depth**: listing pages only for V1 (name/designation/speciality/experience/gender/URL); profile fetch is a separate opt-in flag.
3. **Network catalogs**: single-hospital scope by default; full speciality/procedure/condition/technology/TPA catalogs are not part of the default run.
4. **HTML storage**: raw HTML cache kept under `data/raw/<run>/` (gitignored).

## 15. Next Step

On approval: implement Phase 2 incrementally per the master prompt §25 (smallest coherent change → tests → ruff → mypy → review), starting with utils (urls/text/logging) and models (both implemented), then discovery/crawler against fixtures, then extraction, then processing/schema/export.
