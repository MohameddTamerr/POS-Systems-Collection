from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
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
    QVBoxLayout,
    QWidget,
)

from ..config import (
    BusinessConfig,
    DEFAULT_BUSINESS_NAME,
    THEME_PRESETS,
    read_and_validate_logo,
    validate_hex_color,
)
from ..money import CURRENCIES
from ..theme import contrast_text_hex, scaled_logo_pixmap


class SetupDialog(QDialog):
    """First-launch native white-label business setup window."""

    def __init__(
        self,
        initial_config: BusinessConfig,
        is_first_launch: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Café POS — Business Setup")
        self.setModal(True)
        self.setMinimumWidth(560)

        self._accent_color = validate_hex_color(initial_config.accent_color)
        self._logo_blob = initial_config.logo_blob
        self._logo_ext = initial_config.logo_ext
        self.result_config: BusinessConfig | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)

        title = QLabel("Set Up Your Business Identity")
        title.setStyleSheet("font-size: 17px; font-weight: 700;")
        subtitle = QLabel(
            "Customize your business name, logo, currency, and accent color. "
            "You can change these settings at any time without rebuilding the application."
        )
        subtitle.setWordWrap(True)
        subtitle.setProperty("class", "MutedLabel")
        root.addWidget(title)
        root.addWidget(subtitle)

        form_frame = QFrame()
        form_frame.setProperty("class", "TerminalPanel")
        form_layout = QFormLayout(form_frame)
        form_layout.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        form_layout.setContentsMargins(16, 16, 16, 16)
        form_layout.setSpacing(12)

        self.name_edit = QLineEdit()
        self.name_edit.setMaxLength(60)
        self.name_edit.setPlaceholderText(f"e.g. {DEFAULT_BUSINESS_NAME}")
        if not is_first_launch or initial_config.setup_completed:
            self.name_edit.setText(initial_config.business_name)
        form_layout.addRow("Business Name *", self.name_edit)

        self.currency_combo = QComboBox()
        for code, spec in CURRENCIES.items():
            self.currency_combo.addItem(spec.label, code)
        idx = self.currency_combo.findData(initial_config.currency)
        if idx >= 0:
            self.currency_combo.setCurrentIndex(idx)
        form_layout.addRow("Currency", self.currency_combo)

        # Accent color selector
        color_widget = QWidget()
        color_row = QHBoxLayout(color_widget)
        color_row.setContentsMargins(0, 0, 0, 0)
        color_row.setSpacing(6)
        self._swatch_buttons: list[tuple[str, QPushButton]] = []
        for label_text, hex_code in THEME_PRESETS:
            btn = QPushButton()
            btn.setFixedSize(26, 26)
            btn.setToolTip(f"{label_text} ({hex_code})")
            btn.clicked.connect(lambda _checked=False, hx=hex_code: self._set_accent_color(hx))
            self._swatch_buttons.append((hex_code, btn))
            color_row.addWidget(btn)

        self.custom_color_btn = QPushButton("Choose Color...")
        self.custom_color_btn.clicked.connect(self._pick_custom_color)
        self.color_preview_lbl = QLabel(self._accent_color)
        self.color_preview_lbl.setMinimumWidth(72)
        self.color_preview_lbl.setAlignment(Qt.AlignCenter)
        color_row.addWidget(self.custom_color_btn)
        color_row.addWidget(self.color_preview_lbl)
        color_row.addStretch(1)
        form_layout.addRow("Theme Accent Color", color_widget)

        # Logo upload
        logo_widget = QWidget()
        logo_row = QHBoxLayout(logo_widget)
        logo_row.setContentsMargins(0, 0, 0, 0)
        logo_row.setSpacing(10)
        self.logo_preview = QLabel()
        self.logo_preview.setFixedSize(52, 52)
        self.logo_preview.setAlignment(Qt.AlignCenter)
        self.logo_preview.setStyleSheet(
            "background-color: #FAF8F5; border: 1px solid #D8D2C8; border-radius: 4px;"
        )
        browse_logo_btn = QPushButton("Browse Logo...")
        browse_logo_btn.clicked.connect(self._browse_logo)
        clear_logo_btn = QPushButton("Use Default")
        clear_logo_btn.clicked.connect(self._clear_logo)
        logo_hint = QLabel("Optional (PNG, JPG, BMP, WebP, ICO · max 2 MB)")
        logo_hint.setWordWrap(True)
        logo_hint.setProperty("class", "MutedLabel")
        logo_controls = QVBoxLayout()
        logo_controls.setContentsMargins(0, 0, 0, 0)
        logo_controls.setSpacing(4)
        btn_pair = QHBoxLayout()
        btn_pair.setContentsMargins(0, 0, 0, 0)
        btn_pair.addWidget(browse_logo_btn)
        btn_pair.addWidget(clear_logo_btn)
        btn_pair.addStretch(1)
        logo_controls.addLayout(btn_pair)
        logo_controls.addWidget(logo_hint)

        logo_row.addWidget(self.logo_preview)
        logo_row.addLayout(logo_controls, 1)
        form_layout.addRow("Business Logo", logo_widget)

        self.address_edit = QLineEdit(initial_config.address)
        self.address_edit.setMaxLength(140)
        self.address_edit.setPlaceholderText("Street address, city (printed on receipts)")
        form_layout.addRow("Address", self.address_edit)

        self.phone_edit = QLineEdit(initial_config.phone)
        self.phone_edit.setMaxLength(40)
        self.phone_edit.setPlaceholderText("Phone number (printed on receipts)")
        form_layout.addRow("Phone Number", self.phone_edit)

        root.addWidget(form_frame)

        # Action buttons
        actions = QHBoxLayout()
        self.skip_btn = QPushButton("Skip && Try Demo" if is_first_launch else "Cancel")
        self.skip_btn.clicked.connect(self._on_skip_or_cancel)

        self.save_btn = QPushButton("Save && Launch Register" if is_first_launch else "Save Setup")
        self.save_btn.setProperty("role", "primary")
        self.save_btn.setDefault(True)
        self.save_btn.clicked.connect(self._on_save)

        actions.addStretch(1)
        actions.addWidget(self.skip_btn)
        actions.addWidget(self.save_btn)
        root.addLayout(actions)

        self._is_first_launch = is_first_launch
        self._refresh_color_ui()
        self._refresh_logo_preview()

    def _set_accent_color(self, hex_code: str) -> None:
        self._accent_color = validate_hex_color(hex_code)
        self._refresh_color_ui()

    def _refresh_color_ui(self) -> None:
        for hex_code, btn in self._swatch_buttons:
            selected = hex_code.upper() == self._accent_color.upper()
            border = "2px solid #1F1D1A" if selected else "1px solid #CFC8BC"
            btn.setStyleSheet(
                f"background-color: {hex_code}; border: {border}; border-radius: 4px; padding: 0;"
            )
        fg = contrast_text_hex(self._accent_color)
        self.color_preview_lbl.setText(self._accent_color)
        self.color_preview_lbl.setStyleSheet(
            f"background-color: {self._accent_color}; color: {fg}; "
            "font-weight: 700; font-size: 11px; padding: 4px 8px; border-radius: 4px;"
        )

    def _pick_custom_color(self) -> None:
        chosen = QColorDialog.getColor(QColor(self._accent_color), self, "Select Theme Accent Color")
        if chosen.isValid():
            self._set_accent_color(chosen.name())

    def _browse_logo(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Business Logo",
            "",
            "Images (*.png *.jpg *.jpeg *.bmp *.webp *.ico)",
        )
        if not file_path:
            return
        try:
            blob, ext = read_and_validate_logo(Path(file_path))
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid Logo File", str(exc))
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
        self.logo_preview.setPixmap(scaled_logo_pixmap(temp_cfg, 44, 44))

    def _on_skip_or_cancel(self) -> None:
        if self._is_first_launch:
            self.result_config = BusinessConfig(
                business_name=DEFAULT_BUSINESS_NAME,
                accent_color=self._accent_color,
                currency=self.currency_combo.currentData() or "USD",
                address=self.address_edit.text().strip(),
                phone=self.phone_edit.text().strip(),
                logo_blob=self._logo_blob,
                logo_ext=self._logo_ext,
                setup_completed=True,
            )
            self.accept()
        else:
            self.reject()

    def _on_save(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(
                self,
                "Business Name Required",
                "Please enter a business name (or click 'Skip & Try Demo' to use the default café profile).",
            )
            self.name_edit.setFocus()
            return

        self.result_config = BusinessConfig(
            business_name=name,
            accent_color=self._accent_color,
            currency=self.currency_combo.currentData() or "USD",
            address=self.address_edit.text().strip(),
            phone=self.phone_edit.text().strip(),
            logo_blob=self._logo_blob,
            logo_ext=self._logo_ext,
            setup_completed=True,
        )
        self.accept()

