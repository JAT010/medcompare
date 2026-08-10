"""Programmatic entry point for the hospital website extractor."""

from __future__ import annotations

from hospital_extractor.cli import main

__all__ = ["main"]

if __name__ == "__main__":
    raise SystemExit(main())
