from __future__ import annotations

from shiboken6 import isValid as _qt_is_valid

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
)
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QGraphicsOpacityEffect, QPushButton, QStackedWidget, QWidget

_REDUCED_MOTION = False


def is_reduced_motion() -> bool:
    return _REDUCED_MOTION


def set_reduced_motion(enabled: bool) -> None:
    global _REDUCED_MOTION
    _REDUCED_MOTION = bool(enabled)


def _stop_registered_anim(target: QObject, attr: str) -> None:
    if target is None or not _qt_is_valid(target):
        return
    prev = getattr(target, attr, None)
    if prev is not None:
        if _qt_is_valid(prev):
            try:
                prev.stop()
            except RuntimeError:
                pass
        setattr(target, attr, None)


def clear_widget_animations(widget: QWidget) -> None:
    """Stop tracked fade/flash animations and detach graphics effects before deletion."""
    if widget is None or not _qt_is_valid(widget):
        return
    _stop_registered_anim(widget, "_active_fade_anim")
    _stop_registered_anim(widget, "_active_flash_anim")
    try:
        if widget.graphicsEffect() is not None:
            widget.setGraphicsEffect(None)
    except RuntimeError:
        pass


def animate_fade_in(
    widget: QWidget,
    *,
    duration_ms: int = 150,
    start_opacity: float = 0.45,
) -> None:
    """Smoothly fade a visible child widget in, cleaning up graphics effects afterward."""
    if widget is None or not _qt_is_valid(widget):
        return
    _stop_registered_anim(widget, "_active_fade_anim")
    if is_reduced_motion() or not widget.isVisible():
        widget.setGraphicsEffect(None)
        return

    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(start_opacity)
    widget.setGraphicsEffect(effect)

    anim = QPropertyAnimation(effect, b"opacity", widget)
    anim.setDuration(max(80, min(220, int(duration_ms))))
    anim.setStartValue(float(start_opacity))
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def _cleanup() -> None:
        if not _qt_is_valid(widget):
            return
        try:
            if _qt_is_valid(effect) and widget.graphicsEffect() is effect:
                widget.setGraphicsEffect(None)
        except RuntimeError:
            pass
        if getattr(widget, "_active_fade_anim", None) is anim:
            setattr(widget, "_active_fade_anim", None)

    anim.finished.connect(_cleanup)
    setattr(widget, "_active_fade_anim", anim)
    anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)


def animate_flash_highlight(
    widget: QWidget,
    *,
    duration_ms: int = 155,
    dip_opacity: float = 0.72,
) -> None:
    """Gentle 155ms tactile settle pulse on updated cart rows or totals."""
    if widget is None or not _qt_is_valid(widget) or is_reduced_motion() or not widget.isVisible():
        return
    _stop_registered_anim(widget, "_active_flash_anim")

    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(dip_opacity)
    widget.setGraphicsEffect(effect)

    anim = QPropertyAnimation(effect, b"opacity", widget)
    anim.setDuration(max(90, min(200, int(duration_ms))))
    anim.setStartValue(float(dip_opacity))
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def _cleanup() -> None:
        if not _qt_is_valid(widget):
            return
        try:
            if _qt_is_valid(effect) and widget.graphicsEffect() is effect:
                widget.setGraphicsEffect(None)
        except RuntimeError:
            pass
        if getattr(widget, "_active_flash_anim", None) is anim:
            setattr(widget, "_active_flash_anim", None)

    anim.finished.connect(_cleanup)
    setattr(widget, "_active_flash_anim", anim)
    anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)


def animate_window_fade_in(window: QWidget, *, duration_ms: int = 150) -> None:
    """Non-blocking fade-in for dialogs and top-level auth windows."""
    if window is None or not _qt_is_valid(window):
        return
    _stop_registered_anim(window, "_active_win_fade")
    if is_reduced_motion():
        window.setWindowOpacity(1.0)
        return

    window.setWindowOpacity(0.88)
    anim = QPropertyAnimation(window, b"windowOpacity", window)
    anim.setDuration(max(90, min(200, int(duration_ms))))
    anim.setStartValue(0.88)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def _done() -> None:
        if _qt_is_valid(window) and getattr(window, "_active_win_fade", None) is anim:
            setattr(window, "_active_win_fade", None)

    anim.finished.connect(_done)
    setattr(window, "_active_win_fade", anim)
    anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)


def _render_spinner_icon(color_hex: str, angle_deg: int, size: int = 15) -> QIcon:
    scale = 2
    px = size * scale
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing, True)
    c = QColor(color_hex)
    track = QColor(c)
    track.setAlpha(65)
    painter.setPen(QPen(track, 2.2 * scale, Qt.SolidLine, Qt.RoundCap))
    pad = 2.6 * scale
    rect = QRectF(pad, pad, px - 2 * pad, px - 2 * pad)
    painter.drawArc(rect, 0, 360 * 16)
    painter.setPen(QPen(c, 2.4 * scale, Qt.SolidLine, Qt.RoundCap))
    painter.drawArc(rect, int((-angle_deg) * 16), 115 * 16)
    painter.end()
    pm.setDevicePixelRatio(float(scale))
    icon = QIcon()
    icon.addPixmap(pm)
    return icon


class _SpinnerController(QObject):
    def __init__(self, button: QPushButton, color_hex: str) -> None:
        super().__init__(button)
        self.button = button
        self.color_hex = color_hex
        self.angle = 0
        self.timer = QTimer(self)
        self.timer.setInterval(45)
        self.timer.timeout.connect(self._tick)

    def start(self) -> None:
        self._paint()
        if not is_reduced_motion():
            self.timer.start()

    def stop(self) -> None:
        self.timer.stop()

    def _tick(self) -> None:
        if not _qt_is_valid(self.button):
            self.timer.stop()
            return
        self.angle = (self.angle + 30) % 360
        self._paint()

    def _paint(self) -> None:
        if _qt_is_valid(self.button):
            self.button.setIcon(_render_spinner_icon(self.color_hex, self.angle, 15))


def set_button_loading(
    button: QPushButton,
    loading: bool,
    *,
    loading_text: str = "",
    spinner_color: str = "#FFFFFF",
) -> None:
    """Put a QPushButton cleanly into/out of animated Loading state without shifting layout."""
    if button is None:
        return
    if loading:
        if not getattr(button, "_is_loading_state", False):
            setattr(
                button,
                "_orig_loading_snapshot",
                (button.text(), button.isEnabled(), QIcon(button.icon())),
            )
            setattr(button, "_is_loading_state", True)
        if loading_text:
            button.setText(loading_text)
        button.setEnabled(False)
        button.setProperty("loading", "true")
        button.style().unpolish(button)
        button.style().polish(button)

        ctrl = getattr(button, "_spinner_ctrl", None)
        if ctrl is None:
            ctrl = _SpinnerController(button, spinner_color)
            setattr(button, "_spinner_ctrl", ctrl)
        else:
            ctrl.color_hex = spinner_color
        ctrl.start()
    else:
        ctrl = getattr(button, "_spinner_ctrl", None)
        if ctrl is not None:
            ctrl.stop()
        if getattr(button, "_is_loading_state", False):
            orig = getattr(button, "_orig_loading_snapshot", None)
            setattr(button, "_is_loading_state", False)
            button.setProperty("loading", "false")
            if orig is not None:
                orig_text, orig_enabled, orig_icon = orig
                button.setText(orig_text)
                button.setIcon(orig_icon)
                button.setEnabled(orig_enabled)
            button.style().unpolish(button)
            button.style().polish(button)


class _ButtonPressFilter(QObject):
    """Lightweight event filter adding tactile micro-settle feedback without resizing buttons."""

    def __init__(self, button: QPushButton) -> None:
        super().__init__(button)
        self.button = button

    def eventFilter(self, watched, event) -> bool:
        if watched is self.button and _qt_is_valid(self.button) and self.button.isEnabled():
            et = event.type()
            if et == QEvent.Type.MouseButtonRelease and self.button.rect().contains(
                event.position().toPoint()
            ):
                if not is_reduced_motion():
                    animate_flash_highlight(self.button, duration_ms=135, dip_opacity=0.86)
        return super().eventFilter(watched, event)


def attach_button_feedback(button: QPushButton) -> None:
    """Attach safe tactile release settle feedback to a QPushButton."""
    if button is None or getattr(button, "_has_press_filter", False):
        return
    flt = _ButtonPressFilter(button)
    button.installEventFilter(flt)
    setattr(button, "_press_filter_ref", flt)
    setattr(button, "_has_press_filter", True)


class AnimatedStackedWidget(QStackedWidget):
    """Drop-in QStackedWidget adding gentle 150ms cross-fade on page changes."""

    def setCurrentIndex(self, index: int) -> None:
        prev = self.currentIndex()
        if index != prev and prev >= 0:
            prev_w = self.widget(prev)
            if prev_w is not None:
                clear_widget_animations(prev_w)
        super().setCurrentIndex(index)
        if index != prev and self.isVisible():
            w = self.currentWidget()
            if w is not None:
                animate_fade_in(w, duration_ms=155, start_opacity=0.52)

