from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

from . import __app_name__, __version__

APP_NAME = __app_name__
APP_DISPLAY_NAME = "Café POS"
APP_VERSION = __version__

DEVELOPER_NAME = "Mohamed Tamer"
GITHUB_OWNER = "MohameddTamerr"
GITHUB_REPOSITORY = "MohameddTamerr/POS-Systems-Collection"
GITHUB_REPO_URL = "https://github.com/MohameddTamerr/POS-Systems-Collection"
GITHUB_ISSUES_URL = "https://github.com/MohameddTamerr/POS-Systems-Collection/issues"

LICENSE_NAME = "MIT License"
LICENSE_COPYRIGHT = "Copyright (c) 2026 Mohamed Tamer"

# Optional direct developer contact metadata.
# When left blank, "Contact Developer" safely falls back to the repository Issues page.
DEVELOPER_CONTACT_URL = ""
DEVELOPER_CONTACT_EMAIL = ""


def get_feature_request_url() -> str:
    return GITHUB_ISSUES_URL


def get_bug_report_url() -> str:
    return GITHUB_ISSUES_URL


def has_direct_contact(
    *,
    contact_url: str | None = None,
    contact_email: str | None = None,
) -> bool:
    url = (DEVELOPER_CONTACT_URL if contact_url is None else contact_url).strip()
    email = (DEVELOPER_CONTACT_EMAIL if contact_email is None else contact_email).strip()
    return bool(url or email)


def get_contact_developer_url(
    *,
    contact_url: str | None = None,
    contact_email: str | None = None,
) -> str:
    url = (DEVELOPER_CONTACT_URL if contact_url is None else contact_url).strip()
    if url:
        return url
    email = (DEVELOPER_CONTACT_EMAIL if contact_email is None else contact_email).strip()
    if email:
        return email if email.lower().startswith("mailto:") else f"mailto:{email}"
    return GITHUB_ISSUES_URL


def open_external_url(url: str) -> bool:
    cleaned = (url or "").strip()
    if not cleaned:
        return False
    parsed = QUrl(cleaned)
    if not parsed.isValid() or parsed.scheme().lower() not in {"https", "http", "mailto"}:
        return False
    return bool(QDesktopServices.openUrl(parsed))

