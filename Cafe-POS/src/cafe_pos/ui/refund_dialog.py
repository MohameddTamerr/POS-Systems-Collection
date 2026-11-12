from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..config import BusinessConfig
from ..db import Database, OrderItemRecord, OrderRecord
from ..i18n import layout_direction, tr, tr_payment_method
from ..icons import get_svg_icon
from ..money import format_minor
from ..payments import METHOD_CASH
from ..theme import contrast_text_hex
from .animations import animate_window_fade_in, attach_button_feedback


def _esc_amp(text: str) -> str:
    return (text or "").replace("&", "&&")


@dataclass
class _RefundRowControls:
    item: OrderItemRecord
    remaining_qty: int
    qty_spin: QSpinBox
    action_combo: QComboBox


class RefundOrderDialog(QDialog):
    """Full or partial itemized refund dialog with stock return / waste choice."""

    def __init__(
        self,
        db: Database,
        config: BusinessConfig,
        order: OrderRecord,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.db = db
        self.config = config
        self.order = order
        self.refund_id: int | None = None
        self._faded_once = False
        self._row_controls: list[_RefundRowControls] = []

        self.setModal(True)
        self.setMinimumSize(640, 480)
        self.resize(700, 540)
        self.setLayoutDirection(layout_direction())

        self._refundable = self.db.get_refundable_item_quantities(order.id)
        self._build_ui()
        self.retranslate_ui()
        self._update_preview()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        hdr = QFrame()
        hdr.setProperty("class", "BoutiqueCard")
        hdr_lay = QHBoxLayout(hdr)
        hdr_lay.setContentsMargins(14, 10, 14, 10)
        hdr_lay.setSpacing(10)

        t_col = QVBoxLayout()
        t_col.setSpacing(2)
        self.title_lbl = QLabel()
        self.title_lbl.setProperty("class", "PageHeading")
        self.subtitle_lbl = QLabel()
        self.subtitle_lbl.setProperty("class", "MutedLabel")
        t_col.addWidget(self.title_lbl)
        t_col.addWidget(self.subtitle_lbl)
        hdr_lay.addLayout(t_col, 1)

        self.select_all_btn = QPushButton()
        self.select_all_btn.setProperty("role", "secondary")
        self.select_all_btn.clicked.connect(self._select_all_remaining)
        attach_button_feedback(self.select_all_btn)
        hdr_lay.addWidget(self.select_all_btn)
        root.addWidget(hdr)

        self.items_table = QTableWidget(0, 5)
        self.items_table.verticalHeader().setVisible(False)
        self.items_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.items_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.items_table.setAlternatingRowColors(True)
        hh = self.items_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeToContents)

        self._row_controls.clear()
        self.items_table.setRowCount(len(self.order.items))
        for r_idx, item in enumerate(self.order.items):
            avail = int(self._refundable.get(item.id, 0))
            name_txt = item.display_name()
            if item.modifiers:
                mods = ", ".join(m.display_option_name() for m in item.modifiers)
                if mods:
                    name_txt = f"{name_txt} ({mods})"
            name_item = QTableWidgetItem(name_txt)
            price_item = QTableWidgetItem(format_minor(item.unit_price_minor, self.order.currency))
            price_item.setTextAlignment(Qt.AlignTrailing | Qt.AlignVCenter)
            avail_item = QTableWidgetItem(str(avail))
            avail_item.setTextAlignment(Qt.AlignCenter)

            qty_spin = QSpinBox()
            qty_spin.setRange(0, avail)
            qty_spin.setValue(0)
            qty_spin.setEnabled(avail > 0)
            qty_spin.valueChanged.connect(self._update_preview)

            action_combo = QComboBox()
            action_combo.setEnabled(avail > 0)

            self.items_table.setItem(r_idx, 0, name_item)
            self.items_table.setItem(r_idx, 1, price_item)
            self.items_table.setItem(r_idx, 2, avail_item)
            self.items_table.setCellWidget(r_idx, 3, qty_spin)
            self.items_table.setCellWidget(r_idx, 4, action_combo)
            self.items_table.setRowHeight(r_idx, 40)

            self._row_controls.append(
                _RefundRowControls(
                    item=item,
                    remaining_qty=avail,
                    qty_spin=qty_spin,
                    action_combo=action_combo,
                )
            )
        root.addWidget(self.items_table, 1)

        form_card = QFrame()
        form_card.setProperty("class", "BoutiqueCard")
        form = QFormLayout(form_card)
        form.setContentsMargins(14, 12, 14, 12)
        form.setSpacing(8)

        self.method_lbl = QLabel()
        self.method_combo = QComboBox()
        self.method_combo.setMinimumHeight(34)

        self.reason_lbl = QLabel()
        self.reason_edit = QLineEdit()
        self.reason_edit.setMaxLength(120)
        self.reason_edit.setMinimumHeight(34)

        self.notes_lbl = QLabel()
        self.notes_edit = QLineEdit()
        self.notes_edit.setMaxLength(180)
        self.notes_edit.setMinimumHeight(34)

        form.addRow(self.method_lbl, self.method_combo)
        form.addRow(self.reason_lbl, self.reason_edit)
        form.addRow(self.notes_lbl, self.notes_edit)
        root.addWidget(form_card)

        footer_card = QFrame()
        footer_card.setProperty("class", "CozySubCard")
        f_lay = QHBoxLayout(footer_card)
        f_lay.setContentsMargins(14, 10, 14, 10)
        f_lay.setSpacing(10)

        self.preview_title_lbl = QLabel()
        self.preview_title_lbl.setProperty("class", "MutedLabel")
        self.refund_preview_lbl = QLabel(format_minor(0, self.order.currency))
        self.refund_preview_lbl.setStyleSheet("font-size: 16px; font-weight: 800; color: #B23A3A;")

        self.cancel_btn = QPushButton()
        self.cancel_btn.setMinimumHeight(38)
        self.cancel_btn.clicked.connect(self.reject)
        attach_button_feedback(self.cancel_btn)

        self.confirm_btn = QPushButton()
        self.confirm_btn.setProperty("role", "danger")
        self.confirm_btn.setMinimumHeight(38)
        self.confirm_btn.setMinimumWidth(160)
        self.confirm_btn.clicked.connect(self._on_submit)
        attach_button_feedback(self.confirm_btn)

        f_lay.addWidget(self.preview_title_lbl)
        f_lay.addWidget(self.refund_preview_lbl)
        f_lay.addStretch(1)
        f_lay.addWidget(self.cancel_btn)
        f_lay.addWidget(self.confirm_btn)
        root.addWidget(footer_card)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if not self._faded_once:
            self._faded_once = True
            animate_window_fade_in(self, duration_ms=145)

    def retranslate_ui(self) -> None:
        self.setLayoutDirection(layout_direction())
        self.setWindowTitle(tr("refund.dialog.title", num=self.order.order_number))
        self.title_lbl.setText(tr("refund.dialog.title", num=self.order.order_number))
        self.subtitle_lbl.setText(
            tr(
                "refund.dialog.subtitle",
                paid=format_minor(self.order.total_minor, self.order.currency),
                refunded=format_minor(self.order.refunded_total_minor, self.order.currency),
                remaining=format_minor(self.order.net_paid_minor, self.order.currency),
            )
        )
        self.select_all_btn.setText(_esc_amp(tr("refund.select_all_btn")))
        self.items_table.setHorizontalHeaderLabels(
            [
                tr("refund.col.item"),
                tr("refund.col.price"),
                tr("refund.col.available"),
                tr("refund.col.qty"),
                tr("refund.col.stock_action"),
            ]
        )

        for ctrl in self._row_controls:
            prev_act = ctrl.action_combo.currentData() or "NONE"
            ctrl.action_combo.clear()
            ctrl.action_combo.addItem(tr("refund.stock.none"), "NONE")
            ctrl.action_combo.addItem(tr("refund.stock.returned"), "RETURNED")
            ctrl.action_combo.addItem(tr("refund.stock.waste"), "WASTE")
            idx = ctrl.action_combo.findData(prev_act)
            ctrl.action_combo.setCurrentIndex(idx if idx >= 0 else 0)

        self.method_lbl.setText(tr("refund.method_label"))
        prev_m = self.method_combo.currentData()
        self.method_combo.clear()
        self.method_combo.addItem(tr_payment_method(METHOD_CASH), "CASH")
        self.method_combo.addItem(tr("refund.method.external"), "EXTERNAL")
        for mcode in ("CARD", "INSTAPAY", "EWALLET", "APPLE_PAY", "CUSTOM"):
            self.method_combo.addItem(tr_payment_method(mcode), mcode)
        if prev_m:
            m_idx = self.method_combo.findData(prev_m)
            if m_idx >= 0:
                self.method_combo.setCurrentIndex(m_idx)
        elif self.order.primary_payment and self.order.primary_payment.payment_method != METHOD_CASH:
            m_idx = self.method_combo.findData(self.order.primary_payment.payment_method)
            self.method_combo.setCurrentIndex(m_idx if m_idx >= 0 else 1)

        self.reason_lbl.setText(tr("refund.reason_label"))
        self.reason_edit.setPlaceholderText(tr("refund.reason_ph"))
        self.notes_lbl.setText(tr("refund.notes_label"))
        self.notes_edit.setPlaceholderText(tr("refund.notes_ph"))
        self.preview_title_lbl.setText(tr("refund.preview_label"))
        self.cancel_btn.setText(_esc_amp(tr("common.cancel")))
        self.confirm_btn.setText(_esc_amp(tr("refund.confirm_btn")))
        self.confirm_btn.setIcon(get_svg_icon("rotate_ccw", "#B23A3A", 14))

    def _select_all_remaining(self) -> None:
        for ctrl in self._row_controls:
            if ctrl.remaining_qty > 0:
                ctrl.qty_spin.setValue(ctrl.remaining_qty)

    def _estimated_refund_minor(self) -> int:
        order_sub = max(1, int(self.order.subtotal_minor or 0))
        order_tot = int(self.order.total_minor or 0)
        rem_cap = self.order.net_paid_minor
        all_rem = True
        raw_sum = 0
        for ctrl in self._row_controls:
            q = ctrl.qty_spin.value()
            if q < ctrl.remaining_qty:
                all_rem = False
            if q > 0:
                sold_q = max(1, int(ctrl.item.quantity))
                line_net = (int(ctrl.item.line_total_minor) * order_tot) / order_sub
                raw_sum += int(round((line_net * q) / sold_q))
        if raw_sum <= 0 and not any(c.qty_spin.value() > 0 for c in self._row_controls):
            return 0
        return rem_cap if all_rem else min(rem_cap, max(0, raw_sum))

    def _update_preview(self) -> None:
        any_sel = any(c.qty_spin.value() > 0 for c in self._row_controls)
        est = self._estimated_refund_minor()
        self.refund_preview_lbl.setText(format_minor(est, self.order.currency))
        self.confirm_btn.setEnabled(any_sel and self.order.net_paid_minor > 0)

    def _on_submit(self) -> None:
        items_payload: list[dict[str, object]] = []
        for ctrl in self._row_controls:
            q = ctrl.qty_spin.value()
            if q > 0:
                items_payload.append(
                    {
                        "order_item_id": ctrl.item.id,
                        "quantity": q,
                        "stock_action": str(ctrl.action_combo.currentData() or "NONE"),
                    }
                )
        if not items_payload:
            QMessageBox.warning(self, tr("common.validation_error"), tr("refund.err.select_items"))
            return

        method = str(self.method_combo.currentData() or "CASH")
        try:
            rid = self.db.create_order_refund(
                self.order.id,
                items_payload,
                refund_method=method,
                reason=self.reason_edit.text(),
                notes=self.notes_edit.text(),
            )
        except Exception as exc:
            QMessageBox.critical(self, tr("common.error"), str(exc))
            return

        self.refund_id = rid
        self.accept()

