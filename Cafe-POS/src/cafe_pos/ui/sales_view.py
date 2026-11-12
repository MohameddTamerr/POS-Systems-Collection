from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QItemSelectionModel, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..auth import AuthorizationError, UserAccount
from ..config import BusinessConfig
from ..db import Database, OrderRecord, OrderSummary
from ..icons import get_svg_icon
from ..i18n import format_order_type_summary, tr, tr_count, tr_payment_method, tr_payment_status
from ..money import format_minor
from ..paths import get_receipts_dir
from ..payments import (
    METHOD_CARD,
    METHOD_CASH,
    METHOD_CUSTOM,
    METHOD_EWALLET,
    METHOD_INSTAPAY,
)
from ..receipts import (
    build_receipt_document,
    export_receipt_pdf,
    format_local_timestamp,
    print_receipt_native,
)
from ..theme import contrast_text_hex
from .animations import attach_button_feedback
from .refund_dialog import RefundOrderDialog
from .widgets import EmptyStateWidget, SearchLineEdit

DATE_FILTERS: list[tuple[str, str]] = [
    ("sales.period.all_dates", "all"),
    ("sales.period.today", "today"),
    ("sales.period.week", "week"),
    ("sales.period.month", "month"),
]


def _esc_amp(text: str) -> str:
    return (text or "").replace("&", "&&")


class SalesView(QWidget):
    """Boutique Café Sales History with cashier attribution, filters, and receipt inspector."""

    status_message = Signal(str)
    refund_completed = Signal()

    def __init__(self, db: Database, config: BusinessConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.db = db
        self.config = config
        self._current_user: UserAccount | None = None
        self._orders: list[OrderSummary] = []
        self._selected_order: OrderRecord | None = None
        self._date_filter: str = "all"
        self._date_buttons: list[tuple[str, QPushButton]] = []

        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 10, 16, 12)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(12)

        # ================= LEFT PANE =================
        left_panel = QFrame()
        left_panel.setProperty("class", "BoutiqueCard")
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(16, 14, 16, 16)
        left_layout.setSpacing(10)

        hdr_row = QHBoxLayout()
        hdr_row.setSpacing(8)
        self.title_lbl = QLabel("")
        self.title_lbl.setProperty("class", "PageHeading")

        self.summary_badge = QLabel("")
        self.summary_badge.setProperty("class", "CountBadge")

        self.scope_badge = QLabel("")
        self.scope_badge.setProperty("class", "PrimaryBadge")
        self.scope_badge.hide()

        self.cashier_filter = QComboBox()
        self.cashier_filter.setMinimumWidth(124)
        self.cashier_filter.setMaximumWidth(180)
        self.cashier_filter.addItem("", "all")
        self.cashier_filter.currentIndexChanged.connect(self.refresh)
        self.cashier_filter.hide()

        self.payment_filter = QComboBox()
        self.payment_filter.setMinimumWidth(116)
        self.payment_filter.setMaximumWidth(165)
        self._populate_payment_filter()
        self.payment_filter.currentIndexChanged.connect(self.refresh)

        self.search_input = SearchLineEdit("")
        self.search_input.setMinimumWidth(145)
        self.search_input.setMaximumWidth(230)
        self.search_input.textChanged.connect(self.refresh)

        hdr_row.addWidget(self.title_lbl)
        hdr_row.addWidget(self.summary_badge)
        hdr_row.addWidget(self.scope_badge)
        hdr_row.addStretch(1)
        hdr_row.addWidget(self.payment_filter)
        hdr_row.addWidget(self.cashier_filter)
        hdr_row.addWidget(self.search_input)
        left_layout.addLayout(hdr_row)

        date_row = QHBoxLayout()
        date_row.setSpacing(6)
        cal_lbl = QLabel()
        cal_lbl.setPixmap(get_svg_icon("calendar", "#5E6B5F", 15).pixmap(15, 15))
        date_row.addWidget(cal_lbl)

        for label_key, key in DATE_FILTERS:
            btn = QPushButton(_esc_amp(tr(label_key)))
            btn.setProperty("class", "CategoryChip")
            btn.setProperty("selected", "true" if key == self._date_filter else "false")
            btn.clicked.connect(lambda _c=False, k=key: self._set_date_filter(k))
            self._date_buttons.append((key, btn))
            date_row.addWidget(btn)
        date_row.addStretch(1)

        self.total_filtered_lbl = QLabel("")
        self.total_filtered_lbl.setProperty("class", "MutedLabel")
        date_row.addWidget(self.total_filtered_lbl)
        left_layout.addLayout(date_row)

        self.list_stack = QStackedWidget()

        self.table = QTableWidget(0, 6)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)

        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeToContents)

        self.empty_orders_state = EmptyStateWidget(
            "sales",
            "",
            "",
            accent_color=self.config.accent_color,
        )

        self.list_stack.addWidget(self.table)
        self.list_stack.addWidget(self.empty_orders_state)
        left_layout.addWidget(self.list_stack, 1)

        # ================= RIGHT PANE =================
        right_panel = QFrame()
        right_panel.setProperty("class", "BoutiqueCard")
        right_panel.setMinimumWidth(330)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(16, 14, 16, 16)
        right_layout.setSpacing(10)

        detail_hdr = QHBoxLayout()
        detail_hdr.setSpacing(8)
        self.detail_title_lbl = QLabel("")
        self.detail_title_lbl.setProperty("class", "SectionHeader")

        detail_hdr.addWidget(self.detail_title_lbl, 1)
        right_layout.addLayout(detail_hdr)

        self.meta_card = QFrame()
        self.meta_card.setProperty("class", "CozySubCard")
        meta_row = QHBoxLayout(self.meta_card)
        meta_row.setContentsMargins(11, 7, 11, 7)
        meta_row.setSpacing(8)
        self.meta_left_lbl = QLabel("")
        self.meta_left_lbl.setWordWrap(True)
        self.meta_left_lbl.setStyleSheet("font-size: 12px; font-weight: 600;")
        self.meta_right_lbl = QLabel("")
        self.meta_right_lbl.setWordWrap(True)
        self.meta_right_lbl.setProperty("class", "MutedLabel")
        self.meta_right_lbl.setAlignment(Qt.AlignTrailing | Qt.AlignVCenter)
        meta_row.addWidget(self.meta_left_lbl, 1)
        meta_row.addWidget(self.meta_right_lbl)
        self.meta_card.hide()
        right_layout.addWidget(self.meta_card)

        self.detail_stack = QStackedWidget()

        self.empty_detail_state = EmptyStateWidget(
            "sales",
            "",
            "",
            accent_color=self.config.accent_color,
        )

        self.receipt_preview = QTextEdit()
        self.receipt_preview.setObjectName("ReceiptPreviewEdit")
        self.receipt_preview.setReadOnly(True)

        self.detail_stack.addWidget(self.empty_detail_state)
        self.detail_stack.addWidget(self.receipt_preview)
        right_layout.addWidget(self.detail_stack, 1)

        detail_actions = QHBoxLayout()
        detail_actions.setSpacing(8)
        self.pdf_btn = QPushButton("")
        self.pdf_btn.setIcon(get_svg_icon("pdf", "#202820", 14))
        self.pdf_btn.setIconSize(QSize(14, 14))
        self.pdf_btn.setEnabled(False)
        self.pdf_btn.clicked.connect(self._save_pdf)
        attach_button_feedback(self.pdf_btn)

        self.refund_btn = QPushButton("")
        self.refund_btn.setProperty("role", "danger")
        self.refund_btn.setIcon(get_svg_icon("rotate_ccw", "#B23A3A", 14))
        self.refund_btn.setIconSize(QSize(14, 14))
        self.refund_btn.setEnabled(False)
        self.refund_btn.clicked.connect(self._open_refund_dialog)
        attach_button_feedback(self.refund_btn)

        self.print_btn = QPushButton("")
        self.print_btn.setProperty("role", "primary")
        self.print_btn.setIcon(get_svg_icon("print", "#FFFFFF", 14))
        self.print_btn.setIconSize(QSize(14, 14))
        self.print_btn.setEnabled(False)
        self.print_btn.clicked.connect(self._print_selected)
        attach_button_feedback(self.print_btn)

        detail_actions.addWidget(self.pdf_btn)
        detail_actions.addWidget(self.refund_btn)
        detail_actions.addStretch(1)
        detail_actions.addWidget(self.print_btn)
        right_layout.addLayout(detail_actions)

        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([660, 420])

        outer.addWidget(splitter, 1)
        self._apply_static_texts()

    def _populate_payment_filter(self) -> None:
        prev_data = self.payment_filter.currentData() or "all"
        self.payment_filter.blockSignals(True)
        self.payment_filter.clear()
        for key, value in (
            ("pay.method.all", "all"),
            ("pay.method.cash", METHOD_CASH),
            ("pay.method.apple_pay", "APPLE_PAY"),
            ("pay.method.instapay", METHOD_INSTAPAY),
            ("pay.method.ewallet", METHOD_EWALLET),
            ("pay.method.card", METHOD_CARD),
            ("pay.method.custom", METHOD_CUSTOM),
        ):
            self.payment_filter.addItem(tr(key), value)
        idx = self.payment_filter.findData(prev_data)
        self.payment_filter.setCurrentIndex(idx if idx >= 0 else 0)
        self.payment_filter.blockSignals(False)

    def _apply_static_texts(self) -> None:
        self.title_lbl.setText(tr("sales.title"))
        self.scope_badge.setText(tr("sales.scope.my_sales"))
        self.cashier_filter.setToolTip(tr("sales.cashier_filter_tip"))
        self.payment_filter.setToolTip(tr("sales.filter.payment_tip"))
        self._populate_payment_filter()
        self.search_input.setPlaceholderText(tr("sales.search_ph_v5"))

        key_to_i18n = {key: lkey for lkey, key in DATE_FILTERS}
        for key, btn in self._date_buttons:
            lkey = key_to_i18n.get(key)
            if lkey:
                btn.setText(_esc_amp(tr(lkey)))

        self.table.setHorizontalHeaderLabels(
            [
                tr("sales.col.order"),
                tr("sales.col.datetime"),
                tr("sales.col.cashier"),
                tr("sales.col.total"),
                tr("sales.col.payment_method"),
                tr("sales.col.payment_status"),
            ]
        )
        self.empty_detail_state.set_texts(
            tr("sales.detail.empty_title"),
            tr("sales.detail.empty_desc"),
        )
        self.pdf_btn.setText(_esc_amp(tr("sales.detail.save_pdf_btn")))
        self.refund_btn.setText(_esc_amp(tr("sales.detail.refund_btn")))
        self.print_btn.setText(_esc_amp(tr("sales.inspector.print")))
        if self._selected_order is None:
            self.detail_title_lbl.setText(tr("sales.detail.title"))

    def retranslate_ui(self) -> None:
        self._apply_static_texts()
        self.meta_right_lbl.setAlignment(Qt.AlignTrailing | Qt.AlignVCenter)
        self.refresh()

    def set_config(self, config: BusinessConfig) -> None:
        self.config = config
        self.empty_orders_state.refresh_accent(config.accent_color)
        self.empty_detail_state.refresh_accent(config.accent_color)
        self.print_btn.setIcon(get_svg_icon("print", contrast_text_hex(config.accent_color), 14))
        self.refresh()

    def set_current_user(self, user: UserAccount | None) -> None:
        self._current_user = user
        is_admin = bool(user and user.is_admin)
        is_cashier = bool(user and not user.is_admin)
        self.scope_badge.setVisible(is_cashier)
        self.cashier_filter.setVisible(is_admin)
        self._reload_cashier_filter()
        self.refresh()

    def clear_session_state(self) -> None:
        self._current_user = None
        self._orders = []
        self._selected_order = None
        self.table.setRowCount(0)
        self.summary_badge.setText(tr_count("order", 0))
        self.total_filtered_lbl.clear()
        self.meta_card.hide()
        self.detail_title_lbl.setText(tr("sales.detail.title"))
        self.detail_stack.setCurrentIndex(0)
        self.print_btn.setEnabled(False)
        self.pdf_btn.setEnabled(False)
        self.refund_btn.setEnabled(False)
        self.scope_badge.hide()
        self.cashier_filter.hide()
        self.payment_filter.blockSignals(True)
        self.payment_filter.setCurrentIndex(0)
        self.payment_filter.blockSignals(False)
        self.empty_orders_state.set_texts(
            tr("sales.empty.no_orders_title"),
            tr("sales.empty.no_orders_desc"),
        )
        self.search_input.blockSignals(True)
        self.search_input.clear()
        self.search_input.blockSignals(False)

    def _reload_cashier_filter(self) -> None:
        actor = self.db.session_actor
        self.cashier_filter.blockSignals(True)
        prev_data = self.cashier_filter.currentData()
        self.cashier_filter.clear()
        self.cashier_filter.addItem(tr("sales.cashier_all"), "all")
        if actor is not None and actor.is_admin:
            try:
                for cid, label in self.db.list_order_cashiers():
                    if cid is None:
                        self.cashier_filter.addItem(tr("backend.legacy_unassigned"), "legacy")
                    else:
                        self.cashier_filter.addItem(label, f"user:{cid}")
            except AuthorizationError:
                pass
        idx = self.cashier_filter.findData(prev_data)
        self.cashier_filter.setCurrentIndex(idx if idx >= 0 else 0)
        self.cashier_filter.blockSignals(False)

    def focus_search(self) -> None:
        self.search_input.setFocus()
        self.search_input.selectAll()

    def _set_date_filter(self, key: str) -> None:
        self._date_filter = key
        for btn_key, btn in self._date_buttons:
            btn.setProperty("selected", "true" if btn_key == key else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        self.refresh()

    def refresh(self) -> None:
        actor = self.db.session_actor
        if actor is None and self.db.has_any_users():
            self.clear_session_state()
            return

        prev_selected_id = self._selected_order.id if self._selected_order is not None else None

        cashier_user_id: int | None = None
        legacy_only = False
        if actor is not None and actor.is_admin and not self.cashier_filter.isHidden():
            self._reload_cashier_filter()
            key = str(self.cashier_filter.currentData() or "all")
            if key == "legacy":
                legacy_only = True
            elif key.startswith("user:"):
                try:
                    cashier_user_id = int(key.split(":", 1)[1])
                except ValueError:
                    cashier_user_id = None

        pay_filter = str(self.payment_filter.currentData() or "all")

        try:
            self._orders = self.db.list_orders(
                search=self.search_input.text(),
                date_filter=self._date_filter,
                payment_method=pay_filter,
                cashier_user_id=cashier_user_id,
                legacy_only=legacy_only,
            )
        except AuthorizationError:
            self.clear_session_state()
            return

        count = len(self._orders)
        self.summary_badge.setText(tr_count("order", count))
        total_sales_minor = sum(s.total_minor for s in self._orders)
        self.total_filtered_lbl.setText(
            tr(
                "sales.subtotal_filtered",
                amount=format_minor(total_sales_minor, self.config.currency),
            )
            if count > 0
            else ""
        )

        if not self._orders:
            self.table.setRowCount(0)
            has_filter = (
                bool(self.search_input.text().strip())
                or self._date_filter != "all"
                or pay_filter != "all"
                or legacy_only
                or cashier_user_id is not None
            )
            if has_filter:
                self.empty_orders_state.set_texts(
                    tr("sales.empty.filtered_title"),
                    tr("sales.empty.filtered_desc"),
                )
            else:
                self.empty_orders_state.set_texts(
                    tr("sales.empty.no_orders_title"),
                    tr("sales.empty.no_orders_list_desc"),
                )
            self.list_stack.setCurrentIndex(1)

            self._selected_order = None
            self.detail_title_lbl.setText(tr("sales.detail.title"))
            self.meta_card.hide()
            self.detail_stack.setCurrentIndex(0)
            self.print_btn.setEnabled(False)
            self.pdf_btn.setEnabled(False)
            self.refund_btn.setEnabled(False)
            return

        self.list_stack.setCurrentIndex(0)
        self.table.setRowCount(count)
        target_row = 0
        for row_idx, summary in enumerate(self._orders):
            if prev_selected_id is not None and summary.id == prev_selected_id:
                target_row = row_idx
            num_item = QTableWidgetItem(summary.order_number)
            f = num_item.font()
            f.setBold(True)
            num_item.setFont(f)

            date_item = QTableWidgetItem(format_local_timestamp(summary.created_at))

            cashier_text = summary.cashier_name_snapshot.strip() or (
                tr("sales.detail.legacy_cashier")
                if summary.cashier_user_id is None
                else tr("sales.detail.user_id", id=summary.cashier_user_id)
            )
            cashier_item = QTableWidgetItem(cashier_text)

            tot_item = QTableWidgetItem(format_minor(summary.total_minor, summary.currency))
            tot_item.setTextAlignment(Qt.AlignTrailing | Qt.AlignVCenter)
            tot_item.setFont(f)

            method_txt = tr_payment_method(
                summary.payment_method,
                custom_label=summary.custom_method_label,
                provider=summary.payment_provider,
            )
            method_item = QTableWidgetItem(method_txt)
            status_txt = tr_payment_status(summary.payment_status)
            if summary.refunded_total_minor > 0:
                if summary.refunded_total_minor >= summary.total_minor:
                    status_txt = tr("sales.status.fully_refunded")
                else:
                    status_txt = f"{status_txt} · {tr('sales.status.partial_refund')}"
            status_item = QTableWidgetItem(status_txt)

            self.table.setItem(row_idx, 0, num_item)
            self.table.setItem(row_idx, 1, date_item)
            self.table.setItem(row_idx, 2, cashier_item)
            self.table.setItem(row_idx, 3, tot_item)
            self.table.setItem(row_idx, 4, method_item)
            self.table.setItem(row_idx, 5, status_item)
            self.table.setRowHeight(row_idx, 42)

        self.table.blockSignals(True)
        idx = self.table.model().index(target_row, 0)
        sel_model = self.table.selectionModel()
        if sel_model is not None and idx.isValid():
            sel_model.select(idx, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
            self.table.setCurrentIndex(idx)
        self.table.blockSignals(False)
        self._on_selection_changed()

    def _on_selection_changed(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            self._selected_order = None
            self.detail_title_lbl.setText(tr("sales.detail.title"))
            self.meta_card.hide()
            self.detail_stack.setCurrentIndex(0)
            self.print_btn.setEnabled(False)
            self.pdf_btn.setEnabled(False)
            self.refund_btn.setEnabled(False)
            return

        row = rows[0].row()
        if not (0 <= row < len(self._orders)):
            return

        try:
            order = self.db.get_order(self._orders[row].id)
        except AuthorizationError:
            order = None
        self._selected_order = order
        if order is None:
            self.detail_title_lbl.setText(tr("sales.detail.title"))
            self.meta_card.hide()
            self.detail_stack.setCurrentIndex(0)
            self.print_btn.setEnabled(False)
            self.pdf_btn.setEnabled(False)
            self.refund_btn.setEnabled(False)
            return

        rcp = order.receipt_number or order.order_number
        self.detail_title_lbl.setText(tr("sales.detail.title_order", num=order.order_number))
        total_qty = sum(i.quantity for i in order.items)
        cashier_part = (
            tr("sales.detail.meta_cashier", name=order.cashier_name_snapshot)
            if order.cashier_name_snapshot
            else ""
        )
        left_txt = tr(
            "sales.detail.meta_left",
            items=tr_count("item", total_qty),
            total=format_minor(order.total_minor, order.currency),
            cashier=cashier_part,
        )
        ot_summary = format_order_type_summary(
            order.order_type,
            table_number=order.table_number,
            customer_name=order.customer_name,
        )
        ot_part = tr("sales.detail.order_type", summary=ot_summary)
        self.meta_left_lbl.setText(
            f"{left_txt} · {ot_part} · {tr('sales.detail.receipt_num', rcp=rcp)}"
        )

        pm = order.primary_payment
        if order.is_split_payment:
            parts = []
            for p_leg in order.payments:
                lbl = tr_payment_method(
                    p_leg.payment_method,
                    custom_label=p_leg.custom_method_label,
                    provider=p_leg.payment_provider,
                )
                parts.append(f"{lbl}: {format_minor(p_leg.payment_amount_minor, order.currency)}")
            self.meta_right_lbl.setText(tr("sales.detail.meta_split", legs=" + ".join(parts)))
        elif pm is not None:
            method_txt = tr_payment_method(
                pm.payment_method,
                custom_label=pm.custom_method_label,
                provider=pm.payment_provider,
            )
            ref_txt = f" ({pm.external_reference})" if pm.external_reference else ""
            status_txt = tr_payment_status(pm.payment_status)
            self.meta_right_lbl.setText(
                tr("sales.detail.meta_pay", method=method_txt, ref=ref_txt, status=status_txt)
            )
        else:
            self.meta_right_lbl.setText(
                tr(
                    "sales.detail.meta_right",
                    cash=format_minor(order.cash_received_minor, order.currency),
                    change=format_minor(order.change_minor, order.currency),
                )
            )
        if order.refunded_total_minor > 0:
            self.meta_left_lbl.setText(
                self.meta_left_lbl.text()
                + f" · {tr('sales.detail.refunded_meta', amount=format_minor(order.refunded_total_minor, order.currency), net=format_minor(order.net_paid_minor, order.currency))}"
            )
        self.meta_card.show()

        self.receipt_preview.setDocument(build_receipt_document(order, self.config))
        self.detail_stack.setCurrentIndex(1)
        self.print_btn.setEnabled(True)
        self.pdf_btn.setEnabled(True)
        self.refund_btn.setEnabled(self._can_refund_selected(order))

    def _can_refund_selected(self, order: OrderRecord | None) -> bool:
        if order is None or order.net_paid_minor <= 0:
            return False
        actor = self._current_user or self.db.session_actor
        if actor is not None and not actor.is_admin:
            return bool(getattr(self.config, "allow_cashier_refunds", False))
        return True

    def _open_refund_dialog(self) -> None:
        if self._selected_order is None:
            return
        if not self._can_refund_selected(self._selected_order):
            QMessageBox.warning(self, tr("common.validation_error"), tr("refund.err.admin_only"))
            return
        dlg = RefundOrderDialog(self.db, self.config, self._selected_order, parent=self)
        win = self.window()
        if hasattr(win, "clamp_dialog_to_screen"):
            win.clamp_dialog_to_screen(dlg)
        if dlg.exec() == QDialog.Accepted:
            self.refresh()
            self.refund_completed.emit()
            self.status_message.emit(
                tr("sales.status.refund_recorded", num=self._selected_order.order_number)
            )

    def _print_selected(self) -> None:
        if not self._selected_order:
            return
        try:
            print_receipt_native(self, self._selected_order, self.config)
        except Exception as exc:
            QMessageBox.critical(
                self,
                tr("sales.error.print_title"),
                tr("sales.error.print_msg", exc=exc),
            )

    def _save_pdf(self) -> None:
        if not self._selected_order:
            return
        receipt_num = self._selected_order.receipt_number or self._selected_order.order_number
        default_dir = get_receipts_dir(self.config.receipt_export_dir)
        default_path = str(default_dir / f"Receipt-{receipt_num}.pdf")
        path_str, _ = QFileDialog.getSaveFileName(
            self,
            tr("sales.save_pdf_dialog_title"),
            default_path,
            tr("sales.pdf_dialog_filter"),
        )
        if not path_str:
            return
        try:
            export_receipt_pdf(Path(path_str), self._selected_order, self.config)
        except Exception as exc:
            QMessageBox.critical(
                self,
                tr("sales.error.pdf_title"),
                tr("sales.error.pdf_msg", exc=exc),
            )
            return
        self.status_message.emit(tr("sales.status.pdf_saved", path=path_str))
