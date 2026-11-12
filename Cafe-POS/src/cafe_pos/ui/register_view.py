from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid as _qt_is_valid

from ..config import BusinessConfig
from ..db import (
    DISCOUNT_TYPE_FIXED,
    DISCOUNT_TYPE_NONE,
    DISCOUNT_TYPE_PERCENT,
    ORDER_TYPE_DELIVERY,
    ORDER_TYPE_DINE_IN,
    ORDER_TYPE_TAKEAWAY,
    CartLine,
    CategoryRecord,
    Database,
    HeldOrderRecord,
    OrderRecord,
    Product,
)
from ..icons import get_svg_icon
from ..i18n import is_rtl, tr, tr_category_record, tr_count
from ..money import format_minor, parse_major_to_minor
from ..theme import contrast_text_hex
from .animations import (
    animate_fade_in,
    animate_flash_highlight,
    attach_button_feedback,
    clear_widget_animations,
)
from .held_orders_dialog import HeldOrdersDialog
from .payment_dialog import CheckoutPaymentDialog
from .product_customize_dialog import ProductCustomizeDialog
from .shift_dialog import ShiftManagerDialog
from .widgets import (
    BoutiqueCategoryCard,
    CartItemRowWidget,
    CozyProductCard,
    EmptyStateWidget,
    HorizontalWheelScrollArea,
    SearchLineEdit,
)


def _dispose_layout_widgets(layout: QLayout | None) -> None:
    """Safely stop animations, detach effects, hide, and schedule deletion for dynamic layout children."""
    if layout is None or not _qt_is_valid(layout):
        return
    while layout.count():
        item = layout.takeAt(0)
        if item is None:
            continue
        w = item.widget()
        if w is not None and _qt_is_valid(w):
            if hasattr(w, "stop_animations"):
                w.stop_animations()
            clear_widget_animations(w)
            w.hide()
            w.setParent(None)
            w.deleteLater()
        sub = item.layout()
        if sub is not None and _qt_is_valid(sub):
            _dispose_layout_widgets(sub)


class RegisterView(QWidget):
    """Boutique Café Cashier Register: editorial category cards + studio product grid + cart dock."""

    order_completed = Signal(object)  # emits OrderRecord
    status_message = Signal(str)
    low_stock_clicked = Signal()
    shift_changed = Signal()

    def __init__(self, db: Database, config: BusinessConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.db = db
        self.config = config
        self.selected_category = "All"
        self.cart: dict[int | str, CartLine] = {}
        self._last_products: list[Product] = []
        self._category_records: dict[str, CategoryRecord] = {}
        self._product_mod_counts: dict[int, int] = {}
        self._last_cols: int = 0
        self._product_cards: dict[int, CozyProductCard] = {}
        self._cart_row_widgets: dict[int | str, CartItemRowWidget] = {}
        self._checking_out: bool = False
        self._order_count: int = 0
        self._held_count: int = 0
        self._order_type: str = getattr(config, "default_order_type", ORDER_TYPE_DINE_IN) or ORDER_TYPE_DINE_IN
        self._active_held_order_id: int | None = None
        self._active_held_number: str = ""

        self._build_ui()
        self.refresh_catalog()
        self.refresh_held_count()
        self.refresh_operational_badges()
        self.refresh_cart(animate_cards=False)

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 10, 16, 12)
        outer.setSpacing(0)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(12)

        # ================= LEFT PANE: Clean Organic Canvas over #F7F7E9 =================
        left_pane = QWidget()
        left_layout = QVBoxLayout(left_pane)
        left_layout.setContentsMargins(0, 0, 2, 0)
        left_layout.setSpacing(10)

        # Full-Width Pill Search Bar + Live Match Count Badge
        search_row = QHBoxLayout()
        search_row.setSpacing(8)
        self.search_input = SearchLineEdit(tr("register.search_ph"))
        self.search_input.setMinimumHeight(38)
        self.search_input.textChanged.connect(self._on_search_changed)

        self.catalog_count_badge = QLabel(tr_count("item", 0))
        self.catalog_count_badge.setProperty("class", "CountBadge")

        self.orders_chip = QLabel(tr("header.orders_chip", count=0))
        self.orders_chip.setObjectName("RegisterOrdersChip")

        self.shift_btn = QPushButton(tr("register.shift.idle"))
        self.shift_btn.setProperty("role", "secondary")
        self.shift_btn.setIcon(get_svg_icon("clock", "#202820", 13))
        self.shift_btn.setIconSize(QSize(13, 13))
        self.shift_btn.setStyleSheet("padding: 5px 11px; font-size: 11.5px; font-weight: 700;")
        self.shift_btn.setCursor(Qt.PointingHandCursor)
        self.shift_btn.clicked.connect(self.open_shift_dialog)
        attach_button_feedback(self.shift_btn)

        self.low_stock_badge_btn = QPushButton("")
        self.low_stock_badge_btn.setProperty("role", "danger")
        self.low_stock_badge_btn.setStyleSheet("padding: 5px 10px; font-size: 11.5px; font-weight: 700;")
        self.low_stock_badge_btn.setCursor(Qt.PointingHandCursor)
        self.low_stock_badge_btn.clicked.connect(self.low_stock_clicked.emit)
        self.low_stock_badge_btn.hide()
        attach_button_feedback(self.low_stock_badge_btn)

        search_row.addWidget(self.search_input, 1)
        search_row.addWidget(self.low_stock_badge_btn)
        search_row.addWidget(self.shift_btn)
        search_row.addWidget(self.catalog_count_badge)
        search_row.addWidget(self.orders_chip)
        left_layout.addLayout(search_row)

        # Horizontally Scrollable Illustrated Boutique Category Cards
        self.cat_scroll = HorizontalWheelScrollArea()
        self.cat_scroll.setObjectName("CategoryCardsScroll")
        self.cat_scroll.setWidgetResizable(True)
        self.cat_scroll.setFixedHeight(96)
        self.cat_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.cat_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.category_container = QWidget()
        self.category_container.setObjectName("CategoryStripHost")
        self.category_layout = QHBoxLayout(self.category_container)
        self.category_layout.setContentsMargins(0, 2, 4, 3)
        self.category_layout.setSpacing(9)
        self.cat_scroll.setWidget(self.category_container)
        left_layout.addWidget(self.cat_scroll)

        # Scrollable Studio Product Grid vs Empty Search State
        self.catalog_stack = QStackedWidget()

        self.product_scroll = QScrollArea()
        self.product_scroll.setObjectName("RegisterGridScroll")
        self.product_scroll.setWidgetResizable(True)
        self.product_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.product_grid_host = QWidget()
        self.product_grid_host.setObjectName("ProductGridHost")
        self.product_grid = QGridLayout(self.product_grid_host)
        self.product_grid.setContentsMargins(0, 2, 6, 8)
        self.product_grid.setSpacing(11)
        self.product_grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.product_scroll.setWidget(self.product_grid_host)

        self.empty_catalog_state = EmptyStateWidget(
            "search",
            tr("register.empty_menu.title"),
            tr("register.empty_menu.desc"),
            action_label=tr("register.empty_menu.btn"),
            accent_color=self.config.accent_color,
        )
        self.empty_catalog_state.action_triggered.connect(self._reset_catalog_filter)

        self.catalog_stack.addWidget(self.product_scroll)
        self.catalog_stack.addWidget(self.empty_catalog_state)
        left_layout.addWidget(self.catalog_stack, 1)

        # ================= RIGHT PANE: Boutique Order & Cash Checkout Card =================
        right_panel = QFrame()
        right_panel.setProperty("class", "BoutiqueCard")
        right_panel.setMinimumWidth(330)
        right_panel.setMaximumWidth(440)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        cart_header = QWidget()
        cart_header_outer = QVBoxLayout(cart_header)
        cart_header_outer.setContentsMargins(14, 12, 14, 9)
        cart_header_outer.setSpacing(6)

        cart_header_layout = QHBoxLayout()
        cart_header_layout.setContentsMargins(0, 0, 0, 0)
        cart_header_layout.setSpacing(6)

        self.cart_title_lbl = QLabel(tr("register.order_title"))
        self.cart_title_lbl.setProperty("class", "SectionHeader")

        self.item_count_lbl = QLabel(tr_count("item", 0))
        self.item_count_lbl.setProperty("class", "PrimaryBadge")

        self.held_orders_btn = QPushButton(tr("register.held_btn", count=0))
        self.held_orders_btn.setProperty("role", "secondary")
        self.held_orders_btn.setStyleSheet("padding: 4px 10px; font-size: 11.5px;")
        self.held_orders_btn.setIcon(get_svg_icon("clock", "#6B362A", 12))
        self.held_orders_btn.setIconSize(QSize(12, 12))
        self.held_orders_btn.setToolTip(tr("register.held_btn_tip"))
        self.held_orders_btn.setCursor(Qt.PointingHandCursor)
        self.held_orders_btn.clicked.connect(self._open_held_orders_dialog)
        attach_button_feedback(self.held_orders_btn)

        self.clear_cart_btn = QPushButton(tr("register.clear_btn"))
        self.clear_cart_btn.setProperty("role", "danger")
        self.clear_cart_btn.setStyleSheet("padding: 4px 10px; font-size: 12px;")
        self.clear_cart_btn.setIcon(get_svg_icon("trash", "#B23A3A", 12))
        self.clear_cart_btn.setIconSize(QSize(12, 12))
        self.clear_cart_btn.clicked.connect(self._confirm_clear_cart)
        attach_button_feedback(self.clear_cart_btn)

        cart_header_layout.addWidget(self.cart_title_lbl)
        cart_header_layout.addWidget(self.item_count_lbl)
        cart_header_layout.addStretch(1)
        cart_header_layout.addWidget(self.held_orders_btn)
        cart_header_layout.addWidget(self.clear_cart_btn)
        cart_header_outer.addLayout(cart_header_layout)

        self.held_editing_badge = QLabel("")
        self.held_editing_badge.setProperty("class", "PeachBadge")
        self.held_editing_badge.hide()
        cart_header_outer.addWidget(self.held_editing_badge)
        right_layout.addWidget(cart_header)

        # Compact Order Type Bar (Dine-in / Takeaway / Delivery + Optional Fields)
        self.order_type_bar = QFrame()
        self.order_type_bar.setObjectName("OrderTypeBar")
        ot_lay = QVBoxLayout(self.order_type_bar)
        ot_lay.setContentsMargins(12, 8, 12, 8)
        ot_lay.setSpacing(6)

        ot_pills_row = QHBoxLayout()
        ot_pills_row.setSpacing(6)

        self.dine_in_btn = QPushButton(tr("order_type.dine_in"))
        self.dine_in_btn.setProperty("class", "OrderTypePill")
        self.dine_in_btn.setCursor(Qt.PointingHandCursor)
        self.dine_in_btn.setIconSize(QSize(13, 13))
        self.dine_in_btn.clicked.connect(lambda: self.set_order_type(ORDER_TYPE_DINE_IN))
        attach_button_feedback(self.dine_in_btn)

        self.takeaway_btn = QPushButton(tr("order_type.takeaway"))
        self.takeaway_btn.setProperty("class", "OrderTypePill")
        self.takeaway_btn.setCursor(Qt.PointingHandCursor)
        self.takeaway_btn.setIconSize(QSize(13, 13))
        self.takeaway_btn.clicked.connect(lambda: self.set_order_type(ORDER_TYPE_TAKEAWAY))
        attach_button_feedback(self.takeaway_btn)

        self.delivery_btn = QPushButton(tr("order_type.delivery"))
        self.delivery_btn.setProperty("class", "OrderTypePill")
        self.delivery_btn.setCursor(Qt.PointingHandCursor)
        self.delivery_btn.setIconSize(QSize(13, 13))
        self.delivery_btn.clicked.connect(lambda: self.set_order_type(ORDER_TYPE_DELIVERY))
        attach_button_feedback(self.delivery_btn)

        ot_pills_row.addWidget(self.dine_in_btn, 1)
        ot_pills_row.addWidget(self.takeaway_btn, 1)
        ot_pills_row.addWidget(self.delivery_btn, 1)
        ot_lay.addLayout(ot_pills_row)

        self.dine_in_fields = QWidget()
        di_lay = QHBoxLayout(self.dine_in_fields)
        di_lay.setContentsMargins(0, 0, 0, 0)
        di_lay.setSpacing(6)
        self.table_edit = QLineEdit()
        self.table_edit.setMaxLength(24)
        self.table_edit.setPlaceholderText(tr("order_type.table_ph"))
        self.table_edit.setStyleSheet("font-size: 12px; padding: 4px 9px;")
        di_lay.addWidget(self.table_edit)
        ot_lay.addWidget(self.dine_in_fields)

        self.delivery_fields = QWidget()
        dl_lay = QVBoxLayout(self.delivery_fields)
        dl_lay.setContentsMargins(0, 0, 0, 0)
        dl_lay.setSpacing(5)
        dl_top = QHBoxLayout()
        dl_top.setSpacing(6)
        self.cust_name_edit = QLineEdit()
        self.cust_name_edit.setMaxLength(80)
        self.cust_name_edit.setPlaceholderText(tr("order_type.customer_name_ph"))
        self.cust_name_edit.setStyleSheet("font-size: 12px; padding: 4px 9px;")
        self.cust_phone_edit = QLineEdit()
        self.cust_phone_edit.setMaxLength(32)
        self.cust_phone_edit.setPlaceholderText(tr("order_type.customer_phone_ph"))
        self.cust_phone_edit.setStyleSheet("font-size: 12px; padding: 4px 9px;")
        dl_top.addWidget(self.cust_name_edit, 1)
        dl_top.addWidget(self.cust_phone_edit, 1)
        self.deliv_addr_edit = QLineEdit()
        self.deliv_addr_edit.setMaxLength(200)
        self.deliv_addr_edit.setPlaceholderText(tr("order_type.delivery_addr_ph"))
        self.deliv_addr_edit.setStyleSheet("font-size: 12px; padding: 4px 9px;")
        dl_lay.addLayout(dl_top)
        dl_lay.addWidget(self.deliv_addr_edit)
        ot_lay.addWidget(self.delivery_fields)

        right_layout.addWidget(self.order_type_bar)
        self._sync_order_type_ui()

        # Cart Stack: Friendly Empty Illustration vs Scrollable Cart Rows
        self.cart_stack = QStackedWidget()

        self.empty_cart_state = EmptyStateWidget(
            "cart",
            tr("register.empty_cart.title"),
            tr("register.empty_cart.desc"),
            accent_color=self.config.accent_color,
        )

        self.cart_scroll = QScrollArea()
        self.cart_scroll.setWidgetResizable(True)
        self.cart_scroll.setMinimumHeight(74)
        self.cart_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.cart_list_host = QWidget()
        self.cart_list_host.setObjectName("CartListHost")
        self.cart_list_layout = QVBoxLayout(self.cart_list_host)
        self.cart_list_layout.setContentsMargins(12, 2, 12, 8)
        self.cart_list_layout.setSpacing(7)
        self.cart_list_layout.addStretch(1)
        self.cart_scroll.setWidget(self.cart_list_host)

        self.cart_stack.addWidget(self.empty_cart_state)
        self.cart_stack.addWidget(self.cart_scroll)
        right_layout.addWidget(self.cart_stack, 1)

        # Payment Details & Checkout Pill CTA Footer
        summary_box = QFrame()
        summary_box.setObjectName("OrderSummaryBox")
        summary_layout = QVBoxLayout(summary_box)
        summary_layout.setContentsMargins(16, 12, 16, 14)
        summary_layout.setSpacing(8)

        sub_row = QHBoxLayout()
        self.sub_title_lbl = QLabel(tr("register.subtotal_label"))
        self.sub_title_lbl.setProperty("class", "MutedLabel")
        self.subtotal_lbl = QLabel(format_minor(0, self.config.currency))
        self.subtotal_lbl.setAlignment(Qt.AlignTrailing | Qt.AlignVCenter)
        self.subtotal_lbl.setStyleSheet("font-weight: 600;")
        sub_row.addWidget(self.sub_title_lbl)
        sub_row.addStretch(1)
        sub_row.addWidget(self.subtotal_lbl)
        summary_layout.addLayout(sub_row)

        # Compact Order Discount Controls
        self.discount_controls_host = QWidget()
        dc_v = QVBoxLayout(self.discount_controls_host)
        dc_v.setContentsMargins(0, 0, 0, 0)
        dc_v.setSpacing(4)

        disc_row = QHBoxLayout()
        disc_row.setContentsMargins(0, 0, 0, 0)
        disc_row.setSpacing(6)
        self.discount_type_combo = QComboBox()
        self.discount_type_combo.addItem(tr("discount.none"), DISCOUNT_TYPE_NONE)
        self.discount_type_combo.addItem(tr("discount.percent"), DISCOUNT_TYPE_PERCENT)
        self.discount_type_combo.addItem(tr("discount.fixed"), DISCOUNT_TYPE_FIXED)
        self.discount_type_combo.setMinimumHeight(28)
        self.discount_type_combo.currentIndexChanged.connect(self._on_discount_changed)

        self.discount_val_edit = QLineEdit()
        self.discount_val_edit.setPlaceholderText(tr("discount.value_ph"))
        self.discount_val_edit.setMaximumWidth(86)
        self.discount_val_edit.setMinimumHeight(28)
        self.discount_val_edit.setStyleSheet("font-size: 12px; padding: 3px 7px;")
        self.discount_val_edit.textChanged.connect(self._on_discount_changed)
        self.discount_val_edit.hide()

        self.discount_label_edit = QLineEdit()
        self.discount_label_edit.setPlaceholderText(tr("discount.label_ph"))
        self.discount_label_edit.setMaxLength(60)
        self.discount_label_edit.setMinimumHeight(28)
        self.discount_label_edit.setStyleSheet("font-size: 12px; padding: 3px 7px;")
        self.discount_label_edit.hide()

        self.discount_amt_lbl = QLabel("")
        self.discount_amt_lbl.setStyleSheet("font-weight: 700; color: #B23A3A; font-size: 12.5px;")
        self.discount_amt_lbl.setAlignment(Qt.AlignTrailing | Qt.AlignVCenter)

        disc_row.addWidget(self.discount_type_combo)
        disc_row.addWidget(self.discount_val_edit)
        disc_row.addWidget(self.discount_label_edit, 1)
        disc_row.addStretch(1)
        disc_row.addWidget(self.discount_amt_lbl)
        dc_v.addLayout(disc_row)

        self.discount_hint_lbl = QLabel("")
        self.discount_hint_lbl.setStyleSheet("font-size: 11px; color: #B23A3A;")
        self.discount_hint_lbl.setWordWrap(True)
        self.discount_hint_lbl.hide()
        dc_v.addWidget(self.discount_hint_lbl)
        summary_layout.addWidget(self.discount_controls_host)

        tot_row = QHBoxLayout()
        self.tot_title_lbl = QLabel(tr("register.total_due_label"))
        self.tot_title_lbl.setStyleSheet("font-size: 15px; font-weight: 700; color: #202820;")
        self.total_lbl = QLabel(format_minor(0, self.config.currency))
        self.total_lbl.setObjectName("TotalValueLabel")
        self.total_lbl.setAlignment(Qt.AlignTrailing | Qt.AlignVCenter)
        tot_row.addWidget(self.tot_title_lbl)
        tot_row.addStretch(1)
        tot_row.addWidget(self.total_lbl)
        summary_layout.addLayout(tot_row)

        summary_layout.addSpacing(2)
        cta_row = QHBoxLayout()
        cta_row.setSpacing(8)

        self.hold_btn = QPushButton(tr("register.hold_btn"))
        self.hold_btn.setProperty("role", "secondary")
        self.hold_btn.setMinimumHeight(44)
        self.hold_btn.setMinimumWidth(86)
        self.hold_btn.setIcon(get_svg_icon("pause", "#6B362A", 14))
        self.hold_btn.setIconSize(QSize(14, 14))
        self.hold_btn.setToolTip(tr("register.hold_btn_tip"))
        self.hold_btn.setCursor(Qt.PointingHandCursor)
        self.hold_btn.clicked.connect(self._hold_current_order)
        attach_button_feedback(self.hold_btn)

        self.checkout_btn = QPushButton(tr("register.pay_btn_idle"))
        self.checkout_btn.setObjectName("CheckoutPillBtn")
        self.checkout_btn.setMinimumHeight(44)
        self.checkout_btn.setCursor(Qt.PointingHandCursor)
        self.checkout_btn.setIcon(
            get_svg_icon(
                "arrow_left" if is_rtl() else "arrow_right",
                contrast_text_hex(self.config.accent_color),
                16,
            )
        )
        self.checkout_btn.setIconSize(QSize(16, 16))
        self.checkout_btn.clicked.connect(self._attempt_checkout)
        attach_button_feedback(self.checkout_btn)

        cta_row.addWidget(self.hold_btn)
        cta_row.addWidget(self.checkout_btn, 1)
        summary_layout.addLayout(cta_row)

        right_layout.addWidget(summary_box)

        splitter.addWidget(left_pane)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 7)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([830, 380])

        outer.addWidget(splitter, 1)

    def set_config(self, config: BusinessConfig) -> None:
        self.config = config
        self.empty_catalog_state.refresh_accent(config.accent_color)
        self.empty_cart_state.refresh_accent(config.accent_color)
        self.checkout_btn.setIcon(
            get_svg_icon(
                "arrow_left" if is_rtl() else "arrow_right",
                contrast_text_hex(config.accent_color),
                16,
            )
        )
        self._ensure_enabled_order_type()
        self._sync_order_type_ui()
        self.refresh_operational_badges()
        self.refresh_catalog()
        self.refresh_cart(force_rebuild=True, animate_cards=False)

    def refresh_operational_badges(self) -> None:
        actor = self.db.session_actor
        has_shift = False
        if actor is not None:
            try:
                has_shift = self.db.get_active_shift(actor.id) is not None
            except Exception:
                has_shift = False
        self.shift_btn.setText(
            tr("register.shift.active") if has_shift else tr("register.shift.idle")
        )
        self.shift_btn.setProperty("role", "primary" if has_shift else "secondary")
        on_color = contrast_text_hex(self.config.accent_color) if has_shift else "#202820"
        self.shift_btn.setIcon(get_svg_icon("clock", on_color, 13))
        self.shift_btn.style().unpolish(self.shift_btn)
        self.shift_btn.style().polish(self.shift_btn)

        low_count = 0
        if getattr(self.config, "inventory_tracking_enabled", True):
            try:
                low_count = len(self.db.get_low_stock_ingredients())
            except Exception:
                low_count = 0
        if low_count > 0:
            self.low_stock_badge_btn.setText(tr("register.low_stock_badge", count=low_count))
            self.low_stock_badge_btn.show()
        else:
            self.low_stock_badge_btn.hide()

        # Sync cashier discount permission state
        if actor is not None and not actor.is_admin and not getattr(self.config, "allow_cashier_discounts", True):
            if self.discount_type_combo.currentIndex() != 0:
                self.discount_type_combo.blockSignals(True)
                self.discount_type_combo.setCurrentIndex(0)
                self.discount_type_combo.blockSignals(False)
                self.discount_val_edit.clear()
                self.discount_label_edit.clear()
            self.discount_type_combo.setEnabled(False)
            self.discount_val_edit.hide()
            self.discount_label_edit.hide()
        else:
            self.discount_type_combo.setEnabled(True)

    def open_shift_dialog(self) -> None:
        actor = self.db.session_actor
        dlg = ShiftManagerDialog(self.db, self.config, current_user=actor, parent=self)
        win = self.window()
        if hasattr(win, "clamp_dialog_to_screen"):
            win.clamp_dialog_to_screen(dlg)
        dlg.shift_changed.connect(self._on_shift_dialog_changed)
        dlg.exec()
        self.refresh_operational_badges()

    def _on_shift_dialog_changed(self) -> None:
        self.refresh_operational_badges()
        self.shift_changed.emit()

    def _on_discount_changed(self) -> None:
        dtype = str(self.discount_type_combo.currentData() or DISCOUNT_TYPE_NONE)
        is_active = dtype != DISCOUNT_TYPE_NONE
        self.discount_val_edit.setVisible(is_active)
        self.discount_label_edit.setVisible(is_active)
        self.refresh_cart(force_rebuild=False, animate_cards=False)

    def cart_discount_details(self) -> tuple[str, int, str, int, str]:
        """Return (discount_type, discount_value, discount_label, discount_minor, error_hint)."""
        _cnt, subtotal_minor = self.cart_totals_minor()
        dtype = str(self.discount_type_combo.currentData() or DISCOUNT_TYPE_NONE)
        if dtype == DISCOUNT_TYPE_NONE or subtotal_minor <= 0:
            return DISCOUNT_TYPE_NONE, 0, "", 0, ""

        actor = self.db.session_actor
        if actor is not None and not actor.is_admin and not getattr(self.config, "allow_cashier_discounts", True):
            return DISCOUNT_TYPE_NONE, 0, "", 0, tr("discount.err.cashier_disabled")

        raw_val = self.discount_val_edit.text().strip()
        if not raw_val:
            return dtype, 0, self.discount_label_edit.text().strip(), 0, ""

        if dtype == DISCOUNT_TYPE_PERCENT:
            try:
                norm_raw = raw_val.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩٫", "0123456789."))
                pct = int(round(float(norm_raw)))
            except ValueError:
                return dtype, 0, "", 0, tr("discount.err.invalid_value")
            if pct < 0 or pct > 100:
                return dtype, pct, "", 0, tr("discount.err.percent_range")
            disc_minor = min(subtotal_minor, (subtotal_minor * pct + 50) // 100)
            disc_val = pct
        else:
            try:
                disc_val = parse_major_to_minor(raw_val, self.config.currency)
            except ValueError as exc:
                return dtype, 0, "", 0, str(exc)
            if disc_val < 0:
                return dtype, 0, "", 0, tr("discount.err.invalid_value")
            disc_minor = min(subtotal_minor, disc_val)

        if actor is not None and not actor.is_admin:
            max_pct = max(0, min(100, int(getattr(self.config, "max_cashier_discount_pct", 100) or 0)))
            if disc_minor > 0 and subtotal_minor > 0 and (disc_minor * 100) > (subtotal_minor * max_pct):
                return (
                    dtype,
                    disc_val,
                    self.discount_label_edit.text().strip(),
                    disc_minor,
                    tr("discount.err.exceeds_cap", pct=max_pct),
                )
        return dtype, disc_val, self.discount_label_edit.text().strip(), disc_minor, ""

    def _enabled_order_types(self) -> list[str]:
        out: list[str] = []
        if getattr(self.config, "enable_dine_in", True):
            out.append(ORDER_TYPE_DINE_IN)
        if getattr(self.config, "enable_takeaway", True):
            out.append(ORDER_TYPE_TAKEAWAY)
        if getattr(self.config, "enable_delivery", True):
            out.append(ORDER_TYPE_DELIVERY)
        return out or [ORDER_TYPE_DINE_IN]

    def _ensure_enabled_order_type(self) -> None:
        enabled = self._enabled_order_types()
        if self._order_type not in enabled:
            pref = getattr(self.config, "default_order_type", enabled[0]) or enabled[0]
            self._order_type = pref if pref in enabled else enabled[0]

    def update_order_count(self, count: int) -> None:
        self._order_count = max(0, int(count))
        self.orders_chip.setText(tr("header.orders_chip", count=self._order_count))

    def refresh_held_count(self) -> None:
        try:
            self._held_count = self.db.count_held_orders()
        except Exception:
            self._held_count = 0
        self.held_orders_btn.setText(tr("register.held_btn", count=self._held_count))

    def set_order_type(self, order_type: str) -> None:
        if order_type not in (ORDER_TYPE_DINE_IN, ORDER_TYPE_TAKEAWAY, ORDER_TYPE_DELIVERY):
            order_type = ORDER_TYPE_DINE_IN
        self._order_type = order_type
        self._sync_order_type_ui()

    def _sync_order_type_ui(self) -> None:
        enabled = set(self._enabled_order_types())
        mapping = (
            (self.dine_in_btn, ORDER_TYPE_DINE_IN, "utensils"),
            (self.takeaway_btn, ORDER_TYPE_TAKEAWAY, "orders"),
            (self.delivery_btn, ORDER_TYPE_DELIVERY, "truck"),
        )
        on_primary = contrast_text_hex(self.config.accent_color)
        for btn, otype, icon_name in mapping:
            btn.setVisible(otype in enabled)
            sel = self._order_type == otype
            btn.setProperty("selected", "true" if sel else "false")
            btn.setIcon(get_svg_icon(icon_name, on_primary if sel else "#5E6B5F", 13))
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        self.dine_in_fields.setVisible(self._order_type == ORDER_TYPE_DINE_IN)
        self.delivery_fields.setVisible(self._order_type == ORDER_TYPE_DELIVERY)

    def _reset_order_metadata(self) -> None:
        self._active_held_order_id = None
        self._active_held_number = ""
        self.held_editing_badge.hide()
        self.table_edit.clear()
        self.cust_name_edit.clear()
        self.cust_phone_edit.clear()
        self.deliv_addr_edit.clear()
        self.discount_type_combo.blockSignals(True)
        self.discount_type_combo.setCurrentIndex(0)
        self.discount_type_combo.blockSignals(False)
        self.discount_val_edit.clear()
        self.discount_val_edit.hide()
        self.discount_label_edit.clear()
        self.discount_label_edit.hide()
        self.discount_amt_lbl.clear()
        self.discount_hint_lbl.hide()
        enabled = self._enabled_order_types()
        pref = getattr(self.config, "default_order_type", enabled[0]) or enabled[0]
        self.set_order_type(pref if pref in enabled else enabled[0])

    def _sync_held_editing_badge(self) -> None:
        if self._active_held_order_id is not None and self._active_held_number:
            self.held_editing_badge.setText(
                tr("register.editing_held_badge", num=self._active_held_number)
            )
            self.held_editing_badge.show()
        else:
            self.held_editing_badge.hide()

    def retranslate_ui(self) -> None:
        """Update all visible strings & layout details while preserving cart and search."""
        self.search_input.setPlaceholderText(tr("register.search_ph"))
        self.catalog_count_badge.setText(tr_count("item", len(self._last_products)))
        self.update_order_count(self._order_count)
        self.refresh_held_count()
        self.empty_catalog_state.set_texts(
            tr("register.empty_menu.title"),
            tr("register.empty_menu.desc"),
            tr("register.empty_menu.btn"),
        )
        self.empty_cart_state.set_texts(
            tr("register.empty_cart.title"),
            tr("register.empty_cart.desc"),
        )
        self.cart_title_lbl.setText(tr("register.order_title"))
        self.held_orders_btn.setToolTip(tr("register.held_btn_tip"))
        self.clear_cart_btn.setText(tr("register.clear_btn"))
        self.dine_in_btn.setText(tr("order_type.dine_in"))
        self.takeaway_btn.setText(tr("order_type.takeaway"))
        self.delivery_btn.setText(tr("order_type.delivery"))
        self.table_edit.setPlaceholderText(tr("order_type.table_ph"))
        self.cust_name_edit.setPlaceholderText(tr("order_type.customer_name_ph"))
        self.cust_phone_edit.setPlaceholderText(tr("order_type.customer_phone_ph"))
        self.deliv_addr_edit.setPlaceholderText(tr("order_type.delivery_addr_ph"))
        self._sync_order_type_ui()
        self._sync_held_editing_badge()
        self.hold_btn.setText(tr("register.hold_btn"))
        self.hold_btn.setToolTip(tr("register.hold_btn_tip"))
        self.sub_title_lbl.setText(tr("register.subtotal_label"))
        self.tot_title_lbl.setText(tr("register.total_due_label"))
        cur_dt = self.discount_type_combo.currentData() or DISCOUNT_TYPE_NONE
        self.discount_type_combo.blockSignals(True)
        self.discount_type_combo.clear()
        self.discount_type_combo.addItem(tr("discount.none"), DISCOUNT_TYPE_NONE)
        self.discount_type_combo.addItem(tr("discount.percent"), DISCOUNT_TYPE_PERCENT)
        self.discount_type_combo.addItem(tr("discount.fixed"), DISCOUNT_TYPE_FIXED)
        dt_idx = self.discount_type_combo.findData(cur_dt)
        self.discount_type_combo.setCurrentIndex(dt_idx if dt_idx >= 0 else 0)
        self.discount_type_combo.blockSignals(False)
        self.discount_val_edit.setPlaceholderText(tr("discount.value_ph"))
        self.discount_label_edit.setPlaceholderText(tr("discount.label_ph"))
        self.refresh_operational_badges()
        self.checkout_btn.setIcon(
            get_svg_icon(
                "arrow_left" if is_rtl() else "arrow_right",
                contrast_text_hex(self.config.accent_color),
                16,
            )
        )

        for i in range(self.category_layout.count()):
            item = self.category_layout.itemAt(i)
            w = item.widget() if item is not None else None
            if w is not None and _qt_is_valid(w) and isinstance(w, BoutiqueCategoryCard):
                rec = self._category_records.get(w.category)
                disp = "" if w.category == "All" else (tr_category_record(rec) if rec is not None else "")
                w.retranslate_ui(display_label=disp)

        prod_map = {p.id: p for p in self._last_products}
        for pid, card in list(self._product_cards.items()):
            if not _qt_is_valid(card):
                self._product_cards.pop(pid, None)
                continue
            prod = prod_map.get(pid)
            price_txt = (
                format_minor(prod.price_minor, self.config.currency)
                if prod is not None
                else None
            )
            card.retranslate_ui(
                price_txt,
                name_text=(prod.display_name() if prod is not None else None),
                asset_lookup_name=((prod.name_en or prod.name) if prod is not None else None),
            )

        for key, row_w in list(self._cart_row_widgets.items()):
            if not _qt_is_valid(row_w):
                self._cart_row_widgets.pop(key, None)
                continue
            line = self.cart.get(key)
            if line is not None:
                row_w.retranslate_ui(
                    format_minor(line.unit_price_minor, self.config.currency),
                    format_minor(line.line_total_minor, self.config.currency),
                    name_text=line.display_name(),
                    modifiers_text=line.modifiers_summary(),
                )

        self.refresh_cart(force_rebuild=False, animate_cards=False)

    def clear_session_state(self) -> None:
        """Reset transient register cart and filters on logout or restore."""
        self.cart.clear()
        self._reset_order_metadata()
        self.selected_category = "All"
        self.search_input.blockSignals(True)
        self.search_input.clear()
        self.search_input.blockSignals(False)
        self.refresh_held_count()
        self.refresh_operational_badges()
        self.refresh_catalog()
        self.refresh_cart(force_rebuild=True, animate_cards=False)

    def focus_search(self) -> None:
        self.search_input.setFocus()
        self.search_input.selectAll()

    def _on_search_changed(self, _text: str) -> None:
        self._render_product_tiles()

    def _reset_catalog_filter(self) -> None:
        self.selected_category = "All"
        self.search_input.clear()
        self.refresh_catalog()

    def refresh_catalog(self) -> None:
        cat_records = self.db.list_category_records(include_archived=False)
        self._category_records = {rec.primary_name: rec for rec in cat_records}
        self._product_mod_counts = self.db.list_product_modifier_counts()
        counts = self.db.get_category_counts(include_archived=False)
        categories = ["All"] + list(counts.keys())
        total_all = sum(counts.values())
        if self.selected_category not in categories:
            self.selected_category = "All"

        _dispose_layout_widgets(self.category_layout)

        for cat in categories:
            cat_count = total_all if cat == "All" else counts.get(cat, 0)
            rec = self._category_records.get(cat)
            disp_label = "" if cat == "All" else (tr_category_record(rec) if rec is not None else "")
            card = BoutiqueCategoryCard(
                category=cat,
                item_count=cat_count,
                selected=(cat == self.selected_category),
                accent_color=self.config.accent_color,
                display_label=disp_label,
            )
            card.clicked.connect(self._select_category)
            self.category_layout.addWidget(card)
        self.category_layout.addStretch(1)

        self._render_product_tiles()

    def _select_category(self, category: str) -> None:
        self.selected_category = category
        self.refresh_catalog()

    def _compute_grid_cols(self) -> int:
        avail_width = max(320, self.product_scroll.viewport().width())
        return max(2, min(5, (avail_width - 8) // 188))

    def _render_product_tiles(self) -> None:
        self._product_cards.clear()
        _dispose_layout_widgets(self.product_grid)

        products = self.db.list_products(
            search=self.search_input.text(),
            category=self.selected_category,
            include_archived_categories=False,
        )
        self._last_products = products
        count = len(products)
        self.catalog_count_badge.setText(tr_count("item", count))

        if not products:
            self.product_grid_host.setMinimumHeight(0)
            self.catalog_stack.setCurrentIndex(1)
            return

        self.catalog_stack.setCurrentIndex(0)
        cols = self._compute_grid_cols()
        self._last_cols = cols

        for col in range(5):
            self.product_grid.setColumnStretch(col, 1 if col < cols else 0)

        qty_by_prod = self._summed_qty_by_product()
        for idx, product in enumerate(products):
            price_str = format_minor(product.price_minor, self.config.currency)
            card = CozyProductCard(
                product.id,
                product.display_name(),
                product.category,
                price_str,
                product.image_filename,
                self.config.accent_color,
                asset_lookup_name=(product.name_en or product.name),
            )
            # increment_requested covers both card click and inline [ + ] stepper tap cleanly
            card.increment_requested.connect(lambda p=product: self.add_product_to_cart(p))
            card.decrement_requested.connect(lambda p=product: self.decrement_product_from_cart(p))
            card.set_cart_quantity(qty_by_prod.get(product.id, 0), animate=False)
            self._product_cards[product.id] = card
            self.product_grid.addWidget(card, idx // cols, idx % cols)

        rows = (len(products) + cols - 1) // cols
        margins = self.product_grid.contentsMargins()
        v_spacing = self.product_grid.verticalSpacing()
        if v_spacing < 0:
            v_spacing = self.product_grid.spacing()
        required_h = (
            margins.top()
            + margins.bottom()
            + rows * 196
            + max(0, rows - 1) * max(0, v_spacing)
        )
        self.product_grid_host.setMinimumHeight(required_h)
        self.product_grid.activate()
        self.product_grid_host.updateGeometry()

    def _summed_qty_by_product(self) -> dict[int, int]:
        totals: dict[int, int] = {}
        for line in self.cart.values():
            totals[line.product_id] = totals.get(line.product_id, 0) + line.quantity
        return totals

    def _sync_product_card_badges(self, *, animate: bool = True) -> None:
        qty_by_prod = self._summed_qty_by_product()
        for pid, card in list(self._product_cards.items()):
            if _qt_is_valid(card):
                card.set_cart_quantity(qty_by_prod.get(pid, 0), animate=animate)
            else:
                self._product_cards.pop(pid, None)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._last_products:
            new_cols = self._compute_grid_cols()
            if new_cols != self._last_cols:
                self._render_product_tiles()

    def add_product_to_cart(self, product: Product) -> None:
        mods_enabled = getattr(self.config, "modifiers_enabled", True)
        groups = (
            self.db.get_product_modifiers(product.id, include_inactive=False)
            if mods_enabled and self._product_mod_counts.get(product.id, 0) > 0
            else []
        )
        active_groups = [g for g in groups if g.is_active and not g.is_archived and g.active_options]
        if active_groups:
            dlg = ProductCustomizeDialog(self.config, product, active_groups, parent=self)
            if dlg.exec() != QDialog.Accepted:
                return
            chosen_mods = dlg.result_modifiers
            unit_minor = dlg.result_unit_price_minor
        else:
            chosen_mods = ()
            unit_minor = product.price_minor

        candidate = CartLine(
            product_id=product.id,
            name=product.name,
            unit_price_minor=unit_minor,
            quantity=1,
            image_filename=product.image_filename,
            category=product.category,
            name_en=product.name_en,
            name_ar=product.name_ar,
            modifiers=chosen_mods,
            base_unit_price_minor=product.price_minor,
        )
        cart_key = candidate.cart_key
        existing = self.cart.get(cart_key)
        new_qty = existing.quantity + 1 if existing else 1
        self.cart[cart_key] = CartLine(
            product_id=product.id,
            name=product.name,
            unit_price_minor=unit_minor,
            quantity=new_qty,
            image_filename=product.image_filename,
            category=product.category,
            name_en=product.name_en,
            name_ar=product.name_ar,
            modifiers=chosen_mods,
            base_unit_price_minor=product.price_minor,
        )
        self.refresh_cart(changed_cart_key=cart_key, animate_cards=True)
        self.status_message.emit(
            tr("register.toast.added", name=product.display_name(), qty=new_qty)
        )

    def _edit_cart_line_modifiers(self, cart_key: int | str) -> None:
        line = self.cart.get(cart_key)
        if line is None:
            return
        prod = next((p for p in self._last_products if p.id == line.product_id), None)
        if prod is None:
            all_prods = {p.id: p for p in self.db.list_products()}
            prod = all_prods.get(line.product_id)
        if prod is None:
            return

        groups = self.db.get_product_modifiers(prod.id, include_inactive=False)
        active_groups = [g for g in groups if g.is_active and not g.is_archived and g.active_options]
        if not active_groups:
            return

        dlg = ProductCustomizeDialog(
            self.config,
            prod,
            active_groups,
            initial_selections=line.modifiers,
            editing_cart_item=True,
            parent=self,
        )
        if dlg.exec() != QDialog.Accepted:
            return

        updated_line = CartLine(
            product_id=prod.id,
            name=prod.name,
            unit_price_minor=dlg.result_unit_price_minor,
            quantity=line.quantity,
            image_filename=prod.image_filename,
            category=prod.category,
            name_en=prod.name_en,
            name_ar=prod.name_ar,
            modifiers=dlg.result_modifiers,
            base_unit_price_minor=prod.price_minor,
        )
        new_key = updated_line.cart_key
        if new_key == cart_key:
            self.cart[cart_key] = updated_line
        else:
            # Rebuild dict preserving order smoothly; merge if target key already exists
            rebuilt: dict[int | str, CartLine] = {}
            merged_done = False
            for k, v in self.cart.items():
                if k == cart_key:
                    continue
                if k == new_key:
                    rebuilt[k] = CartLine(
                        product_id=updated_line.product_id,
                        name=updated_line.name,
                        unit_price_minor=updated_line.unit_price_minor,
                        quantity=v.quantity + updated_line.quantity,
                        image_filename=updated_line.image_filename,
                        category=updated_line.category,
                        name_en=updated_line.name_en,
                        name_ar=updated_line.name_ar,
                        modifiers=updated_line.modifiers,
                        base_unit_price_minor=updated_line.base_unit_price_minor,
                    )
                    merged_done = True
                else:
                    rebuilt[k] = v
            if not merged_done:
                rebuilt[new_key] = updated_line
            self.cart = rebuilt

        self.refresh_cart(changed_cart_key=new_key, force_rebuild=True, animate_cards=True)
        self.status_message.emit(
            tr("register.toast.mods_updated", name=updated_line.display_name())
        )

    def decrement_product_from_cart(self, product: Product | int) -> None:
        pid = product.id if isinstance(product, Product) else int(product)
        target_key: int | str | None = None
        for k, v in self.cart.items():
            if v.product_id == pid:
                target_key = k
        if target_key is None:
            return
        line = self.cart[target_key]
        self.update_cart_quantity(target_key, -1)
        remaining = self.cart.get(target_key)
        if remaining is None:
            self.status_message.emit(tr("register.toast.removed", name=line.display_name()))
        else:
            self.status_message.emit(
                tr("register.toast.updated", name=line.display_name(), qty=remaining.quantity)
            )

    def update_cart_quantity(self, cart_key: int | str, delta: int) -> None:
        line = self.cart.get(cart_key)
        if not line:
            return
        new_qty = max(0, line.quantity + delta)
        if new_qty == 0:
            self.cart.pop(cart_key, None)
        else:
            self.cart[cart_key] = CartLine(
                product_id=line.product_id,
                name=line.name,
                unit_price_minor=line.unit_price_minor,
                quantity=new_qty,
                image_filename=line.image_filename,
                category=line.category,
                name_en=line.name_en,
                name_ar=line.name_ar,
                modifiers=line.modifiers,
                base_unit_price_minor=line.base_unit_price_minor,
            )
        self.refresh_cart(changed_cart_key=cart_key, animate_cards=True)

    def remove_cart_item(self, cart_key: int | str) -> None:
        removed = self.cart.pop(cart_key, None)
        self.refresh_cart(changed_cart_key=cart_key, animate_cards=True)
        if removed is not None:
            self.status_message.emit(tr("register.toast.removed", name=removed.display_name()))

    def _confirm_clear_cart(self) -> None:
        if not self.cart:
            return
        answer = QMessageBox.question(
            self,
            tr("register.confirm_clear.title"),
            tr("register.confirm_clear.body"),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self.cart.clear()
            self._reset_order_metadata()
            self.refresh_cart(animate_cards=True)
            self.status_message.emit(tr("register.toast.cleared"))

    def sync_cart_with_catalog(self) -> None:
        """Refresh active cart item metadata/prices/modifiers or drop deleted items."""
        self._product_mod_counts = self.db.list_product_modifier_counts()
        catalog_map = {p.id: p for p in self.db.list_products()}
        updated: dict[int | str, CartLine] = {}
        mods_cache: dict[int, dict[int, tuple]] = {}
        for _key, line in self.cart.items():
            prod = catalog_map.get(line.product_id)
            if prod is None:
                continue
            if prod.id not in mods_cache:
                opt_map = {}
                for grp in self.db.get_product_modifiers(prod.id, include_inactive=False):
                    for opt in grp.active_options:
                        opt_map[opt.id] = (grp, opt)
                mods_cache[prod.id] = opt_map
            opt_map = mods_cache[prod.id]

            refreshed_mods = []
            for mod in line.modifiers:
                if mod.option_id is not None and mod.option_id in opt_map:
                    grp, opt = opt_map[mod.option_id]
                    refreshed_mods.append(
                        type(mod)(
                            group_id=grp.id,
                            option_id=opt.id,
                            group_name=grp.name_en or grp.name_ar,
                            group_name_en=grp.name_en,
                            group_name_ar=grp.name_ar,
                            option_name=opt.name_en or opt.name_ar,
                            option_name_en=opt.name_en,
                            option_name_ar=opt.name_ar,
                            price_delta_minor=opt.resolved_price_delta_minor,
                        )
                    )
            unit_minor = prod.price_minor + sum(m.price_delta_minor for m in refreshed_mods)
            cand = CartLine(
                product_id=prod.id,
                name=prod.name,
                unit_price_minor=unit_minor,
                quantity=line.quantity,
                image_filename=prod.image_filename,
                category=prod.category,
                name_en=prod.name_en,
                name_ar=prod.name_ar,
                modifiers=tuple(refreshed_mods),
                base_unit_price_minor=prod.price_minor,
            )
            ckey = cand.cart_key
            if ckey in updated:
                prev = updated[ckey]
                updated[ckey] = CartLine(
                    product_id=cand.product_id,
                    name=cand.name,
                    unit_price_minor=cand.unit_price_minor,
                    quantity=prev.quantity + cand.quantity,
                    image_filename=cand.image_filename,
                    category=cand.category,
                    name_en=cand.name_en,
                    name_ar=cand.name_ar,
                    modifiers=cand.modifiers,
                    base_unit_price_minor=cand.base_unit_price_minor,
                )
            else:
                updated[ckey] = cand
        self.cart = updated
        self.refresh_cart(force_rebuild=True, animate_cards=False)

    def cart_totals_minor(self) -> tuple[int, int]:
        total_items = sum(line.quantity for line in self.cart.values())
        subtotal = sum(line.line_total_minor for line in self.cart.values())
        return total_items, subtotal

    def refresh_cart(
        self,
        *,
        changed_cart_key: int | str | None = None,
        changed_product_id: int | None = None,
        force_rebuild: bool = False,
        animate_cards: bool = True,
    ) -> None:
        if changed_cart_key is None and changed_product_id is not None:
            changed_cart_key = changed_product_id

        lines = list(self.cart.values())
        desired_keys = [line.cart_key for line in lines]
        existing_keys = list(self._cart_row_widgets.keys())

        can_reuse = (
            bool(lines)
            and not force_rebuild
            and desired_keys == existing_keys
            and all(_qt_is_valid(self._cart_row_widgets.get(k)) for k in desired_keys)
        )
        if can_reuse:
            self.cart_stack.setCurrentIndex(1)
            for line in lines:
                ckey = line.cart_key
                row_card = self._cart_row_widgets[ckey]
                row_card.update_values(
                    line.quantity,
                    format_minor(line.line_total_minor, self.config.currency),
                    format_minor(line.unit_price_minor, self.config.currency),
                    modifiers_text=line.modifiers_summary(),
                    has_modifiers=self._product_mod_counts.get(line.product_id, 0) > 0,
                    pulse=(changed_cart_key == ckey),
                )
        else:
            prev_keys = set(existing_keys)
            self._cart_row_widgets.clear()
            _dispose_layout_widgets(self.cart_list_layout)

            if lines:
                self.cart_stack.setCurrentIndex(1)
                for line in lines:
                    ckey = line.cart_key
                    has_mods = self._product_mod_counts.get(line.product_id, 0) > 0
                    row_card = CartItemRowWidget(
                        name=line.display_name(),
                        unit_price_text=format_minor(line.unit_price_minor, self.config.currency),
                        line_total_text=format_minor(line.line_total_minor, self.config.currency),
                        quantity=line.quantity,
                        image_filename=line.image_filename,
                        category=line.category,
                        accent_color=self.config.accent_color,
                        asset_lookup_name=(line.name_en or line.name),
                        modifiers_text=line.modifiers_summary(),
                        has_modifiers=has_mods,
                    )
                    row_card.decrement_clicked.connect(
                        lambda k=ckey: self.update_cart_quantity(k, -1)
                    )
                    row_card.increment_clicked.connect(
                        lambda k=ckey: self.update_cart_quantity(k, 1)
                    )
                    row_card.remove_clicked.connect(
                        lambda k=ckey: self.remove_cart_item(k)
                    )
                    row_card.edit_modifiers_clicked.connect(
                        lambda k=ckey: self._edit_cart_line_modifiers(k)
                    )
                    self._cart_row_widgets[ckey] = row_card
                    self.cart_list_layout.addWidget(row_card)
                    if animate_cards and ckey not in prev_keys:
                        animate_fade_in(row_card, duration_ms=145, start_opacity=0.6)
                self.cart_list_layout.addStretch(1)
            else:
                self.cart_stack.setCurrentIndex(0)

        item_count, subtotal_minor = self.cart_totals_minor()
        self.item_count_lbl.setText(tr_count("item", item_count))
        self.subtotal_lbl.setText(format_minor(subtotal_minor, self.config.currency))

        _dt, _dv, _dl, disc_minor, disc_err = self.cart_discount_details()
        net_total_minor = max(0, subtotal_minor - disc_minor)
        if disc_minor > 0:
            self.discount_amt_lbl.setText(f"-{format_minor(disc_minor, self.config.currency)}")
        else:
            self.discount_amt_lbl.clear()
        self.discount_hint_lbl.setText(disc_err)
        self.discount_hint_lbl.setVisible(bool(disc_err))

        formatted_total = format_minor(net_total_minor, self.config.currency)
        self.total_lbl.setText(formatted_total)
        if animate_cards and changed_cart_key is not None:
            animate_flash_highlight(self.total_lbl, duration_ms=140, dip_opacity=0.72)

        self.checkout_btn.setText(
            tr("register.pay_btn_total", total=formatted_total)
            if item_count > 0
            else tr("register.pay_btn_idle")
        )

        has_items = item_count > 0
        self.clear_cart_btn.setEnabled(has_items)
        self.hold_btn.setEnabled(has_items)
        self.checkout_btn.setEnabled(has_items and not bool(disc_err))
        self._sync_held_editing_badge()
        self._sync_product_card_badges(animate=animate_cards)

    def _hold_current_order(self) -> None:
        if not self.cart:
            return
        was_editing = self._active_held_order_id is not None
        try:
            held = self.db.create_or_update_held_order(
                list(self.cart.values()),
                order_type=self._order_type,
                table_number=self.table_edit.text(),
                customer_name=self.cust_name_edit.text(),
                customer_phone=self.cust_phone_edit.text(),
                delivery_address=self.deliv_addr_edit.text(),
                held_order_id=self._active_held_order_id,
            )
        except Exception as exc:
            QMessageBox.warning(self, tr("common.validation_error"), str(exc))
            return

        self.cart.clear()
        self._reset_order_metadata()
        self.refresh_held_count()
        self.refresh_cart(animate_cards=True)
        toast_key = "register.toast.held_updated" if was_editing else "register.toast.held"
        self.status_message.emit(tr(toast_key, num=held.hold_number))

    def _open_held_orders_dialog(self) -> None:
        dlg = HeldOrdersDialog(
            self.db,
            self.config,
            has_active_cart=bool(self.cart),
            parent=self,
        )
        dlg.held_orders_changed.connect(self.refresh_held_count)
        accepted = dlg.exec() == QDialog.Accepted
        self.refresh_held_count()
        if accepted and dlg.resumed_order is not None:
            self.resume_held_order(dlg.resumed_order)

    def resume_held_order(self, held: HeldOrderRecord) -> None:
        self._active_held_order_id = held.id
        self._active_held_number = held.hold_number
        self.set_order_type(held.order_type)
        self.table_edit.setText(held.table_number or "")
        self.cust_name_edit.setText(held.customer_name or "")
        self.cust_phone_edit.setText(held.customer_phone or "")
        self.deliv_addr_edit.setText(held.delivery_address or "")

        catalog_map = {p.id: p for p in self.db.list_products()}
        new_cart: dict[int | str, CartLine] = {}
        for item in held.items:
            pid = int(item.product_id) if item.product_id is not None else 0
            prod = catalog_map.get(pid)
            if prod is not None:
                cand = CartLine(
                    product_id=prod.id,
                    name=prod.name,
                    unit_price_minor=item.unit_price_minor,
                    quantity=item.quantity,
                    image_filename=prod.image_filename,
                    category=prod.category,
                    name_en=prod.name_en,
                    name_ar=prod.name_ar,
                    modifiers=item.modifiers,
                    base_unit_price_minor=item.base_unit_price_minor,
                )
            elif pid > 0:
                cand = CartLine(
                    product_id=pid,
                    name=item.product_name,
                    unit_price_minor=item.unit_price_minor,
                    quantity=item.quantity,
                    image_filename=item.image_filename,
                    category=item.category_snapshot,
                    name_en=item.product_name_en,
                    name_ar=item.product_name_ar,
                    modifiers=item.modifiers,
                    base_unit_price_minor=item.base_unit_price_minor,
                )
            else:
                continue
            ckey = cand.cart_key
            if ckey in new_cart:
                prev = new_cart[ckey]
                new_cart[ckey] = CartLine(
                    product_id=cand.product_id,
                    name=cand.name,
                    unit_price_minor=cand.unit_price_minor,
                    quantity=prev.quantity + cand.quantity,
                    image_filename=cand.image_filename,
                    category=cand.category,
                    name_en=cand.name_en,
                    name_ar=cand.name_ar,
                    modifiers=cand.modifiers,
                    base_unit_price_minor=cand.base_unit_price_minor,
                )
            else:
                new_cart[ckey] = cand

        self.cart = new_cart
        self.sync_cart_with_catalog()
        self.refresh_held_count()
        self.status_message.emit(tr("register.toast.resumed", num=held.hold_number))

    def trigger_checkout(self) -> None:
        self._attempt_checkout()

    def _attempt_checkout(self) -> None:
        if self._checking_out:
            return
        item_count, _subtotal_minor = self.cart_totals_minor()
        if item_count == 0:
            return

        d_type, d_val, d_lbl, d_minor, d_err = self.cart_discount_details()
        if d_err:
            QMessageBox.warning(self, tr("common.validation_error"), d_err)
            return

        actor = self.db.session_actor
        if (
            getattr(self.config, "require_shift_for_sales", False)
            and actor is not None
            and self.db.get_active_shift(actor.id) is None
        ):
            answer = QMessageBox.question(
                self,
                tr("shift.required.title"),
                tr("shift.required.prompt"),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if answer == QMessageBox.Yes:
                self.open_shift_dialog()
            if self.db.get_active_shift(actor.id) is None:
                return

        self._checking_out = True
        try:
            dlg = CheckoutPaymentDialog(
                self.db,
                self.config,
                list(self.cart.values()),
                order_type=self._order_type,
                table_number=self.table_edit.text(),
                customer_name=self.cust_name_edit.text(),
                customer_phone=self.cust_phone_edit.text(),
                delivery_address=self.deliv_addr_edit.text(),
                held_order_id=self._active_held_order_id,
                discount_type=d_type,
                discount_value=d_val,
                discount_label=d_lbl,
                discount_minor=d_minor,
                parent=self,
            )
            win = self.window()
            if hasattr(win, "clamp_dialog_to_screen"):
                win.clamp_dialog_to_screen(dlg)
            accepted = dlg.exec() == QDialog.Accepted
            result_order = dlg.result_order
        finally:
            self._checking_out = False

        if not accepted or result_order is None:
            return

        order = result_order
        self.cart.clear()
        self._reset_order_metadata()
        self.refresh_held_count()
        self.refresh_operational_badges()
        self.refresh_cart(animate_cards=True)
        self.status_message.emit(
            tr(
                "register.toast.completed",
                num=order.order_number,
                total=format_minor(order.total_minor, order.currency),
            )
        )
        self.order_completed.emit(order)
