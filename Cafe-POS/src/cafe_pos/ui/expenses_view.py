from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import QDate, QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDateEdit,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..auth import AuthorizationError, Permission, has_permission
from ..config import BusinessConfig
from ..db import EXPENSE_CATEGORIES, Database, ExpenseRecord
from ..icons import get_svg_icon
from ..i18n import layout_direction, tr
from ..money import format_major_input, format_minor, parse_major_to_minor
from ..theme import contrast_text_hex, get_tokens
from .animations import animate_window_fade_in, attach_button_feedback
from .widgets import EmptyStateWidget


def _esc_amp(text: str) -> str:
    return (text or "").replace("&", "&&")


def tr_expense_category(category: str) -> str:
    key = f"expenses.cat.{(category or 'OTHER').strip().lower()}"
    val = tr(key)
    return category if val == key else val


class ExpenseEditorDialog(QDialog):
    """Create or edit a single operating expense entry."""

    def __init__(
        self,
        config: BusinessConfig,
        *,
        existing: ExpenseRecord | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.config = config
        self.existing = existing
        self._did_animate_in = False

        self.result_category: str = existing.category if existing else EXPENSE_CATEGORIES[0]
        self.result_title: str = existing.title if existing else ""
        self.result_amount_minor: int = existing.amount_minor if existing else 0
        self.result_date: str = existing.expense_date if existing else date.today().isoformat()
        self.result_notes: str = existing.notes if existing else ""

        self.setModal(True)
        self.setMinimumWidth(420)
        self.resize(450, 340)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        card = QFrame()
        card.setProperty("class", "BoutiqueCard")
        form = QFormLayout(card)
        form.setContentsMargins(16, 14, 16, 14)
        form.setSpacing(10)

        self.cat_combo = QComboBox()
        for cat in EXPENSE_CATEGORIES:
            self.cat_combo.addItem(tr_expense_category(cat), cat)
        if existing is not None:
            idx = self.cat_combo.findData(existing.category)
            if idx >= 0:
                self.cat_combo.setCurrentIndex(idx)

        self.title_edit = QLineEdit(existing.title if existing else "")
        self.title_edit.setMaxLength(100)

        self.amount_edit = QLineEdit(
            format_major_input(existing.amount_minor, config.currency) if existing else ""
        )
        self.amount_edit.setPlaceholderText("0.00")

        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        if existing is not None:
            qd = QDate.fromString(existing.expense_date, "yyyy-MM-dd")
            self.date_edit.setDate(qd if qd.isValid() else QDate.currentDate())
        else:
            self.date_edit.setDate(QDate.currentDate())

        self.notes_edit = QLineEdit(existing.notes if existing else "")
        self.notes_edit.setMaxLength(220)

        self.cat_lbl = QLabel()
        self.title_lbl = QLabel()
        self.amount_lbl = QLabel()
        self.date_lbl = QLabel()
        self.notes_lbl = QLabel()

        form.addRow(self.cat_lbl, self.cat_combo)
        form.addRow(self.title_lbl, self.title_edit)
        form.addRow(self.amount_lbl, self.amount_edit)
        form.addRow(self.date_lbl, self.date_edit)
        form.addRow(self.notes_lbl, self.notes_edit)
        layout.addWidget(card)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.cancel_btn = QPushButton()
        self.cancel_btn.clicked.connect(self.reject)
        self.save_btn = QPushButton()
        self.save_btn.setProperty("role", "primary")
        self.save_btn.setDefault(True)
        self.save_btn.clicked.connect(self._on_save)
        attach_button_feedback(self.cancel_btn)
        attach_button_feedback(self.save_btn)
        btn_row.addWidget(self.cancel_btn)
        btn_row.addWidget(self.save_btn)
        layout.addLayout(btn_row)

        self.retranslate_ui()

    def retranslate_ui(self) -> None:
        self.setLayoutDirection(layout_direction())
        self.setWindowTitle(
            tr("expenses.dlg.edit_title") if self.existing else tr("expenses.dlg.add_title")
        )
        self.cat_lbl.setText(tr("expenses.col.category"))
        self.title_lbl.setText(tr("expenses.col.title"))
        self.title_edit.setPlaceholderText(tr("expenses.dlg.title_ph"))
        self.amount_lbl.setText(tr("expenses.col.amount"))
        self.date_lbl.setText(tr("expenses.col.date"))
        self.notes_lbl.setText(tr("expenses.col.notes"))
        self.notes_edit.setPlaceholderText(tr("expenses.dlg.notes_ph"))
        self.cancel_btn.setText(_esc_amp(tr("common.cancel")))
        self.save_btn.setText(_esc_amp(tr("common.save")))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._did_animate_in:
            self._did_animate_in = True
            animate_window_fade_in(self)

    def _on_save(self) -> None:
        title = " ".join(self.title_edit.text().split())
        if not title:
            QMessageBox.warning(self, tr("expenses.heading"), tr("expenses.err.title_required"))
            self.title_edit.setFocus()
            return
        try:
            amt = parse_major_to_minor(self.amount_edit.text(), self.config.currency)
        except Exception as exc:
            QMessageBox.warning(self, tr("expenses.heading"), str(exc))
            self.amount_edit.setFocus()
            return
        if amt <= 0:
            QMessageBox.warning(self, tr("expenses.heading"), tr("expenses.err.amount_positive"))
            self.amount_edit.setFocus()
            return

        self.result_category = str(self.cat_combo.currentData() or "OTHER")
        self.result_title = title
        self.result_amount_minor = amt
        self.result_date = self.date_edit.date().toString("yyyy-MM-dd")
        self.result_notes = " ".join(self.notes_edit.text().split())
        self.accept()


class ExpensesView(QWidget):
    """Operating expenses ledger (separate from recipe ingredient COGS)."""

    expenses_changed = Signal()
    status_message = Signal(str)

    def __init__(self, db: Database, config: BusinessConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.db = db
        self.config = config
        self._records: list[ExpenseRecord] = []

        self._build_ui()
        self.retranslate_ui()
        self.refresh()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)

        # Toolbar card
        top_card = QFrame()
        top_card.setProperty("class", "BoutiqueCard")
        top_lay = QVBoxLayout(top_card)
        top_lay.setContentsMargins(14, 12, 14, 12)
        top_lay.setSpacing(8)

        r1 = QHBoxLayout()
        r1.setSpacing(8)
        self.heading_lbl = QLabel()
        self.heading_lbl.setProperty("class", "SectionHeader")

        self.cogs_note_lbl = QLabel()
        self.cogs_note_lbl.setProperty("class", "MutedLabel")
        self.cogs_note_lbl.setWordWrap(True)

        self.total_badge = QLabel()
        self.total_badge.setProperty("class", "PrimaryBadge")

        self.add_btn = QPushButton()
        self.add_btn.setProperty("role", "primary")
        self.add_btn.setCursor(Qt.PointingHandCursor)
        self.add_btn.clicked.connect(self.open_add_dialog)
        attach_button_feedback(self.add_btn)

        hdr_col = QVBoxLayout()
        hdr_col.setSpacing(2)
        hdr_col.addWidget(self.heading_lbl)
        hdr_col.addWidget(self.cogs_note_lbl)

        r1.addLayout(hdr_col, 1)
        r1.addWidget(self.total_badge)
        r1.addWidget(self.add_btn)
        top_lay.addLayout(r1)

        r2 = QHBoxLayout()
        r2.setSpacing(8)
        self.from_lbl = QLabel()
        self.from_lbl.setProperty("class", "MutedLabel")
        self.start_date_edit = QDateEdit()
        self.start_date_edit.setCalendarPopup(True)
        self.start_date_edit.setDisplayFormat("yyyy-MM-dd")
        self.start_date_edit.setDate(QDate.fromString((date.today() - timedelta(days=30)).isoformat(), "yyyy-MM-dd"))
        self.start_date_edit.dateChanged.connect(lambda _d: self.refresh())

        self.to_lbl = QLabel()
        self.to_lbl.setProperty("class", "MutedLabel")
        self.end_date_edit = QDateEdit()
        self.end_date_edit.setCalendarPopup(True)
        self.end_date_edit.setDisplayFormat("yyyy-MM-dd")
        self.end_date_edit.setDate(QDate.currentDate())
        self.end_date_edit.dateChanged.connect(lambda _d: self.refresh())

        self.cat_filter_lbl = QLabel()
        self.cat_filter_lbl.setProperty("class", "MutedLabel")
        self.cat_filter_combo = QComboBox()
        self.cat_filter_combo.currentIndexChanged.connect(lambda _i: self.refresh())

        self.refresh_btn = QPushButton()
        self.refresh_btn.setIcon(get_svg_icon("refresh", "#202820", 13))
        self.refresh_btn.setIconSize(QSize(13, 13))
        self.refresh_btn.clicked.connect(self.refresh)
        attach_button_feedback(self.refresh_btn)

        r2.addWidget(self.from_lbl)
        r2.addWidget(self.start_date_edit)
        r2.addWidget(self.to_lbl)
        r2.addWidget(self.end_date_edit)
        r2.addWidget(self.cat_filter_lbl)
        r2.addWidget(self.cat_filter_combo)
        r2.addStretch(1)
        r2.addWidget(self.refresh_btn)
        top_lay.addLayout(r2)
        outer.addWidget(top_card)

        # Table card
        tbl_card = QFrame()
        tbl_card.setProperty("class", "BoutiqueCard")
        tbl_lay = QVBoxLayout(tbl_card)
        tbl_lay.setContentsMargins(14, 12, 14, 12)
        tbl_lay.setSpacing(8)

        self.stack = QStackedWidget()
        self.table = QTableWidget(0, 7)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(5, QHeaderView.Stretch)
        hdr.setSectionResizeMode(6, QHeaderView.ResizeToContents)

        self.empty_widget = EmptyStateWidget(
            "reports",
            "",
            "",
            accent_color=self.config.accent_color,
        )
        self.stack.addWidget(self.table)
        self.stack.addWidget(self.empty_widget)
        tbl_lay.addWidget(self.stack, 1)
        outer.addWidget(tbl_card, 1)

    def _populate_cat_filter(self) -> None:
        cur = self.cat_filter_combo.currentData() if self.cat_filter_combo.count() else ""
        self.cat_filter_combo.blockSignals(True)
        self.cat_filter_combo.clear()
        self.cat_filter_combo.addItem(tr("common.all"), "")
        for cat in EXPENSE_CATEGORIES:
            self.cat_filter_combo.addItem(tr_expense_category(cat), cat)
        idx = self.cat_filter_combo.findData(cur)
        self.cat_filter_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.cat_filter_combo.blockSignals(False)

    def retranslate_ui(self) -> None:
        on_primary = contrast_text_hex(self.config.accent_color)
        self.add_btn.setIcon(get_svg_icon("plus", on_primary, 13))
        self.add_btn.setIconSize(QSize(13, 13))
        self.heading_lbl.setText(tr("expenses.heading"))
        self.cogs_note_lbl.setText(tr("expenses.cogs_note"))
        self.add_btn.setText(_esc_amp(tr("expenses.add_btn")))
        self.from_lbl.setText(tr("reports.filter.from"))
        self.to_lbl.setText(tr("reports.filter.to"))
        self.cat_filter_lbl.setText(tr("expenses.col.category"))
        self.refresh_btn.setText(_esc_amp(tr("common.refresh")))
        self._populate_cat_filter()
        self.table.setHorizontalHeaderLabels(
            [
                tr("expenses.col.date"),
                tr("expenses.col.category"),
                tr("expenses.col.title"),
                tr("expenses.col.amount"),
                tr("expenses.col.created_by"),
                tr("expenses.col.notes"),
                tr("expenses.col.actions"),
            ]
        )
        self.empty_widget.set_texts(tr("expenses.empty_title"), tr("expenses.empty_desc"))
        self.refresh()

    def set_config(self, config: BusinessConfig) -> None:
        self.config = config
        self.empty_widget.refresh_accent(config.accent_color)
        self.retranslate_ui()

    def clear_session_state(self) -> None:
        self._records = []
        self.table.setRowCount(0)
        self.stack.setCurrentIndex(1)
        self.total_badge.setText(tr("expenses.total_badge", amount=format_minor(0, self.config.currency)))

    def refresh(self) -> None:
        actor = self.db.session_actor
        if self.db.has_any_users() and not has_permission(actor, Permission.MANAGE_EXPENSES):
            self.clear_session_state()
            return
        start_s = self.start_date_edit.date().toString("yyyy-MM-dd")
        end_s = self.end_date_edit.date().toString("yyyy-MM-dd")
        cat_s = str(self.cat_filter_combo.currentData() or "")
        try:
            rows = self.db.list_expenses(start_date=start_s, end_date=end_s, category=cat_s)
        except AuthorizationError:
            self.clear_session_state()
            return

        self._records = rows
        curr = self.config.currency
        total_minor = sum(r.amount_minor for r in rows)
        self.total_badge.setText(tr("expenses.total_badge", amount=format_minor(total_minor, curr)))

        if not rows:
            self.table.setRowCount(0)
            self.stack.setCurrentIndex(1)
            return

        self.stack.setCurrentIndex(0)
        t = get_tokens(self.config.accent_color)
        self.table.setRowCount(len(rows))
        for idx, rec in enumerate(rows):
            d_item = QTableWidgetItem(rec.expense_date)
            c_item = QTableWidgetItem(tr_expense_category(rec.category))
            t_item = QTableWidgetItem(rec.title)
            f_b = t_item.font()
            f_b.setBold(True)
            t_item.setFont(f_b)

            amt_item = QTableWidgetItem(format_minor(rec.amount_minor, rec.currency or curr))
            amt_item.setTextAlignment(Qt.AlignTrailing | Qt.AlignVCenter)
            amt_item.setFont(f_b)
            amt_item.setForeground(QColor(t.primary))

            by_item = QTableWidgetItem(rec.created_by_name or "—")
            notes_item = QTableWidgetItem(rec.notes or "")

            self.table.setItem(idx, 0, d_item)
            self.table.setItem(idx, 1, c_item)
            self.table.setItem(idx, 2, t_item)
            self.table.setItem(idx, 3, amt_item)
            self.table.setItem(idx, 4, by_item)
            self.table.setItem(idx, 5, notes_item)

            act_w = QWidget()
            act_lay = QHBoxLayout(act_w)
            act_lay.setContentsMargins(4, 2, 4, 2)
            act_lay.setSpacing(6)
            edit_btn = QPushButton()
            edit_btn.setIcon(get_svg_icon("edit", "#202820", 13))
            edit_btn.setToolTip(tr("common.edit"))
            edit_btn.setFixedSize(28, 26)
            edit_btn.clicked.connect(lambda _c=False, r=rec: self.open_edit_dialog(r))

            del_btn = QPushButton()
            del_btn.setIcon(get_svg_icon("trash", "#B23A3A", 13))
            del_btn.setToolTip(tr("common.delete"))
            del_btn.setFixedSize(28, 26)
            del_btn.clicked.connect(lambda _c=False, r=rec: self._delete_expense(r))

            act_lay.addWidget(edit_btn)
            act_lay.addWidget(del_btn)
            self.table.setCellWidget(idx, 6, act_w)
            self.table.setRowHeight(idx, 40)

    def _clamp_dialog(self, dlg: QDialog) -> None:
        win = self.window()
        if hasattr(win, "clamp_dialog_to_screen"):
            win.clamp_dialog_to_screen(dlg)

    def open_add_dialog(self) -> None:
        dlg = ExpenseEditorDialog(self.config, parent=self)
        self._clamp_dialog(dlg)
        if dlg.exec() != QDialog.Accepted:
            return
        try:
            self.db.create_expense(
                dlg.result_category,
                dlg.result_title,
                dlg.result_amount_minor,
                dlg.result_date,
                notes=dlg.result_notes,
            )
        except Exception as exc:
            QMessageBox.warning(self, tr("expenses.heading"), str(exc))
            return
        self.refresh()
        self.expenses_changed.emit()
        self.status_message.emit(tr("expenses.status.added", title=dlg.result_title))

    def open_edit_dialog(self, rec: ExpenseRecord) -> None:
        dlg = ExpenseEditorDialog(self.config, existing=rec, parent=self)
        self._clamp_dialog(dlg)
        if dlg.exec() != QDialog.Accepted:
            return
        try:
            self.db.update_expense(
                rec.id,
                category=dlg.result_category,
                title=dlg.result_title,
                amount_minor=dlg.result_amount_minor,
                expense_date=dlg.result_date,
                notes=dlg.result_notes,
            )
        except Exception as exc:
            QMessageBox.warning(self, tr("expenses.heading"), str(exc))
            return
        self.refresh()
        self.expenses_changed.emit()
        self.status_message.emit(tr("expenses.status.updated", title=dlg.result_title))

    def _delete_expense(self, rec: ExpenseRecord) -> None:
        res = QMessageBox.question(
            self,
            tr("expenses.delete_confirm_title"),
            tr("expenses.delete_confirm_body", title=rec.title),
        )
        if res != QMessageBox.Yes:
            return
        try:
            self.db.delete_expense(rec.id)
        except Exception as exc:
            QMessageBox.warning(self, tr("expenses.heading"), str(exc))
            return
        self.refresh()
        self.expenses_changed.emit()
        self.status_message.emit(tr("expenses.status.deleted", title=rec.title))
