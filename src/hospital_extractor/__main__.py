"""Allow running the package with ``python -m hospital_extractor``."""

from __future__ import annotations

from hospital_extractor.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
