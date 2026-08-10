# Website Reconnaissance

Scope: reconnaissance for the Universal Hospital Website Intelligence Extractor.
Target of record: **Max Healthcare** (`https://www.maxhealthcare.in/`) — first real-world integration test is the **Max Smart Super Speciality Hospital, Saket**
(`https://www.maxhealthcare.in/hospital-network/max-smart-super-speciality-hospital-saket`).

Detailed raw findings (URLs, page dumps, JSON-LD samples): see `docs/recon-maxhealthcare-saket.md`.
This document is the methodology + classification + boundaries + risks summary.

Method: read-only HTTP requests with a configurable user-agent. No authentication, no CAPTCHA
handling, no protected areas were accessed. Recon was performed on 2026-08-10.

---

## 1. Access Profile

| Check | Result |
|---|---|
| `robots.txt` | Exists. `Disallow` list: `/book-an-appointment/doctor`, `*?speciality=*`, `*?doctor=*`, `/disease`, `/hi/`, package/landing pages. `GPTBot`/`ClaudeBot`/`ChatGPT-User`/`Google-Extended` allowed. |
| `sitemap.xml` | Exists. Sitemap **index** with 25 child sitemaps. |
| Server | Akamai (`edgesuite.net`). At recon time served `200` to a plain curl UA but `403` to a full Chrome UA — **inconsistent**. UA/headers must be configurable and the crawler must probe `robots.txt` + homepage before committing to a crawl. |
| Rendering | All relevant pages fully **server-rendered** (350–900 KB HTML). **No JavaScript rendering required for V1.** |
| Pagination | **Absent** on doctor listings (`rel=next` not present, no `?page=` links). Listings are flat. |
| Public APIs | None exposed in normal page operation (no `/api/` calls in page scripts). Doctor availability is embedded as a hidden JSON blob in cards. |

## 2. Sitemap Inventory

All counts are URL counts per sitemap.

| Sitemap | Count | Content |
|---|---|---|
| `hospital-network-sitemap.xml` | 18 | `/hospital-network/<slug>` hospital profile pages (network-wide) |
| `speciality-sitemap.xml` | 118 | `/our-specialities/<slug>` |
| `procedures-sitemap.xml` | 275 | `/procedures/<slug>` |
| `conditions-treatments-sitemap.xml` | 690 | `/our-specialities/<spec>/conditions-treatments/<slug>` |
| `technology-sitemap.xml` | 115 | `/technology/<slug>` |
| `doctors-profiles-sitemap.xml` | 558 | `/doctor/<slug>` profiles (network-wide) |
| `doctors-listing-saket-sitemap.xml` | 86 | `/doctor/<speciality>-in-saket` listing pages (Saket-scoped) |
| `doctors-listing-delhi-sitemap.xml` | 115 | `/doctor/<speciality>-in-delhi` |
| `dotors-listing-sitemap.xml` (typo, real) | 45 | mixed listing pages |
| Other `doctors-listing-<city>` | ~9 | per-city listings |
| `blogs`, `faqs`, `directors`, `leadership`, `testimonials` | — | support content |

The sitemap index is the **primary discovery source**.

## 3. URL Patterns

```
/hospital-network/<hospital-slug>                                hospital profile (HIGH)
/doctor/<speciality>-in-<location>                               doctor listing (HIGH)
/doctor/<doctor-slug>                                            doctor profile (MEDIUM - optional depth)
/our-specialities/<slug>                                         speciality (MEDIUM)
/our-specialities/<spec>/conditions-treatments/<slug>            condition/treatment (LOW-MEDIUM)
/procedures/<slug>                                               procedure (MEDIUM)
/technology/<slug>                                               technology (MEDIUM)
/contact-us                                                      per-hospital contact JSON-LD (HIGH)
/award-accreditations                                            awards & accreditations, per hospital (HIGH)
/ambulance/emergency-ambulance-in-delhi                          city-level emergency/ambulance (MEDIUM)
/hospitals-in-india                                              hospital directory (MEDIUM)
/tpa-panel                                                       network TPA panel (LOW)
/find-a-doctor                                                   thin landing page (LOW)
/book-an-appointment/doctor/<uuid>                               booking flow (IRRELEVANT, robots-disallowed)
```

## 4. Page Classification

Classification signals used:

- **Domain/URL scope**: is the page under the target hospital slug, the target city, or network-wide?
- **Content signal**: page title, H1/H2 headings, JSON-LD `@type`, breadcrumbs, link density to entities.
- **Entity signal**: presence of doctor cards, FAQ accordions, facility/technology lists.
- **Structure**: tables, card classes, accordion sections, tabbed sections.

| Class | Pages | Signals |
|---|---|---|
| **HIGH VALUE** | Hospital profile `/hospital-network/max-smart-super-speciality-hospital-saket`; `/doctor/*-in-saket` listing pages (86); `/contact-us` (hospital-specific entry); `/award-accreditations` (hospital-specific section) | Hospital-slug scope; `LocalBusiness`/`Physician` JSON-LD; About Us with capacity figures; doctor cards; per-hospital FAQ; per-hospital awards. |
| **MEDIUM VALUE** | `/our-specialities/<slug>`; `/procedures/<slug>`; `/technology/<slug>`; `/ambulance/emergency-ambulance-in-delhi`; `/hospitals-in-india` | Network-wide but category-bearing; useful description text; city-scoped emergency. |
| **LOW VALUE** | `/conditions-treatments/<slug>`; `/doctor/<doctor-slug>` profiles; `/tpa-panel`; `/preventive-health-plans`; `/top-procedures`; `/faqs` hub | Deep medical content (out of shallow scope); profile depth only behind an opt-in flag; network-wide lists. |
| **IRRELEVANT** | `/blogs/*`, `/news/*`, `/testimonials`, `/investors`, `/careers`, `/academics`, `/book-an-appointment/*`, external domains, images/videos/PDFs | Non-hospital-level content; booking flows; binary assets; off-domain links. |

A page is eligible for a single-hospital run only if it is hospital-slug scoped or explicitly whitelisted
(city emergency page). Everything else is either skipped or limited to description-level extraction
without recursive follow-up.

## 5. Extraction Boundaries (controlled entity depth)

Extract hospital-relevant facts about an entity, then **STOP**. Never recurse into the entity's own sub-world.

| Entity | Extract (shallow) | Do NOT extract |
|---|---|---|
| Hospital | name, type, description, parent org, established year, status, bed/doctor/staff/speciality counts, website | financials, investor relations, corporate structure |
| Doctor | name, speciality, department, designation, experience (when clearly stated), gender, OPD hours, profile URL | education history, memberships, publications, research, universities, patients, external profiles |
| Facility | name, category, availability, description | equipment specs, maintenance |
| Department | name, description | internal staff rosters (those are doctors), org charts |
| Speciality | name, category, description | full medical knowledge, every condition |
| Treatment / Condition | name, speciality, description | symptoms, risk factors, deep clinical content |
| Procedure | name, speciality, description | step-by-step surgical detail, clinical protocols |
| Technology | name, category, description | manuals, technical specs |
| Emergency | service, availability, contact, description | dispatch internals |
| Patient Service | name, category, availability, description | operational details |
| Insurance | provider/TPA name, cashless/government flags, description | policy terms, premium rates |
| Accreditation | name, organization, status, year, description | — |
| Award | name, organization, year, description | — |
| Program | name, category, description | — |
| Visiting | visiting/OPD hours, appointment/registration/admission/discharge info | — |
| Contact | address, city/state/country/postal, phones, emails, maps URL, geo | — |

## 6. Key Website Findings

1. **Hospital profile page is the anchor.** `LocalBusiness` JSON-LD (name, telephone, street address,
   locality, postal code, country, geo, 24/7 opening hours) + About Us paragraph with `650+ bedded`,
   `25 high-end modular operation theatres`, `170+ critical care beds`, `680+ leading specialists`,
   parent org `Gujarmal Modi Hospital & Research Centre for Medical Sciences`.
2. **26-item FAQ accordion** on the hospital page covers emergency (24/7), working hours (24/7),
   visiting hours, insurance/TPA/CGHS, payment options, pharmacy, lab, rehab, parking, international
   patients, virtual consultation. Some answers are template-generated and near-empty (e.g. the address
   answer is `"located at , , , ."` — a boilerplate trap the cleaner must drop).
3. **Doctor listings are flat, server-rendered cards** with name, designation, comma-separated
   specialities, `Experience: NN+ Years`, gender, profile URL, and a hidden JSON availability blob keyed
   by hospital slug. Also a `Physician` JSON-LD `@graph` on each listing. No pagination.
4. **Cards can span multiple hospitals** — a Saket listing may show a doctor whose availability JSON
   includes other locations (e.g. Panchsheel Park). Affiliation filtering should use the profile's
   `hospitalAffiliation[]` when profile fetching is enabled; otherwise accept the listing-page scope.
5. **Doctor profiles** carry `Physician` JSON-LD with `hospitalAffiliation[]` (hospital name + OPD hours)
   and a Work Experience section ("20 Years as Dermatologist").
6. **Two distinct Saket hospitals exist** — `max-smart-super-speciality-hospital-saket` (650+ beds,
   `LocalBusiness` JSON-LD) and `max-super-speciality-hospital-saket` (514+ beds, **no** local
   `LocalBusiness` block; contact available only via `/contact-us` JSON-LD). The input URL is the
   authority; the two must never be merged.
7. **Speciality tab on the hospital page** links to location-scoped doctor listings
   (`/doctor/orthopaedists-in-saket`, ...) — these double as the hospital's speciality directory.
8. **Awards are per-hospital** (`/award-accreditations` with per-hospital sections; years embedded in
   award names, e.g. "ET Healthcare Summit Award (2024)").
9. **Contact JSON-LD is per-hospital** (`@type: hospital`, `@id` = hospital-network URL) with
   appointment/landline phones.
10. **Duplicate/boilerplate patterns**: identical `MedicalOrganization` JSON-LD on every page; H1
    hospital name duplicated; About Us cloned into a hidden "read more" block; doctor profile links
    rendered 3× per card; `javascript:void(0)` hrefs; lazy-loaded images.

## 7. Implementation Risks & V1-Safe Solutions

| Risk | V1 solution |
|---|---|
| Akamai 403 / UA-dependent blocking | Configurable UA + headers; probe `robots.txt` and homepage first; treat 403 as "hard stop with clear error", not a retry storm. |
| Very large site (690 conditions, 558 profiles) | Single-hospital scope: only hospital-slug/city pages. Network catalogs are opt-in, never default. |
| JS-rendered sections | Not required for V1 (all target pages server-rendered). Playwright kept behind a flag for other sites. |
| Duplicate URLs / same page under many forms | URL normalization (lowercase host, default-port strip, fragment strip, dot-segment resolve, trailing-slash strip, sorted query); dedupe before and after fetch. |
| Boilerplate / navigation noise | Text cleaning (collapse whitespace, strip tags, unicode spaces, sequential-dedup); template-answer drop (near-empty FAQ answers). |
| Conflicting values (e.g. beds 650 vs 514 across the two Saket hospitals) | Never merge distinct hospitals; keep per-source values and mark `conflicting` in Data Quality. |
| Multiple representations of same fact | JSON-LD preferred over HTML; controlled confidence levels; never silently overwrite. |
| Doctor appearing on many listing pages | Dedup by normalized profile URL; conservative name matching for missing URLs. |
| Missing data | Tri-state `found / not_found / unclear`; dynamic schema drops empty categories. Never fabricate. |
| Robots restrictions | Robots-aware URL filter (disallow list incl. `*?speciality=*`, `*?doctor=*`, `/book-an-appointment/doctor`); path-based URLs only. |
| Rate limiting | Configurable per-request delay, retry-with-backoff, max pages, graceful Ctrl-C, logging. |
| Malformed HTML / partial pages | Robust parser defaults; records still extracted when partial; failures counted as "pages failed". |

## 8. Files Created / Inspected

- Created: `docs/reconnaissance.md` (this file).
- Reference: `docs/recon-maxhealthcare-saket.md` (raw site findings from Phase 1).
- Inspected (read-only): `pyproject.toml`, `uv.lock`, `config/default.toml` (empty), `src/hospital_extractor/**`
  (scaffold), `tests/**` (scaffold), `README.md`, `data/`, `logs/`.
