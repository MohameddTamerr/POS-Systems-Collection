from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
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
from ..icons import get_svg_icon, get_svg_pixmap
from ..i18n import layout_direction, tr, tr_payment_method
from ..money import format_minor
from ..paths import get_receipts_dir
from ..payments import METHOD_CASH
from ..receipts import build_receipt_document, export_receipt_pdf, print_receipt_native
from ..theme import contrast_text_hex
from .animations import animate_window_fade_in, attach_button_feedback


def _esc_amp(text: str) -> str:
    return (text or "").replace("&", "&&")


class ReceiptDialog(QDialog):
    """Boutique Café native receipt preview and Windows printing/PDF dialog."""

    def __init__(
        self,
        order: OrderRecord,
        config: BusinessConfig,
        parent: QWidget | None = None,
        *,
        auto_saved_path: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.order = order
        self.config = config
        self._auto_saved_path = auto_saved_path
        self._did_animate_in = False

        self.setModal(True)
        self.resize(440, 680)
        self.setMinimumSize(370, 440)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        hdr_card = QFrame()
        hdr_card.setProperty("class", "BoutiqueCard")
        hdr_row = QHBoxLayout(hdr_card)
        hdr_row.setContentsMargins(12, 10, 12, 10)
        hdr_row.setSpacing(10)

        badge = QLabel()
        badge.setFixedSize(36, 36)
        badge.setAlignment(Qt.AlignCenter)
        badge.setStyleSheet(
            "background-color: #DCE8D7; border: 1px solid #C7D1C2; border-radius: 9px;"
        )
        badge.setPixmap(get_svg_pixmap("sales", "#005B37", 18))

        hdr_text = QVBoxLayout()
        hdr_text.setSpacing(2)
        self.header_lbl = QLabel("")
        self.header_lbl.setProperty("class", "SectionHeader")
        self.meta_lbl = QLabel("")
        self.meta_lbl.setProperty("class", "MutedLabel")
        self.meta_lbl.setWordWrap(True)
        self.auto_saved_badge = QLabel("")
        self.auto_saved_badge.setProperty("class", "PrimaryBadge")
        self.auto_saved_badge.hide()
        self.paper_badge = QLabel("")
        self.paper_badge.setProperty("class", "CountBadge")
        hdr_text.addWidget(self.header_lbl)
        hdr_text.addWidget(self.meta_lbl)
        hdr_text.addWidget(self.auto_saved_badge)

        hdr_row.addWidget(badge)
        hdr_row.addLayout(hdr_text, 1)
        hdr_row.addWidget(self.paper_badge, 0, Qt.AlignTop)
        layout.addWidget(hdr_card)

        self.preview_edit = QTextEdit()
        self.preview_edit.setObjectName("ReceiptPreviewEdit")
        self.preview_edit.setReadOnly(True)
        layout.addWidget(self.preview_edit, 1, Qt.AlignHCenter)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.pdf_btn = QPushButton("")
        self.pdf_btn.setIcon(get_svg_icon("pdf", "#202820", 14))
        self.pdf_btn.setIconSize(QSize(14, 14))
        self.pdf_btn.clicked.connect(self._save_pdf)

        on_primary = contrast_text_hex(config.accent_color)
        self.print_btn = QPushButton("")
        self.print_btn.setProperty("role", "primary")
        self.print_btn.setIcon(get_svg_icon("print", on_primary, 14))
        self.print_btn.setIconSize(QSize(14, 14))
        self.print_btn.clicked.connect(self._print_native)

        self.close_btn = QPushButton("")
        self.close_btn.setDefault(True)
        self.close_btn.clicked.connect(self.accept)

        btn_row.addWidget(self.pdf_btn)
        btn_row.addWidget(self.print_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(self.close_btn)
        attach_button_feedback(self.pdf_btn)
        attach_button_feedback(self.print_btn)
        attach_button_feedback(self.close_btn)
        layout.addLayout(btn_row)

        self.retranslate_ui()

    def retranslate_ui(self) -> None:
        self.setLayoutDirection(layout_direction())
        rcp = self.order.receipt_number or self.order.order_number
        self.setWindowTitle(tr("receipt_dialog.window_title", num=rcp))
        self.header_lbl.setText(tr("receipt_dialog.header_completed", num=rcp))
        tot_str = format_minor(self.order.total_minor, self.order.currency)
        pm = self.order.primary_payment
        if pm is not None:
            m_txt = tr_payment_method(
                pm.payment_method,
                custom_label=pm.custom_method_label,
                provider=pm.payment_provider,
            )
            meta_txt = tr("receipt_dialog.payment_meta", method=m_txt, total=tot_str)
            if pm.payment_method == METHOD_CASH and self.order.change_minor > 0:
                chg_str = format_minor(self.order.change_minor, self.order.currency)
                meta_txt = f"{meta_txt} · {tr('receipt.change_due')} {chg_str}"
            self.meta_lbl.setText(meta_txt)
        else:
            self.meta_lbl.setText(
                tr(
                    "receipt_dialog.meta",
                    total=tot_str,
                    change=format_minor(self.order.change_minor, self.order.currency),
                )
            )
        if self._auto_saved_path is not None:
            self.auto_saved_badge.setText(
                tr("receipt_dialog.auto_saved_badge", name=self._auto_saved_path.name)
            )
            self.auto_saved_badge.setToolTip(str(self._auto_saved_path))
            self.auto_saved_badge.show()
        else:
            self.auto_saved_badge.hide()

        width_mm = 58 if int(getattr(self.config, "printer_paper_width_mm", 80) or 80) == 58 else 80
        self.paper_badge.setText(f"{width_mm}mm")
        self.preview_edit.setFixedWidth(310 if width_mm == 58 else 404)
        self.preview_edit.setDocument(
            build_receipt_document(self.order, self.config, paper_width_mm=width_mm)
        )
        self.pdf_btn.setText(_esc_amp(tr("receipt_dialog.save_pdf_dots_btn")))
        self.print_btn.setText(_esc_amp(tr("receipt_dialog.print_dots_btn")))
        self.close_btn.setText(_esc_amp(tr("receipt_dialog.done_btn")))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._did_animate_in:
            self._did_animate_in = True
            animate_window_fade_in(self)

    def _print_native(self) -> None:
        try:
            print_receipt_native(self, self.order, self.config)
        except Exception as exc:
            QMessageBox.critical(
                self,
                tr("sales.error.print_title"),
                tr("sales.error.print_msg", exc=exc),
            )

    def _save_pdf(self) -> None:
        rcp = self.order.receipt_number or self.order.order_number
        default_path = str(
            get_receipts_dir(self.config.receipt_export_dir) / f"Receipt-{rcp}.pdf"
        )
        path_str, _ = QFileDialog.getSaveFileName(
            self,
            tr("sales.save_pdf_dialog_title"),
            default_path,
            tr("sales.pdf_dialog_filter"),
        )
        if not path_str:
            return
        try:
            export_receipt_pdf(Path(path_str), self.order, self.config)
        except Exception as exc:
            QMessageBox.critical(
                self,
                tr("sales.error.pdf_title"),
                tr("sales.error.pdf_msg", exc=exc),
            )
            return
        QMessageBox.information(
            self,
            tr("receipt_dialog.pdf_saved_title"),
            tr("receipt_dialog.pdf_saved_body", path=path_str),
        )
