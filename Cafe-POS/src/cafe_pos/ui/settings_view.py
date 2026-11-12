from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..auth import AuthorizationError
from ..backups import (
    BackupInspectionResult,
    build_backup_filename,
    create_backup_archive,
    inspect_and_validate_backup,
    restore_backup_archive,
    unique_backup_path,
)
from ..config import (
    AUTO_LOCK_CHOICES,
    BACKUP_FREQUENCIES,
    BusinessConfig,
    DEFAULT_BUSINESS_NAME,
    FONT_SCALE_PRESETS,
    IMAGE_PROVIDERS,
    THEME_PRESETS,
    get_auto_lock_label,
    get_backup_frequency_label,
    get_image_provider_label,
    get_theme_preset_label,
    read_and_validate_logo,
    validate_auto_lock_minutes,
    validate_backup_frequency,
    validate_country_code,
    validate_default_order_type_cfg,
    validate_discount_pct_cap,
    validate_font_scale_pct,
    validate_hex_color,
    validate_image_provider,
    validate_low_stock_alert_threshold,
    validate_receipt_language,
    validate_retention_count,
    validate_thermal_width_mm,
    validate_ui_density,
    validate_window_state_pref,
)
from ..db import Database
from ..icons import get_svg_icon
from ..i18n import (
    bidi_for_lang,
    get_language,
    layout_direction,
    normalize_language,
    tr,
    tr_count,
    tr_country,
    tr_order_type,
)
from ..image_search import clear_unused_cached_images, get_image_cache_stats
from ..money import CURRENCIES, format_minor, get_currency_label
from ..paths import get_backups_dir, get_database_path, get_default_receipts_dir
from ..payments import (
    COUNTRY_CODES,
    COUNTRY_DEFAULT_CURRENCY,
    CustomPaymentMethodDef,
    METHOD_APPLE_PAY,
    METHOD_CARD,
    METHOD_CASH,
    METHOD_EWALLET,
    METHOD_INSTAPAY,
    PaymentSettingsConfig,
    apply_country_preset,
    parse_payment_settings,
    serialize_payment_settings,
)
from ..receipts import (
    format_local_timestamp,
    list_available_printers,
    print_test_thermal_receipt,
)
from ..theme import contrast_text_hex, scaled_logo_pixmap
from .animations import animate_window_fade_in, attach_button_feedback, set_reduced_motion
from .widgets import LanguageSelectorWidget


def _esc_amp(text: str) -> str:
    return (text or "").replace("&", "&&")


class RestoreBackupConfirmDialog(QDialog):
    """Inspection summary & Administrator password confirmation before ZIP restore."""

    def __init__(
        self,
        archive_path: Path,
        inspection: BackupInspectionResult | None,
        config: BusinessConfig,
        require_password: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setModal(True)
        self.setMinimumWidth(460)
        self._archive_name = archive_path.name
        self._inspection = inspection
        self._require_password = require_password
        self._did_animate_in = False
        self._err_key: str | None = None
        self.result_password: str = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        card = QFrame()
        card.setProperty("class", "BoutiqueCard")
        c_layout = QVBoxLayout(card)
        c_layout.setContentsMargins(16, 14, 16, 14)
        c_layout.setSpacing(8)

        self.hdr_lbl = QLabel("")
        self.hdr_lbl.setProperty("class", "SectionHeader")
        c_layout.addWidget(self.hdr_lbl)

        self.file_lbl = QLabel("")
        self.file_lbl.setStyleSheet("font-weight: 700;")
        self.file_lbl.setWordWrap(True)
        c_layout.addWidget(self.file_lbl)

        self.details_lbl = QLabel("")
        self.details_lbl.setWordWrap(True)
        self.details_lbl.setProperty("class", "MutedLabel")
        c_layout.addWidget(self.details_lbl)

        warn_box = QFrame()
        warn_box.setProperty("class", "DangerCard")
        warn_l = QVBoxLayout(warn_box)
        warn_l.setContentsMargins(12, 9, 12, 9)
        self.warn_txt = QLabel("")
        self.warn_txt.setWordWrap(True)
        self.warn_txt.setStyleSheet("font-size: 12px; color: #8D2B2B; font-weight: 600;")
        warn_l.addWidget(self.warn_txt)
        c_layout.addWidget(warn_box)

        self.pw_lbl: QLabel | None = None
        self.pw_edit: QLineEdit | None = None
        self.show_btn: QPushButton | None = None
        if require_password:
            self.pw_lbl = QLabel("")
            self.pw_lbl.setStyleSheet("font-weight: 700; margin-top: 4px;")
            c_layout.addWidget(self.pw_lbl)

            pw_row = QHBoxLayout()
            pw_row.setSpacing(6)
            self.pw_edit = QLineEdit()
            self.pw_edit.setEchoMode(QLineEdit.Password)
            self.show_btn = QPushButton("")
            self.show_btn.setFixedWidth(68)
            self.show_btn.setStyleSheet("padding: 5px 8px; font-size: 12px;")

            def _toggle() -> None:
                assert self.pw_edit is not None and self.show_btn is not None
                hidden = self.pw_edit.echoMode() == QLineEdit.Password
                self.pw_edit.setEchoMode(QLineEdit.Normal if hidden else QLineEdit.Password)
                self.show_btn.setText(tr("common.hide") if hidden else tr("common.show"))

            self.show_btn.clicked.connect(_toggle)
            pw_row.addWidget(self.pw_edit, 1)
            pw_row.addWidget(self.show_btn)
            c_layout.addLayout(pw_row)

        root.addWidget(card)

        self.err_lbl = QLabel("")
        self.err_lbl.setProperty("class", "ValidationHint")
        self.err_lbl.setWordWrap(True)
        self.err_lbl.hide()
        root.addWidget(self.err_lbl)

        btn_row = QHBoxLayout()
        self.cancel_btn = QPushButton("")
        self.cancel_btn.clicked.connect(self.reject)

        self.restore_btn = QPushButton("")
        self.restore_btn.setProperty("role", "danger")
        self.restore_btn.setDefault(True)
        self.restore_btn.setIcon(get_svg_icon("refresh", "#B23A3A", 14))
        self.restore_btn.setIconSize(QSize(14, 14))
        self.restore_btn.clicked.connect(self._on_confirm)
        attach_button_feedback(self.cancel_btn)
        attach_button_feedback(self.restore_btn)

        btn_row.addStretch(1)
        btn_row.addWidget(self.cancel_btn)
        btn_row.addWidget(self.restore_btn)
        root.addLayout(btn_row)

        self.retranslate_ui()

    def retranslate_ui(self) -> None:
        self.setLayoutDirection(layout_direction())
        self.setWindowTitle(tr("restore_dialog.title"))
        self.hdr_lbl.setText(tr("restore_dialog.heading_ready"))
        self.file_lbl.setText(
            tr("restore_dialog.file_line", name=bidi_for_lang(self._archive_name))
        )

        if self._inspection is not None:
            when_str = (
                format_local_timestamp(self._inspection.created_at)
                if self._inspection.created_at
                else tr("restore_dialog.unknown_date")
            )
            self.details_lbl.setText(
                tr(
                    "restore_dialog.meta_summary_v5",
                    when=when_str,
                    kind=self._inspection.backup_kind,
                    ver=self._inspection.schema_version,
                    products=tr_count("product", self._inspection.product_count),
                    categories=tr_count("category", self._inspection.category_count),
                    orders=tr_count("order", self._inspection.order_count),
                    images=tr_count("image", self._inspection.image_count),
                    receipts=tr_count("receipt", self._inspection.receipt_pdf_count),
                    users=tr_count("user", self._inspection.user_count),
                )
            )
        else:
            self.details_lbl.setText(tr("restore_dialog.legacy_msg"))

        self.warn_txt.setText(tr("restore_dialog.warning_text"))
        if self.pw_lbl is not None and self.pw_edit is not None and self.show_btn is not None:
            self.pw_lbl.setText(tr("restore_dialog.pw_label_star"))
            self.pw_edit.setPlaceholderText(tr("restore_dialog.pw_ph_active"))
            is_hidden = self.pw_edit.echoMode() == QLineEdit.Password
            self.show_btn.setText(tr("common.show") if is_hidden else tr("common.hide"))

        self.cancel_btn.setText(tr("common.cancel"))
        self.restore_btn.setText(_esc_amp(tr("restore_dialog.confirm_short_btn")))
        if self._err_key:
            self.err_lbl.setText(tr(self._err_key))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._did_animate_in:
            self._did_animate_in = True
            animate_window_fade_in(self)
        if self.pw_edit is not None:
            self.pw_edit.setFocus()

    def _on_confirm(self) -> None:
        if self._require_password and self.pw_edit is not None:
            pw = self.pw_edit.text()
            if not pw:
                self._err_key = "restore_dialog.err_pw_empty"
                self.err_lbl.setText(tr(self._err_key))
                self.err_lbl.show()
                self.pw_edit.setFocus()
                return
            self.result_password = pw
        self.accept()


# (section_key, label_key, icon_name)
SETTINGS_SECTIONS: tuple[tuple[str, str, str], ...] = (
    ("general", "settings.nav.general", "globe"),
    ("appearance", "settings.nav.appearance", "sparkles"),
    ("payments", "settings.nav.payments", "card"),
    ("orders", "settings.nav.orders", "products"),
    ("inventory", "settings.nav.inventory", "archive"),
    ("staff", "settings.nav.staff", "users"),
    ("receipts", "settings.nav.receipts", "receipt"),
    ("data", "settings.nav.data", "refresh"),
)


class SettingsView(QWidget):
    """Boutique Café Admin Settings with left navigation rail, store rules, printers & backups."""

    settings_saved = Signal(object)  # BusinessConfig
    language_changed = Signal(str)
    data_restored = Signal()
    reopen_wizard_requested = Signal()
    status_message = Signal(str)

    def __init__(self, db: Database, config: BusinessConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.db = db
        self.config = config
        self._draft_accent = validate_hex_color(config.accent_color)
        self._draft_logo_blob = config.logo_blob
        self._draft_logo_ext = config.logo_ext
        self._draft_pm_customized = False
        self._draft_custom_methods: list[CustomPaymentMethodDef] = []
        self._draft_disabled_wallets: list[str] = []
        self._wallet_toggle_checks: dict[str, QCheckBox] = {}
        self._draft_currency_touched = False
        self._syncing_ui = False
        self._dirty = False
        self._current_section = 0

        self._build_ui()
        self.sync_from_config(config)
        self.retranslate_ui()

    def _wrap_scroll(self, inner: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setObjectName("SettingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(inner)
        return scroll

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 10, 16, 12)
        outer.setSpacing(10)

        # Sticky top header card
        top_card = QFrame()
        top_card.setProperty("class", "BoutiqueCard")
        top_bar = QHBoxLayout(top_card)
        top_bar.setContentsMargins(16, 11, 16, 11)
        top_bar.setSpacing(10)

        title_col = QVBoxLayout()
        title_col.setSpacing(1)
        self.page_title_lbl = QLabel("")
        self.page_title_lbl.setProperty("class", "PageHeading")
        self.page_sub_lbl = QLabel("")
        self.page_sub_lbl.setWordWrap(True)
        self.page_sub_lbl.setProperty("class", "MutedLabel")
        title_col.addWidget(self.page_title_lbl)
        title_col.addWidget(self.page_sub_lbl)

        self.unsaved_badge = QLabel("")
        self.unsaved_badge.setProperty("class", "PeachBadge")
        self.unsaved_badge.hide()

        self.discard_btn = QPushButton("")
        self.discard_btn.setProperty("role", "secondary")
        self.discard_btn.clicked.connect(self._discard_changes)
        attach_button_feedback(self.discard_btn)
        self.discard_btn.hide()

        self.wizard_btn = QPushButton("")
        self.wizard_btn.setIcon(get_svg_icon("sparkles", "#202820", 14))
        self.wizard_btn.setIconSize(QSize(14, 14))
        self.wizard_btn.clicked.connect(self.reopen_wizard_requested.emit)
        attach_button_feedback(self.wizard_btn)

        self.save_btn = QPushButton("")
        self.save_btn.setProperty("role", "primary")
        self.save_btn.setIcon(get_svg_icon("check", "#FFFFFF", 14))
        self.save_btn.setIconSize(QSize(14, 14))
        self.save_btn.clicked.connect(self._save_settings)
        attach_button_feedback(self.save_btn)

        top_bar.addLayout(title_col, 1)
        top_bar.addWidget(self.unsaved_badge)
        top_bar.addWidget(self.discard_btn)
        top_bar.addWidget(self.wizard_btn)
        top_bar.addWidget(self.save_btn)
        outer.addWidget(top_card)

        # Split Body: Left Navigation Rail (mirrored right in RTL) + Stacked Section Pages
        body_row = QHBoxLayout()
        body_row.setContentsMargins(0, 0, 0, 0)
        body_row.setSpacing(12)

        self.nav_rail = QFrame()
        self.nav_rail.setObjectName("SettingsRailFrame")
        self.nav_rail.setFixedWidth(218)
        rail_l = QVBoxLayout(self.nav_rail)
        rail_l.setContentsMargins(10, 12, 10, 12)
        rail_l.setSpacing(4)

        self.rail_heading_lbl = QLabel("")
        self.rail_heading_lbl.setProperty("class", "MutedLabel")
        self.rail_heading_lbl.setStyleSheet("font-size: 11px; font-weight: 700; padding: 2px 8px 6px 8px;")
        rail_l.addWidget(self.rail_heading_lbl)

        self.section_buttons: list[QPushButton] = []
        for idx, (_sec_key, lbl_key, icon_name) in enumerate(SETTINGS_SECTIONS):
            btn = QPushButton(_esc_amp(tr(lbl_key)))
            btn.setProperty("class", "SettingsNavBtn")
            btn.setProperty("selected", "true" if idx == 0 else "false")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setIconSize(QSize(15, 15))
            btn.clicked.connect(lambda _c=False, i=idx: self.switch_section(i))
            attach_button_feedback(btn)
            self.section_buttons.append(btn)
            rail_l.addWidget(btn)
        rail_l.addStretch(1)

        self.sections_stack = QStackedWidget()

        # ==================== Section 0: General (Identity + Language/Region/Currency) ====================
        sec0 = QWidget()
        sec0.setObjectName("SettingsHost")
        sec0_l = QVBoxLayout(sec0)
        sec0_l.setContentsMargins(2, 2, 6, 14)
        sec0_l.setSpacing(12)

        self.profile_group = QGroupBox("")
        profile_form = QFormLayout(self.profile_group)
        profile_form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        profile_form.setSpacing(11)

        self._lbl_biz_name = QLabel("")
        self.name_edit = QLineEdit()
        self.name_edit.setMaxLength(60)
        self.name_edit.textChanged.connect(self._on_name_edited)
        profile_form.addRow(self._lbl_biz_name, self.name_edit)

        self._lbl_biz_logo = QLabel("")
        logo_widget = QWidget()
        logo_row = QHBoxLayout(logo_widget)
        logo_row.setContentsMargins(0, 0, 0, 0)
        logo_row.setSpacing(10)
        self.logo_preview = QLabel()
        self.logo_preview.setFixedSize(56, 56)
        self.logo_preview.setAlignment(Qt.AlignCenter)
        self.logo_preview.setStyleSheet(
            "background-color: #F7F7E9; border: 1px solid #D8DED3; border-radius: 10px;"
        )
        self.browse_logo_btn = QPushButton("")
        self.browse_logo_btn.setIcon(get_svg_icon("upload", "#202820", 13))
        self.browse_logo_btn.setIconSize(QSize(13, 13))
        self.browse_logo_btn.clicked.connect(self._browse_logo)
        self.clear_logo_btn = QPushButton("")
        self.clear_logo_btn.clicked.connect(self._clear_logo)

        logo_col = QVBoxLayout()
        logo_col.setContentsMargins(0, 0, 0, 0)
        logo_col.setSpacing(4)
        logo_btns = QHBoxLayout()
        logo_btns.setContentsMargins(0, 0, 0, 0)
        logo_btns.addWidget(self.browse_logo_btn)
        logo_btns.addWidget(self.clear_logo_btn)
        logo_btns.addStretch(1)
        self.logo_hint_lbl = QLabel("")
        self.logo_hint_lbl.setWordWrap(True)
        self.logo_hint_lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.logo_hint_lbl.setProperty("class", "MutedLabel")
        logo_col.addLayout(logo_btns)
        logo_col.addWidget(self.logo_hint_lbl)

        logo_row.addWidget(self.logo_preview)
        logo_row.addLayout(logo_col, 1)
        profile_form.addRow(self._lbl_biz_logo, logo_widget)

        self._lbl_address = QLabel("")
        self.address_edit = QLineEdit()
        self.address_edit.setMaxLength(140)
        self.address_edit.textChanged.connect(self._mark_dirty)
        profile_form.addRow(self._lbl_address, self.address_edit)

        self._lbl_phone = QLabel("")
        self.phone_edit = QLineEdit()
        self.phone_edit.setMaxLength(40)
        self.phone_edit.textChanged.connect(self._mark_dirty)
        profile_form.addRow(self._lbl_phone, self.phone_edit)
        sec0_l.addWidget(self.profile_group)

        self.lang_group = QGroupBox("")
        lang_form = QFormLayout(self.lang_group)
        lang_form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        lang_form.setSpacing(11)

        self._lbl_app_lang = QLabel("")
        app_lang_wrap = QWidget()
        app_lang_col = QVBoxLayout(app_lang_wrap)
        app_lang_col.setContentsMargins(0, 0, 0, 0)
        app_lang_col.setSpacing(4)
        app_lang_top = QHBoxLayout()
        app_lang_top.setContentsMargins(0, 0, 0, 0)
        self.lang_selector = LanguageSelectorWidget(get_language())
        self.lang_selector.language_changed.connect(self.language_changed.emit)
        app_lang_top.addWidget(self.lang_selector)
        app_lang_top.addStretch(1)
        self.lang_hint_lbl = QLabel("")
        self.lang_hint_lbl.setWordWrap(True)
        self.lang_hint_lbl.setProperty("class", "MutedLabel")
        app_lang_col.addLayout(app_lang_top)
        app_lang_col.addWidget(self.lang_hint_lbl)
        lang_form.addRow(self._lbl_app_lang, app_lang_wrap)

        self._lbl_country = QLabel("")
        country_wrap = QWidget()
        country_col = QVBoxLayout(country_wrap)
        country_col.setContentsMargins(0, 0, 0, 0)
        country_col.setSpacing(4)
        country_top = QHBoxLayout()
        country_top.setContentsMargins(0, 0, 0, 0)
        country_top.setSpacing(10)
        self.country_combo = QComboBox()
        self.country_combo.setMinimumWidth(230)
        for code in COUNTRY_CODES:
            self.country_combo.addItem(tr_country(code), code)
        self.country_combo.currentIndexChanged.connect(self._on_country_combo_changed)
        self.apply_preset_btn = QPushButton("")
        self.apply_preset_btn.setProperty("role", "secondary")
        self.apply_preset_btn.clicked.connect(self._apply_regional_preset_explicit)
        attach_button_feedback(self.apply_preset_btn)
        country_top.addWidget(self.country_combo)
        country_top.addWidget(self.apply_preset_btn)
        country_top.addStretch(1)
        self.country_hint_lbl = QLabel("")
        self.country_hint_lbl.setWordWrap(True)
        self.country_hint_lbl.setProperty("class", "MutedLabel")
        country_col.addLayout(country_top)
        country_col.addWidget(self.country_hint_lbl)

        self.egp_suggest_banner = QFrame()
        self.egp_suggest_banner.setProperty("class", "CozySubCard")
        egp_banner_row = QHBoxLayout(self.egp_suggest_banner)
        egp_banner_row.setContentsMargins(10, 8, 10, 8)
        egp_banner_row.setSpacing(10)
        self.egp_suggest_lbl = QLabel("")
        self.egp_suggest_lbl.setWordWrap(True)
        self.egp_suggest_lbl.setStyleSheet("font-size: 12px; font-weight: 600; color: #202820;")
        self.egp_suggest_btn = QPushButton("")
        self.egp_suggest_btn.setProperty("role", "primary")
        self.egp_suggest_btn.clicked.connect(self._accept_egp_country_suggestion)
        attach_button_feedback(self.egp_suggest_btn)
        egp_banner_row.addWidget(self.egp_suggest_lbl, 1)
        egp_banner_row.addWidget(self.egp_suggest_btn)
        self.egp_suggest_banner.hide()
        country_col.addWidget(self.egp_suggest_banner)
        lang_form.addRow(self._lbl_country, country_wrap)

        self._lbl_currency = QLabel("")
        curr_col_widget = QWidget()
        curr_col = QVBoxLayout(curr_col_widget)
        curr_col.setContentsMargins(0, 0, 0, 0)
        curr_col.setSpacing(4)
        curr_row = QHBoxLayout()
        curr_row.setContentsMargins(0, 0, 0, 0)
        curr_row.setSpacing(12)
        self.currency_combo = QComboBox()
        self.currency_combo.setMinimumWidth(230)
        for code, spec in CURRENCIES.items():
            self.currency_combo.addItem(spec.label, code)
        self.currency_combo.currentIndexChanged.connect(self._on_currency_combo_changed)
        self.currency_example_lbl = QLabel("")
        self.currency_example_lbl.setProperty("class", "CountBadge")
        curr_row.addWidget(self.currency_combo)
        curr_row.addWidget(self.currency_example_lbl)
        curr_row.addStretch(1)
        self.currency_suggest_lbl = QLabel("")
        self.currency_suggest_lbl.setWordWrap(True)
        self.currency_suggest_lbl.setProperty("class", "MutedLabel")
        curr_col.addLayout(curr_row)
        curr_col.addWidget(self.currency_suggest_lbl)
        lang_form.addRow(self._lbl_currency, curr_col_widget)

        sec0_l.addWidget(self.lang_group)
        sec0_l.addStretch(1)
        self.sections_stack.addWidget(self._wrap_scroll(sec0))

        # ==================== Section 1: Appearance & Defaults ====================
        sec1 = QWidget()
        sec1.setObjectName("SettingsHost")
        sec1_l = QVBoxLayout(sec1)
        sec1_l.setContentsMargins(2, 2, 6, 14)
        sec1_l.setSpacing(12)

        self.appear_group = QGroupBox("")
        appear_layout = QVBoxLayout(self.appear_group)
        appear_layout.setSpacing(12)

        color_row_wrap = QHBoxLayout()
        color_row_wrap.setSpacing(12)
        self.color_lbl = QLabel("")
        self.color_lbl.setMinimumWidth(130)

        self._swatch_buttons: list[tuple[str, str, QPushButton]] = []
        swatches_row = QHBoxLayout()
        swatches_row.setSpacing(7)
        for label_text, hex_code in THEME_PRESETS:
            btn = QPushButton()
            btn.setFixedSize(28, 28)
            btn.clicked.connect(lambda _c=False, hx=hex_code: self._set_accent(hx))
            self._swatch_buttons.append((hex_code, label_text, btn))
            swatches_row.addWidget(btn)

        self.custom_color_btn = QPushButton("")
        self.custom_color_btn.clicked.connect(self._pick_custom_color)
        self.color_badge = QLabel(self._draft_accent)
        self.color_badge.setMinimumWidth(78)
        self.color_badge.setAlignment(Qt.AlignCenter)

        swatches_row.addWidget(self.custom_color_btn)
        swatches_row.addWidget(self.color_badge)
        swatches_row.addStretch(1)

        color_row_wrap.addWidget(self.color_lbl)
        color_row_wrap.addLayout(swatches_row, 1)
        appear_layout.addLayout(color_row_wrap)

        preview_card = QFrame()
        preview_card.setProperty("class", "CozySubCard")
        preview_row = QHBoxLayout(preview_card)
        preview_row.setContentsMargins(14, 10, 14, 10)
        preview_row.setSpacing(12)

        self.prev_caption = QLabel("")
        self.prev_caption.setProperty("class", "MutedLabel")
        self.preview_brand_chip = QLabel(DEFAULT_BUSINESS_NAME)
        self.preview_tab_pill = QLabel("")
        self.preview_price_lbl = QLabel("")
        self.preview_cta_pill = QLabel("")

        preview_row.addWidget(self.prev_caption)
        preview_row.addWidget(self.preview_brand_chip)
        preview_row.addWidget(self.preview_tab_pill)
        preview_row.addWidget(self.preview_price_lbl)
        preview_row.addStretch(1)
        preview_row.addWidget(self.preview_cta_pill)
        appear_layout.addWidget(preview_card)

        defaults_form = QFormLayout()
        defaults_form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        defaults_form.setSpacing(10)

        self._lbl_def_scale = QLabel("")
        self.default_font_scale_combo = QComboBox()
        self.default_font_scale_combo.setMinimumWidth(220)
        for p_key, pct in FONT_SCALE_PRESETS:
            self.default_font_scale_combo.addItem("", pct)
        self.default_font_scale_combo.currentIndexChanged.connect(self._mark_dirty)
        defaults_form.addRow(self._lbl_def_scale, self.default_font_scale_combo)

        self._lbl_def_density = QLabel("")
        self.default_density_combo = QComboBox()
        self.default_density_combo.setMinimumWidth(220)
        self.default_density_combo.addItem("", "comfortable")
        self.default_density_combo.addItem("", "compact")
        self.default_density_combo.currentIndexChanged.connect(self._mark_dirty)
        defaults_form.addRow(self._lbl_def_density, self.default_density_combo)

        self._lbl_win_state = QLabel("")
        self.window_state_combo = QComboBox()
        self.window_state_combo.setMinimumWidth(220)
        self.window_state_combo.addItem("", "maximized")
        self.window_state_combo.addItem("", "normal")
        self.window_state_combo.currentIndexChanged.connect(self._mark_dirty)
        defaults_form.addRow(self._lbl_win_state, self.window_state_combo)

        self.motion_lbl = QLabel("")
        self.reduced_motion_chk = QCheckBox("")
        self.reduced_motion_chk.toggled.connect(self._mark_dirty)
        defaults_form.addRow(self.motion_lbl, self.reduced_motion_chk)

        self.appear_personal_note = QLabel("")
        self.appear_personal_note.setWordWrap(True)
        self.appear_personal_note.setProperty("class", "MutedLabel")
        defaults_form.addRow("", self.appear_personal_note)

        appear_layout.addLayout(defaults_form)
        sec1_l.addWidget(self.appear_group)
        sec1_l.addStretch(1)
        self.sections_stack.addWidget(self._wrap_scroll(sec1))

        # ==================== Section 2: Payments ====================
        sec2 = QWidget()
        sec2.setObjectName("SettingsHost")
        sec2_l = QVBoxLayout(sec2)
        sec2_l.setContentsMargins(2, 2, 6, 14)
        sec2_l.setSpacing(12)

        self.pay_group = QGroupBox("")
        pay_form = QFormLayout(self.pay_group)
        pay_form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        pay_form.setSpacing(11)

        self._lbl_pay_enabled = QLabel("")
        enabled_w = QWidget()
        enabled_row = QHBoxLayout(enabled_w)
        enabled_row.setContentsMargins(0, 0, 0, 0)
        enabled_row.setSpacing(12)
        self.chk_pay_cash = QCheckBox("")
        self.chk_pay_apple_pay = QCheckBox("")
        self.chk_pay_instapay = QCheckBox("")
        self.chk_pay_ewallet = QCheckBox("")
        self.chk_pay_card = QCheckBox("")
        for chk in (
            self.chk_pay_cash,
            self.chk_pay_apple_pay,
            self.chk_pay_instapay,
            self.chk_pay_ewallet,
            self.chk_pay_card,
        ):
            chk.toggled.connect(self._on_payment_field_edited)
            enabled_row.addWidget(chk)
        enabled_row.addStretch(1)
        pay_form.addRow(self._lbl_pay_enabled, enabled_w)

        self._lbl_instapay_instr = QLabel("")
        self.instapay_instr_edit = QLineEdit()
        self.instapay_instr_edit.setMaxLength(180)
        self.instapay_instr_edit.textEdited.connect(self._on_payment_field_edited)
        pay_form.addRow(self._lbl_instapay_instr, self.instapay_instr_edit)

        self._lbl_card_brands = QLabel("")
        self.card_brands_edit = QLineEdit()
        self.card_brands_edit.setMaxLength(160)
        self.card_brands_edit.textEdited.connect(self._on_payment_field_edited)
        pay_form.addRow(self._lbl_card_brands, self.card_brands_edit)

        self._lbl_wallet_providers = QLabel("")
        wallet_col_w = QWidget()
        wallet_col = QVBoxLayout(wallet_col_w)
        wallet_col.setContentsMargins(0, 0, 0, 0)
        wallet_col.setSpacing(6)
        self.wallet_providers_edit = QLineEdit()
        self.wallet_providers_edit.setMaxLength(160)
        self.wallet_providers_edit.textEdited.connect(self._on_wallet_providers_text_edited)
        wallet_col.addWidget(self.wallet_providers_edit)

        self.wallet_toggles_host = QWidget()
        self.wallet_toggles_layout = QHBoxLayout(self.wallet_toggles_host)
        self.wallet_toggles_layout.setContentsMargins(0, 0, 0, 0)
        self.wallet_toggles_layout.setSpacing(12)
        wallet_col.addWidget(self.wallet_toggles_host)
        pay_form.addRow(self._lbl_wallet_providers, wallet_col_w)

        self._lbl_req_ref = QLabel("")
        req_w = QWidget()
        req_row = QHBoxLayout(req_w)
        req_row.setContentsMargins(0, 0, 0, 0)
        req_row.setSpacing(12)
        self.chk_req_apple_pay = QCheckBox("")
        self.chk_req_instapay = QCheckBox("")
        self.chk_req_ewallet = QCheckBox("")
        self.chk_req_card = QCheckBox("")
        for chk in (
            self.chk_req_apple_pay,
            self.chk_req_instapay,
            self.chk_req_ewallet,
            self.chk_req_card,
        ):
            chk.toggled.connect(self._on_payment_field_edited)
            req_row.addWidget(chk)
        req_row.addStretch(1)
        pay_form.addRow(self._lbl_req_ref, req_w)

        self._lbl_custom_pay = QLabel("")
        custom_wrap = QWidget()
        custom_wrap.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        custom_col = QVBoxLayout(custom_wrap)
        custom_col.setContentsMargins(0, 0, 0, 0)
        custom_col.setSpacing(6)
        custom_col.setSizeConstraint(QVBoxLayout.SetMinimumSize)
        custom_add_row = QHBoxLayout()
        custom_add_row.setContentsMargins(0, 0, 0, 0)
        custom_add_row.setSpacing(6)
        self.custom_en_edit = QLineEdit()
        self.custom_en_edit.setMaxLength(48)
        self.custom_en_edit.setMinimumHeight(34)
        self.custom_en_edit.setLayoutDirection(Qt.LeftToRight)
        self.custom_ar_edit = QLineEdit()
        self.custom_ar_edit.setMaxLength(48)
        self.custom_ar_edit.setMinimumHeight(34)
        self.custom_req_chk = QCheckBox("")
        self.custom_add_btn = QPushButton("")
        self.custom_add_btn.clicked.connect(self._add_custom_method)
        attach_button_feedback(self.custom_add_btn)
        custom_add_row.addWidget(self.custom_en_edit, 1)
        custom_add_row.addWidget(self.custom_ar_edit, 1)
        custom_add_row.addWidget(self.custom_req_chk)
        custom_add_row.addWidget(self.custom_add_btn)
        custom_col.addLayout(custom_add_row)

        self.custom_methods_host = QWidget()
        self.custom_methods_host.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.custom_methods_layout = QVBoxLayout(self.custom_methods_host)
        self.custom_methods_layout.setContentsMargins(0, 0, 0, 0)
        self.custom_methods_layout.setSpacing(6)
        self.custom_methods_layout.setSizeConstraint(QVBoxLayout.SetMinimumSize)
        custom_col.addWidget(self.custom_methods_host)
        self._custom_wrap = custom_wrap
        pay_form.addRow(self._lbl_custom_pay, custom_wrap)

        split_note_card = QFrame()
        split_note_card.setProperty("class", "CozySubCard")
        sn_l = QHBoxLayout(split_note_card)
        sn_l.setContentsMargins(12, 8, 12, 8)
        self.split_pay_note_lbl = QLabel("")
        self.split_pay_note_lbl.setWordWrap(True)
        self.split_pay_note_lbl.setProperty("class", "MutedLabel")
        sn_l.addWidget(self.split_pay_note_lbl)
        pay_form.addRow("", split_note_card)

        sec2_l.addWidget(self.pay_group)
        sec2_l.addStretch(1)
        self.sections_stack.addWidget(self._wrap_scroll(sec2))

        # ==================== Section 3: Products & Orders ====================
        sec3 = QWidget()
        sec3.setObjectName("SettingsHost")
        sec3_l = QVBoxLayout(sec3)
        sec3_l.setContentsMargins(2, 2, 6, 14)
        sec3_l.setSpacing(12)

        self.orders_group = QGroupBox("")
        ord_form = QFormLayout(self.orders_group)
        ord_form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        ord_form.setSpacing(11)

        self._lbl_default_otype = QLabel("")
        self.default_order_type_combo = QComboBox()
        self.default_order_type_combo.setMinimumWidth(220)
        self.default_order_type_combo.addItem("", "DINE_IN")
        self.default_order_type_combo.addItem("", "TAKEAWAY")
        self.default_order_type_combo.addItem("", "DELIVERY")
        self.default_order_type_combo.currentIndexChanged.connect(self._mark_dirty)
        ord_form.addRow(self._lbl_default_otype, self.default_order_type_combo)

        self._lbl_enabled_otypes = QLabel("")
        otypes_w = QWidget()
        otypes_row = QHBoxLayout(otypes_w)
        otypes_row.setContentsMargins(0, 0, 0, 0)
        otypes_row.setSpacing(14)
        self.chk_enable_dine_in = QCheckBox("")
        self.chk_enable_takeaway = QCheckBox("")
        self.chk_enable_delivery = QCheckBox("")
        for chk in (self.chk_enable_dine_in, self.chk_enable_takeaway, self.chk_enable_delivery):
            chk.toggled.connect(self._mark_dirty)
            otypes_row.addWidget(chk)
        otypes_row.addStretch(1)
        ord_form.addRow(self._lbl_enabled_otypes, otypes_w)

        self._lbl_modifiers = QLabel("")
        self.chk_modifiers_enabled = QCheckBox("")
        self.chk_modifiers_enabled.toggled.connect(self._mark_dirty)
        ord_form.addRow(self._lbl_modifiers, self.chk_modifiers_enabled)

        self._lbl_cashier_disc = QLabel("")
        disc_w = QWidget()
        disc_row = QHBoxLayout(disc_w)
        disc_row.setContentsMargins(0, 0, 0, 0)
        disc_row.setSpacing(12)
        self.chk_allow_cashier_discounts = QCheckBox("")
        self.chk_allow_cashier_discounts.toggled.connect(self._on_cashier_disc_toggled)
        self._lbl_max_disc_cap = QLabel("")
        self._lbl_max_disc_cap.setProperty("class", "MutedLabel")
        self.max_cashier_discount_spin = QSpinBox()
        self.max_cashier_discount_spin.setRange(0, 100)
        self.max_cashier_discount_spin.setSuffix("%")
        self.max_cashier_discount_spin.valueChanged.connect(self._mark_dirty)
        disc_row.addWidget(self.chk_allow_cashier_discounts)
        disc_row.addWidget(self._lbl_max_disc_cap)
        disc_row.addWidget(self.max_cashier_discount_spin)
        disc_row.addStretch(1)
        ord_form.addRow(self._lbl_cashier_disc, disc_w)
        sec3_l.addWidget(self.orders_group)

        self.img_group = QGroupBox("")
        img_form = QFormLayout(self.img_group)
        img_form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        img_form.setSpacing(11)

        self._lbl_provider = QLabel("")
        self.provider_combo = QComboBox()
        for p_key, p_label in IMAGE_PROVIDERS.items():
            self.provider_combo.addItem(p_label, p_key)
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        img_form.addRow(self._lbl_provider, self.provider_combo)

        self._lbl_pexels = QLabel("")
        key_widget = QWidget()
        key_row = QHBoxLayout(key_widget)
        key_row.setContentsMargins(0, 0, 0, 0)
        key_row.setSpacing(6)
        self.pexels_key_edit = QLineEdit()
        self.pexels_key_edit.setEchoMode(QLineEdit.Password)
        self.pexels_key_edit.textChanged.connect(self._mark_dirty)
        self.toggle_key_btn = QPushButton("")
        self.toggle_key_btn.clicked.connect(self._toggle_pexels_visibility)
        key_row.addWidget(self.pexels_key_edit, 1)
        key_row.addWidget(self.toggle_key_btn)
        img_form.addRow(self._lbl_pexels, key_widget)

        self.provider_note_lbl = QLabel("")
        self.provider_note_lbl.setWordWrap(True)
        self.provider_note_lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.provider_note_lbl.setProperty("class", "MutedLabel")
        img_form.addRow("", self.provider_note_lbl)

        self._lbl_cache = QLabel("")
        cache_widget = QWidget()
        cache_row = QHBoxLayout(cache_widget)
        cache_row.setContentsMargins(0, 0, 0, 0)
        cache_row.setSpacing(10)
        self.cache_stats_lbl = QLabel("")
        self.cache_stats_lbl.setProperty("class", "MutedLabel")
        self.clear_cache_btn = QPushButton("")
        self.clear_cache_btn.clicked.connect(self._cleanup_image_cache)
        cache_row.addWidget(self.cache_stats_lbl, 1)
        cache_row.addWidget(self.clear_cache_btn)
        img_form.addRow(self._lbl_cache, cache_widget)

        self._lbl_online_trans = QLabel("")
        self.online_translation_chk = QCheckBox("")
        self.online_translation_chk.toggled.connect(self._mark_dirty)
        img_form.addRow(self._lbl_online_trans, self.online_translation_chk)
        sec3_l.addWidget(self.img_group)

        sec3_l.addStretch(1)
        self.sections_stack.addWidget(self._wrap_scroll(sec3))

        # ==================== Section 4: Inventory ====================
        sec4 = QWidget()
        sec4.setObjectName("SettingsHost")
        sec4_l = QVBoxLayout(sec4)
        sec4_l.setContentsMargins(2, 2, 6, 14)
        sec4_l.setSpacing(12)

        self.inv_group = QGroupBox("")
        inv_form = QFormLayout(self.inv_group)
        inv_form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        inv_form.setSpacing(11)

        self._lbl_inv_tracking = QLabel("")
        self.chk_inventory_tracking = QCheckBox("")
        self.chk_inventory_tracking.toggled.connect(self._on_inv_tracking_toggled)
        inv_form.addRow(self._lbl_inv_tracking, self.chk_inventory_tracking)

        self._lbl_inv_auto_deduct = QLabel("")
        self.chk_auto_deduct_inventory = QCheckBox("")
        self.chk_auto_deduct_inventory.toggled.connect(self._mark_dirty)
        inv_form.addRow(self._lbl_inv_auto_deduct, self.chk_auto_deduct_inventory)

        self._lbl_inv_low_alert = QLabel("")
        self.default_low_stock_spin = QSpinBox()
        self.default_low_stock_spin.setRange(0, 100000)
        self.default_low_stock_spin.setMinimumWidth(140)
        self.default_low_stock_spin.valueChanged.connect(self._mark_dirty)
        inv_form.addRow(self._lbl_inv_low_alert, self.default_low_stock_spin)

        self.inv_note_lbl = QLabel("")
        self.inv_note_lbl.setWordWrap(True)
        self.inv_note_lbl.setProperty("class", "MutedLabel")
        inv_form.addRow("", self.inv_note_lbl)

        sec4_l.addWidget(self.inv_group)
        sec4_l.addStretch(1)
        self.sections_stack.addWidget(self._wrap_scroll(sec4))

        # ==================== Section 5: Staff & Shifts ====================
        sec5 = QWidget()
        sec5.setObjectName("SettingsHost")
        sec5_l = QVBoxLayout(sec5)
        sec5_l.setContentsMargins(2, 2, 6, 14)
        sec5_l.setSpacing(12)

        self.sec_group = QGroupBox("")
        sec_form = QFormLayout(self.sec_group)
        sec_form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        sec_form.setSpacing(11)

        self._lbl_autolock = QLabel("")
        self.auto_lock_combo = QComboBox()
        self.auto_lock_combo.setMinimumWidth(220)
        for lbl, mins in AUTO_LOCK_CHOICES:
            self.auto_lock_combo.addItem(lbl, mins)
        self.auto_lock_combo.currentIndexChanged.connect(self._mark_dirty)
        sec_form.addRow(self._lbl_autolock, self.auto_lock_combo)

        self.lock_note_lbl = QLabel("")
        self.lock_note_lbl.setWordWrap(True)
        self.lock_note_lbl.setProperty("class", "MutedLabel")
        sec_form.addRow("", self.lock_note_lbl)

        self._lbl_req_shift = QLabel("")
        self.chk_require_shift_for_sales = QCheckBox("")
        self.chk_require_shift_for_sales.toggled.connect(self._mark_dirty)
        sec_form.addRow(self._lbl_req_shift, self.chk_require_shift_for_sales)

        self._lbl_req_open_cash = QLabel("")
        self.chk_require_opening_cash = QCheckBox("")
        self.chk_require_opening_cash.toggled.connect(self._mark_dirty)
        sec_form.addRow(self._lbl_req_open_cash, self.chk_require_opening_cash)

        self._lbl_cashier_refunds = QLabel("")
        self.chk_allow_cashier_refunds = QCheckBox("")
        self.chk_allow_cashier_refunds.toggled.connect(self._mark_dirty)
        sec_form.addRow(self._lbl_cashier_refunds, self.chk_allow_cashier_refunds)

        sec5_l.addWidget(self.sec_group)
        sec5_l.addStretch(1)
        self.sections_stack.addWidget(self._wrap_scroll(sec5))

        # ==================== Section 6: Receipts & Printers ====================
        sec6 = QWidget()
        sec6.setObjectName("SettingsHost")
        sec6_l = QVBoxLayout(sec6)
        sec6_l.setContentsMargins(2, 2, 6, 14)
        sec6_l.setSpacing(12)

        self.rcp_group = QGroupBox("")
        rcp_form = QFormLayout(self.rcp_group)
        rcp_form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        rcp_form.setSpacing(11)

        self._lbl_receipt_lang = QLabel("")
        rec_lang_wrap = QWidget()
        rec_lang_col = QVBoxLayout(rec_lang_wrap)
        rec_lang_col.setContentsMargins(0, 0, 0, 0)
        rec_lang_col.setSpacing(4)
        rec_top = QHBoxLayout()
        rec_top.setContentsMargins(0, 0, 0, 0)
        self.receipt_lang_combo = QComboBox()
        self.receipt_lang_combo.setMinimumWidth(230)
        self.receipt_lang_combo.addItem("", "follow")
        self.receipt_lang_combo.addItem("", "en")
        self.receipt_lang_combo.addItem("", "ar-EG")
        self.receipt_lang_combo.currentIndexChanged.connect(self._mark_dirty)
        rec_top.addWidget(self.receipt_lang_combo)
        rec_top.addStretch(1)
        self.receipt_lang_hint_lbl = QLabel("")
        self.receipt_lang_hint_lbl.setWordWrap(True)
        self.receipt_lang_hint_lbl.setProperty("class", "MutedLabel")
        rec_lang_col.addLayout(rec_top)
        rec_lang_col.addWidget(self.receipt_lang_hint_lbl)
        rcp_form.addRow(self._lbl_receipt_lang, rec_lang_wrap)

        self._lbl_rcp_content = QLabel("")
        rcp_chk_col = QVBoxLayout()
        rcp_chk_col.setContentsMargins(0, 0, 0, 0)
        rcp_chk_col.setSpacing(5)
        self.chk_show_cashier_receipt = QCheckBox("")
        self.chk_show_cashier_receipt.toggled.connect(self._mark_dirty)
        self.chk_show_biz_details_receipt = QCheckBox("")
        self.chk_show_biz_details_receipt.toggled.connect(self._mark_dirty)
        rcp_chk_col.addWidget(self.chk_show_cashier_receipt)
        rcp_chk_col.addWidget(self.chk_show_biz_details_receipt)
        rcp_chk_w = QWidget()
        rcp_chk_w.setLayout(rcp_chk_col)
        rcp_form.addRow(self._lbl_rcp_content, rcp_chk_w)

        self._lbl_printer = QLabel("")
        prn_w = QWidget()
        prn_row = QHBoxLayout(prn_w)
        prn_row.setContentsMargins(0, 0, 0, 0)
        prn_row.setSpacing(8)
        self.printer_combo = QComboBox()
        self.printer_combo.setMinimumWidth(240)
        self.printer_combo.currentIndexChanged.connect(self._mark_dirty)
        self.refresh_printers_btn = QPushButton("")
        self.refresh_printers_btn.setIcon(get_svg_icon("refresh", "#202820", 13))
        self.refresh_printers_btn.setIconSize(QSize(13, 13))
        self.refresh_printers_btn.clicked.connect(self._reload_printers)
        self.test_print_btn = QPushButton("")
        self.test_print_btn.setProperty("role", "secondary")
        self.test_print_btn.setIcon(get_svg_icon("printer", "#202820", 13))
        self.test_print_btn.setIconSize(QSize(13, 13))
        self.test_print_btn.clicked.connect(self._run_test_print)
        attach_button_feedback(self.refresh_printers_btn)
        attach_button_feedback(self.test_print_btn)
        prn_row.addWidget(self.printer_combo, 1)
        prn_row.addWidget(self.refresh_printers_btn)
        prn_row.addWidget(self.test_print_btn)
        rcp_form.addRow(self._lbl_printer, prn_w)

        self._lbl_paper_width = QLabel("")
        self.paper_width_combo = QComboBox()
        self.paper_width_combo.setMinimumWidth(220)
        self.paper_width_combo.addItem("", 80)
        self.paper_width_combo.addItem("", 58)
        self.paper_width_combo.currentIndexChanged.connect(self._mark_dirty)
        rcp_form.addRow(self._lbl_paper_width, self.paper_width_combo)

        self._lbl_auto_print = QLabel("")
        self.chk_auto_print_receipt = QCheckBox("")
        self.chk_auto_print_receipt.toggled.connect(self._mark_dirty)
        rcp_form.addRow(self._lbl_auto_print, self.chk_auto_print_receipt)

        self._lbl_auto_save_pdf = QLabel("")
        self.auto_save_receipt_chk = QCheckBox("")
        self.auto_save_receipt_chk.toggled.connect(self._on_auto_receipt_toggled)
        rcp_form.addRow(self._lbl_auto_save_pdf, self.auto_save_receipt_chk)

        self._lbl_receipt_dir = QLabel("")
        rcp_dir_w = QWidget()
        rcp_dir_row = QHBoxLayout(rcp_dir_w)
        rcp_dir_row.setContentsMargins(0, 0, 0, 0)
        rcp_dir_row.setSpacing(6)
        self.receipt_export_edit = QLineEdit()
        self.receipt_export_edit.setLayoutDirection(Qt.LeftToRight)
        self.receipt_export_edit.textChanged.connect(self._mark_dirty)
        self.browse_receipt_dir_btn = QPushButton("")
        self.browse_receipt_dir_btn.setIcon(get_svg_icon("folder", "#202820", 13))
        self.browse_receipt_dir_btn.setIconSize(QSize(13, 13))
        self.browse_receipt_dir_btn.clicked.connect(self._browse_receipt_dir)
        self.reset_receipt_dir_btn = QPushButton("")
        self.reset_receipt_dir_btn.clicked.connect(lambda: self.receipt_export_edit.clear())
        rcp_dir_row.addWidget(self.receipt_export_edit, 1)
        rcp_dir_row.addWidget(self.browse_receipt_dir_btn)
        rcp_dir_row.addWidget(self.reset_receipt_dir_btn)
        rcp_form.addRow(self._lbl_receipt_dir, rcp_dir_w)

        sec6_l.addWidget(self.rcp_group)
        sec6_l.addStretch(1)
        self.sections_stack.addWidget(self._wrap_scroll(sec6))

        # ==================== Section 7: Data & Backups + Danger Zone ====================
        sec7 = QWidget()
        sec7.setObjectName("SettingsHost")
        sec7_l = QVBoxLayout(sec7)
        sec7_l.setContentsMargins(2, 2, 6, 14)
        sec7_l.setSpacing(12)

        self.db_group = QGroupBox("")
        db_form = QFormLayout(self.db_group)
        db_form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        db_form.setSpacing(10)

        self._lbl_db_file = QLabel("")
        self.db_path_lbl = QLineEdit(str(get_database_path()))
        self.db_path_lbl.setReadOnly(True)
        self.db_path_lbl.setLayoutDirection(Qt.LeftToRight)
        self.db_path_lbl.setStyleSheet("color: #5E6B5F; background-color: #F7F7E9;")
        db_form.addRow(self._lbl_db_file, self.db_path_lbl)

        self._lbl_manual_archive = QLabel("")
        backup_widget = QWidget()
        backup_row = QHBoxLayout(backup_widget)
        backup_row.setContentsMargins(0, 0, 0, 0)
        backup_row.setSpacing(8)
        self.backup_now_btn = QPushButton("")
        self.backup_now_btn.setProperty("role", "primary")
        self.backup_now_btn.setIcon(
            get_svg_icon("archive", contrast_text_hex(self.config.accent_color), 14)
        )
        self.backup_now_btn.setIconSize(QSize(14, 14))
        self.backup_now_btn.clicked.connect(self._backup_zip_now)

        self.restore_btn = QPushButton("")
        self.restore_btn.setIcon(get_svg_icon("refresh", "#202820", 14))
        self.restore_btn.setIconSize(QSize(14, 14))
        self.restore_btn.clicked.connect(self._restore_backup)

        attach_button_feedback(self.backup_now_btn)
        attach_button_feedback(self.restore_btn)
        backup_row.addWidget(self.backup_now_btn)
        backup_row.addWidget(self.restore_btn)
        backup_row.addStretch(1)
        db_form.addRow(self._lbl_manual_archive, backup_widget)

        self._lbl_scheduled = QLabel("")
        self.backup_enabled_chk = QCheckBox("")
        self.backup_enabled_chk.toggled.connect(self._on_auto_backup_toggled)
        db_form.addRow(self._lbl_scheduled, self.backup_enabled_chk)

        self._lbl_sched_ret = QLabel("")
        freq_ret_w = QWidget()
        freq_ret_row = QHBoxLayout(freq_ret_w)
        freq_ret_row.setContentsMargins(0, 0, 0, 0)
        freq_ret_row.setSpacing(10)
        self.backup_freq_combo = QComboBox()
        for f_key, f_lbl in BACKUP_FREQUENCIES.items():
            self.backup_freq_combo.addItem(f_lbl, f_key)
        self.backup_freq_combo.currentIndexChanged.connect(self._mark_dirty)
        self.ret_lbl = QLabel("")
        self.ret_lbl.setProperty("class", "MutedLabel")
        self.backup_retention_spin = QSpinBox()
        self.backup_retention_spin.setRange(1, 90)
        self.backup_retention_spin.setValue(10)
        self.backup_retention_spin.valueChanged.connect(self._mark_dirty)
        freq_ret_row.addWidget(self.backup_freq_combo)
        freq_ret_row.addWidget(self.ret_lbl)
        freq_ret_row.addWidget(self.backup_retention_spin)
        freq_ret_row.addStretch(1)
        db_form.addRow(self._lbl_sched_ret, freq_ret_w)

        self._lbl_backup_dest = QLabel("")
        dest_w = QWidget()
        dest_row = QHBoxLayout(dest_w)
        dest_row.setContentsMargins(0, 0, 0, 0)
        dest_row.setSpacing(6)
        self.backup_dest_edit = QLineEdit()
        self.backup_dest_edit.textChanged.connect(self._mark_dirty)
        self.browse_backup_dir_btn = QPushButton("")
        self.browse_backup_dir_btn.setIcon(get_svg_icon("folder", "#202820", 13))
        self.browse_backup_dir_btn.setIconSize(QSize(13, 13))
        self.browse_backup_dir_btn.clicked.connect(self._browse_backup_destination)
        self.reset_backup_dir_btn = QPushButton("")
        self.reset_backup_dir_btn.clicked.connect(lambda: self.backup_dest_edit.clear())
        dest_row.addWidget(self.backup_dest_edit, 1)
        dest_row.addWidget(self.browse_backup_dir_btn)
        dest_row.addWidget(self.reset_backup_dir_btn)
        db_form.addRow(self._lbl_backup_dest, dest_w)

        self._lbl_last_auto = QLabel("")
        self.last_auto_lbl = QLabel("")
        self.last_auto_lbl.setProperty("class", "MutedLabel")
        db_form.addRow(self._lbl_last_auto, self.last_auto_lbl)

        self.backup_note_lbl = QLabel("")
        self.backup_note_lbl.setWordWrap(True)
        self.backup_note_lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.backup_note_lbl.setProperty("class", "MutedLabel")
        db_form.addRow("", self.backup_note_lbl)
        sec7_l.addWidget(self.db_group)

        danger_card = QFrame()
        danger_card.setProperty("class", "DangerCard")
        danger_row = QHBoxLayout(danger_card)
        danger_row.setContentsMargins(16, 14, 16, 14)
        danger_row.setSpacing(12)

        danger_text = QVBoxLayout()
        danger_text.setSpacing(2)
        self.danger_title_lbl = QLabel("")
        self.danger_title_lbl.setStyleSheet("font-size: 13px; font-weight: 700; color: #B23A3A;")
        self.danger_desc_lbl = QLabel("")
        self.danger_desc_lbl.setWordWrap(True)
        self.danger_desc_lbl.setProperty("class", "MutedLabel")
        danger_text.addWidget(self.danger_title_lbl)
        danger_text.addWidget(self.danger_desc_lbl)

        self.reset_btn = QPushButton("")
        self.reset_btn.setProperty("role", "danger")
        self.reset_btn.clicked.connect(self._factory_reset)
        attach_button_feedback(self.reset_btn)

        danger_row.addLayout(danger_text, 1)
        danger_row.addWidget(self.reset_btn)
        sec7_l.addWidget(danger_card)

        sec7_l.addStretch(1)
        self.sections_stack.addWidget(self._wrap_scroll(sec7))

        body_row.addWidget(self.nav_rail)
        body_row.addWidget(self.sections_stack, 1)
        outer.addLayout(body_row, 1)

    def switch_section(self, index: int) -> None:
        if not (0 <= index < len(SETTINGS_SECTIONS)):
            return
        self._current_section = index
        self.sections_stack.setCurrentIndex(index)
        self._refresh_rail_styles()

    def _refresh_rail_styles(self) -> None:
        accent = validate_hex_color(self._draft_accent)
        for idx, (_sec_key, _lbl_key, icon_name) in enumerate(SETTINGS_SECTIONS):
            btn = self.section_buttons[idx]
            selected = idx == self._current_section
            btn.setProperty("selected", "true" if selected else "false")
            icon_color = accent if selected else "#56665D"
            btn.setIcon(get_svg_icon(icon_name, icon_color, 15))
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def has_unsaved_changes(self) -> bool:
        return bool(self._dirty)

    def _set_dirty(self, dirty: bool) -> None:
        self._dirty = bool(dirty)
        if hasattr(self, "unsaved_badge"):
            self.unsaved_badge.setVisible(self._dirty)
        if hasattr(self, "discard_btn"):
            self.discard_btn.setVisible(self._dirty)

    def _mark_dirty(self, *_args) -> None:
        if not self._syncing_ui:
            self._set_dirty(True)

    def _discard_changes(self) -> None:
        self.sync_from_config(self.config)
        self.status_message.emit(tr("settings.status.discarded"))

    def prompt_unsaved_changes(self) -> str:
        """Ask user whether to Save, Discard, or Stay when leaving Settings with unsaved edits."""
        if not self._dirty:
            return "discard"
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle(tr("settings.unsaved.title"))
        box.setText(tr("settings.unsaved.body"))
        save_b = box.addButton(tr("settings.unsaved.save_btn"), QMessageBox.AcceptRole)
        disc_b = box.addButton(tr("settings.unsaved.discard_btn"), QMessageBox.DestructiveRole)
        stay_b = box.addButton(tr("settings.unsaved.stay_btn"), QMessageBox.RejectRole)
        box.setDefaultButton(save_b)
        box.exec()
        clicked = box.clickedButton()
        if clicked == save_b:
            if self._save_settings():
                return "save"
            return "cancel"
        if clicked == disc_b:
            self.sync_from_config(self.config)
            return "discard"
        return "cancel"

    def _on_name_edited(self) -> None:
        self._mark_dirty()
        self._refresh_theme_preview()

    def _on_cashier_disc_toggled(self, enabled: bool) -> None:
        self.max_cashier_discount_spin.setEnabled(bool(enabled))
        self._mark_dirty()

    def _on_inv_tracking_toggled(self, enabled: bool) -> None:
        self.chk_auto_deduct_inventory.setEnabled(bool(enabled))
        self.default_low_stock_spin.setEnabled(bool(enabled))
        self._mark_dirty()

    def _reload_printers(self, selected_name: str | None = None) -> None:
        target = (
            self.printer_combo.currentData()
            if selected_name is None
            else selected_name
        ) or ""
        prev_sync = self._syncing_ui
        self._syncing_ui = True
        try:
            self.printer_combo.clear()
            self.printer_combo.addItem(tr("settings.receipts.default_printer"), "")
            for p_name in list_available_printers():
                self.printer_combo.addItem(p_name, p_name)
            idx = self.printer_combo.findData(target)
            if idx < 0 and target:
                self.printer_combo.addItem(f"{target} ({tr('settings.receipts.offline_tag')})", target)
                idx = self.printer_combo.count() - 1
            self.printer_combo.setCurrentIndex(max(0, idx))
        finally:
            self._syncing_ui = prev_sync

    def _run_test_print(self) -> None:
        printer_name = str(self.printer_combo.currentData() or "")
        width_mm = int(self.paper_width_combo.currentData() or 80)
        draft_cfg = BusinessConfig(
            business_name=self.name_edit.text().strip() or self.config.business_name,
            currency=self.currency_combo.currentData() or self.config.currency,
            address=self.address_edit.text().strip(),
            phone=self.phone_edit.text().strip(),
            logo_blob=self._draft_logo_blob,
            logo_ext=self._draft_logo_ext,
            receipt_language=validate_receipt_language(
                str(self.receipt_lang_combo.currentData() or "follow")
            ),
            show_cashier_on_receipt=self.chk_show_cashier_receipt.isChecked(),
            show_business_details_on_receipt=self.chk_show_biz_details_receipt.isChecked(),
            printer_name=printer_name,
            printer_paper_width_mm=width_mm,
        )
        try:
            ok, msg = print_test_thermal_receipt(
                draft_cfg,
                printer_name=printer_name,
                paper_width_mm=width_mm,
            )
        except Exception as exc:
            ok, msg = False, str(exc)
        if ok:
            self.status_message.emit(tr("settings.receipts.test_print_ok", printer=msg))
        else:
            QMessageBox.warning(self, tr("settings.receipts.test_print_err_title"), msg)

    def retranslate_ui(self) -> None:
        self.page_title_lbl.setText(tr("settings.heading"))
        self.page_sub_lbl.setText(tr("settings.sub_desc"))
        self.unsaved_badge.setText(tr("settings.unsaved_badge"))
        self.discard_btn.setText(_esc_amp(tr("settings.discard_btn")))
        self.wizard_btn.setText(_esc_amp(tr("settings.wizard_btn")))
        self.save_btn.setText(_esc_amp(tr("settings.save_short_btn")))

        self.rail_heading_lbl.setText(tr("settings.rail.heading"))
        for idx, (_sec_key, lbl_key, _icon_name) in enumerate(SETTINGS_SECTIONS):
            self.section_buttons[idx].setText(_esc_amp(tr(lbl_key)))
        self._refresh_rail_styles()

        # Section 0: General
        self.profile_group.setTitle(_esc_amp(tr("settings.sec2.title")))
        self._lbl_biz_name.setText(tr("settings.business_name_label"))
        self.name_edit.setPlaceholderText(tr("settings.business_name_placeholder"))
        self._lbl_biz_logo.setText(tr("setup.logo_section_label"))
        self.browse_logo_btn.setText(_esc_amp(tr("setup.logo_browse_btn")))
        self.clear_logo_btn.setText(_esc_amp(tr("common.use_default")))
        self.logo_hint_lbl.setText(tr("settings.logo_hint_desc"))
        self._lbl_address.setText(tr("settings.address_label"))
        self.address_edit.setPlaceholderText(tr("settings.address_ph"))
        self._lbl_phone.setText(tr("setup.phone_number_label"))
        self.phone_edit.setPlaceholderText(tr("settings.phone_ph"))

        self.lang_group.setTitle(_esc_amp(tr("settings.sec1.title")))
        self._lbl_app_lang.setText(tr("settings.app_language_label"))
        self.lang_selector.retranslate_ui()
        self.lang_selector.set_language(get_language(), emit=False)
        self.lang_hint_lbl.setText(tr("settings.app_language_hint"))

        self._lbl_country.setText(tr("settings.country_label"))
        prev_country = validate_country_code(
            self.country_combo.currentData() or getattr(self.config, "business_country", "US")
        )
        self.country_combo.blockSignals(True)
        for i in range(self.country_combo.count()):
            code = str(self.country_combo.itemData(i) or "US")
            self.country_combo.setItemText(i, tr_country(code))
        co_idx = self.country_combo.findData(prev_country)
        if co_idx >= 0:
            self.country_combo.setCurrentIndex(co_idx)
        self.country_combo.blockSignals(False)
        self.apply_preset_btn.setText(_esc_amp(tr("settings.apply_preset_btn")))
        self.country_hint_lbl.setText(tr("settings.country_hint"))
        self.egp_suggest_lbl.setText(tr("settings.country_egp_suggest_banner"))
        self.egp_suggest_btn.setText(_esc_amp(tr("settings.country_egp_suggest_btn")))
        self._refresh_country_suggestion_label()

        self._lbl_currency.setText(tr("settings.currency_active_label"))
        for i in range(self.currency_combo.count()):
            code = str(self.currency_combo.itemData(i) or "USD")
            self.currency_combo.setItemText(i, get_currency_label(code))

        # Section 1: Appearance
        self.appear_group.setTitle(_esc_amp(tr("settings.sec3.title")))
        self.color_lbl.setText(tr("settings.accent_label"))
        for hex_code, label_text, btn in self._swatch_buttons:
            localized_name = get_theme_preset_label(hex_code, label_text)
            btn.setToolTip(f"{localized_name} ({hex_code})")
        self.custom_color_btn.setText(_esc_amp(tr("settings.accent_pick_btn")))
        self.prev_caption.setText(tr("settings.live_preview_lbl"))
        self.preview_tab_pill.setText(tr("settings.preview_tab"))
        self.preview_cta_pill.setText(tr("settings.preview_cta"))
        self._lbl_def_scale.setText(tr("settings.appear.default_scale_label"))
        for i, (p_key, pct) in enumerate(FONT_SCALE_PRESETS):
            if i < self.default_font_scale_combo.count():
                self.default_font_scale_combo.setItemText(
                    i, f"{tr(f'display_prefs.preset.{p_key}')} ({pct}%)"
                )
        self._lbl_def_density.setText(tr("settings.appear.default_density_label"))
        self.default_density_combo.setItemText(0, tr("display_prefs.density.comfortable"))
        self.default_density_combo.setItemText(1, tr("display_prefs.density.compact"))
        self._lbl_win_state.setText(tr("settings.appear.window_mode_label"))
        self.window_state_combo.setItemText(0, tr("settings.appear.window_maximized"))
        self.window_state_combo.setItemText(1, tr("settings.appear.window_normal"))
        self.motion_lbl.setText(tr("settings.motion_pref_label"))
        self.reduced_motion_chk.setText(_esc_amp(tr("settings.reduced_motion_short")))
        self.appear_personal_note.setText(tr("settings.appear.personal_prefs_note"))

        # Section 2: Payments
        self.pay_group.setTitle(_esc_amp(tr("settings.group.payments")))
        self._lbl_pay_enabled.setText(tr("settings.pay.enabled_label"))
        self.chk_pay_cash.setText(_esc_amp(tr("pay.method.cash")))
        self.chk_pay_apple_pay.setText(_esc_amp(tr("pay.method.apple_pay")))
        self.chk_pay_instapay.setText(_esc_amp(tr("pay.method.instapay")))
        self.chk_pay_ewallet.setText(_esc_amp(tr("pay.method.ewallet")))
        self.chk_pay_card.setText(_esc_amp(tr("pay.method.card")))
        self._lbl_instapay_instr.setText(tr("settings.pay.instapay_instr_label"))
        self.instapay_instr_edit.setPlaceholderText(tr("settings.pay.instapay_instr_ph"))
        self._lbl_card_brands.setText(tr("settings.pay.card_brands_label"))
        self.card_brands_edit.setPlaceholderText(tr("settings.pay.card_brands_ph"))
        self._lbl_wallet_providers.setText(tr("settings.pay.wallets_label"))
        self.wallet_providers_edit.setPlaceholderText(tr("settings.pay.wallets_ph"))
        self._lbl_req_ref.setText(tr("settings.pay.req_ref_label"))
        self.chk_req_apple_pay.setText(_esc_amp(tr("pay.method.apple_pay")))
        self.chk_req_instapay.setText(_esc_amp(tr("settings.pay.req_instapay_chk")))
        self.chk_req_ewallet.setText(_esc_amp(tr("settings.pay.req_ewallet_chk")))
        self.chk_req_card.setText(_esc_amp(tr("settings.pay.req_card_chk")))
        self._lbl_custom_pay.setText(tr("settings.pay.custom_label"))
        self.custom_en_edit.setPlaceholderText(tr("settings.pay.custom_en_ph"))
        self.custom_ar_edit.setPlaceholderText(tr("settings.pay.custom_ar_ph"))
        self.custom_req_chk.setText(_esc_amp(tr("settings.pay.custom_req_ref")))
        self.custom_add_btn.setText(_esc_amp(tr("settings.pay.custom_add_btn")))
        self._rebuild_custom_methods_list()
        self.split_pay_note_lbl.setText(tr("settings.pay.split_note"))

        # Section 3: Products & Orders
        self.orders_group.setTitle(_esc_amp(tr("settings.orders.group_title")))
        self._lbl_default_otype.setText(tr("settings.orders.default_type_label"))
        self.default_order_type_combo.setItemText(0, tr_order_type("DINE_IN"))
        self.default_order_type_combo.setItemText(1, tr_order_type("TAKEAWAY"))
        self.default_order_type_combo.setItemText(2, tr_order_type("DELIVERY"))
        self._lbl_enabled_otypes.setText(tr("settings.orders.enabled_types_label"))
        self.chk_enable_dine_in.setText(_esc_amp(tr_order_type("DINE_IN")))
        self.chk_enable_takeaway.setText(_esc_amp(tr_order_type("TAKEAWAY")))
        self.chk_enable_delivery.setText(_esc_amp(tr_order_type("DELIVERY")))
        self._lbl_modifiers.setText(tr("settings.orders.modifiers_label"))
        self.chk_modifiers_enabled.setText(_esc_amp(tr("settings.orders.modifiers_chk")))
        self._lbl_cashier_disc.setText(tr("settings.orders.cashier_disc_label"))
        self.chk_allow_cashier_discounts.setText(_esc_amp(tr("settings.orders.cashier_disc_chk")))
        self._lbl_max_disc_cap.setText(tr("settings.orders.max_disc_cap_label"))

        self.img_group.setTitle(_esc_amp(tr("settings.sec5.title")))
        self._lbl_provider.setText(tr("settings.provider_label"))
        for i in range(self.provider_combo.count()):
            p_key = str(self.provider_combo.itemData(i) or "openverse")
            self.provider_combo.setItemText(i, get_image_provider_label(p_key))
        self._lbl_pexels.setText(tr("settings.pexels_label"))
        self.pexels_key_edit.setPlaceholderText(tr("settings.pexels_ph"))
        is_hidden = self.pexels_key_edit.echoMode() == QLineEdit.Password
        self.toggle_key_btn.setText(tr("common.show") if is_hidden else tr("common.hide"))
        self._lbl_cache.setText(tr("settings.cache_label"))
        self.clear_cache_btn.setText(_esc_amp(tr("settings.cache_clear_btn")))
        self._lbl_online_trans.setText(tr("settings.online_translation_label"))
        self.online_translation_chk.setText(_esc_amp(tr("settings.online_translation_chk")))

        # Section 4: Inventory
        self.inv_group.setTitle(_esc_amp(tr("settings.inv.group_title")))
        self._lbl_inv_tracking.setText(tr("settings.inv.tracking_label"))
        self.chk_inventory_tracking.setText(_esc_amp(tr("settings.inv.tracking_chk")))
        self._lbl_inv_auto_deduct.setText(tr("settings.inv.auto_deduct_label"))
        self.chk_auto_deduct_inventory.setText(_esc_amp(tr("settings.inv.auto_deduct_chk")))
        self._lbl_inv_low_alert.setText(tr("settings.inv.low_stock_label"))
        self.inv_note_lbl.setText(tr("settings.inv.note"))

        # Section 5: Staff & Shifts
        self.sec_group.setTitle(_esc_amp(tr("settings.sec4.title")))
        self._lbl_autolock.setText(tr("settings.autolock_label"))
        for i in range(self.auto_lock_combo.count()):
            mins = int(self.auto_lock_combo.itemData(i) or 0)
            self.auto_lock_combo.setItemText(i, get_auto_lock_label(mins))
        self.lock_note_lbl.setText(tr("settings.autolock_note"))
        self._lbl_req_shift.setText(tr("settings.staff.req_shift_label"))
        self.chk_require_shift_for_sales.setText(_esc_amp(tr("settings.staff.req_shift_chk")))
        self._lbl_req_open_cash.setText(tr("settings.staff.req_opening_cash_label"))
        self.chk_require_opening_cash.setText(_esc_amp(tr("settings.staff.req_opening_cash_chk")))
        self._lbl_cashier_refunds.setText(tr("settings.staff.cashier_refunds_label"))
        self.chk_allow_cashier_refunds.setText(_esc_amp(tr("settings.staff.cashier_refunds_chk")))

        # Section 6: Receipts & Printers
        self.rcp_group.setTitle(_esc_amp(tr("settings.receipts.group_title")))
        self._lbl_receipt_lang.setText(tr("settings.receipt_language_label"))
        self.receipt_lang_combo.setItemText(0, tr("common.match_ui_language"))
        self.receipt_lang_combo.setItemText(1, tr("common.language_en"))
        self.receipt_lang_combo.setItemText(2, tr("common.language_ar"))
        self.receipt_lang_hint_lbl.setText(tr("settings.receipt_language_hint"))
        self._lbl_rcp_content.setText(tr("settings.receipts.content_label"))
        self.chk_show_cashier_receipt.setText(_esc_amp(tr("settings.receipts.show_cashier_chk")))
        self.chk_show_biz_details_receipt.setText(_esc_amp(tr("settings.receipts.show_biz_chk")))
        self._lbl_printer.setText(tr("settings.receipts.printer_label"))
        if self.printer_combo.count() > 0:
            self.printer_combo.setItemText(0, tr("settings.receipts.default_printer"))
        self.refresh_printers_btn.setText(_esc_amp(tr("common.refresh")))
        self.test_print_btn.setText(_esc_amp(tr("settings.receipts.test_print_btn")))
        self._lbl_paper_width.setText(tr("settings.receipts.paper_width_label"))
        self.paper_width_combo.setItemText(0, tr("settings.receipts.width_80mm"))
        self.paper_width_combo.setItemText(1, tr("settings.receipts.width_58mm"))
        self._lbl_auto_print.setText(tr("settings.receipts.auto_print_label"))
        self.chk_auto_print_receipt.setText(_esc_amp(tr("settings.receipts.auto_print_chk")))
        self._lbl_auto_save_pdf.setText(tr("settings.receipt_auto_save_label"))
        self.auto_save_receipt_chk.setText(_esc_amp(tr("settings.receipt_auto_save_chk")))
        self._lbl_receipt_dir.setText(tr("settings.receipt_export_dir_label"))
        self.receipt_export_edit.setPlaceholderText(
            tr("settings.receipt_export_ph", dir=bidi_for_lang(get_default_receipts_dir()))
        )
        self.browse_receipt_dir_btn.setText(_esc_amp(tr("settings.browse_folder_btn")))
        self.reset_receipt_dir_btn.setText(_esc_amp(tr("settings.backup_dest_default_btn")))

        # Section 7: Data & Backups + Danger Zone
        self.db_group.setTitle(_esc_amp(tr("settings.sec6.title")))
        self._lbl_db_file.setText(tr("settings.db_file_label"))
        self._lbl_manual_archive.setText(tr("settings.manual_archive_label"))
        self.backup_now_btn.setText(_esc_amp(tr("settings.backup_now_short")))
        self.restore_btn.setText(_esc_amp(tr("settings.restore_btn")))
        self._lbl_scheduled.setText(tr("settings.scheduled_label"))
        self.backup_enabled_chk.setText(_esc_amp(tr("settings.backup_enabled_short")))
        self._lbl_sched_ret.setText(tr("settings.sched_ret_label"))
        for i in range(self.backup_freq_combo.count()):
            f_key = str(self.backup_freq_combo.itemData(i) or "daily")
            self.backup_freq_combo.setItemText(i, get_backup_frequency_label(f_key))
        self.ret_lbl.setText(tr("settings.keep_latest_label"))
        self.backup_retention_spin.setSuffix(tr("settings.archives_suffix"))
        self._lbl_backup_dest.setText(tr("settings.backup_dest_short_label"))
        self.backup_dest_edit.setPlaceholderText(
            tr("settings.backup_dest_ph", dir=bidi_for_lang(get_backups_dir()))
        )
        self.browse_backup_dir_btn.setText(_esc_amp(tr("settings.browse_folder_btn")))
        self.reset_backup_dir_btn.setText(_esc_amp(tr("settings.backup_dest_default_btn")))
        self._lbl_last_auto.setText(tr("settings.backup_last_run_label"))
        self.last_auto_lbl.setText(
            format_local_timestamp(self.config.last_auto_backup_at)
            if self.config.last_auto_backup_at
            else tr("common.never")
        )
        self.backup_note_lbl.setText(tr("settings.backup_note_full"))

        self.danger_title_lbl.setText(tr("settings.danger_title"))
        self.danger_desc_lbl.setText(tr("settings.danger_desc"))
        self.reset_btn.setText(_esc_amp(tr("settings.danger_btn")))

        self._on_provider_changed()
        self._refresh_theme_preview()
        self.refresh_cache_stats()

    def sync_from_config(self, cfg: BusinessConfig) -> None:
        self._syncing_ui = True
        try:
            self.config = cfg
            self.lang_selector.set_language(get_language(), emit=False)
            r_idx = self.receipt_lang_combo.findData(
                validate_receipt_language(getattr(cfg, "receipt_language", "follow"))
            )
            if r_idx >= 0:
                self.receipt_lang_combo.setCurrentIndex(r_idx)

            country = validate_country_code(getattr(cfg, "business_country", "US"))
            co_idx = self.country_combo.findData(country)
            if co_idx >= 0:
                self.country_combo.setCurrentIndex(co_idx)
            self._draft_currency_touched = False

            self.name_edit.setText(cfg.business_name)
            idx = self.currency_combo.findData(cfg.currency)
            if idx >= 0:
                self.currency_combo.setCurrentIndex(idx)
            self.address_edit.setText(cfg.address)
            self.phone_edit.setText(cfg.phone)
            self._draft_logo_blob = cfg.logo_blob
            self._draft_logo_ext = cfg.logo_ext

            pm_cfg = parse_payment_settings(
                getattr(cfg, "payment_methods_json", ""),
                country,
            )
            self._load_payment_config_into_widgets(pm_cfg)

            # Appearance defaults
            def_scale = validate_font_scale_pct(getattr(cfg, "default_font_scale_pct", 100))
            s_idx = self.default_font_scale_combo.findData(def_scale)
            if s_idx < 0:
                # Pick closest preset
                best_idx = min(
                    range(self.default_font_scale_combo.count()),
                    key=lambda i: abs(int(self.default_font_scale_combo.itemData(i) or 100) - def_scale),
                )
                s_idx = best_idx
            self.default_font_scale_combo.setCurrentIndex(s_idx)

            dens_idx = self.default_density_combo.findData(
                validate_ui_density(getattr(cfg, "default_ui_density", "comfortable"))
            )
            if dens_idx >= 0:
                self.default_density_combo.setCurrentIndex(dens_idx)
            ws_idx = self.window_state_combo.findData(
                validate_window_state_pref(getattr(cfg, "window_state_pref", "maximized"))
            )
            if ws_idx >= 0:
                self.window_state_combo.setCurrentIndex(ws_idx)
            self.reduced_motion_chk.setChecked(bool(cfg.reduced_motion))

            # Products & Orders
            ot_idx = self.default_order_type_combo.findData(
                validate_default_order_type_cfg(getattr(cfg, "default_order_type", "DINE_IN"))
            )
            if ot_idx >= 0:
                self.default_order_type_combo.setCurrentIndex(ot_idx)
            self.chk_enable_dine_in.setChecked(bool(getattr(cfg, "enable_dine_in", True)))
            self.chk_enable_takeaway.setChecked(bool(getattr(cfg, "enable_takeaway", True)))
            self.chk_enable_delivery.setChecked(bool(getattr(cfg, "enable_delivery", True)))
            self.chk_modifiers_enabled.setChecked(bool(getattr(cfg, "modifiers_enabled", True)))
            allow_disc = bool(getattr(cfg, "allow_cashier_discounts", True))
            self.chk_allow_cashier_discounts.setChecked(allow_disc)
            self.max_cashier_discount_spin.setValue(
                validate_discount_pct_cap(getattr(cfg, "max_cashier_discount_pct", 15))
            )
            self.max_cashier_discount_spin.setEnabled(allow_disc)

            p_idx = self.provider_combo.findData(validate_image_provider(cfg.image_provider))
            if p_idx >= 0:
                self.provider_combo.setCurrentIndex(p_idx)
            self.pexels_key_edit.setText(cfg.pexels_api_key)
            self._on_provider_changed()
            self.online_translation_chk.setChecked(
                bool(getattr(cfg, "online_translation_enabled", False))
            )

            # Inventory
            inv_on = bool(getattr(cfg, "inventory_tracking_enabled", True))
            self.chk_inventory_tracking.setChecked(inv_on)
            self.chk_auto_deduct_inventory.setChecked(
                bool(getattr(cfg, "auto_deduct_inventory", True))
            )
            self.chk_auto_deduct_inventory.setEnabled(inv_on)
            self.default_low_stock_spin.setValue(
                validate_low_stock_alert_threshold(getattr(cfg, "default_low_stock_alert", 10))
            )
            self.default_low_stock_spin.setEnabled(inv_on)

            # Staff & Shifts
            lock_mins = validate_auto_lock_minutes(cfg.auto_lock_minutes)
            l_idx = self.auto_lock_combo.findData(lock_mins)
            if l_idx >= 0:
                self.auto_lock_combo.setCurrentIndex(l_idx)
            self.chk_require_shift_for_sales.setChecked(
                bool(getattr(cfg, "require_shift_for_sales", False))
            )
            self.chk_require_opening_cash.setChecked(
                bool(getattr(cfg, "require_opening_cash", True))
            )
            self.chk_allow_cashier_refunds.setChecked(
                bool(getattr(cfg, "allow_cashier_refunds", False))
            )

            # Receipts & Printers
            self.chk_show_cashier_receipt.setChecked(
                bool(getattr(cfg, "show_cashier_on_receipt", True))
            )
            self.chk_show_biz_details_receipt.setChecked(
                bool(getattr(cfg, "show_business_details_on_receipt", True))
            )
            self._reload_printers(getattr(cfg, "printer_name", ""))
            pw_idx = self.paper_width_combo.findData(
                validate_thermal_width_mm(getattr(cfg, "printer_paper_width_mm", 80))
            )
            if pw_idx >= 0:
                self.paper_width_combo.setCurrentIndex(pw_idx)
            self.chk_auto_print_receipt.setChecked(
                bool(getattr(cfg, "auto_print_receipt", False))
            )
            self.auto_save_receipt_chk.setChecked(
                bool(getattr(cfg, "auto_save_receipt_pdf", False))
            )
            self.receipt_export_edit.setText(getattr(cfg, "receipt_export_dir", ""))
            self._on_auto_receipt_toggled(self.auto_save_receipt_chk.isChecked())

            # Backups
            self.backup_enabled_chk.setChecked(bool(cfg.backup_enabled))
            f_idx = self.backup_freq_combo.findData(validate_backup_frequency(cfg.backup_frequency))
            if f_idx >= 0:
                self.backup_freq_combo.setCurrentIndex(f_idx)
            self.backup_retention_spin.setValue(validate_retention_count(cfg.backup_retention_count))
            self.backup_dest_edit.setText(cfg.backup_destination)
            self.last_auto_lbl.setText(
                format_local_timestamp(cfg.last_auto_backup_at)
                if cfg.last_auto_backup_at
                else tr("common.never")
            )
            self._on_auto_backup_toggled(bool(cfg.backup_enabled))

            self._set_accent(cfg.accent_color)
            on_primary = contrast_text_hex(cfg.accent_color)
            self.save_btn.setIcon(get_svg_icon("check", on_primary, 14))
            self.backup_now_btn.setIcon(get_svg_icon("archive", on_primary, 14))
            self._refresh_logo_preview()
            self._refresh_country_suggestion_label()
            self._refresh_egp_suggestion_banner(cfg)
            self.db_path_lbl.setText(str(self.db.db_path))
            self.refresh_cache_stats()
            self._set_dirty(False)
        finally:
            self._syncing_ui = False

    def _refresh_egp_suggestion_banner(self, cfg: BusinessConfig | None = None) -> None:
        source_cfg = cfg if cfg is not None else self.config
        show_banner = (
            str(getattr(source_cfg, "currency", "") or "").upper() == "EGP"
            and not bool(getattr(source_cfg, "country_configured", False))
            and validate_country_code(getattr(source_cfg, "business_country", "US")) != "EG"
        )
        self.egp_suggest_banner.setVisible(show_banner)

    def _accept_egp_country_suggestion(self) -> None:
        eg_idx = self.country_combo.findData("EG")
        if eg_idx >= 0:
            self.country_combo.setCurrentIndex(eg_idx)
        self._apply_regional_preset_explicit()
        self.egp_suggest_banner.hide()

    def _clear_layout(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())

    def _refresh_country_suggestion_label(self) -> None:
        code = validate_country_code(self.country_combo.currentData())
        suggested_curr = COUNTRY_DEFAULT_CURRENCY.get(code, "USD")
        self.currency_suggest_lbl.setText(tr("settings.currency_suggest_note", curr=suggested_curr))

    def _load_payment_config_into_widgets(self, pm: PaymentSettingsConfig) -> None:
        prev_sync = self._syncing_ui
        self._syncing_ui = True
        try:
            self._draft_pm_customized = bool(pm.customized)
            methods = set(pm.enabled_methods or [])
            self.chk_pay_cash.setChecked(METHOD_CASH in methods)
            self.chk_pay_apple_pay.setChecked(METHOD_APPLE_PAY in methods)
            self.chk_pay_instapay.setChecked(METHOD_INSTAPAY in methods)
            self.chk_pay_ewallet.setChecked(METHOD_EWALLET in methods)
            self.chk_pay_card.setChecked(METHOD_CARD in methods)
            self.instapay_instr_edit.setText(pm.instapay_instructions or "")
            self.card_brands_edit.setText(", ".join(pm.card_brands or []))
            self.wallet_providers_edit.setText(", ".join(pm.wallet_providers or []))
            self._draft_disabled_wallets = list(pm.disabled_wallet_providers or [])
            self._rebuild_wallet_toggles(pm.wallet_providers or [])
            self.chk_req_apple_pay.setChecked(bool(pm.require_apple_pay_reference))
            self.chk_req_instapay.setChecked(bool(pm.require_instapay_reference))
            self.chk_req_ewallet.setChecked(bool(pm.require_ewallet_reference))
            self.chk_req_card.setChecked(bool(pm.require_card_reference))
            self._draft_custom_methods = list(pm.custom_methods or [])
            self._rebuild_custom_methods_list()
        finally:
            self._syncing_ui = prev_sync

    def _rebuild_wallet_toggles(self, providers: list[str] | None = None) -> None:
        self._clear_layout(self.wallet_toggles_layout)
        self._wallet_toggle_checks.clear()
        if providers is None:
            providers = [s.strip() for s in self.wallet_providers_edit.text().split(",") if s.strip()]
        disabled_keys = {w.casefold() for w in self._draft_disabled_wallets if w}
        for prov in providers:
            chk = QCheckBox(_esc_amp(prov))
            chk.setChecked(prov.casefold() not in disabled_keys)
            chk.toggled.connect(
                lambda checked, p=prov: self._on_wallet_provider_toggled(p, checked)
            )
            self.wallet_toggles_layout.addWidget(chk)
            self._wallet_toggle_checks[prov] = chk
        self.wallet_toggles_layout.addStretch(1)
        self.wallet_toggles_host.setVisible(bool(providers))

    def _on_wallet_providers_text_edited(self, *_args) -> None:
        if not self._syncing_ui:
            self._draft_pm_customized = True
            self._mark_dirty()
        self._rebuild_wallet_toggles()

    def _on_wallet_provider_toggled(self, provider: str, enabled: bool) -> None:
        if self._syncing_ui:
            return
        self._draft_pm_customized = True
        self._mark_dirty()
        key = provider.casefold()
        self._draft_disabled_wallets = [
            w for w in self._draft_disabled_wallets if w.casefold() != key
        ]
        if not enabled:
            self._draft_disabled_wallets.append(provider)

    def _build_draft_payment_config(self) -> PaymentSettingsConfig:
        country = validate_country_code(self.country_combo.currentData())
        methods: list[str] = []
        if self.chk_pay_cash.isChecked():
            methods.append(METHOD_CASH)
        if self.chk_pay_apple_pay.isChecked():
            methods.append(METHOD_APPLE_PAY)
        if self.chk_pay_instapay.isChecked():
            methods.append(METHOD_INSTAPAY)
        if self.chk_pay_ewallet.isChecked():
            methods.append(METHOD_EWALLET)
        if self.chk_pay_card.isChecked():
            methods.append(METHOD_CARD)

        brands = [s.strip() for s in self.card_brands_edit.text().split(",") if s.strip()]
        wallets = [s.strip() for s in self.wallet_providers_edit.text().split(",") if s.strip()]

        return PaymentSettingsConfig(
            country=country,
            customized=bool(self._draft_pm_customized),
            enabled_methods=methods,
            card_brands=brands,
            wallet_providers=wallets,
            disabled_wallet_providers=list(self._draft_disabled_wallets),
            custom_methods=list(self._draft_custom_methods),
            require_card_reference=self.chk_req_card.isChecked(),
            require_apple_pay_reference=self.chk_req_apple_pay.isChecked(),
            require_instapay_reference=self.chk_req_instapay.isChecked(),
            require_ewallet_reference=self.chk_req_ewallet.isChecked(),
            instapay_instructions=self.instapay_instr_edit.text().strip(),
        )

    def _rebuild_custom_methods_list(self) -> None:
        self._clear_layout(self.custom_methods_layout)
        self.custom_methods_host.setVisible(bool(self._draft_custom_methods))
        for idx, cm in enumerate(self._draft_custom_methods):
            row_frame = QFrame()
            row_frame.setProperty("class", "CozySubCard")
            row_frame.setFixedHeight(40)
            row_frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            row_l = QHBoxLayout(row_frame)
            row_l.setContentsMargins(10, 5, 10, 5)
            row_l.setSpacing(8)

            chk = QCheckBox()
            chk.setChecked(bool(cm.enabled))
            chk.toggled.connect(lambda checked, i=idx: self._toggle_custom_method_enabled(i, checked))

            lbl_parts = [cm.label_en]
            if cm.label_ar and cm.label_ar != cm.label_en:
                lbl_parts.append(cm.label_ar)
            title_lbl = QLabel(" · ".join(lbl_parts))
            title_lbl.setStyleSheet("font-weight: 600; font-size: 12px;")

            row_l.addWidget(chk)
            row_l.addWidget(title_lbl, 1)

            if cm.requires_reference:
                ref_badge = QLabel(tr("settings.pay.custom_req_ref"))
                ref_badge.setProperty("class", "PeachBadge")
                ref_badge.setMinimumHeight(24)
                row_l.addWidget(ref_badge)

            del_btn = QPushButton()
            del_btn.setFixedSize(26, 26)
            del_btn.setToolTip(tr("settings.pay.custom_remove_tip"))
            del_btn.setIcon(get_svg_icon("trash", "#B23A3A", 13))
            del_btn.setIconSize(QSize(13, 13))
            del_btn.clicked.connect(lambda _c=False, i=idx: self._remove_custom_method(i))
            row_l.addWidget(del_btn)

            self.custom_methods_layout.addWidget(row_frame)
        self.custom_methods_host.updateGeometry()
        if hasattr(self, "_custom_wrap"):
            self._custom_wrap.updateGeometry()
        self.pay_group.updateGeometry()

    def _toggle_custom_method_enabled(self, idx: int, enabled: bool) -> None:
        if 0 <= idx < len(self._draft_custom_methods):
            cur = self._draft_custom_methods[idx]
            self._draft_custom_methods[idx] = CustomPaymentMethodDef(
                id=cur.id,
                label_en=cur.label_en,
                label_ar=cur.label_ar,
                requires_reference=cur.requires_reference,
                enabled=bool(enabled),
            )
            self._draft_pm_customized = True
            self._mark_dirty()

    def _remove_custom_method(self, idx: int) -> None:
        if 0 <= idx < len(self._draft_custom_methods):
            self._draft_custom_methods.pop(idx)
            self._draft_pm_customized = True
            self._mark_dirty()
            self._rebuild_custom_methods_list()

    def _add_custom_method(self) -> None:
        en = " ".join(self.custom_en_edit.text().strip().split())[:48]
        ar = " ".join(self.custom_ar_edit.text().strip().split())[:48]
        if not en and not ar:
            QMessageBox.warning(
                self,
                tr("common.validation_error"),
                tr("backend.err.payment_custom_label_req"),
            )
            self.custom_en_edit.setFocus()
            return

        effective_en = en or ar
        used_ids = {c.id.casefold() for c in self._draft_custom_methods}
        base_id = f"custom_{len(self._draft_custom_methods) + 1}"
        cid = base_id
        suffix = 2
        while cid.casefold() in used_ids:
            cid = f"custom_{len(self._draft_custom_methods) + suffix}"
            suffix += 1

        self._draft_custom_methods.append(
            CustomPaymentMethodDef(
                id=cid,
                label_en=effective_en,
                label_ar=ar,
                requires_reference=self.custom_req_chk.isChecked(),
                enabled=True,
            )
        )
        self.custom_en_edit.clear()
        self.custom_ar_edit.clear()
        self.custom_req_chk.setChecked(False)
        self._draft_pm_customized = True
        self._mark_dirty()
        self._rebuild_custom_methods_list()

    def _on_payment_field_edited(self, *_args) -> None:
        if not self._syncing_ui:
            self._draft_pm_customized = True
            self._mark_dirty()

    def _on_currency_combo_changed(self) -> None:
        if not self._syncing_ui:
            self._draft_currency_touched = True
            self._mark_dirty()
        self._refresh_theme_preview()

    def _on_country_combo_changed(self) -> None:
        self._refresh_country_suggestion_label()
        if self._syncing_ui:
            return
        self._mark_dirty()
        code = validate_country_code(self.country_combo.currentData())
        if not self._draft_currency_touched and not self._draft_pm_customized:
            sug_curr = COUNTRY_DEFAULT_CURRENCY.get(code, "USD")
            c_idx = self.currency_combo.findData(sug_curr)
            if c_idx >= 0:
                prev_sync = self._syncing_ui
                self._syncing_ui = True
                self.currency_combo.setCurrentIndex(c_idx)
                self._syncing_ui = prev_sync
                self._refresh_theme_preview()
        if not self._draft_pm_customized:
            next_pm = apply_country_preset(self._build_draft_payment_config(), code, force=False)
            self._load_payment_config_into_widgets(next_pm)

    def _apply_regional_preset_explicit(self) -> None:
        code = validate_country_code(self.country_combo.currentData())
        next_pm = apply_country_preset(self._build_draft_payment_config(), code, force=True)
        self._load_payment_config_into_widgets(next_pm)
        sug_curr = COUNTRY_DEFAULT_CURRENCY.get(code, "USD")
        c_idx = self.currency_combo.findData(sug_curr)
        if c_idx >= 0:
            prev_sync = self._syncing_ui
            self._syncing_ui = True
            self.currency_combo.setCurrentIndex(c_idx)
            self._syncing_ui = prev_sync
            self._draft_currency_touched = False
            self._refresh_theme_preview()
        self._mark_dirty()
        self.status_message.emit(tr("settings.pay.preset_applied", country=tr_country(code)))

    def _on_auto_receipt_toggled(self, enabled: bool) -> None:
        self.receipt_export_edit.setEnabled(enabled)
        self.browse_receipt_dir_btn.setEnabled(enabled)
        self.reset_receipt_dir_btn.setEnabled(enabled)
        self._mark_dirty()

    def _browse_receipt_dir(self) -> None:
        start_dir = self.receipt_export_edit.text().strip() or str(get_default_receipts_dir())
        chosen = QFileDialog.getExistingDirectory(
            self, tr("settings.receipt_export_browse_title"), start_dir
        )
        if chosen:
            self.receipt_export_edit.setText(chosen)

    def _on_auto_backup_toggled(self, enabled: bool) -> None:
        self.backup_freq_combo.setEnabled(enabled)
        self.backup_retention_spin.setEnabled(enabled)
        self.backup_dest_edit.setEnabled(enabled)
        self.browse_backup_dir_btn.setEnabled(enabled)
        self.reset_backup_dir_btn.setEnabled(enabled)
        self._mark_dirty()

    def _browse_backup_destination(self) -> None:
        start_dir = self.backup_dest_edit.text().strip() or str(get_backups_dir())
        chosen = QFileDialog.getExistingDirectory(
            self, tr("settings.backup_dest_browse_title"), start_dir
        )
        if chosen:
            self.backup_dest_edit.setText(chosen)

    def refresh_cache_stats(self) -> None:
        refs = self.db.list_referenced_image_filenames()
        total_n, total_b, unused_n, _unused_b = get_image_cache_stats(refs)
        kb = total_b / 1024.0
        size_raw = f"{kb / 1024.0:.2f} MB" if kb >= 1024 else f"{kb:.0f} KB"
        self.cache_stats_lbl.setText(
            tr(
                "settings.cache_stats",
                photos=tr_count("photo", total_n),
                size=bidi_for_lang(size_raw),
                unused=unused_n,
            )
        )
        self.clear_cache_btn.setEnabled(unused_n > 0)

    def _cleanup_image_cache(self) -> None:
        try:
            refs = self.db.list_referenced_image_filenames()
            deleted, freed = clear_unused_cached_images(refs)
        except AuthorizationError as exc:
            QMessageBox.warning(self, tr("common.permission_denied"), str(exc))
            return
        self.refresh_cache_stats()
        kb = freed / 1024.0
        self.status_message.emit(
            tr(
                "settings.cache_cleaned",
                images=tr_count("image", deleted),
                kb=f"{kb:.0f}",
            )
        )

    def _on_provider_changed(self) -> None:
        provider = self.provider_combo.currentData() or "openverse"
        is_pexels = provider == "pexels"
        self.pexels_key_edit.setEnabled(is_pexels)
        self.toggle_key_btn.setEnabled(is_pexels)
        if is_pexels:
            self.provider_note_lbl.setText(tr("settings.provider_pexels_note"))
        else:
            self.provider_note_lbl.setText(tr("settings.provider_ov_note"))
        self._mark_dirty()

    def _toggle_pexels_visibility(self) -> None:
        is_hidden = self.pexels_key_edit.echoMode() == QLineEdit.Password
        self.pexels_key_edit.setEchoMode(QLineEdit.Normal if is_hidden else QLineEdit.Password)
        self.toggle_key_btn.setText(tr("common.hide") if is_hidden else tr("common.show"))

    def _set_accent(self, hex_code: str) -> None:
        self._draft_accent = validate_hex_color(hex_code)
        for preset_hex, _label_text, btn in self._swatch_buttons:
            selected = preset_hex.upper() == self._draft_accent.upper()
            border = "2.5px solid #202820" if selected else "1px solid #D8DED3"
            btn.setStyleSheet(
                f"background-color: {preset_hex}; border: {border}; border-radius: 8px; padding: 0;"
            )
        fg = contrast_text_hex(self._draft_accent)
        self.color_badge.setText(self._draft_accent)
        self.color_badge.setStyleSheet(
            f"background-color: {self._draft_accent}; color: {fg}; "
            "font-weight: 700; font-size: 11px; padding: 5px 10px; border-radius: 8px;"
        )
        self._refresh_theme_preview()
        if hasattr(self, "section_buttons"):
            self._refresh_rail_styles()
        self._mark_dirty()

    def _refresh_theme_preview(self) -> None:
        fg = contrast_text_hex(self._draft_accent)
        biz_name = self.name_edit.text().strip() or DEFAULT_BUSINESS_NAME
        curr = self.currency_combo.currentData() or "USD"
        sample_price = format_minor(450, curr)
        self.currency_example_lbl.setText(tr("settings.currency_sample", price=sample_price))

        self.preview_brand_chip.setText(biz_name)
        self.preview_brand_chip.setStyleSheet(
            "font-size: 12px; font-weight: 700; color: #202820; padding: 3px 6px;"
        )
        self.preview_tab_pill.setStyleSheet(
            f"background-color: {self._draft_accent}; color: {fg}; "
            "font-size: 11px; font-weight: 700; padding: 4px 11px; border-radius: 10px;"
        )
        self.preview_price_lbl.setText(tr("settings.preview_latte", price=sample_price))
        self.preview_price_lbl.setStyleSheet(
            f"color: {self._draft_accent}; font-size: 12px; font-weight: 700;"
        )
        self.preview_cta_pill.setStyleSheet(
            f"background-color: {self._draft_accent}; color: {fg}; "
            "font-size: 11px; font-weight: 700; padding: 5px 13px; border-radius: 12px;"
        )

    def _pick_custom_color(self) -> None:
        chosen = QColorDialog.getColor(
            QColor(self._draft_accent), self, tr("settings.accent_picker_title")
        )
        if chosen.isValid():
            self._set_accent(chosen.name())

    def _browse_logo(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            self,
            tr("settings.logo_dialog_title"),
            "",
            tr("settings.logo_dialog_filter"),
        )
        if not path_str:
            return
        try:
            blob, ext = read_and_validate_logo(Path(path_str))
        except ValueError as exc:
            QMessageBox.warning(self, tr("settings.logo_invalid_title"), str(exc))
            return
        self._draft_logo_blob = blob
        self._draft_logo_ext = ext
        self._refresh_logo_preview()
        self._mark_dirty()

    def _clear_logo(self) -> None:
        self._draft_logo_blob = b""
        self._draft_logo_ext = ""
        self._refresh_logo_preview()
        self._mark_dirty()

    def _refresh_logo_preview(self) -> None:
        preview_cfg = BusinessConfig(
            accent_color=self._draft_accent,
            logo_blob=self._draft_logo_blob,
            logo_ext=self._draft_logo_ext,
        )
        self.logo_preview.setPixmap(scaled_logo_pixmap(preview_cfg, 46, 46))

    def _save_settings(self) -> bool:
        name = self.name_edit.text().strip()
        if not name:
            self.switch_section(0)
            QMessageBox.warning(
                self,
                tr("common.validation_error"),
                tr("settings.error.business_required"),
            )
            self.name_edit.setFocus()
            return False

        draft_pm = self._build_draft_payment_config()
        if not draft_pm.enabled_methods and not any(c.enabled for c in draft_pm.custom_methods):
            self.switch_section(2)
            QMessageBox.warning(
                self,
                tr("common.validation_error"),
                tr("backend.err.payment_invalid_method"),
            )
            self.chk_pay_cash.setFocus()
            return False
        if METHOD_EWALLET in draft_pm.enabled_methods and draft_pm.wallet_providers:
            disabled_keys = {d.casefold() for d in draft_pm.disabled_wallet_providers}
            if all(w.casefold() in disabled_keys for w in draft_pm.wallet_providers):
                self.switch_section(2)
                QMessageBox.warning(
                    self,
                    tr("common.validation_error"),
                    tr("settings.pay.err_wallet_all_disabled"),
                )
                self.wallet_providers_edit.setFocus()
                return False

        en_dine = self.chk_enable_dine_in.isChecked()
        en_take = self.chk_enable_takeaway.isChecked()
        en_deliv = self.chk_enable_delivery.isChecked()
        if not (en_dine or en_take or en_deliv):
            self.switch_section(3)
            QMessageBox.warning(
                self,
                tr("common.validation_error"),
                tr("settings.orders.err_no_order_type"),
            )
            self.chk_enable_dine_in.setFocus()
            return False

        dest_str = self.backup_dest_edit.text().strip()
        latest_cfg = self.db.get_settings()

        updated = BusinessConfig(
            business_name=name,
            accent_color=self._draft_accent,
            currency=self.currency_combo.currentData() or "USD",
            address=self.address_edit.text().strip(),
            phone=self.phone_edit.text().strip(),
            logo_blob=self._draft_logo_blob,
            logo_ext=self._draft_logo_ext,
            setup_completed=True,
            image_provider=self.provider_combo.currentData() or "openverse",
            pexels_api_key=self.pexels_key_edit.text().strip(),
            reduced_motion=self.reduced_motion_chk.isChecked(),
            auto_lock_minutes=int(self.auto_lock_combo.currentData() or 0),
            backup_enabled=self.backup_enabled_chk.isChecked(),
            backup_frequency=str(self.backup_freq_combo.currentData() or "daily"),
            backup_destination=dest_str,
            backup_retention_count=int(self.backup_retention_spin.value()),
            last_auto_backup_at=latest_cfg.last_auto_backup_at,
            app_language=normalize_language(self.lang_selector.language()),
            receipt_language=validate_receipt_language(
                str(self.receipt_lang_combo.currentData() or "follow")
            ),
            business_country=draft_pm.country,
            payment_methods_json=serialize_payment_settings(draft_pm),
            auto_save_receipt_pdf=self.auto_save_receipt_chk.isChecked(),
            receipt_export_dir=self.receipt_export_edit.text().strip(),
            online_translation_enabled=self.online_translation_chk.isChecked(),
            country_configured=True,
            window_state_pref=validate_window_state_pref(
                str(self.window_state_combo.currentData() or "maximized")
            ),
            window_width=int(getattr(latest_cfg, "window_width", 1280) or 1280),
            window_height=int(getattr(latest_cfg, "window_height", 800) or 800),
            default_font_scale_pct=validate_font_scale_pct(
                self.default_font_scale_combo.currentData()
            ),
            default_ui_density=validate_ui_density(
                str(self.default_density_combo.currentData() or "comfortable")
            ),
            allow_cashier_discounts=self.chk_allow_cashier_discounts.isChecked(),
            allow_cashier_refunds=self.chk_allow_cashier_refunds.isChecked(),
            max_cashier_discount_pct=int(self.max_cashier_discount_spin.value()),
            default_order_type=validate_default_order_type_cfg(
                str(self.default_order_type_combo.currentData() or "DINE_IN")
            ),
            enable_dine_in=en_dine,
            enable_takeaway=en_take,
            enable_delivery=en_deliv,
            modifiers_enabled=self.chk_modifiers_enabled.isChecked(),
            inventory_tracking_enabled=self.chk_inventory_tracking.isChecked(),
            auto_deduct_inventory=self.chk_auto_deduct_inventory.isChecked(),
            default_low_stock_alert=int(self.default_low_stock_spin.value()),
            require_shift_for_sales=self.chk_require_shift_for_sales.isChecked(),
            require_opening_cash=self.chk_require_opening_cash.isChecked(),
            show_cashier_on_receipt=self.chk_show_cashier_receipt.isChecked(),
            show_business_details_on_receipt=self.chk_show_biz_details_receipt.isChecked(),
            printer_name=str(self.printer_combo.currentData() or ""),
            printer_paper_width_mm=validate_thermal_width_mm(
                self.paper_width_combo.currentData()
            ),
            auto_print_receipt=self.chk_auto_print_receipt.isChecked(),
        )
        try:
            saved = self.db.save_settings(updated)
        except AuthorizationError as exc:
            QMessageBox.warning(self, tr("common.permission_denied"), str(exc))
            return False
        self.config = saved
        self._set_dirty(False)
        self._refresh_egp_suggestion_banner(saved)
        set_reduced_motion(saved.reduced_motion)
        self.settings_saved.emit(saved)
        self.status_message.emit(tr("settings.status.saved"))
        return True

    def _backup_zip_now(self) -> None:
        default_dir = get_backups_dir()
        default_path = unique_backup_path(default_dir, build_backup_filename(kind="manual"))
        path_str, _ = QFileDialog.getSaveFileName(
            self,
            tr("settings.backup_save_dialog_title"),
            str(default_path),
            tr("settings.backup_save_dialog_filter"),
        )
        if not path_str:
            return
        try:
            written = create_backup_archive(self.db, Path(path_str), backup_kind="manual")
        except Exception as exc:
            QMessageBox.critical(
                self,
                tr("settings.backup_failed_title"),
                tr("settings.backup_failed_msg", exc=exc),
            )
            return
        self.status_message.emit(
            tr("settings.status.backup_saved", name=bidi_for_lang(written.name))
        )
        QMessageBox.information(
            self,
            tr("settings.backup_complete_title"),
            tr("settings.backup_complete_msg", path=bidi_for_lang(written)),
        )

    def _restore_backup(self) -> None:
        default_dir = str(get_backups_dir())
        path_str, _ = QFileDialog.getOpenFileName(
            self,
            tr("settings.restore_open_dialog_title"),
            default_dir,
            tr("settings.restore_open_dialog_filter"),
        )
        if not path_str:
            return
        src_path = Path(path_str)
        inspection: BackupInspectionResult | None = None
        try:
            if src_path.suffix.lower() == ".db":
                self.db.validate_backup_file(src_path)
            else:
                inspection = inspect_and_validate_backup(src_path)
        except Exception as exc:
            QMessageBox.warning(self, tr("settings.invalid_backup_title"), str(exc))
            return

        require_pw = self.db.has_any_users()
        dlg = RestoreBackupConfirmDialog(
            src_path,
            inspection,
            self.config,
            require_password=require_pw,
            parent=self,
        )
        if dlg.exec() != QDialog.Accepted:
            return

        try:
            restore_backup_archive(
                self.db,
                src_path,
                admin_password=dlg.result_password,
                verify_admin_password=require_pw,
            )
        except Exception as exc:
            QMessageBox.critical(
                self,
                tr("settings.restore_failed_title"),
                tr("settings.restore_failed_msg", exc=exc),
            )
            return

        QMessageBox.information(
            self,
            tr("settings.restore_complete_title"),
            tr("settings.restore_complete_msg"),
        )
        self.data_restored.emit()

    def _factory_reset(self) -> None:
        answer = QMessageBox.question(
            self,
            tr("settings.factory_reset.title"),
            tr("settings.factory_reset.body"),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        try:
            self.db.factory_reset()
        except AuthorizationError as exc:
            QMessageBox.warning(self, tr("common.permission_denied"), str(exc))
            return
        self.status_message.emit(tr("settings.factory_reset.status"))
        self.data_restored.emit()
