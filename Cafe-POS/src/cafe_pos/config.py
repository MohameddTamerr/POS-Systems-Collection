from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .money import CURRENCIES

DEFAULT_ACCENT_COLOR = "#1B4332"
DEFAULT_BUSINESS_NAME = "Roast & Ember Café"
DEFAULT_CURRENCY = "USD"
MAX_LOGO_BYTES = 2 * 1024 * 1024  # 2 MB cap for custom business logos
SUPPORTED_LOGO_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".ico"}

THEME_PRESETS: list[tuple[str, str]] = [
    ("Forest Green", "#1B4332"),
    ("Espresso Brown", "#4A2C2A"),
    ("Slate Navy", "#1E3A5F"),
    ("Warm Charcoal", "#2E3236"),
    ("Deep Burgundy", "#6B2737"),
    ("Olive Sage", "#3A5A40"),
]

_HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


@dataclass
class BusinessConfig:
    business_name: str = DEFAULT_BUSINESS_NAME
    accent_color: str = DEFAULT_ACCENT_COLOR
    currency: str = DEFAULT_CURRENCY
    address: str = ""
    phone: str = ""
    logo_blob: bytes = b""
    logo_ext: str = ""
    setup_completed: bool = False


def validate_hex_color(value: str) -> str:
    cleaned = (value or "").strip()
    if _HEX_COLOR_RE.match(cleaned):
        return cleaned.upper()
    return DEFAULT_ACCENT_COLOR


def validate_currency(code: str) -> str:
    upper = (code or "").strip().upper()
    return upper if upper in CURRENCIES else DEFAULT_CURRENCY


def read_and_validate_logo(file_path: Path) -> tuple[bytes, str]:
    if not file_path.exists() or not file_path.is_file():
        raise ValueError("Selected logo file does not exist.")
    ext = file_path.suffix.lower()
    if ext not in SUPPORTED_LOGO_EXTS:
        raise ValueError("Unsupported logo image format. Use PNG, JPG, BMP, WebP, or ICO.")
    size = file_path.stat().st_size
    if size <= 0:
        raise ValueError("Selected logo file is empty.")
    if size > MAX_LOGO_BYTES:
        mb = size / (1024 * 1024)
        raise ValueError(f"Logo file is too large ({mb:.1f} MB). Maximum size is 2.0 MB.")
    return file_path.read_bytes(), ext

