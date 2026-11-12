from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QBoxLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..auth import AuthenticationError
from ..config import BusinessConfig
from ..db import Database
from ..icons import get_svg_icon, render_empty_state_illustration
from ..i18n import bidi_ltr, get_language, normalize_language, tr
from ..meta import (
    DEVELOPER_NAME,
    get_contact_developer_url,
    get_feature_request_url,
    has_direct_contact,
    open_external_url,
)
from ..paths import get_asset_path
from ..theme import contrast_text_hex, scaled_logo_pixmap
from .animations import (
    animate_fade_in,
    animate_flash_highlight,
    attach_button_feedback,
    set_button_loading,
)
from .widgets import LanguageSelectorWidget


def _esc_amp(text: str) -> str:
    return (text or "").replace("&", "&&")


def _rounded_pixmap(src: QPixmap, target_w: int, target_h: int, radius: int = 16) -> QPixmap:
    if src.isNull():
        return src
    scaled = src.scaled(
        target_w * 2,
        target_h * 2,
        Qt.KeepAspectRatioByExpanding,
        Qt.SmoothTransformation,
    )
    cx = max(0, (scaled.width() - target_w * 2) // 2)
    cy = max(0, (scaled.height() - target_h * 2) // 2)
    cropped = scaled.copy(cx, cy, target_w * 2, target_h * 2)

    out = QPixmap(target_w * 2, target_h * 2)
    out.fill(Qt.transparent)
    painter = QPainter(out)
    try:
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, target_w * 2, target_h * 2), radius * 2, radius * 2)
        painter.setClipPath(path)
        painter.drawPixmap(0, 0, cropped)
    finally:
        if painter.isActive():
            painter.end()
    out.setDevicePixelRatio(2.0)
    return out


class LoginView(QWidget):
    """Two-column boutique Sign-In gate with bundled offline cafe illustration."""

    authenticated = Signal(object)  # emits UserAccount
    language_selected = Signal(str)

    def __init__(
        self, db: Database, config: BusinessConfig, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.db = db
        self.config = config
        self.setObjectName("LoginPageHost")
        self._pw_visible = False
        self._notice_key: str | None = None
        self._notice_raw: str = ""
        self._error_key: str | None = None
        self._error_kwargs: dict[str, object] = {}
        self._error_raw: str = ""
        self._did_animate_in = False
        self._raw_hero_pixmap = self._load_hero_pixmap()

        self._build_ui()
        self.retranslate_ui()
        self.refresh_branding(config)

    @staticmethod
    def _load_hero_pixmap() -> QPixmap:
        art_path = get_asset_path("login_cafe_art.png")
        if art_path.is_file():
            pm = QPixmap(str(art_path))
            if not pm.isNull():
                return pm
        return QPixmap()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("LoginScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        host = QWidget()
        host.setObjectName("LoginPageHost")
        outer = QVBoxLayout(host)
        outer.setContentsMargins(24, 18, 24, 18)
        outer.addStretch(1)

        center_row = QHBoxLayout()
        center_row.addStretch(1)

        self.card = QFrame()
        self.card.setObjectName("LoginSplitCard")
        self.card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self.card.setMaximumWidth(860)
        card_split = QHBoxLayout(self.card)
        card_split.setContentsMargins(14, 14, 14, 14)
        card_split.setSpacing(18)

        # ---------------- Left Column: Editorial Cafe Artwork Panel ----------------
        self.hero_panel = QFrame()
        self.hero_panel.setObjectName("LoginHeroPanel")
        self.hero_panel.setMinimumWidth(330)
        self.hero_panel.setMaximumWidth(370)
        hero_layout = QVBoxLayout(self.hero_panel)
        hero_layout.setContentsMargins(20, 20, 20, 20)
        hero_layout.setSpacing(12)

        pill_row = QHBoxLayout()
        pill_row.setContentsMargins(0, 0, 0, 0)
        self.hero_pill_lbl = QLabel("")
        self.hero_pill_lbl.setObjectName("LoginHeroPill")
        self.hero_pill_lbl.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        pill_row.addWidget(self.hero_pill_lbl)
        pill_row.addStretch(1)
        hero_layout.addLayout(pill_row)

        self.hero_art_lbl = QLabel()
        self.hero_art_lbl.setAlignment(Qt.AlignCenter)
        self.hero_art_lbl.setFixedSize(296, 260)
        self._apply_hero_artwork()
        hero_layout.addWidget(self.hero_art_lbl, 0, Qt.AlignCenter)

        self.hero_title_lbl = QLabel("")
        self.hero_title_lbl.setObjectName("LoginHeroTitle")
        self.hero_title_lbl.setWordWrap(True)
        hero_layout.addWidget(self.hero_title_lbl)

        self.hero_tagline_lbl = QLabel("")
        self.hero_tagline_lbl.setObjectName("LoginHeroTagline")
        self.hero_tagline_lbl.setWordWrap(True)
        hero_layout.addWidget(self.hero_tagline_lbl)
        hero_layout.addStretch(1)

        card_split.addWidget(self.hero_panel)

        # ---------------- Right Column: White-Label Sign-In Form ----------------
        self.form_panel = QFrame()
        self.form_panel.setObjectName("LoginFormPanel")
        self.form_panel.setMinimumWidth(330)
        form_layout = QVBoxLayout(self.form_panel)
        form_layout.setContentsMargins(12, 10, 14, 12)
        form_layout.setSpacing(11)

        # Header: White-label Store Brand + Language Switcher
        top_brand_row = QHBoxLayout()
        top_brand_row.setSpacing(10)

        self.logo_lbl = QLabel()
        self.logo_lbl.setObjectName("BrandLogoFrame")
        self.logo_lbl.setFixedSize(44, 44)
        self.logo_lbl.setAlignment(Qt.AlignCenter)

        self.store_title_lbl = QLabel(self.config.business_name)
        self.store_title_lbl.setStyleSheet("font-size: 15.5px; font-weight: 700; color: #202820;")
        self.store_title_lbl.setWordWrap(True)

        self.lang_selector = LanguageSelectorWidget(get_language())
        self.lang_selector.language_changed.connect(self.language_selected.emit)

        top_brand_row.addWidget(self.logo_lbl)
        top_brand_row.addWidget(self.store_title_lbl, 1)
        top_brand_row.addWidget(self.lang_selector, 0, Qt.AlignTop)
        form_layout.addLayout(top_brand_row)

        form_layout.addSpacing(2)

        # Welcome Heading & Calm Subtitle
        self.welcome_heading_lbl = QLabel("")
        self.welcome_heading_lbl.setStyleSheet("font-size: 21px; font-weight: 700; color: #202820;")
        self.welcome_heading_lbl.setWordWrap(True)
        self.subtitle_lbl = QLabel("")
        self.subtitle_lbl.setProperty("class", "MutedLabel")
        self.subtitle_lbl.setWordWrap(True)
        form_layout.addWidget(self.welcome_heading_lbl)
        form_layout.addWidget(self.subtitle_lbl)

        # Informational Notice Banner (auto-lock / logout / restore)
        self.notice_banner = QFrame()
        self.notice_banner.setProperty("class", "CozySubCard")
        notice_row = QHBoxLayout(self.notice_banner)
        notice_row.setContentsMargins(11, 8, 11, 8)
        notice_row.setSpacing(8)
        self.notice_icon_lbl = QLabel()
        self.notice_icon_lbl.setPixmap(get_svg_icon("lock", "#005B37", 14).pixmap(14, 14))
        self.notice_lbl = QLabel("")
        self.notice_lbl.setWordWrap(True)
        self.notice_lbl.setStyleSheet("font-size: 12px; font-weight: 600; color: #202820;")
        notice_row.addWidget(self.notice_icon_lbl)
        notice_row.addWidget(self.notice_lbl, 1)
        self.notice_banner.hide()
        form_layout.addWidget(self.notice_banner)

        # Error Banner
        self.error_banner = QFrame()
        self.error_banner.setObjectName("LoginErrorBanner")
        err_row = QHBoxLayout(self.error_banner)
        err_row.setContentsMargins(11, 8, 11, 8)
        err_row.setSpacing(8)
        err_icon = QLabel()
        err_icon.setPixmap(get_svg_icon("lock", "#B23A3A", 14).pixmap(14, 14))
        self.error_lbl = QLabel("")
        self.error_lbl.setWordWrap(True)
        self.error_lbl.setStyleSheet("font-size: 12px; font-weight: 600; color: #B23A3A;")
        err_row.addWidget(err_icon)
        err_row.addWidget(self.error_lbl, 1)
        self.error_banner.hide()
        form_layout.addWidget(self.error_banner)

        # Credentials Form
        self.user_lbl = QLabel("")
        self.user_lbl.setStyleSheet("font-weight: 600; color: #202820;")
        self.username_edit = QLineEdit()
        self.username_edit.setMaxLength(32)
        self.username_edit.setMinimumHeight(40)
        self.username_edit.textChanged.connect(self._clear_error)
        self.username_edit.returnPressed.connect(self._on_username_enter)
        form_layout.addWidget(self.user_lbl)
        form_layout.addWidget(self.username_edit)

        self.pw_lbl = QLabel("")
        self.pw_lbl.setStyleSheet("font-weight: 600; color: #202820;")
        pw_row = QHBoxLayout()
        pw_row.setSpacing(6)
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.Password)
        self.password_edit.setMaxLength(128)
        self.password_edit.setMinimumHeight(40)
        self.password_edit.textChanged.connect(self._clear_error)
        self.password_edit.returnPressed.connect(self._attempt_login)

        self.toggle_pw_btn = QPushButton()
        self.toggle_pw_btn.setFixedSize(40, 40)
        self.toggle_pw_btn.setCursor(Qt.PointingHandCursor)
        self.toggle_pw_btn.setIcon(get_svg_icon("eye", "#5E6B5F", 16))
        self.toggle_pw_btn.setIconSize(QSize(16, 16))
        self.toggle_pw_btn.clicked.connect(self._toggle_pw_visibility)

        pw_row.addWidget(self.password_edit, 1)
        pw_row.addWidget(self.toggle_pw_btn)
        form_layout.addWidget(self.pw_lbl)
        form_layout.addLayout(pw_row)

        form_layout.addSpacing(4)

        self.sign_in_btn = QPushButton("")
        self.sign_in_btn.setProperty("role", "primary")
        self.sign_in_btn.setMinimumHeight(44)
        self.sign_in_btn.setCursor(Qt.PointingHandCursor)
        self.sign_in_btn.setDefault(True)
        self.sign_in_btn.setAutoDefault(True)
        self.sign_in_btn.clicked.connect(self._attempt_login)
        attach_button_feedback(self.sign_in_btn)
        form_layout.addWidget(self.sign_in_btn)

        form_layout.addSpacing(2)
        self.footer_hint = QLabel("")
        self.footer_hint.setAlignment(Qt.AlignCenter)
        self.footer_hint.setWordWrap(True)
        self.footer_hint.setProperty("class", "MutedLabel")
        form_layout.addWidget(self.footer_hint)

        card_split.addWidget(self.form_panel, 1)

        center_row.addWidget(self.card)
        center_row.addStretch(1)
        outer.addLayout(center_row)

        outer.addSpacing(12)

        # Compact Boutique Developer Contact Surface below the centered login panel
        contact_row = QHBoxLayout()
        contact_row.addStretch(1)

        self.contact_card = QFrame()
        self.contact_card.setObjectName("LoginContactCard")
        self.contact_card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self.contact_card.setMaximumWidth(860)

        self._contact_layout = QBoxLayout(QBoxLayout.LeftToRight, self.contact_card)
        self._contact_layout.setContentsMargins(18, 12, 18, 12)
        self._contact_layout.setSpacing(14)

        contact_info_row = QHBoxLayout()
        contact_info_row.setContentsMargins(0, 0, 0, 0)
        contact_info_row.setSpacing(12)

        self.contact_icon_badge = QFrame()
        self.contact_icon_badge.setObjectName("LoginContactIconBadge")
        self.contact_icon_badge.setFixedSize(38, 38)
        badge_lay = QVBoxLayout(self.contact_icon_badge)
        badge_lay.setContentsMargins(0, 0, 0, 0)
        badge_lay.setAlignment(Qt.AlignCenter)
        self.contact_badge_icon_lbl = QLabel()
        self.contact_badge_icon_lbl.setAlignment(Qt.AlignCenter)
        badge_lay.addWidget(self.contact_badge_icon_lbl)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)

        self.contact_title_lbl = QLabel("")
        self.contact_title_lbl.setObjectName("LoginContactTitle")
        self.contact_title_lbl.setWordWrap(True)

        self.contact_sub_lbl = QLabel("")
        self.contact_sub_lbl.setObjectName("LoginContactSub")
        self.contact_sub_lbl.setWordWrap(True)

        self.contact_credit_lbl = QLabel("")
        self.contact_credit_lbl.setObjectName("LoginContactCredit")
        self.contact_credit_lbl.setWordWrap(True)

        text_col.addWidget(self.contact_title_lbl)
        text_col.addWidget(self.contact_sub_lbl)
        text_col.addWidget(self.contact_credit_lbl)

        contact_info_row.addWidget(self.contact_icon_badge, 0, Qt.AlignTop)
        contact_info_row.addLayout(text_col, 1)

        contact_btn_row = QHBoxLayout()
        contact_btn_row.setContentsMargins(0, 0, 0, 0)
        contact_btn_row.setSpacing(8)

        self.request_feature_btn = QPushButton("")
        self.request_feature_btn.setProperty("class", "LoginContactBtn")
        self.request_feature_btn.setCursor(Qt.PointingHandCursor)
        self.request_feature_btn.setMinimumHeight(34)
        self.request_feature_btn.setAutoDefault(False)
        self.request_feature_btn.clicked.connect(
            lambda: open_external_url(get_feature_request_url())
        )
        attach_button_feedback(self.request_feature_btn)

        self.contact_dev_btn = QPushButton("")
        self.contact_dev_btn.setProperty("class", "LoginContactBtn")
        self.contact_dev_btn.setCursor(Qt.PointingHandCursor)
        self.contact_dev_btn.setMinimumHeight(34)
        self.contact_dev_btn.setAutoDefault(False)
        self.contact_dev_btn.clicked.connect(
            lambda: open_external_url(get_contact_developer_url())
        )
        attach_button_feedback(self.contact_dev_btn)

        contact_btn_row.addWidget(self.request_feature_btn)
        contact_btn_row.addWidget(self.contact_dev_btn)

        self._contact_layout.addLayout(contact_info_row, 1)
        self._contact_layout.addLayout(contact_btn_row, 0)

        contact_row.addWidget(self.contact_card)
        contact_row.addStretch(1)
        outer.addLayout(contact_row)
        outer.addStretch(1)

        self.scroll.setWidget(host)
        root.addWidget(self.scroll)

        self.setTabOrder(self.username_edit, self.password_edit)
        self.setTabOrder(self.password_edit, self.toggle_pw_btn)
        self.setTabOrder(self.toggle_pw_btn, self.sign_in_btn)

    def _apply_hero_artwork(self) -> None:
        if not self._raw_hero_pixmap.isNull():
            self.hero_art_lbl.setPixmap(_rounded_pixmap(self._raw_hero_pixmap, 296, 260, radius=16))
        else:
            accent = getattr(self.config, "accent_color", "#005B37")
            self.hero_art_lbl.setPixmap(render_empty_state_illustration("cart", accent, size=160))

    def _update_responsive_split(self) -> None:
        w = self.width()
        h = self.height()
        compact = (w > 0 and w < 780) or (h > 0 and h < 500)
        self.hero_panel.setVisible(not compact)
        if compact:
            min_w = min(430, max(320, w - 36))
            self.card.setMinimumWidth(min_w)
            self.card.setMaximumWidth(450)
            self.contact_card.setMinimumWidth(min_w)
            self.contact_card.setMaximumWidth(450)
            self._contact_layout.setDirection(QBoxLayout.TopToBottom)
        else:
            avail = max(720, min(860, w - 48)) if w > 0 else 840
            min_w = min(740, avail)
            self.card.setMinimumWidth(min_w)
            self.card.setMaximumWidth(860)
            self.contact_card.setMinimumWidth(min_w)
            self.contact_card.setMaximumWidth(860)
            self._contact_layout.setDirection(QBoxLayout.LeftToRight)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._update_responsive_split()
        if not self._did_animate_in:
            self._did_animate_in = True
            animate_fade_in(self.card, duration_ms=170, start_opacity=0.84)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_responsive_split()

    def retranslate_ui(self) -> None:
        self.lang_selector.set_language(get_language())
        self.lang_selector.retranslate_ui()
        self.hero_pill_lbl.setText(tr("login.hero_pill"))
        self.hero_title_lbl.setText(tr("login.hero_title"))
        self.hero_tagline_lbl.setText(tr("login.hero_tagline"))
        self.welcome_heading_lbl.setText(tr("login.welcome_heading"))
        self.subtitle_lbl.setText(tr("login.subtitle_offline"))
        self.user_lbl.setText(tr("login.username_label"))
        self.username_edit.setPlaceholderText(tr("login.username_ph"))
        self.pw_lbl.setText(tr("login.password_label"))
        self.password_edit.setPlaceholderText(tr("login.password_ph"))
        self.toggle_pw_btn.setToolTip(
            tr("login.pw_hide_tip") if self._pw_visible else tr("login.pw_show_tip")
        )
        self.sign_in_btn.setText(_esc_amp(tr("login.signin_btn")))
        self.footer_hint.setText(tr("login.footer_roles"))
        self.contact_title_lbl.setText(tr("login.contact.title"))
        self.contact_sub_lbl.setText(tr("login.contact.subtitle"))
        self.contact_credit_lbl.setText(
            tr("login.contact.credit", developer=bidi_ltr(DEVELOPER_NAME))
        )
        self.request_feature_btn.setText(_esc_amp(tr("login.contact.request_feature_btn")))
        self.request_feature_btn.setToolTip(get_feature_request_url())
        self.contact_dev_btn.setText(_esc_amp(tr("login.contact.contact_btn")))
        self.contact_dev_btn.setToolTip(
            get_contact_developer_url()
            if has_direct_contact()
            else tr("login.contact.contact_issues_tip")
        )

        if self.notice_banner.isVisible():
            if self._notice_key:
                self.notice_lbl.setText(tr(self._notice_key))
            elif self._notice_raw:
                self.notice_lbl.setText(self._notice_raw)

        if self.error_banner.isVisible():
            if self._error_key:
                self.error_lbl.setText(tr(self._error_key, **self._error_kwargs))
            elif self._error_raw:
                self.error_lbl.setText(self._error_raw)

    def refresh_branding(self, config: BusinessConfig) -> None:
        self.config = config
        self.store_title_lbl.setText(config.business_name)
        self.logo_lbl.setPixmap(scaled_logo_pixmap(config, 32, 32))
        self._apply_hero_artwork()
        fg = contrast_text_hex(config.accent_color)
        self.sign_in_btn.setIcon(get_svg_icon("lock", fg, 15))
        self.sign_in_btn.setIconSize(QSize(15, 15))
        self.notice_icon_lbl.setPixmap(
            get_svg_icon("lock", config.accent_color, 14).pixmap(14, 14)
        )
        self.contact_badge_icon_lbl.setPixmap(
            get_svg_icon("coffee", config.accent_color, 18).pixmap(18, 18)
        )
        self.request_feature_btn.setIcon(get_svg_icon("sparkles", config.accent_color, 14))
        self.request_feature_btn.setIconSize(QSize(14, 14))
        self.contact_dev_btn.setIcon(get_svg_icon("message", config.accent_color, 14))
        self.contact_dev_btn.setIconSize(QSize(14, 14))
        lang_val = normalize_language(getattr(config, "app_language", "") or get_language())
        self.lang_selector.set_language(lang_val)

    def reset_inputs(self, *, clear_notice: bool = True) -> None:
        self.username_edit.clear()
        self.password_edit.clear()
        if self._pw_visible:
            self._toggle_pw_visibility()
        self._clear_error()
        if clear_notice:
            self._notice_key = None
            self._notice_raw = ""
            self.notice_banner.hide()
            self.notice_lbl.clear()

    def show_notice(self, message: str, *, key: str | None = None) -> None:
        text = tr(key) if key else message
        if not text:
            self._notice_key = None
            self._notice_raw = ""
            self.notice_banner.hide()
            return
        self._notice_key = key
        self._notice_raw = "" if key else message
        self.notice_lbl.setText(text)
        self.notice_banner.show()
        animate_flash_highlight(self.notice_banner, duration_ms=150, dip_opacity=0.65)

    def show_error(self, message: str, *, key: str | None = None, **kwargs: object) -> None:
        self._error_key = key
        self._error_kwargs = dict(kwargs)
        self._error_raw = "" if key else message
        self.error_lbl.setText(tr(key, **kwargs) if key else message)
        self.error_banner.show()
        animate_flash_highlight(self.error_banner, duration_ms=150, dip_opacity=0.65)

    def _clear_error(self) -> None:
        self._error_key = None
        self._error_kwargs = {}
        self._error_raw = ""
        if self.error_banner.isVisible():
            self.error_banner.hide()
            self.error_lbl.clear()

    def focus_username(self) -> None:
        self.username_edit.setFocus()
        self.username_edit.selectAll()

    def _on_username_enter(self) -> None:
        if self.password_edit.text():
            self._attempt_login()
        else:
            self.password_edit.setFocus()

    def _toggle_pw_visibility(self) -> None:
        self._pw_visible = not self._pw_visible
        self.password_edit.setEchoMode(
            QLineEdit.Normal if self._pw_visible else QLineEdit.Password
        )
        icon_name = "eye_off" if self._pw_visible else "eye"
        self.toggle_pw_btn.setIcon(get_svg_icon(icon_name, "#5E6B5F", 16))
        self.toggle_pw_btn.setToolTip(
            tr("login.pw_hide_tip") if self._pw_visible else tr("login.pw_show_tip")
        )

    def _attempt_login(self) -> None:
        username = self.username_edit.text().strip()
        password = self.password_edit.text()
        if not username:
            self.show_error("", key="login.error.no_username")
            self.username_edit.setFocus()
            return
        if not password:
            self.show_error("", key="login.error.no_password")
            self.password_edit.setFocus()
            return

        fg = contrast_text_hex(self.config.accent_color)
        set_button_loading(
            self.sign_in_btn,
            True,
            loading_text=tr("login.verifying_btn"),
            spinner_color=fg,
        )
        self.sign_in_btn.repaint()
        try:
            user = self.db.authenticate(username, password)
        except AuthenticationError as exc:
            set_button_loading(self.sign_in_btn, False)
            self.password_edit.clear()
            self.password_edit.setFocus()
            self.show_error(str(exc))
            return
        except Exception as exc:
            set_button_loading(self.sign_in_btn, False)
            self.show_error("", key="login.error.signin_prefix", exc=exc)
            return

        set_button_loading(self.sign_in_btn, False)
        self.reset_inputs(clear_notice=True)
        self.authenticated.emit(user)
