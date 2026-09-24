"""Verification orchestration: background, explicit, local-only.

P19-B adds a persistent verification layer on top of the P19-A snapshot /
change-detection foundation:

- :class:`~hospital_extractor.models.verification.VerificationJob` — created
  when an authoritative capture detects changes; names the target source that
  must be checked.
- :class:`~hospital_extractor.verification.scheduler.VerificationScheduler` —
  SQLite-backed scheduling (schedule / due / claim / complete / fail).
- :class:`~hospital_extractor.verification.service` — the worker: claims due
  jobs, performs a FULL_DATASET capture of the target source, evaluates only
  the triggering changes, stores a snapshot + result.

Crawling is a background operation only.  Normal user reads (profiles, search,
comparison, snapshot/change history) touch the database — never the crawler.
"""

from __future__ import annotations

from hospital_extractor.verification.scheduler import VerificationScheduler
from hospital_extractor.verification.service import (
    create_manual_job,
    run_worker_cycle,
    schedule_job_for_capture,
)

__all__ = [
    "VerificationScheduler",
    "create_manual_job",
    "run_worker_cycle",
    "schedule_job_for_capture",
]