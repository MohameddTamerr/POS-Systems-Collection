from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QEvent, QLocale, QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..auth import Permission, UserAccount, has_permission
from ..backups import run_due_auto_backup
from ..config import BusinessConfig
from ..db import Database, OrderRecord
from ..icons import get_svg_icon
from ..i18n import (
    format_header_date,
    get_language,
    normalize_language,
    qt_locale,
    set_language,
    tr,
    tr_role,
    tr_role_long,
)
from ..paths import get_receipts_dir
from ..receipts import export_receipt_pdf, print_receipt_to_printer
from ..theme import apply_theme, contrast_text_hex, scaled_logo_pixmap
from .about_dialog import AboutDialog
from .animations import AnimatedStackedWidget, attach_button_feedback, set_reduced_motion
from .display_preferences_dialog import DisplayPreferencesDialog
from .login_view import LoginView
from .products_view import ProductsView
from .receipt_dialog import ReceiptDialog
from .register_view import RegisterView
from .reports_view import ReportsView
from .sales_view import SalesView
from .settings_view import SettingsView
from .setup_dialog import SetupDialog
from .users_view import UsersView
from .widgets import ToastBanner

# (label_key, compact_key, tip_key, icon_name, stack_idx, shortcut, permission)
NAV_SPECS: list[tuple[str, str, str, str, int, str, Permission]] = [
    ("nav.register", "nav.register_compact", "nav.register_tip", "register", 0, "F1", Permission.ACCESS_REGISTER),
    ("nav.products", "nav.products_compact", "nav.products_tip", "products", 1, "F2", Permission.MANAGE_PRODUCTS),
    ("nav.sales", "nav.sales_compact", "nav.sales_tip", "sales", 2, "F3", Permission.VIEW_OWN_SALES),
    ("nav.reports", "nav.reports_compact", "nav.reports_tip", "reports", 3, "F4", Permission.VIEW_REPORTS),
    ("nav.users", "nav.users_compact", "nav.users_tip", "users", 4, "F5", Permission.MANAGE_USERS),
    ("nav.settings", "nav.settings_compact", "nav.settings_tip", "settings", 5, "F6", Permission.MANAGE_SETTINGS),
]


def _esc_amp(text: str) -> str:
    return (text or "").replace("&", "&&")


class MainWindow(QMainWindow):
    """Primary Boutique Café POS desktop window with Auth Gate, RBAC, and Auto-Backup."""

    def __init__(self, db: Database, app: QApplication) -> None:
        super().__init__()
        self.db = db
        self.app = app
        self.config: BusinessConfig = self.db.get_settings()
        self.current_user: UserAccount | None = None
        self._last_activity_ts: float = time.monotonic()
        self._last_header_compact_mode: str = ""
        self._pre_fullscreen_maximized: bool = (
            getattr(self.config, "window_state_pref", "maximized") == "maximized"
        )

        init_lang = normalize_language(getattr(self.config, "app_language", "") or "en")
        set_language(init_lang)
        QLocale.setDefault(qt_locale(init_lang))
        set_reduced_motion(self.config.reduced_motion)

        self._restore_initial_window_geometry()
        self.setMinimumSize(900, 485)

        self._build_ui()
        self._register_shortcuts()
        self._apply_branding()
        self._install_activity_monitors()

        # Always start unauthenticated on Login screen until owner setup or sign-in completes.
        self._show_login_gate(notice="")

    @property
    def is_authenticated(self) -> bool:
        return self.current_user is not None

    def _build_ui(self) -> None:
        central = QWidget()
        outer_layout = QVBoxLayout(central)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        self.root_stack = QStackedWidget()

        # Page 0: Login Gate (completely hides navigation & register when signed out)
        self.login_view = LoginView(self.db, self.config)
        self.login_view.authenticated.connect(self._on_user_authenticated)
        self.login_view.language_selected.connect(
            lambda lang: self.switch_language(lang, persist_store=True)
        )
        self.root_stack.addWidget(self.login_view)

        # Page 1: Authenticated Workspace
        self.workspace_widget = QWidget()
        ws_layout = QVBoxLayout(self.workspace_widget)
        ws_layout.setContentsMargins(0, 0, 0, 0)
        ws_layout.setSpacing(0)

        # Clean Top Navigation Bar
        nav_bar = QFrame()
        nav_bar.setObjectName("TopNavBar")
        nav_bar.setFixedHeight(58)
        self._nav_bar = nav_bar
        nav_layout = QHBoxLayout(nav_bar)
        nav_layout.setContentsMargins(16, 6, 16, 6)
        nav_layout.setSpacing(8)
        self._nav_layout = nav_layout

        # Left Brand Group
        self.logo_label = QLabel()
        self.logo_label.setObjectName("BrandLogoFrame")
        self.logo_label.setFixedSize(36, 36)
        self.logo_label.setAlignment(Qt.AlignCenter)

        brand_text_col = QVBoxLayout()
        brand_text_col.setContentsMargins(0, 0, 0, 0)
        brand_text_col.setSpacing(0)
        self.brand_title_lbl = QLabel()
        self.brand_title_lbl.setObjectName("BrandTitleLabel")
        self.brand_title_lbl.setMaximumWidth(290)
        self.brand_sub_lbl = QLabel()
        self.brand_sub_lbl.setObjectName("BrandSubLabel")
        brand_text_col.addWidget(self.brand_title_lbl)
        brand_text_col.addWidget(self.brand_sub_lbl)

        nav_layout.addWidget(self.logo_label)
        nav_layout.addLayout(brand_text_col)
        nav_layout.addStretch(1)

        # Center Primary Navigation Tabs (0..3) + Right Secondary Admin Tabs (4..5)
        self.nav_buttons: list[QPushButton] = []
        self._nav_icon_names: list[str] = []
        for label_key, _ckey, tip_key, icon_name, index, _shortcut_hint, _perm in NAV_SPECS:
            btn = QPushButton(_esc_amp(tr(label_key)))
            btn.setProperty("class", "NavTabButton")
            btn.setFocusPolicy(Qt.TabFocus)
            btn.setToolTip(tr(tip_key))
            btn.setCursor(Qt.PointingHandCursor)
            btn.setIconSize(QSize(14, 14))
            btn.clicked.connect(lambda _c=False, idx=index: self.switch_view(idx))
            attach_button_feedback(btn)
            self.nav_buttons.append(btn)
            self._nav_icon_names.append(icon_name)
            if index <= 3:
                nav_layout.addWidget(btn)

        nav_layout.addStretch(1)

        # Right Admin Utilities & Overflow Control
        for idx in (4, 5):
            nav_layout.addWidget(self.nav_buttons[idx])

        self.nav_overflow_btn = QPushButton()
        self.nav_overflow_btn.setObjectName("NavOverflowBtn")
        self.nav_overflow_btn.setFixedHeight(34)
        self.nav_overflow_btn.setCursor(Qt.PointingHandCursor)
        self.nav_overflow_btn.setIcon(get_svg_icon("more_horiz", "#202820", 15))
        self.nav_overflow_btn.setIconSize(QSize(15, 15))
        self.nav_overflow_btn.clicked.connect(self._show_nav_overflow_menu)
        attach_button_feedback(self.nav_overflow_btn)
        self.nav_overflow_btn.hide()
        nav_layout.addWidget(self.nav_overflow_btn)

        self.nav_overflow_menu = QMenu(self)
        self.nav_overflow_menu.setObjectName("NavOverflowMenu")

        # Compact User Profile Control + Native Account Menu
        self.profile_btn = QPushButton()
        self.profile_btn.setObjectName("UserProfileBtn")
        self.profile_btn.setCursor(Qt.PointingHandCursor)
        self.profile_btn.setFocusPolicy(Qt.TabFocus)
        self.profile_btn.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        self.profile_btn.setFixedHeight(38)
        prof_lay = QHBoxLayout(self.profile_btn)
        prof_lay.setSizeConstraint(QHBoxLayout.SetMinimumSize)
        prof_lay.setContentsMargins(6, 4, 10, 4)
        prof_lay.setSpacing(7)

        self.profile_avatar_lbl = QLabel("CA")
        self.profile_avatar_lbl.setObjectName("ProfileAvatarCircle")
        self.profile_avatar_lbl.setFixedSize(28, 28)
        self.profile_avatar_lbl.setAlignment(Qt.AlignCenter)
        self.profile_avatar_lbl.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        self.session_user_lbl = QLabel("")
        self.session_user_lbl.setStyleSheet("font-size: 12px; font-weight: 700; color: #202820;")
        self.session_user_lbl.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        self.session_role_lbl = QLabel("")
        self.session_role_lbl.setObjectName("ProfileRolePill")
        self.session_role_lbl.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        self.profile_chevron_lbl = QLabel()
        self.profile_chevron_lbl.setPixmap(get_svg_icon("chevron_down", "#5E6B5F", 13).pixmap(13, 13))
        self.profile_chevron_lbl.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        prof_lay.addWidget(self.profile_avatar_lbl)
        prof_lay.addWidget(self.session_user_lbl)
        prof_lay.addWidget(self.session_role_lbl)
        prof_lay.addWidget(self.profile_chevron_lbl)
        self.profile_btn.clicked.connect(self._show_profile_menu)
        attach_button_feedback(self.profile_btn)
        nav_layout.addWidget(self.profile_btn)

        self.profile_menu = QMenu(self)
        self.profile_menu.setObjectName("UserProfileMenu")
        self.menu_identity_action = QAction("", self.profile_menu)
        self.menu_identity_action.setEnabled(False)

        # Language submenu (per-user preference for both Cashier and Admin)
        self.lang_submenu = QMenu("", self.profile_menu)
        self.lang_submenu.setIcon(get_svg_icon("globe", "#202820", 14))
        self._lang_action_group = QActionGroup(self.lang_submenu)
        self._lang_action_group.setExclusive(True)
        self.menu_lang_en_action = QAction("English", self.lang_submenu)
        self.menu_lang_en_action.setCheckable(True)
        self.menu_lang_en_action.triggered.connect(lambda: self._on_user_select_language("en"))
        self.menu_lang_ar_action = QAction("العربية", self.lang_submenu)
        self.menu_lang_ar_action.setCheckable(True)
        self.menu_lang_ar_action.triggered.connect(lambda: self._on_user_select_language("ar-EG"))
        self._lang_action_group.addAction(self.menu_lang_en_action)
        self._lang_action_group.addAction(self.menu_lang_ar_action)
        self.lang_submenu.addAction(self.menu_lang_en_action)
        self.lang_submenu.addAction(self.menu_lang_ar_action)

        self.menu_display_prefs_action = QAction("", self.profile_menu)
        self.menu_display_prefs_action.setIcon(get_svg_icon("monitor", "#202820", 14))
        self.menu_display_prefs_action.triggered.connect(self._open_display_preferences)

        self.menu_shift_action = QAction("", self.profile_menu)
        self.menu_shift_action.setIcon(get_svg_icon("sales", "#202820", 14))
        self.menu_shift_action.triggered.connect(lambda: self.register_view.open_shift_dialog())

        self.menu_fullscreen_action = QAction("", self.profile_menu)
        self.menu_fullscreen_action.setIcon(get_svg_icon("maximize", "#202820", 14))
        self.menu_fullscreen_action.triggered.connect(self.toggle_fullscreen)

        self.menu_about_action = QAction("", self.profile_menu)
        self.menu_about_action.setIcon(get_svg_icon("info", "#202820", 14))
        self.menu_about_action.triggered.connect(self._open_about_dialog)

        self.menu_lock_action = QAction("", self.profile_menu)
        self.menu_lock_action.setIcon(get_svg_icon("lock", "#202820", 14))
        self.menu_lock_action.triggered.connect(lambda: self.logout(reason="manual"))
        self.menu_logout_action = QAction("", self.profile_menu)
        self.menu_logout_action.setIcon(get_svg_icon("logout", "#B23A3A", 14))
        self.menu_logout_action.triggered.connect(lambda: self.logout(reason="manual"))
        self.profile_menu.addAction(self.menu_identity_action)
        self.profile_menu.addSeparator()
        self.profile_menu.addMenu(self.lang_submenu)
        self.profile_menu.addAction(self.menu_display_prefs_action)
        self.profile_menu.addAction(self.menu_shift_action)
        self.profile_menu.addAction(self.menu_fullscreen_action)
        self.profile_menu.addAction(self.menu_about_action)
        self.profile_menu.addSeparator()
        self.profile_menu.addAction(self.menu_lock_action)
        self.profile_menu.addAction(self.menu_logout_action)

        ws_layout.addWidget(nav_bar)

        # Stacked Views (0: Register, 1: Products, 2: Sales, 3: Reports, 4: Users, 5: Settings)
        self.stack = AnimatedStackedWidget()
        self.register_view = RegisterView(self.db, self.config)
        self.products_view = ProductsView(self.db, self.config)
        self.sales_view = SalesView(self.db, self.config)
        self.reports_view = ReportsView(self.db, self.config)
        self.users_view = UsersView(self.db, self.config)
        self.settings_view = SettingsView(self.db, self.config)

        self.stack.addWidget(self.register_view)
        self.stack.addWidget(self.products_view)
        self.stack.addWidget(self.sales_view)
        self.stack.addWidget(self.reports_view)
        self.stack.addWidget(self.users_view)
        self.stack.addWidget(self.settings_view)
        ws_layout.addWidget(self.stack, 1)

        self.root_stack.addWidget(self.workspace_widget)
        outer_layout.addWidget(self.root_stack, 1)

        self.setCentralWidget(central)
        self.statusBar().showMessage(
            tr("main.status.sign_in_required", path=self.db.db_path)
        )

        self.toast = ToastBanner(self)

        # Wire Signals
        self.register_view.order_completed.connect(self._on_order_completed)
        self.register_view.status_message.connect(self._show_status)
        self.register_view.low_stock_clicked.connect(self._open_inventory_low_stock)
        self.register_view.shift_changed.connect(self._on_shift_changed)

        self.products_view.products_changed.connect(self._on_products_changed)
        self.products_view.categories_changed.connect(self._on_products_changed)
        self.products_view.inventory_changed.connect(self._on_inventory_changed)
        self.products_view.status_message.connect(self._show_status)

        self.sales_view.refund_completed.connect(self._on_refund_completed)
        self.sales_view.status_message.connect(self._show_status)
        self.reports_view.status_message.connect(self._show_status)

        self.users_view.users_changed.connect(self._on_users_changed)
        self.users_view.status_message.connect(self._show_status)

        self.settings_view.settings_saved.connect(self._on_settings_saved)
        self.settings_view.language_changed.connect(
            lambda lang: self.switch_language(lang, persist_store=True)
        )
        self.settings_view.data_restored.connect(self._on_data_restored)
        self.settings_view.reopen_wizard_requested.connect(
            lambda: self.run_setup_wizard(is_first_launch=False)
        )
        self.settings_view.status_message.connect(self._show_status)

    def _register_shortcuts(self) -> None:
        QShortcut(QKeySequence("F1"), self).activated.connect(lambda: self.switch_view(0))
        QShortcut(QKeySequence("F2"), self).activated.connect(lambda: self.switch_view(1))
        QShortcut(QKeySequence("F3"), self).activated.connect(lambda: self.switch_view(2))
        QShortcut(QKeySequence("F4"), self).activated.connect(lambda: self.switch_view(3))
        QShortcut(QKeySequence("F5"), self).activated.connect(lambda: self.switch_view(4))
        QShortcut(QKeySequence("F6"), self).activated.connect(lambda: self.switch_view(5))
        QShortcut(QKeySequence("F11"), self).activated.connect(self.toggle_fullscreen)
        QShortcut(QKeySequence("F12"), self).activated.connect(self._on_f12_shortcut)

        esc_fs = QShortcut(QKeySequence("Escape"), self)
        esc_fs.setContext(Qt.WidgetWithChildrenShortcut)
        esc_fs.activated.connect(self._on_escape_pressed)

        find_action = QAction(self)
        find_action.setShortcut(QKeySequence.Find)
        find_action.triggered.connect(self._focus_active_search)
        self.addAction(find_action)

        lock_action = QAction(self)
        lock_action.setShortcut(QKeySequence("Ctrl+L"))
        lock_action.triggered.connect(self._on_lock_shortcut)
        self.addAction(lock_action)

    def _install_activity_monitors(self) -> None:
        self.app.installEventFilter(self)
        self._auto_lock_timer = QTimer(self)
        self._auto_lock_timer.setInterval(15_000)
        self._auto_lock_timer.timeout.connect(self._check_auto_lock_timeout)
        self._auto_lock_timer.start()

        # Check scheduled automatic backups periodically while running (every 10 minutes)
        self._auto_backup_timer = QTimer(self)
        self._auto_backup_timer.setInterval(10 * 60 * 1000)
        self._auto_backup_timer.timeout.connect(self.check_and_run_due_auto_backup)
        self._auto_backup_timer.start()

    def eventFilter(self, watched, event) -> bool:
        if self.current_user is not None:
            et = event.type()
            if et in (
                QEvent.Type.MouseButtonPress,
                QEvent.Type.KeyPress,
                QEvent.Type.Wheel,
            ):
                self._last_activity_ts = time.monotonic()
        return super().eventFilter(watched, event)

    def closeEvent(self, event) -> None:
        self._persist_non_fullscreen_window_state()
        if hasattr(self, "_auto_lock_timer"):
            self._auto_lock_timer.stop()
        if hasattr(self, "_auto_backup_timer"):
            self._auto_backup_timer.stop()
        if hasattr(self, "toast") and hasattr(self.toast, "_timer"):
            self.toast._timer.stop()
        self.app.removeEventFilter(self)
        super().closeEvent(event)

    # ---------------- Window Geometry & Fullscreen Management ----------------

    def _restore_initial_window_geometry(self) -> None:
        """Set sensible normal window size clamped to available screen geometry."""
        pref_w = int(getattr(self.config, "window_width", 1280) or 1280)
        pref_h = int(getattr(self.config, "window_height", 800) or 800)
        screen = QGuiApplication.primaryScreen()
        is_offscreen = (QGuiApplication.platformName() or "").lower() == "offscreen"
        if screen is not None and not is_offscreen:
            avail = screen.availableGeometry()
            w = max(920, min(pref_w, max(920, avail.width() - 32)))
            h = max(560, min(pref_h, max(560, avail.height() - 32)))
            self.resize(w, h)
            x = avail.x() + max(0, (avail.width() - w) // 2)
            y = avail.y() + max(0, (avail.height() - h) // 2)
            self.move(x, y)
        else:
            self.resize(max(920, pref_w), max(560, pref_h))

    def apply_launch_window_state(self) -> None:
        """Apply preferred startup window state without ever trapping into fullscreen."""
        pref = str(getattr(self.config, "window_state_pref", "maximized") or "maximized")
        if pref == "normal":
            self._restore_initial_window_geometry()
            self.showNormal()
        else:
            self.showMaximized()

    def _persist_non_fullscreen_window_state(self) -> None:
        """Save whether window was maximized or normal (never save transient fullscreen)."""
        try:
            if self.isFullScreen():
                pref = "maximized" if self._pre_fullscreen_maximized else "normal"
                geom = self.normalGeometry()
            elif self.isMaximized():
                pref = "maximized"
                geom = self.normalGeometry()
            else:
                pref = "normal"
                geom = self.geometry()
            w = max(900, min(3840, int(geom.width() or self.width() or 1280)))
            h = max(520, min(2160, int(geom.height() or self.height() or 800)))
            self.config = self.db.save_window_state_pref(pref, w, h)
        except Exception:
            pass

    def toggle_fullscreen(self) -> None:
        """Toggle optional F11 immersive fullscreen with safe Escape exit."""
        if self.isFullScreen():
            self._exit_fullscreen()
        else:
            self._pre_fullscreen_maximized = self.isMaximized()
            self.showFullScreen()
            self._update_fullscreen_action_label()
            self._show_status(tr("main.status.fullscreen_hint"))

    def _exit_fullscreen(self) -> None:
        if not self.isFullScreen():
            return
        if self._pre_fullscreen_maximized:
            self.showMaximized()
        else:
            self.showNormal()
        self._update_fullscreen_action_label()

    def _on_escape_pressed(self) -> None:
        if self.isFullScreen():
            self._exit_fullscreen()

    def _update_fullscreen_action_label(self) -> None:
        if not hasattr(self, "menu_fullscreen_action"):
            return
        if self.isFullScreen():
            self.menu_fullscreen_action.setText(tr("nav.profile.exit_fullscreen"))
            self.menu_fullscreen_action.setIcon(get_svg_icon("minimize", "#202820", 14))
        else:
            self.menu_fullscreen_action.setText(tr("nav.profile.fullscreen"))
            self.menu_fullscreen_action.setIcon(get_svg_icon("maximize", "#202820", 14))

    def clamp_dialog_to_screen(self, dlg: QDialog) -> None:
        """Center dialog relative to main window and clamp size to available screen bounds."""
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return
        avail: QRect = screen.availableGeometry()
        max_w = max(360, avail.width() - 32)
        max_h = max(320, avail.height() - 36)
        cur = dlg.size()
        hint = dlg.sizeHint()
        w = min(max(cur.width(), min(hint.width(), max_w)), max_w)
        h = min(max(cur.height(), min(hint.height(), max_h)), max_h)
        dlg.resize(w, h)
        anchor = self.frameGeometry().center() if self.isVisible() else avail.center()
        x = max(avail.left() + 8, min(anchor.x() - w // 2, avail.right() - w - 8))
        y = max(avail.top() + 8, min(anchor.y() - h // 2, avail.bottom() - h - 8))
        dlg.move(x, y)

    def _check_auto_lock_timeout(self) -> None:
        self.trigger_inactivity_check()

    def trigger_inactivity_check(self, simulated_idle_seconds: float | None = None) -> bool:
        """Lock session if inactivity timeout exceeded; returns True if locked."""
        if self.current_user is None:
            return False
        timeout_mins = int(self.config.auto_lock_minutes or 0)
        if timeout_mins <= 0:
            return False
        elapsed = (
            float(simulated_idle_seconds)
            if simulated_idle_seconds is not None
            else (time.monotonic() - self._last_activity_ts)
        )
        if elapsed >= timeout_mins * 60:
            self.logout(reason="auto_lock")
            return True
        return False

    def check_and_run_due_auto_backup(self) -> None:
        try:
            created = run_due_auto_backup(self.db)
        except Exception:
            return
        if created is not None:
            self.config = self.db.get_settings()
            if self.current_user and self.current_user.is_admin:
                self.settings_view.sync_from_config(self.config)
                self._show_status(tr("main.status.auto_backup_saved", name=created.name))

    def _on_lock_shortcut(self) -> None:
        if self.current_user is not None:
            self.logout(reason="manual")

    def _focus_active_search(self) -> None:
        if self.current_user is None:
            return
        idx = self.stack.currentIndex()
        if idx == 0:
            self.register_view.focus_search()
        elif idx == 1 and has_permission(self.current_user, Permission.MANAGE_PRODUCTS):
            self.products_view.focus_search()
        elif idx == 2:
            self.sales_view.focus_search()
        elif idx == 4 and has_permission(self.current_user, Permission.MANAGE_USERS):
            self.users_view.focus_search()

    def _on_f12_shortcut(self) -> None:
        if self.current_user is not None and self.stack.currentIndex() == 0:
            self.register_view.trigger_checkout()

    def _close_open_modal_dialogs(self) -> None:
        for child in self.findChildren(QDialog):
            if child.isVisible():
                child.reject()

    def _show_login_gate(self, *, notice: str = "", notice_key: str | None = None) -> None:
        self.current_user = None
        self.db.set_session_actor(None)
        self._close_open_modal_dialogs()

        self.register_view.clear_session_state()
        self.products_view.set_current_user(None)
        self.sales_view.clear_session_state()
        self.reports_view.clear_session_state()
        self.users_view.clear_session_state()

        self.login_view.refresh_branding(self.config)
        has_notice = bool(notice_key or notice)
        self.login_view.reset_inputs(clear_notice=not has_notice)
        if has_notice:
            self.login_view.show_notice(notice, key=notice_key)
        self.root_stack.setCurrentIndex(0)
        self.login_view.focus_username()
        self.statusBar().showMessage(
            tr("main.status.locked", business=self.config.business_name)
        )

    def _on_user_authenticated(self, user: UserAccount) -> None:
        self.login_user(user)

    def _active_display_prefs(self) -> tuple[int, str, bool]:
        """Return (font_scale_pct, ui_density, reduced_motion) for active user or store defaults."""
        if self.current_user is not None:
            scale = int(getattr(self.current_user, "font_scale_pct", 100) or 100)
            density = str(getattr(self.current_user, "ui_density", "comfortable") or "comfortable")
            red_motion = self.current_user.effective_reduced_motion(self.config.reduced_motion)
            return scale, density, red_motion
        return (
            int(getattr(self.config, "default_font_scale_pct", 100) or 100),
            str(getattr(self.config, "default_ui_density", "comfortable") or "comfortable"),
            bool(self.config.reduced_motion),
        )

    def _apply_active_theme(self, lang: str | None = None) -> None:
        target_lang = normalize_language(lang or get_language())
        scale, density, red_motion = self._active_display_prefs()
        set_reduced_motion(red_motion)
        apply_theme(
            self.app,
            self.config,
            lang=target_lang,
            font_scale_pct=scale,
            density=density,
        )
        # Keep header nav bar and profile pill sized comfortably across 85%–140% scale
        scale_f = max(0.85, min(1.40, scale / 100.0))
        d_mult = 0.92 if density == "compact" else 1.0
        nav_h = max(52, int(round(58 * max(1.0, scale_f * 0.90) * d_mult)))
        if hasattr(self, "_nav_bar"):
            self._nav_bar.setFixedHeight(nav_h)
        if hasattr(self, "profile_btn"):
            self.profile_btn.setFixedHeight(max(36, int(round(38 * max(1.0, scale_f * 0.92)))))

    def _on_user_select_language(self, lang: str) -> None:
        target = normalize_language(lang)
        if self.current_user is not None:
            try:
                updated = self.db.update_my_preferences(preferred_language=target)
                self.current_user = updated
                self.db.set_session_actor(updated)
            except Exception:
                pass
        self.switch_language(target, persist_store=False)

    def _open_display_preferences(self) -> None:
        if self.current_user is None:
            return
        dlg = DisplayPreferencesDialog(self.db, self.current_user, self.config, parent=self)
        self.clamp_dialog_to_screen(dlg)
        if dlg.exec() == QDialog.Accepted and dlg.updated_user is not None:
            self.current_user = dlg.updated_user
            self.db.set_session_actor(self.current_user)
            self._apply_active_theme(get_language())
            self._update_session_header(self.current_user)
            self._show_status(tr("display_prefs.saved_status"))

    def _open_about_dialog(self) -> None:
        dlg = AboutDialog(self.config, parent=self)
        self.clamp_dialog_to_screen(dlg)
        dlg.exec()

    def login_user(self, user: UserAccount) -> None:
        """Activate an authenticated user session and configure RBAC workspace tabs."""
        self.current_user = user
        self.db.set_session_actor(user)
        self._last_activity_ts = time.monotonic()

        target_lang = normalize_language(user.preferred_language or self.config.app_language)
        if target_lang != get_language() or self.app.layoutDirection() != (
            Qt.RightToLeft if target_lang == "ar-EG" else Qt.LeftToRight
        ):
            self.switch_language(target_lang, persist_store=False)
        else:
            self._apply_active_theme(target_lang)

        self._update_session_header(user)
        self.root_stack.setCurrentIndex(1)

        self.register_view.clear_session_state()
        self.register_view.refresh_operational_badges()
        self.products_view.set_current_user(user)
        self.sales_view.set_current_user(user)
        if user.is_admin:
            self.products_view.refresh()
            self.reports_view.refresh()
            self.users_view.refresh()
            self.settings_view.sync_from_config(self.config)

        self._refresh_order_chip()
        self.switch_view(0)
        self.register_view.search_input.setFocus()
        self._show_status(
            tr(
                "main.status.signed_in",
                name=user.label,
                role=tr_role(user.role),
            )
        )

    def logout(self, *, reason: str = "manual") -> None:
        prev_user = self.current_user
        if prev_user is not None:
            action = "AUTH_AUTO_LOCK" if reason == "auto_lock" else "AUTH_LOGOUT"
            details = (
                "Session locked automatically after inactivity timeout"
                if reason == "auto_lock"
                else "User signed out"
            )
            try:
                self.db.record_audit_event(
                    action,
                    actor=prev_user,
                    target_type="user",
                    target_id=str(prev_user.id),
                    details=details,
                )
            except Exception:
                pass

        store_lang = normalize_language(self.config.app_language)
        if reason == "auto_lock":
            notice_key = "login.notice.auto_lock"
        elif reason == "restore":
            notice_key = "login.notice.restore"
        else:
            notice_key = "login.notice.manual_lock"
        self._show_login_gate(notice="", notice_key=notice_key)
        if store_lang != get_language():
            self.switch_language(store_lang, persist_store=False)
        else:
            self._apply_active_theme(store_lang)
            self.retranslate_ui()

    @staticmethod
    def _compute_initials(label: str) -> str:
        parts = [p for p in (label or "").strip().split() if p]
        if len(parts) >= 2:
            return (parts[0][0] + parts[1][0]).upper()
        if parts:
            return parts[0][:2].upper()
        return "U"

    def _popup_pos_under(self, btn: QPushButton, menu: QMenu) -> QPoint:
        menu.adjustSize()
        mw = max(menu.sizeHint().width(), btn.width())
        if self.layoutDirection() == Qt.RightToLeft:
            return btn.mapToGlobal(QPoint(0, btn.height() + 4))
        return btn.mapToGlobal(QPoint(btn.width() - mw, btn.height() + 4))

    def _show_profile_menu(self) -> None:
        if self.current_user is None:
            return
        self.profile_menu.exec(self._popup_pos_under(self.profile_btn, self.profile_menu))

    def _show_nav_overflow_menu(self) -> None:
        if not self.nav_overflow_menu.actions():
            return
        self.nav_overflow_menu.exec(
            self._popup_pos_under(self.nav_overflow_btn, self.nav_overflow_menu)
        )

    def _update_session_header(self, user: UserAccount) -> None:
        fm = self.session_user_lbl.fontMetrics()
        max_name_w = 105 if self.width() < 980 else 155
        elided = fm.elidedText(user.label, Qt.ElideRight, max_name_w)
        self.session_user_lbl.setText(elided)
        self.profile_avatar_lbl.setText(self._compute_initials(user.label))

        role_short = tr_role(user.role)
        role_long = tr_role_long(user.role)
        self.session_role_lbl.setText(role_short)
        self.session_role_lbl.setProperty("admin", "true" if user.is_admin else "false")
        self.session_role_lbl.style().unpolish(self.session_role_lbl)
        self.session_role_lbl.style().polish(self.session_role_lbl)

        self.profile_btn.setToolTip(f"{user.label} (@{user.username}) · {role_long}")
        self.menu_identity_action.setText(
            tr("nav.profile.signed_in_as", name=user.label, role=role_short)
        )
        self.lang_submenu.setTitle(tr("nav.profile.language"))
        cur_lang = get_language()
        self.menu_lang_en_action.setChecked(cur_lang == "en")
        self.menu_lang_ar_action.setChecked(cur_lang == "ar-EG")
        self.menu_display_prefs_action.setText(tr("nav.profile.display_prefs"))
        self.menu_shift_action.setText(tr("nav.profile.manage_shift"))
        self._update_fullscreen_action_label()
        self.menu_about_action.setText(tr("nav.profile.about"))
        self.menu_lock_action.setText(tr("nav.profile.lock_session"))
        self.menu_logout_action.setText(tr("nav.profile.logout"))
        self.profile_btn.updateGeometry()

        self._update_responsive_header()

    def _refresh_nav_icons(self, active_index: int) -> None:
        on_primary = contrast_text_hex(self.config.accent_color)
        for i, btn in enumerate(self.nav_buttons):
            is_active = i == active_index
            btn.setProperty("active", "true" if is_active else "false")
            icon_color = on_primary if is_active else "#5E6B5F"
            btn.setIcon(get_svg_icon(self._nav_icon_names[i], icon_color, 14))
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _refresh_order_chip(self) -> None:
        total_orders = self.db.get_order_count()
        self.register_view.update_order_count(total_orders)

    def switch_view(self, index: int) -> None:
        if self.current_user is None:
            return
        if not (0 <= index < len(NAV_SPECS)):
            return
        required_perm = NAV_SPECS[index][6]
        if not has_permission(self.current_user, required_perm):
            self._show_status(tr("main.status.admin_only_section"))
            return

        if (
            self.stack.currentIndex() == 5
            and index != 5
            and self.isVisible()
            and self.settings_view.has_unsaved_changes()
        ):
            decision = self.settings_view.prompt_unsaved_changes()
            if decision == "cancel":
                return

        if hasattr(self, "toast") and self.stack.currentIndex() != index:
            self.toast.hide()

        self.stack.setCurrentIndex(index)
        self._refresh_nav_icons(index)

        if index == 0:
            self.register_view.refresh_held_count()
        elif index == 1:
            self.products_view.refresh()
        elif index == 2:
            self.sales_view.refresh()
        elif index == 3:
            self.reports_view.refresh()
        elif index == 4:
            self.users_view.refresh()
        elif index == 5:
            self.settings_view.sync_from_config(self.config)

    def _update_responsive_header(self) -> None:
        w = max(900, self.width())
        compact = w < 1080
        very_compact = w < 980
        mode_key = "very_compact" if very_compact else ("compact" if compact else "normal")

        if mode_key != self._last_header_compact_mode:
            self._last_header_compact_mode = mode_key
            if compact:
                self._nav_layout.setContentsMargins(12, 6, 12, 6)
                self._nav_layout.setSpacing(6)
                btn_style = "padding: 6px 10px;"
            else:
                self._nav_layout.setContentsMargins(16, 6, 16, 6)
                self._nav_layout.setSpacing(8)
                btn_style = "padding: 6px 13px;"

            for idx, (label_key, _compact_key, tip_key, *_rest) in enumerate(NAV_SPECS):
                btn = self.nav_buttons[idx]
                btn_txt = _esc_amp(tr(label_key))
                if btn.text() != btn_txt:
                    btn.setText(btn_txt)
                btn.setToolTip(tr(tip_key))
                btn.setStyleSheet(btn_style)

        overflow_indices: list[int] = []
        if compact and self.current_user is not None:
            allowed_count = sum(
                1 for spec in NAV_SPECS if has_permission(self.current_user, spec[6])
            )
            if allowed_count > 3:
                # Overflow secondary Admin tabs smoothly at narrow window widths without hiding Cashier's 2nd tab
                candidate_overflow = [2, 3, 4, 5] if very_compact else [3, 4, 5]
                for idx in candidate_overflow:
                    if has_permission(self.current_user, NAV_SPECS[idx][6]):
                        overflow_indices.append(idx)

        self.nav_overflow_menu.clear()
        for idx, (label_key, _ckey, tip_key, icon_name, *_rest, perm) in enumerate(NAV_SPECS):
            allowed = self.current_user is not None and has_permission(self.current_user, perm)
            in_overflow = idx in overflow_indices
            self.nav_buttons[idx].setVisible(allowed and not in_overflow)
            if in_overflow:
                act = QAction(tr(label_key), self.nav_overflow_menu)
                act.setIcon(get_svg_icon(icon_name, "#202820", 14))
                act.setToolTip(tr(tip_key))
                act.triggered.connect(lambda _c=False, i=idx: self.switch_view(i))
                self.nav_overflow_menu.addAction(act)

        self.nav_overflow_btn.setVisible(bool(overflow_indices))
        self.nav_overflow_btn.setToolTip(tr("nav.more_tip"))

        brand_max_w = 195 if very_compact else (225 if compact else 290)
        if self.brand_title_lbl.maximumWidth() != brand_max_w:
            self.brand_title_lbl.setMaximumWidth(brand_max_w)
        fm = self.brand_title_lbl.fontMetrics()
        elided_biz = fm.elidedText(self.config.business_name, Qt.ElideRight, brand_max_w - 4)
        if self.brand_title_lbl.text() != elided_biz:
            self.brand_title_lbl.setText(elided_biz)
        self.brand_title_lbl.setToolTip(self.config.business_name)

        date_str = format_header_date()
        sub_txt = date_str if very_compact else f"{date_str} · {self.config.currency}"
        if self.brand_sub_lbl.text() != sub_txt:
            self.brand_sub_lbl.setText(sub_txt)

    def switch_language(self, lang: str, *, persist_store: bool = False) -> None:
        """Switch UI language and RTL/LTR direction live without restarting or losing state."""
        target = normalize_language(lang)
        set_language(target)
        QLocale.setDefault(qt_locale(target))

        if persist_store:
            self.config.app_language = target
            can_persist = self.current_user is None or has_permission(
                self.current_user, Permission.MANAGE_SETTINGS
            )
            if can_persist:
                try:
                    self.db.set_app_language(target)
                    self.config = self.db.get_settings()
                except Exception:
                    pass

        if hasattr(self, "toast"):
            self.toast.hide()
        self._apply_active_theme(target)
        self.retranslate_ui()

    def retranslate_ui(self) -> None:
        self.setWindowTitle(tr("main.window_title", business=self.config.business_name))
        self._last_header_compact_mode = ""
        self._update_responsive_header()
        self._refresh_order_chip()

        self.nav_overflow_btn.setToolTip(tr("nav.more_tip"))
        self.lang_submenu.setTitle(tr("nav.profile.language"))
        self.menu_lang_en_action.setChecked(get_language() == "en")
        self.menu_lang_ar_action.setChecked(get_language() == "ar-EG")
        self.menu_display_prefs_action.setText(tr("nav.profile.display_prefs"))
        self.menu_shift_action.setText(tr("nav.profile.manage_shift"))
        self._update_fullscreen_action_label()
        self.menu_about_action.setText(tr("nav.profile.about"))
        self.menu_lock_action.setText(tr("nav.profile.lock_session"))
        self.menu_logout_action.setText(tr("nav.profile.logout"))

        self.login_view.retranslate_ui()
        self.register_view.retranslate_ui()
        self.products_view.retranslate_ui()
        self.sales_view.retranslate_ui()
        self.reports_view.retranslate_ui()
        self.users_view.retranslate_ui()
        self.settings_view.retranslate_ui()

        if self.current_user is not None:
            self._update_session_header(self.current_user)
            self.statusBar().showMessage(
                tr(
                    "main.status.signed_in",
                    name=self.current_user.label,
                    role=tr_role(self.current_user.role),
                )
            )
        else:
            self.statusBar().showMessage(
                tr("main.status.locked", business=self.config.business_name)
            )

        for dlg in self.findChildren(QDialog):
            if dlg.isVisible() and hasattr(dlg, "retranslate_ui"):
                dlg.retranslate_ui()

        if hasattr(self, "toast") and self.toast.isVisible():
            self.toast.reposition()

    def _apply_branding(self) -> None:
        self._apply_active_theme(get_language())
        self.setWindowTitle(tr("main.window_title", business=self.config.business_name))
        self.logo_label.setPixmap(scaled_logo_pixmap(self.config, 26, 26))
        self._last_header_compact_mode = ""
        self._update_responsive_header()

        self.login_view.refresh_branding(self.config)
        self._refresh_order_chip()
        self._refresh_nav_icons(self.stack.currentIndex())
        self.register_view.set_config(self.config)
        self.products_view.set_config(self.config)
        self.sales_view.set_config(self.config)
        if self.current_user and self.current_user.is_admin:
            self.reports_view.set_config(self.config)
            self.users_view.set_config(self.config)
            self.settings_view.sync_from_config(self.config)
        if self.current_user is not None:
            self._update_session_header(self.current_user)

    def _show_status(self, message: str) -> None:
        self.statusBar().showMessage(message, 5000)
        if message:
            self.toast.show_message(message)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "_nav_layout"):
            self._update_responsive_header()
        if hasattr(self, "toast") and self.toast.isVisible():
            self.toast.reposition()

    def _on_products_changed(self) -> None:
        self.register_view.refresh_catalog()
        self.register_view.sync_cart_with_catalog()
        self.register_view.refresh_operational_badges()
        if self.current_user and self.current_user.is_admin:
            self.reports_view.refresh()
            self.settings_view.refresh_cache_stats()

    def _open_inventory_low_stock(self) -> None:
        if self.current_user is None:
            return
        if not (
            has_permission(self.current_user, Permission.MANAGE_PRODUCTS)
            or has_permission(self.current_user, Permission.MANAGE_INVENTORY)
        ):
            return
        self.switch_view(1)
        self.products_view.open_low_stock_filter()

    def _on_shift_changed(self) -> None:
        if self.current_user and self.current_user.is_admin:
            self.reports_view.shifts_tab.refresh()
            self.reports_view.refresh()

    def _on_inventory_changed(self) -> None:
        self.register_view.refresh_operational_badges()
        if self.current_user and self.current_user.is_admin:
            self.reports_view.refresh()

    def _on_refund_completed(self) -> None:
        self.register_view.refresh_operational_badges()
        self.products_view.inventory_view.refresh()
        if self.current_user and self.current_user.is_admin:
            self.reports_view.refresh()

    def _on_users_changed(self) -> None:
        if self.current_user is not None:
            fresh = self.db.list_users()
            for u in fresh:
                if u.id == self.current_user.id:
                    if not u.is_active or not u.is_admin:
                        self.logout(reason="manual")
                        return
                    self.current_user = u
                    self.db.set_session_actor(u)
                    target_lang = normalize_language(u.preferred_language or self.config.app_language)
                    if target_lang != get_language():
                        self.switch_language(target_lang, persist_store=False)
                    else:
                        self._apply_active_theme(target_lang)
                    self._update_session_header(u)
                    break
        self.sales_view.set_current_user(self.current_user)

    def _on_order_completed(self, order: OrderRecord) -> None:
        self._refresh_order_chip()
        self.register_view.refresh_operational_badges()
        self.sales_view.refresh()
        if self.current_user and self.current_user.is_admin:
            self.products_view.inventory_view.refresh()
            self.reports_view.refresh()

        if getattr(self.config, "auto_print_receipt", False):
            try:
                ok, info_msg = print_receipt_to_printer(order, self.config)
                if not ok:
                    self._show_status(tr("receipt.err.auto_print_failed", msg=info_msg))
            except Exception as exc:
                self._show_status(tr("receipt.err.auto_print_failed", msg=exc))

        auto_saved_path: Path | None = None
        if getattr(self.config, "auto_save_receipt_pdf", False):
            rcp_code = order.receipt_number or order.order_number
            safe_rcp = "".join(
                ch if (ch.isalnum() or ch in ("-", "_")) else "_" for ch in rcp_code
            ) or "receipt"
            try:
                export_dir = get_receipts_dir(getattr(self.config, "receipt_export_dir", ""))
                candidate = export_dir / f"Receipt-{safe_rcp}.pdf"
                export_receipt_pdf(candidate, order, self.config)
                if candidate.is_file() and candidate.stat().st_size > 0:
                    auto_saved_path = candidate
            except (OSError, RuntimeError):
                auto_saved_path = None

        dlg = ReceiptDialog(
            order,
            self.config,
            auto_saved_path=auto_saved_path,
            parent=self,
        )
        self.clamp_dialog_to_screen(dlg)
        dlg.exec()

    def _on_settings_saved(self, new_config: BusinessConfig) -> None:
        self.config = new_config
        target_lang = normalize_language(new_config.app_language)
        if target_lang != get_language():
            set_language(target_lang)
            QLocale.setDefault(qt_locale(target_lang))
        self._apply_branding()
        self.retranslate_ui()

    def _on_data_restored(self) -> None:
        self.config = self.db.get_settings()
        target_lang = normalize_language(self.config.app_language)
        set_language(target_lang)
        QLocale.setDefault(qt_locale(target_lang))
        self.register_view.clear_session_state()
        self._apply_branding()
        self.retranslate_ui()

        if not self.db.has_admin():
            # E.g. factory reset or restored a legacy v1/v2 backup with no users table yet
            self._show_login_gate(
                notice="", notice_key="login.notice.setup_required"
            )
            if not self.ensure_initial_admin_ready():
                self.close()
            return

        self.logout(reason="restore")

    def ensure_initial_admin_ready(self) -> bool:
        """Prompt for initial Admin + store setup if no active admin exists; returns False if cancelled."""
        while not self.db.has_admin():
            self.config = self.db.get_settings()
            dlg = SetupDialog(
                self.config,
                is_first_launch=(not self.config.setup_completed),
                require_admin_setup=True,
                parent=self,
            )
            dlg.language_changed.connect(
                lambda lang: self.switch_language(lang, persist_store=False)
            )
            if dlg.exec() != QDialog.Accepted or dlg.result_config is None:
                return False

            try:
                saved_cfg, admin_user = self.db.initialize_owner_and_business(
                    business_cfg=dlg.result_config,
                    admin_username=dlg.result_admin_username,
                    admin_password=dlg.result_admin_password,
                    admin_display_name=dlg.result_admin_display_name,
                )
            except Exception as exc:
                self._show_status(tr("main.status.setup_error", exc=exc))
                continue

            self.config = saved_cfg
            target_lang = normalize_language(saved_cfg.app_language)
            set_language(target_lang)
            QLocale.setDefault(qt_locale(target_lang))
            self._apply_branding()
            self.retranslate_ui()
            self.login_user(admin_user)
            return True

        return True

    def run_setup_wizard(self, is_first_launch: bool = True) -> None:
        if self.db.has_admin():
            if self.current_user is None or not has_permission(
                self.current_user, Permission.MANAGE_SETTINGS
            ):
                self._show_status(tr("main.status.admin_only_wizard"))
                return
            prev_lang = get_language()
            dlg = SetupDialog(
                self.config,
                is_first_launch=is_first_launch,
                require_admin_setup=False,
                parent=self,
            )
            dlg.language_changed.connect(
                lambda lang: self.switch_language(lang, persist_store=False)
            )
            if dlg.exec() == QDialog.Accepted and dlg.result_config is not None:
                self.config = self.db.save_settings(dlg.result_config)
                target_lang = normalize_language(self.config.app_language)
                set_language(target_lang)
                QLocale.setDefault(qt_locale(target_lang))
                self._apply_branding()
                self.retranslate_ui()
                self._show_status(tr("main.status.profile_saved", name=self.config.business_name))
            elif get_language() != prev_lang:
                self.switch_language(prev_lang, persist_store=False)
        else:
            self.ensure_initial_admin_ready()
