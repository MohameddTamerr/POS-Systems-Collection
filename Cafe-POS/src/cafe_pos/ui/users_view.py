from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..auth import (
    AuthorizationError,
    ROLE_ADMIN,
    ROLE_CASHIER,
    UserAccount,
    validate_display_name,
    validate_password_policy,
    validate_username,
)
from ..config import BusinessConfig
from ..db import Database
from ..icons import get_svg_icon
from ..i18n import layout_direction, tr, tr_audit_action, tr_count, tr_role, tr_role_long
from ..receipts import format_local_timestamp
from ..theme import contrast_text_hex
from .animations import animate_window_fade_in, attach_button_feedback
from .widgets import EmptyStateWidget, SearchLineEdit


def _esc_amp(text: str) -> str:
    return (text or "").replace("&", "&&")


class UserEditorDialog(QDialog):
    """Modal dialog for Administrators to create or edit a staff user account."""

    def __init__(
        self,
        config: BusinessConfig,
        *,
        user: UserAccount | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.config = config
        self._editing_user = user
        self._did_animate_in = False
        self._pw_toggles: list[tuple[QLineEdit, QPushButton]] = []
        self._err_key: str | None = None
        self._err_raw: str = ""

        self.setModal(True)
        self.setMinimumWidth(430)
        self.resize(470, 445 if user is None else 340)

        self.result_username: str = ""
        self.result_display_name: str = ""
        self.result_role: str = ROLE_CASHIER
        self.result_password: str = ""
        self.result_preferred_language: str = user.preferred_language if user else ""

        self._build_ui(user)
        self.retranslate_ui()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._did_animate_in:
            self._did_animate_in = True
            animate_window_fade_in(self)

    def _build_ui(self, user: UserAccount | None) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        card = QFrame()
        card.setProperty("class", "BoutiqueCard")
        form = QFormLayout(card)
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        form.setContentsMargins(16, 16, 16, 16)
        form.setSpacing(11)

        self._lbl_username = QLabel("")
        self.username_edit = QLineEdit(user.username if user else "")
        self.username_edit.setMaxLength(32)
        form.addRow(self._lbl_username, self.username_edit)

        self._lbl_display = QLabel("")
        self.display_edit = QLineEdit(user.display_name if user else "")
        self.display_edit.setMaxLength(60)
        form.addRow(self._lbl_display, self.display_edit)

        self._lbl_role = QLabel("")
        self.role_combo = QComboBox()
        self.role_combo.addItem("", ROLE_CASHIER)
        self.role_combo.addItem("", ROLE_ADMIN)
        if user:
            idx = self.role_combo.findData(user.role)
            if idx >= 0:
                self.role_combo.setCurrentIndex(idx)
        form.addRow(self._lbl_role, self.role_combo)

        self._lbl_lang = QLabel("")
        self.lang_combo = QComboBox()
        self.lang_combo.addItem("", "")
        self.lang_combo.addItem("", "en")
        self.lang_combo.addItem("", "ar-EG")
        lang_idx = self.lang_combo.findData(user.preferred_language if user else "")
        self.lang_combo.setCurrentIndex(lang_idx if lang_idx >= 0 else 0)
        form.addRow(self._lbl_lang, self.lang_combo)

        self.pw_edit: QLineEdit | None = None
        self.confirm_edit: QLineEdit | None = None
        self._lbl_pw: QLabel | None = None
        self._lbl_confirm: QLabel | None = None
        if user is None:
            self._lbl_pw = QLabel("")
            self._lbl_confirm = QLabel("")
            pw_wrap, self.pw_edit = self._make_pw_row()
            cf_wrap, self.confirm_edit = self._make_pw_row()
            form.addRow(self._lbl_pw, pw_wrap)
            form.addRow(self._lbl_confirm, cf_wrap)

        root.addWidget(card)

        self.error_lbl = QLabel("")
        self.error_lbl.setProperty("class", "ValidationHint")
        self.error_lbl.setWordWrap(True)
        self.error_lbl.hide()
        root.addWidget(self.error_lbl)

        btn_row = QHBoxLayout()
        self.cancel_btn = QPushButton("")
        self.cancel_btn.clicked.connect(self.reject)
        self.save_btn = QPushButton("")
        self.save_btn.setProperty("role", "primary")
        self.save_btn.setDefault(True)
        self.save_btn.setIcon(
            get_svg_icon("check", contrast_text_hex(self.config.accent_color), 14)
        )
        self.save_btn.setIconSize(QSize(14, 14))
        self.save_btn.clicked.connect(self._on_save)
        attach_button_feedback(self.cancel_btn)
        attach_button_feedback(self.save_btn)

        btn_row.addStretch(1)
        btn_row.addWidget(self.cancel_btn)
        btn_row.addWidget(self.save_btn)
        root.addLayout(btn_row)

    def _make_pw_row(self) -> tuple[QWidget, QLineEdit]:
        wrap = QWidget()
        row = QHBoxLayout(wrap)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        edit = QLineEdit()
        edit.setEchoMode(QLineEdit.Password)
        edit.setMaxLength(128)
        btn = QPushButton(_esc_amp(tr("common.show")))
        btn.setMinimumWidth(64)
        btn.setStyleSheet("padding: 5px 8px; font-size: 12px;")

        def _toggle() -> None:
            hidden = edit.echoMode() == QLineEdit.Password
            edit.setEchoMode(QLineEdit.Normal if hidden else QLineEdit.Password)
            btn.setText(_esc_amp(tr("common.hide" if hidden else "common.show")))

        btn.clicked.connect(_toggle)
        self._pw_toggles.append((edit, btn))
        row.addWidget(edit, 1)
        row.addWidget(btn)
        return wrap, edit

    def retranslate_ui(self) -> None:
        self.setLayoutDirection(layout_direction())
        user = self._editing_user
        self.setWindowTitle(
            tr("user_editor.win_edit") if user else tr("user_editor.win_add")
        )
        self._lbl_username.setText(tr("user_editor.username_label"))
        self.username_edit.setPlaceholderText(tr("user_editor.username_ph"))
        self._lbl_display.setText(tr("user_editor.display_opt_label"))
        self.display_edit.setPlaceholderText(tr("user_editor.display_ph"))
        self._lbl_role.setText(tr("user_editor.role_label"))
        self.role_combo.setItemText(0, tr("role.cashier_option"))
        self.role_combo.setItemText(1, tr("role.admin_option"))
        self._lbl_lang.setText(tr("user_editor.language_label"))
        self.lang_combo.setItemText(0, tr("common.follow_store_default"))
        self.lang_combo.setItemText(1, tr("common.language_en"))
        self.lang_combo.setItemText(2, tr("common.language_ar"))

        if self._lbl_pw is not None and self.pw_edit is not None:
            self._lbl_pw.setText(tr("user_editor.password_label"))
            self.pw_edit.setPlaceholderText(tr("user_editor.password_placeholder"))
        if self._lbl_confirm is not None and self.confirm_edit is not None:
            self._lbl_confirm.setText(tr("user_editor.confirm_label"))
            self.confirm_edit.setPlaceholderText(tr("user_editor.confirm_placeholder"))
        for edit, btn in self._pw_toggles:
            hidden = edit.echoMode() == QLineEdit.Password
            btn.setText(_esc_amp(tr("common.show" if hidden else "common.hide")))

        self.cancel_btn.setText(_esc_amp(tr("common.cancel")))
        self.save_btn.setText(
            _esc_amp(tr("user_editor.save_btn.edit" if user else "user_editor.btn.create"))
        )
        if self._err_key:
            self.error_lbl.setText(tr(self._err_key))
        elif self._err_raw:
            self.error_lbl.setText(self._err_raw)

    def _on_save(self) -> None:
        self._err_key = None
        self._err_raw = ""
        self.error_lbl.hide()
        try:
            username = validate_username(self.username_edit.text())
        except ValueError as exc:
            self._err_raw = str(exc)
            self.error_lbl.setText(self._err_raw)
            self.error_lbl.show()
            self.username_edit.setFocus()
            return

        display_name = validate_display_name(self.display_edit.text(), username)
        role = self.role_combo.currentData() or ROLE_CASHIER
        pref_lang = str(self.lang_combo.currentData() or "")

        if self._editing_user is None and self.pw_edit is not None and self.confirm_edit is not None:
            try:
                pw = validate_password_policy(self.pw_edit.text())
            except ValueError as exc:
                self._err_raw = str(exc)
                self.error_lbl.setText(self._err_raw)
                self.error_lbl.show()
                self.pw_edit.setFocus()
                return
            if pw != self.confirm_edit.text():
                self._err_key = "user_editor.error.password_mismatch"
                self.error_lbl.setText(tr(self._err_key))
                self.error_lbl.show()
                self.confirm_edit.setFocus()
                return
            self.result_password = pw

        self.result_username = username
        self.result_display_name = display_name
        self.result_role = role
        self.result_preferred_language = pref_lang
        self.accept()


class ResetPasswordDialog(QDialog):
    """Modal dialog for Administrators to reset any user's password."""

    def __init__(
        self,
        user: UserAccount,
        config: BusinessConfig,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._user = user
        self.config = config
        self._did_animate_in = False
        self.result_password: str = ""
        self._pw_toggles: list[tuple[QLineEdit, QPushButton]] = []
        self._err_key: str | None = None
        self._err_raw: str = ""

        self.setModal(True)
        self.setMinimumWidth(410)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        card = QFrame()
        card.setProperty("class", "BoutiqueCard")
        form = QFormLayout(card)
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        form.setContentsMargins(16, 14, 16, 14)
        form.setSpacing(10)

        self._lbl_account = QLabel("")
        target_lbl = QLabel(f"{user.label} ({user.username})")
        target_lbl.setStyleSheet("font-weight: 700; color: #202820;")
        form.addRow(self._lbl_account, target_lbl)

        self._lbl_pw = QLabel("")
        self._lbl_confirm = QLabel("")
        pw_wrap, self.pw_edit = self._make_pw_row()
        cf_wrap, self.confirm_edit = self._make_pw_row()
        form.addRow(self._lbl_pw, pw_wrap)
        form.addRow(self._lbl_confirm, cf_wrap)
        root.addWidget(card)

        self.error_lbl = QLabel("")
        self.error_lbl.setProperty("class", "ValidationHint")
        self.error_lbl.setWordWrap(True)
        self.error_lbl.hide()
        root.addWidget(self.error_lbl)

        btn_row = QHBoxLayout()
        self.cancel_btn = QPushButton("")
        self.cancel_btn.clicked.connect(self.reject)
        self.save_btn = QPushButton("")
        self.save_btn.setProperty("role", "primary")
        self.save_btn.setDefault(True)
        self.save_btn.setIcon(get_svg_icon("key", contrast_text_hex(config.accent_color), 14))
        self.save_btn.setIconSize(QSize(14, 14))
        self.save_btn.clicked.connect(self._on_save)
        attach_button_feedback(self.cancel_btn)
        attach_button_feedback(self.save_btn)

        btn_row.addStretch(1)
        btn_row.addWidget(self.cancel_btn)
        btn_row.addWidget(self.save_btn)
        root.addLayout(btn_row)

        self.retranslate_ui()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._did_animate_in:
            self._did_animate_in = True
            animate_window_fade_in(self)

    def _make_pw_row(self) -> tuple[QWidget, QLineEdit]:
        wrap = QWidget()
        row = QHBoxLayout(wrap)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        edit = QLineEdit()
        edit.setEchoMode(QLineEdit.Password)
        edit.setMaxLength(128)
        btn = QPushButton(_esc_amp(tr("common.show")))
        btn.setMinimumWidth(64)
        btn.setStyleSheet("padding: 5px 8px; font-size: 12px;")

        def _toggle() -> None:
            hidden = edit.echoMode() == QLineEdit.Password
            edit.setEchoMode(QLineEdit.Normal if hidden else QLineEdit.Password)
            btn.setText(_esc_amp(tr("common.show" if not hidden else "common.hide")))

        btn.clicked.connect(_toggle)
        self._pw_toggles.append((edit, btn))
        row.addWidget(edit, 1)
        row.addWidget(btn)
        return wrap, edit

    def retranslate_ui(self) -> None:
        self.setLayoutDirection(layout_direction())
        self.setWindowTitle(tr("reset_pw.title", username=self._user.username))
        self._lbl_account.setText(tr("reset_pw.account_label"))
        self._lbl_pw.setText(tr("reset_pw.new_pw_label"))
        self.pw_edit.setPlaceholderText(tr("reset_pw.new_pw_hint"))
        self._lbl_confirm.setText(tr("reset_pw.confirm_label"))
        self.confirm_edit.setPlaceholderText(tr("reset_pw.confirm_hint"))
        for edit, btn in self._pw_toggles:
            hidden = edit.echoMode() == QLineEdit.Password
            btn.setText(_esc_amp(tr("common.show" if hidden else "common.hide")))
        self.cancel_btn.setText(_esc_amp(tr("common.cancel")))
        self.save_btn.setText(_esc_amp(tr("reset_pw.submit_btn")))
        if self._err_key:
            self.error_lbl.setText(tr(self._err_key))
        elif self._err_raw:
            self.error_lbl.setText(self._err_raw)

    def _on_save(self) -> None:
        self._err_key = None
        self._err_raw = ""
        self.error_lbl.hide()
        try:
            pw = validate_password_policy(self.pw_edit.text())
        except ValueError as exc:
            self._err_raw = str(exc)
            self.error_lbl.setText(self._err_raw)
            self.error_lbl.show()
            self.pw_edit.setFocus()
            return
        if pw != self.confirm_edit.text():
            self._err_key = "reset_pw.error.mismatch"
            self.error_lbl.setText(tr(self._err_key))
            self.error_lbl.show()
            self.confirm_edit.setFocus()
            return
        self.result_password = pw
        self.accept()


class UsersView(QWidget):
    """Administrator-only Staff Accounts & Security Audit Log screen."""

    users_changed = Signal()
    status_message = Signal(str)

    def __init__(self, db: Database, config: BusinessConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.db = db
        self.config = config
        self._users: list[UserAccount] = []
        self._mode: str = "staff"

        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 10, 16, 12)
        outer.setSpacing(10)

        panel = QFrame()
        panel.setProperty("class", "BoutiqueCard")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(12)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        self.title_lbl = QLabel("")
        self.title_lbl.setProperty("class", "PageHeading")

        self.count_badge = QLabel("")
        self.count_badge.setProperty("class", "CountBadge")

        self.staff_tab_btn = QPushButton("")
        self.staff_tab_btn.setProperty("class", "CategoryChip")
        self.staff_tab_btn.setProperty("selected", "true")
        self.staff_tab_btn.clicked.connect(lambda: self._switch_subtab("staff"))

        self.audit_tab_btn = QPushButton("")
        self.audit_tab_btn.setProperty("class", "CategoryChip")
        self.audit_tab_btn.setProperty("selected", "false")
        self.audit_tab_btn.clicked.connect(lambda: self._switch_subtab("audit"))

        self.search_input = SearchLineEdit("")
        self.search_input.setMinimumWidth(160)
        self.search_input.setMaximumWidth(240)
        self.search_input.textChanged.connect(self.refresh)

        self.add_user_btn = QPushButton("")
        self.add_user_btn.setProperty("role", "primary")
        self.add_user_btn.setIcon(get_svg_icon("plus", contrast_text_hex(self.config.accent_color), 14))
        self.add_user_btn.setIconSize(QSize(14, 14))
        self.add_user_btn.clicked.connect(self.open_add_dialog)
        attach_button_feedback(self.add_user_btn)

        toolbar.addWidget(self.title_lbl)
        toolbar.addWidget(self.count_badge)
        toolbar.addSpacing(4)
        toolbar.addWidget(self.staff_tab_btn)
        toolbar.addWidget(self.audit_tab_btn)
        toolbar.addStretch(1)
        toolbar.addWidget(self.search_input)
        toolbar.addWidget(self.add_user_btn)
        layout.addLayout(toolbar)

        self.sub_stack = QStackedWidget()

        # Page 0: Staff Table
        staff_host = QWidget()
        staff_layout = QVBoxLayout(staff_host)
        staff_layout.setContentsMargins(0, 0, 0, 0)

        self.staff_stack = QStackedWidget()
        self.table = QTableWidget(0, 6)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)

        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.Fixed)
        hdr.setSectionResizeMode(3, QHeaderView.Fixed)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(5, QHeaderView.Fixed)
        self.table.setColumnWidth(2, 118)
        self.table.setColumnWidth(3, 108)
        self.table.setColumnWidth(5, 250)

        self.empty_users_state = EmptyStateWidget(
            "products",
            "",
            "",
            action_label="",
            accent_color=self.config.accent_color,
        )
        self.empty_users_state.action_triggered.connect(self.open_add_dialog)
        self.staff_stack.addWidget(self.table)
        self.staff_stack.addWidget(self.empty_users_state)
        staff_layout.addWidget(self.staff_stack)

        # Page 1: Security Audit Log Table
        self.audit_table = QTableWidget(0, 5)
        self.audit_table.verticalHeader().setVisible(False)
        self.audit_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.audit_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.audit_table.setAlternatingRowColors(True)
        ahdr = self.audit_table.horizontalHeader()
        ahdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        ahdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        ahdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        ahdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        ahdr.setSectionResizeMode(4, QHeaderView.Stretch)

        self.sub_stack.addWidget(staff_host)
        self.sub_stack.addWidget(self.audit_table)
        layout.addWidget(self.sub_stack, 1)

        outer.addWidget(panel, 1)
        self._apply_static_texts()

    def _apply_static_texts(self) -> None:
        self.title_lbl.setText(tr("users.heading"))
        self.staff_tab_btn.setText(_esc_amp(tr("users.subtab.staff")))
        self.audit_tab_btn.setText(_esc_amp(tr("users.subtab.audit")))
        self.search_input.setPlaceholderText(tr("users.search_ph"))
        self.add_user_btn.setText(_esc_amp(tr("users.add_user_btn")))
        self.table.setHorizontalHeaderLabels(
            [
                tr("users.col.username"),
                tr("users.col.display_name"),
                tr("users.col.role"),
                tr("users.col.status"),
                tr("users.col.last_login"),
                tr("users.col.actions"),
            ]
        )
        self.empty_users_state.set_texts(
            tr("users.empty.title"),
            tr("users.empty.desc"),
            action_label=tr("users.add_user_btn"),
        )
        self.audit_table.setHorizontalHeaderLabels(
            [
                tr("users.audit.col.timestamp"),
                tr("users.audit.col.actor"),
                tr("users.audit.col.action"),
                tr("users.audit.col.target"),
                tr("users.audit.col.details"),
            ]
        )

    def retranslate_ui(self) -> None:
        self._apply_static_texts()
        self.refresh()

    def set_config(self, config: BusinessConfig) -> None:
        self.config = config
        self.empty_users_state.refresh_accent(config.accent_color)
        self.add_user_btn.setIcon(
            get_svg_icon("plus", contrast_text_hex(config.accent_color), 14)
        )
        self.refresh()

    def clear_session_state(self) -> None:
        """Clear sensitive staff/audit tables on logout or lock."""
        self._users = []
        self._clear_staff_table()
        self.table.setRowCount(0)
        self.audit_table.setRowCount(0)
        self.count_badge.setText(tr_count("user", 0))
        self.search_input.blockSignals(True)
        self.search_input.clear()
        self.search_input.blockSignals(False)

    def focus_search(self) -> None:
        self.search_input.setFocus()
        self.search_input.selectAll()

    def _switch_subtab(self, mode: str) -> None:
        self._mode = "audit" if mode == "audit" else "staff"
        self.staff_tab_btn.setProperty("selected", "true" if self._mode == "staff" else "false")
        self.audit_tab_btn.setProperty("selected", "true" if self._mode == "audit" else "false")
        for b in (self.staff_tab_btn, self.audit_tab_btn):
            b.style().unpolish(b)
            b.style().polish(b)
        self.sub_stack.setCurrentIndex(1 if self._mode == "audit" else 0)
        self.refresh()

    def _clear_staff_table(self) -> None:
        for r in range(self.table.rowCount()):
            for c in range(self.table.columnCount()):
                w = self.table.cellWidget(r, c)
                if w:
                    self.table.removeCellWidget(r, c)
                    w.setParent(None)
                    w.deleteLater()
        self.table.clearContents()

    def refresh(self) -> None:
        actor = self.db.session_actor
        if actor is None or not actor.is_admin:
            self.clear_session_state()
            return

        if self._mode == "audit":
            self._refresh_audit_log()
            return

        try:
            all_users = self.db.list_users()
        except AuthorizationError:
            self.clear_session_state()
            return

        query = self.search_input.text().strip().lower()
        if query:
            filtered = [
                u
                for u in all_users
                if query in u.username.lower()
                or query in u.display_name.lower()
                or query in u.role.lower()
                or query in tr_role(u.role).lower()
                or query in tr_role_long(u.role).lower()
            ]
        else:
            filtered = all_users

        self._users = filtered
        self.count_badge.setText(tr_count("user", len(filtered)))

        self._clear_staff_table()
        if not filtered:
            self.table.setRowCount(0)
            self.staff_stack.setCurrentIndex(1)
            return

        self.staff_stack.setCurrentIndex(0)
        self.table.setRowCount(len(filtered))

        for row_idx, usr in enumerate(filtered):
            is_self = actor is not None and usr.id == actor.id

            u_lbl = usr.username + (tr("users.self_suffix") if is_self else "")
            u_item = QTableWidgetItem(u_lbl)
            uf = u_item.font()
            uf.setBold(True)
            u_item.setFont(uf)
            self.table.setItem(row_idx, 0, u_item)

            self.table.setItem(row_idx, 1, QTableWidgetItem(usr.display_name or usr.username))

            # Role pill
            role_wrap = QWidget()
            role_layout = QHBoxLayout(role_wrap)
            role_layout.setContentsMargins(6, 4, 6, 4)
            role_pill = QLabel(tr_role(usr.role))
            role_pill.setAlignment(Qt.AlignCenter)
            role_pill.setMinimumWidth(86)
            role_pill.setProperty(
                "class", "RoleAdminBadge" if usr.is_admin else "RoleCashierBadge"
            )
            role_layout.addWidget(role_pill)
            role_layout.addStretch(1)
            self.table.setCellWidget(row_idx, 2, role_wrap)

            # Status pill
            status_wrap = QWidget()
            status_layout = QHBoxLayout(status_wrap)
            status_layout.setContentsMargins(6, 4, 6, 4)
            status_pill = QLabel(
                tr("users.status.active" if usr.is_active else "users.status.disabled_badge")
            )
            status_pill.setAlignment(Qt.AlignCenter)
            status_pill.setMinimumWidth(78)
            status_pill.setProperty(
                "class", "StatusActiveBadge" if usr.is_active else "StatusDisabledBadge"
            )
            status_layout.addWidget(status_pill)
            status_layout.addStretch(1)
            self.table.setCellWidget(row_idx, 3, status_wrap)

            last_txt = (
                format_local_timestamp(usr.last_login_at) if usr.last_login_at else tr("common.never")
            )
            self.table.setItem(row_idx, 4, QTableWidgetItem(last_txt))

            # Actions
            actions_wrap = QWidget()
            actions_layout = QHBoxLayout(actions_wrap)
            actions_layout.setContentsMargins(6, 4, 6, 4)
            actions_layout.setSpacing(5)

            edit_btn = QPushButton(_esc_amp(tr("users.action.edit")))
            edit_btn.setStyleSheet("padding: 4px 9px; font-size: 11.5px;")
            edit_btn.clicked.connect(lambda _c=False, u=usr: self.open_edit_dialog(u))

            pw_btn = QPushButton(_esc_amp(tr("users.btn.password")))
            pw_btn.setStyleSheet("padding: 4px 9px; font-size: 11.5px;")
            pw_btn.clicked.connect(lambda _c=False, u=usr: self.open_reset_pw_dialog(u))

            toggle_btn = QPushButton(
                _esc_amp(tr("users.action.disable" if usr.is_active else "users.btn.enable"))
            )
            toggle_btn.setProperty("role", "danger" if usr.is_active else "")
            toggle_btn.setStyleSheet("padding: 4px 9px; font-size: 11.5px;")
            toggle_btn.clicked.connect(lambda _c=False, u=usr: self._toggle_active(u))
            if is_self and usr.is_active:
                toggle_btn.setEnabled(False)
                toggle_btn.setToolTip(tr("users.disable_self_tip"))

            for ab in (edit_btn, pw_btn, toggle_btn):
                ab.setMinimumWidth(0)
                ab.setSizePolicy(ab.sizePolicy().horizontalPolicy(), ab.sizePolicy().verticalPolicy())
                ab.ensurePolished()
                ab_w = max(ab.sizeHint().width(), ab.fontMetrics().horizontalAdvance(ab.text()) + 22)
                ab.setMinimumWidth(ab_w)
                attach_button_feedback(ab)
                actions_layout.addWidget(ab)
            actions_layout.addStretch(1)
            self.table.setCellWidget(row_idx, 5, actions_wrap)
            self.table.setRowHeight(row_idx, 48)

        max_actions_w = 240
        for row_idx in range(self.table.rowCount()):
            w = self.table.cellWidget(row_idx, 5)
            if w is not None:
                max_actions_w = max(max_actions_w, w.sizeHint().width() + 14)
        self.table.setColumnWidth(5, max_actions_w)

    def _refresh_audit_log(self) -> None:
        try:
            events = self.db.list_audit_logs(limit=150)
        except AuthorizationError:
            self.clear_session_state()
            return

        q = self.search_input.text().strip().lower()
        if q:
            events = [
                ev
                for ev in events
                if q in ev.actor_username.lower()
                or q in ev.action.lower()
                or q in tr_audit_action(ev.action).lower()
                or q in ev.details.lower()
            ]

        self.count_badge.setText(tr_count("event", len(events)))
        self.audit_table.setRowCount(len(events))
        for r, ev in enumerate(events):
            self.audit_table.setItem(r, 0, QTableWidgetItem(format_local_timestamp(ev.created_at)))
            actor_disp = ev.actor_username or tr("users.audit.system")
            if ev.actor_role:
                actor_disp = f"{actor_disp} ({tr_role(ev.actor_role)})"
            self.audit_table.setItem(r, 1, QTableWidgetItem(actor_disp))
            act_item = QTableWidgetItem(tr_audit_action(ev.action))
            f = act_item.font()
            f.setBold(True)
            act_item.setFont(f)
            self.audit_table.setItem(r, 2, act_item)
            self.audit_table.setItem(
                r, 3, QTableWidgetItem(f"{ev.target_type}:{ev.target_id}".strip(":"))
            )
            self.audit_table.setItem(r, 4, QTableWidgetItem(ev.details))
            self.audit_table.setRowHeight(r, 36)

    def open_add_dialog(self) -> None:
        dlg = UserEditorDialog(self.config, user=None, parent=self)
        if dlg.exec() == QDialog.Accepted:
            try:
                created = self.db.create_user(
                    dlg.result_username,
                    dlg.result_password,
                    dlg.result_role,
                    display_name=dlg.result_display_name,
                    preferred_language=dlg.result_preferred_language,
                )
            except Exception as exc:
                QMessageBox.warning(self, tr("users.error.create_failed"), str(exc))
                return
            self.refresh()
            self.users_changed.emit()
            self.status_message.emit(
                tr(
                    "users.status.created",
                    role=tr_role_long(created.role),
                    username=created.username,
                )
            )

    def open_edit_dialog(self, user: UserAccount) -> None:
        dlg = UserEditorDialog(self.config, user=user, parent=self)
        if dlg.exec() == QDialog.Accepted:
            try:
                updated = self.db.update_user(
                    user.id,
                    username=dlg.result_username,
                    display_name=dlg.result_display_name,
                    role=dlg.result_role,
                    preferred_language=dlg.result_preferred_language,
                )
            except Exception as exc:
                QMessageBox.warning(self, tr("users.error.update_failed"), str(exc))
                return
            self.refresh()
            self.users_changed.emit()
            self.status_message.emit(tr("users.status.updated", username=updated.username))

    def open_reset_pw_dialog(self, user: UserAccount) -> None:
        dlg = ResetPasswordDialog(user, self.config, parent=self)
        if dlg.exec() == QDialog.Accepted:
            try:
                self.db.reset_user_password(user.id, dlg.result_password)
            except Exception as exc:
                QMessageBox.warning(self, tr("users.error.reset_failed"), str(exc))
                return
            self.status_message.emit(tr("users.status.pw_reset", username=user.username))

    def _toggle_active(self, user: UserAccount) -> None:
        target_active = not user.is_active
        title_key = "users.confirm_toggle.title.enable" if target_active else "users.confirm_toggle.title.disable"
        body_key = "users.confirm_toggle.body.enable" if target_active else "users.confirm_toggle.body.disable"
        answer = QMessageBox.question(
            self,
            tr(title_key),
            tr(body_key, username=user.username, role=tr_role_long(user.role)),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            self.db.set_user_active(user.id, target_active)
        except Exception as exc:
            QMessageBox.warning(self, tr("users.error.toggle_failed"), str(exc))
            return
        self.refresh()
        self.users_changed.emit()
        status_key = "users.status.enabled" if target_active else "users.status.disabled"
        self.status_message.emit(tr(status_key, username=user.username))
