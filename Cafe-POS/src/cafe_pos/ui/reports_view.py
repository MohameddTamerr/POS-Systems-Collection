from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from PySide6.QtCore import QDate, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDateEdit,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..auth import AuthorizationError
from ..config import BusinessConfig
from ..db import (
    BreakdownPoint,
    Database,
    ExtendedAnalyticsSummary,
    OrderSummary,
    SalesAnalyticsSummary,
    ShiftRecord,
)
from ..icons import get_svg_icon, get_svg_pixmap
from ..i18n import (
    format_chart_bucket_label,
    is_rtl,
    tr,
    tr_category,
    tr_category_record,
    tr_count,
    tr_payment_method,
)
from ..money import format_minor, get_currency
from ..payments import ALL_PAYMENT_METHODS, METHOD_CASH
from ..receipts import format_local_timestamp
from ..theme import get_tokens
from .animations import attach_button_feedback
from .expenses_view import ExpensesView
from .shift_dialog import ShiftManagerDialog
from .widgets import EmptyStateWidget

REPORT_PERIODS: list[tuple[str, str]] = [
    ("reports.period.today", "today"),
    ("reports.period.yesterday", "yesterday"),
    ("reports.period.week", "week"),
    ("reports.period.month", "month"),
    ("reports.period.all", "all"),
    ("reports.period.custom", "custom"),
]

_SERIES_COLORS = ["#D98E32", "#D97560", "#4F8A7B"]


def _esc_amp(text: str) -> str:
    return (text or "").replace("&", "&&")


def _nice_chart_ceiling(max_val_minor: int, currency: str) -> int:
    """Round chart Y ceiling so 4 horizontal steps land on clean major currency ticks."""
    import math

    spec = get_currency(currency)
    factor = max(1, spec.minor_factor)
    raw_major = max(4.0, (max_val_minor * 1.12) / factor)
    raw_step = raw_major / 4.0
    mag = 10 ** math.floor(math.log10(raw_step)) if raw_step > 0 else 1.0
    norm = raw_step / mag
    for nice in (1.0, 1.5, 2.0, 2.5, 5.0, 10.0):
        if norm <= nice + 1e-9:
            chosen = nice * mag
            return max(factor * 4, int(round(chosen * 4.0 * factor)))
    return max(factor * 4, int(round(10.0 * mag * 4.0 * factor)))


class KpiCard(QFrame):
    """Compact KPI metric card backed cleanly by SQLite totals."""

    def __init__(
        self, title: str, icon_name: str, accent_color: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setProperty("class", "KpiCard")
        self._icon_name = icon_name
        self._accent = accent_color

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.icon_chip = QLabel()
        self.icon_chip.setFixedSize(28, 28)
        self.icon_chip.setAlignment(Qt.AlignCenter)

        self.title_lbl = QLabel(title)
        self.title_lbl.setProperty("class", "KpiTitleLabel")

        top.addWidget(self.icon_chip)
        top.addWidget(self.title_lbl, 1)
        layout.addLayout(top)

        self.value_lbl = QLabel("$0.00")
        self.value_lbl.setProperty("class", "KpiValueLabel")
        layout.addWidget(self.value_lbl)

        self.sub_lbl = QLabel(tr("reports.kpi.no_data"))
        self.sub_lbl.setProperty("class", "KpiSubLabel")
        self.sub_lbl.setWordWrap(True)
        layout.addWidget(self.sub_lbl)

        self._apply_alignment()
        self.refresh_accent(accent_color)

    def _apply_alignment(self) -> None:
        lead_align = (Qt.AlignRight if is_rtl() else Qt.AlignLeft) | Qt.AlignVCenter | Qt.AlignAbsolute
        self.title_lbl.setAlignment(lead_align)
        self.value_lbl.setAlignment(lead_align)
        self.sub_lbl.setAlignment(lead_align)

    def set_title(self, title: str) -> None:
        self._apply_alignment()
        self.title_lbl.setText(title)

    def refresh_accent(self, accent_color: str) -> None:
        self._accent = accent_color
        t = get_tokens(accent_color)
        self.icon_chip.setStyleSheet(
            f"background-color: {t.primary_tint}; border: 1px solid {t.primary_border}; border-radius: 8px;"
        )
        self.icon_chip.setPixmap(get_svg_pixmap(self._icon_name, t.primary, 15))

    def set_values(self, value_text: str, sub_text: str, *, muted_value: bool = False) -> None:
        self._apply_alignment()
        self.value_lbl.setText(value_text)
        t = get_tokens(self._accent)
        if muted_value:
            self.value_lbl.setStyleSheet(f"color: {t.muted};")
        else:
            self.value_lbl.setStyleSheet("")
        self.sub_lbl.setText(sub_text)


class SalesTrendChartWidget(QWidget):
    """Native high-DPI interactive QPainter trend chart for Revenue, Refunds & Expenses."""

    def __init__(self, accent_color: str, currency: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._accent = accent_color
        self._currency = currency
        self._summary: SalesAnalyticsSummary | None = None
        self._ext_summary: ExtendedAnalyticsSummary | None = None
        self._cat_labels: dict[str, str] = {}
        self._hover_idx: int = -1
        self._plot_points: list[QPointF] = []
        self.setMinimumHeight(220)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(True)

    def set_data(
        self,
        summary: SalesAnalyticsSummary,
        accent_color: str,
        currency: str,
        *,
        cat_labels: dict[str, str] | None = None,
    ) -> None:
        self._summary = summary
        self._ext_summary = None
        self._accent = accent_color
        self._currency = currency
        self._cat_labels = dict(cat_labels or {})
        self._hover_idx = -1
        self.update()

    def set_extended_data(
        self,
        ext_summary: ExtendedAnalyticsSummary,
        accent_color: str,
        currency: str,
        *,
        cat_labels: dict[str, str] | None = None,
    ) -> None:
        self._ext_summary = ext_summary
        self._summary = None
        self._accent = accent_color
        self._currency = currency
        self._cat_labels = dict(cat_labels or {})
        self._hover_idx = -1
        self.update()

    def leaveEvent(self, event) -> None:
        if self._hover_idx != -1:
            self._hover_idx = -1
            self.update()
        super().leaveEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not self._plot_points:
            return
        mx = event.position().x()
        best_i = -1
        best_dist = 1e9
        for i, pt in enumerate(self._plot_points):
            d = abs(pt.x() - mx)
            if d < best_dist:
                best_dist = d
                best_i = i
        if best_dist > 44:
            best_i = -1
        if best_i != self._hover_idx:
            self._hover_idx = best_i
            self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        try:
            self._paint_trend(painter)
        finally:
            if painter.isActive():
                painter.end()

    def _paint_trend(self, painter: QPainter) -> None:
        painter.setRenderHint(QPainter.Antialiasing, True)
        t = get_tokens(self._accent)
        rtl = is_rtl()
        w = float(self.width())
        h = float(self.height())
        painter.fillRect(QRectF(0, 0, w, h), QColor(t.surface))

        # Normalize data source (ExtendedAnalyticsSummary preferred when active)
        ext = self._ext_summary
        summary = self._summary

        buckets = list(ext.buckets) if ext is not None else []
        legacy_points = list(summary.daily_points) if (ext is None and summary is not None) else []

        has_any_data = False
        if ext is not None:
            has_any_data = bool(
                buckets and (ext.order_count > 0 or ext.expenses_total_minor > 0 or ext.refunds_total_minor > 0)
            )
        elif summary is not None:
            has_any_data = bool(summary.order_count > 0 and legacy_points)

        if not has_any_data:
            self._plot_points = []
            painter.setPen(QColor(t.muted))
            f = painter.font()
            f.setPointSize(10)
            painter.setFont(f)
            painter.drawText(
                QRectF(16, 0, max(20.0, w - 32), h),
                Qt.AlignCenter | Qt.TextWordWrap,
                tr("reports.chart.empty_prompt"),
            )
            return

        if ext is not None:
            n = len(buckets)
            max_val = max(
                [b.revenue_minor for b in buckets]
                + [b.expenses_minor for b in buckets]
                + [b.refunds_minor for b in buckets]
                + [100]
            )
        else:
            n = len(legacy_points)
            max_val = max(
                [p.total_minor for p in legacy_points]
                + [v for series in summary.category_daily_series.values() for v in series]
                + [100]
            )
        y_max = _nice_chart_ceiling(max_val, self._currency)

        left_pad = 86.0
        right_pad = 22.0
        top_pad = 14.0
        bot_pad = 28.0
        plot_w = max(40.0, w - left_pad - right_pad)
        plot_h = max(40.0, h - top_pad - bot_pad)

        grid_pen = QPen(QColor("#E6ECE0"), 1.0, Qt.DashLine)
        label_font = QFont(self.font())
        label_font.setPointSize(9)
        painter.setFont(label_font)

        for step in range(5):
            frac = step / 4.0
            y = top_pad + plot_h * (1.0 - frac)
            painter.setPen(grid_pen)
            painter.drawLine(QPointF(left_pad, y), QPointF(left_pad + plot_w, y))

            tick_val = int(round(y_max * frac))
            painter.setPen(QColor(t.muted))
            painter.drawText(
                QRectF(2, y - 9, left_pad - 8, 18),
                Qt.AlignRight | Qt.AlignVCenter | Qt.AlignAbsolute,
                format_minor(tick_val, self._currency),
            )

        def to_xy(idx: int, val: int) -> QPointF:
            x = left_pad + (plot_w * 0.5 if n == 1 else (idx / (n - 1)) * plot_w)
            ratio = min(1.0, max(0.0, val / float(y_max)))
            y = top_pad + plot_h * (1.0 - ratio)
            return QPointF(x, y)

        if ext is not None:
            total_pts = [to_xy(i, b.revenue_minor) for i, b in enumerate(buckets)]
            exp_pts = [to_xy(i, b.expenses_minor) for i, b in enumerate(buckets)]
            ref_pts = [to_xy(i, b.refunds_minor) for i, b in enumerate(buckets)]
        else:
            total_pts = [to_xy(i, dp.total_minor) for i, dp in enumerate(legacy_points)]
            exp_pts = []
            ref_pts = []

        self._plot_points = total_pts

        def _draw_curve(pts: list[QPointF], pen: QPen) -> None:
            if len(pts) < 2:
                return
            p = QPainterPath()
            p.moveTo(pts[0])
            for i in range(1, len(pts)):
                p0, p1 = pts[i - 1], pts[i]
                mx = (p0.x() + p1.x()) * 0.5
                p.cubicTo(QPointF(mx, p0.y()), QPointF(mx, p1.y()), p1)
            painter.setPen(pen)
            painter.drawPath(p)

        if len(total_pts) >= 2:
            line_path = QPainterPath()
            line_path.moveTo(total_pts[0])
            for i in range(1, len(total_pts)):
                p0 = total_pts[i - 1]
                p1 = total_pts[i]
                mx = (p0.x() + p1.x()) * 0.5
                line_path.cubicTo(QPointF(mx, p0.y()), QPointF(mx, p1.y()), p1)

            area_path = QPainterPath(line_path)
            area_path.lineTo(QPointF(total_pts[-1].x(), top_pad + plot_h))
            area_path.lineTo(QPointF(total_pts[0].x(), top_pad + plot_h))
            area_path.closeSubpath()

            grad = QLinearGradient(0, top_pad, 0, top_pad + plot_h)
            c_top = QColor(t.primary)
            c_top.setAlpha(68)
            c_bot = QColor(t.sage)
            c_bot.setAlpha(10)
            grad.setColorAt(0.0, c_top)
            grad.setColorAt(1.0, c_bot)
            painter.fillPath(area_path, grad)

            if ext is not None:
                if any(b.expenses_minor > 0 for b in buckets):
                    _draw_curve(exp_pts, QPen(QColor("#D98E32"), 1.8, Qt.DashLine))
                if any(b.refunds_minor > 0 for b in buckets):
                    _draw_curve(ref_pts, QPen(QColor("#B23A3A"), 1.8))
            elif summary is not None:
                for s_idx, (_cat_name, series) in enumerate(summary.category_daily_series.items()):
                    if len(series) != n:
                        continue
                    cat_pts = [to_xy(i, val) for i, val in enumerate(series)]
                    _draw_curve(cat_pts, QPen(QColor(_SERIES_COLORS[s_idx % len(_SERIES_COLORS)]), 1.8))

            painter.setPen(QPen(QColor(t.primary), 2.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.drawPath(line_path)
        else:
            pt = total_pts[0]
            bar_w = min(44.0, plot_w * 0.25)
            bar_h = (top_pad + plot_h) - pt.y()
            painter.setBrush(QColor(t.sage))
            painter.setPen(QPen(QColor(t.primary), 1.5))
            painter.drawRoundedRect(
                QRectF(pt.x() - bar_w * 0.5, pt.y(), bar_w, max(4.0, bar_h)), 6, 6
            )

        for i, pt in enumerate(total_pts):
            is_hover = i == self._hover_idx
            r = 5.5 if is_hover else (3.5 if n <= 16 else 0.0)
            if r > 0:
                painter.setBrush(QColor("#FFFFFF" if is_hover else t.primary))
                painter.setPen(QPen(QColor(t.primary), 2.2 if is_hover else 1.2))
                painter.drawEllipse(pt, r, r)

        step = max(1, (n + 6) // 7)
        painter.setPen(QColor(t.muted))
        for i in range(n):
            if i == 0 or i == n - 1 or i % step == 0:
                if i != n - 1 and (n - 1 - i) < step * 0.55 and n > 2:
                    continue
                x = total_pts[i].x()
                if ext is not None:
                    b = buckets[i]
                    lbl_txt = b.label if ":" in b.label or len(b.key) == 7 else format_chart_bucket_label(b.key, b.label)
                else:
                    dp = legacy_points[i]
                    lbl_txt = format_chart_bucket_label(dp.date_key, dp.label)
                painter.drawText(
                    QRectF(x - 38, top_pad + plot_h + 4, 76, 20),
                    Qt.AlignCenter,
                    lbl_txt,
                )

        if 0 <= self._hover_idx < n:
            hp = total_pts[self._hover_idx]
            painter.setPen(QPen(QColor(t.primary), 1.0, Qt.DashLine))
            painter.drawLine(QPointF(hp.x(), top_pad), QPointF(hp.x(), top_pad + plot_h))

            if ext is not None:
                b = buckets[self._hover_idx]
                lbl_d = b.label if ":" in b.label or len(b.key) == 7 else format_chart_bucket_label(b.key, b.label)
                tip_lines = [
                    tr("reports.chart.tip_header", label=lbl_d, orders=tr_count("order", b.order_count)),
                    tr("reports.tooltip.revenue", amount=format_minor(b.revenue_minor, self._currency)),
                ]
                if b.refunds_minor > 0:
                    tip_lines.append(
                        tr("reports.tooltip.refunds", amount=format_minor(b.refunds_minor, self._currency))
                    )
                if b.expenses_minor > 0:
                    tip_lines.append(
                        tr("reports.tooltip.expenses", amount=format_minor(b.expenses_minor, self._currency))
                    )
            else:
                dp = legacy_points[self._hover_idx]
                date_lbl = format_chart_bucket_label(dp.date_key, dp.label)
                tip_lines = [
                    tr(
                        "reports.chart.tip_header",
                        label=date_lbl,
                        orders=tr_count("order", dp.order_count),
                    ),
                    tr(
                        "reports.tooltip.revenue",
                        amount=format_minor(dp.total_minor, self._currency),
                    ),
                ]
                if summary is not None:
                    for cat_name, series in summary.category_daily_series.items():
                        if self._hover_idx < len(series) and series[self._hover_idx] > 0:
                            cat_disp = self._cat_labels.get((cat_name or "").strip().casefold()) or tr_category(cat_name)
                            tip_lines.append(
                                f"{cat_disp}: {format_minor(series[self._hover_idx], self._currency)}"
                            )

            tip_font = QFont(self.font())
            tip_font.setPointSize(9)
            fm = QFontMetricsF(tip_font)
            box_w = max(
                172.0,
                max((fm.horizontalAdvance(l) for l in tip_lines), default=140.0) + 26.0,
            )
            box_h = 14.0 + len(tip_lines) * 16.0
            bx = hp.x() + 12.0 if hp.x() + 12.0 + box_w < w - 6 else max(8.0, hp.x() - box_w - 12.0)
            by = max(top_pad + 4.0, min(top_pad + plot_h - box_h - 4.0, hp.y() - box_h * 0.5))

            tip_rect = QRectF(bx, by, box_w, box_h)
            painter.setBrush(QColor("#FFFFFF"))
            painter.setPen(QPen(QColor(t.border_strong), 1.2))
            painter.drawRoundedRect(tip_rect, 10, 10)

            text_align = (Qt.AlignRight if rtl else Qt.AlignLeft) | Qt.AlignVCenter | Qt.AlignAbsolute
            for li, line_txt in enumerate(tip_lines):
                painter.setPen(QColor(t.primary if li == 1 else t.text))
                f_line = QFont(tip_font)
                f_line.setBold(li <= 1)
                painter.setFont(f_line)
                painter.drawText(
                    QRectF(bx + 10, by + 7 + li * 16, box_w - 20, 16),
                    text_align,
                    line_txt,
                )


class CategoryBarChartWidget(QWidget):
    """Interactive QPainter horizontal category share bar chart with click-to-filter."""

    category_clicked = Signal(str)

    def __init__(self, accent_color: str, currency: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._accent = accent_color
        self._currency = currency
        self._items: list[BreakdownPoint] = []
        self._cat_labels: dict[str, str] = {}
        self._selected_category: str = ""
        self._hover_idx: int = -1
        self._row_rects: list[tuple[QRectF, str]] = []
        self.setMinimumHeight(220)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(True)

    def set_data(
        self,
        items: list[BreakdownPoint],
        accent_color: str,
        currency: str,
        *,
        cat_labels: dict[str, str] | None = None,
        selected_category: str = "",
    ) -> None:
        self._items = list(items[:6])
        self._accent = accent_color
        self._currency = currency
        self._cat_labels = dict(cat_labels or {})
        self._selected_category = selected_category or ""
        self._hover_idx = -1
        self.update()

    def leaveEvent(self, event) -> None:
        self._hover_idx = -1
        self.unsetCursor()
        self.update()
        super().leaveEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = event.position()
        hit = -1
        for i, (rect, _key) in enumerate(self._row_rects):
            if rect.contains(pos):
                hit = i
                break
        if hit != self._hover_idx:
            self._hover_idx = hit
            if hit >= 0:
                self.setCursor(Qt.PointingHandCursor)
            else:
                self.unsetCursor()
            self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            pos = event.position()
            for rect, key in self._row_rects:
                if rect.contains(pos):
                    self.category_clicked.emit(key)
                    return
        super().mousePressEvent(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        try:
            self._paint_trend(painter)
        finally:
            if painter.isActive():
                painter.end()

    def _paint_trend(self, painter: QPainter) -> None:
        painter.setRenderHint(QPainter.Antialiasing, True)
        t = get_tokens(self._accent)
        rtl = is_rtl()
        w = float(self.width())
        h = float(self.height())
        painter.fillRect(QRectF(0, 0, w, h), QColor(t.surface))

        if not self._items:
            self._row_rects = []
            painter.setPen(QColor(t.muted))
            painter.drawText(
                QRectF(16, 0, max(20.0, w - 32), h),
                Qt.AlignCenter | Qt.TextWordWrap,
                tr("reports.breakdown.empty"),
            )
            return

        total_all = max(1, sum(x.amount_minor for x in self._items))
        max_amt = max(1, max(x.amount_minor for x in self._items))
        pad_x = 8.0
        pad_y = 6.0
        row_h = min(36.0, max(28.0, (h - pad_y * 2) / max(1, len(self._items))))
        self._row_rects = []

        lbl_font = QFont(self.font())
        lbl_font.setPointSize(9)

        for idx, pt in enumerate(self._items):
            y0 = pad_y + idx * row_h
            row_rect = QRectF(pad_x, y0, max(40.0, w - pad_x * 2), row_h - 4.0)
            self._row_rects.append((row_rect, pt.key))

            is_sel = bool(self._selected_category and pt.key == self._selected_category)
            is_hov = idx == self._hover_idx
            if is_sel or is_hov:
                painter.setBrush(QColor(t.primary_tint if is_sel else "#F4F6EE"))
                painter.setPen(QPen(QColor(t.primary if is_sel else t.border), 1.2 if is_sel else 1.0))
                painter.drawRoundedRect(row_rect, 8, 8)

            disp = self._cat_labels.get((pt.key or "").strip().casefold()) or tr_category(pt.key)
            pct = int(round((pt.amount_minor / float(total_all)) * 100.0))
            val_str = f"{format_minor(pt.amount_minor, self._currency)} ({pct}%)"

            painter.setFont(lbl_font)
            painter.setPen(QColor(t.primary if is_sel else t.text))
            name_rect = QRectF(row_rect.left() + 8, row_rect.top() + 2, row_rect.width() * 0.56, 15)
            val_rect = QRectF(row_rect.left() + row_rect.width() * 0.52, row_rect.top() + 2, row_rect.width() * 0.48 - 8, 15)
            if rtl:
                painter.drawText(val_rect, Qt.AlignRight | Qt.AlignVCenter | Qt.AlignAbsolute, disp)
                painter.drawText(name_rect, Qt.AlignLeft | Qt.AlignVCenter | Qt.AlignAbsolute, val_str)
            else:
                painter.drawText(name_rect, Qt.AlignLeft | Qt.AlignVCenter | Qt.AlignAbsolute, disp)
                painter.drawText(val_rect, Qt.AlignRight | Qt.AlignVCenter | Qt.AlignAbsolute, val_str)

            track_rect = QRectF(row_rect.left() + 8, row_rect.top() + 20, max(20.0, row_rect.width() - 16), 7.0)
            painter.setBrush(QColor("#EFEFE0"))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(track_rect, 3.5, 3.5)

            fill_w = max(6.0, track_rect.width() * (pt.amount_minor / float(max_amt)))
            fill_x = track_rect.right() - fill_w if rtl else track_rect.left()
            bar_color = QColor(t.primary if is_sel or idx == 0 else _SERIES_COLORS[(idx - 1) % len(_SERIES_COLORS)])
            painter.setBrush(bar_color)
            painter.drawRoundedRect(QRectF(fill_x, track_rect.top(), fill_w, track_rect.height()), 3.5, 3.5)


class ShiftsOverviewTab(QWidget):
    """Admin overview of cashier shifts, drawer discrepancies, and CSV exports."""

    shifts_changed = Signal()
    status_message = Signal(str)

    def __init__(self, db: Database, config: BusinessConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.db = db
        self.config = config
        self._shifts: list[ShiftRecord] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        top_card = QFrame()
        top_card.setProperty("class", "BoutiqueCard")
        top_lay = QHBoxLayout(top_card)
        top_lay.setContentsMargins(14, 10, 14, 10)
        top_lay.setSpacing(8)

        self.status_lbl = QLabel()
        self.status_lbl.setProperty("class", "MutedLabel")
        self.status_combo = QComboBox()
        self.status_combo.currentIndexChanged.connect(lambda _i: self.refresh())

        self.staff_lbl = QLabel()
        self.staff_lbl.setProperty("class", "MutedLabel")
        self.staff_combo = QComboBox()
        self.staff_combo.currentIndexChanged.connect(lambda _i: self.refresh())

        self.manage_btn = QPushButton()
        self.manage_btn.setProperty("role", "primary")
        self.manage_btn.clicked.connect(self._open_manage_shift)

        self.export_btn = QPushButton()
        self.export_btn.clicked.connect(self._export_selected_csv)

        self.refresh_btn = QPushButton()
        self.refresh_btn.setIcon(get_svg_icon("refresh", "#202820", 13))
        self.refresh_btn.clicked.connect(self.refresh)

        attach_button_feedback(self.manage_btn)
        attach_button_feedback(self.export_btn)
        attach_button_feedback(self.refresh_btn)

        top_lay.addWidget(self.status_lbl)
        top_lay.addWidget(self.status_combo)
        top_lay.addWidget(self.staff_lbl)
        top_lay.addWidget(self.staff_combo)
        top_lay.addStretch(1)
        top_lay.addWidget(self.export_btn)
        top_lay.addWidget(self.manage_btn)
        top_lay.addWidget(self.refresh_btn)
        layout.addWidget(top_card)

        tbl_card = QFrame()
        tbl_card.setProperty("class", "BoutiqueCard")
        tbl_lay = QVBoxLayout(tbl_card)
        tbl_lay.setContentsMargins(14, 12, 14, 12)
        tbl_lay.setSpacing(8)

        self.stack = QStackedWidget()
        self.table = QTableWidget(0, 10)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        hdr = self.table.horizontalHeader()
        for col in range(10):
            hdr.setSectionResizeMode(col, QHeaderView.Stretch if col in (1, 3) else QHeaderView.ResizeToContents)

        self.empty = EmptyStateWidget("sales", "", "", accent_color=config.accent_color)
        self.stack.addWidget(self.table)
        self.stack.addWidget(self.empty)
        tbl_lay.addWidget(self.stack, 1)
        layout.addWidget(tbl_card, 1)

        self.retranslate_ui()

    def _populate_filters(self) -> None:
        cur_st = self.status_combo.currentData() if self.status_combo.count() else ""
        self.status_combo.blockSignals(True)
        self.status_combo.clear()
        self.status_combo.addItem(tr("common.all"), "")
        self.status_combo.addItem(tr("shift.status.open"), "OPEN")
        self.status_combo.addItem(tr("shift.status.closed"), "CLOSED")
        st_idx = self.status_combo.findData(cur_st)
        self.status_combo.setCurrentIndex(st_idx if st_idx >= 0 else 0)
        self.status_combo.blockSignals(False)

        cur_uid = self.staff_combo.currentData() if self.staff_combo.count() else None
        self.staff_combo.blockSignals(True)
        self.staff_combo.clear()
        self.staff_combo.addItem(tr("common.all"), None)
        try:
            users = self.db.list_users()
        except Exception:
            users = []
        for u in users:
            self.staff_combo.addItem(f"{u.label} (@{u.username})", u.id)
        uid_idx = self.staff_combo.findData(cur_uid)
        self.staff_combo.setCurrentIndex(uid_idx if uid_idx >= 0 else 0)
        self.staff_combo.blockSignals(False)

    def retranslate_ui(self) -> None:
        self.status_lbl.setText(tr("shift.filter.status"))
        self.staff_lbl.setText(tr("shift.filter.cashier"))
        self.manage_btn.setText(_esc_amp(tr("shift.manage_btn")))
        self.export_btn.setText(_esc_amp(tr("shift.export_csv_btn")))
        self.refresh_btn.setText(_esc_amp(tr("common.refresh")))
        self._populate_filters()
        self.table.setHorizontalHeaderLabels(
            [
                tr("shift.col.id"),
                tr("shift.col.cashier"),
                tr("shift.col.status"),
                tr("shift.col.opened"),
                tr("shift.col.opening_cash"),
                tr("shift.col.cash_sales"),
                tr("shift.col.digital_sales"),
                tr("shift.col.expected_cash"),
                tr("shift.col.counted_cash"),
                tr("shift.col.discrepancy"),
            ]
        )
        self.empty.set_texts(tr("shift.empty_title"), tr("shift.empty_desc"))
        self.refresh()

    def set_config(self, config: BusinessConfig) -> None:
        self.config = config
        self.empty.refresh_accent(config.accent_color)
        self.refresh()

    def clear_session_state(self) -> None:
        self._shifts = []
        self.table.setRowCount(0)
        self.stack.setCurrentIndex(1)

    def refresh(self) -> None:
        actor = self.db.session_actor
        if self.db.has_any_users() and (actor is None or not actor.is_admin):
            self.clear_session_state()
            return
        self._populate_filters()
        st = str(self.status_combo.currentData() or "") or None
        uid = self.staff_combo.currentData()
        try:
            shifts = self.db.list_shifts(user_id=uid, status=st, limit=100)
        except Exception:
            self.clear_session_state()
            return

        self._shifts = shifts
        if not shifts:
            self.table.setRowCount(0)
            self.stack.setCurrentIndex(1)
            return

        self.stack.setCurrentIndex(0)
        curr = self.config.currency
        self.table.setRowCount(len(shifts))
        for idx, sh in enumerate(shifts):
            c = sh.currency or curr
            id_it = QTableWidgetItem(f"#{sh.id}")
            cashier_it = QTableWidgetItem(sh.cashier_name or "—")
            st_it = QTableWidgetItem(tr("shift.status.open") if sh.status == "OPEN" else tr("shift.status.closed"))
            st_it.setForeground(QColor("#005B37" if sh.status == "OPEN" else "#5E6B5F"))
            op_it = QTableWidgetItem(format_local_timestamp(sh.opened_at))

            def _num_item(txt: str) -> QTableWidgetItem:
                it = QTableWidgetItem(txt)
                it.setTextAlignment(Qt.AlignTrailing | Qt.AlignVCenter)
                return it

            open_cash_it = _num_item(format_minor(sh.opening_cash_minor, c))
            cash_s_it = _num_item(format_minor(sh.cash_sales_minor, c))
            dig_s_it = _num_item(format_minor(sh.digital_sales_minor, c))
            exp_it = _num_item(format_minor(sh.expected_cash_minor, c))
            cnt_it = _num_item(format_minor(sh.counted_cash_minor, c) if sh.status == "CLOSED" else "—")
            if sh.status == "CLOSED":
                prefix = "+" if sh.discrepancy_minor > 0 else ""
                disc_it = _num_item(f"{prefix}{format_minor(sh.discrepancy_minor, c)}")
                if sh.discrepancy_minor < 0:
                    disc_it.setForeground(QColor("#B23A3A"))
                elif sh.discrepancy_minor > 0:
                    disc_it.setForeground(QColor("#D98E32"))
            else:
                disc_it = _num_item("—")

            for col_i, it in enumerate(
                (id_it, cashier_it, st_it, op_it, open_cash_it, cash_s_it, dig_s_it, exp_it, cnt_it, disc_it)
            ):
                self.table.setItem(idx, col_i, it)
            self.table.setRowHeight(idx, 36)

    def _open_manage_shift(self) -> None:
        dlg = ShiftManagerDialog(
            self.db,
            self.config,
            current_user=self.db.session_actor,
            parent=self,
        )
        dlg.shift_changed.connect(self._on_shift_dlg_changed)
        win = self.window()
        if hasattr(win, "clamp_dialog_to_screen"):
            win.clamp_dialog_to_screen(dlg)
        dlg.exec()
        self.refresh()

    def _on_shift_dlg_changed(self) -> None:
        self.refresh()
        self.shifts_changed.emit()

    def _export_selected_csv(self) -> None:
        if not self._shifts:
            QMessageBox.information(self, tr("shift.heading"), tr("shift.empty_title"))
            return
        row = self.table.currentRow()
        sh = self._shifts[row] if 0 <= row < len(self._shifts) else self._shifts[0]
        path_str, _ = QFileDialog.getSaveFileName(
            self,
            tr("shift.export_csv_btn"),
            f"Shift-{sh.id}.csv",
            "CSV Files (*.csv)",
        )
        if not path_str:
            return
        try:
            self.db.export_shift_csv(sh.id, Path(path_str))
        except Exception as exc:
            QMessageBox.warning(self, tr("shift.heading"), str(exc))
            return
        self.status_message.emit(tr("shift.status.exported", path=path_str))


class ReportsView(QWidget):
    """Interactive Boutique Business Intelligence, Expenses & Cashier Shifts screen."""

    status_message = Signal(str)

    def __init__(self, db: Database, config: BusinessConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.db = db
        self.config = config
        self._period = "week"
        self._selected_category: str = ""
        self._period_buttons: list[tuple[str, QPushButton]] = []
        self._tab_buttons: list[QPushButton] = []

        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 10, 16, 12)
        outer.setSpacing(10)

        # Header & Top Sub-Tab Switcher
        header_card = QFrame()
        header_card.setProperty("class", "BoutiqueCard")
        hdr_layout = QVBoxLayout(header_card)
        hdr_layout.setContentsMargins(16, 10, 16, 10)
        hdr_layout.setSpacing(8)

        top_row = QHBoxLayout()
        top_row.setSpacing(10)
        title_col = QVBoxLayout()
        title_col.setSpacing(1)
        self.title_lbl = QLabel("")
        self.title_lbl.setProperty("class", "PageHeading")
        self.sub_lbl = QLabel("")
        self.sub_lbl.setWordWrap(True)
        self.sub_lbl.setProperty("class", "MutedLabel")
        title_col.addWidget(self.title_lbl)
        title_col.addWidget(self.sub_lbl)
        top_row.addLayout(title_col, 1)

        for idx, key in enumerate(("reports.tab.dashboard", "reports.tab.expenses", "reports.tab.shifts")):
            tb = QPushButton(_esc_amp(tr(key)))
            tb.setProperty("class", "CategoryChip")
            tb.setProperty("selected", "true" if idx == 0 else "false")
            tb.clicked.connect(lambda _c=False, i=idx: self.switch_subview(i))
            attach_button_feedback(tb)
            self._tab_buttons.append(tb)
            top_row.addWidget(tb)

        self.refresh_btn = QPushButton("")
        self.refresh_btn.setIcon(get_svg_icon("refresh", "#202820", 13))
        self.refresh_btn.setIconSize(QSize(13, 13))
        self.refresh_btn.clicked.connect(self.refresh)
        attach_button_feedback(self.refresh_btn)
        top_row.addWidget(self.refresh_btn)
        hdr_layout.addLayout(top_row)

        # Filter bar for Dashboard tab
        self.filter_bar_widget = QWidget()
        fb_lay = QHBoxLayout(self.filter_bar_widget)
        fb_lay.setContentsMargins(0, 0, 0, 0)
        fb_lay.setSpacing(6)

        for label_key, key in REPORT_PERIODS:
            btn = QPushButton(_esc_amp(tr(label_key)))
            btn.setProperty("class", "CategoryChip")
            btn.setProperty("selected", "true" if key == self._period else "false")
            btn.clicked.connect(lambda _c=False, k=key: self._set_period(k))
            self._period_buttons.append((key, btn))
            fb_lay.addWidget(btn)

        self.start_date_edit = QDateEdit()
        self.start_date_edit.setCalendarPopup(True)
        self.start_date_edit.setDisplayFormat("yyyy-MM-dd")
        self.start_date_edit.setDate(QDate.fromString((date.today() - timedelta(days=6)).isoformat(), "yyyy-MM-dd"))
        self.start_date_edit.dateChanged.connect(self._on_custom_date_changed)

        self.end_date_edit = QDateEdit()
        self.end_date_edit.setCalendarPopup(True)
        self.end_date_edit.setDisplayFormat("yyyy-MM-dd")
        self.end_date_edit.setDate(QDate.currentDate())
        self.end_date_edit.dateChanged.connect(self._on_custom_date_changed)

        self.bucket_combo = QComboBox()
        self.bucket_combo.addItem("", "day")
        self.bucket_combo.addItem("", "hour")
        self.bucket_combo.addItem("", "week")
        self.bucket_combo.addItem("", "month")
        self.bucket_combo.currentIndexChanged.connect(lambda _i: self.refresh())

        self.cashier_combo = QComboBox()
        self.cashier_combo.currentIndexChanged.connect(lambda _i: self.refresh())

        self.pay_method_combo = QComboBox()
        self.pay_method_combo.currentIndexChanged.connect(lambda _i: self.refresh())

        self.cat_chip_btn = QPushButton()
        self.cat_chip_btn.setProperty("class", "PrimaryBadge")
        self.cat_chip_btn.setCursor(Qt.PointingHandCursor)
        self.cat_chip_btn.clicked.connect(self._clear_category_filter)
        self.cat_chip_btn.hide()

        fb_lay.addSpacing(4)
        fb_lay.addWidget(self.start_date_edit)
        fb_lay.addWidget(self.end_date_edit)
        fb_lay.addWidget(self.bucket_combo)
        fb_lay.addWidget(self.cashier_combo)
        fb_lay.addWidget(self.pay_method_combo)
        fb_lay.addWidget(self.cat_chip_btn)
        fb_lay.addStretch(1)
        hdr_layout.addWidget(self.filter_bar_widget)
        outer.addWidget(header_card)

        # Stacked Subviews (0: Analytics Dashboard, 1: Expenses, 2: Shifts)
        self.view_stack = QStackedWidget()

        # Page 0: Scrollable Dashboard
        scroll = QScrollArea()
        scroll.setObjectName("ReportsScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        host = QWidget()
        host.setObjectName("ReportsHost")
        body = QVBoxLayout(host)
        body.setContentsMargins(0, 2, 4, 8)
        body.setSpacing(12)

        # 1a. Primary KPI Row
        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(10)
        self.kpi_revenue = KpiCard(tr("reports.kpi.period_revenue"), "dollar", self.config.accent_color)
        self.kpi_today = KpiCard(tr("reports.kpi.today_sales"), "trend", self.config.accent_color)
        self.kpi_items = KpiCard(tr("reports.kpi.items_sold"), "bag", self.config.accent_color)
        self.kpi_avg = KpiCard(tr("reports.kpi.avg_order"), "coffee", self.config.accent_color)
        for card in (self.kpi_revenue, self.kpi_today, self.kpi_items, self.kpi_avg):
            kpi_row.addWidget(card, 1)
        body.addLayout(kpi_row)

        # 1b. Secondary Financial Intelligence Row (Expenses, Conditional Gross Profit, Payment Mix)
        kpi_row_2 = QHBoxLayout()
        kpi_row_2.setSpacing(10)
        self.kpi_expenses = KpiCard(tr("reports.kpi.expenses"), "sales", self.config.accent_color)
        self.kpi_profit = KpiCard(tr("reports.kpi.est_gross_profit"), "trend", self.config.accent_color)
        self.kpi_mix = KpiCard(tr("reports.kpi.cash_vs_digital"), "dollar", self.config.accent_color)
        for card in (self.kpi_expenses, self.kpi_profit, self.kpi_mix):
            kpi_row_2.addWidget(card, 1)
        body.addLayout(kpi_row_2)

        # 2. Charts Row (Trend Curve + Interactive Category Bars)
        charts_row = QHBoxLayout()
        charts_row.setSpacing(12)

        chart_card = QFrame()
        chart_card.setProperty("class", "BoutiqueCard")
        chart_layout = QVBoxLayout(chart_card)
        chart_layout.setContentsMargins(16, 13, 16, 13)
        chart_layout.setSpacing(8)

        chart_hdr = QHBoxLayout()
        chart_hdr.setSpacing(10)
        self.chart_title_lbl = QLabel("")
        self.chart_title_lbl.setProperty("class", "SectionHeader")
        self.legend_layout = QHBoxLayout()
        self.legend_layout.setSpacing(10)
        self.peak_lbl = QLabel("")
        self.peak_lbl.setProperty("class", "CountBadge")
        chart_hdr.addWidget(self.chart_title_lbl)
        chart_hdr.addSpacing(8)
        chart_hdr.addLayout(self.legend_layout, 1)
        chart_hdr.addWidget(self.peak_lbl)
        chart_layout.addLayout(chart_hdr)

        self.chart_widget = SalesTrendChartWidget(self.config.accent_color, self.config.currency)
        chart_layout.addWidget(self.chart_widget)

        cat_chart_card = QFrame()
        cat_chart_card.setProperty("class", "BoutiqueCard")
        cat_chart_lay = QVBoxLayout(cat_chart_card)
        cat_chart_lay.setContentsMargins(16, 13, 16, 13)
        cat_chart_lay.setSpacing(8)
        self.cat_chart_title_lbl = QLabel("")
        self.cat_chart_title_lbl.setProperty("class", "SectionHeader")
        self.cat_chart_hint_lbl = QLabel("")
        self.cat_chart_hint_lbl.setProperty("class", "MutedLabel")
        cat_chart_lay.addWidget(self.cat_chart_title_lbl)
        cat_chart_lay.addWidget(self.cat_chart_hint_lbl)
        self.cat_chart_widget = CategoryBarChartWidget(self.config.accent_color, self.config.currency)
        self.cat_chart_widget.category_clicked.connect(self._toggle_category_filter)
        cat_chart_lay.addWidget(self.cat_chart_widget, 1)

        charts_row.addWidget(chart_card, 3)
        charts_row.addWidget(cat_chart_card, 2)
        body.addLayout(charts_row)

        # 3. Bottom Two-Column Row: Top Items & Payment Mix + Recent Transactions
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(12)

        self.breakdown_card = QFrame()
        self.breakdown_card.setProperty("class", "BoutiqueCard")
        self.breakdown_layout = QVBoxLayout(self.breakdown_card)
        self.breakdown_layout.setContentsMargins(16, 14, 16, 14)
        self.breakdown_layout.setSpacing(10)

        self.bd_title_lbl = QLabel("")
        self.bd_title_lbl.setProperty("class", "SectionHeader")
        self.breakdown_layout.addWidget(self.bd_title_lbl)

        self.breakdown_rows_host = QWidget()
        self.breakdown_rows_layout = QVBoxLayout(self.breakdown_rows_host)
        self.breakdown_rows_layout.setContentsMargins(0, 0, 0, 0)
        self.breakdown_rows_layout.setSpacing(8)
        self.breakdown_layout.addWidget(self.breakdown_rows_host, 1)

        recent_card = QFrame()
        recent_card.setProperty("class", "BoutiqueCard")
        recent_layout = QVBoxLayout(recent_card)
        recent_layout.setContentsMargins(16, 14, 16, 14)
        recent_layout.setSpacing(10)

        rec_hdr = QHBoxLayout()
        self.rec_title_lbl = QLabel("")
        self.rec_title_lbl.setProperty("class", "SectionHeader")
        self.recent_count_badge = QLabel("")
        self.recent_count_badge.setProperty("class", "CountBadge")
        rec_hdr.addWidget(self.rec_title_lbl)
        rec_hdr.addStretch(1)
        rec_hdr.addWidget(self.recent_count_badge)
        recent_layout.addLayout(rec_hdr)

        self.recent_stack = QStackedWidget()
        self.recent_table = QTableWidget(0, 5)
        self.recent_table.verticalHeader().setVisible(False)
        self.recent_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.recent_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.recent_table.setAlternatingRowColors(True)
        self.recent_table.setMinimumHeight(210)
        hdr = self.recent_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeToContents)

        self.recent_empty = EmptyStateWidget(
            "sales",
            "",
            "",
            accent_color=self.config.accent_color,
        )
        self.recent_stack.addWidget(self.recent_table)
        self.recent_stack.addWidget(self.recent_empty)
        recent_layout.addWidget(self.recent_stack, 1)

        bottom_row.addWidget(self.breakdown_card, 2)
        bottom_row.addWidget(recent_card, 3)
        body.addLayout(bottom_row)

        scroll.setWidget(host)
        self.view_stack.addWidget(scroll)

        # Page 1: Operating Expenses
        self.expenses_view = ExpensesView(self.db, self.config, parent=self)
        self.expenses_view.expenses_changed.connect(self.refresh)
        self.expenses_view.status_message.connect(self.status_message.emit)
        self.view_stack.addWidget(self.expenses_view)

        # Page 2: Cashier Shifts Overview
        self.shifts_tab = ShiftsOverviewTab(self.db, self.config, parent=self)
        self.shifts_tab.shifts_changed.connect(self.refresh)
        self.shifts_tab.status_message.connect(self.status_message.emit)
        self.view_stack.addWidget(self.shifts_tab)

        outer.addWidget(self.view_stack, 1)
        self._apply_static_texts()
        self._sync_date_pickers_for_preset(self._period)

    def switch_subview(self, index: int) -> None:
        idx = max(0, min(2, int(index)))
        self.view_stack.setCurrentIndex(idx)
        self.filter_bar_widget.setVisible(idx == 0)
        for i, tb in enumerate(self._tab_buttons):
            tb.setProperty("selected", "true" if i == idx else "false")
            tb.style().unpolish(tb)
            tb.style().polish(tb)
        if idx == 1:
            self.expenses_view.refresh()
        elif idx == 2:
            self.shifts_tab.refresh()
        else:
            self.refresh()

    def _populate_dashboard_combos(self) -> None:
        cur_b = self.bucket_combo.currentData() or "day"
        self.bucket_combo.blockSignals(True)
        self.bucket_combo.clear()
        for key, lbl_key in (
            ("hour", "reports.bucket.hour"),
            ("day", "reports.bucket.day"),
            ("week", "reports.bucket.week"),
            ("month", "reports.bucket.month"),
        ):
            self.bucket_combo.addItem(tr(lbl_key), key)
        b_idx = self.bucket_combo.findData(cur_b)
        self.bucket_combo.setCurrentIndex(b_idx if b_idx >= 0 else 1)
        self.bucket_combo.blockSignals(False)

        cur_c = self.cashier_combo.currentData()
        self.cashier_combo.blockSignals(True)
        self.cashier_combo.clear()
        self.cashier_combo.addItem(tr("reports.filter.all_cashiers"), None)
        try:
            users = self.db.list_users()
        except Exception:
            users = []
        for u in users:
            self.cashier_combo.addItem(u.label, u.id)
        c_idx = self.cashier_combo.findData(cur_c)
        self.cashier_combo.setCurrentIndex(c_idx if c_idx >= 0 else 0)
        self.cashier_combo.blockSignals(False)

        cur_pm = self.pay_method_combo.currentData() or ""
        self.pay_method_combo.blockSignals(True)
        self.pay_method_combo.clear()
        self.pay_method_combo.addItem(tr("reports.filter.all_payments"), "")
        for pm in ALL_PAYMENT_METHODS:
            self.pay_method_combo.addItem(tr_payment_method(pm), pm)
        pm_idx = self.pay_method_combo.findData(cur_pm)
        self.pay_method_combo.setCurrentIndex(pm_idx if pm_idx >= 0 else 0)
        self.pay_method_combo.blockSignals(False)

    def _apply_static_texts(self) -> None:
        self.title_lbl.setText(tr("reports.heading"))
        self.sub_lbl.setText(tr("reports.sub_sqlite"))
        for idx, key in enumerate(("reports.tab.dashboard", "reports.tab.expenses", "reports.tab.shifts")):
            if idx < len(self._tab_buttons):
                self._tab_buttons[idx].setText(_esc_amp(tr(key)))

        key_to_i18n = {key: lkey for lkey, key in REPORT_PERIODS}
        for key, btn in self._period_buttons:
            lkey = key_to_i18n.get(key)
            if lkey:
                btn.setText(_esc_amp(tr(lkey)))
        self.refresh_btn.setText(_esc_amp(tr("common.refresh")))

        self._populate_dashboard_combos()

        self.kpi_revenue.set_title(tr("reports.kpi.period_revenue"))
        self.kpi_today.set_title(tr("reports.kpi.today_sales"))
        self.kpi_items.set_title(tr("reports.kpi.items_sold"))
        self.kpi_avg.set_title(tr("reports.kpi.avg_order"))
        self.kpi_expenses.set_title(tr("reports.kpi.expenses"))
        self.kpi_profit.set_title(tr("reports.kpi.est_gross_profit"))
        self.kpi_mix.set_title(tr("reports.kpi.cash_vs_digital"))

        self.chart_title_lbl.setText(tr("reports.chart.title"))
        self.cat_chart_title_lbl.setText(tr("reports.cat_chart.title"))
        self.cat_chart_hint_lbl.setText(tr("reports.cat_chart.hint"))
        self.bd_title_lbl.setText(tr("reports.breakdown.title"))
        self.rec_title_lbl.setText(tr("reports.recent.title"))
        self.recent_table.setHorizontalHeaderLabels(
            [
                tr("sales.col.order"),
                tr("sales.col.datetime"),
                tr("sales.col.items"),
                tr("sales.col.total"),
                tr("reports.recent.col.change"),
            ]
        )
        self.recent_empty.set_texts(
            tr("reports.recent.empty_title"),
            tr("reports.recent.empty_desc"),
        )

    def retranslate_ui(self) -> None:
        self._apply_static_texts()
        self.expenses_view.retranslate_ui()
        self.shifts_tab.retranslate_ui()
        self.refresh()

    def _sync_date_pickers_for_preset(self, key: str) -> None:
        today_d = date.today()
        if key == "today":
            s_d, e_d = today_d, today_d
        elif key == "yesterday":
            s_d = e_d = today_d - timedelta(days=1)
        elif key == "week":
            s_d, e_d = today_d - timedelta(days=6), today_d
        elif key == "month":
            s_d, e_d = today_d - timedelta(days=29), today_d
        elif key == "all":
            s_d, e_d = date(2020, 1, 1), today_d
        else:
            return
        self.start_date_edit.blockSignals(True)
        self.end_date_edit.blockSignals(True)
        self.start_date_edit.setDate(QDate.fromString(s_d.isoformat(), "yyyy-MM-dd"))
        self.end_date_edit.setDate(QDate.fromString(e_d.isoformat(), "yyyy-MM-dd"))
        self.start_date_edit.blockSignals(False)
        self.end_date_edit.blockSignals(False)

        b_target = "hour" if key in ("today", "yesterday") else ("month" if key == "all" else "day")
        b_idx = self.bucket_combo.findData(b_target)
        if b_idx >= 0:
            self.bucket_combo.blockSignals(True)
            self.bucket_combo.setCurrentIndex(b_idx)
            self.bucket_combo.blockSignals(False)

    def _on_custom_date_changed(self) -> None:
        self._period = "custom"
        for btn_key, btn in self._period_buttons:
            btn.setProperty("selected", "true" if btn_key == "custom" else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        self.refresh()

    def _set_period(self, key: str) -> None:
        self._period = key
        for btn_key, btn in self._period_buttons:
            btn.setProperty("selected", "true" if btn_key == key else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        if key != "custom":
            self._sync_date_pickers_for_preset(key)
        self.refresh()

    def _toggle_category_filter(self, cat_key: str) -> None:
        if self._selected_category == cat_key:
            self._selected_category = ""
        else:
            self._selected_category = cat_key
        self.refresh()

    def _clear_category_filter(self) -> None:
        self._selected_category = ""
        self.refresh()

    def set_config(self, config: BusinessConfig) -> None:
        self.config = config
        for card in (
            self.kpi_revenue,
            self.kpi_today,
            self.kpi_items,
            self.kpi_avg,
            self.kpi_expenses,
            self.kpi_profit,
            self.kpi_mix,
        ):
            card.refresh_accent(config.accent_color)
        self.recent_empty.refresh_accent(config.accent_color)
        self.expenses_view.set_config(config)
        self.shifts_tab.set_config(config)
        self.refresh()

    def clear_session_state(self) -> None:
        """Reset displayed financial metrics cleanly when signed out or unauthorized."""
        curr = self.config.currency
        self.kpi_revenue.set_values(format_minor(0, curr), tr_count("order", 0))
        self.kpi_today.set_values(
            format_minor(0, curr),
            tr("reports.kpi.orders_today", orders=tr_count("order", 0)),
        )
        self.kpi_items.set_values("0", tr("reports.kpi.across_cats", n=0))
        self.kpi_avg.set_values(format_minor(0, curr), tr("reports.kpi.no_orders_yet"))
        self.kpi_expenses.set_values(format_minor(0, curr), tr("reports.kpi.expenses_sub_cogs_sep"))
        self.kpi_profit.set_values("—", tr("reports.kpi.profit_add_costs_hint"), muted_value=True)
        self.kpi_mix.set_values(format_minor(0, curr), tr("reports.kpi.no_data"))
        self._clear_layout(self.legend_layout)
        self.peak_lbl.setText("")
        self.chart_widget._summary = None
        self.chart_widget._ext_summary = None
        self.chart_widget.update()
        self.cat_chart_widget.set_data([], self.config.accent_color, curr)
        self._clear_layout(self.breakdown_rows_layout)
        self.recent_table.setRowCount(0)
        self.recent_count_badge.setText(tr("reports.recent.shown_badge", n=0))
        self.recent_stack.setCurrentIndex(1)
        self.expenses_view.clear_session_state()
        self.shifts_tab.clear_session_state()

    def _clear_layout(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())

    def refresh(self) -> None:
        actor = self.db.session_actor
        if self.db.has_any_users() and (actor is None or not actor.is_admin):
            self.clear_session_state()
            return

        s_d = date.fromisoformat(self.start_date_edit.date().toString("yyyy-MM-dd"))
        e_d = date.fromisoformat(self.end_date_edit.date().toString("yyyy-MM-dd"))
        b_mode = str(self.bucket_combo.currentData() or "day")
        cashier_uid = self.cashier_combo.currentData()
        pm_filter = str(self.pay_method_combo.currentData() or "") or None
        cat_filter = self._selected_category or None

        try:
            ext = self.db.get_extended_analytics(
                start_date=s_d,
                end_date=e_d,
                bucket_mode=b_mode,
                cashier_user_id=cashier_uid,
                payment_method=pm_filter,
                category=cat_filter,
            )
            recent_orders = list(ext.recent_orders)
            cat_records = self.db.list_category_records(include_archived=True)
            prod_records = self.db.list_products(include_archived_categories=True)
        except AuthorizationError:
            self.clear_session_state()
            return

        curr = self.config.currency
        t = get_tokens(self.config.accent_color)

        cat_labels: dict[str, str] = {}
        for crecord in cat_records:
            disp = tr_category_record(crecord)
            for variant in (crecord.primary_name, crecord.name_en, crecord.name_ar):
                if variant:
                    cat_labels[variant.strip().casefold()] = disp

        def _cat_disp(raw_cat: str) -> str:
            clean = (raw_cat or "").strip()
            if not clean:
                return tr("category.other")
            return cat_labels.get(clean.casefold()) or tr_category(clean)

        prod_labels: dict[str, str] = {}
        for precord in prod_records:
            pdisp = precord.display_name()
            for variant in (precord.name, precord.name_en, precord.name_ar):
                if variant:
                    prod_labels[variant.strip().casefold()] = pdisp

        if self._selected_category:
            self.cat_chip_btn.setText(
                tr("reports.filter.category_chip", name=_cat_disp(self._selected_category)) + " ×"
            )
            self.cat_chip_btn.show()
        else:
            self.cat_chip_btn.hide()

        rev_sub = tr_count("order", ext.order_count)
        if ext.discounts_total_minor > 0 or ext.refunds_total_minor > 0:
            rev_sub = tr(
                "reports.kpi.rev_disc_ref_sub",
                orders=rev_sub,
                disc=format_minor(ext.discounts_total_minor, curr),
                ref=format_minor(ext.refunds_total_minor, curr),
            )
        self.kpi_revenue.set_values(format_minor(ext.net_revenue_minor, curr), rev_sub)
        self.kpi_today.set_values(
            format_minor(ext.today_revenue_minor, curr),
            tr("reports.kpi.orders_today", orders=tr_count("order", ext.today_order_count)),
        )
        n_cats = len(ext.category_breakdown)
        cats_sub = (
            tr("reports.kpi.across_one_cat")
            if n_cats == 1
            else tr("reports.kpi.across_cats", n=n_cats)
        )
        self.kpi_items.set_values(str(ext.items_sold), cats_sub)
        self.kpi_avg.set_values(
            format_minor(ext.average_order_minor, curr),
            tr("reports.kpi.avg_value") if ext.order_count else tr("reports.kpi.no_orders_yet"),
        )

        # Secondary KPI row
        self.kpi_expenses.set_values(
            format_minor(ext.expenses_total_minor, curr),
            tr("reports.kpi.expenses_sub_cogs_sep"),
        )
        if ext.has_reliable_cost_data and ext.order_count > 0:
            self.kpi_profit.set_values(
                format_minor(ext.estimated_gross_profit_minor, curr),
                tr(
                    "reports.kpi.profit_cogs_sub",
                    cogs=format_minor(ext.estimated_cogs_minor, curr),
                    covered=ext.cost_covered_orders,
                    total=ext.order_count,
                ),
                muted_value=False,
            )
        else:
            self.kpi_profit.set_values("—", tr("reports.kpi.profit_add_costs_hint"), muted_value=True)

        self.kpi_mix.set_values(
            format_minor(ext.cash_sales_minor, curr),
            tr("reports.kpi.digital_sub", amount=format_minor(ext.digital_sales_minor, curr)),
        )

        # Legend & Trend chart
        self._clear_layout(self.legend_layout)
        legend_specs = [
            (tr("reports.chart.legend"), t.primary),
            (tr("reports.kpi.expenses"), "#D98E32"),
            (tr("reports.legend.refunds"), "#B23A3A"),
        ]
        for leg_name, color_hex in legend_specs:
            chip = QWidget()
            cl = QHBoxLayout(chip)
            cl.setContentsMargins(0, 0, 0, 0)
            cl.setSpacing(5)
            dot = QLabel()
            dot.setFixedSize(9, 9)
            dot.setStyleSheet(f"background-color: {color_hex}; border-radius: 4px;")
            lbl = QLabel(leg_name)
            lbl.setProperty("class", "MutedLabel")
            cl.addWidget(dot)
            cl.addWidget(lbl)
            self.legend_layout.addWidget(chip)
        self.legend_layout.addStretch(1)

        peak_minor = max((b.revenue_minor for b in ext.buckets), default=0)
        self.peak_lbl.setText(tr("reports.peak_label", amount=format_minor(peak_minor, curr)))
        self.chart_widget.set_extended_data(ext, self.config.accent_color, curr, cat_labels=cat_labels)
        self.cat_chart_widget.set_data(
            ext.category_breakdown,
            self.config.accent_color,
            curr,
            cat_labels=cat_labels,
            selected_category=self._selected_category,
        )

        # Left column: Payment Breakdown + Top Selling Items
        self._clear_layout(self.breakdown_rows_layout)
        if not ext.payment_breakdown and not ext.top_products:
            empty_msg = QLabel(tr("reports.breakdown.empty"))
            empty_msg.setProperty("class", "MutedLabel")
            empty_msg.setWordWrap(True)
            self.breakdown_rows_layout.addWidget(empty_msg)
            self.breakdown_rows_layout.addStretch(1)
        else:
            if ext.payment_breakdown:
                pm_hdr = QLabel(tr("reports.breakdown.by_payment"))
                pm_hdr.setStyleSheet("font-size: 12px; font-weight: 700; color: #5E6B5F;")
                self.breakdown_rows_layout.addWidget(pm_hdr)
                denom_pm = max(1, sum(p.amount_minor for p in ext.payment_breakdown))
                for pb in ext.payment_breakdown[:4]:
                    pct = int(round((pb.amount_minor / denom_pm) * 100))
                    row = QWidget()
                    rl = QVBoxLayout(row)
                    rl.setContentsMargins(0, 0, 0, 0)
                    rl.setSpacing(3)
                    top_r = QHBoxLayout()
                    top_r.setContentsMargins(0, 0, 0, 0)
                    top_r.addWidget(QLabel(f"{tr_payment_method(pb.key)} ({pb.count})"))
                    top_r.addStretch(1)
                    val_lbl = QLabel(
                        tr("reports.breakdown.cat_val", amount=format_minor(pb.amount_minor, curr), pct=pct)
                    )
                    val_lbl.setStyleSheet("font-weight: 700; font-size: 12px;")
                    top_r.addWidget(val_lbl)
                    bar = QProgressBar()
                    bar.setRange(0, 100)
                    bar.setValue(pct)
                    bar.setTextVisible(False)
                    bar.setFixedHeight(7)
                    bar.setStyleSheet(
                        f"QProgressBar {{ background-color: #EFEFE0; border: none; border-radius: 3px; }}"
                        f"QProgressBar::chunk {{ background-color: {t.primary}; border-radius: 3px; }}"
                    )
                    rl.addLayout(top_r)
                    rl.addWidget(bar)
                    self.breakdown_rows_layout.addWidget(row)

            if ext.top_products:
                top_hdr = QLabel(tr("reports.breakdown.best_selling"))
                top_hdr.setStyleSheet(
                    "font-size: 12px; font-weight: 700; color: #5E6B5F; margin-top: 6px;"
                )
                self.breakdown_rows_layout.addWidget(top_hdr)
                for ps in ext.top_products[:5]:
                    pr = QHBoxLayout()
                    pr.setContentsMargins(0, 0, 0, 0)
                    disp_pname = prod_labels.get((ps.key or "").strip().casefold()) or ps.label
                    name_lbl = QLabel(disp_pname)
                    name_lbl.setStyleSheet("font-weight: 600; font-size: 12.5px;")
                    qty_badge = QLabel(tr("reports.breakdown.sold_badge", n=ps.count))
                    qty_badge.setProperty("class", "CountBadge")
                    rev_lbl = QLabel(format_minor(ps.amount_minor, curr))
                    rev_lbl.setStyleSheet(f"font-weight: 700; font-size: 12.5px; color: {t.primary};")
                    pr.addWidget(name_lbl, 1)
                    pr.addWidget(qty_badge)
                    pr.addWidget(rev_lbl)
                    wrap = QWidget()
                    wrap.setLayout(pr)
                    self.breakdown_rows_layout.addWidget(wrap)

            self.breakdown_rows_layout.addStretch(1)

        self._populate_recent_orders(recent_orders, curr)
        if self.view_stack.currentIndex() == 1:
            self.expenses_view.refresh()
        elif self.view_stack.currentIndex() == 2:
            self.shifts_tab.refresh()

    def _populate_recent_orders(self, orders: list[OrderSummary], curr: str) -> None:
        self.recent_count_badge.setText(tr("reports.recent.shown_badge", n=len(orders)))
        if not orders:
            self.recent_table.setRowCount(0)
            self.recent_stack.setCurrentIndex(1)
            return

        self.recent_stack.setCurrentIndex(0)
        self.recent_table.setRowCount(len(orders))
        for row_idx, s in enumerate(orders):
            num_item = QTableWidgetItem(s.order_number)
            f = num_item.font()
            f.setBold(True)
            num_item.setFont(f)

            dt_item = QTableWidgetItem(format_local_timestamp(s.created_at))
            cnt_item = QTableWidgetItem(str(s.item_count))
            cnt_item.setTextAlignment(Qt.AlignCenter)

            tot_item = QTableWidgetItem(format_minor(s.total_minor, s.currency or curr))
            tot_item.setTextAlignment(Qt.AlignTrailing | Qt.AlignVCenter)
            tot_item.setFont(f)

            chg_item = QTableWidgetItem(format_minor(s.change_minor, s.currency or curr))
            chg_item.setTextAlignment(Qt.AlignTrailing | Qt.AlignVCenter)

            self.recent_table.setItem(row_idx, 0, num_item)
            self.recent_table.setItem(row_idx, 1, dt_item)
            self.recent_table.setItem(row_idx, 2, cnt_item)
            self.recent_table.setItem(row_idx, 3, tot_item)
            self.recent_table.setItem(row_idx, 4, chg_item)
            self.recent_table.setRowHeight(row_idx, 36)
