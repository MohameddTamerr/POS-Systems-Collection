from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .i18n import DEFAULT_LANGUAGE, normalize_language, tr
from .money import CURRENCIES
from .payments import normalize_country_code

# Premium Soft Glass Boutique Café default design tokens
PALETTE_BG = "#F7F8F4"
PALETTE_PRIMARY = "#005B37"
PALETTE_SAGE = "#EEF2EE"
PALETTE_SECONDARY = "#F4C7AE"
PALETTE_SURFACE = "#FFFFFF"
PALETTE_TEXT = "#26352F"
PALETTE_BORDER = "#D9E2DC"
PALETTE_MUTED = "#56665D"

DEFAULT_ACCENT_COLOR = PALETTE_PRIMARY
DEFAULT_BUSINESS_NAME = "Roast & Ember Café"
DEFAULT_CURRENCY = "USD"
DEFAULT_IMAGE_PROVIDER = "openverse"
DEFAULT_BACKUP_FREQUENCY = "daily"
DEFAULT_BACKUP_RETENTION = 7
DEFAULT_APP_LANGUAGE = DEFAULT_LANGUAGE
DEFAULT_RECEIPT_LANGUAGE = "follow"
DEFAULT_BUSINESS_COUNTRY = "US"

DEFAULT_FONT_SCALE_PCT = 100
MIN_FONT_SCALE_PCT = 85
MAX_FONT_SCALE_PCT = 140
FONT_SCALE_PRESETS: tuple[tuple[str, int], ...] = (
    ("small", 90),
    ("default", 100),
    ("large", 115),
    ("xlarge", 130),
)
VALID_UI_DENSITIES = frozenset({"comfortable", "compact"})
VALID_WINDOW_STATES = frozenset({"maximized", "normal"})
VALID_THERMAL_WIDTHS = frozenset({58, 80})
VALID_ORDER_TYPES_CFG = frozenset({"DINE_IN", "TAKEAWAY", "DELIVERY"})

BACKUP_FREQUENCIES: dict[str, str] = {
    "daily": "Daily (when open or at next launch)",
    "weekly": "Weekly (when open or at next launch)",
}

AUTO_LOCK_CHOICES: list[tuple[str, int]] = [
    ("Disabled (Manual Logout Only)", 0),
    ("After 2 minutes", 2),
    ("After 5 minutes", 5),
    ("After 10 minutes", 10),
    ("After 15 minutes", 15),
    ("After 30 minutes", 30),
]

MAX_LOGO_BYTES = 2 * 1024 * 1024  # 2 MB cap for custom business logos
SUPPORTED_LOGO_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".ico"}

MAX_PRODUCT_IMAGE_BYTES = 8 * 1024 * 1024  # 8 MB cap for product images
SUPPORTED_PRODUCT_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}

IMAGE_PROVIDERS: dict[str, str] = {
    "openverse": "Openverse (Public Domain / CC0 · No API Key)",
    "pexels": "Pexels (Requires Pexels API Key)",
}

THEME_PRESETS: list[tuple[str, str]] = [
    ("Boutique Green", "#005B37"),
    ("MoCoffee Emerald", "#185C47"),
    ("Cozy Sage", "#72917B"),
    ("Roasted Espresso", "#5B3E31"),
    ("Terracotta Peach", "#C4624B"),
    ("Coastal Slate", "#2B4C65"),
    ("Plum Noir", "#6E3B4B"),
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
    image_provider: str = DEFAULT_IMAGE_PROVIDER
    pexels_api_key: str = ""
    reduced_motion: bool = False
    auto_lock_minutes: int = 0
    backup_enabled: bool = False
    backup_frequency: str = DEFAULT_BACKUP_FREQUENCY
    backup_destination: str = ""
    backup_retention_count: int = DEFAULT_BACKUP_RETENTION
    last_auto_backup_at: str = ""
    app_language: str = DEFAULT_APP_LANGUAGE
    receipt_language: str = DEFAULT_RECEIPT_LANGUAGE
    business_country: str = DEFAULT_BUSINESS_COUNTRY
    payment_methods_json: str = ""
    auto_save_receipt_pdf: bool = False
    receipt_export_dir: str = ""
    online_translation_enabled: bool = False
    country_configured: bool = False
    window_state_pref: str = "maximized"
    window_width: int = 1280
    window_height: int = 800
    default_font_scale_pct: int = DEFAULT_FONT_SCALE_PCT
    default_ui_density: str = "comfortable"
    allow_cashier_discounts: bool = True
    allow_cashier_refunds: bool = False
    max_cashier_discount_pct: int = 15
    default_order_type: str = "DINE_IN"
    enable_dine_in: bool = True
    enable_takeaway: bool = True
    enable_delivery: bool = True
    modifiers_enabled: bool = True
    inventory_tracking_enabled: bool = True
    auto_deduct_inventory: bool = True
    default_low_stock_alert: int = 10
    require_shift_for_sales: bool = False
    require_opening_cash: bool = True
    show_cashier_on_receipt: bool = True
    show_business_details_on_receipt: bool = True
    printer_name: str = ""
    printer_paper_width_mm: int = 80
    auto_print_receipt: bool = False


def validate_hex_color(value: str) -> str:
    cleaned = (value or "").strip()
    if _HEX_COLOR_RE.match(cleaned):
        return cleaned.upper()
    return DEFAULT_ACCENT_COLOR


def validate_currency(code: str) -> str:
    upper = (code or "").strip().upper()
    return upper if upper in CURRENCIES else DEFAULT_CURRENCY


def validate_app_language(lang: str | None) -> str:
    return normalize_language(lang, allow_empty=False, allow_follow=False)


def validate_receipt_language(lang: str | None) -> str:
    return normalize_language(lang, allow_empty=False, allow_follow=True)


def validate_country_code(code: str | None) -> str:
    return normalize_country_code(code)


def validate_image_provider(provider: str) -> str:
    lowered = (provider or "").strip().lower()
    return lowered if lowered in IMAGE_PROVIDERS else DEFAULT_IMAGE_PROVIDER


def validate_backup_frequency(freq: str) -> str:
    lowered = (freq or "").strip().lower()
    return lowered if lowered in BACKUP_FREQUENCIES else DEFAULT_BACKUP_FREQUENCY


def validate_retention_count(count: int) -> int:
    try:
        n = int(count)
    except (TypeError, ValueError):
        return DEFAULT_BACKUP_RETENTION
    return max(1, min(90, n))


def validate_auto_lock_minutes(minutes: int) -> int:
    try:
        n = int(minutes)
    except (TypeError, ValueError):
        return 0
    return max(0, min(240, n))


def validate_font_scale_pct(scale_pct: int | float | str | None) -> int:
    try:
        n = int(round(float(scale_pct)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_FONT_SCALE_PCT
    return max(MIN_FONT_SCALE_PCT, min(MAX_FONT_SCALE_PCT, n))


def validate_ui_density(density: str | None) -> str:
    lowered = (density or "").strip().lower()
    return lowered if lowered in VALID_UI_DENSITIES else "comfortable"


def validate_window_state_pref(state: str | None) -> str:
    lowered = (state or "").strip().lower()
    return lowered if lowered in VALID_WINDOW_STATES else "maximized"


def validate_window_dimension(val: int | None, *, default: int, min_val: int, max_val: int) -> int:
    try:
        n = int(val)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(min_val, min(max_val, n))


def validate_discount_pct_cap(pct: int | None) -> int:
    try:
        n = int(pct)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 15
    return max(0, min(100, n))


def validate_default_order_type_cfg(val: str | None) -> str:
    upper = (val or "").strip().upper()
    return upper if upper in VALID_ORDER_TYPES_CFG else "DINE_IN"


def validate_thermal_width_mm(val: int | None) -> int:
    try:
        n = int(val)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 80
    return n if n in VALID_THERMAL_WIDTHS else 80


def validate_low_stock_alert_threshold(val: int | None) -> int:
    try:
        n = int(val)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 10
    return max(0, min(100000, n))


def get_backup_frequency_label(freq: str, lang: str | None = None) -> str:
    norm = validate_backup_frequency(freq)
    return tr(f"backup.freq.{norm}", lang=lang)


def get_auto_lock_label(minutes: int, lang: str | None = None) -> str:
    return tr(f"autolock.{int(minutes)}", lang=lang)


def get_image_provider_label(provider: str, lang: str | None = None, *, short: bool = False) -> str:
    norm = validate_image_provider(provider)
    key = f"provider.short.{norm}" if short else f"provider.{norm}"
    return tr(key, lang=lang)


_PRESET_KEY_MAP = {
    "#005B37": "theme.preset.green",
    "#185C47": "theme.preset.emerald",
    "#72917B": "theme.preset.sage",
    "#5B3E31": "theme.preset.mocha",
    "#C4624B": "theme.preset.peach",
    "#2B4C65": "theme.preset.slate",
    "#6E3B4B": "theme.preset.rose",
}


def get_theme_preset_label(hex_color: str, fallback: str = "", lang: str | None = None) -> str:
    key = _PRESET_KEY_MAP.get((hex_color or "").strip().upper())
    if key:
        return tr(key, lang=lang)
    return fallback


def read_and_validate_logo(file_path: Path) -> tuple[bytes, str]:
    if not file_path.exists() or not file_path.is_file():
        raise ValueError(tr("backend.err.logo_missing"))
    ext = file_path.suffix.lower()
    if ext not in SUPPORTED_LOGO_EXTS:
        raise ValueError(tr("backend.err.logo_format", ext=ext))
    size = file_path.stat().st_size
    if size <= 0:
        raise ValueError(tr("backend.err.logo_empty"))
    if size > MAX_LOGO_BYTES:
        mb = size / (1024 * 1024)
        raise ValueError(tr("backend.err.logo_size", mb=f"{mb:.1f}"))
    return file_path.read_bytes(), ext
