from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..bilingual import (
    detect_primary_script,
    suggest_bilingual_name,
    translate_online_optional,
)
from ..config import BusinessConfig
from ..db import Database, ModifierGroup, Product
from ..icons import (
    get_bundled_product_asset_path,
    get_svg_icon,
    render_category_placeholder,
)
from ..i18n import layout_direction, tr, tr_category
from ..image_search import (
    ImageDownloadWorker,
    ImageSearchError,
    ImageSearchResult,
    ImageSearchWorker,
    ThumbnailBatchWorker,
    build_google_images_search_url,
    has_alpha_transparency,
    read_local_product_image_bytes,
    remove_white_background_cutout,
    render_cropped_product_image,
    resolve_product_image_path,
    save_optimized_product_image,
    suggest_image_queries,
)
from ..money import format_major_input, format_minor, get_currency, parse_major_to_minor
from ..theme import contrast_text_hex
from .animations import animate_window_fade_in, attach_button_feedback
from .category_combo import SmartCategoryComboBox
from .inventory_view import RecipeEditorWidget
from .modifier_manager_dialog import ModifierManagerDialog
from .widgets import (
    FlowLayout,
    GalleryThumbCard,
    SearchLineEdit,
    clip_pixmap_rounded,
    fit_pixmap_centered,
)

STANDARD_CATEGORIES = [
    "Espresso",
    "Cold Drinks",
    "Tea & Matcha",
    "Bakery",
    "Kitchen",
    "Retail",
]


_MODULE_RETIRED_THREADS: list[QThread] = []


class _OnlineTranslateWorker(QThread):
    """Opt-in background translation helper (only invoked when explicitly enabled in Settings)."""

    translated = Signal(str)
    failed = Signal(str)

    def __init__(self, text: str, src: str, dst: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.text = text
        self.src = src
        self.dst = dst
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        try:
            res = translate_online_optional(
                self.text,
                self.src,
                self.dst,
                enabled=True,
                timeout_seconds=3.5,
            )
            if self._cancelled:
                return
            if res and res.target_text.strip():
                self.translated.emit(res.target_text.strip())
            else:
                self.failed.emit("")
        except Exception as exc:
            if not self._cancelled:
                self.failed.emit(str(exc))


class ProductEditorDialog(QDialog):
    """Boutique two-column Product Editor with Smart Online/Local Photo & Cutout Helper."""

    categories_mutated = Signal()

    def __init__(
        self,
        config: BusinessConfig,
        existing_categories: list[str],
        product: Product | None = None,
        parent: QWidget | None = None,
        *,
        db: Database | None = None,
        is_admin: bool = True,
    ) -> None:
        super().__init__(parent)
        self.config = config
        self.currency_code = config.currency
        self.db = db
        self._is_admin = bool(is_admin)
        self._editing_product = product
        self._did_animate_in = False

        self.setWindowTitle("Edit Product" if product else "Add Product")
        self.setModal(True)
        self.setLayoutDirection(layout_direction())
        self.resize(880, 600)
        self.setMinimumSize(740, 480)

        self.result_name: str = ""
        self.result_name_en: str = product.name_en if product else ""
        self.result_name_ar: str = product.name_ar if product else ""
        self.result_category: str = ""
        self.result_category_id: int | None = product.category_id if product else None
        self.result_pending_cat_en: str = ""
        self.result_pending_cat_ar: str = ""
        self.result_price_minor: int = 0
        self.result_image_filename: str = product.image_filename if product else ""
        self.result_image_source: str = product.image_source if product else ""
        self.result_image_attribution: str = product.image_attribution if product else ""
        self.result_image_license: str = product.image_license if product else ""
        self.result_image_source_url: str = product.image_source_url if product else ""
        self.result_modifier_group_ids: list[int] = []
        self.result_option_price_overrides: dict[int, int] = {}
        self.result_recipe_entries: list[tuple[int, int]] = []
        self.recipe_editor: RecipeEditorWidget | None = None

        self._mod_group_checks: dict[int, QCheckBox] = {}
        self._mod_override_edits: dict[int, QLineEdit] = {}
        self._mod_group_opt_hosts: dict[int, QWidget] = {}
        self._mod_groups_cache: list[ModifierGroup] = []
        self._pending_mod_group_ids: set[int] = set()
        self._pending_mod_overrides: dict[int, int] = {}

        self._alt_name_edited_manually: bool = False
        self._suggested_alt_text: str = ""
        self._translate_worker: _OnlineTranslateWorker | None = None

        self._raw_image_bytes: bytes | None = None
        self._image_removed: bool = False
        self._pending_meta: dict[str, str] = {
            "source": self.result_image_source,
            "attribution": self.result_image_attribution,
            "license": self.result_image_license,
            "source_url": self.result_image_source_url,
        }
        self._fit_mode: str = "cover"
        self._zoom: float = 1.0
        self._selected_result_id: str = ""
        self._gallery_cards: dict[str, GalleryThumbCard] = {}
        self._gallery_card_list: list[GalleryThumbCard] = []
        self._last_gallery_cols: int = 4
        self._search_results: list[ImageSearchResult] = []
        self._user_edited_image_query: bool = False

        self._search_worker: ImageSearchWorker | None = None
        self._thumb_worker: ThumbnailBatchWorker | None = None
        self._download_worker: ImageDownloadWorker | None = None
        self._retired_workers: list[object] = []
        self._existing_categories = list(existing_categories)
        self._gallery_status_key: str | None = "product_editor.gallery_prompt_idle"
        self._gallery_status_kwargs: dict[str, object] = {}

        self._suggest_timer = QTimer(self)
        self._suggest_timer.setSingleShot(True)
        self._suggest_timer.timeout.connect(self._on_name_debounce_elapsed)
        self._last_chip_sig: tuple[str, str, str] | None = None

        if product and product.image_filename:
            existing_path = resolve_product_image_path(product.image_filename)
            if existing_path is not None:
                try:
                    self._raw_image_bytes = existing_path.read_bytes()
                    if has_alpha_transparency(self._raw_image_bytes):
                        self._fit_mode = "contain"
                except OSError:
                    self._raw_image_bytes = None

        if product and self.db is not None:
            assigned_groups = self.db.get_product_modifiers(product.id, include_inactive=True)
            for grp in assigned_groups:
                self._pending_mod_group_ids.add(grp.id)
                for opt in grp.options:
                    if opt.has_price_override:
                        self._pending_mod_overrides[opt.id] = int(opt.effective_price_delta_minor)

        self._build_ui(existing_categories, product)
        self.retranslate_ui()
        self._refresh_bilingual_suggestion(auto_fill=(product is None))
        self._refresh_query_chips()
        self._refresh_image_preview()
        self._validate_form_live()

    def _build_ui(self, existing_categories: list[str], product: Product | None) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)

        body_cols = QHBoxLayout()
        body_cols.setSpacing(12)

        # ---------------- LEFT COLUMN: Details ----------------
        left_card = QFrame()
        left_card.setProperty("class", "BoutiqueCard")
        left_card.setMinimumWidth(262)
        left_card.setMaximumWidth(315)
        left_outer = QVBoxLayout(left_card)
        left_outer.setContentsMargins(0, 0, 0, 0)
        left_outer.setSpacing(0)

        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        left_host = QWidget()
        left_host.setStyleSheet("background: transparent;")
        left_layout = QVBoxLayout(left_host)
        left_layout.setContentsMargins(14, 14, 14, 14)
        left_layout.setSpacing(7)

        self.details_hdr = QLabel("")
        self.details_hdr.setProperty("class", "SectionHeader")
        self.details_sub = QLabel("")
        self.details_sub.setProperty("class", "MutedLabel")
        left_layout.addWidget(self.details_hdr)
        left_layout.addWidget(self.details_sub)

        self.name_lbl = QLabel("")
        self.name_lbl.setStyleSheet("font-weight: 600;")
        self.name_edit = QLineEdit(product.name if product else "")
        self.name_edit.setMaxLength(80)
        self.name_edit.textChanged.connect(self._on_product_name_changed)
        self.name_hint = QLabel("")
        self.name_hint.setProperty("class", "ValidationHint")
        self.name_hint.hide()

        left_layout.addWidget(self.name_lbl)
        left_layout.addWidget(self.name_edit)
        left_layout.addWidget(self.name_hint)

        # Bilingual counterpart field + suggestion chip + optional online translate
        initial_alt = ""
        if product is not None:
            if detect_primary_script(product.name) == "ar":
                initial_alt = product.name_en or ""
            else:
                initial_alt = product.name_ar or ""
            self._alt_name_edited_manually = bool(initial_alt.strip())

        self.alt_name_lbl = QLabel("")
        self.alt_name_lbl.setStyleSheet("font-weight: 600; font-size: 12px;")
        self.alt_name_edit = QLineEdit(initial_alt)
        self.alt_name_edit.setMaxLength(80)
        self.alt_name_edit.textEdited.connect(self._on_alt_name_edited)
        self.alt_name_edit.textChanged.connect(self._on_alt_name_changed)

        alt_btn_row = QHBoxLayout()
        alt_btn_row.setContentsMargins(0, 0, 0, 0)
        alt_btn_row.setSpacing(6)
        self.suggest_chip_btn = QPushButton("")
        self.suggest_chip_btn.setProperty("class", "CategoryChip")
        self.suggest_chip_btn.setStyleSheet("padding: 3px 9px; font-size: 11px;")
        self.suggest_chip_btn.setCursor(Qt.PointingHandCursor)
        self.suggest_chip_btn.clicked.connect(self._apply_bilingual_suggestion)
        self.suggest_chip_btn.hide()

        self.online_translate_btn = QPushButton("")
        self.online_translate_btn.setProperty("role", "secondary")
        self.online_translate_btn.setStyleSheet("padding: 3px 9px; font-size: 11px;")
        self.online_translate_btn.setCursor(Qt.PointingHandCursor)
        self.online_translate_btn.clicked.connect(self._trigger_online_translation)
        self.online_translate_btn.setVisible(bool(self.config.online_translation_enabled))
        attach_button_feedback(self.online_translate_btn)

        alt_btn_row.addWidget(self.suggest_chip_btn)
        alt_btn_row.addWidget(self.online_translate_btn)
        alt_btn_row.addStretch(1)

        left_layout.addWidget(self.alt_name_lbl)
        left_layout.addWidget(self.alt_name_edit)
        left_layout.addLayout(alt_btn_row)

        self.cat_lbl = QLabel("")
        self.cat_lbl.setStyleSheet("font-weight: 600;")
        self.category_combo = SmartCategoryComboBox(
            db=self.db,
            existing_categories=existing_categories,
            is_admin=self._is_admin,
        )
        if product is not None:
            self.category_combo.select_category(
                category_id=product.category_id,
                name=product.category,
            )
        self.category_combo.currentTextChanged.connect(self._on_category_changed)
        self.category_combo.pending_draft_changed.connect(self._on_pending_category_changed)
        self.category_combo.categories_mutated.connect(self._on_categories_mutated)

        self.pending_cat_badge = QLabel("")
        self.pending_cat_badge.setProperty("class", "PeachBadge")
        self.pending_cat_badge.setWordWrap(True)
        self.pending_cat_badge.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.pending_cat_badge.hide()

        self.cat_hint = QLabel("")
        self.cat_hint.setProperty("class", "ValidationHint")
        self.cat_hint.setWordWrap(True)
        self.cat_hint.hide()

        left_layout.addWidget(self.cat_lbl)
        left_layout.addWidget(self.category_combo)
        left_layout.addWidget(self.pending_cat_badge)
        left_layout.addWidget(self.cat_hint)

        spec = get_currency(self.currency_code)
        self.price_lbl = QLabel("")
        self.price_lbl.setStyleSheet("font-weight: 600;")
        self.price_edit = QLineEdit(
            format_major_input(product.price_minor, self.currency_code) if product else ""
        )
        self.price_edit.setPlaceholderText("0.00" if spec.decimals == 2 else "0")
        self.price_edit.textChanged.connect(self._validate_form_live)
        self.price_hint = QLabel("")
        self.price_hint.setProperty("class", "MutedLabel")

        left_layout.addWidget(self.price_lbl)
        left_layout.addWidget(self.price_edit)
        left_layout.addWidget(self.price_hint)

        left_layout.addSpacing(2)

        tip_box = QFrame()
        tip_box.setProperty("class", "CozySubCard")
        tip_layout = QVBoxLayout(tip_box)
        tip_layout.setContentsMargins(11, 9, 11, 9)
        tip_layout.setSpacing(3)
        self.tip_title_lbl = QLabel("")
        self.tip_title_lbl.setStyleSheet("font-size: 12px; font-weight: 700;")
        self.tip_desc_lbl = QLabel("")
        self.tip_desc_lbl.setWordWrap(True)
        self.tip_desc_lbl.setProperty("class", "MutedLabel")
        tip_layout.addWidget(self.tip_title_lbl)
        tip_layout.addWidget(self.tip_desc_lbl)
        left_layout.addWidget(tip_box)
        left_layout.addStretch(1)

        left_scroll.setWidget(left_host)
        left_outer.addWidget(left_scroll)
        body_cols.addWidget(left_card)

        # ---------------- RIGHT COLUMN: Photo Helper & Product Modifiers Tabs ----------------
        right_card = QFrame()
        right_card.setProperty("class", "BoutiqueCard")
        right_outer = QVBoxLayout(right_card)
        right_outer.setContentsMargins(12, 10, 12, 12)
        right_outer.setSpacing(6)

        self.right_tabs = QTabWidget()

        self.photo_tab = QWidget()
        right_layout = QVBoxLayout(self.photo_tab)
        right_layout.setContentsMargins(6, 10, 6, 4)
        right_layout.setSpacing(9)

        photo_hdr_row = QHBoxLayout()
        self.photo_hdr_lbl = QLabel("")
        self.photo_hdr_lbl.setProperty("class", "SectionHeader")
        self.provider_tag_lbl = QLabel("")
        self.provider_tag_lbl.setProperty("class", "PrimaryBadge")
        photo_hdr_row.addWidget(self.photo_hdr_lbl)
        photo_hdr_row.addStretch(1)
        photo_hdr_row.addWidget(self.provider_tag_lbl)
        right_layout.addLayout(photo_hdr_row)

        preview_box = QFrame()
        preview_box.setProperty("class", "CozySubCard")
        preview_row = QHBoxLayout(preview_box)
        preview_row.setContentsMargins(10, 9, 10, 9)
        preview_row.setSpacing(10)

        self.preview_lbl = QLabel()
        self.preview_lbl.setFixedSize(92, 92)
        self.preview_lbl.setAlignment(Qt.AlignCenter)
        self.preview_lbl.setStyleSheet(
            "background-color: #FFFFFF; border: 1px solid #D8DED3; border-radius: 12px;"
        )
        preview_row.addWidget(self.preview_lbl)

        controls_col = QVBoxLayout()
        controls_col.setSpacing(5)

        self.image_status_lbl = QLabel("")
        self.image_status_lbl.setWordWrap(True)
        self.image_status_lbl.setStyleSheet("font-size: 12px; font-weight: 600;")

        self.image_attr_lbl = QLabel("")
        self.image_attr_lbl.setWordWrap(True)
        self.image_attr_lbl.setProperty("class", "MutedLabel")

        fit_row = QHBoxLayout()
        fit_row.setSpacing(5)
        self.fit_cover_btn = QPushButton("")
        self.fit_cover_btn.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        self.fit_cover_btn.setStyleSheet("padding: 4px 10px;")
        self.fit_cover_btn.clicked.connect(lambda: self._set_fit_mode("cover"))

        self.fit_contain_btn = QPushButton("")
        self.fit_contain_btn.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        self.fit_contain_btn.setStyleSheet("padding: 4px 10px;")
        self.fit_contain_btn.clicked.connect(lambda: self._set_fit_mode("contain"))

        self.zoom_lbl = QLabel("")
        self.zoom_lbl.setProperty("class", "MutedLabel")
        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setRange(100, 180)
        self.zoom_slider.setValue(100)
        self.zoom_slider.setMinimumWidth(62)
        self.zoom_slider.setMaximumWidth(95)
        self.zoom_slider.valueChanged.connect(self._on_zoom_changed)

        self.upload_btn = QPushButton("")
        self.upload_btn.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        self.upload_btn.setStyleSheet("padding: 4px 10px;")
        self.upload_btn.setIcon(get_svg_icon("upload", "#202820", 13))
        self.upload_btn.setIconSize(QSize(13, 13))
        self.upload_btn.clicked.connect(self._upload_local_image)

        self.cutout_btn = QPushButton("")
        self.cutout_btn.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        self.cutout_btn.setStyleSheet("padding: 4px 10px;")
        self.cutout_btn.setIcon(get_svg_icon("wand", "#202820", 13))
        self.cutout_btn.setIconSize(QSize(13, 13))
        self.cutout_btn.clicked.connect(self._remove_white_bg)

        self.remove_img_btn = QPushButton("")
        self.remove_img_btn.setProperty("role", "danger")
        self.remove_img_btn.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        self.remove_img_btn.setStyleSheet("padding: 4px 10px;")
        self.remove_img_btn.setIcon(get_svg_icon("trash", "#B23A3A", 12))
        self.remove_img_btn.setIconSize(QSize(12, 12))
        self.remove_img_btn.clicked.connect(self._remove_image)

        fit_row.addWidget(self.fit_cover_btn)
        fit_row.addWidget(self.fit_contain_btn)
        fit_row.addWidget(self.zoom_lbl)
        fit_row.addWidget(self.zoom_slider)
        fit_row.addStretch(1)

        upload_row = QHBoxLayout()
        upload_row.setSpacing(6)
        upload_row.addWidget(self.upload_btn)
        upload_row.addWidget(self.cutout_btn)
        upload_row.addWidget(self.remove_img_btn)
        upload_row.addStretch(1)

        controls_col.addWidget(self.image_status_lbl)
        controls_col.addWidget(self.image_attr_lbl)
        controls_col.addLayout(fit_row)
        controls_col.addLayout(upload_row)
        preview_row.addLayout(controls_col, 1)
        right_layout.addWidget(preview_box)

        search_bar_row = QHBoxLayout()
        search_bar_row.setSpacing(6)
        self.photo_search_input = SearchLineEdit("")
        self.photo_search_input.textEdited.connect(self._mark_manual_query_edit)
        self.photo_search_input.returnPressed.connect(self._run_manual_photo_search)

        self.search_photos_btn = QPushButton("")
        self.search_photos_btn.setProperty("role", "secondary")
        self.search_photos_btn.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        self.search_photos_btn.setStyleSheet("padding: 6px 12px;")
        self.search_photos_btn.clicked.connect(self._run_manual_photo_search)

        self.cancel_search_btn = QPushButton("")
        self.cancel_search_btn.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        self.cancel_search_btn.setVisible(False)
        self.cancel_search_btn.clicked.connect(self._cancel_active_search)

        self.google_btn = QPushButton("")
        self.google_btn.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        self.google_btn.setStyleSheet("padding: 6px 10px;")
        self.google_btn.setIcon(get_svg_icon("external", "#202820", 13))
        self.google_btn.setIconSize(QSize(13, 13))
        self.google_btn.clicked.connect(self._open_google_images)

        search_bar_row.addWidget(self.photo_search_input, 1)
        search_bar_row.addWidget(self.search_photos_btn)
        search_bar_row.addWidget(self.cancel_search_btn)
        search_bar_row.addWidget(self.google_btn)
        right_layout.addLayout(search_bar_row)

        self.chips_host = QWidget()
        self.chips_flow = FlowLayout(self.chips_host, margin=0, h_spacing=6, v_spacing=5)
        right_layout.addWidget(self.chips_host)

        self.gallery_status_lbl = QLabel("")
        self.gallery_status_lbl.setWordWrap(True)
        self.gallery_status_lbl.setProperty("class", "MutedLabel")
        right_layout.addWidget(self.gallery_status_lbl)

        self.gallery_scroll = QScrollArea()
        self.gallery_scroll.setWidgetResizable(True)
        self.gallery_scroll.setMinimumHeight(112)
        self.gallery_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.gallery_host = QWidget()
        self.gallery_host.setObjectName("GalleryHost")
        self.gallery_grid = QGridLayout(self.gallery_host)
        self.gallery_grid.setContentsMargins(2, 2, 4, 2)
        self.gallery_grid.setSpacing(8)
        self.gallery_grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.gallery_scroll.setWidget(self.gallery_host)
        right_layout.addWidget(self.gallery_scroll, 1)

        self.right_tabs.addTab(self.photo_tab, tr("product_editor.tab_photo"))
        self.modifiers_tab = self._build_modifiers_tab()
        self.right_tabs.addTab(self.modifiers_tab, tr("product_editor.tab_modifiers"))
        self.recipe_tab = self._build_recipe_tab()
        self.right_tabs.addTab(self.recipe_tab, tr("product_editor.tab_recipe"))
        if self.db is None:
            self.right_tabs.setTabEnabled(1, False)
            self.right_tabs.setTabEnabled(2, False)

        right_outer.addWidget(self.right_tabs, 1)
        body_cols.addWidget(right_card, 1)
        root.addLayout(body_cols, 1)

        footer_row = QHBoxLayout()
        self.form_status_lbl = QLabel("")
        self.form_status_lbl.setProperty("class", "MutedLabel")

        self.cancel_btn = QPushButton("")
        self.cancel_btn.clicked.connect(self.reject)

        self.save_btn = QPushButton("")
        self.save_btn.setProperty("role", "primary")
        self.save_btn.setIcon(get_svg_icon("check", contrast_text_hex(self.config.accent_color), 14))
        self.save_btn.setIconSize(QSize(14, 14))
        self.save_btn.setDefault(True)
        self.save_btn.clicked.connect(self._on_save)

        footer_row.addWidget(self.form_status_lbl, 1)
        footer_row.addWidget(self.cancel_btn)
        footer_row.addWidget(self.save_btn)
        attach_button_feedback(self.cancel_btn)
        attach_button_feedback(self.save_btn)
        root.addLayout(footer_row)

    def _current_category_canonical(self) -> str:
        return self.category_combo.current_primary_name()

    def _effective_bilingual_names(self) -> tuple[str, str]:
        name = " ".join(self.name_edit.text().strip().split())[:80]
        alt = " ".join(self.alt_name_edit.text().strip().split())[:80]
        if not name:
            return "", ""
        if detect_primary_script(name) == "ar":
            return alt, name
        return name, alt

    def retranslate_ui(self) -> None:
        self.setLayoutDirection(layout_direction())
        if self._editing_product:
            self.setWindowTitle(
                tr("product_editor.title.edit", name=self._editing_product.display_name())
            )
        else:
            self.setWindowTitle(tr("product_editor.title.add"))

        self.details_hdr.setText(tr("product_editor.section.details"))
        self.details_sub.setText(tr("product_editor.details_req_hint"))
        self.name_lbl.setText(tr("product_editor.name_label"))
        self.name_edit.setPlaceholderText(tr("product_editor.name_ph"))
        self.cat_lbl.setText(tr("product_editor.cat_req_label"))
        self.pending_cat_badge.setText(tr("cat_combo.pending_draft"))

        self.category_combo.retranslate_ui()
        self._sync_bilingual_labels()
        self.online_translate_btn.setText(tr("product_editor.online_translate_btn"))
        self.online_translate_btn.setVisible(bool(self.config.online_translation_enabled))

        spec = get_currency(self.currency_code)
        self.price_lbl.setText(tr("product_editor.price_label", currency=spec.code))
        self.tip_title_lbl.setText(tr("product_editor.tip_title"))
        self.tip_desc_lbl.setText(tr("product_editor.tip_desc"))

        self.photo_hdr_lbl.setText(tr("product_editor.photo_header"))
        self.provider_tag_lbl.setText(
            tr("product_editor.provider_ov")
            if self.config.image_provider == "openverse"
            else tr("product_editor.provider_px")
        )
        self.fit_cover_btn.setText(tr("product_editor.fit_fill"))
        self.fit_cover_btn.setToolTip(tr("product_editor.fit_fill_tip"))
        self.fit_contain_btn.setText(tr("product_editor.fit_contain"))
        self.fit_contain_btn.setToolTip(tr("product_editor.fit_contain_tip"))
        self.zoom_lbl.setText(tr("product_editor.zoom_label"))
        self.upload_btn.setText(tr("product_editor.upload_short"))
        self.upload_btn.setToolTip(tr("product_editor.upload_tip"))
        self.cutout_btn.setText(tr("product_editor.cutout_btn"))
        self.cutout_btn.setToolTip(tr("product_editor.cutout_tip"))
        self.remove_img_btn.setText(tr("common.clear"))
        self.remove_img_btn.setToolTip(tr("product_editor.remove_img_tip"))
        self.photo_search_input.setPlaceholderText(tr("product_editor.photo_search_ph"))
        self.search_photos_btn.setText(tr("product_editor.search_btn_short"))
        self.cancel_search_btn.setText(tr("common.cancel"))
        self.google_btn.setText(tr("product_editor.google_btn"))
        self.google_btn.setToolTip(tr("product_editor.google_tip"))
        self.cancel_btn.setText(tr("common.cancel"))
        self.save_btn.setText(tr("product_editor.save_btn"))
        self.right_tabs.setTabText(0, tr("product_editor.tab_photo"))
        self.right_tabs.setTabText(1, tr("product_editor.tab_modifiers"))
        self.right_tabs.setTabText(2, tr("product_editor.tab_recipe"))
        if self.recipe_editor is not None:
            self.recipe_hint_lbl.setText(tr("product_editor.recipe_hint"))
            self.recipe_editor.retranslate_ui()
        self.mod_hint_lbl.setText(tr("product_editor.modifiers_assign_hint"))
        self.manage_mods_btn.setText(tr("product_editor.modifiers_manage_link"))
        self.manage_mods_btn.setVisible(self._is_admin and self.db is not None)
        self._load_modifiers_tab()

        if self._gallery_status_key:
            self.gallery_status_lbl.setText(
                tr(self._gallery_status_key, **self._gallery_status_kwargs)
            )

        self._refresh_query_chips()
        self._refresh_image_preview()
        self._validate_form_live()

    def _build_modifiers_tab(self) -> QWidget:
        tab = QWidget()
        lay = QVBoxLayout(tab)
        lay.setContentsMargins(6, 10, 6, 4)
        lay.setSpacing(8)

        top_row = QHBoxLayout()
        top_row.setSpacing(8)
        self.mod_hint_lbl = QLabel(tr("product_editor.modifiers_assign_hint"))
        self.mod_hint_lbl.setProperty("class", "MutedLabel")
        self.mod_hint_lbl.setWordWrap(True)
        top_row.addWidget(self.mod_hint_lbl, 1)

        self.manage_mods_btn = QPushButton(tr("product_editor.modifiers_manage_link"))
        self.manage_mods_btn.setProperty("role", "secondary")
        self.manage_mods_btn.setIcon(get_svg_icon("sliders", "#202820", 13))
        self.manage_mods_btn.setIconSize(QSize(13, 13))
        self.manage_mods_btn.setStyleSheet("padding: 5px 10px; font-size: 11.5px;")
        self.manage_mods_btn.clicked.connect(self._open_modifier_manager)
        attach_button_feedback(self.manage_mods_btn)
        top_row.addWidget(self.manage_mods_btn)
        lay.addLayout(top_row)

        self.mods_scroll = QScrollArea()
        self.mods_scroll.setWidgetResizable(True)
        self.mods_scroll.setFrameShape(QFrame.NoFrame)
        self.mods_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.mods_host = QWidget()
        self.mods_host.setObjectName("CheckoutScrollHost")
        self.mods_layout = QVBoxLayout(self.mods_host)
        self.mods_layout.setContentsMargins(2, 2, 4, 2)
        self.mods_layout.setSpacing(8)
        self.mods_layout.addStretch(1)
        self.mods_scroll.setWidget(self.mods_host)
        lay.addWidget(self.mods_scroll, 1)
        return tab

    def _build_recipe_tab(self) -> QWidget:
        tab = QWidget()
        lay = QVBoxLayout(tab)
        lay.setContentsMargins(6, 10, 6, 4)
        lay.setSpacing(8)
        self.recipe_hint_lbl = QLabel(tr("product_editor.recipe_hint"))
        self.recipe_hint_lbl.setProperty("class", "MutedLabel")
        self.recipe_hint_lbl.setWordWrap(True)
        lay.addWidget(self.recipe_hint_lbl)

        if self.db is not None:
            self.recipe_editor = RecipeEditorWidget(
                self.db,
                self.config,
                editable=self._is_admin,
                parent=tab,
            )
            if self._editing_product is not None:
                self.recipe_editor.load_entries(
                    self.db.get_product_recipe(self._editing_product.id)
                )
            lay.addWidget(self.recipe_editor, 1)
        else:
            lay.addStretch(1)
        return tab

    def _snapshot_modifier_draft(self) -> None:
        if not self._mod_group_checks:
            return
        self._pending_mod_group_ids = {
            gid for gid, chk in self._mod_group_checks.items() if chk.isChecked()
        }
        updated_ov: dict[int, int] = dict(self._pending_mod_overrides)
        for oid, edit in self._mod_override_edits.items():
            raw = edit.text().strip()
            if not raw:
                updated_ov.pop(oid, None)
            else:
                try:
                    minor = parse_major_to_minor(raw, self.currency_code)
                    if minor >= 0:
                        updated_ov[oid] = minor
                except ValueError:
                    pass
        self._pending_mod_overrides = updated_ov

    def _load_modifiers_tab(self) -> None:
        self._snapshot_modifier_draft()
        self._mod_group_checks.clear()
        self._mod_override_edits.clear()
        self._mod_group_opt_hosts.clear()
        while self.mods_layout.count():
            item = self.mods_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

        if self.db is None:
            self.mods_layout.addStretch(1)
            return

        groups = self.db.list_modifier_groups(include_inactive=True, include_archived=False)
        self._mod_groups_cache = groups
        if not groups:
            empty_lbl = QLabel(tr("product_editor.modifiers_none"))
            empty_lbl.setProperty("class", "MutedLabel")
            empty_lbl.setWordWrap(True)
            empty_lbl.setAlignment(Qt.AlignCenter)
            empty_lbl.setContentsMargins(16, 28, 16, 28)
            self.mods_layout.addWidget(empty_lbl)
            self.mods_layout.addStretch(1)
            return

        spec = get_currency(self.currency_code)
        for grp in groups:
            card = QFrame()
            card.setProperty("class", "CozySubCard")
            card_lay = QVBoxLayout(card)
            card_lay.setContentsMargins(11, 9, 11, 9)
            card_lay.setSpacing(6)

            hdr_row = QHBoxLayout()
            hdr_row.setSpacing(6)
            mode_txt = (
                tr("modifiers.mode_single")
                if grp.selection_mode == "single"
                else tr("modifiers.mode_multi")
            )
            req_txt = (
                tr("customize.required_badge")
                if grp.is_required
                else tr("customize.optional_badge")
            )
            chk = QCheckBox(f"{grp.display_name().replace('&', '&&')}  ({mode_txt} · {req_txt})")
            chk.setChecked(grp.id in self._pending_mod_group_ids)
            chk.setEnabled(self._is_admin)
            self._mod_group_checks[grp.id] = chk
            hdr_row.addWidget(chk, 1)

            if not grp.is_active:
                dis_badge = QLabel(tr("modifiers.status_disabled"))
                dis_badge.setProperty("class", "PeachBadge")
                hdr_row.addWidget(dis_badge)
            card_lay.addLayout(hdr_row)

            opts_host = QWidget()
            opts_lay = QVBoxLayout(opts_host)
            opts_lay.setContentsMargins(18, 2, 0, 2)
            opts_lay.setSpacing(4)
            for opt in grp.options:
                if opt.is_archived:
                    continue
                orow = QHBoxLayout()
                orow.setSpacing(6)
                olbl = QLabel(opt.display_name())
                olbl.setStyleSheet("font-size: 12px; font-weight: 600; color: #202820;")
                def_lbl = QLabel(
                    tr(
                        "product_editor.modifiers_default_hint",
                        price=format_minor(opt.price_delta_minor, self.currency_code),
                    )
                )
                def_lbl.setProperty("class", "MutedLabel")

                ov_edit = QLineEdit()
                ov_edit.setFixedWidth(84)
                ov_edit.setPlaceholderText(format_major_input(opt.price_delta_minor, self.currency_code))
                ov_edit.setEnabled(self._is_admin)
                if opt.id in self._pending_mod_overrides:
                    ov_edit.setText(
                        format_major_input(self._pending_mod_overrides[opt.id], self.currency_code)
                    )
                self._mod_override_edits[opt.id] = ov_edit

                reset_btn = QPushButton(tr("product_editor.modifiers_reset_btn"))
                reset_btn.setStyleSheet("padding: 3px 8px; font-size: 11px;")
                reset_btn.setVisible(bool(ov_edit.text().strip()))
                reset_btn.setEnabled(self._is_admin)
                ov_edit.textChanged.connect(
                    lambda txt, b=reset_btn: b.setVisible(bool(txt.strip()))
                )
                reset_btn.clicked.connect(lambda _c=False, e=ov_edit: e.clear())

                orow.addWidget(olbl, 1)
                orow.addWidget(def_lbl)
                orow.addWidget(ov_edit)
                orow.addWidget(reset_btn)
                opts_lay.addLayout(orow)

            opts_host.setVisible(chk.isChecked())
            chk.toggled.connect(opts_host.setVisible)
            self._mod_group_opt_hosts[grp.id] = opts_host
            card_lay.addWidget(opts_host)
            self.mods_layout.addWidget(card)

        self.mods_layout.addStretch(1)

    def _open_modifier_manager(self) -> None:
        if self.db is None or not self._is_admin:
            return
        self._snapshot_modifier_draft()
        dlg = ModifierManagerDialog(self.db, self.config, parent=self)
        dlg.modifiers_mutated.connect(self._on_modifier_manager_mutated)
        dlg.exec()
        self._load_modifiers_tab()

    def _on_modifier_manager_mutated(self) -> None:
        if self._editing_product is not None and self.db is not None:
            # Refresh current group assignments from DB if edited externally for this product
            fresh = self.db.get_product_modifiers(self._editing_product.id, include_inactive=True)
            for g in fresh:
                self._pending_mod_group_ids.add(g.id)
        self._load_modifiers_tab()

    def _sync_bilingual_labels(self) -> None:
        primary = detect_primary_script(self.name_edit.text())
        if primary == "ar":
            self.alt_name_lbl.setText(tr("product_editor.bilingual_en_label"))
            self.alt_name_edit.setPlaceholderText(tr("product_editor.bilingual_en_ph"))
            self.alt_name_edit.setLayoutDirection(Qt.LeftToRight)
        else:
            self.alt_name_lbl.setText(tr("product_editor.bilingual_ar_label"))
            self.alt_name_edit.setPlaceholderText(tr("product_editor.bilingual_ar_ph"))
            self.alt_name_edit.setLayoutDirection(Qt.RightToLeft)

    def _on_alt_name_edited(self, _text: str) -> None:
        self._alt_name_edited_manually = True

    def _on_alt_name_changed(self, _text: str) -> None:
        self._refresh_bilingual_suggestion(auto_fill=False)
        self._refresh_query_chips()
        if self._raw_image_bytes is None:
            self._refresh_image_preview()

    def _refresh_bilingual_suggestion(self, *, auto_fill: bool = False) -> None:
        name = " ".join(self.name_edit.text().strip().split())
        self._sync_bilingual_labels()
        if not name:
            self._suggested_alt_text = ""
            if auto_fill and not self._alt_name_edited_manually:
                self.alt_name_edit.blockSignals(True)
                self.alt_name_edit.clear()
                self.alt_name_edit.blockSignals(False)
            self.suggest_chip_btn.hide()
            return

        cache_lookup = self.db.get_cached_translation if self.db is not None else None
        sugg = suggest_bilingual_name(name, is_category=False, cache_lookup=cache_lookup)
        primary = detect_primary_script(name)
        counterpart = sugg.target_text if sugg.target_lang != primary else ""
        self._suggested_alt_text = counterpart

        if auto_fill and not self._alt_name_edited_manually:
            self.alt_name_edit.blockSignals(True)
            self.alt_name_edit.setText(counterpart)
            self.alt_name_edit.blockSignals(False)

        cur_alt = self.alt_name_edit.text().strip()
        if counterpart and counterpart != cur_alt:
            self.suggest_chip_btn.setText(
                tr("product_editor.suggest_chip", text=counterpart).replace("&", "&&")
            )
            self.suggest_chip_btn.show()
        else:
            self.suggest_chip_btn.hide()

        self.online_translate_btn.setVisible(bool(self.config.online_translation_enabled))

    def _apply_bilingual_suggestion(self) -> None:
        if not self._suggested_alt_text:
            return
        self._alt_name_edited_manually = False
        self.alt_name_edit.setText(self._suggested_alt_text)
        self.suggest_chip_btn.hide()

    def _trigger_online_translation(self) -> None:
        if not self.config.online_translation_enabled:
            return
        raw = self.name_edit.text().strip()
        if not raw:
            return
        if self._translate_worker is not None and self._translate_worker.isRunning():
            return
        primary = detect_primary_script(raw)
        src = "ar" if primary == "ar" else "en"
        dst = "en" if primary == "ar" else "ar"
        self.online_translate_btn.setEnabled(False)
        self.online_translate_btn.setText(tr("product_editor.online_translating"))
        w = _OnlineTranslateWorker(raw, src, dst, parent=None)
        w.translated.connect(self._on_online_translation_ready)
        w.failed.connect(self._on_online_translation_failed)
        w.finished.connect(self._on_online_translation_finished)
        self._translate_worker = w
        w.start()

    def _on_online_translation_ready(self, translated_text: str) -> None:
        clean = " ".join((translated_text or "").strip().split())[:80]
        if clean:
            self._alt_name_edited_manually = True
            self.alt_name_edit.setText(clean)
            self.suggest_chip_btn.hide()

    def _on_online_translation_failed(self, _err: str) -> None:
        pass

    def _on_online_translation_finished(self) -> None:
        self.online_translate_btn.setEnabled(True)
        self.online_translate_btn.setText(tr("product_editor.online_translate_btn"))

    def _on_pending_category_changed(self, is_pending: bool) -> None:
        self.pending_cat_badge.setVisible(bool(is_pending) and self._is_admin)
        self._validate_form_live()

    def _on_categories_mutated(self) -> None:
        if self.db is not None:
            self._existing_categories = [
                c.primary_name for c in self.db.list_category_records(include_archived=False)
            ]
        self.categories_mutated.emit()
        self._on_category_changed()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._did_animate_in:
            self._did_animate_in = True
            animate_window_fade_in(self)

    def _mark_manual_query_edit(self) -> None:
        self._user_edited_image_query = bool(self.photo_search_input.text().strip())

    def _on_product_name_changed(self, text: str) -> None:
        self._refresh_bilingual_suggestion(auto_fill=(not self._alt_name_edited_manually))
        self._validate_form_live()
        self._refresh_query_chips()
        if self._raw_image_bytes is None:
            self._refresh_image_preview()
        if self._editing_product is None and not self._user_edited_image_query:
            clean = text.strip()
            en_name, _ar_name = self._effective_bilingual_names()
            suggestions = suggest_image_queries(
                clean, self._current_category_canonical(), product_name_en=en_name
            )
            query_for_box = (
                suggestions[0]
                if (detect_primary_script(clean) == "ar" and suggestions)
                else (en_name or clean)
            )
            self.photo_search_input.setText(query_for_box)
            self.photo_search_input.home(False)
            if len(clean) >= 3:
                self._suggest_timer.start(600)
            else:
                self._suggest_timer.stop()

    def _on_category_changed(self) -> None:
        self._refresh_query_chips()
        self._validate_form_live()
        if self._raw_image_bytes is None:
            self._refresh_image_preview()

    def _on_name_debounce_elapsed(self) -> None:
        query = self.photo_search_input.text().strip()
        if len(query) >= 3:
            self.start_image_search(query)

    def _refresh_query_chips(self) -> None:
        en_name, _ar_name = self._effective_bilingual_names()
        sig = (
            self.name_edit.text().strip(),
            self._current_category_canonical(),
            en_name,
        )
        if sig == self._last_chip_sig and self.chips_flow.count() > 0:
            return
        self._last_chip_sig = sig

        while self.chips_flow.count():
            item = self.chips_flow.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()

        suggestions = suggest_image_queries(
            sig[0],
            sig[1],
            product_name_en=sig[2],
        )
        if not suggestions:
            hint = QLabel(tr("product_editor.chips_empty"))
            hint.setProperty("class", "MutedLabel")
            self.chips_flow.addWidget(hint)
            return

        prefix = QLabel(tr("product_editor.chips_prefix"))
        prefix.setProperty("class", "MutedLabel")
        self.chips_flow.addWidget(prefix)
        for q in suggestions:
            chip = QPushButton(q.replace("&", "&&"))
            chip.setProperty("class", "CategoryChip")
            chip.setStyleSheet("padding: 3px 10px; font-size: 11px;")
            chip.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
            chip.clicked.connect(lambda _c=False, query=q: self._trigger_chip_search(query))
            self.chips_flow.addWidget(chip)

    def _trigger_chip_search(self, query: str) -> None:
        self._user_edited_image_query = True
        self.photo_search_input.setText(query)
        self.photo_search_input.home(False)
        self.start_image_search(query)

    def _run_manual_photo_search(self) -> None:
        q = self.photo_search_input.text().strip() or self.name_edit.text().strip()
        if not q:
            self._gallery_status_key = "product_editor.enter_term_hint"
            self._gallery_status_kwargs = {}
            self.gallery_status_lbl.setText(tr("product_editor.enter_term_hint"))
            return
        self.photo_search_input.setText(q)
        self.photo_search_input.home(False)
        self.start_image_search(q)

    def _retire_worker(self, worker) -> None:
        if worker is None:
            return
        cancel_fn = getattr(worker, "cancel", None)
        if callable(cancel_fn):
            cancel_fn()
        if isinstance(worker, (ImageSearchWorker, ThumbnailBatchWorker, ImageDownloadWorker)):
            if worker.isRunning():
                self._retired_workers.append(worker)
                worker.finished.connect(
                    lambda w=worker: self._retired_workers.remove(w)
                    if w in self._retired_workers
                    else None
                )
            else:
                worker.deleteLater()

    def _cancel_active_search(self) -> None:
        self._retire_worker(self._search_worker)
        self._search_worker = None
        self._retire_worker(self._thumb_worker)
        self._thumb_worker = None
        self.cancel_search_btn.setVisible(False)
        self.search_photos_btn.setEnabled(True)
        self._gallery_status_key = "product_editor.search_cancelled"
        self._gallery_status_kwargs = {}
        self.gallery_status_lbl.setText(tr("product_editor.search_cancelled"))

    def start_image_search(self, query: str) -> None:
        self._cancel_active_search()
        clean_q = query.strip()
        if not clean_q:
            return

        provider_name = (
            tr("provider.short.openverse")
            if self.config.image_provider == "openverse"
            else tr("provider.short.pexels")
        )
        self._gallery_status_key = "product_editor.searching_provider"
        self._gallery_status_kwargs = {"provider": provider_name, "query": clean_q}
        self.gallery_status_lbl.setText(
            tr("product_editor.searching_provider", provider=provider_name, query=clean_q)
        )
        self.cancel_search_btn.setVisible(True)
        self.search_photos_btn.setEnabled(False)

        worker = ImageSearchWorker(
            self.config.image_provider,
            self.config.pexels_api_key,
            clean_q,
            limit=8,
            parent=self,
        )
        worker.results_ready.connect(self._on_search_results)
        worker.search_failed.connect(self._on_search_failed)
        self._search_worker = worker
        worker.start()

    def _compute_gallery_cols(self) -> int:
        vp_w = max(240, self.gallery_scroll.viewport().width())
        return max(2, min(4, (vp_w - 6) // 114))

    def _relayout_gallery_cards(self) -> None:
        if not self._gallery_card_list:
            return
        cols = self._compute_gallery_cols()
        self._last_gallery_cols = cols
        while self.gallery_grid.count():
            self.gallery_grid.takeAt(0)
        for idx, card in enumerate(self._gallery_card_list):
            self.gallery_grid.addWidget(card, idx // cols, idx % cols)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._gallery_card_list:
            new_cols = self._compute_gallery_cols()
            if new_cols != self._last_gallery_cols:
                self._relayout_gallery_cards()

    def _on_search_results(self, query: str, results: list[ImageSearchResult]) -> None:
        if query != self.photo_search_input.text().strip() and self._search_worker is None:
            return
        self.photo_search_input.home(False)
        self.cancel_search_btn.setVisible(False)
        self.search_photos_btn.setEnabled(True)
        self._search_results = list(results)
        self._clear_gallery_grid()

        if not results:
            self._gallery_status_key = "product_editor.no_photos_for"
            self._gallery_status_kwargs = {"query": query}
            self.gallery_status_lbl.setText(
                tr("product_editor.no_photos_for", query=query)
            )
            return

        self._gallery_status_key = "product_editor.found_photos"
        self._gallery_status_kwargs = {"count": len(results), "query": query}
        self.gallery_status_lbl.setText(
            tr("product_editor.found_photos", count=len(results), query=query)
        )
        urls: list[str] = []
        for item in results[:8]:
            card = GalleryThumbCard(item)
            card.set_selected(item.id == self._selected_result_id)
            card.selected.connect(self._on_gallery_item_clicked)
            self._gallery_cards[item.id] = card
            self._gallery_cards[item.thumbnail_url] = card
            self._gallery_card_list.append(card)
            urls.append(item.thumbnail_url)
        self._relayout_gallery_cards()

        self._retire_worker(self._thumb_worker)
        thumb_worker = ThumbnailBatchWorker(urls, parent=self)
        thumb_worker.thumbnail_loaded.connect(self._on_thumbnail_loaded)
        self._thumb_worker = thumb_worker
        thumb_worker.start()

    def _on_search_failed(self, _query: str, error_msg: str) -> None:
        self.cancel_search_btn.setVisible(False)
        self.search_photos_btn.setEnabled(True)
        self._clear_gallery_grid()
        self._gallery_status_key = None
        self.gallery_status_lbl.setText(error_msg)

    def _on_thumbnail_loaded(self, url: str, raw_bytes: bytes) -> None:
        card = self._gallery_cards.get(url)
        if card is not None:
            card.set_thumbnail_bytes(raw_bytes)

    def _clear_gallery_grid(self) -> None:
        self._gallery_cards.clear()
        self._gallery_card_list.clear()
        while self.gallery_grid.count():
            item = self.gallery_grid.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()

    def _on_gallery_item_clicked(self, item: ImageSearchResult) -> None:
        self._selected_result_id = item.id
        for card in self._gallery_card_list:
            card.set_selected(card.item.id == item.id)

        self._gallery_status_key = "product_editor.downloading_photo"
        self._gallery_status_kwargs = {"badge": item.badge_label}
        self.gallery_status_lbl.setText(
            tr("product_editor.downloading_photo", badge=item.badge_label)
        )
        self._retire_worker(self._download_worker)
        dl = ImageDownloadWorker(item, parent=self)
        dl.image_downloaded.connect(self._on_image_downloaded)
        dl.download_failed.connect(self._on_download_failed)
        self._download_worker = dl
        dl.start()

    def _on_image_downloaded(self, item: ImageSearchResult, raw_bytes: bytes) -> None:
        self._raw_image_bytes = raw_bytes
        self._image_removed = False
        if has_alpha_transparency(raw_bytes):
            self._fit_mode = "contain"
        self._pending_meta = {
            "source": item.source_provider,
            "attribution": item.attribution,
            "license": item.license_code,
            "source_url": item.landing_url or item.image_url,
        }
        self._gallery_status_key = "product_editor.photo_ready"
        self._gallery_status_kwargs = {"badge": item.badge_label}
        self.gallery_status_lbl.setText(
            tr("product_editor.photo_ready", badge=item.badge_label)
        )
        self._refresh_image_preview()

    def _on_download_failed(self, msg: str) -> None:
        self._gallery_status_key = "product_editor.download_fallback"
        self._gallery_status_kwargs = {"msg": msg}
        self.gallery_status_lbl.setText(
            tr("product_editor.download_fallback", msg=msg)
        )

    def _open_google_images(self) -> None:
        q = self.photo_search_input.text().strip() or self.name_edit.text().strip() or "cafe beverage"
        QDesktopServices.openUrl(QUrl(build_google_images_search_url(q)))

    def _upload_local_image(self) -> None:
        file_str, _ = QFileDialog.getOpenFileName(
            self,
            tr("product_editor.select_img_title"),
            "",
            tr("product_editor.upload_dialog_filter"),
        )
        if not file_str:
            return
        try:
            raw = read_local_product_image_bytes(Path(file_str))
        except ImageSearchError as exc:
            QMessageBox.warning(self, tr("product_editor.invalid_img_title"), str(exc))
            return

        self._raw_image_bytes = raw
        self._image_removed = False
        self._selected_result_id = ""
        if has_alpha_transparency(raw):
            self._fit_mode = "contain"
        self._pending_meta = {
            "source": "local",
            "attribution": tr("product_editor.uploaded_from", name=Path(file_str).name),
            "license": (
                tr("product_editor.license_local_cutout")
                if has_alpha_transparency(raw)
                else tr("product_editor.license_local_file")
            ),
            "source_url": "",
        }
        self._refresh_image_preview()

    def _remove_white_bg(self) -> None:
        if self._raw_image_bytes is None:
            return
        try:
            self._raw_image_bytes = remove_white_background_cutout(self._raw_image_bytes)
        except ImageSearchError as exc:
            QMessageBox.warning(self, tr("product_editor.cutout_error_title"), str(exc))
            return
        self._fit_mode = "contain"
        self._zoom = 1.0
        self.zoom_slider.setValue(100)
        self.image_attr_lbl.setText(
            (self._pending_meta.get("attribution") or tr("products.col.photo"))
            + tr("product_editor.bg_removed_suffix")
        )
        self._refresh_image_preview()

    def _remove_image(self) -> None:
        self._raw_image_bytes = None
        self._image_removed = True
        self._selected_result_id = ""
        self._pending_meta = {
            "source": "",
            "attribution": "",
            "license": "",
            "source_url": "",
        }
        for card in self._gallery_card_list:
            card.set_selected(False)
        self._refresh_image_preview()

    def _set_fit_mode(self, mode: str) -> None:
        self._fit_mode = "contain" if mode == "contain" else "cover"
        self.zoom_slider.setEnabled(self._raw_image_bytes is not None and self._fit_mode == "cover")
        self._refresh_image_preview()

    def _on_zoom_changed(self, value: int) -> None:
        self._zoom = value / 100.0
        self._refresh_image_preview()

    def _refresh_image_preview(self) -> None:
        has_img = self._raw_image_bytes is not None
        self.remove_img_btn.setEnabled(
            has_img or bool(self.result_image_filename and not self._image_removed)
        )
        self.fit_cover_btn.setEnabled(has_img)
        self.fit_contain_btn.setEnabled(has_img)
        self.cutout_btn.setEnabled(has_img)
        self.zoom_slider.setEnabled(has_img and self._fit_mode == "cover")

        self.fit_cover_btn.setProperty("role", "primary" if self._fit_mode == "cover" and has_img else "")
        self.fit_contain_btn.setProperty("role", "primary" if self._fit_mode == "contain" and has_img else "")
        for btn in (self.fit_cover_btn, self.fit_contain_btn):
            btn.style().unpolish(btn)
            btn.style().polish(btn)

        if self._raw_image_bytes is not None:
            preserve_alpha = has_alpha_transparency(self._raw_image_bytes)
            try:
                rendered = render_cropped_product_image(
                    self._raw_image_bytes,
                    fit_mode=self._fit_mode,
                    zoom=self._zoom,
                    output_size=216,
                    preserve_alpha=preserve_alpha,
                )
                pm = QPixmap()
                if pm.loadFromData(rendered) and not pm.isNull():
                    if preserve_alpha:
                        self.preview_lbl.setPixmap(fit_pixmap_centered(pm, 84, 84, radius=0, scale=2))
                    else:
                        self.preview_lbl.setPixmap(clip_pixmap_rounded(pm, 86, 86, radius=9, scale=2))
                    src = self._pending_meta.get("source") or "cached"
                    lic = self._pending_meta.get("license") or (
                        tr("product_editor.transparent_png") if preserve_alpha else ""
                    )
                    attr = self._pending_meta.get("attribution") or tr("product_editor.ready_offline")
                    self.image_status_lbl.setText(
                        tr("product_editor.selected_photo_status", src=src.title())
                        + (f" ({lic})" if lic else "")
                    )
                    self.image_attr_lbl.setText(attr)
                    return
            except ImageSearchError:
                pass

        name_txt = self.name_edit.text().strip()
        en_name, _ar_name = self._effective_bilingual_names()
        asset_key = en_name or name_txt
        bundled = get_bundled_product_asset_path(asset_key) if not self._image_removed else None
        if bundled is not None:
            raw_pm = QPixmap(str(bundled))
            if not raw_pm.isNull():
                self.preview_lbl.setPixmap(fit_pixmap_centered(raw_pm, 84, 84, radius=0, scale=2))
                self.image_status_lbl.setText(tr("product_editor.builtin_studio_status"))
                self.image_attr_lbl.setText(tr("product_editor.builtin_studio_attr"))
                return

        category = self._current_category_canonical() or "Espresso"
        placeholder = render_category_placeholder(
            category, 86, 86, self.config.accent_color, product_name=asset_key
        )
        self.preview_lbl.setPixmap(fit_pixmap_centered(placeholder, 84, 84, radius=0, scale=2))
        self.image_status_lbl.setText(
            tr("product_editor.cat_placeholder_status", category=tr_category(category))
        )
        self.image_attr_lbl.setText(tr("product_editor.cat_placeholder_attr"))

    def _validate_form_live(self) -> bool:
        name = self.name_edit.text().strip()
        price_ok = False
        if not name:
            self.name_hint.setText(tr("product_editor.name_required"))
            self.name_hint.show()
        else:
            self.name_hint.hide()

        sel = self.category_combo.resolve_selection()
        cat_ok = bool(sel.primary_name)
        if sel.archived_conflict is not None:
            self.cat_hint.setText(
                tr("backend.err.category_exists", name=sel.display_name)
            )
            self.cat_hint.show()
            cat_ok = False
        elif not self._is_admin and self.db is not None and sel.primary_name and sel.category_id is None:
            self.cat_hint.setText(tr("cat_combo.create_admin_only"))
            self.cat_hint.show()
            cat_ok = False
        elif not sel.primary_name:
            self.cat_hint.setText(tr("product_editor.category_required"))
            self.cat_hint.show()
            cat_ok = False
        else:
            self.cat_hint.hide()

        raw_price = self.price_edit.text().strip()
        if not raw_price:
            self.price_hint.setText(tr("product_editor.price_hint_idle"))
            self.price_hint.setStyleSheet("color: #5E6B5F;")
        else:
            try:
                minor = parse_major_to_minor(raw_price, self.currency_code)
                if minor <= 0:
                    self.price_hint.setText(tr("product_editor.price_gt_zero"))
                    self.price_hint.setStyleSheet("color: #B23A3A; font-weight: 600;")
                else:
                    self.price_hint.setText(
                        tr(
                            "product_editor.price_preview",
                            price=format_minor(minor, self.currency_code),
                        )
                    )
                    self.price_hint.setStyleSheet("color: #005B37; font-weight: 600;")
                    price_ok = True
            except ValueError as exc:
                self.price_hint.setText(str(exc))
                self.price_hint.setStyleSheet("color: #B23A3A; font-weight: 600;")

        valid = bool(name) and price_ok and cat_ok
        self.form_status_lbl.setText(
            tr("product_editor.status_ready") if valid else tr("product_editor.status_incomplete")
        )
        return valid

    def _on_save(self) -> None:
        self.category_combo.hidePopup()
        name = " ".join(self.name_edit.text().strip().split())
        sel = self.category_combo.resolve_selection()
        if not name:
            QMessageBox.warning(
                self, tr("common.validation_error"), tr("product_editor.name_required")
            )
            self.name_edit.setFocus()
            return
        if not sel.primary_name:
            QMessageBox.warning(
                self, tr("common.validation_error"), tr("product_editor.category_required")
            )
            self.category_combo.setFocus()
            return
        if sel.archived_conflict is not None:
            QMessageBox.warning(
                self,
                tr("common.validation_error"),
                tr("backend.err.category_exists", name=sel.display_name),
            )
            self.category_combo.setFocus()
            return
        if not self._is_admin and self.db is not None and sel.category_id is None:
            QMessageBox.warning(
                self, tr("common.permission_denied"), tr("cat_combo.create_admin_only")
            )
            self.category_combo.setFocus()
            return
        try:
            price_minor = parse_major_to_minor(self.price_edit.text(), self.currency_code)
        except ValueError as exc:
            QMessageBox.warning(self, tr("product_editor.invalid_price_title"), str(exc))
            self.price_edit.setFocus()
            return
        if price_minor <= 0:
            QMessageBox.warning(
                self, tr("product_editor.invalid_price_title"), tr("product_editor.price_gt_zero")
            )
            self.price_edit.setFocus()
            return

        if self._image_removed:
            self.result_image_filename = ""
            self.result_image_source = ""
            self.result_image_attribution = ""
            self.result_image_license = ""
            self.result_image_source_url = ""
        elif self._raw_image_bytes is not None:
            try:
                preserve_alpha = has_alpha_transparency(self._raw_image_bytes)
                optimized = render_cropped_product_image(
                    self._raw_image_bytes,
                    fit_mode=self._fit_mode,
                    zoom=self._zoom,
                    preserve_alpha=preserve_alpha,
                )
                filename = save_optimized_product_image(optimized)
                self.result_image_filename = filename
                self.result_image_source = self._pending_meta.get("source", "")
                self.result_image_attribution = self._pending_meta.get("attribution", "")
                self.result_image_license = self._pending_meta.get("license", "")
                self.result_image_source_url = self._pending_meta.get("source_url", "")
            except ImageSearchError as exc:
                QMessageBox.warning(self, tr("product_editor.image_save_err_title"), str(exc))
                return

        selected_mod_gids: list[int] = []
        mod_overrides: dict[int, int] = {}
        for grp in self._mod_groups_cache:
            chk = self._mod_group_checks.get(grp.id)
            if chk is None or not chk.isChecked():
                continue
            selected_mod_gids.append(grp.id)
            for opt in grp.options:
                if opt.is_archived:
                    continue
                ov_edit = self._mod_override_edits.get(opt.id)
                if ov_edit is None:
                    continue
                raw_ov = ov_edit.text().strip()
                if raw_ov:
                    try:
                        ov_minor = parse_major_to_minor(raw_ov, self.currency_code)
                    except ValueError as exc:
                        self.right_tabs.setCurrentIndex(1)
                        QMessageBox.warning(self, tr("common.validation_error"), str(exc))
                        ov_edit.setFocus()
                        return
                    if ov_minor < 0:
                        self.right_tabs.setCurrentIndex(1)
                        QMessageBox.warning(
                            self,
                            tr("common.validation_error"),
                            tr("backend.err.modifier_delta_neg"),
                        )
                        ov_edit.setFocus()
                        return
                    mod_overrides[opt.id] = ov_minor

        en_name, ar_name = self._effective_bilingual_names()
        self._cleanup_all_workers()
        self.result_name = name
        self.result_name_en = en_name
        self.result_name_ar = ar_name
        self.result_category = sel.primary_name
        self.result_category_id = sel.category_id
        self.result_pending_cat_en = sel.pending_en if sel.is_pending_new else ""
        self.result_pending_cat_ar = sel.pending_ar if sel.is_pending_new else ""
        self.result_price_minor = price_minor
        self.result_modifier_group_ids = selected_mod_gids
        self.result_option_price_overrides = mod_overrides
        self.result_recipe_entries = (
            self.recipe_editor.entries() if self.recipe_editor is not None else []
        )
        self.accept()

    def _cleanup_all_workers(self) -> None:
        self.category_combo.hidePopup()
        self._suggest_timer.stop()
        for attr in ("_search_worker", "_thumb_worker", "_download_worker"):
            worker = getattr(self, attr, None)
            if worker is not None:
                cancel_fn = getattr(worker, "cancel", None)
                if callable(cancel_fn):
                    cancel_fn()
                if worker.isRunning():
                    worker.wait(900)
                setattr(self, attr, None)
        t_worker = getattr(self, "_translate_worker", None)
        if t_worker is not None:
            t_worker.cancel()
            try:
                t_worker.translated.disconnect(self._on_online_translation_ready)
                t_worker.failed.disconnect(self._on_online_translation_failed)
                t_worker.finished.disconnect(self._on_online_translation_finished)
            except (RuntimeError, TypeError):
                pass
            if t_worker.isRunning():
                _MODULE_RETIRED_THREADS.append(t_worker)
                t_worker.finished.connect(
                    lambda w=t_worker: (
                        _MODULE_RETIRED_THREADS.remove(w)
                        if w in _MODULE_RETIRED_THREADS
                        else None,
                        w.deleteLater(),
                    )
                )
            else:
                t_worker.deleteLater()
            self._translate_worker = None
        for rw in list(self._retired_workers):
            if hasattr(rw, "isRunning") and rw.isRunning():
                rw.wait(600)
        self._retired_workers.clear()

    def reject(self) -> None:
        self._cleanup_all_workers()
        super().reject()

    def closeEvent(self, event) -> None:
        self._cleanup_all_workers()
        super().closeEvent(event)
