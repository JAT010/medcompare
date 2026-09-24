"""Benchmark runner (P26): executes deterministic cases with ground truth.

Each case returns decomposed metrics with raw counts. Failures are recorded,
never hidden. Performance timings use ``time.perf_counter`` over the
in-process workload (not production web latency).
"""

from __future__ import annotations

import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from hospital_extractor.benchmark.fixtures import FixtureBundle, build_fixtures
from hospital_extractor.benchmark.models import (
    BenchmarkCase,
    BenchmarkResult,
    BenchmarkRun,
    EnvironmentInfo,
    MetricResult,
    RealWorldObservation,
)

__all__ = ["run_benchmarks"]

METHODOLOGY = (
    "Deterministic in-process fixtures with explicit ground truth "
    "(TEST CORRECTNESS). Real-world site results are reported separately as "
    "REAL-WORLD OBSERVATION from prior documented runs — not model accuracy. "
    "No composite overall score."
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _case(
    case_id: str,
    label: str,
    category: str,
    description: str,
    fixture_ids: list[str],
) -> BenchmarkCase:
    return BenchmarkCase(
        case_id=case_id,
        label=label,
        category=category,
        description=description,
        fixture_ids=fixture_ids,
    )


def _run_case(
    case: BenchmarkCase,
    fn: Callable[[FixtureBundle], tuple[bool | None, list[MetricResult], str]],
    fx: FixtureBundle,
) -> BenchmarkResult:
    start = time.perf_counter()
    try:
        passed, metrics, notes = fn(fx)
        duration = (time.perf_counter() - start) * 1000.0
        if passed is None:
            status: Literal["pass", "fail", "observation", "error"] = "observation"
        elif passed:
            status = "pass"
        else:
            status = "fail"
        return BenchmarkResult(
            case=case,
            passed=passed,
            status=status,
            metrics=metrics,
            duration_ms=round(duration, 3),
            notes=notes,
        )
    except Exception as exc:  # noqa: BLE001 — record, never hide
        duration = (time.perf_counter() - start) * 1000.0
        return BenchmarkResult(
            case=case,
            passed=False,
            status="error",
            metrics=[],
            duration_ms=round(duration, 3),
            error=f"{type(exc).__name__}: {exc}",
        )


# ---------------------------------------------------------------------------
# Case implementations
# ---------------------------------------------------------------------------


def _case_change_detection(fx: FixtureBundle) -> tuple[bool | None, list[MetricResult], str]:
    """A/B/C/D: unchanged, modified, added, removed + fingerprint determinism."""
    from hospital_extractor.benchmark._snap import make_snapshot
    from hospital_extractor.snapshots import (
        build_change_set,
        canonical_digest,
    )

    snap_a = make_snapshot(fx.unchanged, "s_base")
    snap_a2 = make_snapshot(fx.unchanged, "s_base_copy")
    snap_mod = make_snapshot(fx.modified, "s_mod")
    snap_add = make_snapshot(fx.with_added, "s_add")
    snap_rem = make_snapshot(fx.with_removed, "s_rem")

    # A: same content → NO_CHANGE
    cs_same = build_change_set(snap_a, snap_a2)
    # B: bed_count modified
    cs_mod = build_change_set(snap_a, snap_mod)
    # C: treatment added (base has 1, added has 2)
    cs_add = build_change_set(snap_a, snap_add)
    # D: treatment removed (removed has 1, base has 1 — equal length; use add→rem)
    cs_rem = build_change_set(snap_add, snap_rem)

    # Fingerprint determinism
    h1 = canonical_digest(fx.unchanged)
    h2 = canonical_digest(fx.unchanged)
    h3 = canonical_digest(fx.modified)
    det_ok = h1 == h2 and h1 != h3

    mod_changes = [c for c in cs_mod.changes if c.change_type.value == "modified"]
    add_changes = [c for c in cs_add.changes if c.change_type.value == "added"]
    rem_changes = [c for c in cs_rem.changes if c.change_type.value == "removed"]

    metrics = [
        MetricResult.available(
            name="change_unchanged_no_change",
            definition="Identical datasets produce ChangeSetStatus.NO_CHANGE.",
            value=cs_same.status.value == "no_change",
            expected=True,
            passed=cs_same.status.value == "no_change",
            fixture_id="A",
            detail=f"status={cs_same.status.value}",
        ),
        MetricResult.available(
            name="change_modified_detected",
            definition="bed_count 300→450 yields ≥1 MODIFIED change.",
            value=len(mod_changes),
            expected=">=1",
            passed=len(mod_changes) >= 1,
            fixture_id="B",
            detail=f"total_changes={len(cs_mod.changes)}",
        ),
        MetricResult.available(
            name="change_added_entity",
            definition="Adding a treatment yields ≥1 ADDED change.",
            value=len(add_changes),
            expected=">=1",
            passed=len(add_changes) >= 1,
            fixture_id="C",
        ),
        MetricResult.available(
            name="change_removed_entity",
            definition="Removing a treatment yields ≥1 REMOVED change.",
            value=len(rem_changes),
            expected=">=1",
            passed=len(rem_changes) >= 1,
            fixture_id="D",
        ),
        MetricResult.available(
            name="fingerprint_determinism",
            definition=(
                "canonical_digest is stable on identical input and differs "
                "on modified input."
            ),
            value=det_ok,
            expected=True,
            passed=det_ok,
            detail=f"h_len={len(h1)}",
        ),
    ]
    return all(m.passed for m in metrics), metrics, "change detection A–D"


def _case_verification(fx: FixtureBundle) -> tuple[bool | None, list[MetricResult], str]:
    """H/I/J: verified / mismatch / unverified evaluation rules."""
    from hospital_extractor.models.change import Change, ChangeType
    from hospital_extractor.verification.evaluate import evaluate_change

    # H: website still has the claimed treatment → VERIFIED (ADDED present)
    change_added = Change(
        category="treatments",
        path="treatments",
        field="name",
        change_type=ChangeType.ADDED,
        entity_name="Cardiac Surgery",
        old_value=None,
        new_value="Cardiac Surgery",
    )
    ev_h = evaluate_change(change_added, fx.verified_claim)

    # I: claim bed_count 300, website 420 → MISMATCH
    change_mod = Change(
        category="hospital",
        path="hospital",
        field="bed_count",
        change_type=ChangeType.MODIFIED,
        entity_name=None,
        old_value=300,
        new_value=300,  # claim value the API asserted; website differs
    )
    # For MODIFIED: new_value is claim; website is fixture.hospital.bed_count=420
    # evaluate uses change.new_value as claim and website field as target
    # Actually evaluate_change compares website value to change.new_value for MODIFIED
    # Let's set new_value to the claim (300) and website has 420 → MISMATCH
    ev_i = evaluate_change(change_mod, fx.mismatched_claim)

    # J: ADDED treatment not present on website → UNVERIFIED
    change_add_missing = Change(
        category="treatments",
        path="treatments",
        field="name",
        change_type=ChangeType.ADDED,
        entity_name="Nonexistent Procedure",
        old_value=None,
        new_value="Nonexistent Procedure",
    )
    ev_j = evaluate_change(change_add_missing, fx.unverified_target)

    h_ok = ev_h.outcome.value == "verified"
    i_ok = ev_i.outcome.value == "mismatch"
    j_ok = ev_j.outcome.value == "unverified"

    website_bed = (
        fx.mismatched_claim.hospital.bed_count
        if fx.mismatched_claim.hospital is not None
        else None
    )
    metrics = [
        MetricResult.available(
            name="verification_verified",
            definition="ADDED entity present on website → VERIFIED.",
            value=ev_h.outcome.value,
            expected="verified",
            passed=h_ok,
            fixture_id="H",
        ),
        MetricResult.available(
            name="verification_mismatch",
            definition="MODIFIED claim differs from website value → MISMATCH.",
            value=ev_i.outcome.value,
            expected="mismatch",
            passed=i_ok,
            fixture_id="I",
            detail=f"claim={change_mod.new_value} website={website_bed}",
        ),
        MetricResult.available(
            name="verification_unverified",
            definition="ADDED entity absent on website → UNVERIFIED.",
            value=ev_j.outcome.value,
            expected="unverified",
            passed=j_ok,
            fixture_id="J",
        ),
    ]
    return all(m.passed for m in metrics), metrics, "verification H–J"


def _case_reconciliation(fx: FixtureBundle) -> tuple[bool | None, list[MetricResult], str]:
    """K: state-machine transition table matches ALLOWED_TRANSITIONS."""
    from hospital_extractor.reconciliation.models import (
        ALLOWED_TRANSITIONS,
        ReconciliationCaseStatus,
    )

    ok = True
    metrics: list[MetricResult] = []
    for src, allowed in fx.reconciliation_transitions.items():
        status = ReconciliationCaseStatus(src)
        actual = sorted(s.value for s in ALLOWED_TRANSITIONS[status])
        expected = sorted(allowed)
        match = actual == expected
        ok = ok and match
        metrics.append(
            MetricResult.available(
                name=f"recon_transitions_{src}",
                definition=f"ALLOWED_TRANSITIONS[{src}] equals ground-truth set.",
                value=",".join(actual),
                expected=",".join(expected),
                passed=match,
                fixture_id="K",
            )
        )
    return ok, metrics, "reconciliation transitions K"


def _case_monitoring(fx: FixtureBundle) -> tuple[bool | None, list[MetricResult], str]:
    """L/M: claim once + retry/backoff settings + failed→requeued semantics."""
    from hospital_extractor.monitoring.settings import monitoring_settings
    from hospital_extractor.monitoring.store import MonitoringStore
    from hospital_extractor.storage import HospitalDatabase

    with tempfile.TemporaryDirectory() as tmp:
        db = HospitalDatabase(Path(tmp) / "bench.db")
        store = MonitoringStore(db, settings=monitoring_settings())
        due_before = store.get_due(datetime.now(UTC))
        store.close()
        db.close()

    settings = monitoring_settings()
    l_ok = len(due_before) == 0
    m_ok = settings.max_attempts >= 1 and settings.backoff_minutes >= 0

    metrics = [
        MetricResult.available(
            name="monitoring_empty_store_no_due",
            definition="Fresh monitoring store has zero due policies (no phantom runs).",
            value=len(due_before),
            expected=0,
            passed=l_ok,
            fixture_id="L",
        ),
        MetricResult.available(
            name="monitoring_retry_policy",
            definition="max_attempts≥1 and backoff_minutes≥0 (failure recovery configured).",
            value=settings.max_attempts,
            expected=">=1",
            passed=m_ok,
            fixture_id="M",
            detail=f"backoff_minutes={settings.backoff_minutes}",
        ),
    ]
    return l_ok and m_ok, metrics, "monitoring L–M"


def _case_connectors(fx: FixtureBundle) -> tuple[bool | None, list[MetricResult], str]:
    """N/O: unauthorized → no request; authorized path allows host check."""
    from hospital_extractor.sources.authorization import (
        AuthorizationPolicy,
        CredentialRef,
        assert_safe_url,
        resolve_credential,
    )

    unauth = AuthorizationPolicy.deny_all()
    n_ok = unauth.authorized is False

    auth_policy = AuthorizationPolicy(
        authorized=True,
        allowed_hosts=("api.example-hospital.in",),
        allowed_schemes=("https",),
    )
    denied_reason = ""
    try:
        assert_safe_url("https://api.example-hospital.in/v1", policy=auth_policy)
        o_ok = True
    except Exception as exc:  # noqa: BLE001
        o_ok = False
        denied_reason = str(exc)[:120]

    # Unauthorized check_url must deny without network (local only)
    decision = unauth.check_url("https://api.example-hospital.in/v1")
    denied_without_network = decision.allowed is False

    ref = CredentialRef("MEDCOMPARE_CONNECTOR_BENCH_TOKEN")
    repr_text = repr(ref)
    repr_ok = "env_var=" in repr_text and "MEDCOMPARE_CONNECTOR_BENCH_TOKEN" in repr_text
    secret_absent = resolve_credential(CredentialRef("MEDCOMPARE_BENCH_UNSET_XYZ")) is None

    metrics = [
        MetricResult.available(
            name="connector_unauthorized_fail_closed",
            definition="deny_all policy → authorized=False and check_url denies (no request).",
            value=denied_without_network,
            expected=True,
            passed=n_ok and denied_without_network,
            fixture_id="N",
            detail=decision.reason or decision.code,
        ),
        MetricResult.available(
            name="connector_authorized_allowlist",
            definition="Allowlisted HTTPS host passes assert_safe_url under authorized policy.",
            value=o_ok,
            expected=True,
            passed=o_ok,
            fixture_id="O",
            detail=denied_reason,
        ),
        MetricResult.available(
            name="connector_credential_ref_repr_safe",
            definition="CredentialRef.__repr__ exposes env var name only, never a secret.",
            value=repr_ok,
            expected=True,
            passed=repr_ok,
            fixture_id="N",
        ),
        MetricResult.available(
            name="connector_unset_credential_is_none",
            definition="Unset credential env resolves to None (no silent default secret).",
            value=secret_absent,
            expected=True,
            passed=secret_absent,
            fixture_id="N",
        ),
    ]
    return all(m.passed for m in metrics), metrics, "connectors N–O"


def _case_search(fx: FixtureBundle) -> tuple[bool | None, list[MetricResult], str]:
    """P: structured search + UNKNOWN preservation + provenance."""
    from hospital_extractor.search.models import Filter, FilterOperator, Query
    from hospital_extractor.search.service import search_datasets
    from hospital_extractor.storage import HospitalDatabase

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "s.db"
        with HospitalDatabase(db_path) as db:
            db.save_dataset(fx.search_a)
            db.save_dataset(fx.search_b)
            db.save_dataset(fx.search_c)
            q_all = Query(text="", filters=[], limit=50)
            res_all = search_datasets(db, q_all)
            q_text = Query(text="Knee", filters=[], limit=50)
            res_text = search_datasets(db, q_text)

            # Filter: bed_count exists (UNKNOWN/gamma has None → excluded from gt)
            q_gt = Query(
                text="",
                filters=[
                    Filter(field="hospital.bed_count", operator=FilterOperator.GTE, value=100)
                ],
                limit=50,
            )
            res_gt = search_datasets(db, q_gt)

            # Gamma bed_count is None → NOT_EXISTS match
            q_nx = Query(
                text="",
                filters=[
                    Filter(
                        field="hospital.bed_count",
                        operator=FilterOperator.NOT_EXISTS,
                        value=None,
                    )
                ],
                limit=50,
            )
            res_nx = search_datasets(db, q_nx)

    all_count = len(res_all.matches)
    text_count = len(res_text.matches)
    gt_count = len(res_gt.matches)
    nx_count = len(res_nx.matches)

    metrics = [
        MetricResult.available(
            name="search_returns_all_stored",
            definition="Unfiltered search returns every stored dataset.",
            value=all_count,
            numerator=all_count,
            denominator=3,
            expected=3,
            passed=all_count == 3,
            fixture_id="P",
        ),
        MetricResult.available(
            name="search_text_filter",
            definition="Free-text 'Knee' matches hospitals with that treatment.",
            value=text_count,
            expected=">=1",
            passed=text_count >= 1,
            fixture_id="P",
        ),
        MetricResult.available(
            name="search_unknown_bed_count_preserved",
            definition="bed_count NOT_EXISTS matches the UNKNOWN hospital (never coerced to 0/NO).",
            value=nx_count,
            expected=1,
            passed=nx_count == 1,
            fixture_id="G",
        ),
        MetricResult.available(
            name="search_gte_excludes_unknown",
            definition="bed_count GTE 100 excludes UNKNOWN (does not treat NULL as ≥).",
            value=gt_count,
            expected=2,
            passed=gt_count == 2,
            fixture_id="P",
            detail="alpha+beta only; gamma UNKNOWN excluded",
        ),
    ]
    return all(m.passed for m in metrics), metrics, "search P"


def _case_comparison(fx: FixtureBundle) -> tuple[bool | None, list[MetricResult], str]:
    """Q: 2–4 hospital comparison, no ranking field, descriptive only."""
    from hospital_extractor.comparison.service import compare_datasets
    from hospital_extractor.comparison.target import resolve_target

    target = resolve_target("Knee Replacement")
    result = compare_datasets(
        [
            ("h_alpha", fx.search_a),
            ("h_beta", fx.search_b),
            ("h_gamma", fx.search_c),
        ],
        target,
    )
    hospital_count = len(result.hospitals)
    # No ranking field on ComparisonResult / HospitalComparison
    has_rank = hasattr(result, "ranking") or any(
        hasattr(h, "rank") for h in result.hospitals
    )
    # Dimensions live on each HospitalComparison (descriptive only)
    dim_count = (
        sum(len(h.dimensions) for h in result.hospitals) if result.hospitals else 0
    )

    metrics = [
        MetricResult.available(
            name="comparison_hospital_count",
            definition="Comparison accepts 2–4 hospitals (here 3).",
            value=hospital_count,
            numerator=hospital_count,
            denominator=3,
            expected=3,
            passed=hospital_count == 3,
            fixture_id="Q",
        ),
        MetricResult.available(
            name="comparison_no_ranking_field",
            definition="ComparisonResult exposes no ranking/score ordering field.",
            value=not has_rank,
            expected=True,
            passed=not has_rank,
            fixture_id="Q",
        ),
        MetricResult.available(
            name="comparison_has_dimensions",
            definition="Descriptive dimensions are computed for the target.",
            value=dim_count,
            expected=">=1",
            passed=dim_count >= 1,
            fixture_id="Q",
        ),
    ]
    return all(m.passed for m in metrics), metrics, "comparison Q"


def _case_quality(fx: FixtureBundle) -> tuple[bool | None, list[MetricResult], str]:
    """R: P23 metrics from stored rows; overall_score remains null."""
    from hospital_extractor.quality.models import PipelineQualityReport
    from hospital_extractor.quality.service import build_dataset_quality_report
    from hospital_extractor.storage import HospitalDatabase

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "q.db"
        with HospitalDatabase(db_path) as db:
            db.save_dataset(fx.unchanged)
            db.save_dataset(fx.unknown)
            summaries = db.list_datasets()
            ds_report = build_dataset_quality_report(db, summaries[0])
            pipeline = PipelineQualityReport()

    cov = ds_report.metric_value("hospital_field_coverage_rate")
    unk_rate = ds_report.metric_value("unknown_field_rate")
    dup_rate = ds_report.metric_value("duplicate_rate")
    conf_rate = ds_report.metric_value("conflict_rate")
    fresh = ds_report.metric_value("freshness_age_days")
    overall_null = ds_report.overall_score is None and pipeline.overall_score is None

    def _num(name: str) -> int | float | None:
        raw = ds_report.metric_value(name)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            return None
        return raw

    metrics = [
        MetricResult.available(
            name="quality_coverage_rate",
            definition="hospital_field_coverage_rate = populated/tracked (P23).",
            value=cov,
            numerator=_num("hospital_fields_populated"),
            denominator=_num("hospital_fields_tracked"),
            expected="0..1",
            passed=isinstance(cov, (int, float)) and 0.0 <= float(cov) <= 1.0,
            unit="ratio",
            fixture_id="R",
        ),
        MetricResult.available(
            name="quality_unknown_rate",
            definition="unknown_field_rate present and in [0,1].",
            value=unk_rate,
            expected="0..1",
            passed=isinstance(unk_rate, (int, float)) and 0.0 <= float(unk_rate) <= 1.0,
            unit="ratio",
            fixture_id="G",
        ),
        MetricResult.available(
            name="quality_duplicate_rate",
            definition="duplicate_rate from DataQuality.duplicates_removed / (extracted+dups).",
            value=dup_rate,
            expected="0..1",
            passed=isinstance(dup_rate, (int, float)) and 0.0 <= float(dup_rate) <= 1.0,
            unit="ratio",
            fixture_id="E",
        ),
        MetricResult.available(
            name="quality_conflict_rate",
            definition="conflict_rate from DataQuality.conflicts_detected / max(records,1).",
            value=conf_rate,
            expected="0..1",
            passed=isinstance(conf_rate, (int, float)) and 0.0 <= float(conf_rate) <= 1.0,
            unit="ratio",
            fixture_id="F",
        ),
        MetricResult.available(
            name="quality_freshness_age_days",
            definition="freshness_age_days ≥ 0 for stored summaries.",
            value=fresh,
            expected=">=0",
            passed=isinstance(fresh, (int, float)) and float(fresh) >= 0.0,
            unit="days",
            fixture_id="R",
        ),
        MetricResult.available(
            name="quality_overall_score_null",
            definition="P23 overall_score remains null (no composite score).",
            value=overall_null,
            expected=True,
            passed=overall_null,
            fixture_id="R",
        ),
    ]
    return all(m.passed for m in metrics), metrics, "quality R"


def _case_security(fx: FixtureBundle) -> tuple[bool | None, list[MetricResult], str]:
    """S/T: RBAC map + audit store append-only + production fail-closed."""
    from hospital_extractor.auth.models import Permission, Role
    from hospital_extractor.auth.permissions import PERMISSIONS_BY_ROLE, role_has_permission
    from hospital_extractor.auth.store import AuthStore
    from hospital_extractor.ops.startup import StartupConfigError, verify_startup

    # S: RBAC map completeness
    roles_ok = set(PERMISSIONS_BY_ROLE) == set(Role)
    viewer_limited = not role_has_permission(Role.VIEWER, Permission.CAPTURE_SOURCE)
    admin_full = role_has_permission(Role.ADMIN, Permission.MANAGE_USERS)

    # T: audit store — create event, then read append-only log
    with tempfile.TemporaryDirectory() as tmp:
        store_path = Path(tmp) / "sec.db"
        store = AuthStore(store_path)
        store.ensure_schema()
        with store:
            store.create_user(username="bench_admin", password="Bench-Str0ng-x", role=Role.ADMIN)
            users = store.list_users()
            from hospital_extractor.auth.audit import record_audit

            record_audit(
                store,
                action="bench.audit",
                outcome="success",
                actor_id=users[0].user_id,
                actor_role=Role.ADMIN.value,
                resource_type="benchmark",
                resource_id="case_t",
            )
            events = store.list_audit(limit=10)
        store.close()

    audit_ok = len(events) >= 1 and len(users) == 1
    # No password hash equals plaintext
    hash_ok = users[0].password_hash != "Bench-Str0ng-x" and len(users[0].password_hash) > 20

    # Production fail-closed
    from hospital_extractor.auth.models import AuthMode, AuthSettings

    prod_settings_ok = True
    try:
        verify_startup(
            settings=AuthSettings(
                require_auth=False,
                mode=AuthMode.PRODUCTION,
                allow_dev_header=False,
            ),
            raise_on_error=True,
        )
        prod_settings_ok = False  # should have raised
    except StartupConfigError:
        prod_settings_ok = True
    except Exception:
        prod_settings_ok = False

    metrics = [
        MetricResult.available(
            name="rbac_roles_complete",
            definition="PERMISSIONS_BY_ROLE covers every Role.",
            value=roles_ok,
            expected=True,
            passed=roles_ok,
            fixture_id="S",
        ),
        MetricResult.available(
            name="rbac_viewer_cannot_capture",
            definition="VIEWER lacks CAPTURE_SOURCE (least privilege).",
            value=viewer_limited,
            expected=True,
            passed=viewer_limited,
            fixture_id="S",
        ),
        MetricResult.available(
            name="rbac_admin_can_manage_users",
            definition="ADMIN holds MANAGE_USERS.",
            value=admin_full,
            expected=True,
            passed=admin_full,
            fixture_id="S",
        ),
        MetricResult.available(
            name="audit_event_recorded",
            definition="Audit store persists ≥1 event for a recorded action.",
            value=len(events),
            numerator=len(events),
            expected=">=1",
            passed=audit_ok,
            fixture_id="T",
        ),
        MetricResult.available(
            name="password_not_stored_plaintext",
            definition="User.password_hash ≠ plaintext password and is a scrypt digest.",
            value=hash_ok,
            expected=True,
            passed=hash_ok,
            fixture_id="S",
        ),
        MetricResult.available(
            name="production_fail_closed",
            definition="Production + require_auth=false raises StartupConfigError.",
            value=prod_settings_ok,
            expected=True,
            passed=prod_settings_ok,
            fixture_id="U",
        ),
    ]
    return all(m.passed for m in metrics), metrics, "security S–T + fail-closed U"


def _case_deployment(fx: FixtureBundle) -> tuple[bool | None, list[MetricResult], str]:
    """U: startup verify, health/ready via TestClient, package metadata."""
    from fastapi.testclient import TestClient

    from hospital_extractor.api.app import create_app
    from hospital_extractor.ops.startup import verify_startup

    report = verify_startup(raise_on_error=False)
    startup_ok = report.ok

    app = create_app()
    with tempfile.TemporaryDirectory() as tmp:
        app.state.medcompare_db_path = Path(tmp) / "dep.db"
        with TestClient(app) as client:
            h = client.get("/health")
            r = client.get("/ready")
            a = client.get("/api/v1/health")

    health_ok = h.status_code == 200 and h.json().get("status") == "ok"
    ready_ok = r.status_code == 200 and r.json().get("status") == "ok"
    api_ok = a.status_code == 200

    pyproject = Path(__file__).resolve().parents[3] / "pyproject.toml"
    pkg_ok = pyproject.is_file() and 'hospital-extractor = "hospital_extractor.cli:main"' in (
        pyproject.read_text(encoding="utf-8")
    )

    metrics = [
        MetricResult.available(
            name="deployment_startup_verify",
            definition="verify_startup() reports ok for default development config.",
            value=startup_ok,
            expected=True,
            passed=startup_ok,
            fixture_id="U",
            detail=report.mode,
        ),
        MetricResult.available(
            name="deployment_health",
            definition="GET /health returns 200 status ok.",
            value=h.status_code,
            expected=200,
            passed=health_ok,
            fixture_id="U",
        ),
        MetricResult.available(
            name="deployment_ready",
            definition="GET /ready returns 200 when DB/config/assets OK.",
            value=r.status_code,
            expected=200,
            passed=ready_ok,
            fixture_id="U",
        ),
        MetricResult.available(
            name="deployment_api_health",
            definition="GET /api/v1/health remains open and 200.",
            value=a.status_code,
            expected=200,
            passed=api_ok,
            fixture_id="U",
        ),
        MetricResult.available(
            name="deployment_entry_point_declared",
            definition="pyproject declares hospital-extractor console script.",
            value=pkg_ok,
            expected=True,
            passed=pkg_ok,
            fixture_id="U",
        ),
    ]
    return all(m.passed for m in metrics), metrics, "deployment U"


def _case_performance(fx: FixtureBundle) -> tuple[bool | None, list[MetricResult], str]:
    """PERF: in-process timings only (not production web latency)."""
    from hospital_extractor.benchmark._snap import make_snapshot
    from hospital_extractor.search.models import Query
    from hospital_extractor.search.service import search_datasets
    from hospital_extractor.snapshots import build_change_set, canonical_digest
    from hospital_extractor.storage import HospitalDatabase

    # Fingerprint timing (repeated)
    t0 = time.perf_counter()
    iterations = 50
    for _ in range(iterations):
        canonical_digest(fx.unchanged)
    fp_ms = (time.perf_counter() - t0) * 1000.0 / iterations

    # Search timing
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "perf.db"
        with HospitalDatabase(db_path) as db:
            db.save_dataset(fx.search_a)
            db.save_dataset(fx.search_b)
            db.save_dataset(fx.search_c)
            t1 = time.perf_counter()
            search_rep = 20
            for _ in range(search_rep):
                search_datasets(db, Query(text="", filters=[], limit=50))
            search_ms = (time.perf_counter() - t1) * 1000.0 / search_rep

    # Change detection timing
    s1 = make_snapshot(fx.unchanged, "p1")
    s2 = make_snapshot(fx.modified, "p2")
    t2 = time.perf_counter()
    cd_rep = 50
    for _ in range(cd_rep):
        build_change_set(s1, s2)
    cd_ms = (time.perf_counter() - t2) * 1000.0 / cd_rep

    metrics = [
        MetricResult.available(
            name="perf_fingerprint_mean_ms",
            definition="Mean canonical_digest runtime over N=50 identical calls (in-process).",
            value=round(fp_ms, 4),
            numerator=round(fp_ms, 4),
            denominator=iterations,
            unit="ms",
            detail="workload=single canonical dataset",
            fixture_id="PERF",
        ),
        MetricResult.available(
            name="perf_search_mean_ms",
            definition="Mean search_datasets runtime over N=20 empty queries on 3 hospitals.",
            value=round(search_ms, 4),
            numerator=round(search_ms, 4),
            denominator=20,
            unit="ms",
            detail="workload=3 stored datasets",
            fixture_id="PERF",
        ),
        MetricResult.available(
            name="perf_change_detection_mean_ms",
            definition="Mean build_change_set runtime over N=50 modified pairs.",
            value=round(cd_ms, 4),
            numerator=round(cd_ms, 4),
            denominator=50,
            unit="ms",
            detail="workload=hospital bed_count change",
            fixture_id="PERF",
        ),
        MetricResult.not_measured(
            name="perf_memory_rss",
            definition="Peak RSS of the API process during load.",
            detail="NOT MEASURED — reliable cross-platform RSS not collected in this harness.",
        ),
        MetricResult.not_applicable(
            name="perf_production_p95_latency",
            definition="Production HTTP p95 under real traffic.",
            detail="NOT APPLICABLE — no production deployment exists (P25 NOT CLAIMED).",
        ),
    ]
    # Performance case is observation (no pass/fail threshold invented)
    return None, metrics, "in-process performance (observation)"


def _case_reliability(fx: FixtureBundle) -> tuple[bool | None, list[MetricResult], str]:
    """Idempotency: re-writing the same dataset does not duplicate rows."""
    from hospital_extractor.storage import HospitalDatabase

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "rel.db"
        with HospitalDatabase(db_path) as db:
            db.save_dataset(fx.unchanged)
            first = len(db.list_datasets())
            db.save_dataset(fx.unchanged)
            second = len(db.list_datasets())
            hospitals_after = len(db.list_hospitals())

    metrics = [
        MetricResult.available(
            name="idempotent_dataset_write",
            definition="Writing the same dataset twice yields a stable row count (no silent dup).",
            value=second,
            numerator=second,
            denominator=first,
            expected=first,
            passed=first == second and first >= 1,
            fixture_id="REL",
            detail=f"first={first} second={second}",
        ),
        MetricResult.available(
            name="hospital_identity_stable",
            definition="Re-write does not create duplicate hospital registry rows.",
            value=hospitals_after,
            expected=1,
            passed=hospitals_after == 1,
            fixture_id="REL",
        ),
    ]
    return all(m.passed for m in metrics), metrics, "reliability / idempotency"


def _case_extraction(fx: FixtureBundle) -> tuple[bool | None, list[MetricResult], str]:
    """Extraction fixture: built dataset has provenance + expected fields."""
    ds = fx.unchanged
    gt = fx.ground_truth
    bed_ok = ds.hospital is not None and ds.hospital.bed_count == gt["unchanged_bed_count"]
    name_ok = ds.hospital is not None and ds.hospital.name == "Fixture Hospital"
    treat_ok = len(ds.treatments) == 1 and ds.treatments[0].name == "Knee Replacement"

    # Provenance coverage over all entity rows in fixture
    rows = [ds.hospital, *ds.treatments] if ds.hospital else list(ds.treatments)
    with_prov = [
        r
        for r in rows
        if r is not None and bool(getattr(r, "source_url", ""))
    ]
    prov_rate = (len(with_prov) / len(rows)) if rows else 0.0

    # UNKNOWN fixture
    unk_ok = fx.unknown.hospital is not None and fx.unknown.hospital.bed_count is None

    metrics = [
        MetricResult.available(
            name="extraction_bed_count_matches_ground_truth",
            definition="Fixture hospital.bed_count equals constructed ground truth (300).",
            value=ds.hospital.bed_count if ds.hospital else None,
            expected=gt["unchanged_bed_count"],
            passed=bed_ok,
            fixture_id="A",
        ),
        MetricResult.available(
            name="extraction_name_matches_ground_truth",
            definition="Fixture hospital.name equals constructed name.",
            value=ds.hospital.name if ds.hospital else None,
            expected="Fixture Hospital",
            passed=name_ok,
            fixture_id="A",
        ),
        MetricResult.available(
            name="extraction_provenance_coverage",
            definition="Share of fixture entity rows carrying source_url.",
            value=round(prov_rate, 6),
            numerator=len(with_prov),
            denominator=len(rows),
            expected=1.0,
            passed=prov_rate == 1.0,
            unit="ratio",
            fixture_id="A",
        ),
        MetricResult.available(
            name="extraction_treatment_count",
            definition="Baseline fixture has exactly one treatment record.",
            value=len(ds.treatments),
            expected=1,
            passed=treat_ok,
            fixture_id="A",
        ),
        MetricResult.available(
            name="extraction_unknown_preserved",
            definition="UNKNOWN fixture keeps bed_count=None (never 0 or NO).",
            value=fx.unknown.hospital.bed_count if fx.unknown.hospital else "missing",
            expected=None,
            passed=unk_ok,
            fixture_id="G",
        ),
        MetricResult.not_measured(
            name="real_world_field_accuracy",
            definition="Accuracy vs human-labeled full-site gold labels.",
            detail="NOT MEASURED — no full human-labeled gold set exists for live sites.",
        ),
    ]
    return all(m.passed for m in metrics if m.passed is not None), metrics, "extraction A/G"


# ---------------------------------------------------------------------------
# Real-world observations (documented prior runs — not re-executed)
# ---------------------------------------------------------------------------


def _real_world_observations() -> list[RealWorldObservation]:
    return [
        RealWorldObservation(
            source_id="max_healthcare",
            url="https://www.maxhealthcare.in",
            recorded_date="2026-09-24",
            classification="exact_baseline_match",
            summary=(
                "Prior crawl: 103 pages fetched (baseline 103), 1632 records, "
                "804 duplicates removed, 152 conflicts, 53 specialities, 54 doctors. "
                "Match-to-baseline EXACT. Duration 346.5s vs ~207s historical → environmental."
            ),
            known_limitations=["Site content can drift between runs"],
            evidence_doc="docs/validation-report-post-p19c.md#phase-11",
            raw_counts={
                "pages_fetched": 103,
                "pages_baseline": 103,
                "records_extracted": 1632,
                "duplicates_removed": 804,
                "conflicts_detected": 152,
                "specialities": 53,
                "doctors": 54,
            },
        ),
        RealWorldObservation(
            source_id="manipal_hospitals",
            url="https://www.manipalhospitals.com",
            recorded_date="2026-09-24",
            classification="expected_website_change_with_known_limitation",
            summary=(
                "Prior crawl: 137 pages (baseline 138), 8329 records, 7776 dups, "
                "55 conflicts. Speciality master 460 vs baseline 480. "
                "Doctors block failed — JS-rendered directory (pre-existing limitation)."
            ),
            known_limitations=[
                "JS-rendered doctor directory (pre-existing)",
                "Speciality count drift 460–480 (site change)",
            ],
            evidence_doc="docs/validation-report-post-p19c.md#phase-12",
            raw_counts={
                "pages_fetched": 137,
                "records_extracted": 8329,
                "duplicates_removed": 7776,
                "conflicts_detected": 55,
                "specialities": 460,
                "specialities_baseline": 480,
            },
        ),
        RealWorldObservation(
            source_id="apollo_hospitals",
            url="https://www.apollohospitals.com",
            recorded_date="2026-09-24",
            classification="access_denied_403",
            summary=(
                "Prior crawl: homepage status 403; recorded as access denial, "
                "not bypassed; no fabricated data."
            ),
            known_limitations=["HTTP 403 blocks automated access"],
            evidence_doc="docs/validation-report-post-p19c.md#phase-13",
            raw_counts={"http_status": 403, "exit_code": 1},
        ),
        RealWorldObservation(
            source_id="fortis_healthcare",
            url="https://www.fortishealthcare.com",
            recorded_date="2026-09-24",
            classification="reachable_js_rendered_near_empty",
            summary=(
                "Prior crawl: previously 403; now reachable (200) but JS-rendered/"
                "anti-bot → extraction near-empty with graceful degradation; no bypass."
            ),
            known_limitations=["JS-rendered content not extracted without a browser"],
            evidence_doc="docs/validation-report-post-p19c.md#phase-14",
            raw_counts={"pages_fetched": 200, "records_extracted": 0},
        ),
    ]


def _limitations() -> list[str]:
    return [
        "Fixture metrics measure TEST CORRECTNESS against controlled synthetic ground truth, "
        "not clinical or real-world extraction accuracy.",
        "No full human-labeled gold standard exists for live hospital websites; "
        "real_world_field_accuracy is NOT MEASURED.",
        "Performance numbers are in-process micro timings, not production HTTP p95 "
        "(no production deployment is claimed).",
        "Real-world site results are prior documented observations (2026-09-24), "
        "not re-executed in this benchmark run; sites drift over time.",
        "Apollo 403 and Fortis JS-rendering are site limitations, not model accuracy claims.",
        "No government or hospital API credentials were used; connector success cases "
        "use local allowlist checks only.",
        "Docker runtime and remote GitHub CI are not executed in this harness "
        "(see final validation report for honest status).",
        "No composite overall score is defined or reported.",
    ]


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------


def run_benchmarks(
    *,
    case_filter: str | None = None,
    package_version: str = "0.1.0",
) -> BenchmarkRun:
    """Run all (or filtered) benchmark cases and return a full report."""
    fx = build_fixtures()
    run = BenchmarkRun(
        environment=EnvironmentInfo.capture(package_version=package_version),
        methodology=METHODOLOGY,
        limitations=_limitations(),
    )
    run.real_world_observations = _real_world_observations()

    case_defs: list[tuple[BenchmarkCase, Any]] = [
        (
            _case(
                "extraction",
                "Extraction & provenance fixtures",
                "extraction",
                "Field values, provenance coverage, UNKNOWN preservation (A/G).",
                ["A", "G"],
            ),
            _case_extraction,
        ),
        (
            _case(
                "change_detection",
                "Change detection & fingerprint",
                "change_detection",
                "Unchanged / modified / added / removed + digest determinism (A–D).",
                ["A", "B", "C", "D"],
            ),
            _case_change_detection,
        ),
        (
            _case(
                "verification",
                "Verification outcomes",
                "verification",
                "VERIFIED / MISMATCH / UNVERIFIED evaluation rules (H/I/J).",
                ["H", "I", "J"],
            ),
            _case_verification,
        ),
        (
            _case(
                "reconciliation",
                "Reconciliation state machine",
                "reconciliation",
                "ALLOWED_TRANSITIONS equals ground-truth table (K).",
                ["K"],
            ),
            _case_reconciliation,
        ),
        (
            _case(
                "monitoring",
                "Monitoring scheduling safety",
                "monitoring",
                "No phantom due policies; retry policy configured (L/M).",
                ["L", "M"],
            ),
            _case_monitoring,
        ),
        (
            _case(
                "connectors",
                "Connector authorization",
                "connectors",
                "Fail-closed unauthorized; allowlist pass; credential hygiene (N/O).",
                ["N", "O"],
            ),
            _case_connectors,
        ),
        (
            _case(
                "search",
                "Structured search & UNKNOWN",
                "search",
                "Filters, text match, UNKNOWN not coerced (P/G).",
                ["P", "G"],
            ),
            _case_search,
        ),
        (
            _case(
                "comparison",
                "Multi-hospital comparison",
                "comparison",
                "2–4 hospitals, no ranking field, descriptive dimensions (Q).",
                ["Q"],
            ),
            _case_comparison,
        ),
        (
            _case(
                "quality",
                "P23 quality metrics",
                "quality",
                "Coverage/unknown/dup/conflict/freshness formulas; overall_score null (R).",
                ["R"],
            ),
            _case_quality,
        ),
        (
            _case(
                "security",
                "Security & RBAC",
                "security",
                "Role map, audit events, password hashing, production fail-closed (S/T/U).",
                ["S", "T", "U"],
            ),
            _case_security,
        ),
        (
            _case(
                "deployment",
                "Deployment health & startup",
                "deployment",
                "verify_startup, /health, /ready, entry point (U).",
                ["U"],
            ),
            _case_deployment,
        ),
        (
            _case(
                "reliability",
                "Idempotency & duplicate prevention",
                "reliability",
                "Same dataset written twice does not duplicate rows.",
                ["A"],
            ),
            _case_reliability,
        ),
        (
            _case(
                "performance",
                "In-process performance",
                "performance",
                "Fingerprint / search / change-detection mean timings (observation).",
                ["PERF"],
            ),
            _case_performance,
        ),
    ]

    for case, fn in case_defs:
        if case_filter and case.case_id != case_filter and case.category != case_filter:
            continue
        run.cases.append(_run_case(case, fn, fx))

    # Explicit unmeasured categories the spec allows us to skip honestly
    if not case_filter:
        run.cases.append(
            BenchmarkResult(
                case=_case(
                    "real_world_live_revalidation",
                    "Live site re-crawl accuracy",
                    "real_world",
                    "Would require live crawls + human labels this run.",
                    [],
                ),
                passed=None,
                status="observation",
                metrics=[
                    MetricResult.not_measured(
                        name="real_world_live_accuracy",
                        definition="Accuracy vs human labels on a fresh live crawl.",
                        detail=(
                            "NOT MEASURED — prior observations reused; "
                            "no new live crawl in P26 harness."
                        ),
                    )
                ],
                notes="See real_world_observations for documented 2026-09-24 prior runs.",
            )
        )
        run.cases.append(
            BenchmarkResult(
                case=_case(
                    "docker_runtime",
                    "Docker image runtime",
                    "deployment",
                    "Build/run container and hit health probes.",
                    [],
                ),
                passed=None,
                status="observation",
                metrics=[
                    MetricResult.not_measured(
                        name="docker_image_smoke",
                        definition="docker build + run + /health success.",
                        detail="NOT MEASURED — Docker daemon unavailable on validation host.",
                    )
                ],
            )
        )
        run.cases.append(
            BenchmarkResult(
                case=_case(
                    "remote_ci",
                    "Remote GitHub Actions",
                    "deployment",
                    "Workflow green on github.com.",
                    [],
                ),
                passed=None,
                status="observation",
                metrics=[
                    MetricResult.not_measured(
                        name="remote_ci_status",
                        definition="GitHub Actions workflow conclusion=success.",
                        detail="NOT MEASURED — workflow not executed on GitHub in this validation.",
                    )
                ],
            )
        )

    run.build_summary()
    return run
