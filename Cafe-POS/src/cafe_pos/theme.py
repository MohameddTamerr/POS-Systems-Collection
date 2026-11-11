from __future__ import annotations

from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication

from .config import BusinessConfig, DEFAULT_ACCENT_COLOR, validate_hex_color
from .paths import get_asset_path


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    cleaned = validate_hex_color(hex_color).lstrip("#")
    return int(cleaned[0:2], 16), int(cleaned[2:4], 16), int(cleaned[4:6], 16)


def _darken_hex(hex_color: str, factor: float = 0.80) -> str:
    r, g, b = _hex_to_rgb(hex_color)
    return "#{:02X}{:02X}{:02X}".format(
        max(0, min(255, int(r * factor))),
        max(0, min(255, int(g * factor))),
        max(0, min(255, int(b * factor))),
    )


def _blend_with_white(hex_color: str, weight: float) -> str:
    r, g, b = _hex_to_rgb(hex_color)
    nr = int(255 * (1.0 - weight) + r * weight)
    ng = int(255 * (1.0 - weight) + g * weight)
    nb = int(255 * (1.0 - weight) + b * weight)
    return f"#{nr:02X}{ng:02X}{nb:02X}"


def contrast_text_hex(hex_color: str) -> str:
    r, g, b = _hex_to_rgb(hex_color)

    def linearize(channel: int) -> float:
        s = channel / 255.0
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    luminance = 0.2126 * linearize(r) + 0.7152 * linearize(g) + 0.0722 * linearize(b)
    return "#1C1917" if luminance > 0.38 else "#FFFFFF"


def scaled_logo_pixmap(cfg: BusinessConfig, width: int, height: int) -> QPixmap:
    from PySide6.QtCore import Qt

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


def build_stylesheet(accent_color: str) -> str:
    accent = validate_hex_color(accent_color or DEFAULT_ACCENT_COLOR)
    accent_hover = _darken_hex(accent, 0.78)
    accent_tint = _blend_with_white(accent, 0.11)
    accent_tint_hover = _blend_with_white(accent, 0.18)
    accent_border = _blend_with_white(accent, 0.35)
    on_accent = contrast_text_hex(accent)

    return f"""
    QWidget {{
        font-family: "Segoe UI", "Inter", Arial, sans-serif;
        font-size: 13px;
        color: #1F1D1A;
    }}

    QMainWindow, QDialog {{
        background-color: #F5F3EF;
    }}

    QFrame#TopNavBar {{
        background-color: #FFFFFF;
        border-bottom: 1px solid #D8D2C8;
    }}

    QLabel#BrandTitleLabel {{
        font-size: 15px;
        font-weight: 700;
        color: #1F1D1A;
    }}

    QLabel#BrandSubLabel {{
        font-size: 11px;
        color: #6B655D;
    }}

    QPushButton.NavTabButton {{
        background-color: transparent;
        border: 1px solid transparent;
        border-radius: 5px;
        padding: 7px 14px;
        font-weight: 600;
        color: #4A453E;
    }}

    QPushButton.NavTabButton:hover {{
        background-color: #ECE8E0;
        color: #1F1D1A;
    }}

    QPushButton.NavTabButton[active="true"] {{
        background-color: {accent};
        color: {on_accent};
        border-color: {accent};
    }}

    QFrame.TerminalPanel, QGroupBox {{
        background-color: #FFFFFF;
        border: 1px solid #D8D2C8;
        border-radius: 6px;
    }}

    QGroupBox {{
        margin-top: 12px;
        padding: 16px 14px 14px 14px;
        font-weight: 700;
    }}

    QGroupBox::title {{
        subcontrol-origin: margin;
        subcontrol-position: top left;
        left: 12px;
        padding: 0 6px;
        background-color: #F5F3EF;
        color: #1F1D1A;
    }}

    QLabel.SectionHeader {{
        font-size: 15px;
        font-weight: 700;
        color: #1F1D1A;
    }}

    QLabel.MutedLabel {{
        font-size: 12px;
        color: #6B655D;
    }}

    QLineEdit, QComboBox, QSpinBox, QTextEdit {{
        background-color: #FAF8F5;
        border: 1px solid #CFC8BC;
        border-radius: 4px;
        padding: 6px 9px;
        color: #1F1D1A;
        selection-background-color: {accent};
        selection-color: {on_accent};
    }}

    QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
        background-color: #FFFFFF;
        border-color: {accent};
    }}

    QPushButton {{
        background-color: #FFFFFF;
        border: 1px solid #CFC8BC;
        border-radius: 5px;
        padding: 7px 13px;
        font-weight: 600;
        color: #1F1D1A;
    }}

    QPushButton:hover {{
        background-color: #F0ECE4;
        border-color: #B5ADA0;
    }}

    QPushButton:pressed {{
        background-color: #E5E0D6;
    }}

    QPushButton:disabled {{
        background-color: #EFECE6;
        color: #9A9389;
        border-color: #DDD7CD;
    }}

    QPushButton#PrimaryActionBtn, QPushButton[role="primary"] {{
        background-color: {accent};
        border-color: {accent};
        color: {on_accent};
    }}

    QPushButton#PrimaryActionBtn:hover, QPushButton[role="primary"]:hover {{
        background-color: {accent_hover};
        border-color: {accent_hover};
    }}

    QPushButton#PrimaryActionBtn:disabled, QPushButton[role="primary"]:disabled {{
        background-color: #C7C1B7;
        border-color: #C7C1B7;
        color: #F5F3EF;
    }}

    QPushButton[role="danger"] {{
        background-color: #FFFFFF;
        border-color: #D9A5A5;
        color: #9B1C1C;
    }}

    QPushButton[role="danger"]:hover {{
        background-color: #FDF2F2;
        border-color: #9B1C1C;
    }}

    QPushButton.CategoryButton {{
        background-color: #FAF8F5;
        border: 1px solid #D8D2C8;
        border-radius: 4px;
        padding: 6px 12px;
        font-weight: 600;
        color: #4A453E;
    }}

    QPushButton.CategoryButton:hover {{
        background-color: #ECE8E0;
    }}

    QPushButton.CategoryButton[selected="true"] {{
        background-color: {accent};
        border-color: {accent};
        color: {on_accent};
    }}

    QPushButton.ProductTile {{
        background-color: #FFFFFF;
        border: 1px solid #D8D2C8;
        border-radius: 5px;
        padding: 10px 12px;
        text-align: left;
    }}

    QPushButton.ProductTile:hover {{
        background-color: {accent_tint};
        border-color: {accent_border};
    }}

    QPushButton.ProductTile:pressed {{
        background-color: {accent_tint_hover};
    }}

    QLabel[role="price"] {{
        color: {accent};
    }}

    QPushButton.QtyMiniBtn {{
        padding: 2px 7px;
        min-width: 24px;
        min-height: 24px;
        font-weight: 700;
        border-radius: 4px;
    }}

    QPushButton.RemoveRowBtn {{
        padding: 0px;
        min-width: 26px;
        min-height: 26px;
        font-size: 15px;
        font-weight: 700;
        border-radius: 4px;
    }}

    QPushButton.QuickCashBtn {{
        background-color: #FAF8F5;
        border: 1px solid #D8D2C8;
        padding: 5px 9px;
        font-size: 12px;
        font-weight: 600;
    }}

    QPushButton.QuickCashBtn:hover {{
        background-color: {accent_tint};
        border-color: {accent_border};
    }}

    QTableWidget {{
        background-color: #FFFFFF;
        alternate-background-color: #FAF8F5;
        border: 1px solid #D8D2C8;
        border-radius: 5px;
        gridline-color: #ECE7DE;
        selection-background-color: {accent_tint};
        selection-color: #1F1D1A;
    }}

    QHeaderView::section {{
        background-color: #EFECE6;
        color: #4A453E;
        font-weight: 700;
        font-size: 12px;
        padding: 7px 8px;
        border: none;
        border-bottom: 1px solid #D8D2C8;
        border-right: 1px solid #E2DDD3;
    }}

    QFrame#OrderSummaryBox {{
        background-color: #FAF8F5;
        border-top: 1px solid #D8D2C8;
        border-bottom-left-radius: 6px;
        border-bottom-right-radius: 6px;
    }}

    QLabel#TotalValueLabel {{
        font-size: 20px;
        font-weight: 700;
        color: {accent};
    }}

    QLabel#ChangeValueLabel {{
        font-size: 16px;
        font-weight: 700;
        color: {accent};
    }}

    QLabel#ChangeValueLabel[insufficient="true"] {{
        color: #9B1C1C;
    }}

    QTextEdit#ReceiptPreviewEdit {{
        background-color: #FFFFFF;
        border: 1px solid #D8D2C8;
        font-family: "Consolas", "Courier New", monospace;
    }}

    QSplitter {{
        background-color: #F5F3EF;
    }}

    QSplitter::handle {{
        background-color: #DED8CE;
        border-radius: 2px;
    }}

    QSplitter::handle:hover {{
        background-color: {accent_border};
    }}

    QScrollArea {{
        border: none;
        background-color: #FFFFFF;
    }}

    QWidget#ProductGridHost {{
        background-color: #FFFFFF;
    }}

    QWidget#CategoryStripHost {{
        background-color: #FFFFFF;
    }}

    QScrollArea#SettingsScroll, QWidget#SettingsHost {{
        background-color: #F5F3EF;
    }}

    QStatusBar {{
        background-color: #EFECE6;
        border-top: 1px solid #D8D2C8;
        color: #4A453E;
        font-size: 12px;
    }}
    """


def apply_theme(app: QApplication, cfg: BusinessConfig) -> None:
    app.setStyleSheet(build_stylesheet(cfg.accent_color))
