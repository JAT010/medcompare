"""Role → permission map (P24). Fail closed for unknown roles/permissions."""

from __future__ import annotations

from hospital_extractor.auth.models import Permission, Role

__all__ = ["PERMISSIONS_BY_ROLE", "role_has_permission"]

# Documented role capabilities:
# ADMIN       — full platform control (users, config, sources, submit, all data)
# OPERATOR    — capture/monitor authorized sources, generate reports, verify
# REVIEWER    — verification + reconciliation review, generate reports, view
# VIEWER      — read-only hospital + quality data
PERMISSIONS_BY_ROLE: dict[Role, frozenset[Permission]] = {
    Role.ADMIN: frozenset(Permission),
    Role.OPERATOR: frozenset(
        {
            Permission.VIEW_HOSPITAL,
            Permission.VIEW_QUALITY,
            Permission.CAPTURE_SOURCE,
            Permission.MANAGE_MONITORING,
            Permission.VERIFY_CHANGE,
            Permission.GENERATE_REPORT,
        }
    ),
    Role.REVIEWER: frozenset(
        {
            Permission.VIEW_HOSPITAL,
            Permission.VIEW_QUALITY,
            Permission.VERIFY_CHANGE,
            Permission.REVIEW_RECONCILIATION,
            Permission.GENERATE_REPORT,
        }
    ),
    Role.VIEWER: frozenset(
        {
            Permission.VIEW_HOSPITAL,
            Permission.VIEW_QUALITY,
        }
    ),
}


def role_has_permission(role: Role, permission: Permission) -> bool:
    """True when *role* is a known role that includes *permission*."""
    allowed = PERMISSIONS_BY_ROLE.get(role)
    if allowed is None:
        return False
    return permission in allowed
