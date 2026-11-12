from __future__ import annotations

import os
import re
from dataclasses import dataclass
from enum import Enum

from argon2 import PasswordHasher, Type as Argon2Type
from argon2.exceptions import InvalidHash, VerificationError, VerifyMismatchError

from .i18n import normalize_language, tr

ROLE_ADMIN = "ADMIN"
ROLE_CASHIER = "CASHIER"
VALID_ROLES = {ROLE_ADMIN, ROLE_CASHIER}

USERNAME_MIN_LEN = 3
USERNAME_MAX_LEN = 32
PASSWORD_MIN_LEN = 8
PASSWORD_MAX_LEN = 128

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")

_PASSWORD_HASHER = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
    hash_len=32,
    salt_len=16,
    type=Argon2Type.ID,
)

# Generated lazily once per process from OS entropy so unknown usernames perform
# real Argon2id work without embedding any hash constant in source code.
_RUNTIME_DUMMY_HASH: str | None = None


class Permission(str, Enum):
    ACCESS_REGISTER = "access_register"
    CREATE_ORDER = "create_order"
    VIEW_OWN_SALES = "view_own_sales"
    VIEW_ALL_SALES = "view_all_sales"
    MANAGE_PRODUCTS = "manage_products"
    MANAGE_CATEGORIES = "manage_categories"
    VIEW_REPORTS = "view_reports"
    MANAGE_USERS = "manage_users"
    MANAGE_SETTINGS = "manage_settings"
    MANAGE_BACKUPS = "manage_backups"
    RESTORE_BACKUP = "restore_backup"
    FACTORY_RESET = "factory_reset"
    MANAGE_OWN_SHIFT = "manage_own_shift"
    VIEW_ALL_SHIFTS = "view_all_shifts"
    MANAGE_INVENTORY = "manage_inventory"
    MANAGE_EXPENSES = "manage_expenses"
    PROCESS_REFUND = "process_refund"


ROLE_PERMISSIONS: dict[str, frozenset[Permission]] = {
    ROLE_CASHIER: frozenset(
        {
            Permission.ACCESS_REGISTER,
            Permission.CREATE_ORDER,
            Permission.VIEW_OWN_SALES,
            Permission.MANAGE_OWN_SHIFT,
        }
    ),
    ROLE_ADMIN: frozenset(Permission),
}


class AuthorizationError(PermissionError):
    """Raised when a user lacks permission or an authenticated session is missing."""


class AuthenticationError(ValueError):
    """Raised when credentials fail or an account is locked/disabled."""

    def __init__(self, message: str, *, locked_seconds: int = 0) -> None:
        super().__init__(message)
        self.locked_seconds = int(locked_seconds)


@dataclass(frozen=True)
class UserAccount:
    """Public session/account DTO (intentionally excludes password hashes)."""

    id: int
    username: str
    display_name: str
    role: str
    is_active: bool
    created_at: str
    updated_at: str
    last_login_at: str = ""
    preferred_language: str = ""
    font_scale_pct: int = 100
    ui_density: str = "comfortable"
    reduced_motion_pref: int = -1

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    @property
    def label(self) -> str:
        return self.display_name.strip() or self.username

    def effective_reduced_motion(self, store_default: bool) -> bool:
        if self.reduced_motion_pref == 1:
            return True
        if self.reduced_motion_pref == 0:
            return False
        return bool(store_default)


@dataclass(frozen=True)
class AuditEvent:
    id: int
    created_at: str
    actor_user_id: int | None
    actor_username: str
    actor_role: str
    action: str
    target_type: str
    target_id: str
    details: str


def validate_username(username: str) -> str:
    cleaned = (username or "").strip()
    if not cleaned:
        raise ValueError(tr("backend.err.username_empty"))
    if not _USERNAME_RE.match(cleaned):
        raise ValueError(tr("backend.err.username_chars"))
    return cleaned


def validate_display_name(display_name: str, fallback_username: str = "") -> str:
    cleaned = " ".join((display_name or "").strip().split())[:60]
    if cleaned:
        return cleaned
    return (fallback_username or "").strip()[:60]


def validate_password_policy(password: str) -> str:
    if password is None or not isinstance(password, str) or not password:
        raise ValueError(tr("backend.err.password_required"))
    if not password.strip():
        raise ValueError(tr("backend.err.password_whitespace"))
    if len(password) < PASSWORD_MIN_LEN:
        raise ValueError(tr("backend.err.password_length", min=PASSWORD_MIN_LEN))
    if len(password) > PASSWORD_MAX_LEN:
        raise ValueError(tr("backend.err.password_max", max=PASSWORD_MAX_LEN))
    return password


def validate_role(role: str) -> str:
    upper = (role or "").strip().upper()
    if upper not in VALID_ROLES:
        raise ValueError(tr("backend.err.invalid_role"))
    return upper


def validate_preferred_language(lang: str | None) -> str:
    return normalize_language(lang, allow_empty=True, allow_follow=False)


def hash_password(password: str) -> str:
    valid_pw = validate_password_policy(password)
    return _PASSWORD_HASHER.hash(valid_pw)


def verify_password(stored_hash: str, password: str) -> bool:
    if not stored_hash or not password:
        return False
    try:
        return bool(_PASSWORD_HASHER.verify(stored_hash, password))
    except (VerifyMismatchError, VerificationError, InvalidHash, ValueError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    if not stored_hash:
        return False
    try:
        return bool(_PASSWORD_HASHER.check_needs_rehash(stored_hash))
    except Exception:
        return True


def dummy_verify(password: str) -> None:
    """Spend comparable Argon2id verification time when username does not exist."""
    global _RUNTIME_DUMMY_HASH
    if _RUNTIME_DUMMY_HASH is None:
        _RUNTIME_DUMMY_HASH = _PASSWORD_HASHER.hash(os.urandom(24).hex())
    verify_password(_RUNTIME_DUMMY_HASH, password or "dummy")


def throttle_duration_seconds(failed_attempts: int) -> int:
    if failed_attempts >= 12:
        return 180
    if failed_attempts >= 8:
        return 60
    if failed_attempts >= 5:
        return 30
    return 0


def has_permission(user: UserAccount | None, permission: Permission) -> bool:
    if user is None or not user.is_active:
        return False
    return permission in ROLE_PERMISSIONS.get(user.role, frozenset())


def require_permission(
    user: UserAccount | None, permission: Permission, message: str = ""
) -> UserAccount:
    if user is None:
        raise AuthorizationError(message or tr("backend.err.auth_required"))
    if not user.is_active:
        raise AuthorizationError(tr("backend.err.account_disabled_session"))
    if not has_permission(user, permission):
        raise AuthorizationError(message or tr("backend.err.admin_required"))
    return user

