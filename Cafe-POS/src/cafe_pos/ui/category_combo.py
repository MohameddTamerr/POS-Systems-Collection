from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..auth import AuthorizationError
from ..bilingual import (
    detect_primary_script,
    normalize_category_key,
    suggest_bilingual_pair,
)
from ..db import CategoryRecord, Database
from ..icons import get_svg_icon
from ..i18n import (
    canonical_category_or_raw,
    is_rtl,
    layout_direction,
    tr,
    tr_category,
    tr_category_record,
    tr_count,
)
from .animations import animate_window_fade_in, attach_button_feedback

_RESERVED_KEYS = {
    "all",
    normalize_category_key("كل القائمة"),
}


@dataclass(frozen=True)
class CategorySelection:
    category_id: int | None
    primary_name: str
    display_name: str
    is_pending_new: bool
    pending_en: str
    pending_ar: str
    archived_conflict: CategoryRecord | None = None


class CategoryRenameDialog(QDialog):
    """Boutique inline dialog to rename a product category in English and Arabic."""

    def __init__(self, category: CategoryRecord, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.category = category
        self._did_animate_in = False
        self.result_name_en: str = category.name_en
        self.result_name_ar: str = category.name_ar
        self.result_primary: str = category.primary_name

        self.setModal(True)
        self.setMinimumWidth(390)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        card = QFrame()
        card.setProperty("class", "BoutiqueCard")
        c_layout = QVBoxLayout(card)
        c_layout.setContentsMargins(14, 12, 14, 14)
        c_layout.setSpacing(8)

        self.hdr_lbl = QLabel("")
        self.hdr_lbl.setProperty("class", "SectionHeader")
        self.desc_lbl = QLabel("")
        self.desc_lbl.setWordWrap(True)
        self.desc_lbl.setProperty("class", "MutedLabel")
        c_layout.addWidget(self.hdr_lbl)
        c_layout.addWidget(self.desc_lbl)

        self.en_lbl = QLabel("")
        self.en_lbl.setStyleSheet("font-weight: 600;")
        self.en_edit = QLineEdit(category.name_en)
        self.en_edit.setMaxLength(40)
        self.en_edit.setLayoutDirection(Qt.LeftToRight)
        c_layout.addWidget(self.en_lbl)
        c_layout.addWidget(self.en_edit)

        self.ar_lbl = QLabel("")
        self.ar_lbl.setStyleSheet("font-weight: 600;")
        self.ar_edit = QLineEdit(category.name_ar)
        self.ar_edit.setMaxLength(40)
        c_layout.addWidget(self.ar_lbl)
        c_layout.addWidget(self.ar_edit)

        root.addWidget(card)

        btn_row = QHBoxLayout()
        self.cancel_btn = QPushButton("")
        self.cancel_btn.clicked.connect(self.reject)
        self.save_btn = QPushButton("")
        self.save_btn.setProperty("role", "primary")
        self.save_btn.setDefault(True)
        self.save_btn.clicked.connect(self._on_save)
        attach_button_feedback(self.cancel_btn)
        attach_button_feedback(self.save_btn)

        btn_row.addStretch(1)
        btn_row.addWidget(self.cancel_btn)
        btn_row.addWidget(self.save_btn)
        root.addLayout(btn_row)

        self.retranslate_ui()

    def retranslate_ui(self) -> None:
        self.setLayoutDirection(layout_direction())
        self.setWindowTitle(tr("cat_combo.rename_title"))
        self.hdr_lbl.setText(tr("cat_combo.rename_title"))
        self.desc_lbl.setText(tr("cat_combo.rename_prompt"))
        self.en_lbl.setText(tr("cat_combo.name_en_label"))
        self.ar_lbl.setText(tr("cat_combo.name_ar_label"))
        self.cancel_btn.setText(tr("common.cancel"))
        self.save_btn.setText(tr("common.save"))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._did_animate_in:
            self._did_animate_in = True
            animate_window_fade_in(self)
        if is_rtl():
            self.ar_edit.setFocus()
        else:
            self.en_edit.setFocus()

    def _on_save(self) -> None:
        en = " ".join(self.en_edit.text().strip().split())[:40]
        ar = " ".join(self.ar_edit.text().strip().split())[:40]
        if not en and not ar:
            QMessageBox.warning(
                self,
                tr("common.validation_error"),
                tr("product_editor.category_required"),
            )
            return
        self.result_name_en = en
        self.result_name_ar = ar
        self.result_primary = en or ar
        self.accept()


class CategoryDeleteChoiceDialog(QDialog):
    """Safe deletion modal for non-empty categories: Move Products & Delete or Archive Instead."""

    def __init__(
        self,
        category: CategoryRecord,
        product_count: int,
        move_targets: list[CategoryRecord],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.category = category
        self.product_count = product_count
        self.move_targets = list(move_targets)
        self._did_animate_in = False
        self.result_action: str = ""  # "move_delete" | "archive"
        self.result_move_to_id: int | None = None

        self.setModal(True)
        self.setMinimumWidth(430)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        warn_card = QFrame()
        warn_card.setProperty("class", "BoutiqueCard")
        w_layout = QVBoxLayout(warn_card)
        w_layout.setContentsMargins(14, 12, 14, 14)
        w_layout.setSpacing(8)

        self.hdr_lbl = QLabel("")
        self.hdr_lbl.setProperty("class", "SectionHeader")
        self.body_lbl = QLabel("")
        self.body_lbl.setWordWrap(True)
        self.body_lbl.setProperty("class", "MutedLabel")
        w_layout.addWidget(self.hdr_lbl)
        w_layout.addWidget(self.body_lbl)

        self.move_lbl = QLabel("")
        self.move_lbl.setStyleSheet("font-weight: 600; margin-top: 4px;")
        self.move_combo = QComboBox()
        for target in self.move_targets:
            self.move_combo.addItem(tr_category_record(target), target.id)
        w_layout.addWidget(self.move_lbl)
        w_layout.addWidget(self.move_combo)

        root.addWidget(warn_card)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.cancel_btn = QPushButton("")
        self.cancel_btn.clicked.connect(self.reject)

        self.archive_btn = QPushButton("")
        self.archive_btn.setProperty("role", "secondary")
        self.archive_btn.clicked.connect(self._choose_archive)

        self.move_delete_btn = QPushButton("")
        self.move_delete_btn.setProperty("role", "danger")
        self.move_delete_btn.setEnabled(bool(self.move_targets))
        self.move_delete_btn.clicked.connect(self._choose_move_delete)

        attach_button_feedback(self.cancel_btn)
        attach_button_feedback(self.archive_btn)
        attach_button_feedback(self.move_delete_btn)

        btn_row.addWidget(self.cancel_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(self.archive_btn)
        btn_row.addWidget(self.move_delete_btn)
        root.addLayout(btn_row)

        self.retranslate_ui()

    def retranslate_ui(self) -> None:
        self.setLayoutDirection(layout_direction())
        self.setWindowTitle(tr("cat_combo.delete_used_title"))
        self.hdr_lbl.setText(tr("cat_combo.delete_used_title"))
        self.body_lbl.setText(
            tr(
                "cat_combo.delete_used_body",
                name=tr_category_record(self.category),
                count=tr_count("product", self.product_count),
            )
        )
        self.move_lbl.setText(tr("cat_combo.move_target_label"))
        for idx, target in enumerate(self.move_targets):
            if idx < self.move_combo.count():
                self.move_combo.setItemText(idx, tr_category_record(target))
        self.cancel_btn.setText(tr("common.cancel"))
        self.archive_btn.setText(tr("cat_combo.archive_instead_btn"))
        self.move_delete_btn.setText(tr("cat_combo.move_delete_btn"))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._did_animate_in:
            self._did_animate_in = True
            animate_window_fade_in(self)

    def _choose_archive(self) -> None:
        self.result_action = "archive"
        self.result_move_to_id = None
        self.accept()

    def _choose_move_delete(self) -> None:
        target_data = self.move_combo.currentData()
        if target_data is None:
            return
        self.result_action = "move_delete"
        self.result_move_to_id = int(target_data)
        self.accept()


class _SmartCategoryRowWidget(QWidget):
    """Boutique dropdown item widget supporting optional inline '⋯' actions menu button."""

    menu_requested = Signal(object, QPoint)  # CategoryRecord, global anchor point

    def __init__(
        self,
        *,
        kind: str,
        primary_text: str,
        secondary_text: str = "",
        category: CategoryRecord | None = None,
        show_actions: bool = False,
        is_archived: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.kind = kind
        self.category = category
        self.setFocusPolicy(Qt.NoFocus)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 5, 8, 5)
        layout.setSpacing(6)

        if kind == "create":
            icon_lbl = QLabel()
            icon_lbl.setFixedSize(16, 16)
            icon_lbl.setAlignment(Qt.AlignCenter)
            icon_lbl.setAttribute(Qt.WA_TransparentForMouseEvents)
            icon_lbl.setPixmap(get_svg_icon("plus", "#005B37", 13).pixmap(13, 13))
            layout.addWidget(icon_lbl)

        title_lbl = QLabel(primary_text)
        title_lbl.setFocusPolicy(Qt.NoFocus)
        title_lbl.setAttribute(Qt.WA_TransparentForMouseEvents)
        if kind == "create":
            title_lbl.setStyleSheet("font-size: 12.5px; font-weight: 700; color: #005B37;")
        elif kind == "readonly_hint":
            title_lbl.setStyleSheet("font-size: 11.5px; color: #7A3E31;")
            title_lbl.setWordWrap(True)
        elif is_archived:
            title_lbl.setStyleSheet("font-size: 12.5px; font-weight: 600; color: #6B786B;")
        else:
            title_lbl.setStyleSheet("font-size: 12.5px; font-weight: 600; color: #202820;")
        layout.addWidget(title_lbl, 1)

        if secondary_text and kind == "category":
            sec_lbl = QLabel(secondary_text)
            sec_lbl.setFocusPolicy(Qt.NoFocus)
            sec_lbl.setAttribute(Qt.WA_TransparentForMouseEvents)
            sec_lbl.setStyleSheet("font-size: 11px; color: #738274;")
            layout.addWidget(sec_lbl)

        if is_archived:
            arch_pill = QLabel(tr("cat_combo.archived_badge"))
            arch_pill.setFocusPolicy(Qt.NoFocus)
            arch_pill.setAttribute(Qt.WA_TransparentForMouseEvents)
            arch_pill.setStyleSheet(
                "background-color: #FDEEE9; color: #7A3E31; font-size: 10px; "
                "font-weight: 700; padding: 1px 6px; border-radius: 6px;"
            )
            layout.addWidget(arch_pill)

        self.more_btn: QToolButton | None = None
        if show_actions and category is not None and kind == "category":
            btn = QToolButton(self)
            btn.setObjectName("CatRowMoreBtn")
            btn.setIcon(get_svg_icon("more_horiz", "#4A5A4B", 15))
            btn.setIconSize(QSize(15, 15))
            btn.setFocusPolicy(Qt.NoFocus)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip(tr("cat_combo.actions_tip"))
            btn.setFixedSize(28, 24)
            btn.setStyleSheet(
                "QToolButton#CatRowMoreBtn { border: 1px solid transparent; border-radius: 6px; "
                "background: transparent; padding: 0; } "
                "QToolButton#CatRowMoreBtn:hover { background-color: #DCE8D7; border-color: #C7D1C2; }"
            )
            btn.clicked.connect(self._on_more_clicked)
            layout.addWidget(btn)
            self.more_btn = btn

    def _on_more_clicked(self) -> None:
        if self.category is None or self.more_btn is None:
            return
        gpos = self.more_btn.mapToGlobal(QPoint(0, self.more_btn.height()))
        self.menu_requested.emit(self.category, gpos)


class SmartCategoryComboBox(QComboBox):
    """Editable searchable category ComboBox with Admin inline create/rename/delete/archive popup."""

    categories_mutated = Signal()
    pending_draft_changed = Signal(bool)

    def __init__(
        self,
        *,
        db: Database | None = None,
        existing_categories: list[str] | None = None,
        is_admin: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.db = db
        self._fallback_categories: list[str] = list(existing_categories or [])
        self._is_admin = bool(is_admin)
        self._categories: list[CategoryRecord] = []
        self._selected_category_id: int | None = None
        self._pending_draft_raw: str = ""
        self._pending_en: str = ""
        self._pending_ar: str = ""
        self._popup_filter_text: str = ""
        self._internal_edit_guard: bool = False
        self._syncing_pending: bool = False
        self._app_filter_installed: bool = False
        self._translation_memo: dict[tuple[str, str, str], tuple[str, bool, str] | None] = {}
        self._diag_events: deque[str] = deque(maxlen=80)

        self.setEditable(True)
        self.setCompleter(None)
        self.setInsertPolicy(QComboBox.NoInsert)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        le = self.lineEdit()
        le.setMaxLength(40)
        le.setPlaceholderText(tr("cat_combo.search_ph"))
        le.textEdited.connect(self._on_line_text_edited)
        le.installEventFilter(self)

        self._build_popup()
        self.reload_categories()

    def _build_popup(self) -> None:
        self._popup = QFrame(
            self,
            Qt.Tool | Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus,
        )
        self._popup.setObjectName("SmartCatPopup")
        self._popup.setFocusPolicy(Qt.NoFocus)
        self._popup.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self._popup.setStyleSheet(
            "QFrame#SmartCatPopup { background-color: #FFFFFF; border: 1px solid #C7D1C2; border-radius: 12px; }"
        )
        p_layout = QVBoxLayout(self._popup)
        p_layout.setContentsMargins(4, 4, 4, 4)
        p_layout.setSpacing(2)

        self._list = QListWidget(self._popup)
        self._list.setFocusPolicy(Qt.NoFocus)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._list.setSelectionMode(QListWidget.SingleSelection)
        self._list.setStyleSheet(
            "QListWidget { background: transparent; border: none; outline: none; } "
            "QListWidget::item { border-radius: 8px; padding: 1px; margin: 1px 0; } "
            "QListWidget::item:hover { background-color: #EEF4EC; } "
            "QListWidget::item:selected { background-color: #DCE8D7; }"
        )
        self._list.itemClicked.connect(self._on_list_item_clicked)
        p_layout.addWidget(self._list)

    def _cache_lookup(self, src: str, s_lang: str, d_lang: str) -> tuple[str, bool, str] | None:
        if self.db is None:
            return None
        key = (" ".join((src or "").strip().split()).casefold(), s_lang, d_lang)
        if key not in self._translation_memo:
            self._translation_memo[key] = self.db.get_cached_translation(src, s_lang, d_lang)
        return self._translation_memo[key]

    def set_admin_mode(self, is_admin: bool) -> None:
        self._is_admin = bool(is_admin)
        if not self._is_admin:
            self._clear_pending_draft()
        if self._popup.isVisible():
            self._populate_popup(self._popup_filter_text)

    def reload_categories(
        self,
        *,
        select_id: int | None = None,
        select_name: str | None = None,
        fallback_list: list[str] | None = None,
    ) -> None:
        self._translation_memo.clear()
        if fallback_list is not None:
            self._fallback_categories = list(fallback_list)

        if self.db is not None:
            self._categories = self.db.list_category_records(include_archived=True)
        else:
            synth: list[CategoryRecord] = []
            seen: set[str] = set()
            for idx, raw in enumerate(self._fallback_categories):
                clean = " ".join((raw or "").strip().split())
                if not clean:
                    continue
                nk = normalize_category_key(clean)
                if nk in seen or nk in _RESERVED_KEYS:
                    continue
                seen.add(nk)
                en, ar, _s = suggest_bilingual_pair(clean, is_category=True)
                synth.append(
                    CategoryRecord(
                        id=-(idx + 1),
                        normalized_key=nk,
                        name_en=en,
                        name_ar=ar,
                        display_order=(idx + 1) * 10,
                    )
                )
            self._categories = synth

        prev_id = select_id if select_id is not None else self._selected_category_id
        prev_text = select_name if select_name is not None else self.lineEdit().text().strip()
        active_cats = [c for c in self._categories if not c.is_archived]

        self._internal_edit_guard = True
        self.blockSignals(True)
        self.clear()
        for cat in active_cats:
            self.addItem(tr_category_record(cat), cat.primary_name)
            row_i = self.count() - 1
            self.setItemData(row_i, cat.id, Qt.UserRole + 1)

        chosen_idx = -1
        if prev_id is not None and prev_id > 0:
            for i, cat in enumerate(active_cats):
                if cat.id == prev_id:
                    chosen_idx = i
                    break
        if chosen_idx < 0 and prev_text:
            cat_match = self._find_category_match(prev_text, active_only=True)
            if cat_match is not None:
                for i, cat in enumerate(active_cats):
                    if cat.primary_name == cat_match.primary_name and cat.id == cat_match.id:
                        chosen_idx = i
                        break

        if chosen_idx >= 0:
            self.setCurrentIndex(chosen_idx)
            chosen_cat = active_cats[chosen_idx]
            self._selected_category_id = chosen_cat.id if chosen_cat.id > 0 else None
            self.lineEdit().setText(tr_category_record(chosen_cat))
        elif self._pending_draft_raw:
            self.setCurrentIndex(-1)
            self.lineEdit().setText(self._pending_draft_raw)
        elif active_cats:
            self.setCurrentIndex(0)
            chosen_cat = active_cats[0]
            self._selected_category_id = chosen_cat.id if chosen_cat.id > 0 else None
            self.lineEdit().setText(tr_category_record(chosen_cat))
        else:
            self.setCurrentIndex(-1)
            self._selected_category_id = None
        self.blockSignals(False)
        self._internal_edit_guard = False

        if self._popup.isVisible():
            self._populate_popup(self._popup_filter_text)

    def _find_category_match(
        self, raw_text: str, *, active_only: bool = False
    ) -> CategoryRecord | None:
        clean = " ".join((raw_text or "").strip().split())
        if not clean:
            return None
        nkey = normalize_category_key(clean)
        canon = canonical_category_or_raw(clean)
        canon_key = normalize_category_key(canon) if canon else ""
        pool = [c for c in self._categories if not c.is_archived] if active_only else self._categories
        for cat in pool:
            keys = {
                cat.normalized_key,
                normalize_category_key(cat.name_en),
                normalize_category_key(cat.name_ar),
                normalize_category_key(tr_category_record(cat)),
            }
            if nkey in keys or (canon_key and canon_key in keys):
                return cat
        return None

    def setCurrentText(self, text: str) -> None:  # type: ignore[override]
        clean = " ".join((text or "").strip().split())
        matched = self._find_category_match(clean, active_only=True)
        if matched is not None:
            self._select_category_record(matched, emit_signal=True)
            return
        self._selected_category_id = None
        self._internal_edit_guard = True
        self.blockSignals(True)
        self.setCurrentIndex(-1)
        self.lineEdit().setText(clean)
        self.blockSignals(False)
        self._internal_edit_guard = False
        self._sync_pending_state_from_text(clean)
        self.currentTextChanged.emit(clean)

    def setEditText(self, text: str) -> None:  # type: ignore[override]
        self.setCurrentText(text)

    def select_category(self, *, category_id: int | None = None, name: str = "") -> None:
        if category_id is not None:
            for cat in self._categories:
                if cat.id == category_id and not cat.is_archived:
                    self._select_category_record(cat, emit_signal=False)
                    return
        if name:
            matched = self._find_category_match(name, active_only=True)
            if matched is not None:
                self._select_category_record(matched, emit_signal=False)
                return
            self._internal_edit_guard = True
            self.blockSignals(True)
            self.setCurrentIndex(-1)
            self._selected_category_id = None
            self.lineEdit().setText(name.strip())
            self.blockSignals(False)
            self._internal_edit_guard = False
            self._sync_pending_state_from_text(name.strip())

    def _clear_pending_draft(self) -> None:
        had = bool(self._pending_draft_raw)
        self._pending_draft_raw = ""
        self._pending_en = ""
        self._pending_ar = ""
        if had:
            self.pending_draft_changed.emit(False)

    def _sync_pending_state_from_text(self, raw_text: str) -> None:
        if self._syncing_pending:
            return
        self._syncing_pending = True
        try:
            clean = " ".join((raw_text or "").strip().split())[:40]
            if not clean or normalize_category_key(clean) in _RESERVED_KEYS:
                self._clear_pending_draft()
                return
            matched_any = self._find_category_match(clean, active_only=False)
            if matched_any is not None:
                self._clear_pending_draft()
                return
            if not self._is_admin:
                self._clear_pending_draft()
                return
            had = bool(self._pending_draft_raw)
            en, ar, _sug = suggest_bilingual_pair(
                clean, is_category=True, cache_lookup=self._cache_lookup
            )
            if detect_primary_script(clean) == "ar":
                ar = ar or clean
            else:
                en = en or clean
            self._pending_draft_raw = clean
            self._pending_en = en
            self._pending_ar = ar
            if not had:
                self.pending_draft_changed.emit(True)
        finally:
            self._syncing_pending = False

    def stage_pending_category(self, raw_name: str) -> None:
        clean = " ".join((raw_name or "").strip().split())[:40]
        if not self._is_admin or not clean or normalize_category_key(clean) in _RESERVED_KEYS:
            return
        self._diag_events.append(f"stage_pending:{clean}")
        en, ar, _sug = suggest_bilingual_pair(
            clean, is_category=True, cache_lookup=self._cache_lookup
        )
        if detect_primary_script(clean) == "ar":
            ar = ar or clean
        else:
            en = en or clean
        self._selected_category_id = None
        self._pending_draft_raw = clean
        self._pending_en = en
        self._pending_ar = ar
        self._internal_edit_guard = True
        self.blockSignals(True)
        self.setCurrentIndex(-1)
        self.lineEdit().setText(clean)
        self.blockSignals(False)
        self._internal_edit_guard = False
        self.pending_draft_changed.emit(True)
        self.currentTextChanged.emit(clean)

    def _select_category_record(self, cat: CategoryRecord, *, emit_signal: bool = True) -> None:
        self._clear_pending_draft()
        self._selected_category_id = cat.id if cat.id > 0 else None
        self._internal_edit_guard = True
        self.blockSignals(True)
        active_idx = self.findData(cat.primary_name)
        if active_idx >= 0:
            self.setCurrentIndex(active_idx)
        else:
            self.setCurrentIndex(-1)
        disp = tr_category_record(cat)
        self.lineEdit().setText(disp)
        self.blockSignals(False)
        self._internal_edit_guard = False
        if emit_signal:
            self.currentIndexChanged.emit(self.currentIndex())
            self.currentTextChanged.emit(disp)

    def resolve_selection(self) -> CategorySelection:
        txt = " ".join(self.lineEdit().text().strip().split())
        if not txt:
            return CategorySelection(None, "", "", False, "", "", None)

        active_match = self._find_category_match(txt, active_only=True)
        if active_match is not None:
            return CategorySelection(
                category_id=active_match.id if active_match.id > 0 else None,
                primary_name=active_match.primary_name,
                display_name=tr_category_record(active_match),
                is_pending_new=False,
                pending_en="",
                pending_ar="",
                archived_conflict=None,
            )

        archived_match = self._find_category_match(txt, active_only=False)
        if archived_match is not None and archived_match.is_archived:
            return CategorySelection(
                category_id=None,
                primary_name=archived_match.primary_name,
                display_name=tr_category_record(archived_match),
                is_pending_new=False,
                pending_en="",
                pending_ar="",
                archived_conflict=archived_match,
            )

        canon = canonical_category_or_raw(txt)
        if canon in {
            "Espresso",
            "Cold Drinks",
            "Tea & Matcha",
            "Bakery",
            "Kitchen",
            "Retail",
            "Other",
        } and self.db is None:
            return CategorySelection(
                category_id=None,
                primary_name=canon,
                display_name=tr_category(canon),
                is_pending_new=False,
                pending_en="",
                pending_ar="",
                archived_conflict=None,
            )

        if self._pending_draft_raw:
            return CategorySelection(
                category_id=None,
                primary_name=self._pending_en or self._pending_ar or self._pending_draft_raw,
                display_name=self._pending_draft_raw,
                is_pending_new=self._is_admin,
                pending_en=self._pending_en,
                pending_ar=self._pending_ar,
                archived_conflict=None,
            )

        en, ar, _sug = suggest_bilingual_pair(
            txt, is_category=True, cache_lookup=self._cache_lookup
        )
        if detect_primary_script(txt) == "ar":
            ar = ar or txt
        else:
            en = en or txt
        return CategorySelection(
            category_id=None,
            primary_name=en or ar or txt,
            display_name=txt,
            is_pending_new=self._is_admin,
            pending_en=en,
            pending_ar=ar,
            archived_conflict=None,
        )

    def current_primary_name(self) -> str:
        sel = self.resolve_selection()
        return sel.primary_name

    def retranslate_ui(self) -> None:
        self.lineEdit().setPlaceholderText(tr("cat_combo.search_ph"))
        if self._selected_category_id is not None:
            self.reload_categories(select_id=self._selected_category_id)
        else:
            cur_txt = self.lineEdit().text().strip()
            self.reload_categories(select_name=cur_txt or None)

    # ---------------- Popup Management & Live Filtering ----------------

    def _install_outside_filter(self) -> None:
        app = QApplication.instance()
        if app is not None and not self._app_filter_installed:
            app.installEventFilter(self)
            self._app_filter_installed = True

    def _remove_outside_filter(self) -> None:
        app = QApplication.instance()
        if app is not None and self._app_filter_installed:
            try:
                app.removeEventFilter(self)
            except RuntimeError:
                pass
            self._app_filter_installed = False

    def hideEvent(self, event) -> None:
        self.hidePopup()
        super().hideEvent(event)

    def showPopup(self) -> None:
        # On click of combo arrow/field, show full category list first unless actively typing
        self._populate_popup(self._popup_filter_text)
        if self._list.count() == 0:
            return
        popup_w = max(self.width(), 300)
        row_h_sum = sum(
            max(34, self._list.sizeHintForRow(i)) for i in range(self._list.count())
        )
        popup_h = min(252, max(44, row_h_sum + 12))
        self._popup.setFixedWidth(popup_w)
        self._popup.setFixedHeight(popup_h)
        if is_rtl():
            top_left = self.mapToGlobal(QPoint(self.width() - popup_w, self.height() + 3))
        else:
            top_left = self.mapToGlobal(QPoint(0, self.height() + 3))
        self._popup.move(top_left)
        self._diag_events.append("popup_show")
        self._popup.show()
        self._install_outside_filter()

    def hidePopup(self) -> None:
        self._popup_filter_text = ""
        self._remove_outside_filter()
        if hasattr(self, "_popup") and self._popup.isVisible():
            self._diag_events.append("popup_hide")
            self._popup.hide()

    def _on_line_text_edited(self, text: str) -> None:
        if self._internal_edit_guard:
            return
        self._selected_category_id = None
        self._popup_filter_text = text
        self._sync_pending_state_from_text(text)
        if not self._popup.isVisible():
            self.showPopup()
        else:
            self._populate_popup(text)
        # Notify listeners after state settles without forcing focus theft
        self.currentTextChanged.emit(text)

    def eventFilter(self, watched, event) -> bool:
        et = event.type()
        if watched is self.lineEdit():
            if et == QEvent.Type.MouseButtonPress and not self._popup.isVisible():
                self._popup_filter_text = ""
                self.showPopup()
                return False
            if et == QEvent.Type.ShortcutOverride and self._popup.isVisible():
                if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Escape):
                    event.accept()
                    return True
            if et == QEvent.Type.KeyPress:
                key = event.key()
                if key == Qt.Key_Down:
                    if not self._popup.isVisible():
                        self._popup_filter_text = ""
                        self.showPopup()
                        return True
                    self._move_list_selection(1)
                    return True
                if key == Qt.Key_Up and self._popup.isVisible():
                    self._move_list_selection(-1)
                    return True
                if key in (Qt.Key_Return, Qt.Key_Enter) and self._popup.isVisible():
                    cur = self._list.currentItem()
                    if cur is not None:
                        self._activate_list_item(cur)
                        return True
                    # Auto-activate pending "create" row or hide popup cleanly without blocking Enter
                    for row_i in range(self._list.count()):
                        cand = self._list.item(row_i)
                        payload = cand.data(Qt.UserRole) if cand else None
                        if payload and payload[0] == "create":
                            self._activate_list_item(cand)
                            return True
                    self.hidePopup()
                    return True
                if key == Qt.Key_Escape and self._popup.isVisible():
                    self.hidePopup()
                    return True
        elif self._app_filter_installed and self._popup.isVisible():
            if et == QEvent.Type.MouseButtonPress:
                gpos = event.globalPosition().toPoint() if hasattr(event, "globalPosition") else None
                if gpos is not None:
                    inside_combo = self.rect().contains(self.mapFromGlobal(gpos))
                    inside_popup = self._popup.rect().contains(self._popup.mapFromGlobal(gpos))
                    if not inside_combo and not inside_popup:
                        self.hidePopup()
        return super().eventFilter(watched, event)

    def _move_list_selection(self, delta: int) -> None:
        cnt = self._list.count()
        if cnt <= 0:
            return
        row = self._list.currentRow()
        start = 0 if row < 0 else (row + delta) % cnt
        for step in range(cnt):
            cand = (start + step * (1 if delta >= 0 else -1)) % cnt
            item = self._list.item(cand)
            if item and (item.flags() & Qt.ItemIsSelectable):
                self._list.setCurrentRow(cand)
                return

    def _populate_popup(self, query_text: str) -> None:
        self._list.clear()
        q_clean = " ".join((query_text or "").strip().split())
        q_norm = normalize_category_key(q_clean)

        active_cats = [c for c in self._categories if not c.is_archived]
        archived_cats = [c for c in self._categories if c.is_archived]

        def _matches(cat: CategoryRecord) -> bool:
            if not q_norm:
                return True
            hay = " ".join(
                [
                    cat.normalized_key,
                    normalize_category_key(cat.name_en),
                    normalize_category_key(cat.name_ar),
                    normalize_category_key(tr_category_record(cat)),
                ]
            )
            return q_norm in hay

        matched_active = [c for c in active_cats if _matches(c)]
        matched_archived = [c for c in archived_cats if _matches(c)] if self._is_admin else []

        exact_active = self._find_category_match(q_clean, active_only=True) if q_clean else None
        exact_archived = self._find_category_match(q_clean, active_only=False) if q_clean else None
        is_exact_archived = bool(exact_archived is not None and exact_archived.is_archived)

        for cat in matched_active:
            disp = tr_category_record(cat)
            other = cat.name_en if is_rtl() else cat.name_ar
            sec = other if other and other != disp else ""
            item = QListWidgetItem(self._list)
            item.setData(Qt.UserRole, ("category", cat.id))
            item.setSizeHint(QSize(260, 34))
            row_w = _SmartCategoryRowWidget(
                kind="category",
                primary_text=disp,
                secondary_text=sec,
                category=cat,
                show_actions=self._is_admin and self.db is not None and cat.id > 0,
                is_archived=False,
            )
            row_w.menu_requested.connect(self._open_row_context_menu)
            self._list.addItem(item)
            self._list.setItemWidget(item, row_w)
            if self._selected_category_id == cat.id:
                self._list.setCurrentItem(item)

        for cat in matched_archived:
            disp = tr_category_record(cat)
            item = QListWidgetItem(self._list)
            item.setData(Qt.UserRole, ("archived", cat.id))
            item.setSizeHint(QSize(260, 34))
            row_w = _SmartCategoryRowWidget(
                kind="category",
                primary_text=disp,
                category=cat,
                show_actions=self._is_admin and self.db is not None and cat.id > 0,
                is_archived=True,
            )
            row_w.menu_requested.connect(self._open_row_context_menu)
            self._list.addItem(item)
            self._list.setItemWidget(item, row_w)

        if q_clean and exact_active is None and not is_exact_archived and q_norm not in _RESERVED_KEYS:
            if self._is_admin:
                create_item = QListWidgetItem(self._list)
                create_item.setData(Qt.UserRole, ("create", q_clean))
                create_item.setSizeHint(QSize(260, 36))
                row_w = _SmartCategoryRowWidget(
                    kind="create",
                    primary_text=tr("cat_combo.create_action", name=q_clean),
                )
                self._list.addItem(create_item)
                self._list.setItemWidget(create_item, row_w)
            else:
                hint_item = QListWidgetItem(self._list)
                hint_item.setFlags(Qt.ItemIsEnabled)
                hint_item.setSizeHint(QSize(260, 34))
                row_w = _SmartCategoryRowWidget(
                    kind="readonly_hint",
                    primary_text=tr("cat_combo.create_admin_only"),
                )
                self._list.addItem(hint_item)
                self._list.setItemWidget(hint_item, row_w)

        popup_h = min(
            252,
            max(
                44,
                sum(max(34, self._list.sizeHintForRow(i)) for i in range(self._list.count())) + 12,
            ),
        )
        self._popup.setFixedHeight(popup_h)

    def _on_list_item_clicked(self, item: QListWidgetItem) -> None:
        self._activate_list_item(item)

    def _activate_list_item(self, item: QListWidgetItem) -> None:
        payload = item.data(Qt.UserRole)
        if not payload:
            return
        kind, val = payload
        if kind == "create":
            self.stage_pending_category(str(val))
            self.hidePopup()
            return
        if kind == "category":
            cat_id = int(val)
            cat = next((c for c in self._categories if c.id == cat_id), None)
            if cat is not None:
                self._select_category_record(cat, emit_signal=True)
            self.hidePopup()
            return
        if kind == "archived":
            cat_id = int(val)
            cat = next((c for c in self._categories if c.id == cat_id), None)
            self.hidePopup()
            if cat is not None and self._is_admin:
                self._restore_category(cat)

    # ---------------- Admin Inline Row Context Actions ----------------

    def _open_row_context_menu(self, category: CategoryRecord, global_pos: QPoint) -> None:
        if not self._is_admin or self.db is None or category.id <= 0:
            return
        # Close Qt.Popup cleanly BEFORE opening QMenu/modal dialogs to avoid Windows grab conflicts.
        self._popup.hide()

        menu = QMenu(self)
        menu.setLayoutDirection(layout_direction())
        rename_act = menu.addAction(
            get_svg_icon("edit", "#202820", 13), tr("cat_combo.menu.rename")
        )
        if category.is_archived:
            restore_act = menu.addAction(
                get_svg_icon("refresh", "#005B37", 13), tr("cat_combo.menu.restore")
            )
            archive_act = None
        else:
            restore_act = None
            archive_act = menu.addAction(
                get_svg_icon("archive", "#5E6B5F", 13), tr("cat_combo.menu.archive")
            )
        delete_act = menu.addAction(
            get_svg_icon("trash", "#B23A3A", 13), tr("cat_combo.menu.delete")
        )

        chosen = menu.exec(global_pos)
        if chosen is None:
            return
        if chosen is rename_act:
            self._rename_category(category)
        elif restore_act is not None and chosen is restore_act:
            self._restore_category(category)
        elif archive_act is not None and chosen is archive_act:
            self._archive_category(category)
        elif chosen is delete_act:
            self._delete_category(category)

    def _rename_category(self, cat: CategoryRecord) -> None:
        if self.db is None:
            return
        dlg = CategoryRenameDialog(cat, parent=self.window())
        if dlg.exec() != QDialog.Accepted:
            return
        try:
            updated = self.db.rename_category(
                cat.id,
                dlg.result_primary,
                name_en=dlg.result_name_en,
                name_ar=dlg.result_name_ar,
            )
        except (ValueError, AuthorizationError) as exc:
            QMessageBox.warning(self.window(), tr("common.validation_error"), str(exc))
            return

        keep_id = (
            updated.id
            if (self._selected_category_id == cat.id or not self._selected_category_id)
            else self._selected_category_id
        )
        self.reload_categories(select_id=keep_id)
        self.categories_mutated.emit()

    def _archive_category(self, cat: CategoryRecord) -> None:
        if self.db is None:
            return
        try:
            self.db.archive_category(cat.id)
        except (ValueError, AuthorizationError) as exc:
            QMessageBox.warning(self.window(), tr("common.validation_error"), str(exc))
            return

        next_id = None if self._selected_category_id == cat.id else self._selected_category_id
        self.reload_categories(select_id=next_id)
        self.categories_mutated.emit()

    def _restore_category(self, cat: CategoryRecord) -> None:
        if self.db is None:
            return
        try:
            restored = self.db.restore_category(cat.id)
        except (ValueError, AuthorizationError) as exc:
            QMessageBox.warning(self.window(), tr("common.validation_error"), str(exc))
            return
        self.reload_categories(select_id=restored.id)
        self.categories_mutated.emit()

    def _delete_category(self, cat: CategoryRecord) -> None:
        if self.db is None:
            return
        counts = self.db.get_category_product_counts()
        prod_count = counts.get(cat.id, 0)

        if prod_count == 0:
            ans = QMessageBox.question(
                self.window(),
                tr("cat_combo.delete_empty_title"),
                tr("cat_combo.delete_empty_body", name=tr_category_record(cat)),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if ans != QMessageBox.Yes:
                return
            try:
                self.db.delete_category(cat.id)
            except (ValueError, AuthorizationError) as exc:
                QMessageBox.warning(self.window(), tr("common.validation_error"), str(exc))
                return
            next_id = None if self._selected_category_id == cat.id else self._selected_category_id
            self.reload_categories(select_id=next_id)
            self.categories_mutated.emit()
            return

        targets = [c for c in self._categories if not c.is_archived and c.id != cat.id]
        dlg = CategoryDeleteChoiceDialog(cat, prod_count, targets, parent=self.window())
        if dlg.exec() != QDialog.Accepted:
            return

        try:
            if dlg.result_action == "archive":
                self.db.delete_category(cat.id, archive_if_nonempty=True)
                next_id = None if self._selected_category_id == cat.id else self._selected_category_id
            elif dlg.result_action == "move_delete" and dlg.result_move_to_id is not None:
                self.db.delete_category(cat.id, move_to_category_id=dlg.result_move_to_id)
                next_id = (
                    dlg.result_move_to_id
                    if self._selected_category_id == cat.id
                    else self._selected_category_id
                )
            else:
                return
        except (ValueError, AuthorizationError) as exc:
            QMessageBox.warning(self.window(), tr("common.validation_error"), str(exc))
            return

        self.reload_categories(select_id=next_id)
        self.categories_mutated.emit()
