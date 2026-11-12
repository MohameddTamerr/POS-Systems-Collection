from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..auth import UserAccount
from ..config import (
    BusinessConfig,
    DEFAULT_FONT_SCALE_PCT,
    FONT_SCALE_PRESETS,
    MAX_FONT_SCALE_PCT,
    MIN_FONT_SCALE_PCT,
    validate_font_scale_pct,
    validate_ui_density,
)
from ..db import Database
from ..icons import get_svg_icon
from ..i18n import layout_direction, tr
from ..theme import contrast_text_hex, get_tokens
from .animations import animate_window_fade_in, attach_button_feedback


class DisplayPreferencesDialog(QDialog):
    """Per-user Display & Accessibility dialog accessible by both Cashier and Admin."""

    preferences_saved = Signal(object)  # emits updated UserAccount

    def __init__(
        self,
        db: Database,
        user: UserAccount,
        config: BusinessConfig,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.db = db
        self.user = user
        self._updated_user: UserAccount | None = None
        self.config = config
        self._did_animate_in = False
        self._scale_pct = validate_font_scale_pct(getattr(user, "font_scale_pct", 100))
        self._density = validate_ui_density(getattr(user, "ui_density", "comfortable"))
        self._motion_pref = int(getattr(user, "reduced_motion_pref", -1))

        self.setModal(True)
        self.setMinimumWidth(470)
        self.setLayoutDirection(layout_direction())

        self._build_ui()
        self.retranslate_ui()
        self._sync_controls()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        card = QFrame()
        card.setProperty("class", "BoutiqueCard")
        c_lay = QVBoxLayout(card)
        c_lay.setContentsMargins(16, 14, 16, 14)
        c_lay.setSpacing(12)

        self.hdr_lbl = QLabel("")
        self.hdr_lbl.setProperty("class", "PageHeading")
        self.sub_lbl = QLabel("")
        self.sub_lbl.setProperty("class", "MutedLabel")
        self.sub_lbl.setWordWrap(True)
        c_lay.addWidget(self.hdr_lbl)
        c_lay.addWidget(self.sub_lbl)

        # Font Size Presets + Slider
        self.font_lbl = QLabel("")
        self.font_lbl.setStyleSheet("font-weight: 700;")
        c_lay.addWidget(self.font_lbl)

        presets_row = QHBoxLayout()
        presets_row.setSpacing(6)
        self.preset_group = QButtonGroup(self)
        self.preset_buttons: list[tuple[str, int, QPushButton]] = []
        for p_key, p_val in FONT_SCALE_PRESETS:
            btn = QPushButton("")
            btn.setProperty("class", "CategoryChip")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _c=False, val=p_val: self._set_scale(val))
            attach_button_feedback(btn)
            self.preset_group.addButton(btn)
            self.preset_buttons.append((p_key, p_val, btn))
            presets_row.addWidget(btn)
        c_lay.addLayout(presets_row)

        slider_row = QHBoxLayout()
        slider_row.setSpacing(10)
        self.scale_slider = QSlider(Qt.Horizontal)
        self.scale_slider.setRange(MIN_FONT_SCALE_PCT, MAX_FONT_SCALE_PCT)
        self.scale_slider.setSingleStep(5)
        self.scale_slider.setPageStep(10)
        self.scale_slider.valueChanged.connect(self._on_slider_changed)

        self.scale_badge = QLabel("")
        self.scale_badge.setProperty("class", "PrimaryBadge")
        self.scale_badge.setMinimumWidth(56)
        self.scale_badge.setAlignment(Qt.AlignCenter)

        slider_row.addWidget(self.scale_slider, 1)
        slider_row.addWidget(self.scale_badge)
        c_lay.addLayout(slider_row)

        # UI Density
        self.density_lbl = QLabel("")
        self.density_lbl.setStyleSheet("font-weight: 700;")
        self.density_combo = QComboBox()
        self.density_combo.addItem("", "comfortable")
        self.density_combo.addItem("", "compact")
        self.density_combo.currentIndexChanged.connect(self._on_density_changed)
        c_lay.addWidget(self.density_lbl)
        c_lay.addWidget(self.density_combo)

        # Reduced Motion override
        self.motion_lbl = QLabel("")
        self.motion_lbl.setStyleSheet("font-weight: 700;")
        self.motion_combo = QComboBox()
        self.motion_combo.addItem("", -1)
        self.motion_combo.addItem("", 1)
        self.motion_combo.addItem("", 0)
        self.motion_combo.currentIndexChanged.connect(self._on_motion_changed)
        c_lay.addWidget(self.motion_lbl)
        c_lay.addWidget(self.motion_combo)

        # Live Preview Box
        preview_card = QFrame()
        preview_card.setProperty("class", "CozySubCard")
        pv_lay = QVBoxLayout(preview_card)
        pv_lay.setContentsMargins(12, 10, 12, 10)
        pv_lay.setSpacing(6)
        self.preview_hdr = QLabel("")
        self.preview_hdr.setProperty("class", "MutedLabel")
        self.preview_title = QLabel("")
        self.preview_price = QLabel("")
        pv_lay.addWidget(self.preview_hdr)
        pv_lay.addWidget(self.preview_title)
        pv_lay.addWidget(self.preview_price)
        c_lay.addWidget(preview_card)

        root.addWidget(card)

        # Footer Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.restore_btn = QPushButton("")
        self.restore_btn.setProperty("role", "secondary")
        self.restore_btn.setIcon(get_svg_icon("refresh", "#26352F", 13))
        self.restore_btn.setIconSize(QSize(13, 13))
        self.restore_btn.clicked.connect(self._restore_defaults)

        self.cancel_btn = QPushButton("")
        self.cancel_btn.clicked.connect(self.reject)

        self.save_btn = QPushButton("")
        self.save_btn.setProperty("role", "primary")
        self.save_btn.setIcon(get_svg_icon("check", contrast_text_hex(self.config.accent_color), 14))
        self.save_btn.setIconSize(QSize(14, 14))
        self.save_btn.setDefault(True)
        self.save_btn.clicked.connect(self._on_save)

        attach_button_feedback(self.restore_btn)
        attach_button_feedback(self.cancel_btn)
        attach_button_feedback(self.save_btn)

        btn_row.addWidget(self.restore_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(self.cancel_btn)
        btn_row.addWidget(self.save_btn)
        root.addLayout(btn_row)

    def _set_scale(self, value: int) -> None:
        self._scale_pct = validate_font_scale_pct(value)
        self._sync_controls()

    def _on_slider_changed(self, value: int) -> None:
        self._scale_pct = validate_font_scale_pct(value)
        self._sync_presets_and_preview()

    def _on_density_changed(self) -> None:
        val = self.density_combo.currentData()
        if val:
            self._density = validate_ui_density(str(val))
        self._sync_presets_and_preview()

    def _on_motion_changed(self) -> None:
        val = self.motion_combo.currentData()
        if val is not None:
            self._motion_pref = int(val)

    def _restore_defaults(self) -> None:
        self._scale_pct = DEFAULT_FONT_SCALE_PCT
        self._density = "comfortable"
        self._motion_pref = -1
        self._sync_controls()

    def _sync_controls(self) -> None:
        self.scale_slider.blockSignals(True)
        self.scale_slider.setValue(self._scale_pct)
        self.scale_slider.blockSignals(False)

        d_idx = self.density_combo.findData(self._density)
        if d_idx >= 0:
            self.density_combo.blockSignals(True)
            self.density_combo.setCurrentIndex(d_idx)
            self.density_combo.blockSignals(False)

        m_idx = self.motion_combo.findData(self._motion_pref)
        if m_idx >= 0:
            self.motion_combo.blockSignals(True)
            self.motion_combo.setCurrentIndex(m_idx)
            self.motion_combo.blockSignals(False)

        self._sync_presets_and_preview()

    def _sync_presets_and_preview(self) -> None:
        self.scale_badge.setText(f"{self._scale_pct}%")
        for _key, p_val, btn in self.preset_buttons:
            selected = self._scale_pct == p_val
            btn.setChecked(selected)
            btn.setProperty("selected", "true" if selected else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

        factor = self._scale_pct / 100.0
        title_px = max(11, int(round(14.5 * factor)))
        price_px = max(10, int(round(12.5 * factor)))
        t = get_tokens(self.config.accent_color)
        self.preview_title.setStyleSheet(
            f"font-size: {title_px}px; font-weight: 700; color: {t.text};"
        )
        self.preview_price.setStyleSheet(
            f"font-size: {price_px}px; font-weight: 700; color: {t.primary};"
        )

    def retranslate_ui(self) -> None:
        self.setLayoutDirection(layout_direction())
        self.setWindowTitle(tr("display_prefs.title"))
        self.hdr_lbl.setText(tr("display_prefs.title"))
        self.sub_lbl.setText(tr("display_prefs.subtitle"))
        self.font_lbl.setText(tr("display_prefs.font_size_label"))
        for p_key, p_val, btn in self.preset_buttons:
            btn.setText(f"{tr(f'display_prefs.preset.{p_key}')} ({p_val}%)")

        self.density_lbl.setText(tr("display_prefs.density_label"))
        self.density_combo.setItemText(0, tr("display_prefs.density.comfortable"))
        self.density_combo.setItemText(1, tr("display_prefs.density.compact"))

        self.motion_lbl.setText(tr("display_prefs.motion_label"))
        self.motion_combo.setItemText(0, tr("display_prefs.motion.follow_store"))
        self.motion_combo.setItemText(1, tr("display_prefs.motion.reduce"))
        self.motion_combo.setItemText(2, tr("display_prefs.motion.allow"))

        self.preview_hdr.setText(tr("display_prefs.preview_header"))
        self.preview_title.setText(tr("display_prefs.preview_sample_item"))
        self.preview_price.setText(tr("display_prefs.preview_sample_sub", pct=self._scale_pct))

        self.restore_btn.setText(tr("display_prefs.restore_defaults"))
        self.cancel_btn.setText(tr("common.cancel"))
        self.save_btn.setText(tr("common.save"))
        self._sync_presets_and_preview()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._did_animate_in:
            self._did_animate_in = True
            animate_window_fade_in(self)

    @property
    def updated_user(self) -> UserAccount | None:
        return self._updated_user

    def _on_save(self) -> None:
        updated = self.db.update_my_preferences(
            font_scale_pct=self._scale_pct,
            ui_density=self._density,
            reduced_motion_pref=self._motion_pref,
            actor=self.user,
        )
        self.user = updated
        self._updated_user = updated
        self.preferences_saved.emit(updated)
        self.accept()
