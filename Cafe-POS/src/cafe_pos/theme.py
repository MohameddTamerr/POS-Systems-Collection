from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette, QPixmap
from PySide6.QtWidgets import QApplication

from .config import (
    BusinessConfig,
    DEFAULT_ACCENT_COLOR,
    DEFAULT_FONT_SCALE_PCT,
    PALETTE_BG,
    PALETTE_BORDER,
    PALETTE_MUTED,
    PALETTE_SAGE,
    PALETTE_SECONDARY,
    PALETTE_SURFACE,
    PALETTE_TEXT,
    validate_font_scale_pct,
    validate_hex_color,
    validate_ui_density,
)
from .i18n import is_rtl, layout_direction
from .paths import get_asset_path

_ACTIVE_FONT_SCALE_PCT: int = DEFAULT_FONT_SCALE_PCT
_ACTIVE_UI_DENSITY: str = "comfortable"


def get_active_font_scale_pct() -> int:
    return _ACTIVE_FONT_SCALE_PCT


def get_active_ui_density() -> str:
    return _ACTIVE_UI_DENSITY


def scale_px(base_px: float, scale_pct: int | None = None) -> int:
    pct = _ACTIVE_FONT_SCALE_PCT if scale_pct is None else validate_font_scale_pct(scale_pct)
    return max(1, int(round(base_px * (pct / 100.0))))


def scale_pt(base_px: float, scale_pct: int | None = None) -> str:
    pct = _ACTIVE_FONT_SCALE_PCT if scale_pct is None else validate_font_scale_pct(scale_pct)
    val = round(base_px * (pct / 100.0), 1)
    return f"{int(val)}px" if val.is_integer() else f"{val}px"


@dataclass(frozen=True)
class CozyTokens:
    bg: str
    surface: str
    surface_alt: str
    text: str
    muted: str
    border: str
    border_strong: str
    primary: str
    primary_hover: str
    primary_pressed: str
    primary_tint: str
    primary_tint_hover: str
    primary_border: str
    card_outline: str
    on_primary: str
    sage: str
    secondary: str
    secondary_tint: str
    danger: str
    danger_bg: str
    danger_border: str


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    cleaned = validate_hex_color(hex_color).lstrip("#")
    return int(cleaned[0:2], 16), int(cleaned[2:4], 16), int(cleaned[4:6], 16)


def _darken_hex(hex_color: str, factor: float = 0.84) -> str:
    r, g, b = _hex_to_rgb(hex_color)
    return "#{:02X}{:02X}{:02X}".format(
        max(0, min(255, int(r * factor))),
        max(0, min(255, int(g * factor))),
        max(0, min(255, int(b * factor))),
    )


def _blend(hex_a: str, hex_b: str, weight_b: float) -> str:
    ra, ga, ba = _hex_to_rgb(hex_a)
    rb, gb, bb = _hex_to_rgb(hex_b)
    nr = int(ra * (1.0 - weight_b) + rb * weight_b)
    ng = int(ga * (1.0 - weight_b) + gb * weight_b)
    nb = int(ba * (1.0 - weight_b) + bb * weight_b)
    return f"#{nr:02X}{ng:02X}{nb:02X}"


def contrast_text_hex(hex_color: str) -> str:
    r, g, b = _hex_to_rgb(hex_color)

    def linearize(channel: int) -> float:
        s = channel / 255.0
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    luminance = 0.2126 * linearize(r) + 0.7152 * linearize(g) + 0.0722 * linearize(b)
    return "#202820" if luminance > 0.42 else "#FFFFFF"


def get_tokens(accent_color: str) -> CozyTokens:
    primary = validate_hex_color(accent_color or DEFAULT_ACCENT_COLOR)
    sage = _blend(PALETTE_SAGE, primary, 0.08) if primary != DEFAULT_ACCENT_COLOR else PALETTE_SAGE
    return CozyTokens(
        bg=PALETTE_BG,
        surface=PALETTE_SURFACE,
        surface_alt="#EEF2EE",
        text=PALETTE_TEXT,
        muted=PALETTE_MUTED,
        border=PALETTE_BORDER,
        border_strong="#C3D0C8",
        primary=primary,
        primary_hover=_darken_hex(primary, 0.85),
        primary_pressed=_darken_hex(primary, 0.72),
        primary_tint=_blend("#FFFFFF", primary, 0.08),
        primary_tint_hover=_blend("#FFFFFF", primary, 0.14),
        primary_border=_blend("#FFFFFF", primary, 0.34),
        card_outline=_blend(PALETTE_BORDER, primary, 0.18),
        on_primary=contrast_text_hex(primary),
        sage=sage,
        secondary=PALETTE_SECONDARY,
        secondary_tint="#FDF0E8",
        danger="#B23A3A",
        danger_bg="#FDF2F1",
        danger_border="#E9B8B4",
    )


def scaled_logo_pixmap(cfg: BusinessConfig, width: int, height: int) -> QPixmap:
    pixmap = QPixmap()
    if cfg.logo_blob:
        pixmap.loadFromData(cfg.logo_blob)
    if pixmap.isNull():
        default_path = get_asset_path("default_logo.png")
        if default_path.exists():
            pixmap.load(str(default_path))
    if pixmap.isNull():
        fallback = QPixmap(width, height)
        fallback.fill(QColor(validate_hex_color(cfg.accent_color)))
        return fallback
    return pixmap.scaled(width, height, Qt.KeepAspectRatio, Qt.SmoothTransformation)


def build_stylesheet(
    accent_color: str,
    *,
    rtl: bool = False,
    font_scale_pct: int = DEFAULT_FONT_SCALE_PCT,
    density: str = "comfortable",
) -> str:
    t = get_tokens(accent_color)
    pct = validate_font_scale_pct(font_scale_pct)
    dens = validate_ui_density(density)
    pad_scale = 0.82 if dens == "compact" else 1.0
    font_factor = pct / 100.0

    def fs(base_px: float) -> str:
        val = round(base_px * font_factor, 1)
        return f"{int(val)}px" if val.is_integer() else f"{val}px"

    def sp(base_px: float, *, floor_px: int = 2) -> int:
        return max(floor_px, int(round(base_px * pad_scale * (0.6 + 0.4 * font_factor))))

    in_h = max(20, int(round(20 * (0.55 + 0.45 * font_factor) * (0.9 if dens == "compact" else 1.0))))
    chevron_path = get_asset_path("chevron_down.png")
    chevron_rule = (
        f"image: url({chevron_path.as_posix()}); width: 12px; height: 12px;"
        if chevron_path.exists()
        else ""
    )
    chevron_up_path = get_asset_path("chevron_up.png")
    spin_up_rule = (
        f"image: url({chevron_up_path.as_posix()}); width: 9px; height: 9px;"
        if chevron_up_path.exists()
        else ""
    )
    spin_down_rule = (
        f"image: url({chevron_path.as_posix()}); width: 9px; height: 9px;"
        if chevron_path.exists()
        else ""
    )
    check_path = get_asset_path("checkbox_check.png")
    check_rule = (
        f"image: url({check_path.as_posix()});"
        if check_path.exists()
        else ""
    )
    gb_title_side = (
        "subcontrol-position: top right;\n        right: 16px;"
        if rtl
        else "subcontrol-position: top left;\n        left: 16px;"
    )
    spin_padding = "padding: 6px 10px 6px 30px;" if rtl else "padding: 6px 30px 6px 10px;"
    spin_border_side = (
        "border-right: 1px solid {b}; border-left: none;".format(b=t.border)
        if rtl
        else "border-left: 1px solid {b};".format(b=t.border)
    )
    spin_up_pos = (
        f"subcontrol-position: top left;\n        border-top-left-radius: 9px;\n        border-bottom: 1px solid {t.border};"
        if rtl
        else f"subcontrol-position: top right;\n        border-top-right-radius: 9px;\n        border-bottom: 1px solid {t.border};"
    )
    spin_down_pos = (
        "subcontrol-position: bottom left;\n        border-bottom-left-radius: 9px;"
        if rtl
        else "subcontrol-position: bottom right;\n        border-bottom-right-radius: 9px;"
    )
    combo_padding = "padding-left: 30px; padding-right: 12px;" if rtl else "padding-left: 12px; padding-right: 30px;"
    combo_drop_pos = "subcontrol-position: center left;" if rtl else "subcontrol-position: center right;"
    header_col_border = (
        f"border-left: 1px solid {t.border}; border-right: none;"
        if rtl
        else f"border-right: 1px solid {t.border};"
    )
    receipt_font = (
        '"Segoe UI Variable Text", "Segoe UI Variable", "Segoe UI", "Tahoma", "Arial", sans-serif'
        if rtl
        else '"Consolas", "Courier New", monospace'
    )
    return f"""
    QWidget {{
        font-family: "Segoe UI Variable Text", "Segoe UI Variable", "Segoe UI", "Tahoma", "Arial", sans-serif;
        font-size: {fs(13)};
        color: {t.text};
    }}

    QMainWindow, QDialog {{
        background-color: {t.bg};
    }}

    /* ================= Top Floating Boutique Header ================= */
    QFrame#TopNavBar {{
        background-color: {t.surface};
        border-bottom: 1px solid {t.border};
    }}

    QLabel#BrandLogoFrame {{
        background-color: {t.surface};
        border: 1px solid {t.border};
        border-radius: 12px;
    }}

    QLabel#BrandTitleLabel {{
        font-size: {fs(15)};
        font-weight: 700;
        color: {t.text};
    }}

    QLabel#BrandSubLabel {{
        font-size: {fs(11.5)};
        font-weight: 500;
        color: {t.muted};
    }}

    QLabel#HeaderOrdersChip, QLabel#RegisterOrdersChip {{
        background-color: {t.sage};
        border: 1px solid {t.border};
        color: {t.text};
        font-size: {fs(11.5)};
        font-weight: 700;
        padding: {sp(4)}px {sp(11)}px;
        border-radius: 12px;
    }}

    QPushButton.NavTabButton {{
        background-color: transparent;
        border: 1px solid transparent;
        border-radius: 14px;
        padding: {sp(6)}px {sp(12)}px;
        font-size: {fs(13)};
        font-weight: 600;
        color: {t.muted};
    }}

    QPushButton.NavTabButton:hover {{
        background-color: {t.surface};
        border-color: {t.border};
        color: {t.text};
    }}

    QPushButton.NavTabButton[active="true"] {{
        background-color: {t.primary};
        color: {t.on_primary};
        border-color: {t.primary};
    }}

    QPushButton.NavTabButton:focus {{
        border: 1.5px solid {t.primary_border};
    }}

    QPushButton.NavTabButton[active="true"]:focus {{
        border-color: {t.primary};
    }}

    QPushButton#NavOverflowBtn {{
        background-color: {t.surface};
        border: 1px solid {t.border};
        border-radius: 14px;
        padding: 5px 10px;
        font-weight: 700;
        color: {t.text};
    }}

    QPushButton#NavOverflowBtn:hover {{
        background-color: {t.sage};
        border-color: {t.primary_border};
    }}

    QPushButton#UserProfileBtn {{
        background-color: {t.surface};
        border: 1px solid {t.border};
        border-radius: 19px;
        padding: 3px 10px 3px 5px;
    }}

    QPushButton#UserProfileBtn:hover {{
        background-color: {t.sage};
        border-color: {t.primary_border};
    }}

    QPushButton#UserProfileBtn:focus {{
        border: 1.5px solid {t.primary};
    }}

    QLabel#ProfileAvatarCircle {{
        background-color: {t.primary};
        color: {t.on_primary};
        border-radius: 14px;
        font-size: 11px;
        font-weight: 700;
    }}

    QLabel#ProfileRolePill {{
        background-color: {t.sage};
        border: 1px solid {t.border};
        color: {t.text};
        font-size: 10px;
        font-weight: 700;
        padding: 1px 7px;
        border-radius: 8px;
    }}

    QLabel#ProfileRolePill[admin="true"] {{
        background-color: {t.primary_tint};
        border: 1px solid {t.primary_border};
        color: {t.primary};
    }}

    QMenu {{
        background-color: {t.surface};
        border: 1px solid {t.border_strong};
        border-radius: 12px;
        padding: 6px;
        color: {t.text};
    }}

    QMenu::item {{
        padding: 7px 14px;
        border-radius: 8px;
        font-size: 12.5px;
        font-weight: 600;
        color: {t.text};
    }}

    QMenu::item:selected {{
        background-color: {t.sage};
        color: {t.text};
    }}

    QMenu::item:disabled {{
        color: {t.muted};
        background-color: transparent;
        font-weight: 600;
    }}

    QMenu::separator {{
        height: 1px;
        background: {t.border};
        margin: 5px 8px;
    }}

    QFrame#SessionUserPill {{
        background-color: {t.surface};
        border: 1px solid {t.border};
        border-radius: 15px;
    }}

    QPushButton#LogoutBtn {{
        background-color: {t.surface};
        border: 1px solid {t.danger_border};
        border-radius: 14px;
        padding: 5px 11px;
        font-size: 12px;
        font-weight: 700;
        color: {t.danger};
    }}

    QPushButton#LogoutBtn:hover {{
        background-color: {t.danger_bg};
        border-color: {t.danger};
    }}

    QPushButton#LogoutBtn:pressed {{
        background-color: #F9DFDD;
    }}

    QFrame#LanguageSelectorBar {{
        background-color: {t.surface_alt};
        border: 1px solid {t.border_strong};
        border-radius: 15px;
    }}

    QPushButton.LangOptionBtn {{
        background-color: transparent;
        border: none;
        border-radius: 12px;
        padding: 4px 12px;
        font-size: 12px;
        font-weight: 700;
        color: {t.muted};
    }}

    QPushButton.LangOptionBtn:hover {{
        color: {t.text};
        background-color: {t.surface};
    }}

    QPushButton.LangOptionBtn[selected="true"] {{
        background-color: {t.primary};
        color: {t.on_primary};
    }}

    QFrame#LoginCard, QFrame#LoginSplitCard {{
        background-color: {t.surface};
        border: 1px solid {t.border_strong};
        border-radius: 22px;
    }}

    QFrame#LoginHeroPanel {{
        background-color: {t.sage};
        border: 1px solid {t.border};
        border-radius: 17px;
    }}

    QFrame#LoginFormPanel {{
        background-color: transparent;
        border: none;
    }}

    QLabel#LoginHeroPill {{
        background-color: {t.surface};
        border: 1px solid {t.border};
        color: {t.primary};
        font-size: 11px;
        font-weight: 700;
        padding: 4px 12px;
        border-radius: 12px;
    }}

    QLabel#LoginHeroTitle {{
        font-size: 18px;
        font-weight: 700;
        color: {t.text};
    }}

    QLabel#LoginHeroTagline {{
        font-size: 12.5px;
        color: {t.muted};
    }}

    QFrame#LoginErrorBanner {{
        background-color: {t.danger_bg};
        border: 1px solid {t.danger_border};
        border-radius: 11px;
    }}

    QFrame#LoginContactCard {{
        background-color: rgba(255, 255, 255, 0.84);
        border: 1px solid {t.border};
        border-radius: 18px;
    }}

    QFrame#LoginContactIconBadge {{
        background-color: {t.primary_tint};
        border: 1px solid {t.primary_border};
        border-radius: 11px;
    }}

    QLabel#LoginContactTitle {{
        font-size: {fs(12.5)};
        font-weight: 700;
        color: {t.text};
    }}

    QLabel#LoginContactSub {{
        font-size: {fs(11.5)};
        color: {t.muted};
    }}

    QLabel#LoginContactCredit {{
        font-size: {fs(11)};
        font-weight: 600;
        color: {t.primary};
    }}

    QPushButton.LoginContactBtn {{
        background-color: {t.surface};
        border: 1px solid {t.border_strong};
        border-radius: 11px;
        padding: {sp(6)}px {sp(12)}px;
        font-size: {fs(12)};
        font-weight: 600;
        color: {t.text};
    }}

    QPushButton.LoginContactBtn:hover {{
        background-color: {t.primary_tint};
        border-color: {t.primary_border};
        color: {t.primary};
    }}

    QPushButton.LoginContactBtn:pressed {{
        background-color: {t.sage};
    }}

    QPushButton.AboutLinkBtn {{
        background-color: transparent;
        border: none;
        outline: none;
        padding: 2px 4px;
        font-size: {fs(12.5)};
        font-weight: 700;
        color: {t.primary};
        text-align: {"right" if rtl else "left"};
    }}

    QPushButton.AboutLinkBtn:hover {{
        background-color: transparent;
        border: none;
        color: {t.primary_hover};
        text-decoration: underline;
    }}

    QPushButton.AboutLinkBtn:focus {{
        border: none;
        outline: none;
    }}

    QLabel.RoleAdminBadge {{
        background-color: {t.primary};
        color: {t.on_primary};
        font-size: 10.5px;
        font-weight: 700;
        padding: 2px 9px;
        border-radius: 9px;
    }}

    QLabel.RoleCashierBadge {{
        background-color: {t.sage};
        border: 1px solid {t.border};
        color: {t.text};
        font-size: 10.5px;
        font-weight: 700;
        padding: 2px 9px;
        border-radius: 9px;
    }}

    QLabel.StatusActiveBadge {{
        background-color: #E6F4EA;
        border: 1px solid #B9DFC6;
        color: #125E34;
        font-size: 11px;
        font-weight: 700;
        padding: 2px 9px;
        border-radius: 9px;
    }}

    QLabel.StatusDisabledBadge {{
        background-color: {t.danger_bg};
        border: 1px solid {t.danger_border};
        color: {t.danger};
        font-size: 11px;
        font-weight: 700;
        padding: 2px 9px;
        border-radius: 9px;
    }}

    QCheckBox {{
        spacing: 8px;
        color: {t.text};
        font-weight: 600;
    }}

    QCheckBox::indicator {{
        width: 17px;
        height: 17px;
        border-radius: 5px;
        border: 1.4px solid {t.border_strong};
        background-color: {t.surface};
    }}

    QCheckBox::indicator:hover {{
        border-color: {t.primary};
    }}

    QCheckBox::indicator:checked {{
        background-color: {t.primary};
        border-color: {t.primary};
        {check_rule}
    }}

    /* ================= Lists & Checkable Product Assignment Rows ================= */
    QListWidget {{
        background-color: {t.surface};
        border: 1px solid {t.border};
        border-radius: 12px;
        padding: 5px;
        outline: none;
    }}

    QListWidget::item {{
        border-radius: 9px;
        padding: 6px 10px;
        color: {t.text};
    }}

    QListWidget::item:hover {{
        background-color: {t.surface_alt};
    }}

    QListWidget::item:selected {{
        background-color: {t.sage};
        color: {t.text};
        font-weight: 700;
    }}

    QListWidget::indicator {{
        width: 18px;
        height: 18px;
        border-radius: 5px;
        border: 1.5px solid {t.border_strong};
        background-color: {t.surface};
    }}

    QListWidget::indicator:hover {{
        border-color: {t.primary};
    }}

    QListWidget::indicator:checked {{
        background-color: {t.primary};
        border-color: {t.primary};
        {check_rule}
    }}

    QListWidget::indicator:checked:hover {{
        background-color: {t.primary_hover};
        border-color: {t.primary_hover};
        {check_rule}
    }}

    QListWidget#AssignedProductsList::item {{
        background-color: transparent;
        border: none;
        padding: 6px 10px;
    }}

    /* ================= Panels, Cards & Surfaces ================= */
    QFrame.CozyPanel, QFrame.BoutiqueCard, QFrame.SurfaceCard {{
        background-color: {t.surface};
        border: 1px solid {t.border};
        border-radius: 16px;
    }}

    QFrame#CheckoutHeaderCard, QFrame#CheckoutDetailCard {{
        background-color: {t.surface};
        border: 1px solid {t.border};
        border-radius: 16px;
    }}

    QFrame#CheckoutTotalBadge {{
        background-color: {t.primary_tint};
        border: 1px solid {t.primary_border};
        border-radius: 12px;
    }}

    QFrame#InstaPayInfoBox {{
        background-color: {t.bg};
        border: 1px solid {t.primary_border};
        border-radius: 12px;
    }}

    QPushButton.PayMethodCard {{
        background-color: {t.surface};
        border: 1.5px solid {t.border};
        border-radius: 13px;
        padding: 10px 8px;
        font-size: 12.5px;
        font-weight: 700;
        color: {t.text};
    }}

    QPushButton.PayMethodCard:hover {{
        background-color: {t.surface_alt};
        border-color: {t.primary_border};
    }}

    QPushButton.PayMethodCard:focus {{
        border: 2px solid {t.primary};
    }}

    QPushButton.PayMethodCard[selected="true"] {{
        background-color: {t.primary_tint};
        border: 2px solid {t.primary};
        color: {t.primary};
    }}

    QPushButton.QuickCashExactBtn {{
        background-color: {t.primary};
        border: 1.5px solid {t.primary};
        border-radius: 10px;
        padding: 6px 12px;
        font-size: 12px;
        font-weight: 700;
        color: {t.on_primary};
    }}

    QPushButton.QuickCashExactBtn:hover {{
        background-color: {t.primary_hover};
        border-color: {t.primary_hover};
    }}

    QPushButton.QuickCashAddBtn {{
        background-color: {t.sage};
        border: 1px solid {t.border_strong};
        border-radius: 10px;
        padding: 6px 12px;
        font-size: 12px;
        font-weight: 700;
        color: {t.text};
    }}

    QPushButton.QuickCashAddBtn:hover {{
        background-color: {t.primary_tint};
        border-color: {t.primary_border};
        color: {t.primary};
    }}

    QFrame.CozySubCard {{
        background-color: {t.bg};
        border: 1px solid {t.border};
        border-radius: 12px;
    }}

    QFrame.DangerCard {{
        background-color: {t.danger_bg};
        border: 1px solid {t.danger_border};
        border-radius: 16px;
    }}

    QGroupBox {{
        background-color: {t.surface};
        border: 1px solid {t.border};
        border-radius: 16px;
        margin-top: 15px;
        padding: 20px 18px 16px 18px;
        font-weight: 700;
        color: {t.text};
    }}

    QGroupBox::title {{
        subcontrol-origin: margin;
        {gb_title_side}
        padding: 3px 12px;
        border-radius: 9px;
        background-color: {t.sage};
        border: 1px solid {t.border};
        color: {t.text};
        font-size: 12px;
        font-weight: 700;
    }}

    QLabel.PageHeading {{
        font-size: {fs(16.5)};
        font-weight: 700;
        color: {t.text};
    }}

    QLabel.SectionHeader {{
        font-size: {fs(15)};
        font-weight: 700;
        color: {t.text};
    }}

    QLabel.MutedLabel {{
        font-size: {fs(12)};
        color: {t.muted};
    }}

    QLabel.CountBadge {{
        background-color: {t.sage};
        border: 1px solid {t.border};
        color: {t.text};
        font-size: {fs(11.5)};
        font-weight: 700;
        padding: {sp(3)}px {sp(10)}px;
        border-radius: 11px;
    }}

    QLabel.PrimaryBadge {{
        background-color: {t.primary_tint};
        border: 1px solid {t.primary_border};
        color: {t.primary};
        font-size: {fs(11.5)};
        font-weight: 700;
        padding: {sp(3)}px {sp(10)}px;
        border-radius: 11px;
    }}

    QLabel.PeachBadge {{
        background-color: {t.secondary_tint};
        border: 1px solid {t.secondary};
        color: #7A3E31;
        font-size: {fs(11)};
        font-weight: 700;
        padding: {sp(4)}px {sp(10)}px;
        border-radius: 10px;
    }}

    QLabel.CategoryPill {{
        background-color: {t.sage};
        border: 1px solid {t.border};
        color: {t.text};
        font-size: {fs(11)};
        font-weight: 600;
        padding: {sp(3)}px {sp(9)}px;
        border-radius: 9px;
    }}

    QLabel.ValidationHint {{
        font-size: {fs(12)};
        color: {t.danger};
        font-weight: 600;
    }}

    /* ================= Inputs, Dropdowns & Search Pill ================= */
    QLineEdit, QComboBox, QSpinBox, QTextEdit {{
        background-color: {t.surface};
        border: 1px solid {t.border};
        border-radius: 10px;
        padding: {sp(6)}px {sp(11)}px;
        font-size: {fs(13)};
        color: {t.text};
        selection-background-color: {t.primary};
        selection-color: {t.on_primary};
    }}

    QLineEdit, QComboBox, QSpinBox {{
        min-height: {in_h}px;
    }}

    QSpinBox {{
        {spin_padding}
        min-width: 118px;
    }}

    QSpinBox::up-button, QSpinBox::down-button {{
        subcontrol-origin: border;
        width: 22px;
        background-color: {t.surface_alt};
        {spin_border_side}
    }}

    QSpinBox::up-button {{
        {spin_up_pos}
    }}

    QSpinBox::down-button {{
        {spin_down_pos}
    }}

    QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
        background-color: {t.sage};
    }}

    QSpinBox::up-arrow {{
        {spin_up_rule}
    }}

    QSpinBox::down-arrow {{
        {spin_down_rule}
    }}

    QComboBox {{
        {combo_padding}
    }}

    QComboBox::drop-down {{
        subcontrol-origin: padding;
        {combo_drop_pos}
        width: 28px;
        border: none;
        background: transparent;
    }}

    QComboBox::down-arrow {{
        {chevron_rule}
    }}

    QComboBox QAbstractItemView {{
        background-color: {t.surface};
        border: 1px solid {t.border_strong};
        border-radius: 10px;
        padding: 4px;
        color: {t.text};
        selection-background-color: {t.sage};
        selection-color: {t.text};
        outline: none;
    }}

    QLineEdit:hover, QComboBox:hover {{
        border-color: {t.primary_border};
    }}

    QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
        background-color: {t.surface};
        border: 1.5px solid {t.primary};
    }}

    QLineEdit.SearchInput {{
        background-color: {t.surface};
        border: 1.2px solid {t.border};
        border-radius: 18px;
        padding: 8px 14px;
        font-size: 13px;
    }}

    QLineEdit.SearchInput:focus {{
        background-color: {t.surface};
        border: 1.5px solid {t.primary};
    }}

    /* ================= Buttons ================= */
    QPushButton {{
        background-color: {t.surface};
        border: 1px solid {t.border_strong};
        border-radius: 10px;
        padding: {sp(7)}px {sp(14)}px;
        font-size: {fs(13)};
        font-weight: 600;
        color: {t.text};
    }}

    QPushButton:hover {{
        background-color: {t.sage};
        border-color: {t.primary_border};
    }}

    QPushButton:pressed {{
        background-color: {t.border};
    }}

    QPushButton:focus {{
        border: 1.5px solid {t.primary};
    }}

    QPushButton:disabled {{
        background-color: {t.surface_alt};
        color: #A2ABA2;
        border-color: {t.border};
    }}

    QPushButton#PrimaryActionBtn, QPushButton[role="primary"] {{
        background-color: {t.primary};
        border-color: {t.primary};
        color: {t.on_primary};
        font-weight: 700;
        border-radius: 12px;
    }}

    QPushButton#PrimaryActionBtn:hover, QPushButton[role="primary"]:hover {{
        background-color: {t.primary_hover};
        border-color: {t.primary_hover};
    }}

    QPushButton#PrimaryActionBtn:pressed, QPushButton[role="primary"]:pressed {{
        background-color: {t.primary_pressed};
        border-color: {t.primary_pressed};
    }}

    QPushButton#PrimaryActionBtn:disabled, QPushButton[role="primary"]:disabled {{
        background-color: #CBD3C8;
        border-color: #CBD3C8;
        color: #7D877D;
    }}

    QPushButton[role="primary"][loading="true"],
    QPushButton#PrimaryActionBtn[loading="true"],
    QPushButton#CheckoutPillBtn[loading="true"] {{
        background-color: {t.primary_hover};
        border-color: {t.primary_hover};
        color: {t.on_primary};
    }}

    QPushButton#CheckoutPillBtn {{
        background-color: {t.primary};
        border: 1px solid {t.primary};
        border-radius: 21px;
        color: {t.on_primary};
        font-size: {fs(14)};
        font-weight: 700;
        padding: {sp(8)}px {sp(18)}px;
    }}

    QPushButton#CheckoutPillBtn:hover {{
        background-color: {t.primary_hover};
        border-color: {t.primary_hover};
    }}

    QPushButton#CheckoutPillBtn:pressed {{
        background-color: {t.primary_pressed};
    }}

    QPushButton#CheckoutPillBtn:disabled {{
        background-color: #CBD3C8;
        border-color: #CBD3C8;
        color: #7B857B;
    }}

    QPushButton[role="secondary"] {{
        background-color: {t.secondary_tint};
        border-color: {t.secondary};
        color: #6B362A;
    }}

    QPushButton[role="secondary"]:hover {{
        background-color: {t.secondary};
        color: {t.text};
    }}

    QPushButton[role="danger"] {{
        background-color: {t.surface};
        border-color: {t.danger_border};
        color: {t.danger};
    }}

    QPushButton[role="danger"]:hover {{
        background-color: {t.danger_bg};
        border-color: {t.danger};
    }}

    /* Filter Chips & Period Pills */
    QPushButton.CategoryChip {{
        background-color: {t.surface};
        border: 1px solid {t.border};
        border-radius: 15px;
        padding: 5px 13px;
        font-size: 12px;
        font-weight: 600;
        color: {t.muted};
    }}

    QPushButton.CategoryChip:hover {{
        background-color: {t.sage};
        border-color: {t.primary_border};
        color: {t.text};
    }}

    QPushButton.CategoryChip[selected="true"] {{
        background-color: {t.primary};
        border-color: {t.primary};
        color: {t.on_primary};
    }}

    /* ================= Boutique Product Card & Circular Add Button ================= */
    QFrame.CozyProductCard {{
        background-color: {t.surface};
        border: 1.2px solid {t.card_outline};
        border-radius: 16px;
    }}

    QFrame.CozyProductCard[active_cart="true"] {{
        border: 1.6px solid {t.primary};
        background-color: #FAFCF8;
    }}

    QFrame.CozyProductCard:hover {{
        background-color: {t.primary_tint};
        border: 1.5px solid {t.primary};
    }}

    QFrame.CozyProductCard:focus {{
        border: 2px solid {t.primary};
    }}

    QLabel.ProductCardPrice {{
        font-size: 13px;
        font-weight: 700;
        color: {t.primary};
    }}

    QLabel.ProductCartQtyBadge {{
        background-color: {t.secondary};
        color: {t.text};
        border-radius: 9px;
        font-size: 10.5px;
        font-weight: 700;
        padding: 2px 7px;
    }}

    QPushButton.CircleAddBtn {{
        background-color: {t.surface};
        border: 1.3px solid {t.border_strong};
        border-radius: 17px;
        min-width: 34px;
        max-width: 34px;
        min-height: 34px;
        max-height: 34px;
        padding: 0px;
    }}

    QPushButton.CircleAddBtn:hover {{
        background-color: {t.primary};
        border-color: {t.primary};
    }}

    /* ================= Cart Items & Payment Dock ================= */
    QFrame.CartItemCard {{
        background-color: {t.surface};
        border: 1px solid {t.border};
        border-radius: 12px;
    }}

    QFrame.CartItemCard:hover {{
        background-color: {t.bg};
        border-color: {t.primary_border};
    }}

    QFrame.QtyStepperPill {{
        background-color: {t.bg};
        border: 1px solid {t.border};
        border-radius: 11px;
    }}

    QPushButton.QtyMiniBtn {{
        background-color: transparent;
        border: none;
        padding: 0px;
        min-width: 22px;
        min-height: 22px;
        border-radius: 7px;
        font-weight: 700;
    }}

    QPushButton.QtyMiniBtn:hover {{
        background-color: {t.sage};
    }}

    QPushButton.RemoveRowBtn {{
        background-color: {t.surface};
        border: 1px solid {t.danger_border};
        padding: 0px;
        min-width: 26px;
        min-height: 26px;
        border-radius: 8px;
        color: {t.danger};
    }}

    QPushButton.RemoveRowBtn:hover {{
        background-color: {t.danger_bg};
        border-color: {t.danger};
    }}

    QPushButton.MiniEditBtn {{
        background-color: {t.bg};
        border: 1px solid {t.border};
        padding: 0px;
        min-width: 24px;
        min-height: 24px;
        border-radius: 7px;
        color: {t.text};
    }}

    QPushButton.MiniEditBtn:hover {{
        background-color: {t.sage};
        border-color: {t.primary_border};
    }}

    QFrame#OrderTypeBar {{
        background-color: {t.surface_alt};
        border-top: 1px solid {t.border};
        border-bottom: 1px solid {t.border};
    }}

    QPushButton.OrderTypePill {{
        background-color: {t.surface};
        border: 1px solid {t.border};
        border-radius: 10px;
        padding: 5px 8px;
        font-size: 11.5px;
        font-weight: 600;
        color: {t.muted};
    }}

    QPushButton.OrderTypePill:hover {{
        border-color: {t.primary_border};
        background-color: {t.primary_tint};
        color: {t.text};
    }}

    QPushButton.OrderTypePill[selected="true"] {{
        background-color: {t.primary};
        border: 1px solid {t.primary};
        color: {t.on_primary};
        font-weight: 700;
    }}

    QPushButton.ModifierOptionBtn {{
        background-color: {t.surface};
        border: 1.5px solid {t.border};
        border-radius: 11px;
        padding: 7px 11px;
        font-size: 12.5px;
        font-weight: 600;
        color: {t.text};
        text-align: left;
    }}

    QPushButton.ModifierOptionBtn:hover {{
        border-color: {t.primary_border};
        background-color: {t.primary_tint};
    }}

    QPushButton.ModifierOptionBtn[selected="true"] {{
        background-color: {t.sage};
        border: 2px solid {t.primary};
        color: {t.primary};
        font-weight: 700;
    }}

    QPushButton.QuickCashBtn {{
        background-color: {t.sage};
        border: 1px solid {t.border};
        border-radius: 10px;
        padding: 3px 9px;
        font-size: 11.5px;
        font-weight: 700;
        color: {t.text};
    }}

    QPushButton.QuickCashBtn:hover {{
        background-color: {t.primary};
        border-color: {t.primary};
        color: {t.on_primary};
    }}

    QFrame#OrderSummaryBox {{
        background-color: {t.surface};
        border-top: 1px solid {t.border};
        border-bottom-left-radius: 16px;
        border-bottom-right-radius: 16px;
    }}

    QLabel#TotalValueLabel {{
        font-size: {fs(20)};
        font-weight: 700;
        color: {t.primary};
    }}

    QLabel#ChangeValueLabel {{
        font-size: {fs(14.5)};
        font-weight: 700;
        color: {t.primary};
    }}

    QLabel#ChangeValueLabel[insufficient="true"] {{
        color: {t.danger};
    }}

    /* ================= Analytics KPI & Report Cards ================= */
    QFrame.KpiCard {{
        background-color: {t.surface};
        border: 1px solid {t.border};
        border-radius: 16px;
    }}

    QLabel.KpiTitleLabel {{
        font-size: {fs(12)};
        font-weight: 600;
        color: {t.muted};
    }}

    QLabel.KpiValueLabel {{
        font-size: {fs(22)};
        font-weight: 700;
        color: {t.text};
    }}

    QLabel.KpiSubLabel {{
        font-size: {fs(11.5)};
        font-weight: 600;
        color: {t.primary};
    }}

    /* ================= Gallery Thumbnails ================= */
    QFrame.GalleryThumbCard {{
        background-color: {t.surface};
        border: 1.5px solid {t.border};
        border-radius: 12px;
    }}

    QFrame.GalleryThumbCard:hover {{
        border-color: {t.primary_border};
        background-color: {t.primary_tint};
    }}

    QFrame.GalleryThumbCard[selected="true"] {{
        border: 2px solid {t.primary};
        background-color: {t.sage};
    }}

    /* ================= Tables (Products & Sales & Reports) ================= */
    QTableWidget {{
        background-color: {t.surface};
        alternate-background-color: {t.bg};
        border: 1px solid {t.border};
        border-radius: 12px;
        gridline-color: {t.border};
        selection-background-color: {t.sage};
        selection-color: {t.text};
        outline: none;
    }}

    QTableWidget::item {{
        padding: 4px 8px;
    }}

    QTableWidget::item:hover {{
        background-color: {t.surface_alt};
    }}

    QTableWidget::item:selected {{
        background-color: {t.sage};
        color: {t.text};
    }}

    QHeaderView::section {{
        background-color: {t.surface_alt};
        color: {t.muted};
        font-weight: 700;
        font-size: 12px;
        padding: 9px 10px;
        border: none;
        border-bottom: 1px solid {t.border};
        {header_col_border}
    }}

    QTextEdit#ReceiptPreviewEdit {{
        background-color: {t.surface};
        border: 1px solid {t.border};
        border-radius: 12px;
        font-family: {receipt_font};
    }}

    /* ================= Toast Banner ================= */
    QFrame#ToastBanner {{
        background-color: {t.text};
        border: 1px solid {t.text};
        border-radius: 10px;
    }}

    QLabel#ToastBannerLabel {{
        color: #FFFFFF;
        font-size: 11.5px;
        font-weight: 600;
    }}

    /* ================= Scroll Areas, Hosts & Splitters ================= */
    QSplitter {{
        background-color: {t.bg};
    }}

    QSplitter::handle {{
        background-color: {t.bg};
    }}

    QSplitter::handle:hover {{
        background-color: {t.primary_border};
        border-radius: 3px;
    }}

    QScrollArea {{
        border: none;
        background-color: transparent;
    }}

    QScrollArea#RegisterGridScroll,
    QWidget#ProductGridHost,
    QScrollArea#CategoryCardsScroll,
    QWidget#CategoryStripHost,
    QScrollArea#SettingsScroll,
    QWidget#SettingsHost,
    QScrollArea#ReportsScroll,
    QWidget#ReportsHost,
    QScrollArea#UsersScroll,
    QWidget#UsersHost,
    QScrollArea#LoginScroll,
    QWidget#LoginPageHost,
    QWidget#CheckoutScrollHost {{
        background-color: {t.bg};
    }}

    QWidget#CartListHost,
    QWidget#GalleryHost,
    QScrollArea#SetupPage2Scroll,
    QScrollArea#SetupPage2Scroll > QWidget,
    QWidget#SetupPage2Host {{
        background-color: {t.surface};
        border: none;
    }}

    QScrollBar:vertical {{
        background: transparent;
        width: 8px;
        margin: 2px;
    }}

    QScrollBar::handle:vertical {{
        background: #C8D0C2;
        border-radius: 4px;
        min-height: 28px;
    }}

    QScrollBar::handle:vertical:hover {{
        background: {t.primary_border};
    }}

    QScrollBar::add-line:vertical,
    QScrollBar::sub-line:vertical {{
        height: 0px;
    }}

    QScrollBar:horizontal {{
        background: transparent;
        height: 5px;
        margin: 1px;
    }}

    QScrollBar::handle:horizontal {{
        background: #C8D0C2;
        border-radius: 3px;
        min-width: 28px;
    }}

    QScrollBar::add-line:horizontal,
    QScrollBar::sub-line:horizontal {{
        width: 0px;
    }}

    /* ================= Settings Navigation Rail ================= */
    QFrame#SettingsRailFrame {{
        background-color: {t.surface};
        border: 1px solid {t.border};
        border-radius: 16px;
    }}

    QPushButton.SettingsNavBtn {{
        background-color: transparent;
        border: 1px solid transparent;
        border-radius: 11px;
        padding: {sp(8)}px {sp(12)}px;
        font-size: {fs(13)};
        font-weight: 600;
        color: {t.muted};
        text-align: {"right" if rtl else "left"};
    }}

    QPushButton.SettingsNavBtn:hover {{
        background-color: {t.sage};
        color: {t.text};
    }}

    QPushButton.SettingsNavBtn[selected="true"] {{
        background-color: {t.primary_tint};
        border: 1px solid {t.primary_border};
        color: {t.primary};
        font-weight: 700;
    }}

    QFrame.AlertBannerCard {{
        background-color: {t.secondary_tint};
        border: 1px solid {t.secondary};
        border-radius: 12px;
    }}

    QStatusBar {{
        background-color: {t.surface};
        border-top: 1px solid {t.border};
        color: {t.muted};
        font-size: {fs(11.5)};
    }}
    """


def apply_theme(
    app: QApplication,
    cfg: BusinessConfig,
    *,
    lang: str | None = None,
    font_scale_pct: int | None = None,
    density: str | None = None,
) -> None:
    global _ACTIVE_FONT_SCALE_PCT, _ACTIVE_UI_DENSITY
    if app.style() is None or app.style().objectName().lower() != "fusion":
        app.setStyle("Fusion")
    rtl = is_rtl(lang)
    app.setLayoutDirection(layout_direction(lang))
    eff_scale = validate_font_scale_pct(
        font_scale_pct if font_scale_pct is not None else getattr(cfg, "default_font_scale_pct", DEFAULT_FONT_SCALE_PCT)
    )
    eff_density = validate_ui_density(
        density if density is not None else getattr(cfg, "default_ui_density", "comfortable")
    )
    _ACTIVE_FONT_SCALE_PCT = eff_scale
    _ACTIVE_UI_DENSITY = eff_density

    t = get_tokens(cfg.accent_color)
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(t.bg))
    palette.setColor(QPalette.WindowText, QColor(t.text))
    palette.setColor(QPalette.Base, QColor(t.surface))
    palette.setColor(QPalette.AlternateBase, QColor(t.bg))
    palette.setColor(QPalette.ToolTipBase, QColor(t.surface))
    palette.setColor(QPalette.ToolTipText, QColor(t.text))
    palette.setColor(QPalette.Text, QColor(t.text))
    palette.setColor(QPalette.Button, QColor(t.surface))
    palette.setColor(QPalette.ButtonText, QColor(t.text))
    palette.setColor(QPalette.BrightText, QColor("#FFFFFF"))
    palette.setColor(QPalette.Highlight, QColor(t.primary))
    palette.setColor(QPalette.HighlightedText, QColor(t.on_primary))
    palette.setColor(QPalette.PlaceholderText, QColor(t.muted))
    palette.setColor(QPalette.Disabled, QPalette.Text, QColor("#98A298"))
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#98A298"))
    palette.setColor(QPalette.Disabled, QPalette.WindowText, QColor("#98A298"))
    app.setPalette(palette)
    app.setStyleSheet(
        build_stylesheet(
            cfg.accent_color,
            rtl=rtl,
            font_scale_pct=eff_scale,
            density=eff_density,
        )
    )
