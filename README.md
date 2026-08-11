# hospital-website-extractor

Universal hospital website intelligence extractor. Crawls a hospital site, extracts
structured records, and projects them onto a 25-field master intake form (CSV/XLSX).

## Quick start

```bash
# install
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"

# crawl a hospital and export
.venv\Scripts\python main.py https://www.example-hospital.in --export
```

`--export` writes the dataset JSON plus the master intake files
(`master_hospital_intake.csv`, `master_hospital_intake.xlsx`) and the
quality/provenance report into `data/`.

## What it extracts

Eleven canonical categories feed the 25-field intake:

- identity & contacts: hospital, contacts, infrastructure
- services: facilities, departments, specialities, doctors
- clinical: treatments, procedures, technologies
- credentials: accreditations

The intake (see `schema/intake.py`) is the single output contract — one row per
hospital with text/number/dropdown/boolean fields, booleans resolved to
yes/no/unknown via weighted evidence.

## Architecture

- `crawler/` — fetch, classify (URL + content signals), crawl loop
- `extraction/` — per-category extractors plus `engine.build_dataset`
- `processing/` — normalize, deduplicate, validate, conflict detection
- `schema/` — canonical categories, field mapping, intake schema, quality
- `export/` — master CSV/XLSX intake outputs + quality/provenance workbook
- `models/` — pydantic records, dataset container, intake model

## Development

```bash
.venv\Scripts\python -m pytest
.venv\Scripts\python -m ruff check src tests
.venv\Scripts\python -m mypy src
```
