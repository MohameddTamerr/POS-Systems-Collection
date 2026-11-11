from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..config import BusinessConfig
from ..db import OrderRecord
from ..receipts import build_receipt_document, export_receipt_pdf, print_receipt_native


class ReceiptDialog(QDialog):
    """Native receipt preview and Windows printing dialog."""

    def __init__(
        self,
        order: OrderRecord,
        config: BusinessConfig,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.order = order
        self.config = config

        self.setWindowTitle(f"Receipt — {order.order_number}")
        self.setModal(True)
        self.resize(410, 560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        header = QLabel(f"Sale Completed — {order.order_number}")
        header.setProperty("class", "SectionHeader")
        layout.addWidget(header)

        self.preview_edit = QTextEdit()
        self.preview_edit.setObjectName("ReceiptPreviewEdit")
        self.preview_edit.setReadOnly(True)
        self.preview_edit.setDocument(build_receipt_document(order, config))
        layout.addWidget(self.preview_edit, 1)

        btn_row = QHBoxLayout()
        pdf_btn = QPushButton("Save as PDF...")
        pdf_btn.clicked.connect(self._save_pdf)

        print_btn = QPushButton("Print Receipt...")
        print_btn.setProperty("role", "primary")
        print_btn.clicked.connect(self._print_native)

        close_btn = QPushButton("Close")
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.accept)

        btn_row.addWidget(pdf_btn)
        btn_row.addWidget(print_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _print_native(self) -> None:
        try:
            print_receipt_native(self, self.order, self.config)
        except Exception as exc:
            QMessageBox.critical(self, "Print Error", f"Unable to print receipt:\n{exc}")

    def _save_pdf(self) -> None:
        default_name = f"Receipt-{self.order.order_number}.pdf"
        path_str, _ = QFileDialog.getSaveFileName(
            self,
            "Save Receipt as PDF",
            default_name,
            "PDF Files (*.pdf)",
        )
        if not path_str:
            return
        try:
            export_receipt_pdf(Path(path_str), self.order, self.config)
        except Exception as exc:
            QMessageBox.critical(self, "PDF Export Error", f"Unable to export PDF:\n{exc}")
            return
        QMessageBox.information(self, "PDF Saved", f"Receipt exported to:\n{path_str}")

