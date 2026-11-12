from __future__ import annotations

import uuid
from dataclasses import dataclass

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QShowEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..config import BusinessConfig
from ..db import (
    DISCOUNT_TYPE_FIXED,
    DISCOUNT_TYPE_NONE,
    DISCOUNT_TYPE_PERCENT,
    ORDER_TYPE_DINE_IN,
    CartLine,
    Database,
    OrderRecord,
    validate_order_type,
)
from ..i18n import (
    format_order_type_summary,
    get_language,
    is_rtl,
    layout_direction,
    tr,
    tr_count,
    tr_payment_method,
)
from ..icons import get_svg_icon
from ..money import (
    format_major_input,
    format_minor,
    get_currency,
    parse_major_to_minor,
)
from ..payments import (
    METHOD_APPLE_PAY,
    METHOD_CARD,
    METHOD_CASH,
    METHOD_CUSTOM,
    METHOD_EWALLET,
    METHOD_INSTAPAY,
    CustomPaymentMethodDef,
    parse_payment_settings,
    validate_external_reference,
)
from ..theme import contrast_text_hex, get_tokens
from .animations import animate_window_fade_in, attach_button_feedback, set_button_loading


def _esc_amp(text: str) -> str:
    return (text or "").replace("&", "&&")


@dataclass(frozen=True)
class _MethodOption:
    key: str
    method_type: str
    stack_index: int
    icon_name: str
    custom_def: CustomPaymentMethodDef | None = None


class CheckoutPaymentDialog(QDialog):
    """Compact modal payment selection & manual tender confirmation dialog."""

    def __init__(
        self,
        db: Database,
        config: BusinessConfig,
        cart_lines: list[CartLine],
        initial_cash_text: str = "",
        parent: QWidget | None = None,
        *,
        order_type: str = ORDER_TYPE_DINE_IN,
        table_number: str = "",
        customer_name: str = "",
        customer_phone: str = "",
        delivery_address: str = "",
        held_order_id: int | None = None,
        discount_type: str = DISCOUNT_TYPE_NONE,
        discount_value: int = 0,
        discount_label: str = "",
        discount_minor: int = 0,
    ) -> None:
        super().__init__(parent)
        self.db = db
        self.config = config
        self.cart_lines = list(cart_lines)
        self._order_type = validate_order_type(order_type)
        self._table_number = (table_number or "").strip()
        self._customer_name = (customer_name or "").strip()
        self._customer_phone = (customer_phone or "").strip()
        self._delivery_address = (delivery_address or "").strip()
        self._held_order_id = int(held_order_id) if held_order_id is not None else None
        self.result_order: OrderRecord | None = None
        self._client_tx_key = str(uuid.uuid4())
        self._faded_once = False
        self._is_submitting = False
        self._split_mode: bool = False
        self._split_legs: list[dict[str, object]] = []

        self._pay_cfg = parse_payment_settings(
            config.payment_methods_json,
            config.business_country,
        )
        self._subtotal_minor = sum(
            max(0, int(line.unit_price_minor)) * max(1, int(line.quantity))
            for line in self.cart_lines
        )
        self._discount_type = (discount_type or DISCOUNT_TYPE_NONE).strip().upper()
        self._discount_value = max(0, int(discount_value or 0))
        self._discount_label = (discount_label or "").strip()
        if self._discount_type == DISCOUNT_TYPE_PERCENT and self._discount_value > 0:
            pct = min(100, self._discount_value)
            calc_disc = min(self._subtotal_minor, (self._subtotal_minor * pct + 50) // 100)
        elif self._discount_type == DISCOUNT_TYPE_FIXED and self._discount_value > 0:
            calc_disc = min(self._subtotal_minor, self._discount_value)
        else:
            calc_disc = min(self._subtotal_minor, max(0, int(discount_minor or 0)))
        self._discount_minor = calc_disc
        self._total_minor = max(0, self._subtotal_minor - self._discount_minor)
        self._item_count = sum(max(1, int(line.quantity)) for line in self.cart_lines)

        self._method_options: list[_MethodOption] = self._build_method_options()
        self.method_buttons: dict[str, QPushButton] = {}
        self.selected_method_key: str = self._method_options[0].key
        self._custom_ref_cache: dict[str, str] = {}
        self._custom_chk_cache: dict[str, bool] = {}

        self.setModal(True)
        self.setMinimumWidth(560)
        self.setLayoutDirection(layout_direction())

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        # Header Card: Title/Subtitle/Items + Prominent Order Total Badge
        header_card = QFrame()
        header_card.setObjectName("CheckoutHeaderCard")
        hdr_lay = QHBoxLayout(header_card)
        hdr_lay.setContentsMargins(14, 11, 14, 11)
        hdr_lay.setSpacing(12)

        left_hdr = QVBoxLayout()
        left_hdr.setContentsMargins(0, 0, 0, 0)
        left_hdr.setSpacing(3)
        self.title_lbl = QLabel()
        self.title_lbl.setProperty("class", "PageHeading")
        self.subtitle_lbl = QLabel()
        self.subtitle_lbl.setProperty("class", "MutedLabel")
        self.subtitle_lbl.setWordWrap(True)
        badges_row = QHBoxLayout()
        badges_row.setContentsMargins(0, 0, 0, 0)
        badges_row.setSpacing(6)
        self.items_badge = QLabel()
        self.items_badge.setProperty("class", "PrimaryBadge")
        self.items_badge.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.order_type_badge = QLabel()
        self.order_type_badge.setProperty("class", "CountBadge")
        self.order_type_badge.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.discount_badge = QLabel()
        self.discount_badge.setProperty("class", "PeachBadge")
        self.discount_badge.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.discount_badge.setVisible(self._discount_minor > 0)
        badges_row.addWidget(self.items_badge)
        badges_row.addWidget(self.order_type_badge)
        badges_row.addWidget(self.discount_badge)
        badges_row.addStretch(1)
        left_hdr.addWidget(self.title_lbl)
        left_hdr.addWidget(self.subtitle_lbl)
        left_hdr.addLayout(badges_row)
        hdr_lay.addLayout(left_hdr, 1)

        total_badge = QFrame()
        total_badge.setObjectName("CheckoutTotalBadge")
        tb_lay = QVBoxLayout(total_badge)
        tb_lay.setContentsMargins(14, 8, 14, 8)
        tb_lay.setSpacing(2)
        self.total_title_lbl = QLabel()
        self.total_title_lbl.setProperty("class", "MutedLabel")
        self.total_title_lbl.setAlignment(Qt.AlignCenter)
        self.total_value_lbl = QLabel(format_minor(self._total_minor, self.config.currency))
        self.total_value_lbl.setObjectName("TotalValueLabel")
        self.total_value_lbl.setAlignment(Qt.AlignCenter)
        tb_lay.addWidget(self.total_title_lbl)
        tb_lay.addWidget(self.total_value_lbl)
        hdr_lay.addWidget(total_badge, 0, Qt.AlignVCenter)
        root.addWidget(header_card)

        # Scrollable middle container only engages if height is tight (1366x768 @ 150% DPI)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll_host = QWidget()
        self._scroll_host.setObjectName("CheckoutScrollHost")
        body_lay = QVBoxLayout(self._scroll_host)
        body_lay.setContentsMargins(2, 2, 2, 4)
        body_lay.setSpacing(10)

        # Mode Switch: Single Payment | Split Payment
        mode_row = QHBoxLayout()
        mode_row.setContentsMargins(0, 0, 0, 0)
        mode_row.setSpacing(6)
        self.single_mode_btn = QPushButton()
        self.single_mode_btn.setProperty("class", "CategoryChip")
        self.single_mode_btn.setProperty("selected", "true")
        self.single_mode_btn.setCursor(Qt.PointingHandCursor)
        self.single_mode_btn.clicked.connect(lambda: self.set_split_mode(False))
        attach_button_feedback(self.single_mode_btn)

        self.split_mode_btn = QPushButton()
        self.split_mode_btn.setProperty("class", "CategoryChip")
        self.split_mode_btn.setProperty("selected", "false")
        self.split_mode_btn.setCursor(Qt.PointingHandCursor)
        self.split_mode_btn.clicked.connect(lambda: self.set_split_mode(True))
        attach_button_feedback(self.split_mode_btn)

        mode_row.addWidget(self.single_mode_btn)
        mode_row.addWidget(self.split_mode_btn)
        mode_row.addStretch(1)
        body_lay.addLayout(mode_row)

        self.mode_stack = QStackedWidget()

        # Page 0: Single Payment Mode
        single_page = QWidget()
        single_lay = QVBoxLayout(single_page)
        single_lay.setContentsMargins(0, 0, 0, 0)
        single_lay.setSpacing(10)

        # Payment Method Card Grid (up to 4 equal columns)
        grid_host = QWidget()
        self.method_grid = QGridLayout(grid_host)
        self.method_grid.setContentsMargins(0, 0, 0, 0)
        self.method_grid.setHorizontalSpacing(8)
        self.method_grid.setVerticalSpacing(8)
        cols = min(4, max(1, len(self._method_options)))
        for c in range(cols):
            self.method_grid.setColumnStretch(c, 1)
        for idx, opt in enumerate(self._method_options):
            btn = QPushButton()
            btn.setProperty("class", "PayMethodCard")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFocusPolicy(Qt.StrongFocus)
            btn.setMinimumHeight(48)
            btn.setIconSize(QSize(17, 17))
            btn.clicked.connect(lambda _c=False, k=opt.key: self.select_method(k))
            attach_button_feedback(btn)
            self.method_buttons[opt.key] = btn
            self.method_grid.addWidget(btn, idx // cols, idx % cols)
        single_lay.addWidget(grid_host)

        # Dedicated Active Method Detail Card
        self._detail_card = QFrame()
        self._detail_card.setObjectName("CheckoutDetailCard")
        detail_lay = QVBoxLayout(self._detail_card)
        detail_lay.setContentsMargins(15, 13, 15, 13)
        detail_lay.setSpacing(8)

        self.method_stack = QStackedWidget()
        self.method_stack.addWidget(self._build_cash_page(initial_cash_text))  # 0
        self.method_stack.addWidget(self._build_apple_pay_page())             # 1
        self.method_stack.addWidget(self._build_instapay_page())              # 2
        self.method_stack.addWidget(self._build_ewallet_page())               # 3
        self.method_stack.addWidget(self._build_card_page())                  # 4
        self.method_stack.addWidget(self._build_custom_page())                # 5
        detail_lay.addWidget(self.method_stack)

        single_lay.addWidget(self._detail_card)
        self.mode_stack.addWidget(single_page)

        # Page 1: Split Payment Mode
        self.mode_stack.addWidget(self._build_split_page())
        body_lay.addWidget(self.mode_stack)

        self._scroll.setWidget(self._scroll_host)
        root.addWidget(self._scroll, 1)

        self.validation_lbl = QLabel("")
        self.validation_lbl.setProperty("class", "ValidationHint")
        self.validation_lbl.setWordWrap(True)
        self.validation_lbl.hide()
        root.addWidget(self.validation_lbl)

        # Footer CTA bar
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addStretch(1)
        self.cancel_btn = QPushButton()
        self.cancel_btn.setMinimumHeight(40)
        self.cancel_btn.setMinimumWidth(100)
        self.cancel_btn.clicked.connect(self.reject)
        attach_button_feedback(self.cancel_btn)

        self.confirm_btn = QPushButton()
        self.confirm_btn.setObjectName("PrimaryActionBtn")
        self.confirm_btn.setProperty("role", "primary")
        self.confirm_btn.setMinimumHeight(40)
        self.confirm_btn.setMinimumWidth(190)
        self.confirm_btn.setDefault(True)
        self.confirm_btn.clicked.connect(self._on_confirm)
        attach_button_feedback(self.confirm_btn)

        btn_row.addWidget(self.cancel_btn)
        btn_row.addWidget(self.confirm_btn)
        root.addLayout(btn_row)

        self.retranslate_ui()
        self.select_method(self._method_options[0].key)
        self._fit_compact_size()

    def _fit_compact_size(self) -> None:
        self.layout().activate()
        host_h = self._scroll_host.sizeHint().height()
        host_w = self._scroll_host.sizeHint().width()
        target_w = max(610, host_w + 48)
        self._scroll.setMinimumHeight(min(host_h + 12, 400))
        screen = QGuiApplication.screenAt(self.frameGeometry().center()) or QGuiApplication.primaryScreen()
        max_h = 680
        if screen is not None:
            max_h = max(460, int(screen.availableGeometry().height() * 0.86))
        if host_h + 220 <= max_h:
            self._scroll.setMinimumHeight(host_h + 10)
        desired_h = self.layout().sizeHint().height()
        final_h = min(desired_h + 12, max_h)
        self.resize(target_w, final_h)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if not self._faded_once:
            self._faded_once = True
            animate_window_fade_in(self, duration_ms=145)
        QTimer.singleShot(0, self._focus_active_input)

    def _focus_active_input(self) -> None:
        opt = self._current_option()
        if opt.method_type == METHOD_CASH:
            self.cash_edit.setFocus()
            self.cash_edit.selectAll()
        elif opt.method_type == METHOD_APPLE_PAY:
            self.apple_pay_ref_edit.setFocus()
        elif opt.method_type == METHOD_INSTAPAY:
            self.instapay_ref_edit.setFocus()
        elif opt.method_type == METHOD_EWALLET:
            self.ewallet_ref_edit.setFocus()
        elif opt.method_type == METHOD_CARD:
            self.card_ref_edit.setFocus()
        else:
            self.custom_ref_edit.setFocus()

    def _build_method_options(self) -> list[_MethodOption]:
        options: list[_MethodOption] = []
        enabled = set(self._pay_cfg.enabled_methods or [])
        order_defs = (
            (METHOD_CASH, 0, "banknote"),
            (METHOD_APPLE_PAY, 1, "apple_pay"),
            (METHOD_INSTAPAY, 2, "instapay"),
            (METHOD_EWALLET, 3, "wallet"),
            (METHOD_CARD, 4, "credit_card"),
        )
        for mtype, stack_idx, icon_name in order_defs:
            if mtype in enabled:
                options.append(_MethodOption(mtype, mtype, stack_idx, icon_name))
        for custom in self._pay_cfg.custom_methods:
            if custom.enabled:
                options.append(
                    _MethodOption(
                        f"CUSTOM:{custom.id}",
                        METHOD_CUSTOM,
                        5,
                        "sparkles",
                        custom_def=custom,
                    )
                )
        if not options:
            options.append(_MethodOption(METHOD_CASH, METHOD_CASH, 0, "banknote"))
        return options

    def _current_option(self) -> _MethodOption:
        for opt in self._method_options:
            if opt.key == self.selected_method_key:
                return opt
        return self._method_options[0]

    def _make_due_row(self) -> tuple[QHBoxLayout, QLabel, QLabel]:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 2)
        title = QLabel()
        title.setProperty("class", "MutedLabel")
        val = QLabel(format_minor(self._total_minor, self.config.currency))
        val.setStyleSheet("font-size: 13.5px; font-weight: 700; color: #202820;")
        row.addWidget(title)
        row.addStretch(1)
        row.addWidget(val)
        return row, title, val

    # ------------------------------------------------------------------ pages
    def _build_cash_page(self, initial_cash_text: str) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        due_row, self.cash_due_title_lbl, self.cash_due_val_lbl = self._make_due_row()
        lay.addLayout(due_row)

        self.cash_label = QLabel()
        self.cash_label.setStyleSheet("font-size: 12.5px; font-weight: 700; color: #202820;")
        lay.addWidget(self.cash_label)

        self.cash_edit = QLineEdit(initial_cash_text.strip())
        self.cash_edit.setMinimumHeight(40)
        self.cash_edit.setStyleSheet("font-size: 15px; font-weight: 700;")
        self.cash_edit.setAlignment(Qt.AlignRight if is_rtl() else Qt.AlignLeft)
        self.cash_edit.textChanged.connect(self._evaluate_state)
        self.cash_edit.returnPressed.connect(self._on_confirm_if_valid)
        lay.addWidget(self.cash_edit)

        self.cash_quick_host = QWidget()
        self.cash_quick_layout = QHBoxLayout(self.cash_quick_host)
        self.cash_quick_layout.setContentsMargins(0, 2, 0, 0)
        self.cash_quick_layout.setSpacing(8)
        self.cash_exact_btn: QPushButton | None = None
        self.quick_add_buttons: dict[int, QPushButton] = {}
        self._populate_quick_cash_buttons()
        lay.addWidget(self.cash_quick_host)

        self.cash_quick_hint_lbl = QLabel()
        self.cash_quick_hint_lbl.setProperty("class", "MutedLabel")
        self.cash_quick_hint_lbl.setWordWrap(True)
        lay.addWidget(self.cash_quick_hint_lbl)

        chg_row = QHBoxLayout()
        chg_row.setContentsMargins(0, 4, 0, 0)
        self.cash_change_title_lbl = QLabel()
        self.cash_change_title_lbl.setStyleSheet("font-size: 13px; font-weight: 700; color: #202820;")
        self.cash_change_val_lbl = QLabel(format_minor(0, self.config.currency))
        self.cash_change_val_lbl.setObjectName("ChangeValueLabel")
        self.cash_change_val_lbl.setProperty("insufficient", "false")
        chg_row.addWidget(self.cash_change_title_lbl)
        chg_row.addStretch(1)
        chg_row.addWidget(self.cash_change_val_lbl)
        lay.addLayout(chg_row)
        return page

    def _populate_quick_cash_buttons(self) -> None:
        while self.cash_quick_layout.count():
            item = self.cash_quick_layout.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        self.cash_exact_btn = None
        self.quick_add_buttons.clear()

        if self._total_minor <= 0:
            self.cash_quick_layout.addStretch(1)
            return

        exact_btn = QPushButton(tr("register.exact_btn"))
        exact_btn.setProperty("class", "QuickCashExactBtn")
        exact_btn.setMinimumHeight(32)
        exact_btn.setCursor(Qt.PointingHandCursor)
        exact_btn.clicked.connect(self._set_exact_cash)
        attach_button_feedback(exact_btn)
        self.cash_exact_btn = exact_btn
        self.cash_quick_layout.addWidget(exact_btn, 1)

        for delta_major in (10, 20, 50):
            key = f"pay.quick_add_{delta_major}"
            btn = QPushButton(tr(key))
            btn.setProperty("class", "QuickCashAddBtn")
            btn.setMinimumHeight(32)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _c=False, d=delta_major: self._increment_cash_major(d))
            attach_button_feedback(btn)
            self.quick_add_buttons[delta_major] = btn
            self.cash_quick_layout.addWidget(btn, 1)

    def _set_exact_cash(self) -> None:
        self.cash_edit.setText(format_major_input(self._total_minor, self.config.currency))
        self.cash_edit.setFocus()

    def _increment_cash_major(self, delta_major: int) -> None:
        spec = get_currency(self.config.currency)
        add_minor = int(delta_major) * spec.minor_factor
        raw = self.cash_edit.text().strip()
        base_minor = 0
        if raw:
            try:
                parsed = parse_major_to_minor(raw, self.config.currency)
                if parsed >= 0:
                    base_minor = parsed
            except ValueError:
                base_minor = 0
        self.cash_edit.setText(format_major_input(base_minor + add_minor, self.config.currency))
        self.cash_edit.setFocus()

    def _build_apple_pay_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        due_row, self.apple_due_title_lbl, self.apple_due_val_lbl = self._make_due_row()
        lay.addLayout(due_row)

        self.apple_notice_lbl = QLabel()
        self.apple_notice_lbl.setProperty("class", "MutedLabel")
        self.apple_notice_lbl.setWordWrap(True)
        lay.addWidget(self.apple_notice_lbl)

        self.apple_pay_ref_label = QLabel()
        self.apple_pay_ref_label.setStyleSheet("font-size: 12px; font-weight: 700; color: #202820;")
        self.apple_pay_ref_edit = QLineEdit()
        self.apple_pay_ref_edit.setMinimumHeight(36)
        self.apple_pay_ref_edit.textChanged.connect(self._evaluate_state)
        self.apple_pay_ref_note_lbl = QLabel()
        self.apple_pay_ref_note_lbl.setProperty("class", "MutedLabel")
        self.apple_pay_ref_note_lbl.setWordWrap(True)
        lay.addWidget(self.apple_pay_ref_label)
        lay.addWidget(self.apple_pay_ref_edit)
        lay.addWidget(self.apple_pay_ref_note_lbl)

        self.apple_pay_confirm_chk = QCheckBox()
        self.apple_pay_confirm_chk.toggled.connect(self._evaluate_state)
        lay.addWidget(self.apple_pay_confirm_chk)
        return page

    def _build_instapay_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        due_row, self.instapay_due_title_lbl, self.instapay_due_val_lbl = self._make_due_row()
        lay.addLayout(due_row)

        self.instapay_info_box = QFrame()
        self.instapay_info_box.setObjectName("InstaPayInfoBox")
        info_lay = QVBoxLayout(self.instapay_info_box)
        info_lay.setContentsMargins(11, 8, 11, 8)
        info_lay.setSpacing(2)
        self.instapay_info_title_lbl = QLabel()
        self.instapay_info_title_lbl.setStyleSheet("font-size: 11px; font-weight: 700; color: #005B37;")
        self.instapay_info_val_lbl = QLabel((self._pay_cfg.instapay_instructions or "").strip())
        self.instapay_info_val_lbl.setWordWrap(True)
        self.instapay_info_val_lbl.setStyleSheet("font-size: 12.5px; font-weight: 700; color: #202820;")
        info_lay.addWidget(self.instapay_info_title_lbl)
        info_lay.addWidget(self.instapay_info_val_lbl)
        self.instapay_info_box.setVisible(bool((self._pay_cfg.instapay_instructions or "").strip()))
        lay.addWidget(self.instapay_info_box)

        self.instapay_ref_label = QLabel()
        self.instapay_ref_label.setStyleSheet("font-size: 12px; font-weight: 700; color: #202820;")
        self.instapay_ref_edit = QLineEdit()
        self.instapay_ref_edit.setMinimumHeight(36)
        self.instapay_ref_edit.textChanged.connect(self._evaluate_state)
        self.instapay_ref_note_lbl = QLabel()
        self.instapay_ref_note_lbl.setProperty("class", "MutedLabel")
        self.instapay_ref_note_lbl.setWordWrap(True)
        lay.addWidget(self.instapay_ref_label)
        lay.addWidget(self.instapay_ref_edit)
        lay.addWidget(self.instapay_ref_note_lbl)

        self.instapay_confirm_chk = QCheckBox()
        self.instapay_confirm_chk.toggled.connect(self._evaluate_state)
        lay.addWidget(self.instapay_confirm_chk)
        return page

    def _build_ewallet_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        due_row, self.ewallet_due_title_lbl, self.ewallet_due_val_lbl = self._make_due_row()
        lay.addLayout(due_row)

        self.ewallet_provider_label = QLabel()
        self.ewallet_provider_label.setStyleSheet("font-size: 12px; font-weight: 700; color: #202820;")
        self.ewallet_provider_combo = QComboBox()
        self.ewallet_provider_combo.setMinimumHeight(36)
        wallets = self._pay_cfg.active_wallet_providers() or ["Vodafone Cash", "Orange Cash", "e& money", "WE Pay"]
        for prov in wallets:
            self.ewallet_provider_combo.addItem(prov, prov)
        lay.addWidget(self.ewallet_provider_label)
        lay.addWidget(self.ewallet_provider_combo)

        self.ewallet_ref_label = QLabel()
        self.ewallet_ref_label.setStyleSheet("font-size: 12px; font-weight: 700; color: #202820;")
        self.ewallet_ref_edit = QLineEdit()
        self.ewallet_ref_edit.setMinimumHeight(36)
        self.ewallet_ref_edit.textChanged.connect(self._evaluate_state)
        self.ewallet_ref_note_lbl = QLabel()
        self.ewallet_ref_note_lbl.setProperty("class", "MutedLabel")
        self.ewallet_ref_note_lbl.setWordWrap(True)
        lay.addWidget(self.ewallet_ref_label)
        lay.addWidget(self.ewallet_ref_edit)
        lay.addWidget(self.ewallet_ref_note_lbl)

        self.ewallet_confirm_chk = QCheckBox()
        self.ewallet_confirm_chk.toggled.connect(self._evaluate_state)
        lay.addWidget(self.ewallet_confirm_chk)
        return page

    def _build_card_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        due_row, self.card_due_title_lbl, self.card_due_val_lbl = self._make_due_row()
        lay.addLayout(due_row)

        self.card_notice_lbl = QLabel()
        self.card_notice_lbl.setProperty("class", "MutedLabel")
        self.card_notice_lbl.setWordWrap(True)
        lay.addWidget(self.card_notice_lbl)

        self.card_brand_label = QLabel()
        self.card_brand_label.setStyleSheet("font-size: 12px; font-weight: 700; color: #202820;")
        self.card_brand_combo = QComboBox()
        self.card_brand_combo.setMinimumHeight(36)
        brands = self._pay_cfg.card_brands or ["Visa", "Mastercard"]
        for brand in brands:
            self.card_brand_combo.addItem(brand, brand)
        lay.addWidget(self.card_brand_label)
        lay.addWidget(self.card_brand_combo)

        self.card_ref_label = QLabel()
        self.card_ref_label.setStyleSheet("font-size: 12px; font-weight: 700; color: #202820;")
        self.card_ref_edit = QLineEdit()
        self.card_ref_edit.setMinimumHeight(36)
        self.card_ref_edit.textChanged.connect(self._evaluate_state)
        self.card_ref_note_lbl = QLabel()
        self.card_ref_note_lbl.setProperty("class", "MutedLabel")
        self.card_ref_note_lbl.setWordWrap(True)
        lay.addWidget(self.card_ref_label)
        lay.addWidget(self.card_ref_edit)
        lay.addWidget(self.card_ref_note_lbl)

        self.card_confirm_chk = QCheckBox()
        self.card_confirm_chk.toggled.connect(self._evaluate_state)
        lay.addWidget(self.card_confirm_chk)
        return page

    def _build_custom_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        due_row, self.custom_due_title_lbl, self.custom_due_val_lbl = self._make_due_row()
        lay.addLayout(due_row)

        self.custom_method_badge = QLabel()
        self.custom_method_badge.setProperty("class", "PrimaryBadge")
        self.custom_method_badge.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        lay.addWidget(self.custom_method_badge)

        self.custom_ref_label = QLabel()
        self.custom_ref_label.setStyleSheet("font-size: 12px; font-weight: 700; color: #202820;")
        self.custom_ref_edit = QLineEdit()
        self.custom_ref_edit.setMinimumHeight(36)
        self.custom_ref_edit.textChanged.connect(self._on_custom_fields_edited)
        self.custom_ref_note_lbl = QLabel()
        self.custom_ref_note_lbl.setProperty("class", "MutedLabel")
        self.custom_ref_note_lbl.setWordWrap(True)
        lay.addWidget(self.custom_ref_label)
        lay.addWidget(self.custom_ref_edit)
        lay.addWidget(self.custom_ref_note_lbl)

        self.custom_confirm_chk = QCheckBox()
        self.custom_confirm_chk.toggled.connect(self._on_custom_fields_edited)
        lay.addWidget(self.custom_confirm_chk)
        return page

    def _build_split_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        # Balance Strip
        bal_card = QFrame()
        bal_card.setObjectName("CheckoutDetailCard")
        bal_lay = QHBoxLayout(bal_card)
        bal_lay.setContentsMargins(12, 8, 12, 8)
        bal_lay.setSpacing(12)

        self.split_paid_title_lbl = QLabel()
        self.split_paid_title_lbl.setProperty("class", "MutedLabel")
        self.split_paid_lbl = QLabel(format_minor(0, self.config.currency))
        self.split_paid_lbl.setStyleSheet("font-size: 13.5px; font-weight: 700; color: #005B37;")

        self.split_remaining_title_lbl = QLabel()
        self.split_remaining_title_lbl.setProperty("class", "MutedLabel")
        self.split_remaining_lbl = QLabel(format_minor(self._total_minor, self.config.currency))
        self.split_remaining_lbl.setStyleSheet("font-size: 14px; font-weight: 800; color: #202820;")

        bal_lay.addWidget(self.split_paid_title_lbl)
        bal_lay.addWidget(self.split_paid_lbl)
        bal_lay.addStretch(1)
        bal_lay.addWidget(self.split_remaining_title_lbl)
        bal_lay.addWidget(self.split_remaining_lbl)
        lay.addWidget(bal_card)

        # Add Split Leg Card
        add_card = QFrame()
        add_card.setObjectName("CheckoutDetailCard")
        add_lay = QVBoxLayout(add_card)
        add_lay.setContentsMargins(12, 10, 12, 10)
        add_lay.setSpacing(7)

        row1 = QHBoxLayout()
        row1.setSpacing(8)
        self.split_method_combo = QComboBox()
        self.split_method_combo.setMinimumHeight(34)
        self.split_method_combo.currentIndexChanged.connect(self._sync_split_inputs)

        self.split_amount_edit = QLineEdit()
        self.split_amount_edit.setMinimumHeight(34)
        self.split_amount_edit.setMaximumWidth(150)

        self.split_cash_received_edit = QLineEdit()
        self.split_cash_received_edit.setMinimumHeight(34)
        self.split_cash_received_edit.setMaximumWidth(150)

        self.split_add_btn = QPushButton()
        self.split_add_btn.setProperty("role", "secondary")
        self.split_add_btn.setMinimumHeight(34)
        self.split_add_btn.clicked.connect(self._on_add_split_leg)
        attach_button_feedback(self.split_add_btn)

        row1.addWidget(self.split_method_combo, 1)
        row1.addWidget(self.split_amount_edit)
        row1.addWidget(self.split_cash_received_edit)
        row1.addWidget(self.split_add_btn)
        add_lay.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(8)
        self.split_provider_combo = QComboBox()
        self.split_provider_combo.setMinimumHeight(32)
        self.split_ref_edit = QLineEdit()
        self.split_ref_edit.setMinimumHeight(32)
        self.split_confirm_chk = QCheckBox()
        row2.addWidget(self.split_provider_combo, 1)
        row2.addWidget(self.split_ref_edit, 1)
        row2.addWidget(self.split_confirm_chk)
        add_lay.addLayout(row2)
        lay.addWidget(add_card)

        # Added Legs Table
        self.split_legs_table = QTableWidget(0, 4)
        self.split_legs_table.verticalHeader().setVisible(False)
        self.split_legs_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.split_legs_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.split_legs_table.setMinimumHeight(110)
        sh = self.split_legs_table.horizontalHeader()
        sh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        sh.setSectionResizeMode(1, QHeaderView.Stretch)
        sh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        sh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        lay.addWidget(self.split_legs_table)
        return page

    def set_split_mode(self, enabled: bool) -> None:
        self._split_mode = bool(enabled)
        self.mode_stack.setCurrentIndex(1 if self._split_mode else 0)
        self.single_mode_btn.setProperty("selected", "false" if self._split_mode else "true")
        self.split_mode_btn.setProperty("selected", "true" if self._split_mode else "false")
        for btn in (self.single_mode_btn, self.split_mode_btn):
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        if self._split_mode:
            self._sync_split_inputs()
            self._refresh_split_summary()
        self._evaluate_state()

    def _selected_split_option(self) -> _MethodOption:
        key = str(self.split_method_combo.currentData() or self._method_options[0].key)
        for opt in self._method_options:
            if opt.key == key:
                return opt
        return self._method_options[0]

    def _split_remaining_minor(self) -> int:
        allocated = sum(int(leg["amount_minor"]) for leg in self._split_legs)
        return max(0, self._total_minor - allocated)

    def _sync_split_inputs(self) -> None:
        opt = self._selected_split_option()
        is_cash = opt.method_type == METHOD_CASH
        self.split_cash_received_edit.setVisible(is_cash)
        has_provider = opt.method_type in (METHOD_CARD, METHOD_EWALLET)
        self.split_provider_combo.setVisible(has_provider)
        if has_provider:
            self.split_provider_combo.clear()
            items = (
                (self._pay_cfg.card_brands or ["Visa", "Mastercard"])
                if opt.method_type == METHOD_CARD
                else (
                    self._pay_cfg.active_wallet_providers()
                    or ["Vodafone Cash", "Orange Cash", "e& money", "WE Pay"]
                )
            )
            for p in items:
                self.split_provider_combo.addItem(p, p)
        self.split_ref_edit.setVisible(not is_cash)
        self.split_confirm_chk.setVisible(not is_cash)
        if not self.split_amount_edit.text().strip():
            rem = self._split_remaining_minor()
            if rem > 0:
                self.split_amount_edit.setText(format_major_input(rem, self.config.currency))

    def _refresh_split_summary(self) -> None:
        allocated = sum(int(leg["amount_minor"]) for leg in self._split_legs)
        rem = max(0, self._total_minor - allocated)
        self.split_paid_lbl.setText(format_minor(allocated, self.config.currency))
        self.split_remaining_lbl.setText(format_minor(rem, self.config.currency))
        self.split_remaining_lbl.setStyleSheet(
            "font-size: 14px; font-weight: 800; color: #005B37;"
            if rem == 0
            else "font-size: 14px; font-weight: 800; color: #B23A3A;"
        )
        self.split_legs_table.setRowCount(len(self._split_legs))
        for r_idx, leg in enumerate(self._split_legs):
            m_lbl = str(leg.get("_ui_method_label") or tr_payment_method(str(leg["payment_method"])))
            details = str(leg.get("payment_provider") or leg.get("custom_method_label") or "")
            ref = str(leg.get("external_reference") or "")
            if ref:
                details = f"{details} ({ref})".strip()
            if str(leg["payment_method"]) == METHOD_CASH:
                crecv = int(leg.get("cash_received_minor", leg["amount_minor"]))
                amt = int(leg["amount_minor"])
                if crecv > amt:
                    details = tr(
                        "pay.split.cash_change_note",
                        recv=format_minor(crecv, self.config.currency),
                        chg=format_minor(crecv - amt, self.config.currency),
                    )
            amt_item = QTableWidgetItem(format_minor(int(leg["amount_minor"]), self.config.currency))
            amt_item.setTextAlignment(Qt.AlignTrailing | Qt.AlignVCenter)
            rm_btn = QPushButton(tr("common.remove"))
            rm_btn.setProperty("role", "danger")
            rm_btn.clicked.connect(lambda _c=False, idx=r_idx: self._remove_split_leg(idx))
            self.split_legs_table.setItem(r_idx, 0, QTableWidgetItem(m_lbl))
            self.split_legs_table.setItem(r_idx, 1, QTableWidgetItem(details or "-"))
            self.split_legs_table.setItem(r_idx, 2, amt_item)
            self.split_legs_table.setCellWidget(r_idx, 3, rm_btn)
            self.split_legs_table.setRowHeight(r_idx, 34)
        self.split_amount_edit.setText(
            format_major_input(rem, self.config.currency) if rem > 0 else ""
        )

    def _remove_split_leg(self, idx: int) -> None:
        if 0 <= idx < len(self._split_legs):
            self._split_legs.pop(idx)
            self._refresh_split_summary()
            self._evaluate_state()

    def _on_add_split_leg(self) -> None:
        rem = self._split_remaining_minor()
        if rem <= 0:
            self._set_validation(tr("pay.split.err.already_balanced"))
            return
        raw_amt = self.split_amount_edit.text().strip()
        if not raw_amt:
            self._set_validation(tr("pay.split.err.amount_required"))
            return
        try:
            amt_minor = parse_major_to_minor(raw_amt, self.config.currency)
        except ValueError as exc:
            self._set_validation(str(exc))
            return
        if amt_minor <= 0:
            self._set_validation(tr("pay.split.err.amount_positive"))
            return
        if amt_minor > rem:
            self._set_validation(
                tr("pay.split.err.exceeds_remaining", rem=format_minor(rem, self.config.currency))
            )
            return

        opt = self._selected_split_option()
        lang = get_language()
        leg_label = (
            opt.custom_def.display_label(lang)
            if opt.custom_def is not None
            else tr_payment_method(opt.method_type, lang=lang)
        )
        leg: dict[str, object] = {
            "amount_minor": amt_minor,
            "payment_method": opt.method_type,
            "_ui_method_label": leg_label,
        }
        if opt.method_type == METHOD_CASH:
            raw_recv = self.split_cash_received_edit.text().strip()
            if raw_recv:
                try:
                    recv_minor = parse_major_to_minor(raw_recv, self.config.currency)
                except ValueError as exc:
                    self._set_validation(str(exc))
                    return
                if recv_minor < amt_minor:
                    self._set_validation(tr("backend.err.payment_insufficient"))
                    return
            else:
                recv_minor = amt_minor
            leg["cash_received_minor"] = recv_minor
        else:
            req = False
            if opt.method_type == METHOD_APPLE_PAY:
                req = self._pay_cfg.require_apple_pay_reference
                leg["payment_provider"] = "Apple Pay"
            elif opt.method_type == METHOD_INSTAPAY:
                req = self._pay_cfg.require_instapay_reference
                leg["payment_provider"] = "InstaPay"
            elif opt.method_type == METHOD_EWALLET:
                req = self._pay_cfg.require_ewallet_reference
                leg["payment_provider"] = str(self.split_provider_combo.currentText() or "").strip()
            elif opt.method_type == METHOD_CARD:
                req = self._pay_cfg.require_card_reference
                leg["payment_provider"] = str(self.split_provider_combo.currentText() or "").strip()
            elif opt.custom_def is not None:
                req = bool(opt.custom_def.requires_reference)
                leg["custom_method_label"] = opt.custom_def.display_label(lang)

            try:
                clean_ref = validate_external_reference(self.split_ref_edit.text(), required=req)
            except ValueError as exc:
                self._set_validation(str(exc))
                return
            if not self.split_confirm_chk.isChecked():
                self._set_validation(tr("pay.split.err.confirm_external"))
                return
            leg["external_reference"] = clean_ref
            leg["confirmed_external"] = True
            leg["reference_required"] = req

        self._split_legs.append(leg)
        self.split_ref_edit.clear()
        self.split_cash_received_edit.clear()
        self.split_confirm_chk.setChecked(False)
        self._set_validation("")
        self._refresh_split_summary()
        self._evaluate_state()

    # -------------------------------------------------------------- state/i18n
    def _on_custom_fields_edited(self) -> None:
        opt = self._current_option()
        if opt.custom_def is not None:
            self._custom_ref_cache[opt.custom_def.id] = self.custom_ref_edit.text()
            self._custom_chk_cache[opt.custom_def.id] = self.custom_confirm_chk.isChecked()
        self._evaluate_state()

    def _refresh_method_card_icons(self) -> None:
        tokens = get_tokens(self.config.accent_color)
        for opt in self._method_options:
            btn = self.method_buttons[opt.key]
            is_sel = opt.key == self.selected_method_key
            btn.setProperty("selected", "true" if is_sel else "false")
            icon_color = tokens.primary if is_sel else tokens.muted
            btn.setIcon(get_svg_icon(opt.icon_name, icon_color, 17))
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def select_method(self, method_key: str) -> bool:
        target: _MethodOption | None = None
        for opt in self._method_options:
            if opt.key == method_key:
                target = opt
                break
        if target is None:
            return False

        if self._split_mode:
            self.set_split_mode(False)

        prev = self._current_option()
        if prev.custom_def is not None:
            self._custom_ref_cache[prev.custom_def.id] = self.custom_ref_edit.text()
            self._custom_chk_cache[prev.custom_def.id] = self.custom_confirm_chk.isChecked()

        self.selected_method_key = target.key
        self._refresh_method_card_icons()

        if target.custom_def is not None:
            self._sync_custom_page_labels(target.custom_def)
            self.custom_ref_edit.blockSignals(True)
            self.custom_confirm_chk.blockSignals(True)
            self.custom_ref_edit.setText(self._custom_ref_cache.get(target.custom_def.id, ""))
            self.custom_confirm_chk.setChecked(self._custom_chk_cache.get(target.custom_def.id, False))
            self.custom_ref_edit.blockSignals(False)
            self.custom_confirm_chk.blockSignals(False)

        self.method_stack.setCurrentIndex(target.stack_index)
        self._evaluate_state()
        self._focus_active_input()
        return True

    def _sync_custom_page_labels(self, custom_def: CustomPaymentMethodDef) -> None:
        self.custom_method_badge.setText(custom_def.display_label(get_language()))
        req = custom_def.requires_reference
        self.custom_ref_label.setText(tr("pay.ref_req_label") if req else tr("pay.ref_label"))
        self.custom_ref_edit.setPlaceholderText(
            tr("pay.ref_required_ph") if req else tr("pay.ref_optional_ph")
        )

    def retranslate_ui(self) -> None:
        self.setLayoutDirection(layout_direction())
        self.setWindowTitle(tr("pay.dialog.title"))
        self.title_lbl.setText(tr("pay.dialog.title"))
        self.subtitle_lbl.setText(tr("pay.dialog.subtitle"))
        self.total_title_lbl.setText(tr("pay.dialog.order_total"))
        self.items_badge.setText(tr_count("item", self._item_count))
        ot_summary = format_order_type_summary(
            self._order_type,
            table_number=self._table_number,
            customer_name=self._customer_name,
        )
        self.order_type_badge.setText(tr("pay.dialog.order_type_badge", summary=ot_summary))
        if self._discount_minor > 0:
            self.discount_badge.setText(
                tr("pay.dialog.discount_badge", amount=format_minor(self._discount_minor, self.config.currency))
            )
        self.discount_badge.setVisible(self._discount_minor > 0)
        formatted_total = format_minor(self._total_minor, self.config.currency)
        self.total_value_lbl.setText(formatted_total)

        self.single_mode_btn.setText(_esc_amp(tr("pay.mode.single")))
        self.split_mode_btn.setText(_esc_amp(tr("pay.mode.split")))
        self.split_paid_title_lbl.setText(tr("pay.split.paid_label"))
        self.split_remaining_title_lbl.setText(tr("pay.split.remaining_label"))
        self.split_amount_edit.setPlaceholderText(tr("pay.split.amount_ph"))
        self.split_cash_received_edit.setPlaceholderText(tr("pay.split.cash_recv_ph"))
        self.split_ref_edit.setPlaceholderText(tr("pay.ref_optional_ph"))
        self.split_confirm_chk.setText(_esc_amp(tr("pay.split.confirm_chk")))
        self.split_add_btn.setText(_esc_amp(tr("pay.split.add_btn")))
        self.split_legs_table.setHorizontalHeaderLabels(
            [
                tr("pay.split.col.method"),
                tr("pay.split.col.details"),
                tr("pay.split.col.amount"),
                tr("common.actions"),
            ]
        )
        prev_split_k = self.split_method_combo.currentData()
        self.split_method_combo.blockSignals(True)
        self.split_method_combo.clear()
        lang_cur = get_language()
        for opt in self._method_options:
            lbl = (
                opt.custom_def.display_label(lang_cur)
                if opt.custom_def is not None
                else tr_payment_method(opt.method_type, lang=lang_cur)
            )
            self.split_method_combo.addItem(lbl, opt.key)
        s_idx = self.split_method_combo.findData(prev_split_k)
        self.split_method_combo.setCurrentIndex(s_idx if s_idx >= 0 else 0)
        self.split_method_combo.blockSignals(False)
        self._sync_split_inputs()
        self._refresh_split_summary()

        due_lbl_txt = tr("pay.dialog.amount_due_label")
        for t_lbl, v_lbl in (
            (self.cash_due_title_lbl, self.cash_due_val_lbl),
            (self.apple_due_title_lbl, self.apple_due_val_lbl),
            (self.instapay_due_title_lbl, self.instapay_due_val_lbl),
            (self.ewallet_due_title_lbl, self.ewallet_due_val_lbl),
            (self.card_due_title_lbl, self.card_due_val_lbl),
            (self.custom_due_title_lbl, self.custom_due_val_lbl),
        ):
            t_lbl.setText(due_lbl_txt)
            v_lbl.setText(formatted_total)

        lang = get_language()
        for opt in self._method_options:
            btn = self.method_buttons[opt.key]
            if opt.custom_def is not None:
                btn.setText(_esc_amp(opt.custom_def.display_label(lang)))
            else:
                btn.setText(_esc_amp(tr_payment_method(opt.method_type, lang=lang)))
        self._refresh_method_card_icons()

        self.cash_label.setText(tr("register.cash_label"))
        self.cash_edit.setPlaceholderText(tr("register.cash_placeholder"))
        self.cash_edit.setAlignment(Qt.AlignRight if is_rtl() else Qt.AlignLeft)
        self._populate_quick_cash_buttons()
        self.cash_quick_hint_lbl.setText(tr("pay.cash_quick_hint"))

        self.apple_notice_lbl.setText(tr("pay.apple_pay.notice"))
        self.apple_pay_ref_label.setText(
            tr("pay.ref_req_label")
            if self._pay_cfg.require_apple_pay_reference
            else tr("pay.ref_label")
        )
        self.apple_pay_ref_edit.setPlaceholderText(
            tr("pay.ref_required_ph")
            if self._pay_cfg.require_apple_pay_reference
            else tr("pay.ref_optional_ph")
        )
        self.apple_pay_ref_note_lbl.setText(tr("pay.dialog.ref_help_note"))
        self.apple_pay_confirm_chk.setText(_esc_amp(tr("pay.confirm_apple_pay_chk")))

        self.instapay_info_title_lbl.setText(tr("pay.instapay.info_title"))
        self.instapay_ref_label.setText(
            tr("pay.ref_req_label")
            if self._pay_cfg.require_instapay_reference
            else tr("pay.ref_label")
        )
        self.instapay_ref_edit.setPlaceholderText(
            tr("pay.ref_required_ph")
            if self._pay_cfg.require_instapay_reference
            else tr("pay.ref_optional_ph")
        )
        self.instapay_ref_note_lbl.setText(tr("pay.dialog.ref_help_note"))
        self.instapay_confirm_chk.setText(_esc_amp(tr("pay.confirm_instapay_chk")))

        self.ewallet_provider_label.setText(tr("pay.ewallet.provider_label"))
        self.ewallet_ref_label.setText(
            tr("pay.ref_req_label")
            if self._pay_cfg.require_ewallet_reference
            else tr("pay.ref_label")
        )
        self.ewallet_ref_edit.setPlaceholderText(
            tr("pay.ref_required_ph")
            if self._pay_cfg.require_ewallet_reference
            else tr("pay.ref_optional_ph")
        )
        self.ewallet_ref_note_lbl.setText(tr("pay.dialog.ref_help_note"))
        self.ewallet_confirm_chk.setText(_esc_amp(tr("pay.confirm_ewallet_chk")))

        self.card_notice_lbl.setText(tr("pay.dialog.no_card_entry_note"))
        self.card_brand_label.setText(tr("pay.card.network_label"))
        self.card_ref_label.setText(
            tr("pay.ref_req_label") if self._pay_cfg.require_card_reference else tr("pay.ref_label")
        )
        self.card_ref_edit.setPlaceholderText(
            tr("pay.ref_required_ph")
            if self._pay_cfg.require_card_reference
            else tr("pay.ref_optional_ph")
        )
        self.card_ref_note_lbl.setText(tr("pay.dialog.ref_help_note"))
        self.card_confirm_chk.setText(_esc_amp(tr("pay.confirm_card_chk")))

        self.custom_ref_note_lbl.setText(tr("pay.dialog.ref_help_note"))
        self.custom_confirm_chk.setText(_esc_amp(tr("pay.confirm_custom_chk")))
        opt = self._current_option()
        if opt.custom_def is not None:
            self._sync_custom_page_labels(opt.custom_def)

        self.cancel_btn.setText(_esc_amp(tr("common.cancel")))
        self.confirm_btn.setText(_esc_amp(tr("pay.dialog.confirm_btn")))
        on_primary = contrast_text_hex(self.config.accent_color)
        self.confirm_btn.setIcon(get_svg_icon("check", on_primary, 14))
        self.confirm_btn.setIconSize(QSize(14, 14))
        self._evaluate_state()

    def _parse_cash(self) -> tuple[bool, int, str]:
        raw = self.cash_edit.text().strip()
        if not raw:
            return False, -1, ""
        try:
            minor = parse_major_to_minor(raw, self.config.currency)
        except ValueError as exc:
            return False, -1, str(exc)
        if minor < self._total_minor:
            return False, minor, tr("backend.err.payment_insufficient")
        return True, minor, ""

    def _set_validation(self, message: str) -> None:
        clean = (message or "").strip()
        self.validation_lbl.setText(clean)
        self.validation_lbl.setVisible(bool(clean))

    def _evaluate_state(self) -> bool:
        if self._split_mode:
            rem = self._split_remaining_minor()
            valid = bool(self._split_legs) and rem == 0
            err_msg = ""
            if self._split_legs and rem > 0:
                err_msg = tr(
                    "pay.split.remaining_hint",
                    rem=format_minor(rem, self.config.currency),
                )
            self._set_validation(err_msg)
            if not self._is_submitting and not getattr(self.confirm_btn, "_is_loading_state", False):
                self.confirm_btn.setEnabled(valid and len(self.cart_lines) > 0)
            return valid

        opt = self._current_option()
        valid = False
        err_msg = ""

        if opt.method_type == METHOD_CASH:
            ok, cash_minor, cash_err = self._parse_cash()
            if not self.cash_edit.text().strip():
                self.cash_change_title_lbl.setText(tr("register.change_due_label"))
                self.cash_change_val_lbl.setText(format_minor(0, self.config.currency))
                self.cash_change_val_lbl.setProperty("insufficient", "false")
                valid = False
                err_msg = ""
            elif cash_minor < 0:
                self.cash_change_title_lbl.setText(tr("register.payment_error_label"))
                self.cash_change_val_lbl.setText(tr("register.invalid_amount_label"))
                self.cash_change_val_lbl.setProperty("insufficient", "true")
                valid = False
                err_msg = cash_err
            elif cash_minor < self._total_minor:
                short_minor = self._total_minor - cash_minor
                self.cash_change_title_lbl.setText(tr("register.insufficient_cash_label"))
                self.cash_change_val_lbl.setText(
                    tr("register.short_by", amount=format_minor(short_minor, self.config.currency))
                )
                self.cash_change_val_lbl.setProperty("insufficient", "true")
                valid = False
                err_msg = cash_err
            else:
                change_minor = cash_minor - self._total_minor
                self.cash_change_title_lbl.setText(tr("register.change_due_label"))
                self.cash_change_val_lbl.setText(format_minor(change_minor, self.config.currency))
                self.cash_change_val_lbl.setProperty("insufficient", "false")
                valid = True
                err_msg = ""

            self.cash_change_val_lbl.style().unpolish(self.cash_change_val_lbl)
            self.cash_change_val_lbl.style().polish(self.cash_change_val_lbl)
        else:
            if opt.method_type == METHOD_APPLE_PAY:
                ref_raw = self.apple_pay_ref_edit.text()
                req = self._pay_cfg.require_apple_pay_reference
                checked = self.apple_pay_confirm_chk.isChecked()
            elif opt.method_type == METHOD_INSTAPAY:
                ref_raw = self.instapay_ref_edit.text()
                req = self._pay_cfg.require_instapay_reference
                checked = self.instapay_confirm_chk.isChecked()
            elif opt.method_type == METHOD_EWALLET:
                ref_raw = self.ewallet_ref_edit.text()
                req = self._pay_cfg.require_ewallet_reference
                checked = self.ewallet_confirm_chk.isChecked()
            elif opt.method_type == METHOD_CARD:
                ref_raw = self.card_ref_edit.text()
                req = self._pay_cfg.require_card_reference
                checked = self.card_confirm_chk.isChecked()
            else:
                ref_raw = self.custom_ref_edit.text()
                req = bool(opt.custom_def.requires_reference) if opt.custom_def else False
                checked = self.custom_confirm_chk.isChecked()

            ref_ok = True
            try:
                validate_external_reference(ref_raw, required=req)
            except ValueError as exc:
                ref_ok = False
                if (
                    bool(ref_raw.strip())
                    or checked
                    or str(exc) == tr("backend.err.payment_pan_forbidden")
                ):
                    err_msg = str(exc)

            valid = bool(ref_ok and checked)

        self._set_validation(err_msg)
        if not self._is_submitting and not getattr(self.confirm_btn, "_is_loading_state", False):
            self.confirm_btn.setEnabled(valid and len(self.cart_lines) > 0)
        return valid

    def _on_confirm_if_valid(self) -> None:
        if self._evaluate_state():
            self._on_confirm()

    def _on_confirm(self) -> None:
        if self._is_submitting:
            return
        if not self._evaluate_state():
            return

        self._is_submitting = True
        opt = self._current_option()
        on_primary = contrast_text_hex(self.config.accent_color)
        set_button_loading(
            self.confirm_btn,
            True,
            loading_text=tr("register.processing_btn"),
            spinner_color=on_primary,
        )
        order_kwargs = {
            "order_type": self._order_type,
            "table_number": self._table_number,
            "customer_name": self._customer_name,
            "customer_phone": self._customer_phone,
            "delivery_address": self._delivery_address,
            "held_order_id": self._held_order_id,
            "discount_type": self._discount_type,
            "discount_value": self._discount_value,
            "discount_label": self._discount_label,
            "discount_minor": self._discount_minor,
        }
        try:
            if self._split_mode:
                clean_legs = [
                    {k: v for k, v in leg.items() if not k.startswith("_ui_")}
                    for leg in self._split_legs
                ]
                order = self.db.create_order_with_payment(
                    self.cart_lines,
                    split_payments=clean_legs,
                    client_tx_key=self._client_tx_key,
                    **order_kwargs,
                )
            elif opt.method_type == METHOD_CASH:
                _, cash_minor, _ = self._parse_cash()
                order = self.db.create_order_with_payment(
                    self.cart_lines,
                    payment_method=METHOD_CASH,
                    cash_received_minor=cash_minor,
                    client_tx_key=self._client_tx_key,
                    **order_kwargs,
                )
            elif opt.method_type == METHOD_APPLE_PAY:
                order = self.db.create_order_with_payment(
                    self.cart_lines,
                    payment_method=METHOD_APPLE_PAY,
                    payment_provider="Apple Pay",
                    external_reference=self.apple_pay_ref_edit.text(),
                    confirmed_external=self.apple_pay_confirm_chk.isChecked(),
                    reference_required=self._pay_cfg.require_apple_pay_reference,
                    client_tx_key=self._client_tx_key,
                    **order_kwargs,
                )
            elif opt.method_type == METHOD_INSTAPAY:
                order = self.db.create_order_with_payment(
                    self.cart_lines,
                    payment_method=METHOD_INSTAPAY,
                    payment_provider="InstaPay",
                    external_reference=self.instapay_ref_edit.text(),
                    confirmed_external=self.instapay_confirm_chk.isChecked(),
                    reference_required=self._pay_cfg.require_instapay_reference,
                    client_tx_key=self._client_tx_key,
                    **order_kwargs,
                )
            elif opt.method_type == METHOD_EWALLET:
                provider = str(
                    self.ewallet_provider_combo.currentData()
                    or self.ewallet_provider_combo.currentText()
                    or ""
                ).strip()
                order = self.db.create_order_with_payment(
                    self.cart_lines,
                    payment_method=METHOD_EWALLET,
                    payment_provider=provider,
                    external_reference=self.ewallet_ref_edit.text(),
                    confirmed_external=self.ewallet_confirm_chk.isChecked(),
                    reference_required=self._pay_cfg.require_ewallet_reference,
                    client_tx_key=self._client_tx_key,
                    **order_kwargs,
                )
            elif opt.method_type == METHOD_CARD:
                provider = str(
                    self.card_brand_combo.currentData()
                    or self.card_brand_combo.currentText()
                    or ""
                ).strip()
                order = self.db.create_order_with_payment(
                    self.cart_lines,
                    payment_method=METHOD_CARD,
                    payment_provider=provider,
                    external_reference=self.card_ref_edit.text(),
                    confirmed_external=self.card_confirm_chk.isChecked(),
                    reference_required=self._pay_cfg.require_card_reference,
                    client_tx_key=self._client_tx_key,
                    **order_kwargs,
                )
            else:
                custom_def = opt.custom_def
                label = custom_def.display_label(get_language()) if custom_def else tr("pay.method.custom")
                req = bool(custom_def.requires_reference) if custom_def else False
                order = self.db.create_order_with_payment(
                    self.cart_lines,
                    payment_method=METHOD_CUSTOM,
                    custom_method_label=label,
                    external_reference=self.custom_ref_edit.text(),
                    confirmed_external=self.custom_confirm_chk.isChecked(),
                    reference_required=req,
                    client_tx_key=self._client_tx_key,
                    **order_kwargs,
                )
        except Exception as exc:
            self._is_submitting = False
            set_button_loading(self.confirm_btn, False)
            self._evaluate_state()
            self._set_validation(str(exc))
            QMessageBox.critical(
                self,
                tr("register.error.checkout_title"),
                tr("register.error.checkout_body", exc=exc),
            )
            return

        self._is_submitting = False
        set_button_loading(self.confirm_btn, False)
        self.result_order = order
        self.accept()
