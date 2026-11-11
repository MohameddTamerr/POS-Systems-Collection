from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..config import BusinessConfig
from ..db import Database, OrderRecord, OrderSummary
from ..money import format_minor
from ..receipts import (
    build_receipt_document,
    export_receipt_pdf,
    format_local_timestamp,
    print_receipt_native,
)


class SalesView(QWidget):
    """Sales History view with order list and native receipt details inspector."""

    status_message = Signal(str)

    def __init__(self, db: Database, config: BusinessConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.db = db
        self.config = config
        self._orders: list[OrderSummary] = []
        self._selected_order: OrderRecord | None = None

        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(10)

        # Left: Orders table
        left_panel = QFrame()
        left_panel.setProperty("class", "TerminalPanel")
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(14, 14, 14, 14)
        left_layout.setSpacing(10)

        hdr_row = QHBoxLayout()
        title = QLabel("Sales History")
        title.setProperty("class", "SectionHeader")

        self.summary_lbl = QLabel("0 orders")
        self.summary_lbl.setProperty("class", "MutedLabel")

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search order # or item...")
        self.search_input.setMaximumWidth(240)
        self.search_input.textChanged.connect(self.refresh)

        hdr_row.addWidget(title)
        hdr_row.addWidget(self.summary_lbl)
        hdr_row.addStretch(1)
        hdr_row.addWidget(self.search_input)
        left_layout.addLayout(hdr_row)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Order #", "Date & Time", "Items", "Total", "Cash", "Change"]
        )
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

        left_layout.addWidget(self.table, 1)

        # Right: Order details / Receipt preview
        right_panel = QFrame()
        right_panel.setProperty("class", "TerminalPanel")
        right_panel.setMinimumWidth(340)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(14, 14, 14, 14)
        right_layout.setSpacing(10)

        detail_hdr = QHBoxLayout()
        self.detail_title_lbl = QLabel("Order Details")
        self.detail_title_lbl.setProperty("class", "SectionHeader")

        self.pdf_btn = QPushButton("Save PDF...")
        self.pdf_btn.setEnabled(False)
        self.pdf_btn.clicked.connect(self._save_pdf)

        self.print_btn = QPushButton("Print Receipt...")
        self.print_btn.setProperty("role", "primary")
        self.print_btn.setEnabled(False)
        self.print_btn.clicked.connect(self._print_selected)

        detail_hdr.addWidget(self.detail_title_lbl)
        detail_hdr.addStretch(1)
        detail_hdr.addWidget(self.pdf_btn)
        detail_hdr.addWidget(self.print_btn)
        right_layout.addLayout(detail_hdr)

        self.receipt_preview = QTextEdit()
        self.receipt_preview.setObjectName("ReceiptPreviewEdit")
        self.receipt_preview.setReadOnly(True)
        self.receipt_preview.setPlainText("Select a completed sale on the left to inspect its details and receipt.")
        right_layout.addWidget(self.receipt_preview, 1)

        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([640, 380])

        outer.addWidget(splitter, 1)

    def set_config(self, config: BusinessConfig) -> None:
        self.config = config
        self.refresh()

    def focus_search(self) -> None:
        self.search_input.setFocus()
        self.search_input.selectAll()

    def refresh(self) -> None:
        self._orders = self.db.list_orders(self.search_input.text())
        self.table.setRowCount(len(self._orders))

        total_sales_minor = 0
        for row_idx, summary in enumerate(self._orders):
            total_sales_minor += summary.total_minor
            num_item = QTableWidgetItem(summary.order_number)
            date_item = QTableWidgetItem(format_local_timestamp(summary.created_at))
            cnt_item = QTableWidgetItem(str(summary.item_count))
            cnt_item.setTextAlignment(Qt.AlignCenter)

            tot_item = QTableWidgetItem(format_minor(summary.total_minor, summary.currency))
            tot_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            cash_item = QTableWidgetItem(format_minor(summary.cash_received_minor, summary.currency))
            cash_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            chg_item = QTableWidgetItem(format_minor(summary.change_minor, summary.currency))
            chg_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)

            self.table.setItem(row_idx, 0, num_item)
            self.table.setItem(row_idx, 1, date_item)
            self.table.setItem(row_idx, 2, cnt_item)
            self.table.setItem(row_idx, 3, tot_item)
            self.table.setItem(row_idx, 4, cash_item)
            self.table.setItem(row_idx, 5, chg_item)

        count = len(self._orders)
        self.summary_lbl.setText(
            f"{count} {'order' if count == 1 else 'orders'} · "
            f"Total: {format_minor(total_sales_minor, self.config.currency)}"
        )

        if self._orders:
            self.table.selectRow(0)
        else:
            self._selected_order = None
            self.detail_title_lbl.setText("Order Details")
            self.receipt_preview.setPlainText("No completed sales recorded yet.")
            self.print_btn.setEnabled(False)
            self.pdf_btn.setEnabled(False)

    def _on_selection_changed(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            self._selected_order = None
            self.print_btn.setEnabled(False)
            self.pdf_btn.setEnabled(False)
            return

        row = rows[0].row()
        if not (0 <= row < len(self._orders)):
            return

        order = self.db.get_order(self._orders[row].id)
        self._selected_order = order
        if order is None:
            self.print_btn.setEnabled(False)
            self.pdf_btn.setEnabled(False)
            return

        self.detail_title_lbl.setText(f"Receipt — {order.order_number}")
        self.receipt_preview.setDocument(build_receipt_document(order, self.config))
        self.print_btn.setEnabled(True)
        self.pdf_btn.setEnabled(True)

    def _print_selected(self) -> None:
        if not self._selected_order:
            return
        try:
            print_receipt_native(self, self._selected_order, self.config)
        except Exception as exc:
            QMessageBox.critical(self, "Print Error", f"Unable to print receipt:\n{exc}")

    def _save_pdf(self) -> None:
        if not self._selected_order:
            return
        default_name = f"Receipt-{self._selected_order.order_number}.pdf"
        path_str, _ = QFileDialog.getSaveFileName(
            self,
            "Save Receipt as PDF",
            default_name,
            "PDF Files (*.pdf)",
        )
        if not path_str:
            return
        try:
            export_receipt_pdf(Path(path_str), self._selected_order, self.config)
        except Exception as exc:
            QMessageBox.critical(self, "PDF Export Error", f"Unable to export PDF:\n{exc}")
            return
        self.status_message.emit(f"Saved PDF receipt to {path_str}")

