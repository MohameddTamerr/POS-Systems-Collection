from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..auth import AuthorizationError, UserAccount
from ..config import BusinessConfig
from ..db import Database, ShiftRecord
from ..i18n import layout_direction, tr
from ..icons import get_svg_icon
from ..money import format_major_input, format_minor, parse_major_to_minor
from ..paths import get_exports_dir
from ..receipts import format_local_timestamp
from ..theme import contrast_text_hex
from .animations import animate_window_fade_in, attach_button_feedback


def _esc_amp(text: str) -> str:
    return (text or "").replace("&", "&&")


class ShiftManagerDialog(QDialog):
    """Cashier Shift Manager: Open shift, record cash-in/out, close with discrepancy count, export CSV."""

    shift_changed = Signal()

    def __init__(
        self,
        db: Database,
        config: BusinessConfig,
        current_user: UserAccount | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.db = db
        self.config = config
        self._current_user = current_user or db.session_actor
        self._active_shift: ShiftRecord | None = None
        self._shifts_history: list[ShiftRecord] = []
        self._faded_once = False

        self.setModal(True)
        self.setMinimumSize(680, 560)
        self.resize(780, 660)
        self.setLayoutDirection(layout_direction())

        self._build_ui()
        self.retranslate_ui()
        self.refresh()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        # Header
        hdr = QFrame()
        hdr.setProperty("class", "BoutiqueCard")
        hdr_lay = QHBoxLayout(hdr)
        hdr_lay.setContentsMargins(14, 10, 14, 10)
        hdr_lay.setSpacing(10)

        icon_lbl = QLabel()
        icon_lbl.setPixmap(get_svg_icon("clock", self.config.accent_color, 20).pixmap(20, 20))
        hdr_lay.addWidget(icon_lbl)

        title_col = QVBoxLayout()
        title_col.setContentsMargins(0, 0, 0, 0)
        title_col.setSpacing(2)
        self.title_lbl = QLabel()
        self.title_lbl.setProperty("class", "PageHeading")
        self.subtitle_lbl = QLabel()
        self.subtitle_lbl.setProperty("class", "MutedLabel")
        title_col.addWidget(self.title_lbl)
        title_col.addWidget(self.subtitle_lbl)
        hdr_lay.addLayout(title_col, 1)

        self.staff_filter_label = QLabel()
        self.staff_filter_label.setProperty("class", "MutedLabel")
        self.staff_combo = QComboBox()
        self.staff_combo.setMinimumWidth(160)
        self.staff_combo.currentIndexChanged.connect(self.refresh)
        if not (self._current_user and self._current_user.is_admin):
            self.staff_filter_label.hide()
            self.staff_combo.hide()

        self.status_badge = QLabel()
        self.status_badge.setProperty("class", "CountBadge")

        hdr_lay.addWidget(self.staff_filter_label)
        hdr_lay.addWidget(self.staff_combo)
        hdr_lay.addWidget(self.status_badge)
        root.addWidget(hdr)

        # Scroll area for main body
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(2, 2, 2, 4)
        body_lay.setSpacing(10)

        self.state_stack = QStackedWidget()

        # Page 0: Open Shift
        open_card = QFrame()
        open_card.setProperty("class", "BoutiqueCard")
        open_lay = QVBoxLayout(open_card)
        open_lay.setContentsMargins(16, 14, 16, 14)
        open_lay.setSpacing(10)

        self.open_heading_lbl = QLabel()
        self.open_heading_lbl.setProperty("class", "SectionHeader")
        self.open_desc_lbl = QLabel()
        self.open_desc_lbl.setProperty("class", "MutedLabel")
        self.open_desc_lbl.setWordWrap(True)
        open_lay.addWidget(self.open_heading_lbl)
        open_lay.addWidget(self.open_desc_lbl)

        open_form = QFormLayout()
        open_form.setSpacing(8)
        self.opening_cash_lbl = QLabel()
        self.opening_cash_edit = QLineEdit("0")
        self.opening_cash_edit.setMinimumHeight(36)
        self.opening_notes_lbl = QLabel()
        self.open_notes_edit = QLineEdit()
        self.open_notes_edit.setMaxLength(180)
        self.open_notes_edit.setMinimumHeight(34)
        open_form.addRow(self.opening_cash_lbl, self.opening_cash_edit)
        open_form.addRow(self.opening_notes_lbl, self.open_notes_edit)
        open_lay.addLayout(open_form)

        open_btn_row = QHBoxLayout()
        open_btn_row.addStretch(1)
        self.open_shift_btn = QPushButton()
        self.open_shift_btn.setProperty("role", "primary")
        self.open_shift_btn.setMinimumHeight(38)
        self.open_shift_btn.setMinimumWidth(160)
        self.open_shift_btn.clicked.connect(self._on_open_shift)
        attach_button_feedback(self.open_shift_btn)
        open_btn_row.addWidget(self.open_shift_btn)
        open_lay.addLayout(open_btn_row)
        open_lay.addStretch(1)
        self.state_stack.addWidget(open_card)

        # Page 1: Active Open Shift Management
        active_page = QWidget()
        act_lay = QVBoxLayout(active_page)
        act_lay.setContentsMargins(0, 0, 0, 0)
        act_lay.setSpacing(10)

        # KPI Grid
        kpi_card = QFrame()
        kpi_card.setProperty("class", "BoutiqueCard")
        kpi_outer = QVBoxLayout(kpi_card)
        kpi_outer.setContentsMargins(14, 12, 14, 12)
        kpi_outer.setSpacing(8)

        self.active_info_lbl = QLabel()
        self.active_info_lbl.setStyleSheet("font-size: 13px; font-weight: 700; color: #202820;")
        kpi_outer.addWidget(self.active_info_lbl)

        kpi_grid = QGridLayout()
        kpi_grid.setSpacing(8)
        self._kpi_titles: list[QLabel] = []
        self._kpi_vals: list[QLabel] = []
        for idx in range(6):
            box = QFrame()
            box.setProperty("class", "CozySubCard")
            bl = QVBoxLayout(box)
            bl.setContentsMargins(10, 7, 10, 7)
            bl.setSpacing(2)
            t_lbl = QLabel()
            t_lbl.setProperty("class", "MutedLabel")
            v_lbl = QLabel("0")
            v_lbl.setStyleSheet(
                "font-size: 15px; font-weight: 800; color: #005B37;"
                if idx == 5
                else "font-size: 14px; font-weight: 700; color: #202820;"
            )
            bl.addWidget(t_lbl)
            bl.addWidget(v_lbl)
            self._kpi_titles.append(t_lbl)
            self._kpi_vals.append(v_lbl)
            kpi_grid.addWidget(box, idx // 3, idx % 3)
        kpi_outer.addLayout(kpi_grid)
        act_lay.addWidget(kpi_card)

        # Cash Movement + Close Shift side-by-side
        ops_row = QHBoxLayout()
        ops_row.setSpacing(10)

        mv_card = QFrame()
        mv_card.setProperty("class", "BoutiqueCard")
        mv_lay = QVBoxLayout(mv_card)
        mv_lay.setContentsMargins(14, 12, 14, 12)
        mv_lay.setSpacing(8)
        self.mv_heading_lbl = QLabel()
        self.mv_heading_lbl.setProperty("class", "SectionHeader")
        mv_lay.addWidget(self.mv_heading_lbl)

        mv_form = QFormLayout()
        mv_form.setSpacing(6)
        self.mv_type_lbl = QLabel()
        self.mv_type_combo = QComboBox()
        self.mv_type_combo.setMinimumHeight(32)
        self.mv_amt_lbl = QLabel()
        self.mv_amount_edit = QLineEdit()
        self.mv_amount_edit.setMinimumHeight(32)
        self.mv_reason_lbl = QLabel()
        self.mv_reason_edit = QLineEdit()
        self.mv_reason_edit.setMaxLength(160)
        self.mv_reason_edit.setMinimumHeight(32)
        mv_form.addRow(self.mv_type_lbl, self.mv_type_combo)
        mv_form.addRow(self.mv_amt_lbl, self.mv_amount_edit)
        mv_form.addRow(self.mv_reason_lbl, self.mv_reason_edit)
        mv_lay.addLayout(mv_form)

        self.record_mv_btn = QPushButton()
        self.record_mv_btn.setProperty("role", "secondary")
        self.record_mv_btn.setMinimumHeight(34)
        self.record_mv_btn.clicked.connect(self._on_record_movement)
        attach_button_feedback(self.record_mv_btn)
        mv_lay.addWidget(self.record_mv_btn)
        mv_lay.addStretch(1)

        close_card = QFrame()
        close_card.setProperty("class", "BoutiqueCard")
        cl_lay = QVBoxLayout(close_card)
        cl_lay.setContentsMargins(14, 12, 14, 12)
        cl_lay.setSpacing(8)
        self.close_heading_lbl = QLabel()
        self.close_heading_lbl.setProperty("class", "SectionHeader")
        cl_lay.addWidget(self.close_heading_lbl)

        cl_form = QFormLayout()
        cl_form.setSpacing(6)
        self.counted_cash_lbl = QLabel()
        counted_row = QHBoxLayout()
        counted_row.setSpacing(6)
        self.counted_cash_edit = QLineEdit()
        self.counted_cash_edit.setMinimumHeight(32)
        self.counted_cash_edit.textChanged.connect(self._update_discrepancy_preview)
        self.exact_counted_btn = QPushButton()
        self.exact_counted_btn.setProperty("role", "secondary")
        self.exact_counted_btn.setMinimumHeight(32)
        self.exact_counted_btn.clicked.connect(self._fill_exact_expected)
        attach_button_feedback(self.exact_counted_btn)
        counted_row.addWidget(self.counted_cash_edit, 1)
        counted_row.addWidget(self.exact_counted_btn)

        self.discrepancy_title_lbl = QLabel()
        self.discrepancy_val_lbl = QLabel("-")
        self.discrepancy_val_lbl.setStyleSheet("font-weight: 700;")

        self.close_notes_lbl = QLabel()
        self.close_notes_edit = QLineEdit()
        self.close_notes_edit.setMaxLength(180)
        self.close_notes_edit.setMinimumHeight(32)

        cl_form.addRow(self.counted_cash_lbl, counted_row)
        cl_form.addRow(self.discrepancy_title_lbl, self.discrepancy_val_lbl)
        cl_form.addRow(self.close_notes_lbl, self.close_notes_edit)
        cl_lay.addLayout(cl_form)

        self.close_shift_btn = QPushButton()
        self.close_shift_btn.setProperty("role", "primary")
        self.close_shift_btn.setMinimumHeight(34)
        self.close_shift_btn.clicked.connect(self._on_close_shift)
        attach_button_feedback(self.close_shift_btn)
        cl_lay.addWidget(self.close_shift_btn)
        cl_lay.addStretch(1)

        ops_row.addWidget(mv_card, 1)
        ops_row.addWidget(close_card, 1)
        act_lay.addLayout(ops_row)

        self.state_stack.addWidget(active_page)
        body_lay.addWidget(self.state_stack)

        # Recent Shifts History Table + Export CSV
        hist_card = QFrame()
        hist_card.setProperty("class", "BoutiqueCard")
        hist_lay = QVBoxLayout(hist_card)
        hist_lay.setContentsMargins(14, 12, 14, 12)
        hist_lay.setSpacing(8)

        hist_hdr = QHBoxLayout()
        self.hist_title_lbl = QLabel()
        self.hist_title_lbl.setProperty("class", "SectionHeader")
        self.export_csv_btn = QPushButton()
        self.export_csv_btn.setProperty("role", "secondary")
        self.export_csv_btn.setIcon(get_svg_icon("pdf", "#202820", 13))
        self.export_csv_btn.setIconSize(QSize(13, 13))
        self.export_csv_btn.clicked.connect(self._on_export_csv)
        attach_button_feedback(self.export_csv_btn)
        hist_hdr.addWidget(self.hist_title_lbl)
        hist_hdr.addStretch(1)
        hist_hdr.addWidget(self.export_csv_btn)
        hist_lay.addLayout(hist_hdr)

        self.history_table = QTableWidget(0, 7)
        self.history_table.verticalHeader().setVisible(False)
        self.history_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.history_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.history_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.history_table.setAlternatingRowColors(True)
        self.history_table.setMinimumHeight(145)
        hh = self.history_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(6, QHeaderView.ResizeToContents)
        self.history_table.itemSelectionChanged.connect(self._sync_export_btn)
        hist_lay.addWidget(self.history_table)
        body_lay.addWidget(hist_card)

        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self.close_dlg_btn = QPushButton()
        self.close_dlg_btn.setMinimumHeight(36)
        self.close_dlg_btn.clicked.connect(self.accept)
        attach_button_feedback(self.close_dlg_btn)
        footer.addWidget(self.close_dlg_btn)
        root.addLayout(footer)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if not self._faded_once:
            self._faded_once = True
            animate_window_fade_in(self, duration_ms=145)

    def _reload_staff_combo(self) -> None:
        if not (self._current_user and self._current_user.is_admin):
            return
        prev_uid = self.staff_combo.currentData()
        self.staff_combo.blockSignals(True)
        self.staff_combo.clear()
        my_id = self._current_user.id
        self.staff_combo.addItem(
            tr("shift.staff_me", name=self._current_user.label), my_id
        )
        try:
            for u in self.db.list_users():
                if u.is_active and u.id != my_id:
                    self.staff_combo.addItem(f"{u.label} (@{u.username})", u.id)
        except Exception:
            pass
        idx = self.staff_combo.findData(prev_uid)
        self.staff_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.staff_combo.blockSignals(False)

    def _target_user_id(self) -> int | None:
        if self._current_user and self._current_user.is_admin and self.staff_combo.count() > 0:
            data = self.staff_combo.currentData()
            return int(data) if data is not None else self._current_user.id
        return self._current_user.id if self._current_user else None

    def retranslate_ui(self) -> None:
        self.setLayoutDirection(layout_direction())
        self.setWindowTitle(tr("shift.dialog.title"))
        self.title_lbl.setText(tr("shift.dialog.title"))
        self.subtitle_lbl.setText(tr("shift.dialog.subtitle"))
        self.staff_filter_label.setText(tr("shift.staff_label"))
        self._reload_staff_combo()

        self.open_heading_lbl.setText(tr("shift.open.heading"))
        self.open_desc_lbl.setText(tr("shift.open.desc"))
        self.opening_cash_lbl.setText(tr("shift.open.opening_cash_label"))
        self.opening_cash_edit.setPlaceholderText(tr("shift.open.opening_cash_ph"))
        self.opening_notes_lbl.setText(tr("shift.open.notes_label"))
        self.open_notes_edit.setPlaceholderText(tr("shift.open.notes_ph"))
        self.open_shift_btn.setText(_esc_amp(tr("shift.open.btn")))

        kpi_keys = (
            "shift.kpi.opening",
            "shift.kpi.cash_sales",
            "shift.kpi.digital_sales",
            "shift.kpi.cash_refunds",
            "shift.kpi.cash_in_out",
            "shift.kpi.expected_cash",
        )
        for lbl, key in zip(self._kpi_titles, kpi_keys):
            lbl.setText(tr(key))

        self.mv_heading_lbl.setText(tr("shift.mv.heading"))
        self.mv_type_lbl.setText(tr("shift.mv.type_label"))
        prev_mv = self.mv_type_combo.currentData()
        self.mv_type_combo.clear()
        self.mv_type_combo.addItem(tr("shift.mv.type_in"), "CASH_IN")
        self.mv_type_combo.addItem(tr("shift.mv.type_out"), "CASH_OUT")
        mv_idx = self.mv_type_combo.findData(prev_mv)
        if mv_idx >= 0:
            self.mv_type_combo.setCurrentIndex(mv_idx)
        self.mv_amt_lbl.setText(tr("shift.mv.amount_label"))
        self.mv_amount_edit.setPlaceholderText(tr("shift.mv.amount_ph"))
        self.mv_reason_lbl.setText(tr("shift.mv.reason_label"))
        self.mv_reason_edit.setPlaceholderText(tr("shift.mv.reason_ph"))
        self.record_mv_btn.setText(_esc_amp(tr("shift.mv.record_btn")))

        self.close_heading_lbl.setText(tr("shift.close.heading"))
        self.counted_cash_lbl.setText(tr("shift.close.counted_label"))
        self.counted_cash_edit.setPlaceholderText(tr("shift.close.counted_ph"))
        self.exact_counted_btn.setText(_esc_amp(tr("shift.close.exact_btn")))
        self.discrepancy_title_lbl.setText(tr("shift.close.discrepancy_label"))
        self.close_notes_lbl.setText(tr("shift.close.notes_label"))
        self.close_notes_edit.setPlaceholderText(tr("shift.close.notes_ph"))
        self.close_shift_btn.setText(_esc_amp(tr("shift.close.btn")))

        self.hist_title_lbl.setText(tr("shift.history.heading"))
        self.export_csv_btn.setText(_esc_amp(tr("shift.export_csv_btn")))
        self.history_table.setHorizontalHeaderLabels(
            [
                tr("shift.col.id"),
                tr("shift.col.cashier"),
                tr("shift.col.status"),
                tr("shift.col.opened"),
                tr("shift.col.expected"),
                tr("shift.col.counted"),
                tr("shift.col.discrepancy"),
            ]
        )
        self.close_dlg_btn.setText(_esc_amp(tr("common.close")))

        on_primary = contrast_text_hex(self.config.accent_color)
        self.open_shift_btn.setIcon(get_svg_icon("play", on_primary, 14))
        self.close_shift_btn.setIcon(get_svg_icon("lock", on_primary, 14))

    def refresh(self) -> None:
        target_uid = self._target_user_id()
        self._active_shift = None
        if target_uid is not None:
            try:
                self._active_shift = self.db.get_active_shift(target_uid)
            except Exception:
                self._active_shift = None

        if self._active_shift is None:
            self.state_stack.setCurrentIndex(0)
            self.status_badge.setText(tr("shift.status.none"))
            self.status_badge.setProperty("class", "CountBadge")
        else:
            sh = self._active_shift
            self.state_stack.setCurrentIndex(1)
            self.status_badge.setText(tr("shift.status.open_badge"))
            self.status_badge.setProperty("class", "PrimaryBadge")
            self.active_info_lbl.setText(
                tr(
                    "shift.active.banner",
                    cashier=sh.cashier_name,
                    opened=format_local_timestamp(sh.opened_at),
                    orders=sh.order_count,
                )
            )
            net_io = sh.cash_in_minor - sh.cash_out_minor
            io_str = (
                f"+{format_minor(net_io, sh.currency)}"
                if net_io >= 0
                else f"-{format_minor(abs(net_io), sh.currency)}"
            )
            vals = (
                format_minor(sh.opening_cash_minor, sh.currency),
                format_minor(sh.cash_sales_minor, sh.currency),
                format_minor(sh.digital_sales_minor, sh.currency),
                format_minor(sh.cash_refunds_minor, sh.currency),
                io_str,
                format_minor(sh.expected_cash_minor, sh.currency),
            )
            for lbl, val in zip(self._kpi_vals, vals):
                lbl.setText(val)
            self._update_discrepancy_preview()

        self.status_badge.style().unpolish(self.status_badge)
        self.status_badge.style().polish(self.status_badge)

        try:
            uid_filter = (
                target_uid
                if (self._current_user and not self._current_user.is_admin)
                else None
            )
            self._shifts_history = self.db.list_shifts(user_id=uid_filter, limit=40)
        except Exception:
            self._shifts_history = []

        self.history_table.setRowCount(len(self._shifts_history))
        for r_idx, sh in enumerate(self._shifts_history):
            cur = sh.currency
            id_item = QTableWidgetItem(f"#{sh.id}")
            name_item = QTableWidgetItem(sh.cashier_name)
            st_item = QTableWidgetItem(
                tr("shift.status.open") if sh.status == "OPEN" else tr("shift.status.closed")
            )
            opened_item = QTableWidgetItem(format_local_timestamp(sh.opened_at))
            exp_item = QTableWidgetItem(format_minor(sh.expected_cash_minor, cur))
            exp_item.setTextAlignment(Qt.AlignTrailing | Qt.AlignVCenter)
            cnt_item = QTableWidgetItem(
                format_minor(sh.counted_cash_minor, cur) if sh.status == "CLOSED" else "-"
            )
            cnt_item.setTextAlignment(Qt.AlignTrailing | Qt.AlignVCenter)
            if sh.status == "CLOSED":
                d = sh.discrepancy_minor
                d_txt = (
                    format_minor(0, cur)
                    if d == 0
                    else (f"+{format_minor(d, cur)}" if d > 0 else f"-{format_minor(abs(d), cur)}")
                )
            else:
                d_txt = "-"
            disc_item = QTableWidgetItem(d_txt)
            disc_item.setTextAlignment(Qt.AlignTrailing | Qt.AlignVCenter)

            for c_idx, itm in enumerate(
                (id_item, name_item, st_item, opened_item, exp_item, cnt_item, disc_item)
            ):
                self.history_table.setItem(r_idx, c_idx, itm)
            self.history_table.setRowHeight(r_idx, 34)

        self._sync_export_btn()

    def _fill_exact_expected(self) -> None:
        if self._active_shift is None:
            return
        self.counted_cash_edit.setText(
            format_major_input(self._active_shift.expected_cash_minor, self._active_shift.currency)
        )

    def _update_discrepancy_preview(self) -> None:
        if self._active_shift is None:
            self.discrepancy_val_lbl.setText("-")
            return
        raw = self.counted_cash_edit.text().strip()
        if not raw:
            self.discrepancy_val_lbl.setText("-")
            self.discrepancy_val_lbl.setStyleSheet("font-weight: 700; color: #5E6B5F;")
            return
        try:
            counted = parse_major_to_minor(raw, self._active_shift.currency)
        except ValueError:
            self.discrepancy_val_lbl.setText(tr("register.invalid_amount_label"))
            self.discrepancy_val_lbl.setStyleSheet("font-weight: 700; color: #B23A3A;")
            return
        diff = counted - self._active_shift.expected_cash_minor
        cur = self._active_shift.currency
        if diff == 0:
            self.discrepancy_val_lbl.setText(tr("shift.discrepancy.exact"))
            self.discrepancy_val_lbl.setStyleSheet("font-weight: 700; color: #005B37;")
        elif diff > 0:
            self.discrepancy_val_lbl.setText(
                tr("shift.discrepancy.over", amount=format_minor(diff, cur))
            )
            self.discrepancy_val_lbl.setStyleSheet("font-weight: 700; color: #1E6B52;")
        else:
            self.discrepancy_val_lbl.setText(
                tr("shift.discrepancy.short", amount=format_minor(abs(diff), cur))
            )
            self.discrepancy_val_lbl.setStyleSheet("font-weight: 700; color: #B23A3A;")

    def _on_open_shift(self) -> None:
        raw = self.opening_cash_edit.text().strip()
        if not raw:
            if getattr(self.config, "require_opening_cash", False):
                QMessageBox.warning(
                    self, tr("common.validation_error"), tr("shift.err.opening_cash_required")
                )
                return
            opening_minor = 0
        else:
            try:
                opening_minor = parse_major_to_minor(raw, self.config.currency)
            except ValueError as exc:
                QMessageBox.warning(self, tr("common.validation_error"), str(exc))
                return
        if opening_minor < 0:
            QMessageBox.warning(
                self, tr("common.validation_error"), tr("shift.err.opening_cash_nonneg")
            )
            return

        target_uid = self._target_user_id()
        try:
            self.db.open_shift(
                opening_minor,
                notes=self.open_notes_edit.text(),
                target_user_id=target_uid,
            )
        except Exception as exc:
            QMessageBox.warning(self, tr("common.validation_error"), str(exc))
            return

        self.open_notes_edit.clear()
        self.counted_cash_edit.clear()
        self.refresh()
        self.shift_changed.emit()

    def _on_record_movement(self) -> None:
        if self._active_shift is None:
            return
        raw = self.mv_amount_edit.text().strip()
        if not raw:
            QMessageBox.warning(
                self, tr("common.validation_error"), tr("shift.err.movement_positive")
            )
            return
        try:
            amt = parse_major_to_minor(raw, self._active_shift.currency)
        except ValueError as exc:
            QMessageBox.warning(self, tr("common.validation_error"), str(exc))
            return
        mv_type = str(self.mv_type_combo.currentData() or "CASH_IN")
        try:
            self.db.record_shift_cash_movement(
                self._active_shift.id,
                mv_type,
                amt,
                reason=self.mv_reason_edit.text(),
            )
        except Exception as exc:
            QMessageBox.warning(self, tr("common.validation_error"), str(exc))
            return
        self.mv_amount_edit.clear()
        self.mv_reason_edit.clear()
        self.refresh()
        self.shift_changed.emit()

    def _on_close_shift(self) -> None:
        if self._active_shift is None:
            return
        raw = self.counted_cash_edit.text().strip()
        if not raw:
            QMessageBox.warning(
                self, tr("common.validation_error"), tr("shift.err.counted_required")
            )
            return
        try:
            counted = parse_major_to_minor(raw, self._active_shift.currency)
        except ValueError as exc:
            QMessageBox.warning(self, tr("common.validation_error"), str(exc))
            return
        try:
            self.db.close_shift(
                self._active_shift.id,
                counted,
                notes=self.close_notes_edit.text(),
            )
        except Exception as exc:
            QMessageBox.warning(self, tr("common.validation_error"), str(exc))
            return
        self.counted_cash_edit.clear()
        self.close_notes_edit.clear()
        self.refresh()
        self.shift_changed.emit()

    def _selected_shift_for_export(self) -> ShiftRecord | None:
        sel = self.history_table.selectionModel().selectedRows()
        if sel:
            r = sel[0].row()
            if 0 <= r < len(self._shifts_history):
                return self._shifts_history[r]
        if self._active_shift is not None:
            return self._active_shift
        if self._shifts_history:
            return self._shifts_history[0]
        return None

    def _sync_export_btn(self) -> None:
        self.export_csv_btn.setEnabled(self._selected_shift_for_export() is not None)

    def _on_export_csv(self) -> None:
        sh = self._selected_shift_for_export()
        if sh is None:
            return
        default_dir = get_exports_dir()
        default_path = str(default_dir / f"Shift-{sh.id}-{sh.cashier_name or 'cashier'}.csv")
        path_str, _ = QFileDialog.getSaveFileName(
            self,
            tr("shift.export_dialog_title"),
            default_path,
            tr("shift.csv_filter"),
        )
        if not path_str:
            return
        try:
            self.db.export_shift_csv(sh.id, Path(path_str))
        except Exception as exc:
            QMessageBox.critical(self, tr("common.error"), str(exc))
            return
        QMessageBox.information(
            self,
            tr("shift.export_done_title"),
            tr("shift.export_done_body", path=path_str),
        )

