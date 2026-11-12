from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from .paths import get_asset_path

# Crisp inline Lucide-style SVG strokes (currentColor substituted per render)
_ICON_PATHS: dict[str, str] = {
    "register": (
        '<rect x="3" y="4" width="18" height="12" rx="2"/>'
        '<path d="M7 20h10M9 16v4M15 16v4"/>'
        '<path d="M7 9h4M7 12h2M14 9h3v3h-3z"/>'
    ),
    "products": (
        '<path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/>'
        '<path d="m3.3 7 8.7 5 8.7-5M12 22V12"/>'
    ),
    "sales": (
        '<path d="M4 2v20l2-1 2 1 2-1 2 1 2-1 2 1 2-1 2 1V2l-2 1-2-1-2 1-2-1-2 1-2-1-2 1Z"/>'
        '<path d="M8 7h8M8 11h8M8 15h5"/>'
    ),
    "reports": (
        '<path d="M3 3v18h18"/>'
        '<path d="m7 14 4-4 4 3 5-6"/>'
    ),
    "settings": (
        '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/>'
        '<circle cx="12" cy="12" r="3"/>'
    ),
    "search": (
        '<circle cx="11" cy="11" r="7.5"/>'
        '<path d="m20.5 20.5-4.35-4.35"/>'
    ),
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "minus": '<path d="M5 12h14"/>',
    "close": '<path d="M18 6 6 18M6 6l12 12"/>',
    "arrow_right": '<path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>',
    "arrow_left": '<path d="M19 12H5"/><path d="m12 19-7-7 7-7"/>',
    "trash": (
        '<path d="M3 6h18"/>'
        '<path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/>'
        '<path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>'
        '<line x1="10" y1="11" x2="10" y2="17"/>'
        '<line x1="14" y1="11" x2="14" y2="17"/>'
    ),
    "edit": (
        '<path d="M12 20h9"/>'
        '<path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4Z"/>'
    ),
    "upload": (
        '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>'
        '<polyline points="17 8 12 3 7 8"/>'
        '<line x1="12" y1="3" x2="12" y2="15"/>'
    ),
    "image": (
        '<rect x="3" y="3" width="18" height="18" rx="2"/>'
        '<circle cx="9" cy="9" r="2"/>'
        '<path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/>'
    ),
    "wand": (
        '<path d="M15 4V2M15 16v-2M8 9h2M20 9h2M17.8 11.8 19 13M17.8 6.2 19 5M3 21l9-9M12.2 6.2 11 5"/>'
    ),
    "print": (
        '<polyline points="6 9 6 2 18 2 18 9"/>'
        '<path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/>'
        '<rect x="6" y="14" width="12" height="8"/>'
    ),
    "pdf": (
        '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/>'
        '<polyline points="14 2 14 8 20 8"/>'
        '<path d="M12 18v-6M9 15l3 3 3-3"/>'
    ),
    "check": '<polyline points="20 6 9 17 4 12"/>',
    "sparkles": (
        '<path d="m12 3-1.9 5.1L5 10l5.1 1.9L12 17l1.9-5.1L19 10l-5.1-1.9Z"/>'
        '<path d="M5 3v4M3 5h4M19 17v4M17 19h4"/>'
    ),
    "coffee": (
        '<path d="M17 8h1a4 4 0 1 1 0 8h-1"/>'
        '<path d="M3 8h14v9a4 4 0 0 1-4 4H7a4 4 0 0 1-4-4Z"/>'
        '<line x1="6" y1="2" x2="6" y2="4"/>'
        '<line x1="10" y1="2" x2="10" y2="4"/>'
        '<line x1="14" y1="2" x2="14" y2="4"/>'
    ),
    "calendar": (
        '<rect x="3" y="4" width="18" height="18" rx="2"/>'
        '<line x1="16" y1="2" x2="16" y2="6"/>'
        '<line x1="8" y1="2" x2="8" y2="6"/>'
        '<line x1="3" y1="10" x2="21" y2="10"/>'
    ),
    "database": (
        '<ellipse cx="12" cy="5" rx="8" ry="3"/>'
        '<path d="M4 5v14c0 1.66 3.58 3 8 3s8-1.34 8-3V5"/>'
        '<path d="M4 12c0 1.66 3.58 3 8 3s8-1.34 8-3"/>'
    ),
    "refresh": (
        '<path d="M3 12a9 9 0 0 1 15.36-6.36L21 8"/>'
        '<path d="M21 3v5h-5"/>'
        '<path d="M21 12a9 9 0 0 1-15.36 6.36L3 16"/>'
        '<path d="M8 16H3v5"/>'
    ),
    "dollar": (
        '<rect x="2" y="6" width="20" height="12" rx="2"/>'
        '<circle cx="12" cy="12" r="2.5"/>'
        '<path d="M6 12h.01M18 12h.01"/>'
    ),
    "trend": '<polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/><polyline points="16 7 22 7 22 13"/>',
    "bag": (
        '<path d="M6 2 3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4Z"/>'
        '<line x1="3" y1="6" x2="21" y2="6"/>'
        '<path d="M16 10a4 4 0 0 1-8 0"/>'
    ),
    "external": (
        '<path d="M15 3h6v6"/>'
        '<path d="M10 14 21 3"/>'
        '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>'
    ),
    "users": (
        '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/>'
        '<circle cx="9" cy="7" r="4"/>'
        '<path d="M22 21v-2a4 4 0 0 0-3-3.87"/>'
        '<path d="M16 3.13a4 4 0 0 1 0 7.75"/>'
    ),
    "user": (
        '<path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/>'
        '<circle cx="12" cy="7" r="4"/>'
    ),
    "lock": (
        '<rect x="3" y="11" width="18" height="11" rx="2"/>'
        '<path d="M7 11V7a5 5 0 0 1 10 0v4"/>'
    ),
    "shield": (
        '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z"/>'
    ),
    "key": (
        '<circle cx="7.5" cy="15.5" r="5.5"/>'
        '<path d="m21 2-9.6 9.6"/>'
        '<path d="m15.5 7.5 3 3L22 7l-3-3"/>'
    ),
    "logout": (
        '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/>'
        '<polyline points="16 17 21 12 16 7"/>'
        '<line x1="21" y1="12" x2="9" y2="12"/>'
    ),
    "eye": (
        '<path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/>'
        '<circle cx="12" cy="12" r="3"/>'
    ),
    "eye_off": (
        '<path d="M9.88 9.88a3 3 0 1 0 4.24 4.24"/>'
        '<path d="M10.73 5.08A10.43 10.43 0 0 1 12 5c7 0 10 7 10 7a13.16 13.16 0 0 1-1.67 2.68"/>'
        '<path d="M6.61 6.61A13.526 13.526 0 0 0 2 12s3 7 10 7a9.74 9.74 0 0 0 5.39-1.61"/>'
        '<line x1="2" y1="2" x2="22" y2="22"/>'
    ),
    "archive": (
        '<rect x="2" y="3" width="20" height="5" rx="1"/>'
        '<path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8"/>'
        '<path d="M10 12h4"/>'
    ),
    "folder": (
        '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>'
    ),
    "credit_card": (
        '<rect x="2" y="5" width="20" height="14" rx="2"/>'
        '<line x1="2" y1="10" x2="22" y2="10"/>'
        '<line x1="6" y1="15" x2="10" y2="15"/>'
    ),
    "smartphone": (
        '<rect x="5" y="2" width="14" height="20" rx="2"/>'
        '<line x1="12" y1="18" x2="12.01" y2="18"/>'
    ),
    "wallet": (
        '<path d="M19 7V4a1 1 0 0 0-1-1H5a2 2 0 0 0 0 4h15a1 1 0 0 1 1 1v4h-3a2 2 0 0 0 0 4h3a1 1 0 0 0 1-1v-2a1 1 0 0 0-1-1"/>'
        '<path d="M3 5v14a2 2 0 0 0 2 2h15a1 1 0 0 0 1-1v-4"/>'
    ),
    "globe": (
        '<circle cx="12" cy="12" r="10"/>'
        '<path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20"/>'
        '<path d="M2 12h20"/>'
    ),
    "more_horiz": (
        '<circle cx="12" cy="12" r="1.5"/>'
        '<circle cx="19" cy="12" r="1.5"/>'
        '<circle cx="5" cy="12" r="1.5"/>'
    ),
    "languages": (
        '<path d="m5 8 6 6"/><path d="m4 14 6-6 2-3"/><path d="M2 5h12"/><path d="M7 2h1"/>'
        '<path d="m22 22-5-10-5 10"/><path d="M14 18h6"/>'
    ),
    "banknote": (
        '<rect x="2" y="6" width="20" height="12" rx="2"/>'
        '<circle cx="12" cy="12" r="2.5"/>'
        '<path d="M6 12h.01M18 12h.01"/>'
    ),
    "apple_pay": (
        '<rect x="3" y="5" width="18" height="14" rx="2.5"/>'
        '<path d="M9.5 15c.9 0 1.3-.7 2.5-.7 1.1 0 1.5.7 2.4.7 1.2 0 2.1-1.4 2.1-2.8a2.3 2.3 0 0 0-1.4-2.1 2.2 2.2 0 0 0-1.9.3c-.4.2-.8.4-1.2.4s-.8-.2-1.2-.4a2.2 2.2 0 0 0-1.9-.3A2.4 2.4 0 0 0 7.4 12.2C7.4 13.6 8.3 15 9.5 15Z"/>'
        '<path d="M12.2 8.2c.4-.5.9-.8 1.5-.8"/>'
    ),
    "instapay": (
        '<path d="M13 2 4 14h7l-1 8 9-12h-7Z"/>'
    ),
    "chevron_down": '<path d="m6 9 6 6 6-6"/>',
    "chevron_right": '<path d="m9 18 6-6-6-6"/>',
    "chevron_left": '<path d="m15 18-6-6 6-6"/>',
    "sliders": (
        '<line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/>'
        '<line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/>'
        '<line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/>'
        '<line x1="2" y1="14" x2="6" y2="14"/><line x1="10" y1="8" x2="14" y2="8"/><line x1="18" y1="16" x2="22" y2="16"/>'
    ),
    "pause": '<rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15.5 14"/>',
    "utensils": (
        '<path d="M3 2v7c0 1.1.9 2 2 2h4a2 2 0 0 0 2-2V2"/><path d="M7 2v20"/>'
        '<path d="M21 15V2a5 5 0 0 0-5 5v6c0 1.1.9 2 2 2h3Zm0 0v7"/>'
    ),
    "truck": (
        '<path d="M14 18V6a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2v11a1 1 0 0 0 1 1h2"/>'
        '<path d="M15 18H9"/><path d="M19 18h2a1 1 0 0 0 1-1v-3.65a1 1 0 0 0-.22-.624l-3.48-4.35A1 1 0 0 0 17.52 8H14"/>'
        '<circle cx="17" cy="18" r="2"/><circle cx="7" cy="18" r="2"/>'
    ),
    "maximize": (
        '<path d="M8 3H5a2 2 0 0 0-2 2v3"/><path d="M21 8V5a2 2 0 0 0-2-2h-3"/>'
        '<path d="M3 16v3a2 2 0 0 0 2 2h3"/><path d="M16 21h3a2 2 0 0 0 2-2v-3"/>'
    ),
    "minimize": (
        '<path d="M8 3v3a2 2 0 0 1-2 2H3"/><path d="M21 8h-3a2 2 0 0 1-2-2V3"/>'
        '<path d="M3 16h3a2 2 0 0 1 2 2v3"/><path d="M16 21v-3a2 2 0 0 1 2-2h3"/>'
    ),
    "alert_triangle": (
        '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/>'
        '<path d="M12 9v4"/><path d="M12 17h.01"/>'
    ),
    "box": (
        '<path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/>'
        '<polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/>'
    ),
    "percent": (
        '<line x1="19" y1="5" x2="5" y2="19"/>'
        '<circle cx="6.5" cy="6.5" r="2.5"/><circle cx="17.5" cy="17.5" r="2.5"/>'
    ),
    "monitor": (
        '<rect x="2" y="3" width="20" height="14" rx="2"/>'
        '<line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/>'
    ),
    "store": (
        '<path d="m2 7 4.41-4.41A2 2 0 0 1 7.83 2h8.34a2 2 0 0 1 1.42.59L22 7"/>'
        '<path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8"/>'
        '<path d="M15 22v-4a2 2 0 0 0-2-2h-2a2 2 0 0 0-2 2v4"/>'
        '<path d="M2 7h20"/>'
    ),
    "tag": (
        '<path d="M12 2H2v10l9.29 9.29c.94.94 2.48.94 3.42 0l6.58-6.58c.94-.94.94-2.48 0-3.42L12 2Z"/>'
        '<path d="M7 7h.01"/>'
    ),
    "rotate_ccw": (
        '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/>'
        '<path d="M3 3v5h5"/>'
    ),
    "receipt": (
        '<path d="M4 2v20l2-1 2 1 2-1 2 1 2-1 2 1 2-1 2 1V2l-2 1-2-1-2 1-2-1-2 1-2-1-2 1Z"/>'
        '<path d="M16 8h-6a2 2 0 1 0 0 4h4a2 2 0 1 1 0 4H8"/>'
        '<path d="M12 17.5v.5M12 6v.5"/>'
    ),
    "info": (
        '<circle cx="12" cy="12" r="10"/>'
        '<path d="M12 16v-4"/>'
        '<path d="M12 8h.01"/>'
    ),
    "help": (
        '<circle cx="12" cy="12" r="10"/>'
        '<path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/>'
        '<path d="M12 17h.01"/>'
    ),
    "message": (
        '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>'
    ),
    "bug": (
        '<path d="m8 2 1.88 1.88"/><path d="M14.12 3.88 16 2"/>'
        '<path d="M9 7.13v-1a3.003 3.003 0 1 1 6 0v1"/>'
        '<path d="M12 20c-3.3 0-6-2.7-6-6v-3a4 4 0 0 1 4-4h4a4 4 0 0 1 4 4v3c0 3.3-2.7 6-6 6"/>'
        '<path d="M12 20v-9"/><path d="M6.53 9C4.6 8.8 3 7.1 3 5"/><path d="M6 13H2"/>'
        '<path d="M3 21c0-2.1 1.7-3.9 3.8-4"/><path d="M20.97 5c0 2.1-1.6 3.8-3.5 4"/>'
        '<path d="M22 13h-4"/><path d="M17.2 17c2.1.1 3.8 1.9 3.8 4"/>'
    ),
}

# Bundled transparent studio photos mapped to seeded starter menu names
DEFAULT_PRODUCT_ASSETS: dict[str, str] = {
    "double espresso": "double_espresso.png",
    "flat white": "flat_white.png",
    "classic cappuccino": "classic_cappuccino.png",
    "caramel latte": "caramel_latte.png",
    "iced americano": "iced_americano.png",
    "iced vanilla latte": "iced_vanilla_latte.png",
    "ceremonial matcha": "ceremonial_matcha.png",
    "spiced chai latte": "spiced_chai_latte.png",
    "butter croissant": "butter_croissant.png",
    "chocolate muffin": "chocolate_muffin.png",
    "turkey pesto panini": "turkey_pesto_panini.png",
    "house blend beans (250g)": "house_blend_beans.png",
}


def get_bundled_product_asset_path(
    product_name: str, fallback_en: str = ""
) -> Path | None:
    """Resolve bundled studio PNG for EN or known AR product names."""
    candidates: list[str] = []
    for raw in (product_name, fallback_en):
        cleaned = " ".join((raw or "").strip().split())
        if cleaned:
            candidates.append(cleaned.lower())

    # If the caller supplied an Arabic/localized name, resolve its canonical EN form too
    if product_name and not any(c in DEFAULT_PRODUCT_ASSETS for c in candidates):
        from .bilingual import suggest_bilingual_name

        sug = suggest_bilingual_name(product_name, is_category=False)
        if sug.target_lang == "en" and sug.target_text.strip():
            candidates.append(" ".join(sug.target_text.strip().lower().split()))

    for key in candidates:
        rel = DEFAULT_PRODUCT_ASSETS.get(key)
        if not rel:
            continue
        p = get_asset_path(f"products/{rel}")
        if p.exists() and p.is_file():
            return p
    return None


@lru_cache(maxsize=384)
def render_svg_pixmap(
    svg_markup: str, width: int, height: int, scale: int = 2
) -> QPixmap:
    """Rasterize clean inline SVG onto a high-DPI transparent QPixmap."""
    px_w = max(1, int(width * scale))
    px_h = max(1, int(height * scale))
    pixmap = QPixmap(px_w, px_h)
    pixmap.fill(Qt.transparent)

    renderer = QSvgRenderer(QByteArray(svg_markup.encode("utf-8")))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    renderer.render(painter, QRectF(0, 0, px_w, px_h))
    painter.end()

    pixmap.setDevicePixelRatio(float(scale))
    return pixmap


def get_svg_icon(name: str, color: str = "#202820", size: int = 16) -> QIcon:
    inner = _ICON_PATHS.get(name, _ICON_PATHS["coffee"])
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="-1.5 -1 27 26" '
        f'fill="none" stroke="{color}" stroke-width="2" '
        f'stroke-linecap="round" stroke-linejoin="round">{inner}</svg>'
    )
    pm = render_svg_pixmap(svg, size, size, scale=2)
    icon = QIcon()
    icon.addPixmap(pm)
    return icon


def get_svg_pixmap(name: str, color: str = "#202820", size: int = 18) -> QPixmap:
    inner = _ICON_PATHS.get(name, _ICON_PATHS["coffee"])
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        f'fill="none" stroke="{color}" stroke-width="2" '
        f'stroke-linecap="round" stroke-linejoin="round">{inner}</svg>'
    )
    return render_svg_pixmap(svg, size, size, scale=2)


def _blend(hex_a: str, hex_b: str, weight_b: float) -> str:
    ha = hex_a.lstrip("#")
    hb = hex_b.lstrip("#")
    ra, ga, ba = int(ha[0:2], 16), int(ha[2:4], 16), int(ha[4:6], 16)
    rb, gb, bb = int(hb[0:2], 16), int(hb[2:4], 16), int(hb[4:6], 16)
    r = int(ra * (1.0 - weight_b) + rb * weight_b)
    g = int(ga * (1.0 - weight_b) + gb * weight_b)
    b = int(ba * (1.0 - weight_b) + bb * weight_b)
    return f"#{r:02X}{g:02X}{b:02X}"


def _build_category_artwork_svg(category: str, accent: str, product_name: str = "") -> str:
    """Clean transparent-background studio vector illustration specific to item name & category."""
    cat = (category or "").lower()
    name = (product_name or "").lower()
    espresso = "#5E3B2E"
    sage_soft = _blend("#FFFFFF", accent, 0.20)
    peach = "#F7B4A3"

    if any(k in name for k in ("croissant", "danish", "brioche", "كرواسون")):
        return f"""
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 140 120">
          <ellipse cx="70" cy="96" rx="44" ry="8" fill="{sage_soft}" opacity="0.55"/>
          <path d="M26 74 C22 48 44 28 70 28 C96 28 118 48 114 74 C112 83 102 86 95 79 C87 72 79 75 70 75 C61 75 53 72 45 79 C38 86 28 83 26 74 Z"
                fill="#DE9E62" stroke="{espresso}" stroke-width="2.6"/>
          <path d="M48 34 C51 50 51 64 47 77 M70 29 C70 46 70 62 70 75 M92 34 C89 50 89 64 93 77"
                stroke="{espresso}" stroke-width="2.2" fill="none" stroke-linecap="round"/>
        </svg>"""

    if any(k in name for k in ("muffin", "cupcake", "cake", "tart", "مافن", "كيك", "تارت", "تشيز")):
        return f"""
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 140 120">
          <ellipse cx="70" cy="98" rx="34" ry="7" fill="{sage_soft}" opacity="0.55"/>
          <path d="M42 62 L49 95 H91 L98 62 Z" fill="{peach}" stroke="{espresso}" stroke-width="2.5"/>
          <line x1="56" y1="62" x2="59" y2="95" stroke="{espresso}" stroke-width="1.8" opacity="0.45"/>
          <line x1="70" y1="62" x2="70" y2="95" stroke="{espresso}" stroke-width="1.8" opacity="0.45"/>
          <line x1="84" y1="62" x2="81" y2="95" stroke="{espresso}" stroke-width="1.8" opacity="0.45"/>
          <path d="M37 62 C33 42 50 28 70 28 C90 28 107 42 103 62 C93 66 83 62 70 65 C57 62 47 66 37 62 Z"
                fill="#6B4235" stroke="{espresso}" stroke-width="2.5"/>
          <circle cx="57" cy="43" r="3.5" fill="#3D221A"/><circle cx="77" cy="40" r="3.5" fill="#3D221A"/>
          <circle cx="85" cy="51" r="3" fill="#3D221A"/><circle cx="66" cy="53" r="3" fill="#3D221A"/>
        </svg>"""

    if any(
        k in name or k in cat
        for k in (
            "iced",
            "cold",
            "juice",
            "smoothie",
            "milkshake",
            "frappe",
            "mojito",
            "مثلج",
            "بارد",
            "عصير",
            "سموذي",
            "ميلك شيك",
            "فرابيه",
            "موهيتو",
        )
    ):
        return f"""
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 140 120">
          <ellipse cx="70" cy="103" rx="30" ry="6" fill="{sage_soft}" opacity="0.55"/>
          <path d="M46 28 H94 L87 96 C86 99 83 101 79 101 H61 C57 101 54 99 53 96 Z"
                fill="#FFFFFF" stroke="{espresso}" stroke-width="2.5"/>
          <path d="M49 46 H91 L86 93 C85 96 83 97 79 97 H61 C57 97 55 96 54 93 Z"
                fill="#D8AA7D" opacity="0.88"/>
          <rect x="57" y="42" width="12" height="12" rx="3" transform="rotate(-12 63 48)" fill="#FFFFFF" opacity="0.82"/>
          <rect x="74" y="45" width="11" height="11" rx="3" transform="rotate(14 79 50)" fill="#FFFFFF" opacity="0.78"/>
          <path d="M84 13 L89 44" stroke="{accent}" stroke-width="4.5" stroke-linecap="round"/>
        </svg>"""

    if any(k in name or k in cat for k in ("matcha", "tea", "chai", "ماتشا", "شاي", "كركديه")):
        cup_fill = accent if "matcha" in name or "matcha" in cat or "ماتشا" in name else "#DDA06B"
        return f"""
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 140 120">
          <ellipse cx="70" cy="96" rx="36" ry="7" fill="{sage_soft}" opacity="0.6"/>
          <path d="M38 45 H102 C102 74 90 90 70 90 C50 90 38 74 38 45 Z"
                fill="#FFFFFF" stroke="{espresso}" stroke-width="2.5"/>
          <ellipse cx="70" cy="45" rx="32" ry="7" fill="{cup_fill}"/>
          <path d="M97 30 C110 18 121 22 117 36 C113 42 103 40 97 30 Z"
                fill="{accent}" stroke="{espresso}" stroke-width="2"/>
          <path d="M62 24 C60 18 64 15 63 11 M75 22 C73 16 77 13 76 9"
                stroke="{accent}" stroke-width="2.2" fill="none" stroke-linecap="round"/>
        </svg>"""

    if any(k in name or k in cat for k in ("panini", "sandwich", "toast", "kitchen", "food", "بانيني", "ساندوتش", "توست", "مطبخ")):
        return f"""
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 140 120">
          <ellipse cx="70" cy="94" rx="46" ry="9" fill="{sage_soft}" opacity="0.55"/>
          <rect x="30" y="60" width="80" height="22" rx="10" fill="#DFAA74" stroke="{espresso}" stroke-width="2.3"/>
          <path d="M32 62 Q50 54 70 62 T108 62" stroke="{accent}" stroke-width="4.5" fill="none" stroke-linecap="round"/>
          <rect x="28" y="42" width="84" height="19" rx="9" fill="#ECC08E" stroke="{espresso}" stroke-width="2.3"/>
          <line x1="48" y1="46" x2="54" y2="57" stroke="{espresso}" stroke-width="2.2" stroke-linecap="round"/>
          <line x1="68" y1="46" x2="74" y2="57" stroke="{espresso}" stroke-width="2.2" stroke-linecap="round"/>
          <line x1="88" y1="46" x2="94" y2="57" stroke="{espresso}" stroke-width="2.2" stroke-linecap="round"/>
        </svg>"""

    if any(k in name or k in cat for k in ("bean", "bag", "retail", "merch", "حبوب", "بيع")):
        return f"""
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 140 120">
          <ellipse cx="70" cy="100" rx="34" ry="6" fill="{sage_soft}" opacity="0.6"/>
          <path d="M46 28 H94 L100 92 C100 96 96 98 92 98 H48 C44 98 40 96 40 92 Z"
                fill="#FFFFFF" stroke="{espresso}" stroke-width="2.5"/>
          <path d="M44 28 L50 18 H90 L96 28 Z" fill="{peach}" stroke="{espresso}" stroke-width="2.2"/>
          <rect x="52" y="46" width="36" height="32" rx="7" fill="{accent}"/>
          <circle cx="70" cy="62" r="8" fill="#FFFFFF"/>
          <path d="M68 56 C70 60 70 64 68 68" stroke="{espresso}" stroke-width="2" fill="none" stroke-linecap="round"/>
        </svg>"""

    if any(k in cat for k in ("bak", "pastry", "dessert", "مخبوز", "حلويات")):
        return f"""
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 140 120">
          <ellipse cx="70" cy="95" rx="42" ry="8" fill="{sage_soft}" opacity="0.55"/>
          <path d="M28 73 C25 49 45 30 70 30 C95 30 115 49 112 73 Z"
                fill="#E0A166" stroke="{espresso}" stroke-width="2.5"/>
          <path d="M46 46 Q70 38 94 46" stroke="{peach}" stroke-width="4" fill="none" stroke-linecap="round"/>
        </svg>"""

    # Default Boutique Porcelain Café Cup (Espresso / Latte / Flat White / Cappuccino)
    return f"""
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 140 120">
      <ellipse cx="68" cy="94" rx="40" ry="8" fill="{sage_soft}" opacity="0.6"/>
      <path d="M96 49 H105 C112 49 116 54 116 60 C116 66 111 71 104 71 H94"
            fill="none" stroke="{espresso}" stroke-width="4" stroke-linecap="round"/>
      <path d="M38 42 H98 L94 76 C93 85 85 90 68 90 C51 90 43 85 42 76 Z"
            fill="#FFFFFF" stroke="{espresso}" stroke-width="2.5"/>
      <ellipse cx="68" cy="42" rx="30" ry="7" fill="{espresso}"/>
      <path d="M62 42 C65 39 71 39 74 42 C71 45 65 45 62 42 Z" fill="{peach}"/>
      <path d="M58 26 C56 21 61 18 59 13 M70 24 C68 19 73 16 71 11 M82 26 C80 21 85 18 83 13"
            stroke="{accent}" stroke-width="2.2" fill="none" stroke-linecap="round"/>
    </svg>"""


@lru_cache(maxsize=128)
def render_category_placeholder(
    category: str,
    width: int,
    height: int,
    accent_color: str = "#005B37",
    product_name: str = "",
) -> QPixmap:
    svg = _build_category_artwork_svg(category, accent_color, product_name=product_name)
    return render_svg_pixmap(svg, width, height, scale=2)


@lru_cache(maxsize=64)
def render_category_corner_artwork(
    category: str, stroke_color: str, width: int = 86, height: int = 68
) -> QPixmap:
    """Crisp editorial corner line-art illustration for Boutique Category Cards."""
    cat = (category or "").lower()
    if cat in {"all", "all menu"}:
        inner = """
        <circle cx="56" cy="44" r="24" stroke="{s}" stroke-width="2.4" fill="none"/>
        <path d="M46 44 C46 37 52 33 57 38 C62 43 66 39 66 34" stroke="{s}" stroke-width="2.2" fill="none" stroke-linecap="round"/>
        <path d="M44 52 Q56 59 68 52" stroke="{s}" stroke-width="2.2" fill="none" stroke-linecap="round"/>
        <circle cx="24" cy="24" r="5" stroke="{s}" stroke-width="2" fill="none"/>
        """
    elif any(
        k in cat
        for k in (
            "cold",
            "iced",
            "juice",
            "smoothie",
            "milkshake",
            "frappe",
            "mojito",
            "بارد",
            "مثلج",
            "عصير",
            "سموذي",
            "ميلك شيك",
            "موهيتو",
        )
    ):
        inner = """
        <path d="M36 16 H70 L65 64 H41 Z" stroke="{s}" stroke-width="2.4" fill="none" stroke-linejoin="round"/>
        <path d="M40 34 H66" stroke="{s}" stroke-width="2" stroke-linecap="round"/>
        <rect x="44" y="38" width="9" height="9" rx="2" stroke="{s}" stroke-width="1.8" fill="none"/>
        <rect x="55" y="42" width="8" height="8" rx="2" stroke="{s}" stroke-width="1.8" fill="none"/>
        <path d="M62 6 L56 32" stroke="{s}" stroke-width="2.4" stroke-linecap="round"/>
        """
    elif any(k in cat for k in ("tea", "matcha", "شاي", "ماتشا")):
        inner = """
        <path d="M32 34 H72 C72 54 63 64 52 64 C41 64 32 54 32 34 Z" stroke="{s}" stroke-width="2.4" fill="none"/>
        <path d="M72 39 H78 C82 39 84 42 84 46 C84 50 81 53 77 53 H70" stroke="{s}" stroke-width="2.2" fill="none"/>
        <path d="M58 24 C67 13 79 16 74 28 C70 33 61 31 58 24 Z" stroke="{s}" stroke-width="2.2" fill="none"/>
        <path d="M46 24 C45 19 48 16 47 12" stroke="{s}" stroke-width="2" stroke-linecap="round" fill="none"/>
        """
    elif any(
        k in cat
        for k in (
            "bak",
            "pastry",
            "pastries",
            "snack",
            "kitchen",
            "food",
            "dessert",
            "sandwich",
            "breakfast",
            "مخبوز",
            "معجن",
            "حلويات",
            "ساندوتش",
            "مطبخ",
            "إفطار",
        )
    ):
        inner = """
        <path d="M22 50 C20 32 35 20 54 20 C73 20 88 32 86 50 C84 56 77 58 72 53 C67 48 61 51 54 51 C47 51 41 48 36 53 C31 58 24 56 22 50 Z"
              stroke="{s}" stroke-width="2.4" fill="none"/>
        <path d="M38 24 C41 34 41 44 38 52 M54 20 C54 32 54 42 54 51 M70 24 C67 34 67 44 70 52"
              stroke="{s}" stroke-width="2" fill="none" stroke-linecap="round"/>
        """
    elif any(k in cat for k in ("retail", "bean", "بيع", "حبوب")):
        inner = """
        <path d="M35 18 H69 L73 62 H31 Z" stroke="{s}" stroke-width="2.4" fill="none" stroke-linejoin="round"/>
        <rect x="40" y="30" width="24" height="20" rx="4" stroke="{s}" stroke-width="2" fill="none"/>
        <path d="M52 34 C54 38 54 42 52 46" stroke="{s}" stroke-width="2" fill="none" stroke-linecap="round"/>
        """
    else:
        # Top-down Latte Art Cup rim (signature look from reference Image 4!)
        inner = """
        <circle cx="54" cy="42" r="24" stroke="{s}" stroke-width="2.5" fill="none"/>
        <circle cx="54" cy="42" r="18" stroke="{s}" stroke-width="2" fill="none"/>
        <path d="M77 37 H84 C88 37 90 40 90 43 C90 46 87 48 83 48 H76" stroke="{s}" stroke-width="2.4" fill="none"/>
        <path d="M54 29 C47 35 45 45 54 52 C63 45 61 35 54 29 Z" stroke="{s}" stroke-width="2" fill="none"/>
        <path d="M54 32 V52" stroke="{s}" stroke-width="1.8" stroke-linecap="round"/>
        """

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 92 70">'
        f'{inner.replace("{s}", stroke_color)}</svg>'
    )
    return render_svg_pixmap(svg, width, height, scale=2)


@lru_cache(maxsize=24)
def render_empty_state_illustration(
    kind: str, accent_color: str = "#005B37", size: int = 84
) -> QPixmap:
    peach = "#F7B4A3"
    cream = "#F7F7E9"
    espresso = "#202820"
    sage_soft = _blend("#FFFFFF", accent_color, 0.16)

    if kind == "cart":
        svg = f"""
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120">
          <circle cx="60" cy="60" r="52" fill="{sage_soft}"/>
          <circle cx="92" cy="30" r="14" fill="{peach}" opacity="0.8"/>
          <path d="M36 42 L84 42 L89 88 C89 92 85 95 80 95 L40 95 C35 95 31 92 31 88 Z"
                fill="#FFFFFF" stroke="{espresso}" stroke-width="2.6"/>
          <path d="M47 42 V35 C47 27 53 22 60 22 C67 22 73 27 73 35 V42"
                fill="none" stroke="{espresso}" stroke-width="2.6" stroke-linecap="round"/>
          <circle cx="60" cy="68" r="13" fill="{accent_color}" opacity="0.20"/>
          <path d="M60 61 v14 M53 68 h14" stroke="{accent_color}" stroke-width="2.6" stroke-linecap="round"/>
        </svg>"""
    elif kind == "sales":
        svg = f"""
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120">
          <circle cx="60" cy="60" r="52" fill="{sage_soft}"/>
          <circle cx="30" cy="34" r="12" fill="{peach}" opacity="0.85"/>
          <path d="M36 26 H84 V94 L76 89 L68 94 L60 89 L52 94 L44 89 L36 94 Z"
                fill="#FFFFFF" stroke="{espresso}" stroke-width="2.6" stroke-linejoin="round"/>
          <line x1="48" y1="45" x2="72" y2="45" stroke="{accent_color}" stroke-width="3" stroke-linecap="round"/>
          <line x1="48" y1="58" x2="72" y2="58" stroke="#C5CEC0" stroke-width="3" stroke-linecap="round"/>
          <line x1="48" y1="71" x2="64" y2="71" stroke="#C5CEC0" stroke-width="3" stroke-linecap="round"/>
        </svg>"""
    elif kind == "products":
        svg = f"""
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120">
          <circle cx="60" cy="60" r="52" fill="{peach}" opacity="0.45"/>
          <rect x="30" y="34" width="60" height="54" rx="10" fill="#FFFFFF" stroke="{espresso}" stroke-width="2.6"/>
          <rect x="42" y="46" width="36" height="22" rx="4" fill="{sage_soft}"/>
          <path d="M51 57 L58 51 L65 59 L70 54 L76 62 H44 Z" fill="{accent_color}" opacity="0.8"/>
          <line x1="42" y1="77" x2="74" y2="77" stroke="#C5CEC0" stroke-width="3" stroke-linecap="round"/>
        </svg>"""
    elif kind == "inventory":
        svg = f"""
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120">
          <circle cx="60" cy="60" r="52" fill="{sage_soft}"/>
          <circle cx="88" cy="30" r="12" fill="{peach}" opacity="0.8"/>
          <path d="M28 46 L60 28 L92 46 V82 L60 100 L28 82 Z" fill="#FFFFFF" stroke="{espresso}" stroke-width="2.6" stroke-linejoin="round"/>
          <path d="M28 46 L60 64 L92 46 M60 64 V100" fill="none" stroke="{accent_color}" stroke-width="2.4" stroke-linejoin="round"/>
        </svg>"""
    elif kind == "shift":
        svg = f"""
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120">
          <circle cx="60" cy="60" r="52" fill="{sage_soft}"/>
          <circle cx="32" cy="34" r="12" fill="{peach}" opacity="0.82"/>
          <rect x="26" y="40" width="68" height="48" rx="10" fill="#FFFFFF" stroke="{espresso}" stroke-width="2.6"/>
          <rect x="34" y="48" width="52" height="16" rx="4" fill="{sage_soft}"/>
          <circle cx="60" cy="76" r="5" fill="{accent_color}"/>
        </svg>"""
    elif kind == "expenses":
        svg = f"""
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120">
          <circle cx="60" cy="60" r="52" fill="{peach}" opacity="0.4"/>
          <rect x="28" y="34" width="64" height="54" rx="10" fill="#FFFFFF" stroke="{espresso}" stroke-width="2.6"/>
          <circle cx="60" cy="61" r="15" fill="{sage_soft}" stroke="{accent_color}" stroke-width="2.4"/>
          <path d="M60 53 V69 M54 58 H66 M54 64 H64" stroke="{accent_color}" stroke-width="2.2" stroke-linecap="round"/>
        </svg>"""
    elif kind == "refund":
        svg = f"""
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120">
          <circle cx="60" cy="60" r="52" fill="{sage_soft}"/>
          <path d="M36 52 A24 24 0 1 1 42 78" fill="none" stroke="{accent_color}" stroke-width="4" stroke-linecap="round"/>
          <path d="M36 38 V54 H52" fill="none" stroke="{espresso}" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>"""
    else:
        svg = f"""
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120">
          <circle cx="60" cy="60" r="52" fill="{sage_soft}"/>
          <circle cx="88" cy="34" r="12" fill="{peach}" opacity="0.8"/>
          <circle cx="56" cy="56" r="22" fill="#FFFFFF" stroke="{espresso}" stroke-width="2.8"/>
          <line x1="72" y1="72" x2="90" y2="90" stroke="{espresso}" stroke-width="4.2" stroke-linecap="round"/>
          <circle cx="56" cy="56" r="10" fill="{cream}"/>
        </svg>"""
    return render_svg_pixmap(svg, size, size, scale=2)
