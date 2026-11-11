from __future__ import annotations

from datetime import date
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..config import BusinessConfig, THEME_PRESETS, read_and_validate_logo, validate_hex_color
from ..db import Database
from ..money import CURRENCIES
from ..paths import get_database_path
from ..theme import contrast_text_hex, scaled_logo_pixmap


class SettingsView(QWidget):
    """White-label business personalization and SQLite backup/restore settings."""

    settings_saved = Signal(object)  # BusinessConfig
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

        self._build_ui()
        self.sync_from_config(config)

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)

        scroll = QScrollArea()
        scroll.setObjectName("SettingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        host = QWidget()
        host.setObjectName("SettingsHost")
        layout = QVBoxLayout(host)
        layout.setContentsMargins(2, 2, 2, 12)
        layout.setSpacing(14)

        # Group 1: Business Personalization
        biz_group = QGroupBox("Business Personalization (White-Label Settings)")
        biz_form = QFormLayout(biz_group)
        biz_form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        biz_form.setSpacing(12)

        self.name_edit = QLineEdit()
        self.name_edit.setMaxLength(60)
        biz_form.addRow("Business Name *", self.name_edit)

        self.currency_combo = QComboBox()
        for code, spec in CURRENCIES.items():
            self.currency_combo.addItem(spec.label, code)
        biz_form.addRow("Currency", self.currency_combo)

        # Accent Color
        color_widget = QWidget()
        color_row = QHBoxLayout(color_widget)
        color_row.setContentsMargins(0, 0, 0, 0)
        color_row.setSpacing(6)
        self._swatch_buttons: list[tuple[str, QPushButton]] = []
        for label_text, hex_code in THEME_PRESETS:
            btn = QPushButton()
            btn.setFixedSize(26, 26)
            btn.setToolTip(f"{label_text} ({hex_code})")
            btn.clicked.connect(lambda _c=False, hx=hex_code: self._set_accent(hx))
            self._swatch_buttons.append((hex_code, btn))
            color_row.addWidget(btn)

        custom_btn = QPushButton("Choose Custom Color...")
        custom_btn.clicked.connect(self._pick_custom_color)
        self.color_badge = QLabel(self._draft_accent)
        self.color_badge.setMinimumWidth(72)
        self.color_badge.setAlignment(Qt.AlignCenter)
        color_row.addWidget(custom_btn)
        color_row.addWidget(self.color_badge)
        color_row.addStretch(1)
        biz_form.addRow("Theme Accent Color", color_widget)

        # Logo
        logo_widget = QWidget()
        logo_row = QHBoxLayout(logo_widget)
        logo_row.setContentsMargins(0, 0, 0, 0)
        logo_row.setSpacing(10)
        self.logo_preview = QLabel()
        self.logo_preview.setFixedSize(56, 56)
        self.logo_preview.setAlignment(Qt.AlignCenter)
        self.logo_preview.setStyleSheet(
            "background-color: #FAF8F5; border: 1px solid #D8D2C8; border-radius: 4px;"
        )
        browse_btn = QPushButton("Browse Logo...")
        browse_btn.clicked.connect(self._browse_logo)
        clear_btn = QPushButton("Remove Custom Logo")
        clear_btn.clicked.connect(self._clear_logo)

        logo_col = QVBoxLayout()
        logo_col.setContentsMargins(0, 0, 0, 0)
        logo_col.setSpacing(4)
        logo_btns = QHBoxLayout()
        logo_btns.setContentsMargins(0, 0, 0, 0)
        logo_btns.addWidget(browse_btn)
        logo_btns.addWidget(clear_btn)
        logo_btns.addStretch(1)
        hint = QLabel("PNG, JPG, BMP, WebP, or ICO up to 2 MB. Displayed in top bar and printed receipts.")
        hint.setWordWrap(True)
        hint.setProperty("class", "MutedLabel")
        logo_col.addLayout(logo_btns)
        logo_col.addWidget(hint)

        logo_row.addWidget(self.logo_preview)
        logo_row.addLayout(logo_col, 1)
        biz_form.addRow("Business Logo", logo_widget)

        self.address_edit = QLineEdit()
        self.address_edit.setMaxLength(140)
        biz_form.addRow("Address", self.address_edit)

        self.phone_edit = QLineEdit()
        self.phone_edit.setMaxLength(40)
        biz_form.addRow("Phone Number", self.phone_edit)

        action_widget = QWidget()
        action_row = QHBoxLayout(action_widget)
        action_row.setContentsMargins(0, 0, 0, 0)
        wizard_btn = QPushButton("Launch Setup Wizard...")
        wizard_btn.clicked.connect(self.reopen_wizard_requested.emit)
        save_btn = QPushButton("Save Settings")
        save_btn.setProperty("role", "primary")
        save_btn.clicked.connect(self._save_settings)
        action_row.addWidget(wizard_btn)
        action_row.addStretch(1)
        action_row.addWidget(save_btn)
        biz_form.addRow("", action_widget)

        layout.addWidget(biz_group)

        # Group 2: Database Storage, Backup & Restore
        db_group = QGroupBox("Local SQLite Database, Backup && Restore")
        db_form = QFormLayout(db_group)
        db_form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        db_form.setSpacing(12)

        self.db_path_lbl = QLineEdit(str(get_database_path()))
        self.db_path_lbl.setReadOnly(True)
        self.db_path_lbl.setStyleSheet("color: #6B655D; background-color: #FAF8F5;")
        db_form.addRow("Database Location", self.db_path_lbl)

        backup_widget = QWidget()
        backup_row = QHBoxLayout(backup_widget)
        backup_row.setContentsMargins(0, 0, 0, 0)
        backup_btn = QPushButton("Backup Database...")
        backup_btn.clicked.connect(self._backup_db)

        restore_btn = QPushButton("Restore Database...")
        restore_btn.clicked.connect(self._restore_db)

        reset_btn = QPushButton("Factory Reset...")
        reset_btn.setProperty("role", "danger")
        reset_btn.clicked.connect(self._factory_reset)

        backup_row.addWidget(backup_btn)
        backup_row.addWidget(restore_btn)
        backup_row.addStretch(1)
        backup_row.addWidget(reset_btn)
        db_form.addRow("Maintenance", backup_widget)

        note = QLabel(
            "All data is stored locally in a standalone SQLite database inside your Windows user AppData folder "
            "so settings, logos, products, and sales remain intact across application restarts and EXE updates."
        )
        note.setWordWrap(True)
        note.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        note.setProperty("class", "MutedLabel")
        db_form.addRow("", note)

        layout.addWidget(db_group)
        layout.addStretch(1)

        scroll.setWidget(host)
        outer.addWidget(scroll, 1)

    def sync_from_config(self, cfg: BusinessConfig) -> None:
        self.config = cfg
        self.name_edit.setText(cfg.business_name)
        idx = self.currency_combo.findData(cfg.currency)
        if idx >= 0:
            self.currency_combo.setCurrentIndex(idx)
        self.address_edit.setText(cfg.address)
        self.phone_edit.setText(cfg.phone)
        self._draft_logo_blob = cfg.logo_blob
        self._draft_logo_ext = cfg.logo_ext
        self._set_accent(cfg.accent_color)
        self._refresh_logo_preview()
        self.db_path_lbl.setText(str(self.db.db_path))

    def _set_accent(self, hex_code: str) -> None:
        self._draft_accent = validate_hex_color(hex_code)
        for preset_hex, btn in self._swatch_buttons:
            selected = preset_hex.upper() == self._draft_accent.upper()
            border = "2px solid #1F1D1A" if selected else "1px solid #CFC8BC"
            btn.setStyleSheet(
                f"background-color: {preset_hex}; border: {border}; border-radius: 4px; padding: 0;"
            )
        fg = contrast_text_hex(self._draft_accent)
        self.color_badge.setText(self._draft_accent)
        self.color_badge.setStyleSheet(
            f"background-color: {self._draft_accent}; color: {fg}; "
            "font-weight: 700; font-size: 11px; padding: 4px 8px; border-radius: 4px;"
        )

    def _pick_custom_color(self) -> None:
        chosen = QColorDialog.getColor(QColor(self._draft_accent), self, "Select Theme Accent Color")
        if chosen.isValid():
            self._set_accent(chosen.name())

    def _browse_logo(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            self,
            "Select Business Logo",
            "",
            "Images (*.png *.jpg *.jpeg *.bmp *.webp *.ico)",
        )
        if not path_str:
            return
        try:
            blob, ext = read_and_validate_logo(Path(path_str))
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid Logo File", str(exc))
            return
        self._draft_logo_blob = blob
        self._draft_logo_ext = ext
        self._refresh_logo_preview()

    def _clear_logo(self) -> None:
        self._draft_logo_blob = b""
        self._draft_logo_ext = ""
        self._refresh_logo_preview()

    def _refresh_logo_preview(self) -> None:
        preview_cfg = BusinessConfig(
            accent_color=self._draft_accent,
            logo_blob=self._draft_logo_blob,
            logo_ext=self._draft_logo_ext,
        )
        self.logo_preview.setPixmap(scaled_logo_pixmap(preview_cfg, 48, 48))

    def _save_settings(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Validation Error", "Business Name cannot be empty.")
            self.name_edit.setFocus()
            return

        updated = BusinessConfig(
            business_name=name,
            accent_color=self._draft_accent,
            currency=self.currency_combo.currentData() or "USD",
            address=self.address_edit.text().strip(),
            phone=self.phone_edit.text().strip(),
            logo_blob=self._draft_logo_blob,
            logo_ext=self._draft_logo_ext,
            setup_completed=True,
        )
        saved = self.db.save_settings(updated)
        self.config = saved
        self.settings_saved.emit(saved)
        self.status_message.emit("Business settings saved.")
        QMessageBox.information(self, "Settings Saved", "Business personalization updated.")

    def _backup_db(self) -> None:
        default_name = f"cafe-pos-backup-{date.today().isoformat()}.db"
        path_str, _ = QFileDialog.getSaveFileName(
            self,
            "Backup Café POS Database",
            default_name,
            "SQLite Database (*.db);;All Files (*)",
        )
        if not path_str:
            return
        try:
            self.db.backup_to_file(Path(path_str))
        except Exception as exc:
            QMessageBox.critical(self, "Backup Failed", f"Could not back up database:\n{exc}")
            return
        self.status_message.emit(f"Database backed up to {path_str}")
        QMessageBox.information(self, "Backup Complete", f"Database backup saved to:\n{path_str}")

    def _restore_db(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            self,
            "Restore Café POS Database",
            "",
            "SQLite Database (*.db);;All Files (*)",
        )
        if not path_str:
            return
        src_path = Path(path_str)
        try:
            self.db.validate_backup_file(src_path)
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid Backup File", str(exc))
            return

        answer = QMessageBox.question(
            self,
            "Confirm Database Restore",
            "Restoring from this backup will replace all current products, sales history, and business settings.\n\n"
            "Do you want to continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        try:
            self.db.restore_from_file(src_path)
        except Exception as exc:
            QMessageBox.critical(self, "Restore Failed", f"Could not restore database:\n{exc}")
            return

        self.data_restored.emit()
        self.status_message.emit("Database restored successfully.")
        QMessageBox.information(self, "Restore Complete", "Database restored successfully.")

    def _factory_reset(self) -> None:
        answer = QMessageBox.question(
            self,
            "Confirm Factory Reset",
            "This will erase all sales history, custom products, and business personalization "
            "and restore the default Café POS database.\n\nContinue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        self.db.factory_reset()
        self.data_restored.emit()
        self.status_message.emit("Application data reset to factory defaults.")
        self.reopen_wizard_requested.emit()

