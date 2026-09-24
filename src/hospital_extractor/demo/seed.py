"""Deterministic seed for a clean MedCompare demo database.

Creates a separate SQLite file (never the real development store) containing
fictional hospitals, simulated sources, monitoring, verification, reconciliation,
reports, audit events, quality metrics, and DEMO users whose passwords come
from environment variables — never from source code.

Real hospital API/feed integrations require an authorized source configuration
and credentials and are not included by default.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from hospital_extractor.auth.audit import AuditOutcome, record_audit
from hospital_extractor.auth.models import Role
from hospital_extractor.auth.store import AuthStore
from hospital_extractor.config import load_config
from hospital_extractor.extraction import build_dataset
from hospital_extractor.models.change import ChangeSetStatus, ChangeType
from hospital_extractor.models.dataset import HospitalDataset
from hospital_extractor.models.verification import (
    ChangeVerification,
    VerificationJob,
    VerificationOutcome,
    VerificationProvenance,
    VerificationResult,
    VerificationResultStatus,
)
from hospital_extractor.monitoring.service import create_policy
from hospital_extractor.reconciliation.service import create_cases_for_result
from hospital_extractor.reconciliation.settings import reconciliation_settings
from hospital_extractor.reporting.service import generate_report
from hospital_extractor.storage import HospitalDatabase

__all__ = [
    "DEMO_ADMIN_PASSWORD_ENV",
    "DEMO_DB_PATH",
    "DEMO_USER_PASSWORD_ENV",
    "SeedError",
    "SeedSummary",
    "seed_demo",
]

DEMO_DB_PATH = Path("data/demo/medcompare-demo.db")
DEMO_ADMIN_PASSWORD_ENV = "MEDCOMPARE_DEMO_ADMIN_PASSWORD"
DEMO_USER_PASSWORD_ENV = "MEDCOMPARE_DEMO_USER_PASSWORD"

DEMO_ADMIN_USERNAME = "admin@medcompare.demo"
DEMO_USER_USERNAME = "user@medcompare.demo"
DEMO_REVIEWER_USERNAME = "reviewer@medcompare.demo"

# Fixed demo identities (deterministic across reseeds).
DEMO_HOSPITALS: list[dict[str, str]] = [
    {
        "hospital_id": "h_demo_northstar",
        "name": "Northstar Multispeciality Hospital (DEMO)",
        "ref": "demo-northstar",
        "city": "Pune",
        "source_type": "mock_api",
    },
    {
        "hospital_id": "h_demo_solstice",
        "name": "Solstice Cardiac & Ortho Institute (DEMO)",
        "ref": "demo-solstice",
        "city": "Chennai",
        "source_type": "mock_api",
    },
    {
        "hospital_id": "h_demo_harborview",
        "name": "Harborview General Hospital (DEMO)",
        "ref": "demo-harborview",
        "city": "Kochi",
        "source_type": "website",
    },
    {
        "hospital_id": "h_demo_lakeshore",
        "name": "Lakeshore Research Hospital (DEMO)",
        "ref": "demo-lakeshore",
        "city": "Jaipur",
        "source_type": "website",
    },
]

_FIXED_NOW = datetime(2026, 9, 24, 12, 0, 0, tzinfo=UTC)


class SeedError(RuntimeError):
    """Demo seed failed (missing env password, I/O, etc.)."""


@dataclass
class SeedSummary:
    """Counts and IDs produced by one successful seed run."""

    db_path: Path
    hospitals: list[str] = field(default_factory=list)
    datasets: int = 0
    snapshots: int = 0
    change_sets: int = 0
    verification_jobs: int = 0
    verification_results: int = 0
    reconciliation_cases: int = 0
    reports: int = 0
    monitoring_policies: int = 0
    audit_events: int = 0
    users: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "db_path": str(self.db_path),
            "hospitals": list(self.hospitals),
            "datasets": self.datasets,
            "snapshots": self.snapshots,
            "change_sets": self.change_sets,
            "verification_jobs": self.verification_jobs,
            "verification_results": self.verification_results,
            "reconciliation_cases": self.reconciliation_cases,
            "reports": self.reports,
            "monitoring_policies": self.monitoring_policies,
            "audit_events": self.audit_events,
            "users": list(self.users),
            "disclaimer": (
                "Real hospital API/feed integrations require an authorized "
                "source configuration and credentials and are not included "
                "by default."
            ),
            "note": (
                "All hospitals and credentials in this database are fictional "
                "and labeled DEMO. Search queries are not persistently stored; "
                "results are generated from stored hospital evidence."
            ),
        }


def _require_password(env_name: str) -> str:
    value = os.environ.get(env_name, "")
    if not value:
        raise SeedError(
            f"Set {env_name} to a password of at least 8 characters "
            "(never hardcode demo passwords in source)."
        )
    if len(value) < 8:
        raise SeedError(f"{env_name} must be at least 8 characters")
    return value


def _website_pages(
    ref: str, name: str, beds: int, doctors: int, procedures: list[str]
) -> list[tuple[str, str]]:
    base = f"https://{ref}.example.test"
    proc_html = "".join(f"<li>{p}</li>" for p in procedures)
    home = f"""<!doctype html><html><head><title>{name}</title></head>
<body>
<h1>{name}</h1>
<p>{name} is a multi-speciality hospital in India with {beds} beds and {doctors} doctors.
Established in 2005. DEMO fictional data for MedCompare only.</p>
<script type="application/ld+json">{{
  "@context": "https://schema.org",
  "@type": "Hospital",
  "name": "{name}",
  "url": "{base}/",
  "description": "{beds}-bed multi-speciality hospital.",
  "telephone": "+91-80-00000000",
  "address": {{
    "@type": "PostalAddress",
    "addressLocality": "Demo City",
    "addressRegion": "Demo State",
    "addressCountry": "IN"
  }},
  "foundingDate": "2005"
}}</script>
</body></html>"""
    treatments = f"""<!doctype html><html><head><title>Treatments - {name}</title></head>
<body><h1>Treatments</h1><ul>{proc_html}</ul></body></html>"""
    contact = f"""<!doctype html><html><head><title>Contact - {name}</title></head>
<body><h1>Contact</h1><p>{name}, Demo City</p><p>+91-80-00000000</p>
<p>care@{ref}.example.test</p></body></html>"""
    return [
        (f"{base}/", home),
        (f"{base}/treatments", treatments),
        (f"{base}/contact", contact),
    ]


def _register_hospital(db: HospitalDatabase, entry: dict[str, str]) -> str:
    hospital_id = entry["hospital_id"]
    name = entry["name"]
    ref = entry["ref"]
    cur = db._connection  # noqa: SLF001 — intentional direct seed of fixed IDs
    cur.execute(
        "INSERT OR IGNORE INTO hospitals (hospital_id, name, created_at) VALUES (?, ?, ?)",
        (hospital_id, name, _FIXED_NOW.isoformat()),
    )
    cur.execute(
        "INSERT OR IGNORE INTO hospital_aliases (alias_type, alias_value, hospital_id)"
        " VALUES ('source_ref', ?, ?)",
        (ref, hospital_id),
    )
    cur.execute(
        "INSERT OR IGNORE INTO hospital_aliases (alias_type, alias_value, hospital_id)"
        " VALUES ('name_slug', ?, ?)",
        (_slug(name), hospital_id),
    )
    cur.execute(
        "UPDATE hospitals SET name = ? WHERE hospital_id = ?",
        (name, hospital_id),
    )
    cur.commit()
    return hospital_id


def _slug(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in value)
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-")


def _website_dataset(
    entry: dict[str, str],
    beds: int,
    doctors: int,
    procedures: list[str],
    config: object,
) -> HospitalDataset:
    pages = _website_pages(entry["ref"], entry["name"], beds, doctors, procedures)
    return build_dataset(
        pages,
        target_url=f"https://{entry['ref']}.example.test",
        config=config,  # type: ignore[arg-type]
    )


def _load_mock_dataset(hospital_ref: str) -> HospitalDataset:
    import anyio
    import httpx

    from hospital_extractor.mockapi.app import create_mock_app
    from hospital_extractor.sources.mock_api import MockHospitalAPIDataSource

    transport = httpx.ASGITransport(app=create_mock_app())
    source = MockHospitalAPIDataSource(
        hospital_id=hospital_ref,
        client=httpx.AsyncClient(transport=transport, base_url="http://testserver"),
    )
    dataset: HospitalDataset = anyio.run(source.fetch)
    return dataset


def seed_demo(
    db_path: Path | str = DEMO_DB_PATH,
    *,
    reset: bool = False,
    admin_password: str | None = None,
    user_password: str | None = None,
    reviewer_password: str | None = None,
) -> SeedSummary:
    """Create (or reset) a clean deterministic demo database.

    Passwords default to env vars ``MEDCOMPARE_DEMO_ADMIN_PASSWORD`` and
    ``MEDCOMPARE_DEMO_USER_PASSWORD``.  Optional ``reviewer_password`` uses
    ``MEDCOMPARE_DEMO_REVIEWER_PASSWORD`` when not passed.
    """
    path = Path(db_path)
    if reset and path.exists():
        path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)

    admin_password = admin_password or _require_password(DEMO_ADMIN_PASSWORD_ENV)
    user_password = user_password or _require_password(DEMO_USER_PASSWORD_ENV)
    reviewer_password = reviewer_password or os.environ.get(
        "MEDCOMPARE_DEMO_REVIEWER_PASSWORD", ""
    )

    summary = SeedSummary(db_path=path)

    with HospitalDatabase(path) as db, AuthStore(path) as store:
        store.ensure_schema()
        config = load_config()
        now = _FIXED_NOW
        db_auth = store

        # --- hospitals + datasets + snapshots -----------------------------
        website_specs = {
            "demo-harborview": (420, 180, ["Knee Replacement", "Angioplasty", "Liver Transplant"]),
            "demo-lakeshore": (
                310,
                140,
                ["Knee Replacement", "Cancer Chemotherapy", "Robotic Surgery"],
            ),
        }

        for entry in DEMO_HOSPITALS:
            hospital_id = _register_hospital(db, entry)
            summary.hospitals.append(hospital_id)

            if entry["source_type"] == "mock_api":
                dataset = _load_mock_dataset(entry["ref"])
            else:
                beds, doctors, procs = website_specs[entry["ref"]]
                dataset = _website_dataset(entry, beds, doctors, procs, config)

            capture = db.save_snapshot(dataset, captured_at=now, hospital_id=hospital_id)
            summary.datasets += 1
            summary.snapshots += 1
            summary.change_sets += 1
            assert capture.change_set.status is ChangeSetStatus.INITIAL_SNAPSHOT

            # Second capture with a small deterministic delta → CHANGES_DETECTED
            if entry["source_type"] == "website":
                beds, doctors, procs = website_specs[entry["ref"]]
                delta = _website_dataset(entry, beds + 20, doctors, procs, config)
                capture2 = db.save_snapshot(
                    delta, captured_at=now, hospital_id=hospital_id
                )
                summary.datasets += 1
                summary.snapshots += 1
                summary.change_sets += 1
                assert capture2.change_set.status is ChangeSetStatus.CHANGES_DETECTED

        # --- verification on harborview (mismatch → recon → report) -------
        harbor_id = "h_demo_harborview"
        change_sets = db.list_change_sets(harbor_id, source_type="website")
        changes_detected = next(
            cs for cs in change_sets if cs.status is ChangeSetStatus.CHANGES_DETECTED
        )
        job_id = "v_demo_harborview"
        db.create_verification_job(
            _demo_job(
                job_id,
                harbor_id,
                changes_detected.change_set_id,
                changes_detected.snapshot_id,
                now,
            )
        )
        summary.verification_jobs += 1

        result = VerificationResult(
            verification_result_id="r_demo_harborview",
            job_id=job_id,
            hospital_id=harbor_id,
            change_set_id=changes_detected.change_set_id,
            status=VerificationResultStatus.MISMATCH,
            change_results=[
                ChangeVerification(
                    path="hospital.bed_count",
                    category="hospital",
                    change_type=ChangeType.MODIFIED,
                    claimed_value=440,
                    website_value=420,
                    outcome=VerificationOutcome.MISMATCH,
                    detail="DEMO: claimed bed count differs from website capture",
                )
            ],
            verification_snapshot_id=changes_detected.snapshot_id,
            checked_at=now,
            provenance=VerificationProvenance(
                triggering_source_type="mock_api",
                target_source_type="website",
                target_ref=f"https://{harbor_id.replace('h_demo_', 'demo-')}.example.test",
            ),
            explanation="DEMO mismatch for teacher walkthrough",
            created_at=now,
        )
        db.save_verification_result(result)
        summary.verification_results += 1

        cases = create_cases_for_result(
            db,
            result,
            settings=reconciliation_settings(),
            actor="demo-seed",
            now=now,
        )
        summary.reconciliation_cases += len(cases)
        case = cases[0]
        db.save_report(
            generate_report(
                db,
                case,
                format="json",
                generated_by="demo-seed",
                now=now,
            )
        )
        summary.reports += 1

        # Second open case on lakeshore (also has CHANGES_DETECTED)
        lake_id = "h_demo_lakeshore"
        lake_cs = next(
            cs
            for cs in db.list_change_sets(lake_id, source_type="website")
            if cs.status is ChangeSetStatus.CHANGES_DETECTED
        )
        lake_job_id = "v_demo_lakeshore"
        db.create_verification_job(
            _demo_job(lake_job_id, lake_id, lake_cs.change_set_id, lake_cs.snapshot_id, now)
        )
        summary.verification_jobs += 1
        lake_result = VerificationResult(
            verification_result_id="r_demo_lakeshore",
            job_id=lake_job_id,
            hospital_id=lake_id,
            change_set_id=lake_cs.change_set_id,
            status=VerificationResultStatus.MISMATCH,
            change_results=[
                ChangeVerification(
                    path="hospital.bed_count",
                    category="hospital",
                    change_type=ChangeType.MODIFIED,
                    claimed_value=330,
                    website_value=310,
                    outcome=VerificationOutcome.MISMATCH,
                    detail="DEMO: second open review case",
                )
            ],
            verification_snapshot_id=lake_cs.snapshot_id,
            checked_at=now,
            provenance=VerificationProvenance(
                triggering_source_type="mock_api",
                target_source_type="website",
                target_ref="https://demo-lakeshore.example.test",
            ),
            explanation="DEMO second reconciliation case",
            created_at=now,
        )
        db.save_verification_result(lake_result)
        summary.verification_results += 1
        lake_cases = create_cases_for_result(
            db,
            lake_result,
            settings=reconciliation_settings(),
            actor="demo-seed",
            now=now,
        )
        summary.reconciliation_cases += len(lake_cases)

        # --- monitoring policies ------------------------------------------
        for entry in DEMO_HOSPITALS[:3]:
            create_policy(
                db,
                hospital_id=entry["hospital_id"],
                frequency="daily",
                source_type=entry["source_type"],
                target_ref=f"https://{entry['ref']}.example.test",
                enabled=True,
                created_by="demo-seed",
                now=now,
            )
            summary.monitoring_policies += 1

        # --- users + audit -------------------------------------------------
        for action, actor, rtype, rid in [
            ("demo.seed", "system", "database", str(path)),
            ("hospital.capture", "demo-seed", "snapshot", "demo"),
            ("verification.create", "demo-seed", "verification_job", "v_demo_harborview"),
            (
                "reconciliation.review_started",
                "demo-seed",
                "reconciliation_case",
                case.reconciliation_case_id,
            ),
            ("report.generate", "demo-seed", "report", "demo"),
            ("monitoring.create", "demo-seed", "monitoring_policy", "demo"),
            ("source.test", "demo-seed", "source_type", "hospital_api"),
        ]:
            record_audit(
                db_auth,
                action=action,
                outcome=AuditOutcome.SUCCESS,
                actor_id=actor,
                actor_role="demo",
                resource_type=rtype,
                resource_id=rid,
                hospital_id=(
                    harbor_id
                    if rtype
                    in {
                        "snapshot",
                        "verification_job",
                        "reconciliation_case",
                        "report",
                        "monitoring_policy",
                    }
                    else ""
                ),
                metadata={"demo": True, "seeded_at": now.isoformat()},
            )
            summary.audit_events += 1

        store.create_user(
            username=DEMO_ADMIN_USERNAME,
            password=admin_password,
            role=Role.ADMIN,
            enabled=True,
            hospital_ids=[h["hospital_id"] for h in DEMO_HOSPITALS],
            user_id="u_demo_admin",
        )
        summary.users.append(DEMO_ADMIN_USERNAME)
        store.create_user(
            username=DEMO_USER_USERNAME,
            password=user_password,
            role=Role.VIEWER,
            enabled=True,
            hospital_ids=["h_demo_harborview"],
            user_id="u_demo_user",
        )
        summary.users.append(DEMO_USER_USERNAME)
        if reviewer_password:
            store.create_user(
                username=DEMO_REVIEWER_USERNAME,
                password=reviewer_password,
                role=Role.REVIEWER,
                enabled=True,
                hospital_ids=["h_demo_harborview", "h_demo_lakeshore"],
                user_id="u_demo_reviewer",
            )
            summary.users.append(DEMO_REVIEWER_USERNAME)

        record_audit(
            store,
            action="auth.login",
            outcome=AuditOutcome.SUCCESS,
            actor_id=DEMO_ADMIN_USERNAME,
            actor_role="admin",
            resource_type="user",
            resource_id="u_demo_admin",
            metadata={"demo": True, "note": "seeded audit sample"},
        )
        summary.audit_events += 1

    return summary


def _demo_job(
    job_id: str,
    hospital_id: str,
    change_set_id: str,
    snapshot_id: str,
    now: datetime,
) -> VerificationJob:
    from hospital_extractor.models.verification import VerificationJob, VerificationJobStatus

    return VerificationJob(
        verification_job_id=job_id,
        hospital_id=hospital_id,
        change_set_id=change_set_id,
        triggering_snapshot_id=snapshot_id,
        triggering_source_type="mock_api",
        target_source_type="website",
        target_ref=f"https://{hospital_id.replace('h_demo_', 'demo-')}.example.test",
        status=VerificationJobStatus.MISMATCH,
        scheduled_for=now,
        started_at=now,
        completed_at=now,
        attempt_count=1,
        last_error=None,
        created_at=now,
        updated_at=now,
    )
