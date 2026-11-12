from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QLocale, QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QColorDialog,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..auth import (
    validate_display_name,
    validate_password_policy,
    validate_username,
)
from ..config import (
    BusinessConfig,
    DEFAULT_BUSINESS_NAME,
    THEME_PRESETS,
    read_and_validate_logo,
    validate_country_code,
    validate_hex_color,
)
from ..icons import get_svg_icon, get_svg_pixmap
from ..i18n import (
    get_language,
    layout_direction,
    normalize_language,
    qt_locale,
    set_language,
    tr,
    tr_country,
)
from ..money import CURRENCIES, format_minor
from ..payments import (
    COUNTRY_CODES,
    COUNTRY_DEFAULT_CURRENCY,
    apply_country_preset,
    parse_payment_settings,
    serialize_payment_settings,
)
from ..theme import apply_theme, contrast_text_hex, scaled_logo_pixmap
from .animations import animate_window_fade_in, attach_button_feedback
from .widgets import LanguageSelectorWidget


def _esc_amp(text: str) -> str:
    return (text or "").replace("&", "&&")


class SetupDialog(QDialog):
    """Boutique two-step onboarding wizard for store personalization & initial administrator setup."""

    language_changed = Signal(str)

    def __init__(
        self,
        initial_config: BusinessConfig,
        is_first_launch: bool = True,
        require_admin_setup: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._initial_config = initial_config
        self._is_first_launch = is_first_launch
        self._require_admin_setup = bool(require_admin_setup)
        self._did_animate_in = False
        self._selected_language = normalize_language(
            getattr(initial_config, "app_language", "") or get_language()
        )
        self._validation_key: str | None = None
        self._validation_raw: str = ""

        self.setModal(True)
        self.setMinimumSize(580, 460)
        self.resize(670, 580 if self._require_admin_setup else 510)

        self._accent_color = validate_hex_color(initial_config.accent_color)
        self._logo_blob = initial_config.logo_blob
        self._logo_ext = initial_config.logo_ext
        self._business_country = validate_country_code(getattr(initial_config, "business_country", "US"))
        self._currency_touched = False
        self.result_config: BusinessConfig | None = None
        self.result_admin_username: str = ""
        self.result_admin_display_name: str = ""
        self.result_admin_password: str = ""

        self._build_ui()
        # On existing upgraded DBs that only need an initial admin, start directly on Step 2
        start_step = 1 if (self._require_admin_setup and initial_config.setup_completed) else 0
        self.setLayoutDirection(layout_direction(self._selected_language))
        self.retranslate_ui()
        self._set_step(start_step)
        self._refresh_color_ui()
        self._refresh_logo_preview()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._did_animate_in:
            self._did_animate_in = True
            animate_window_fade_in(self)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(11)

        hero_card = QFrame()
        hero_card.setProperty("class", "BoutiqueCard")
        hero_layout = QHBoxLayout(hero_card)
        hero_layout.setContentsMargins(16, 12, 16, 12)
        hero_layout.setSpacing(12)

        hero_badge = QLabel()
        hero_badge.setFixedSize(44, 44)
        hero_badge.setAlignment(Qt.AlignCenter)
        hero_badge.setStyleSheet(
            "background-color: #DCE8D7; border: 1px solid #C7D1C2; border-radius: 12px;"
        )
        hero_badge.setPixmap(
            get_svg_pixmap("shield" if self._require_admin_setup else "coffee", "#005B37", 23)
        )

        hero_text = QVBoxLayout()
        hero_text.setSpacing(2)
        self.hero_title = QLabel("")
        self.hero_title.setProperty("class", "PageHeading")
        self.hero_sub = QLabel("")
        self.hero_sub.setWordWrap(True)
        self.hero_sub.setProperty("class", "MutedLabel")
        hero_text.addWidget(self.hero_title)
        hero_text.addWidget(self.hero_sub)

        self.lang_selector = LanguageSelectorWidget(self._selected_language)
        self.lang_selector.language_changed.connect(self._on_language_toggled)

        hero_layout.addWidget(hero_badge)
        hero_layout.addLayout(hero_text, 1)
        hero_layout.addWidget(self.lang_selector, 0, Qt.AlignTop)
        root.addWidget(hero_card)

        step_row = QHBoxLayout()
        step_row.setSpacing(8)
        self.step1_btn = QPushButton("")
        self.step1_btn.setProperty("class", "CategoryChip")
        self.step1_btn.clicked.connect(lambda: self._set_step(0))

        self.step2_btn = QPushButton("")
        self.step2_btn.setProperty("class", "CategoryChip")
        self.step2_btn.clicked.connect(lambda: self._set_step(1))

        step_row.addWidget(self.step1_btn)
        step_row.addWidget(self.step2_btn)
        step_row.addStretch(1)
        root.addLayout(step_row)

        self.step_stack = QStackedWidget()

        # ---------------- PAGE 1: Store Identity ----------------
        page1 = QFrame()
        page1.setProperty("class", "BoutiqueCard")
        page1_outer = QVBoxLayout(page1)
        page1_outer.setContentsMargins(4, 4, 4, 4)

        p1_scroll = QScrollArea()
        p1_scroll.setObjectName("SetupPage1Scroll")
        p1_scroll.setFrameShape(QFrame.NoFrame)
        p1_scroll.setWidgetResizable(True)
        p1_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        p1_host = QWidget()
        p1_host.setObjectName("SetupPage1Host")

        p1_form = QFormLayout(p1_host)
        p1_form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        p1_form.setContentsMargins(14, 12, 14, 12)
        p1_form.setSpacing(10)

        self.p1_name_lbl = QLabel("")
        self.name_edit = QLineEdit()
        self.name_edit.setMaxLength(60)
        if not self._is_first_launch or self._initial_config.setup_completed:
            self.name_edit.setText(self._initial_config.business_name)
        elif self._require_admin_setup:
            self.name_edit.setText(self._initial_config.business_name or DEFAULT_BUSINESS_NAME)
        self.name_edit.textChanged.connect(self._refresh_color_ui)
        p1_form.addRow(self.p1_name_lbl, self.name_edit)

        self.p1_country_lbl = QLabel("")
        self.country_combo = QComboBox()
        for code in COUNTRY_CODES:
            self.country_combo.addItem(tr_country(code), code)
        c_init = self.country_combo.findData(self._business_country)
        if c_init >= 0:
            self.country_combo.setCurrentIndex(c_init)
        self.country_combo.currentIndexChanged.connect(self._on_country_changed)
        p1_form.addRow(self.p1_country_lbl, self.country_combo)

        curr_wrap = QWidget()
        curr_row = QHBoxLayout(curr_wrap)
        curr_row.setContentsMargins(0, 0, 0, 0)
        curr_row.setSpacing(10)
        self.p1_curr_lbl = QLabel("")
        self.currency_combo = QComboBox()
        for code, spec in CURRENCIES.items():
            self.currency_combo.addItem(spec.label, code)
        idx = self.currency_combo.findData(self._initial_config.currency)
        if idx >= 0:
            self.currency_combo.setCurrentIndex(idx)
        self.currency_combo.currentIndexChanged.connect(self._on_currency_user_changed)

        self.currency_badge = QLabel("")
        self.currency_badge.setProperty("class", "CountBadge")
        curr_row.addWidget(self.currency_combo, 1)
        curr_row.addWidget(self.currency_badge)
        p1_form.addRow(self.p1_curr_lbl, curr_wrap)

        self.p1_logo_lbl = QLabel("")
        logo_widget = QWidget()
        logo_row = QHBoxLayout(logo_widget)
        logo_row.setContentsMargins(0, 0, 0, 0)
        logo_row.setSpacing(12)

        self.logo_preview = QLabel()
        self.logo_preview.setFixedSize(62, 62)
        self.logo_preview.setAlignment(Qt.AlignCenter)
        self.logo_preview.setStyleSheet(
            "background-color: #F7F7E9; border: 1px solid #D8DED3; border-radius: 10px;"
        )

        logo_col = QVBoxLayout()
        logo_col.setContentsMargins(0, 0, 0, 0)
        logo_col.setSpacing(5)
        btn_pair = QHBoxLayout()
        btn_pair.setContentsMargins(0, 0, 0, 0)
        btn_pair.setSpacing(6)

        self.browse_logo_btn = QPushButton("")
        self.browse_logo_btn.setIcon(get_svg_icon("upload", "#202820", 13))
        self.browse_logo_btn.setIconSize(QSize(13, 13))
        self.browse_logo_btn.clicked.connect(self._browse_logo)

        self.clear_logo_btn = QPushButton("")
        self.clear_logo_btn.clicked.connect(self._clear_logo)

        btn_pair.addWidget(self.browse_logo_btn)
        btn_pair.addWidget(self.clear_logo_btn)
        btn_pair.addStretch(1)

        self.logo_hint_lbl = QLabel("")
        self.logo_hint_lbl.setWordWrap(True)
        self.logo_hint_lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.logo_hint_lbl.setProperty("class", "MutedLabel")
        logo_col.addLayout(btn_pair)
        logo_col.addWidget(self.logo_hint_lbl)

        logo_row.addWidget(self.logo_preview)
        logo_row.addLayout(logo_col, 1)
        p1_form.addRow(self.p1_logo_lbl, logo_widget)

        self.p1_addr_lbl = QLabel("")
        self.address_edit = QLineEdit(self._initial_config.address)
        self.address_edit.setMaxLength(140)
        p1_form.addRow(self.p1_addr_lbl, self.address_edit)

        self.p1_phone_lbl = QLabel("")
        self.phone_edit = QLineEdit(self._initial_config.phone)
        self.phone_edit.setMaxLength(40)
        p1_form.addRow(self.p1_phone_lbl, self.phone_edit)

        p1_scroll.setWidget(p1_host)
        page1_outer.addWidget(p1_scroll)
        self.step_stack.addWidget(page1)

        # ---------------- PAGE 2: Style (+ Admin Account on Initial Setup) ----------------
        page2 = QFrame()
        page2.setProperty("class", "BoutiqueCard")
        page2_outer = QVBoxLayout(page2)
        page2_outer.setContentsMargins(4, 4, 4, 4)

        p2_scroll = QScrollArea()
        p2_scroll.setObjectName("SetupPage2Scroll")
        p2_scroll.setFrameShape(QFrame.NoFrame)
        p2_scroll.setWidgetResizable(True)
        p2_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        p2_host = QWidget()
        p2_host.setObjectName("SetupPage2Host")
        p2_layout = QVBoxLayout(p2_host)
        p2_layout.setContentsMargins(14, 12, 14, 12)
        p2_layout.setSpacing(10)

        p2_form = QFormLayout()
        p2_form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        p2_form.setSpacing(10)

        # Admin account section (only created and shown when initial Admin setup is required)
        self.admin_section_lbl: QLabel | None = None
        self.p2_user_lbl: QLabel | None = None
        self.admin_username_edit: QLineEdit | None = None
        self.p2_disp_lbl: QLabel | None = None
        self.admin_display_edit: QLineEdit | None = None
        self.p2_pw_lbl: QLabel | None = None
        self.admin_password_edit: QLineEdit | None = None
        self._pw_toggle: QPushButton | None = None
        self.p2_confirm_lbl: QLabel | None = None
        self.admin_confirm_edit: QLineEdit | None = None
        self._cf_toggle: QPushButton | None = None

        if self._require_admin_setup:
            self.admin_section_lbl = QLabel("")
            self.admin_section_lbl.setProperty("class", "SectionHeader")

            self.p2_user_lbl = QLabel("")
            self.admin_username_edit = QLineEdit()
            self.admin_username_edit.setMaxLength(32)
            self.admin_username_edit.textChanged.connect(self._clear_validation_banner)

            self.p2_disp_lbl = QLabel("")
            self.admin_display_edit = QLineEdit()
            self.admin_display_edit.setMaxLength(60)

            self.p2_pw_lbl = QLabel("")
            pw_row_w, self.admin_password_edit, self._pw_toggle = self._make_password_field("")
            self.p2_confirm_lbl = QLabel("")
            confirm_row_w, self.admin_confirm_edit, self._cf_toggle = self._make_password_field("")
            self.admin_password_edit.textChanged.connect(self._clear_validation_banner)
            self.admin_confirm_edit.textChanged.connect(self._clear_validation_banner)

            p2_layout.addWidget(self.admin_section_lbl)
            admin_form = QFormLayout()
            admin_form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
            admin_form.setSpacing(9)
            admin_form.addRow(self.p2_user_lbl, self.admin_username_edit)
            admin_form.addRow(self.p2_disp_lbl, self.admin_display_edit)
            admin_form.addRow(self.p2_pw_lbl, pw_row_w)
            admin_form.addRow(self.p2_confirm_lbl, confirm_row_w)
            p2_layout.addLayout(admin_form)

            style_sep = QFrame()
            style_sep.setFrameShape(QFrame.HLine)
            style_sep.setStyleSheet("color: #D8DED3;")
            p2_layout.addWidget(style_sep)

        self.p2_theme_lbl = QLabel("")
        color_widget = QWidget()
        color_row = QHBoxLayout(color_widget)
        color_row.setContentsMargins(0, 0, 0, 0)
        color_row.setSpacing(6)
        self._swatch_buttons: list[tuple[str, str, QPushButton]] = []
        for label_text, hex_code in THEME_PRESETS:
            btn = QPushButton()
            btn.setFixedSize(26, 26)
            btn.clicked.connect(lambda _c=False, hx=hex_code: self._set_accent_color(hx))
            self._swatch_buttons.append((label_text, hex_code, btn))
            color_row.addWidget(btn)

        self.custom_color_btn = QPushButton("")
        self.custom_color_btn.clicked.connect(self._pick_custom_color)
        self.color_preview_lbl = QLabel(self._accent_color)
        self.color_preview_lbl.setMinimumWidth(72)
        self.color_preview_lbl.setAlignment(Qt.AlignCenter)
        color_row.addWidget(self.custom_color_btn)
        color_row.addWidget(self.color_preview_lbl)
        color_row.addStretch(1)
        p2_form.addRow(self.p2_theme_lbl, color_widget)
        p2_layout.addLayout(p2_form)

        preview_card = QFrame()
        preview_card.setProperty("class", "CozySubCard")
        prev_row = QHBoxLayout(preview_card)
        prev_row.setContentsMargins(12, 8, 12, 8)
        prev_row.setSpacing(10)
        self.prev_tag_lbl = QLabel("")
        self.prev_tag_lbl.setProperty("class", "MutedLabel")
        self.wizard_preview_brand = QLabel(DEFAULT_BUSINESS_NAME)
        self.wizard_preview_brand.setStyleSheet("font-weight: 700; font-size: 12px;")
        self.wizard_preview_pill = QLabel("")
        prev_row.addWidget(self.prev_tag_lbl)
        prev_row.addWidget(self.wizard_preview_brand, 1)
        prev_row.addWidget(self.wizard_preview_pill)
        p2_layout.addWidget(preview_card)
        p2_layout.addStretch(1)

        p2_scroll.setWidget(p2_host)
        page2_outer.addWidget(p2_scroll)
        self.step_stack.addWidget(page2)
        root.addWidget(self.step_stack, 1)

        self.validation_lbl = QLabel("")
        self.validation_lbl.setProperty("class", "ValidationHint")
        self.validation_lbl.setWordWrap(True)
        self.validation_lbl.hide()
        root.addWidget(self.validation_lbl)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.skip_btn = QPushButton("")
        self.skip_btn.clicked.connect(self._on_skip_or_cancel)

        self.back_btn = QPushButton("")
        self.back_btn.clicked.connect(lambda: self._set_step(0))

        self.next_btn = QPushButton("")
        self.next_btn.setProperty("role", "primary")
        self.next_btn.clicked.connect(lambda: self._set_step(1))

        self.save_btn = QPushButton("")
        self.save_btn.setProperty("role", "primary")
        self.save_btn.setIcon(get_svg_icon("check", contrast_text_hex(self._accent_color), 14))
        self.save_btn.setIconSize(QSize(14, 14))
        self.save_btn.setDefault(True)
        self.save_btn.clicked.connect(self._on_save)

        for btn in (self.skip_btn, self.back_btn, self.next_btn, self.save_btn):
            attach_button_feedback(btn)

        actions.addWidget(self.skip_btn)
        actions.addStretch(1)
        actions.addWidget(self.back_btn)
        actions.addWidget(self.next_btn)
        actions.addWidget(self.save_btn)
        root.addLayout(actions)

    def _on_country_changed(self) -> None:
        code = validate_country_code(self.country_combo.currentData())
        self._business_country = code
        if not self._currency_touched and (
            self._is_first_launch or not self._initial_config.setup_completed
        ):
            suggested_curr = COUNTRY_DEFAULT_CURRENCY.get(code, "USD")
            c_idx = self.currency_combo.findData(suggested_curr)
            if c_idx >= 0:
                self.currency_combo.blockSignals(True)
                self.currency_combo.setCurrentIndex(c_idx)
                self.currency_combo.blockSignals(False)
        self._refresh_color_ui()

    def _on_currency_user_changed(self) -> None:
        self._currency_touched = True
        self._refresh_color_ui()

    def _on_language_toggled(self, lang: str) -> None:
        self._selected_language = normalize_language(lang)
        set_language(self._selected_language)
        QLocale.setDefault(qt_locale(self._selected_language))
        direction = layout_direction(self._selected_language)
        app = QApplication.instance()
        if app is not None:
            temp_cfg = BusinessConfig(
                business_name=self.name_edit.text().strip() or DEFAULT_BUSINESS_NAME,
                accent_color=self._accent_color,
                app_language=self._selected_language,
            )
            apply_theme(app, temp_cfg, lang=self._selected_language)
        self.setLayoutDirection(direction)
        self.retranslate_ui()
        self.language_changed.emit(self._selected_language)

    def retranslate_ui(self) -> None:
        if self._require_admin_setup and self._initial_config.setup_completed:
            self.setWindowTitle(tr("setup.title.upgrade_admin"))
            self.hero_title.setText(tr("setup.hero.upgrade.title"))
            self.hero_sub.setText(tr("setup.hero.upgrade.sub"))
        elif self._require_admin_setup:
            self.setWindowTitle(tr("setup.title.store_and_admin"))
            self.hero_title.setText(tr("setup.hero.welcome.title"))
            self.hero_sub.setText(tr("setup.hero.welcome_admin.sub"))
        elif self._is_first_launch:
            self.setWindowTitle(tr("setup.title.first_launch"))
            self.hero_title.setText(tr("setup.hero.welcome.title"))
            self.hero_sub.setText(tr("setup.hero.welcome_first.sub"))
        else:
            self.setWindowTitle(tr("setup.title.wizard"))
            self.hero_title.setText(tr("setup.hero.edit.title"))
            self.hero_sub.setText(tr("setup.hero.edit.sub"))

        self.lang_selector.set_language(self._selected_language)
        self.lang_selector.retranslate_ui()

        self.step1_btn.setText(_esc_amp(tr("setup.step1_pill")))
        self.step2_btn.setText(
            _esc_amp(
                tr("setup.step2_pill_admin")
                if self._require_admin_setup
                else tr("setup.step2_pill_style")
            )
        )

        self.p1_name_lbl.setText(tr("setup.business_name_label"))
        self.name_edit.setPlaceholderText(tr("setup.business_name_placeholder"))
        self.p1_country_lbl.setText(tr("settings.country_label"))
        prev_country = validate_country_code(self.country_combo.currentData() or self._business_country)
        self.country_combo.blockSignals(True)
        self.country_combo.clear()
        for code in COUNTRY_CODES:
            self.country_combo.addItem(tr_country(code), code)
        co_idx = self.country_combo.findData(prev_country)
        self.country_combo.setCurrentIndex(max(0, co_idx))
        self.country_combo.blockSignals(False)

        self.p1_curr_lbl.setText(tr("setup.currency_label"))

        prev_curr = self.currency_combo.currentData() or self._initial_config.currency or "USD"
        self.currency_combo.blockSignals(True)
        self.currency_combo.clear()
        for code, spec in CURRENCIES.items():
            key = f"currency.{code}"
            lbl = tr(key)
            self.currency_combo.addItem(spec.label if lbl == key else lbl, code)
        c_idx = self.currency_combo.findData(prev_curr)
        self.currency_combo.setCurrentIndex(max(0, c_idx))
        self.currency_combo.blockSignals(False)

        self.p1_logo_lbl.setText(tr("setup.logo_section_label"))
        self.browse_logo_btn.setText(_esc_amp(tr("setup.logo_browse_btn")))
        self.clear_logo_btn.setText(_esc_amp(tr("common.use_default")))
        self.logo_hint_lbl.setText(tr("setup.logo_hint"))

        self.p1_addr_lbl.setText(tr("setup.receipt_address_label"))
        self.address_edit.setPlaceholderText(tr("setup.address_ph"))
        self.p1_phone_lbl.setText(tr("setup.phone_number_label"))
        self.phone_edit.setPlaceholderText(tr("setup.phone_ph"))

        if self._require_admin_setup and self.admin_section_lbl is not None:
            assert self.p2_user_lbl is not None
            assert self.admin_username_edit is not None
            assert self.p2_disp_lbl is not None
            assert self.admin_display_edit is not None
            assert self.p2_pw_lbl is not None
            assert self.admin_password_edit is not None
            assert self._pw_toggle is not None
            assert self.p2_confirm_lbl is not None
            assert self.admin_confirm_edit is not None
            assert self._cf_toggle is not None

            self.admin_section_lbl.setText(tr("role.admin_long"))
            self.p2_user_lbl.setText(tr("setup.admin_username_label"))
            self.admin_username_edit.setPlaceholderText(tr("setup.admin_username_ph"))
            self.p2_disp_lbl.setText(tr("setup.display_name_label"))
            self.admin_display_edit.setPlaceholderText(tr("setup.admin_display_ph"))
            self.p2_pw_lbl.setText(tr("setup.admin_password_label"))
            self.admin_password_edit.setPlaceholderText(tr("setup.admin_pw_ph"))
            self.p2_confirm_lbl.setText(tr("setup.admin_confirm_label"))
            self.admin_confirm_edit.setPlaceholderText(tr("setup.admin_confirm_ph"))

            for edit_w, toggle_w in (
                (self.admin_password_edit, self._pw_toggle),
                (self.admin_confirm_edit, self._cf_toggle),
            ):
                hidden = edit_w.echoMode() == QLineEdit.Password
                toggle_w.setText(_esc_amp(tr("common.show") if hidden else tr("common.hide")))

        self.p2_theme_lbl.setText(tr("setup.theme_color_label"))
        preset_keys = {
            "#005B37": "theme.preset.green",
            "#52796F": "theme.preset.sage",
            "#7D4E2D": "theme.preset.mocha",
            "#C85A3F": "theme.preset.peach",
            "#2B5973": "theme.preset.slate",
            "#8A3D61": "theme.preset.rose",
        }
        for fallback_name, hex_code, btn in self._swatch_buttons:
            pk = preset_keys.get(hex_code.upper())
            lbl = tr(pk) if pk else fallback_name
            btn.setToolTip(f"{lbl} ({hex_code})")
        self.custom_color_btn.setText(_esc_amp(tr("common.custom_dots")))
        self.prev_tag_lbl.setText(tr("setup.preview_label"))
        self.wizard_preview_pill.setText(tr("setup.preview_cta"))

        if self._require_admin_setup:
            cancel_label = tr("setup.exit_btn")
        elif self._is_first_launch:
            cancel_label = tr("setup.skip_btn")
        else:
            cancel_label = tr("common.cancel")
        self.skip_btn.setText(_esc_amp(cancel_label))
        self.back_btn.setText(_esc_amp(tr("common.back")))
        self.next_btn.setText(
            _esc_amp(
                tr("setup.next_admin_btn")
                if self._require_admin_setup
                else tr("setup.next_theme_btn")
            )
        )
        if self._require_admin_setup:
            save_label = tr("setup.create_admin_btn")
        elif self._is_first_launch:
            save_label = tr("setup.finish_open_btn")
        else:
            save_label = tr("setup.save_setup_btn")
        self.save_btn.setText(_esc_amp(save_label))

        if self._validation_key:
            self.validation_lbl.setText(tr(self._validation_key))
        elif self._validation_raw:
            self.validation_lbl.setText(self._validation_raw)

        self._refresh_color_ui()

    def _make_password_field(self, placeholder: str) -> tuple[QWidget, QLineEdit, QPushButton]:
        wrap = QWidget()
        row = QHBoxLayout(wrap)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        edit = QLineEdit()
        edit.setEchoMode(QLineEdit.Password)
        edit.setPlaceholderText(placeholder)
        edit.setMaxLength(128)
        toggle = QPushButton(tr("common.show"))
        toggle.setFixedWidth(68)
        toggle.setStyleSheet("padding: 5px 8px; font-size: 12px;")

        def _toggle() -> None:
            hidden = edit.echoMode() == QLineEdit.Password
            edit.setEchoMode(QLineEdit.Normal if hidden else QLineEdit.Password)
            toggle.setText(tr("common.hide") if hidden else tr("common.show"))

        toggle.clicked.connect(_toggle)
        row.addWidget(edit, 1)
        row.addWidget(toggle)
        return wrap, edit, toggle

    def _clear_validation_banner(self) -> None:
        self._validation_key = None
        self._validation_raw = ""
        self.validation_lbl.hide()
        self.validation_lbl.setText("")

    def _show_validation_error(self, message: str, *, key: str | None = None) -> None:
        self._validation_key = key
        self._validation_raw = "" if key else message
        self.validation_lbl.setText(tr(key) if key else message)
        self.validation_lbl.show()

    def _set_step(self, step_idx: int) -> None:
        idx = 1 if step_idx == 1 else 0
        self.step_stack.setCurrentIndex(idx)
        self.step1_btn.setProperty("selected", "true" if idx == 0 else "false")
        self.step2_btn.setProperty("selected", "true" if idx == 1 else "false")
        for b in (self.step1_btn, self.step2_btn):
            b.style().unpolish(b)
            b.style().polish(b)

        self.back_btn.setVisible(idx == 1)
        self.next_btn.setVisible(idx == 0)
        self.save_btn.setVisible(idx == 1)
        if idx == 1 and self._require_admin_setup:
            if not self.admin_username_edit.text():
                self.admin_username_edit.setFocus()
            else:
                self.admin_password_edit.setFocus()

    def _set_accent_color(self, hex_code: str) -> None:
        self._accent_color = validate_hex_color(hex_code)
        self._refresh_color_ui()

    def _refresh_color_ui(self) -> None:
        for _fallback, hex_code, btn in self._swatch_buttons:
            selected = hex_code.upper() == self._accent_color.upper()
            border = "2.5px solid #202820" if selected else "1px solid #D8DED3"
            btn.setStyleSheet(
                f"background-color: {hex_code}; border: {border}; border-radius: 7px; padding: 0;"
            )
        fg = contrast_text_hex(self._accent_color)
        self.color_preview_lbl.setText(self._accent_color)
        self.color_preview_lbl.setStyleSheet(
            f"background-color: {self._accent_color}; color: {fg}; "
            "font-weight: 700; font-size: 11px; padding: 4px 8px; border-radius: 7px;"
        )

        curr = self.currency_combo.currentData() or "USD"
        self.currency_badge.setText(tr("setup.sample_price", price=format_minor(450, curr)))
        biz_name = self.name_edit.text().strip() or DEFAULT_BUSINESS_NAME
        self.wizard_preview_brand.setText(f"{biz_name} · {curr}")
        self.wizard_preview_pill.setStyleSheet(
            f"background-color: {self._accent_color}; color: {fg}; "
            "font-weight: 700; font-size: 11px; padding: 4px 10px; border-radius: 10px;"
        )
        self.save_btn.setIcon(get_svg_icon("check", fg, 14))

    def _pick_custom_color(self) -> None:
        chosen = QColorDialog.getColor(
            QColor(self._accent_color), self, tr("setup.theme_picker_title")
        )
        if chosen.isValid():
            self._set_accent_color(chosen.name())

    def _browse_logo(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            tr("setup.logo_dialog_title"),
            "",
            tr("setup.logo_dialog_filter"),
        )
        if not file_path:
            return
        try:
            blob, ext = read_and_validate_logo(Path(file_path))
        except ValueError as exc:
            QMessageBox.warning(self, tr("setup.logo_invalid_title"), str(exc))
            return
        self._logo_blob = blob
        self._logo_ext = ext
        self._refresh_logo_preview()

    def _clear_logo(self) -> None:
        self._logo_blob = b""
        self._logo_ext = ""
        self._refresh_logo_preview()

    def _refresh_logo_preview(self) -> None:
        temp_cfg = BusinessConfig(
            accent_color=self._accent_color,
            logo_blob=self._logo_blob,
            logo_ext=self._logo_ext,
        )
        self.logo_preview.setPixmap(scaled_logo_pixmap(temp_cfg, 50, 50))

    def _build_config(self, business_name: str) -> BusinessConfig:
        base = self._initial_config
        country = validate_country_code(self.country_combo.currentData() or self._business_country)
        base_pm = parse_payment_settings(
            getattr(base, "payment_methods_json", ""),
            getattr(base, "business_country", "US"),
        )
        pm_cfg = apply_country_preset(base_pm, country, force=False)
        return BusinessConfig(
            business_name=business_name,
            accent_color=self._accent_color,
            currency=self.currency_combo.currentData() or "USD",
            address=self.address_edit.text().strip(),
            phone=self.phone_edit.text().strip(),
            logo_blob=self._logo_blob,
            logo_ext=self._logo_ext,
            setup_completed=True,
            image_provider=base.image_provider,
            pexels_api_key=base.pexels_api_key,
            reduced_motion=base.reduced_motion,
            auto_lock_minutes=base.auto_lock_minutes,
            backup_enabled=base.backup_enabled,
            backup_frequency=base.backup_frequency,
            backup_destination=base.backup_destination,
            backup_retention_count=base.backup_retention_count,
            last_auto_backup_at=base.last_auto_backup_at,
            app_language=self._selected_language,
            receipt_language=getattr(base, "receipt_language", "follow") or "follow",
            business_country=country,
            payment_methods_json=serialize_payment_settings(pm_cfg),
            auto_save_receipt_pdf=bool(getattr(base, "auto_save_receipt_pdf", False)),
            receipt_export_dir=getattr(base, "receipt_export_dir", ""),
            online_translation_enabled=bool(getattr(base, "online_translation_enabled", False)),
            country_configured=True,
        )

    def _on_skip_or_cancel(self) -> None:
        if self._require_admin_setup:
            # Never bypass initial administrator setup.
            self.reject()
            return
        if self._is_first_launch:
            chosen_name = self.name_edit.text().strip() or DEFAULT_BUSINESS_NAME
            self.result_config = self._build_config(chosen_name)
            self.accept()
        else:
            self.reject()

    def _on_save(self) -> None:
        self._clear_validation_banner()
        name = self.name_edit.text().strip()
        if not name:
            self._set_step(0)
            self._show_validation_error(
                tr("setup.error.business_req_msg"), key="setup.error.business_req_msg"
            )
            self.name_edit.setFocus()
            return

        if self._require_admin_setup:
            try:
                username = validate_username(self.admin_username_edit.text())
            except ValueError as exc:
                self._set_step(1)
                self._show_validation_error(str(exc))
                self.admin_username_edit.setFocus()
                return

            try:
                disp = validate_display_name(self.admin_display_edit.text(), username)
            except ValueError as exc:
                self._set_step(1)
                self._show_validation_error(str(exc))
                self.admin_display_edit.setFocus()
                return

            try:
                pw = validate_password_policy(self.admin_password_edit.text())
            except ValueError as exc:
                self._set_step(1)
                self._show_validation_error(str(exc))
                self.admin_password_edit.setFocus()
                return

            if pw != self.admin_confirm_edit.text():
                self._set_step(1)
                self._show_validation_error(
                    tr("setup.error.passwords_mismatch_msg"),
                    key="setup.error.passwords_mismatch_msg",
                )
                self.admin_confirm_edit.setFocus()
                return

            self.result_admin_username = username
            self.result_admin_display_name = disp
            self.result_admin_password = pw

        self.result_config = self._build_config(name)
        self.accept()
