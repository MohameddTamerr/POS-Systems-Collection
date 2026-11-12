from __future__ import annotations

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QEvent,
    QPoint,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QTransform,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from shiboken6 import isValid as _qt_is_valid

from ..icons import (
    get_bundled_product_asset_path,
    get_svg_icon,
    get_svg_pixmap,
    render_category_corner_artwork,
    render_category_placeholder,
    render_empty_state_illustration,
)
from ..i18n import is_rtl, normalize_language, tr, tr_category, tr_count
from ..image_search import resolve_product_image_path
from ..theme import get_tokens
from .animations import (
    animate_flash_highlight,
    attach_button_feedback,
    clear_widget_animations,
    is_reduced_motion,
)


def _mix_color(a: QColor, b: QColor, t: float) -> QColor:
    clamped = max(0.0, min(1.0, float(t)))
    return QColor(
        int(round(a.red() + (b.red() - a.red()) * clamped)),
        int(round(a.green() + (b.green() - a.green()) * clamped)),
        int(round(a.blue() + (b.blue() - a.blue()) * clamped)),
        int(round(a.alpha() + (b.alpha() - a.alpha()) * clamped)),
    )


class FlowLayout(QLayout):
    """Standard responsive wrapping layout for chips without clipping text."""

    def __init__(
        self, parent: QWidget | None = None, margin: int = 0, h_spacing: int = 6, v_spacing: int = 6
    ) -> None:
        super().__init__(parent)
        self._items = []
        self._h_spacing = h_spacing
        self._v_spacing = v_spacing
        self.setContentsMargins(margin, margin, margin, margin)

    def __del__(self) -> None:
        while self._items:
            self.takeAt(0)

    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientations:
        return Qt.Orientations(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        m = self.contentsMargins()
        effective = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x = effective.x()
        y = effective.y()
        line_height = 0

        for item in self._items:
            w = item.sizeHint().width()
            h = item.sizeHint().height()
            next_x = x + w + self._h_spacing
            if next_x - self._h_spacing > effective.right() and line_height > 0:
                x = effective.x()
                y += line_height + self._v_spacing
                next_x = x + w + self._h_spacing
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), QSize(w, h)))
            x = next_x
            line_height = max(line_height, h)

        return y + line_height - rect.y() + m.bottom()


class HorizontalWheelScrollArea(QScrollArea):
    """Horizontal scroll area that converts vertical mouse-wheel deltas into smooth horizontal pan."""

    def wheelEvent(self, event: QWheelEvent) -> None:
        hbar = self.horizontalScrollBar()
        if hbar and hbar.maximum() > 0:
            delta = event.angleDelta().x() or event.angleDelta().y()
            if delta:
                hbar.setValue(hbar.value() - delta)
                event.accept()
                return
        super().wheelEvent(event)


class LanguageSelectorWidget(QFrame):
    """Segmented bilingual selector pill: [English | العربية]."""

    language_changed = Signal(str)

    def __init__(self, initial_language: str = "en", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("LanguageSelectorBar")
        self.setLayoutDirection(Qt.LeftToRight)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self._current_language = normalize_language(initial_language)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        self.en_btn = QPushButton(tr("common.language_en", "en"))
        self.en_btn.setProperty("class", "LangOptionBtn")
        self.en_btn.setCursor(Qt.PointingHandCursor)
        self.en_btn.setFocusPolicy(Qt.StrongFocus)
        self.en_btn.clicked.connect(lambda: self._on_pick("en"))

        self.ar_btn = QPushButton(tr("common.language_ar", "ar-EG"))
        self.ar_btn.setProperty("class", "LangOptionBtn")
        self.ar_btn.setCursor(Qt.PointingHandCursor)
        self.ar_btn.setFocusPolicy(Qt.StrongFocus)
        self.ar_btn.clicked.connect(lambda: self._on_pick("ar-EG"))

        attach_button_feedback(self.en_btn)
        attach_button_feedback(self.ar_btn)

        layout.addWidget(self.en_btn)
        layout.addWidget(self.ar_btn)
        self.retranslate_ui()
        self._sync_styles()

    def language(self) -> str:
        return self._current_language

    def set_language(self, lang: str, *, emit: bool = False) -> None:
        norm = normalize_language(lang)
        changed = norm != self._current_language
        self._current_language = norm
        self._sync_styles()
        if emit and changed:
            self.language_changed.emit(norm)

    def retranslate_ui(self) -> None:
        self.en_btn.setText(tr("common.language_en", "en"))
        self.ar_btn.setText(tr("common.language_ar", "ar-EG"))
        self.en_btn.setAccessibleName(tr("common.language_en", "en"))
        self.ar_btn.setAccessibleName(tr("common.language_ar", "ar-EG"))

    def _sync_styles(self) -> None:
        self.en_btn.setProperty("selected", "true" if self._current_language == "en" else "false")
        self.ar_btn.setProperty("selected", "true" if self._current_language == "ar-EG" else "false")
        for btn in (self.en_btn, self.ar_btn):
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _on_pick(self, lang: str) -> None:
        norm = normalize_language(lang)
        if norm == self._current_language:
            return
        self._current_language = norm
        self._sync_styles()
        self.language_changed.emit(norm)


def clip_pixmap_rounded(
    source: QPixmap, target_w: int, target_h: int, radius: int = 8, scale: int = 2
) -> QPixmap:
    """Center-crop/fill a QPixmap cleanly onto a high-DPI rounded-corner QPixmap."""
    px_w = max(1, int(target_w * scale))
    px_h = max(1, int(target_h * scale))

    norm_src = QPixmap(source)
    norm_src.setDevicePixelRatio(1.0)
    scaled = norm_src.scaled(px_w, px_h, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    sx = max(0, (scaled.width() - px_w) // 2)
    sy = max(0, (scaled.height() - px_h) // 2)
    cropped = scaled.copy(sx, sy, px_w, px_h)
    cropped.setDevicePixelRatio(1.0)

    out = QPixmap(px_w, px_h)
    out.fill(Qt.transparent)
    painter = QPainter(out)
    try:
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, px_w, px_h), radius * scale, radius * scale)
        painter.setClipPath(path)
        painter.drawPixmap(0, 0, cropped)
    finally:
        if painter.isActive():
            painter.end()

    out.setDevicePixelRatio(float(scale))
    return out


def fit_pixmap_centered(
    source: QPixmap, target_w: int, target_h: int, radius: int = 0, scale: int = 2
) -> QPixmap:
    """Scale a QPixmap cleanly inside (target_w, target_h) preserving full transparency & shape."""
    px_w = max(1, int(target_w * scale))
    px_h = max(1, int(target_h * scale))

    norm_src = QPixmap(source)
    norm_src.setDevicePixelRatio(1.0)
    scaled = norm_src.scaled(px_w, px_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    scaled.setDevicePixelRatio(1.0)

    out = QPixmap(px_w, px_h)
    out.fill(Qt.transparent)
    painter = QPainter(out)
    try:
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        ox = (px_w - scaled.width()) // 2
        oy = (px_h - scaled.height()) // 2
        if radius > 0:
            path = QPainterPath()
            path.addRoundedRect(
                QRectF(ox, oy, scaled.width(), scaled.height()),
                radius * scale,
                radius * scale,
            )
            painter.setClipPath(path)
        painter.drawPixmap(ox, oy, scaled)
    finally:
        if painter.isActive():
            painter.end()

    out.setDevicePixelRatio(float(scale))
    return out


def build_product_pixmap(
    image_filename: str,
    category: str,
    width: int,
    height: int,
    accent_color: str = "#005B37",
    radius: int = 10,
    product_name: str = "",
    contain_transparent: bool = True,
) -> QPixmap:
    """Load custom photo, bundled studio cutout PNG, or product-aware SVG artwork."""
    img_path = resolve_product_image_path(image_filename)
    if img_path is not None:
        raw_pm = QPixmap(str(img_path))
        if not raw_pm.isNull():
            is_png = img_path.suffix.lower() == ".png"
            if is_png:
                return fit_pixmap_centered(raw_pm, width, height, radius=0, scale=2)
            if contain_transparent:
                return fit_pixmap_centered(raw_pm, width, height, radius=radius, scale=2)
            return clip_pixmap_rounded(raw_pm, width, height, radius=radius, scale=2)

    if not (image_filename or "").strip():
        bundled = get_bundled_product_asset_path(product_name)
        if bundled is not None:
            raw_pm = QPixmap(str(bundled))
            if not raw_pm.isNull():
                return fit_pixmap_centered(raw_pm, width, height, radius=0, scale=2)

    placeholder = render_category_placeholder(
        category, width, height, accent_color, product_name=product_name
    )
    return fit_pixmap_centered(placeholder, width, height, radius=0, scale=2)


class SquareThumbLabel(QLabel):
    """Consistent rounded-square product image thumbnail."""

    def __init__(self, size: int = 44, radius: int = 8, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._size = size
        self._radius = radius
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignCenter)

    def set_product_image(
        self,
        image_filename: str,
        category: str,
        accent_color: str = "#005B37",
        product_name: str = "",
    ) -> None:
        pm = build_product_pixmap(
            image_filename,
            category,
            self._size,
            self._size,
            accent_color,
            radius=self._radius,
            product_name=product_name,
            contain_transparent=False,
        )
        self.setPixmap(pm)

    def set_raw_bytes_preview(
        self, raw_bytes: bytes | None, category: str = "", accent_color: str = "#005B37"
    ) -> None:
        if raw_bytes:
            pm = QPixmap()
            if pm.loadFromData(raw_bytes) and not pm.isNull():
                self.setPixmap(fit_pixmap_centered(pm, self._size, self._size, radius=self._radius))
                return
        self.set_product_image("", category, accent_color)


class SearchLineEdit(QLineEdit):
    """Boutique search bar with crisp inline search icon & automatic clear control."""

    def __init__(self, placeholder: str = "Search...", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("class", "SearchInput")
        self.setPlaceholderText(placeholder)
        self.setClearButtonEnabled(True)
        self.addAction(get_svg_icon("search", "#5E6B5F", 15), QLineEdit.LeadingPosition)


class BoutiqueCategoryCard(QFrame):
    """Editorial café category card with smooth hover/press animations & corner art."""

    clicked = Signal(str)

    def __init__(
        self,
        category: str,
        item_count: int,
        selected: bool,
        accent_color: str,
        parent: QWidget | None = None,
        *,
        display_label: str = "",
    ) -> None:
        super().__init__(parent)
        self.category = category
        self._display_label = (display_label or "").strip()
        self._item_count = item_count
        self._selected = selected
        self._accent_color = accent_color
        self._hovered = False
        self._pressed = False
        self._hover_progress = 1.0 if selected else 0.0
        self._press_progress = 0.0

        self._hover_anim = QPropertyAnimation(self, b"hoverProgress", self)
        self._hover_anim.setDuration(145)
        self._hover_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._press_anim = QPropertyAnimation(self, b"pressProgress", self)
        self._press_anim.setDuration(110)
        self._press_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setFixedSize(136, 86)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(11, 8, 11, 8)
        layout.setSpacing(2)

        badge_row = QHBoxLayout()
        badge_row.setContentsMargins(0, 0, 0, 0)
        self.status_badge = QLabel("")
        self.status_badge.setAttribute(Qt.WA_TransparentForMouseEvents)
        badge_row.addWidget(self.status_badge)
        badge_row.addStretch(1)
        layout.addLayout(badge_row)

        layout.addStretch(1)

        self.title_lbl = QLabel("")
        self.title_lbl.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(self.title_lbl)

        self.count_lbl = QLabel("")
        self.count_lbl.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(self.count_lbl)

        self.retranslate_ui()
        self._apply_text_styles()

    def retranslate_ui(
        self,
        item_count: int | None = None,
        *,
        display_label: str | None = None,
    ) -> None:
        if item_count is not None:
            self._item_count = max(0, int(item_count))
        if display_label is not None:
            self._display_label = display_label.strip()
        if self.category == "All":
            display_name = tr("category.all_menu")
        elif self._display_label:
            display_name = self._display_label
        else:
            display_name = tr_category(self.category)
        items_str = tr_count("item", self._item_count)
        self.status_badge.setText(
            tr("category.badge.all") if self.category == "All" else tr("category.badge.available")
        )
        self.title_lbl.setText(display_name)
        self.count_lbl.setText(items_str)
        self.setAccessibleName(tr("category.a11y", name=display_name, items=items_str))
        self.update()

    def getHoverProgress(self) -> float:
        return self._hover_progress

    def setHoverProgress(self, value: float) -> None:
        self._hover_progress = float(value)
        self.update()

    hoverProgress = Property(float, getHoverProgress, setHoverProgress)

    def getPressProgress(self) -> float:
        return self._press_progress

    def setPressProgress(self, value: float) -> None:
        self._press_progress = float(value)
        self.update()

    pressProgress = Property(float, getPressProgress, setPressProgress)

    def _animate_prop(self, anim: QPropertyAnimation, start: float, end: float, duration: int) -> None:
        if not _qt_is_valid(self) or not _qt_is_valid(anim):
            return
        anim.stop()
        if is_reduced_motion():
            anim.setDuration(0)
        else:
            anim.setDuration(duration)
        anim.setStartValue(start)
        anim.setEndValue(end)
        anim.start()

    def stop_animations(self) -> None:
        for anim in (getattr(self, "_hover_anim", None), getattr(self, "_press_anim", None)):
            if anim is not None and _qt_is_valid(anim):
                anim.stop()
        clear_widget_animations(self)

    def _apply_text_styles(self) -> None:
        t = get_tokens(self._accent_color)
        if self._selected:
            fg = t.on_primary
            sub_fg = "#DCE8D7" if fg == "#FFFFFF" else "#2E3A2E"
            pill_bg = "rgba(255, 255, 255, 0.14)" if fg == "#FFFFFF" else "rgba(32, 40, 32, 0.08)"
            pill_border = "rgba(255, 255, 255, 0.42)" if fg == "#FFFFFF" else "rgba(32, 40, 32, 0.28)"
            self.status_badge.setStyleSheet(
                f"background-color: {pill_bg}; border: 1px solid {pill_border}; "
                f"color: {fg}; font-size: 10px; font-weight: 700; padding: 2px 8px; border-radius: 9px;"
            )
            self.title_lbl.setStyleSheet(f"font-size: 13.5px; font-weight: 700; color: {fg};")
            self.count_lbl.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {sub_fg};")
        else:
            pill_bg = t.secondary_tint if self._hovered else "#F7F7E9"
            pill_border = t.secondary if self._hovered else t.border
            self.status_badge.setStyleSheet(
                f"background-color: {pill_bg}; border: 1px solid {pill_border}; "
                f"color: {t.text}; font-size: 10px; font-weight: 600; padding: 2px 8px; border-radius: 9px;"
            )
            self.title_lbl.setStyleSheet(f"font-size: 13.5px; font-weight: 700; color: {t.text};")
            self.count_lbl.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {t.muted};")

    def enterEvent(self, event: QEvent) -> None:
        self._hovered = True
        self._apply_text_styles()
        if not self._selected:
            self._animate_prop(self._hover_anim, self._hover_progress, 1.0, 145)
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        self._hovered = False
        self._apply_text_styles()
        if not self._selected:
            self._animate_prop(self._hover_anim, self._hover_progress, 0.0, 145)
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._pressed = True
            self._animate_prop(self._press_anim, self._press_progress, 1.0, 95)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton and self._pressed:
            self._pressed = False
            self._animate_prop(self._press_anim, self._press_progress, 0.0, 130)
            if self.rect().contains(event.position().toPoint()):
                self.clicked.emit(self.category)
            event.accept()
            return
        self._pressed = False
        self._animate_prop(self._press_anim, self._press_progress, 0.0, 130)
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.clicked.emit(self.category)
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, _event) -> None:
        t = get_tokens(self._accent_color)
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

            inset = 1.0 + 1.0 * self._press_progress
            rect = QRectF(inset, inset, self.width() - 2.0 * inset, self.height() - 2.0 * inset)
            clip_path = QPainterPath()
            clip_path.addRoundedRect(rect, 15.0, 15.0)

            if self._selected:
                base_bg = QColor(t.primary)
                bg = _mix_color(base_bg, QColor(t.primary_pressed), self._press_progress * 0.45)
                border = QColor(t.primary)
                stroke_hex = "#FFFFFF" if t.on_primary == "#FFFFFF" else "#202820"
                art_opacity = 0.28
            else:
                bg = _mix_color(QColor(t.surface), QColor(t.secondary_tint), self._hover_progress)
                if self._press_progress > 0:
                    bg = _mix_color(bg, QColor(t.secondary), self._press_progress * 0.25)
                border = _mix_color(QColor(t.border), QColor(t.secondary), self._hover_progress)
                stroke_hex = "#B67363" if self._hover_progress > 0.5 else "#C5CEC0"
                art_opacity = 0.72 - 0.22 * self._hover_progress

            painter.save()
            painter.setClipPath(clip_path)
            painter.fillPath(clip_path, bg)

            art_w, art_h = 68, 52
            art_pm = render_category_corner_artwork(self.category, stroke_hex, art_w, art_h)
            painter.setOpacity(art_opacity)
            if self.layoutDirection() == Qt.RightToLeft:
                flipped = art_pm.transformed(QTransform().scale(-1.0, 1.0))
                painter.drawPixmap(-8, self.height() - art_h + 6, flipped)
            else:
                painter.drawPixmap(self.width() - art_w + 8, self.height() - art_h + 6, art_pm)
            painter.restore()

            pen_w = 2.0 if self.hasFocus() else 1.3
            painter.setPen(QPen(QColor(t.primary if self.hasFocus() else border), pen_w))
            painter.drawPath(clip_path)
        finally:
            if painter.isActive():
                painter.end()


class ProductQtyControl(QWidget):
    """Animated dynamic quantity control: renders [ + ] when 0 and [ - ] N [ + ] when >= 1."""

    increment_clicked = Signal()
    decrement_clicked = Signal()

    def __init__(
        self, accent_color: str, product_name: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._accent_color = accent_color
        self._product_name = product_name
        self._qty = 0
        self._display_qty = 0
        self._expand = 0.0
        self._pulse = 0.0
        self._plus_hover = 0.0
        self._minus_hover = 0.0
        self._plus_press = 0.0
        self._minus_press = 0.0
        self._hovered_zone = ""
        self._pressed_zone = ""

        self.setFixedSize(86, 32)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self._update_accessibility()

        self._expand_anim = QPropertyAnimation(self, b"expandProgress", self)
        self._expand_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._pulse_anim = QPropertyAnimation(self, b"pulseProgress", self)
        self._pulse_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._plus_hover_anim = QPropertyAnimation(self, b"plusHover", self)
        self._plus_hover_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._minus_hover_anim = QPropertyAnimation(self, b"minusHover", self)
        self._minus_hover_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._plus_press_anim = QPropertyAnimation(self, b"plusPress", self)
        self._plus_press_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._minus_press_anim = QPropertyAnimation(self, b"minusPress", self)
        self._minus_press_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    @property
    def quantity(self) -> int:
        return self._qty

    def getExpandProgress(self) -> float:
        return self._expand

    def setExpandProgress(self, v: float) -> None:
        self._expand = max(0.0, min(1.0, float(v)))
        self.update()

    expandProgress = Property(float, getExpandProgress, setExpandProgress)

    def getPulseProgress(self) -> float:
        return self._pulse

    def setPulseProgress(self, v: float) -> None:
        self._pulse = max(0.0, min(1.0, float(v)))
        self.update()

    pulseProgress = Property(float, getPulseProgress, setPulseProgress)

    def getPlusHover(self) -> float:
        return self._plus_hover

    def setPlusHover(self, v: float) -> None:
        self._plus_hover = max(0.0, min(1.0, float(v)))
        self.update()

    plusHover = Property(float, getPlusHover, setPlusHover)

    def getMinusHover(self) -> float:
        return self._minus_hover

    def setMinusHover(self, v: float) -> None:
        self._minus_hover = max(0.0, min(1.0, float(v)))
        self.update()

    minusHover = Property(float, getMinusHover, setMinusHover)

    def getPlusPress(self) -> float:
        return self._plus_press

    def setPlusPress(self, v: float) -> None:
        self._plus_press = max(0.0, min(1.0, float(v)))
        self.update()

    plusPress = Property(float, getPlusPress, setPlusPress)

    def getMinusPress(self) -> float:
        return self._minus_press

    def setMinusPress(self, v: float) -> None:
        self._minus_press = max(0.0, min(1.0, float(v)))
        self.update()

    minusPress = Property(float, getMinusPress, setMinusPress)

    def _run_anim(
        self, anim: QPropertyAnimation, start: float, end: float, duration_ms: int
    ) -> None:
        if not _qt_is_valid(self) or not _qt_is_valid(anim):
            return
        anim.stop()
        if is_reduced_motion():
            anim.setDuration(0)
        else:
            anim.setDuration(duration_ms)
        anim.setStartValue(float(start))
        anim.setEndValue(float(end))
        anim.start()

    def stop_animations(self) -> None:
        for anim in (
            getattr(self, "_expand_anim", None),
            getattr(self, "_pulse_anim", None),
            getattr(self, "_plus_hover_anim", None),
            getattr(self, "_minus_hover_anim", None),
            getattr(self, "_plus_press_anim", None),
            getattr(self, "_minus_press_anim", None),
        ):
            if anim is not None and _qt_is_valid(anim):
                anim.stop()
        clear_widget_animations(self)

    def _update_accessibility(self) -> None:
        if self._qty <= 0:
            self.setAccessibleName(tr("qty.a11y_add", product=self._product_name))
            self.setToolTip(tr("qty.tip_add", product=self._product_name))
        else:
            self.setAccessibleName(
                tr("qty.a11y_name", product=self._product_name, qty=self._qty)
            )
            self.setToolTip(
                tr("qty.tip_adjust", product=self._product_name, qty=self._qty)
            )

    def set_product_name(self, name: str) -> None:
        self._product_name = name
        self._update_accessibility()

    def retranslate_ui(self) -> None:
        self._update_accessibility()
        self.update()

    def _is_rtl_dir(self) -> bool:
        return self.layoutDirection() == Qt.RightToLeft or is_rtl()

    def set_accent_color(self, accent_color: str) -> None:
        self._accent_color = accent_color
        self.update()

    def set_quantity(self, qty: int, *, animate: bool = True) -> None:
        clean = max(0, int(qty))
        prev = self._qty
        self._qty = clean
        if clean > 0:
            self._display_qty = clean
        self._update_accessibility()

        target_expand = 1.0 if clean > 0 else 0.0
        if not animate or is_reduced_motion():
            self._expand_anim.stop()
            self._pulse_anim.stop()
            self._expand = target_expand
            self._pulse = 0.0
            if clean == 0:
                self._display_qty = 0
            self.update()
            return

        if abs(target_expand - self._expand) > 0.01:
            self._run_anim(self._expand_anim, self._expand, target_expand, 165)

        if clean > 0 and clean != prev:
            self._run_anim(self._pulse_anim, 1.0, 0.0, 150)
        elif clean == 0 and prev > 0:
            self._run_anim(self._pulse_anim, 0.45, 0.0, 140)
        else:
            self.update()

    def click_plus(self) -> None:
        if not is_reduced_motion():
            self._run_anim(self._plus_press_anim, 1.0, 0.0, 130)
        self.increment_clicked.emit()

    def click_minus(self) -> None:
        if self._qty <= 0:
            return
        if not is_reduced_motion():
            self._run_anim(self._minus_press_anim, 1.0, 0.0, 130)
        self.decrement_clicked.emit()

    def _hit_zone(self, pos: QPoint) -> str:
        if not self.rect().contains(pos):
            return ""
        rtl = self._is_rtl_dir()
        if self._qty <= 0:
            if rtl:
                return "plus" if pos.x() <= self.height() + 4 else ""
            return "plus" if pos.x() >= self.width() - self.height() - 4 else ""
        if rtl:
            if pos.x() < 31:
                return "plus"
            if pos.x() >= self.width() - 31:
                return "minus"
            return ""
        if pos.x() < 31:
            return "minus"
        if pos.x() >= self.width() - 31:
            return "plus"
        return ""

    def _set_hover_zone(self, zone: str) -> None:
        if zone == self._hovered_zone:
            return
        self._hovered_zone = zone
        self._run_anim(self._plus_hover_anim, self._plus_hover, 1.0 if zone == "plus" else 0.0, 140)
        self._run_anim(self._minus_hover_anim, self._minus_hover, 1.0 if zone == "minus" else 0.0, 140)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self._set_hover_zone(self._hit_zone(event.position().toPoint()))
        super().mouseMoveEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        self._set_hover_zone("")
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            zone = self._hit_zone(event.position().toPoint())
            if zone:
                self._pressed_zone = zone
                if zone == "plus":
                    self._run_anim(self._plus_press_anim, self._plus_press, 1.0, 90)
                elif zone == "minus":
                    self._run_anim(self._minus_press_anim, self._minus_press, 1.0, 90)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton and self._pressed_zone:
            pressed = self._pressed_zone
            self._pressed_zone = ""
            self._run_anim(self._plus_press_anim, self._plus_press, 0.0, 130)
            self._run_anim(self._minus_press_anim, self._minus_press, 0.0, 130)
            release_zone = self._hit_zone(event.position().toPoint())
            if release_zone == pressed:
                if pressed == "plus":
                    self.increment_clicked.emit()
                elif pressed == "minus" and self._qty > 0:
                    self.decrement_clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        rtl = self._is_rtl_dir()
        inc_arrow = Qt.Key_Left if rtl else Qt.Key_Right
        dec_arrow = Qt.Key_Right if rtl else Qt.Key_Left
        if key in (Qt.Key_Plus, Qt.Key_Equal, Qt.Key_Up, inc_arrow, Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.click_plus()
            event.accept()
            return
        if key in (Qt.Key_Minus, Qt.Key_Down, dec_arrow) and self._qty > 0:
            self.click_minus()
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        try:
            self._paint_control(painter)
        finally:
            if painter.isActive():
                painter.end()

    def _paint_control(self, painter: QPainter) -> None:
        t = get_tokens(self._accent_color)
        painter.setRenderHint(QPainter.Antialiasing, True)

        w = float(self.width())
        h = float(self.height())
        exp = self._expand
        rtl = self._is_rtl_dir()

        # Collapse state: clean circular [ + ] button on trailing edge (right in LTR, left in RTL)
        if exp <= 0.01:
            scale_in = 1.4 * self._plus_press
            side = (h - 2.0) - 2.0 * scale_in
            cx = (1.0 + scale_in) if rtl else (w - (h - 2.0) - 1.0 + scale_in)
            cy = 1.0 + scale_in
            rect = QRectF(cx, cy, side, side)

            bg = _mix_color(QColor(t.surface), QColor(t.primary), self._plus_hover)
            if self._plus_press > 0:
                bg = _mix_color(bg, QColor(t.primary_pressed), self._plus_press * 0.45)
            border = _mix_color(QColor(t.border_strong), QColor(t.primary), self._plus_hover)
            if self.hasFocus():
                border = QColor(t.primary)

            painter.setBrush(bg)
            painter.setPen(QPen(border, 1.8 if self.hasFocus() else 1.3))
            painter.drawEllipse(rect)

            icon_color = _mix_color(QColor(t.text), QColor(t.on_primary), self._plus_hover)
            painter.setPen(QPen(icon_color, 1.9, Qt.SolidLine, Qt.RoundCap))
            mid = rect.center()
            arm = 4.6
            painter.drawLine(QPointF_safe(mid.x() - arm, mid.y()), QPointF_safe(mid.x() + arm, mid.y()))
            painter.drawLine(QPointF_safe(mid.x(), mid.y() - arm), QPointF_safe(mid.x(), mid.y() + arm))
            return

        # Expanded state: smooth Boutique pill ([ - ] N [ + ] in LTR, mirrored [ + ] N [ - ] in RTL)
        pill_w = h + (w - h) * exp
        pill_left = 1.0 if rtl else (w - pill_w + 1.0)
        pill_rect = QRectF(pill_left, 1.0, pill_w - 2.0, h - 2.0)
        clip_path = QPainterPath()
        radius = (h - 2.0) * 0.5
        clip_path.addRoundedRect(pill_rect, radius, radius)

        base_pill_bg = _mix_color(QColor(t.surface), QColor(t.sage), 0.72 * exp)
        if self._pulse > 0:
            base_pill_bg = _mix_color(base_pill_bg, QColor(t.secondary_tint), self._pulse * 0.65)
        pill_border = _mix_color(QColor(t.border_strong), QColor(t.primary), 0.65 * exp)
        if self.hasFocus():
            pill_border = QColor(t.primary)

        painter.save()
        painter.setClipPath(clip_path)
        painter.fillPath(clip_path, base_pill_bg)

        # Minus circular hit control (left in LTR, right in RTL)
        if exp > 0.35:
            m_inset = 1.1 * self._minus_press
            m_side = (h - 8.0) - 2.0 * m_inset
            m_left = (pill_rect.right() - 3.0 - m_side + m_inset) if rtl else (pill_rect.left() + 3.0 + m_inset)
            m_rect = QRectF(
                m_left,
                (h - m_side) * 0.5,
                m_side,
                m_side,
            )
            m_bg = _mix_color(QColor("#FFFFFF"), QColor(t.secondary), self._minus_hover)
            if self._minus_press > 0:
                m_bg = _mix_color(m_bg, QColor(t.secondary), 0.65)
            painter.setOpacity(min(1.0, (exp - 0.35) / 0.65))
            painter.setBrush(m_bg)
            painter.setPen(QPen(_mix_color(QColor(t.border), QColor(t.secondary), self._minus_hover), 1.0))
            painter.drawEllipse(m_rect)

            mc = m_rect.center()
            painter.setPen(QPen(QColor(t.text), 1.85, Qt.SolidLine, Qt.RoundCap))
            painter.drawLine(QPointF_safe(mc.x() - 3.8, mc.y()), QPointF_safe(mc.x() + 3.8, mc.y()))
            painter.setOpacity(1.0)

        # Center animated quantity readout
        if exp > 0.25 and self._display_qty > 0:
            qty_str = str(self._display_qty)
            font = QFont(self.font())
            font.setPointSize(10 if self._display_qty < 10 else 9)
            font.setBold(True)
            painter.setFont(font)
            painter.setOpacity(min(1.0, (exp - 0.25) / 0.55))
            center_x = pill_rect.center().x()
            center_y = pill_rect.center().y()
            painter.save()
            painter.translate(center_x, center_y)
            sc = 1.0 + 0.14 * self._pulse
            painter.scale(sc, sc)
            painter.setPen(QColor(t.text))
            painter.drawText(QRectF(-15.0, -11.0, 30.0, 22.0), Qt.AlignCenter, qty_str)
            painter.restore()
            painter.setOpacity(1.0)

        # Plus accent circle (right in LTR, left in RTL)
        p_inset = 1.2 * self._plus_press
        p_side = (h - 8.0) + 6.0 * (1.0 - exp) - 2.0 * p_inset
        p_left = (pill_rect.left() + 3.0 + p_inset) if rtl else (pill_rect.right() - 3.0 - p_side + p_inset)
        p_rect = QRectF(
            p_left,
            (h - p_side) * 0.5,
            p_side,
            p_side,
        )
        p_bg = _mix_color(QColor(t.primary), QColor(t.primary_hover), self._plus_hover)
        if self._plus_press > 0:
            p_bg = _mix_color(p_bg, QColor(t.primary_pressed), self._plus_press * 0.6)
        painter.setBrush(p_bg)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(p_rect)

        pc = p_rect.center()
        painter.setPen(QPen(QColor(t.on_primary), 1.85, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(QPointF_safe(pc.x() - 4.0, pc.y()), QPointF_safe(pc.x() + 4.0, pc.y()))
        painter.drawLine(QPointF_safe(pc.x(), pc.y() - 4.0), QPointF_safe(pc.x(), pc.y() + 4.0))
        painter.restore()

        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(pill_border, 1.8 if self.hasFocus() else 1.2))
        painter.drawPath(clip_path)


def QPointF_safe(x: float, y: float):
    from PySide6.QtCore import QPointF

    return QPointF(x, y)


class CozyProductCard(QFrame):
    """Boutique café product card with studio image, full-width name, price, and [ - ] N [ + ] control."""

    increment_requested = Signal()
    decrement_requested = Signal()
    clicked = Signal()

    def __init__(
        self,
        product_id: int,
        name: str,
        category: str,
        price_text: str,
        image_filename: str,
        accent_color: str,
        parent: QWidget | None = None,
        *,
        asset_lookup_name: str = "",
    ) -> None:
        super().__init__(parent)
        self.product_id = product_id
        self._name = name
        self._asset_lookup_name = (asset_lookup_name or "").strip() or name
        self._image_filename = image_filename
        self._category = category
        self._accent_color = accent_color
        self._last_stage_w = 0
        self._pressed = False
        self._quantity = 0

        self.setProperty("class", "CozyProductCard")
        self.setProperty("active_cart", "false")
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumWidth(148)
        self.setFixedHeight(196)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAccessibleName(
            tr("product_card.a11y", name=name, category=tr_category(category), price=price_text)
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(11, 9, 11, 10)
        layout.setSpacing(4)

        # Clean open product photo stage directly on the white card canvas
        self.photo_lbl = QLabel()
        self.photo_lbl.setFixedHeight(98)
        self.photo_lbl.setAlignment(Qt.AlignCenter)
        self.photo_lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.photo_lbl.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.photo_lbl.setStyleSheet("background: transparent; border: none;")
        layout.addWidget(self.photo_lbl)

        lead_align = (Qt.AlignRight if is_rtl() else Qt.AlignLeft) | Qt.AlignVCenter | Qt.AlignAbsolute
        # Full-width product title (never crowded by quantity stepper!)
        self.name_lbl = QLabel(name)
        self.name_lbl.setWordWrap(True)
        self.name_lbl.setAlignment(lead_align)
        self.name_lbl.setMinimumHeight(34)
        self.name_lbl.setMaximumHeight(36)
        self.name_lbl.setStyleSheet("font-size: 12.5px; font-weight: 700; color: #202820;")
        self.name_lbl.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(self.name_lbl)

        # Bottom Row: Price on Leading side + Dynamic [ + ] / [ - ] N [ + ] Control on Trailing side
        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 1, 0, 0)
        bottom.setSpacing(6)

        self.price_lbl = QLabel(price_text)
        self.price_lbl.setProperty("class", "ProductCardPrice")
        self.price_lbl.setAlignment(lead_align)
        self.price_lbl.setAttribute(Qt.WA_TransparentForMouseEvents)

        self.qty_control = ProductQtyControl(accent_color, name)
        self.qty_control.increment_clicked.connect(self._on_inc)
        self.qty_control.decrement_clicked.connect(self._on_dec)

        bottom.addWidget(self.price_lbl, 1)
        bottom.addWidget(self.qty_control, 0, Qt.AlignVCenter)
        layout.addLayout(bottom)

        self._update_banner_pixmap(164)

    @property
    def quantity(self) -> int:
        return self._quantity

    def _on_inc(self) -> None:
        self.increment_requested.emit()
        self.clicked.emit()

    def _on_dec(self) -> None:
        self.decrement_requested.emit()

    def stop_animations(self) -> None:
        qty_ctrl = getattr(self, "qty_control", None)
        if qty_ctrl is not None and _qt_is_valid(qty_ctrl):
            qty_ctrl.stop_animations()
        clear_widget_animations(self)

    def set_cart_quantity(self, quantity: int, *, animate: bool = True) -> None:
        if not _qt_is_valid(self):
            return
        clean = max(0, int(quantity))
        self._quantity = clean
        if _qt_is_valid(self.qty_control):
            self.qty_control.set_quantity(clean, animate=animate)
        new_active = "true" if clean > 0 else "false"
        if self.property("active_cart") != new_active:
            self.setProperty("active_cart", new_active)
            self.style().unpolish(self)
            self.style().polish(self)
        self._sync_accessible_name()

    def retranslate_ui(
        self,
        price_text: str | None = None,
        *,
        name_text: str | None = None,
        asset_lookup_name: str | None = None,
    ) -> None:
        if not _qt_is_valid(self):
            return
        lead_align = (Qt.AlignRight if is_rtl() else Qt.AlignLeft) | Qt.AlignVCenter | Qt.AlignAbsolute
        self.name_lbl.setAlignment(lead_align)
        self.price_lbl.setAlignment(lead_align)
        if name_text is not None:
            self._name = name_text
            self.name_lbl.setText(name_text)
            self.qty_control.set_product_name(name_text)
        if asset_lookup_name is not None:
            clean_lookup = asset_lookup_name.strip() or self._name
            if clean_lookup != self._asset_lookup_name:
                self._asset_lookup_name = clean_lookup
                self._last_stage_w = 0
                self._update_banner_pixmap(max(116, self.width() - 22))
        if price_text is not None:
            self.price_lbl.setText(price_text)
        self.qty_control.retranslate_ui()
        self._sync_accessible_name()

    def _sync_accessible_name(self) -> None:
        cat_txt = tr_category(self._category)
        price_txt = self.price_lbl.text()
        if self._quantity > 0:
            self.setAccessibleName(
                tr(
                    "product_card.a11y_in_cart",
                    name=self._name,
                    category=cat_txt,
                    price=price_txt,
                    qty=self._quantity,
                )
            )
        else:
            self.setAccessibleName(
                tr("product_card.a11y", name=self._name, category=cat_txt, price=price_txt)
            )

    def _update_banner_pixmap(self, target_w: int) -> None:
        w = max(116, int(target_w))
        if abs(w - self._last_stage_w) < 3:
            return
        self._last_stage_w = w
        pm = build_product_pixmap(
            self._image_filename,
            self._category,
            w,
            96,
            accent_color=self._accent_color,
            radius=10,
            product_name=self._asset_lookup_name,
            contain_transparent=True,
        )
        self.photo_lbl.setPixmap(pm)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if not _qt_is_valid(self):
            return
        inner_w = self.width() - 22
        if inner_w > 40:
            self._update_banner_pixmap(inner_w)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            pos_in_ctrl = self.qty_control.mapFrom(self, event.position().toPoint())
            if not self.qty_control.rect().contains(pos_in_ctrl):
                self._pressed = True
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton and self._pressed:
            self._pressed = False
            if self.rect().contains(event.position().toPoint()):
                self._on_inc()
            event.accept()
            return
        self._pressed = False
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space, Qt.Key_Plus, Qt.Key_Equal):
            self._on_inc()
            event.accept()
            return
        if key == Qt.Key_Minus and self._quantity > 0:
            self._on_dec()
            event.accept()
            return
        super().keyPressEvent(event)


class CartItemRowWidget(QFrame):
    """Boutique shopping cart line with product thumbnail, stacked price/stepper, and remove icon."""

    decrement_clicked = Signal()
    increment_clicked = Signal()
    remove_clicked = Signal()
    edit_modifiers_clicked = Signal()

    def __init__(
        self,
        name: str,
        unit_price_text: str,
        line_total_text: str,
        quantity: int,
        image_filename: str,
        category: str,
        accent_color: str,
        parent: QWidget | None = None,
        *,
        asset_lookup_name: str = "",
        modifiers_text: str = "",
        has_modifiers: bool = False,
    ) -> None:
        super().__init__(parent)
        self._name = name
        self._asset_lookup_name = (asset_lookup_name or "").strip() or name
        self._qty = quantity
        self._unit_price_text = unit_price_text
        self._modifiers_text = modifiers_text
        self._has_modifiers = bool(has_modifiers)
        self.setProperty("class", "CartItemCard")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(9, 8, 9, 8)
        layout.setSpacing(8)

        thumb_wrap = QFrame()
        thumb_wrap.setFixedSize(44, 44)
        thumb_wrap.setStyleSheet(
            "background-color: #F7F7E9; border: 1px solid #D8DED3; border-radius: 10px;"
        )
        tw_layout = QHBoxLayout(thumb_wrap)
        tw_layout.setContentsMargins(3, 3, 3, 3)
        thumb = SquareThumbLabel(size=36, radius=7)
        thumb.set_product_image(
            image_filename,
            category,
            accent_color,
            product_name=self._asset_lookup_name,
        )
        tw_layout.addWidget(thumb)
        layout.addWidget(thumb_wrap)

        lead_align = (Qt.AlignRight if is_rtl() else Qt.AlignLeft) | Qt.AlignVCenter | Qt.AlignAbsolute
        trail_align = (Qt.AlignLeft if is_rtl() else Qt.AlignRight) | Qt.AlignVCenter | Qt.AlignAbsolute

        info_col = QVBoxLayout()
        info_col.setContentsMargins(0, 0, 0, 0)
        info_col.setSpacing(2)
        self.title_lbl = QLabel(name)
        self.title_lbl.setWordWrap(True)
        self.title_lbl.setAlignment(lead_align)
        self.title_lbl.setStyleSheet("font-size: 12.5px; font-weight: 700; color: #202820;")
        self.modifiers_lbl = QLabel(self._modifiers_text)
        self.modifiers_lbl.setWordWrap(True)
        self.modifiers_lbl.setAlignment(lead_align)
        self.modifiers_lbl.setStyleSheet("font-size: 11px; font-weight: 600; color: #005B37;")
        self.modifiers_lbl.setVisible(bool(self._modifiers_text))
        self.unit_lbl = QLabel(tr("register.item_each", price=unit_price_text))
        self.unit_lbl.setProperty("class", "MutedLabel")
        self.unit_lbl.setAlignment(lead_align)
        info_col.addWidget(self.title_lbl)
        info_col.addWidget(self.modifiers_lbl)
        info_col.addWidget(self.unit_lbl)
        layout.addLayout(info_col, 1)

        right_col = QVBoxLayout()
        right_col.setContentsMargins(0, 0, 0, 0)
        right_col.setSpacing(5)
        right_col.setAlignment(Qt.AlignTrailing | Qt.AlignVCenter)

        top_right = QHBoxLayout()
        top_right.setContentsMargins(0, 0, 0, 0)
        top_right.setSpacing(6)
        self.total_lbl = QLabel(line_total_text)
        self.total_lbl.setAlignment(trail_align)
        self.total_lbl.setStyleSheet("font-size: 13px; font-weight: 700; color: #202820;")

        self.del_btn = QPushButton()
        self.del_btn.setProperty("class", "RemoveRowBtn")
        self.del_btn.setFixedSize(22, 22)
        self.del_btn.setIcon(get_svg_icon("trash", "#B23A3A", 11))
        self.del_btn.setIconSize(QSize(11, 11))
        self.del_btn.setToolTip(tr("cart_row.remove_item_tip", name=name))
        self.del_btn.clicked.connect(self.remove_clicked.emit)

        top_right.addWidget(self.total_lbl)
        top_right.addWidget(self.del_btn)
        right_col.addLayout(top_right)

        stepper_pill = QFrame()
        stepper_pill.setProperty("class", "QtyStepperPill")
        stepper = QHBoxLayout(stepper_pill)
        stepper.setContentsMargins(3, 1, 3, 1)
        stepper.setSpacing(2)

        self.dec_btn = QPushButton()
        self.dec_btn.setProperty("class", "QtyMiniBtn")
        self.dec_btn.setIcon(get_svg_icon("minus", "#202820", 10))
        self.dec_btn.setIconSize(QSize(10, 10))
        self.dec_btn.setToolTip(tr("cart_row.decrease_tip"))
        self.dec_btn.setAccessibleName(tr("cart_row.dec_a11y"))
        self.dec_btn.clicked.connect(self.decrement_clicked.emit)

        self.qty_lbl = QLabel(str(quantity))
        self.qty_lbl.setAlignment(Qt.AlignCenter)
        self.qty_lbl.setMinimumWidth(18)
        self.qty_lbl.setStyleSheet("font-size: 12.5px; font-weight: 700; color: #202820;")

        self.inc_btn = QPushButton()
        self.inc_btn.setProperty("class", "QtyMiniBtn")
        self.inc_btn.setIcon(get_svg_icon("plus", "#202820", 10))
        self.inc_btn.setIconSize(QSize(10, 10))
        self.inc_btn.setToolTip(tr("cart_row.increase_tip"))
        self.inc_btn.setAccessibleName(tr("cart_row.inc_a11y"))
        self.inc_btn.clicked.connect(self.increment_clicked.emit)

        stepper.addWidget(self.dec_btn)
        stepper.addWidget(self.qty_lbl)
        stepper.addWidget(self.inc_btn)

        self.edit_mods_btn = QPushButton()
        self.edit_mods_btn.setProperty("class", "MiniEditBtn")
        self.edit_mods_btn.setFixedSize(24, 24)
        self.edit_mods_btn.setIcon(get_svg_icon("edit", "#005B37", 11))
        self.edit_mods_btn.setIconSize(QSize(11, 11))
        self.edit_mods_btn.setCursor(Qt.PointingHandCursor)
        self.edit_mods_btn.setToolTip(tr("register.cart_edit_mods_tip", name=name))
        self.edit_mods_btn.setVisible(self._has_modifiers)
        self.edit_mods_btn.clicked.connect(self.edit_modifiers_clicked.emit)

        step_align = QHBoxLayout()
        step_align.setContentsMargins(0, 0, 0, 0)
        step_align.setSpacing(5)
        step_align.addStretch(1)
        step_align.addWidget(self.edit_mods_btn)
        step_align.addWidget(stepper_pill)
        right_col.addLayout(step_align)

        layout.addLayout(right_col)

    def stop_animations(self) -> None:
        clear_widget_animations(self)

    def update_values(
        self,
        quantity: int,
        line_total_text: str,
        unit_price_text: str,
        *,
        modifiers_text: str | None = None,
        has_modifiers: bool | None = None,
        pulse: bool = False,
    ) -> None:
        if not _qt_is_valid(self):
            return
        self._qty = quantity
        self._unit_price_text = unit_price_text
        self.qty_lbl.setText(str(quantity))
        self.total_lbl.setText(line_total_text)
        self.unit_lbl.setText(tr("register.item_each", price=unit_price_text))
        if modifiers_text is not None:
            self._modifiers_text = modifiers_text
            self.modifiers_lbl.setText(modifiers_text)
            self.modifiers_lbl.setVisible(bool(modifiers_text))
        if has_modifiers is not None:
            self._has_modifiers = bool(has_modifiers)
            self.edit_mods_btn.setVisible(self._has_modifiers)
        if pulse:
            animate_flash_highlight(self, duration_ms=145, dip_opacity=0.76)

    def retranslate_ui(
        self,
        unit_price_text: str,
        line_total_text: str,
        *,
        name_text: str | None = None,
        modifiers_text: str | None = None,
    ) -> None:
        if not _qt_is_valid(self):
            return
        lead_align = (Qt.AlignRight if is_rtl() else Qt.AlignLeft) | Qt.AlignVCenter | Qt.AlignAbsolute
        trail_align = (Qt.AlignLeft if is_rtl() else Qt.AlignRight) | Qt.AlignVCenter | Qt.AlignAbsolute
        if name_text is not None:
            self._name = name_text
            self.title_lbl.setText(name_text)
        if modifiers_text is not None:
            self._modifiers_text = modifiers_text
            self.modifiers_lbl.setText(modifiers_text)
            self.modifiers_lbl.setVisible(bool(modifiers_text))
        self.title_lbl.setAlignment(lead_align)
        self.modifiers_lbl.setAlignment(lead_align)
        self.unit_lbl.setAlignment(lead_align)
        self.total_lbl.setAlignment(trail_align)
        self._unit_price_text = unit_price_text
        self.unit_lbl.setText(tr("register.item_each", price=unit_price_text))
        self.total_lbl.setText(line_total_text)
        self.del_btn.setToolTip(tr("cart_row.remove_item_tip", name=self._name))
        self.edit_mods_btn.setToolTip(tr("register.cart_edit_mods_tip", name=self._name))
        self.dec_btn.setToolTip(tr("cart_row.decrease_tip"))
        self.dec_btn.setAccessibleName(tr("cart_row.dec_a11y"))
        self.inc_btn.setToolTip(tr("cart_row.increase_tip"))
        self.inc_btn.setAccessibleName(tr("cart_row.inc_a11y"))


class EmptyStateWidget(QFrame):
    """Friendly illustrated empty state replacing blank white areas."""

    action_triggered = Signal()

    def __init__(
        self,
        illustration_kind: str,
        title: str,
        description: str,
        action_label: str = "",
        accent_color: str = "#005B37",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setStyleSheet("background: transparent; border: none;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(7)
        layout.setAlignment(Qt.AlignCenter)

        self.art_lbl = QLabel()
        self.art_lbl.setAlignment(Qt.AlignCenter)
        self.art_lbl.setPixmap(
            render_empty_state_illustration(illustration_kind, accent_color=accent_color, size=74)
        )
        layout.addWidget(self.art_lbl)

        self.title_lbl = QLabel(title)
        self.title_lbl.setAlignment(Qt.AlignCenter)
        self.title_lbl.setStyleSheet("font-size: 14px; font-weight: 700; color: #202820;")
        layout.addWidget(self.title_lbl)

        self.desc_lbl = QLabel(description)
        self.desc_lbl.setWordWrap(True)
        self.desc_lbl.setAlignment(Qt.AlignCenter)
        self.desc_lbl.setMaximumWidth(300)
        self.desc_lbl.setProperty("class", "MutedLabel")
        layout.addWidget(self.desc_lbl)

        self.action_btn = QPushButton(action_label)
        self.action_btn.setProperty("role", "secondary")
        self.action_btn.setVisible(bool(action_label))
        self.action_btn.clicked.connect(self.action_triggered.emit)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(self.action_btn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        self._kind = illustration_kind

    def refresh_accent(self, accent_color: str) -> None:
        self.art_lbl.setPixmap(
            render_empty_state_illustration(self._kind, accent_color=accent_color, size=74)
        )

    def set_texts(self, title: str, description: str, action_label: str = "") -> None:
        self.title_lbl.setText(title)
        self.desc_lbl.setText(description)
        self.action_btn.setText(action_label)
        self.action_btn.setVisible(bool(action_label))


class GalleryThumbCard(QFrame):
    """Clickable thumbnail item in the Smart Product Image Helper gallery."""

    selected = Signal(object)

    def __init__(self, item, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.item = item
        self.setProperty("class", "GalleryThumbCard")
        self.setProperty("selected", "false")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(106, 102)
        self.setToolTip(f"{item.title}\n{item.attribution}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 5)
        layout.setSpacing(4)

        self.img_lbl = QLabel(tr("product_editor.thumb_loading"))
        self.img_lbl.setFixedSize(92, 68)
        self.img_lbl.setAlignment(Qt.AlignCenter)
        self.img_lbl.setStyleSheet(
            "background-color: #F7F7E9; border-radius: 6px; font-size: 10px; color: #5E6B5F;"
        )
        self.img_lbl.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(self.img_lbl)

        badge_row = QHBoxLayout()
        badge_row.setContentsMargins(0, 0, 0, 0)
        badge = QLabel(item.badge_label)
        badge.setStyleSheet(
            "background-color: #FDEEE9; color: #7A3E31; font-size: 10px; "
            "font-weight: 700; padding: 1px 6px; border-radius: 5px;"
        )
        badge.setAttribute(Qt.WA_TransparentForMouseEvents)
        badge_row.addWidget(badge)
        badge_row.addStretch(1)
        layout.addLayout(badge_row)

    def set_thumbnail_bytes(self, raw_bytes: bytes) -> None:
        pm = QPixmap()
        if pm.loadFromData(raw_bytes) and not pm.isNull():
            self.img_lbl.setText("")
            self.img_lbl.setPixmap(clip_pixmap_rounded(pm, 92, 68, radius=6, scale=2))

    def set_selected(self, is_selected: bool) -> None:
        self.setProperty("selected", "true" if is_selected else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.selected.emit(self.item)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class ToastBanner(QFrame):
    """Non-intrusive confirmation pill anchored cleanly above the status bar."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("ToastBanner")
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._fade_anim: QPropertyAnimation | None = None
        self.hide()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 3, 11, 3)
        layout.setSpacing(6)

        self.icon_lbl = QLabel()
        self.icon_lbl.setPixmap(get_svg_pixmap("check", "#DCE8D7", 12))
        self.text_lbl = QLabel("")
        self.text_lbl.setObjectName("ToastBannerLabel")
        layout.addWidget(self.icon_lbl)
        layout.addWidget(self.text_lbl)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fade_out)

    def _stop_anim(self) -> None:
        if self._fade_anim is not None:
            if _qt_is_valid(self._fade_anim):
                try:
                    self._fade_anim.stop()
                except RuntimeError:
                    pass
            self._fade_anim = None

    def show_message(self, message: str, duration_ms: int = 2200) -> None:
        if not _qt_is_valid(self):
            return
        self._stop_anim()
        self.text_lbl.setText(message)
        self.adjustSize()
        self.reposition()
        self.show()
        self.raise_()

        if is_reduced_motion():
            self.setGraphicsEffect(None)
        else:
            effect = QGraphicsOpacityEffect(self)
            effect.setOpacity(0.0)
            self.setGraphicsEffect(effect)
            anim = QPropertyAnimation(effect, b"opacity", self)
            anim.setDuration(150)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)

            def _clear_effect() -> None:
                if _qt_is_valid(self):
                    self.setGraphicsEffect(None)

            anim.finished.connect(_clear_effect)
            self._fade_anim = anim
            anim.start()

        self._timer.start(duration_ms)

    def _fade_out(self) -> None:
        if not _qt_is_valid(self):
            return
        self._stop_anim()
        if is_reduced_motion() or not self.isVisible():
            self.setGraphicsEffect(None)
            self.hide()
            return
        effect = QGraphicsOpacityEffect(self)
        effect.setOpacity(1.0)
        self.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", self)
        anim.setDuration(160)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        def _done() -> None:
            if not _qt_is_valid(self):
                return
            self.hide()
            self.setGraphicsEffect(None)
            self._fade_anim = None

        anim.finished.connect(_done)
        self._fade_anim = anim
        anim.start()

    def reposition(self) -> None:
        p = self.parentWidget()
        if p is None:
            return
        rtl = p.layoutDirection() == Qt.RightToLeft or is_rtl()
        x = 16 if rtl else max(16, p.width() - self.width() - 16)
        self.move(
            x,
            max(8, p.height() - self.height() - 32),
        )
