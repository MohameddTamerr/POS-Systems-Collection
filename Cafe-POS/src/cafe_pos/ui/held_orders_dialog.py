from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..auth import AuthorizationError
from ..config import BusinessConfig
from ..db import Database, HeldOrderRecord
from ..i18n import (
    format_order_type_summary,
    format_timestamp_display,
    layout_direction,
    tr,
    tr_count,
)
from ..icons import get_svg_icon
from ..money import format_minor
from ..theme import contrast_text_hex
from .animations import attach_button_feedback
from .widgets import EmptyStateWidget, SearchLineEdit


class HeldOrdersDialog(QDialog):
    """Boutique dialog to inspect, resume, or cancel held unpaid orders."""

    held_orders_changed = Signal()

    def __init__(
        self,
        db: Database,
        config: BusinessConfig,
        *,
        has_active_cart: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.db = db
        self.config = config
        self._has_active_cart = bool(has_active_cart)
        self.resumed_order: HeldOrderRecord | None = None
        self._held_orders: list[HeldOrderRecord] = []

        self.setModal(True)
        self.setLayoutDirection(layout_direction())
        self.setWindowTitle(tr("held_orders.title"))
        self.resize(680, 560)
        self.setMinimumSize(540, 400)

        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        hdr_row = QHBoxLayout()
        hdr_text = QVBoxLayout()
        hdr_text.setSpacing(2)
        title = QLabel(tr("held_orders.title"))
        title.setProperty("class", "PageHeading")
        sub = QLabel(tr("held_orders.subtitle"))
        sub.setProperty("class", "MutedLabel")
        sub.setWordWrap(True)
        hdr_text.addWidget(title)
        hdr_text.addWidget(sub)
        hdr_row.addLayout(hdr_text, 1)

        self.count_badge = QLabel(tr_count("held", 0))
        self.count_badge.setProperty("class", "PrimaryBadge")
        hdr_row.addWidget(self.count_badge)
        root.addLayout(hdr_row)

        self.search_input = SearchLineEdit(tr("register.search_ph"))
        self.search_input.textChanged.connect(self.refresh)
        root.addWidget(self.search_input)

        self.stack = QStackedWidget()

        self.empty_state = EmptyStateWidget(
            "orders",
            tr("held_orders.empty_title"),
            tr("held_orders.empty_desc"),
            accent_color=self.config.accent_color,
        )

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setObjectName("CheckoutScrollHost")
        self.list_host = QWidget()
        self.list_host.setObjectName("CheckoutScrollHost")
        self.list_layout = QVBoxLayout(self.list_host)
        self.list_layout.setContentsMargins(2, 2, 4, 2)
        self.list_layout.setSpacing(9)
        self.list_layout.addStretch(1)
        self.scroll.setWidget(self.list_host)

        self.stack.addWidget(self.empty_state)
        self.stack.addWidget(self.scroll)
        root.addWidget(self.stack, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        close_btn = QPushButton(tr("common.close"))
        close_btn.clicked.connect(self.reject)
        attach_button_feedback(close_btn)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    def refresh(self) -> None:
        try:
            self._held_orders = self.db.list_held_orders(search=self.search_input.text())
        except AuthorizationError:
            self._held_orders = []

        self.count_badge.setText(tr_count("held", len(self._held_orders)))

        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()

        if not self._held_orders:
            self.stack.setCurrentIndex(0)
            return

        self.stack.setCurrentIndex(1)
        on_primary = contrast_text_hex(self.config.accent_color)
        for held in self._held_orders:
            card = QFrame()
            card.setProperty("class", "BoutiqueCard")
            c_lay = QVBoxLayout(card)
            c_lay.setContentsMargins(14, 12, 14, 12)
            c_lay.setSpacing(7)

            top = QHBoxLayout()
            num_badge = QLabel(held.hold_number)
            num_badge.setProperty("class", "PrimaryBadge")
            type_badge = QLabel(
                format_order_type_summary(
                    held.order_type,
                    table_number=held.table_number,
                    customer_name=held.customer_name,
                )
            )
            type_badge.setProperty("class", "CategoryPill")
            total_lbl = QLabel(format_minor(held.subtotal_minor, held.currency))
            total_lbl.setStyleSheet("font-size: 15px; font-weight: 700; color: #005B37;")
            top.addWidget(num_badge)
            top.addWidget(type_badge)
            top.addStretch(1)
            top.addWidget(total_lbl)
            c_lay.addLayout(top)

            meta_parts = [
                tr_count("item", held.item_count),
                format_timestamp_display(held.updated_at or held.created_at),
            ]
            if held.cashier_name_snapshot:
                meta_parts.append(tr("held_orders.meta_cashier", name=held.cashier_name_snapshot))
            meta_lbl = QLabel(" · ".join(meta_parts))
            meta_lbl.setProperty("class", "MutedLabel")
            c_lay.addWidget(meta_lbl)

            lines_txt: list[str] = []
            for it in held.items:
                line_s = f"{it.quantity}× {it.display_name()}"
                if it.modifiers:
                    line_s += " (" + ", ".join(m.display_option_name() for m in it.modifiers) + ")"
                lines_txt.append(line_s)
            items_lbl = QLabel(" · ".join(lines_txt))
            items_lbl.setWordWrap(True)
            items_lbl.setStyleSheet("font-size: 12px; color: #202820;")
            c_lay.addWidget(items_lbl)

            actions = QHBoxLayout()
            actions.setSpacing(8)
            actions.addStretch(1)
            cancel_btn = QPushButton(tr("held_orders.cancel_btn"))
            cancel_btn.setProperty("role", "danger")
            cancel_btn.setIcon(get_svg_icon("trash", "#B23A3A", 13))
            cancel_btn.setIconSize(QSize(13, 13))
            cancel_btn.clicked.connect(lambda _c=False, h=held: self._confirm_cancel_held(h))
            attach_button_feedback(cancel_btn)

            resume_btn = QPushButton(tr("held_orders.resume_btn"))
            resume_btn.setProperty("role", "primary")
            resume_btn.setIcon(get_svg_icon("arrow_right", on_primary, 13))
            resume_btn.setIconSize(QSize(13, 13))
            resume_btn.clicked.connect(lambda _c=False, h=held: self._resume_held(h))
            attach_button_feedback(resume_btn)

            actions.addWidget(cancel_btn)
            actions.addWidget(resume_btn)
            c_lay.addLayout(actions)
            self.list_layout.addWidget(card)

        self.list_layout.addStretch(1)

    def _resume_held(self, held: HeldOrderRecord) -> None:
        if self._has_active_cart:
            ans = QMessageBox.question(
                self,
                tr("held_orders.confirm_replace_title"),
                tr("held_orders.confirm_replace_body", num=held.hold_number),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if ans != QMessageBox.Yes:
                return
        self.resumed_order = held
        self.accept()

    def _confirm_cancel_held(self, held: HeldOrderRecord) -> None:
        ans = QMessageBox.question(
            self,
            tr("held_orders.confirm_cancel_title"),
            tr(
                "held_orders.confirm_cancel_body",
                num=held.hold_number,
                total=format_minor(held.subtotal_minor, held.currency),
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ans != QMessageBox.Yes:
            return
        try:
            self.db.cancel_held_order(held.id)
        except (ValueError, AuthorizationError) as exc:
            QMessageBox.warning(self, tr("common.validation_error"), str(exc))
            return
        self.held_orders_changed.emit()
        self.refresh()

