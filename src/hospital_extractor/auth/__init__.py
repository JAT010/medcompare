"""Authentication, RBAC, and audit primitives (P24).

Two independent authorization layers:

- **P24 user authorization** (this package) — may *this actor* call the API?
- **P21 source authorization** (``sources.authorization``) — may *this app*
  contact that external source?  Fail-closed; never bypassed by ADMIN.

Development/test authentication is explicit and labeled; production expects
env-configured bootstrap credentials and never hard-coded passwords.
"""

from __future__ import annotations

from hospital_extractor.auth.audit import record_audit
from hospital_extractor.auth.dependencies import (
    current_principal,
    optional_principal,
    require_any_permission,
    require_hospital_access,
    require_permission,
)
from hospital_extractor.auth.hashing import hash_password, verify_password
from hospital_extractor.auth.models import (
    AuditEvent,
    AuthMode,
    AuthSettings,
    LoginRequest,
    LoginResponse,
    Permission,
    Principal,
    Role,
    TokenRecord,
    User,
    UserCreate,
    UserPublic,
    UserRoleUpdate,
)
from hospital_extractor.auth.permissions import (
    PERMISSIONS_BY_ROLE,
    role_has_permission,
)
from hospital_extractor.auth.service import (
    AuthService,
    AuthServiceError,
    authenticate,
    create_user,
    get_auth_service,
    list_users,
)
from hospital_extractor.auth.store import AuthStore

__all__ = [
    "PERMISSIONS_BY_ROLE",
    "AuditEvent",
    "AuthMode",
    "AuthSettings",
    "AuthService",
    "AuthServiceError",
    "AuthStore",
    "LoginRequest",
    "LoginResponse",
    "Permission",
    "Principal",
    "Role",
    "TokenRecord",
    "User",
    "UserCreate",
    "UserPublic",
    "UserRoleUpdate",
    "authenticate",
    "create_user",
    "current_principal",
    "get_auth_service",
    "hash_password",
    "list_users",
    "optional_principal",
    "record_audit",
    "require_any_permission",
    "require_hospital_access",
    "require_permission",
    "role_has_permission",
    "verify_password",
]
