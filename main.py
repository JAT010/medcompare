"""Launcher for the hospital website extractor.

Runs the CLI as ``python main.py ...``. The actual entry point lives in
:mod:`hospital_extractor.cli`.
"""

from __future__ import annotations

from hospital_extractor.cli import main

__all__ = ["main"]

if __name__ == "__main__":
    raise SystemExit(main())
