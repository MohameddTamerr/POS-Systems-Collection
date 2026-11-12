from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
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
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .._db_inventory import (
    INGREDIENT_UNITS,
    MOVEMENT_TYPES,
    IngredientRecord,
    RecipeEntryRecord,
    format_qty_milli,
    parse_qty_to_milli,
)
from ..auth import AuthorizationError
from ..config import BusinessConfig
from ..db import Database
from ..i18n import layout_direction, tr, tr_count
from ..icons import get_svg_icon
from ..money import format_major_input, format_minor, parse_major_to_minor
from ..receipts import format_local_timestamp
from ..theme import contrast_text_hex
from .animations import animate_window_fade_in, attach_button_feedback
from .widgets import SearchLineEdit


def _esc_amp(text: str) -> str:
    return (text or "").replace("&", "&&")


@dataclass
class _DraftRecipeRow:
    ingredient_id: int
    name: str
    unit: str
    qty_milli: int
    stock_qty_milli: int
    cost_per_unit_minor: int


class RecipeEditorWidget(QWidget):
    """Reusable recipe ingredient builder with live estimated COGS per unit."""

    changed = Signal()

    def __init__(
        self,
        db: Database,
        config: BusinessConfig,
        *,
        editable: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.db = db
        self.config = config
        self._editable = bool(editable)
        self._ingredients: list[IngredientRecord] = []
        self._rows: list[_DraftRecipeRow] = []

        self._build_ui()
        self.reload_ingredients()
        self.retranslate_ui()

    def _build_ui(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        add_row = QHBoxLayout()
        add_row.setSpacing(6)
        self.ing_combo = QComboBox()
        self.ing_combo.setMinimumHeight(32)
        self.ing_combo.currentIndexChanged.connect(self._sync_unit_hint)

        self.qty_edit = QLineEdit()
        self.qty_edit.setMinimumHeight(32)
        self.qty_edit.setMaximumWidth(100)

        self.unit_lbl = QLabel("g")
        self.unit_lbl.setProperty("class", "CountBadge")
        self.unit_lbl.setMinimumWidth(38)
        self.unit_lbl.setAlignment(Qt.AlignCenter)

        self.add_btn = QPushButton()
        self.add_btn.setProperty("role", "secondary")
        self.add_btn.setMinimumHeight(32)
        self.add_btn.clicked.connect(self._on_add_entry)
        attach_button_feedback(self.add_btn)

        add_row.addWidget(self.ing_combo, 1)
        add_row.addWidget(self.qty_edit)
        add_row.addWidget(self.unit_lbl)
        add_row.addWidget(self.add_btn)
        lay.addLayout(add_row)

        self.table = QTableWidget(0, 5)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setMinimumHeight(140)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        lay.addWidget(self.table, 1)

        foot = QHBoxLayout()
        self.cost_lbl = QLabel()
        self.cost_lbl.setStyleSheet("font-weight: 700; color: #005B37;")
        foot.addStretch(1)
        foot.addWidget(self.cost_lbl)
        lay.addLayout(foot)

        self.ing_combo.setEnabled(self._editable)
        self.qty_edit.setEnabled(self._editable)
        self.add_btn.setEnabled(self._editable)

    def reload_ingredients(self) -> None:
        self._ingredients = self.db.list_ingredients(include_archived=False, only_active=True)
        prev_id = self.ing_combo.currentData()
        self.ing_combo.blockSignals(True)
        self.ing_combo.clear()
        for ing in self._ingredients:
            self.ing_combo.addItem(f"{ing.display_name()} ({ing.unit})", ing.id)
        idx = self.ing_combo.findData(prev_id)
        if idx >= 0:
            self.ing_combo.setCurrentIndex(idx)
        self.ing_combo.blockSignals(False)
        self._sync_unit_hint()

    def _sync_unit_hint(self) -> None:
        iid = self.ing_combo.currentData()
        ing = next((x for x in self._ingredients if x.id == iid), None)
        self.unit_lbl.setText(ing.unit if ing else "-")

    def retranslate_ui(self) -> None:
        self.qty_edit.setPlaceholderText(tr("inventory.recipe.qty_ph"))
        self.add_btn.setText(_esc_amp(tr("inventory.recipe.add_btn")))
        self.table.setHorizontalHeaderLabels(
            [
                tr("inventory.col.ingredient"),
                tr("inventory.col.qty"),
                tr("inventory.col.stock"),
                tr("inventory.col.est_cost"),
                tr("common.actions"),
            ]
        )
        self._render_table()

    def load_entries(self, entries: list[RecipeEntryRecord]) -> None:
        self._rows = [
            _DraftRecipeRow(
                ingredient_id=e.ingredient_id,
                name=e.display_ingredient_name(),
                unit=e.unit,
                qty_milli=e.qty_milli,
                stock_qty_milli=e.stock_qty_milli,
                cost_per_unit_minor=e.cost_per_unit_minor,
            )
            for e in entries
        ]
        self._render_table()

    def load_entry_pairs(self, pairs: list[tuple[int, int]]) -> None:
        ing_map = {ing.id: ing for ing in self._ingredients}
        rows: list[_DraftRecipeRow] = []
        for ing_id, qty_milli in pairs:
            iid = int(ing_id)
            ing = ing_map.get(iid) or self.db.get_ingredient(iid)
            if ing is None:
                continue
            rows.append(
                _DraftRecipeRow(
                    ingredient_id=ing.id,
                    name=ing.display_name(),
                    unit=ing.unit,
                    qty_milli=int(qty_milli),
                    stock_qty_milli=ing.stock_qty_milli,
                    cost_per_unit_minor=ing.cost_per_unit_minor,
                )
            )
        self._rows = rows
        self._render_table()

    def entries(self) -> list[tuple[int, int]]:
        return [(r.ingredient_id, r.qty_milli) for r in self._rows]

    def _on_add_entry(self) -> None:
        iid = self.ing_combo.currentData()
        if iid is None:
            return
        ing = next((x for x in self._ingredients if x.id == iid), None)
        if ing is None:
            return
        try:
            qty_milli = parse_qty_to_milli(self.qty_edit.text())
        except ValueError as exc:
            QMessageBox.warning(self, tr("common.validation_error"), str(exc))
            return
        if qty_milli <= 0:
            QMessageBox.warning(
                self, tr("common.validation_error"), tr("inventory.err.qty_positive")
            )
            return

        for existing in self._rows:
            if existing.ingredient_id == ing.id:
                existing.qty_milli += qty_milli
                break
        else:
            self._rows.append(
                _DraftRecipeRow(
                    ingredient_id=ing.id,
                    name=ing.display_name(),
                    unit=ing.unit,
                    qty_milli=qty_milli,
                    stock_qty_milli=ing.stock_qty_milli,
                    cost_per_unit_minor=ing.cost_per_unit_minor,
                )
            )
        self.qty_edit.clear()
        self._render_table()
        self.changed.emit()

    def _remove_row(self, idx: int) -> None:
        if 0 <= idx < len(self._rows):
            self._rows.pop(idx)
            self._render_table()
            self.changed.emit()

    def _render_table(self) -> None:
        self.table.setRowCount(len(self._rows))
        total_cost = 0
        for r_idx, row in enumerate(self._rows):
            line_cost = int(round((row.qty_milli * row.cost_per_unit_minor) / 1000.0))
            total_cost += line_cost
            qty_item = QTableWidgetItem(f"{format_qty_milli(row.qty_milli)} {row.unit}")
            stk_item = QTableWidgetItem(f"{format_qty_milli(row.stock_qty_milli)} {row.unit}")
            cost_item = QTableWidgetItem(
                format_minor(line_cost, self.config.currency)
                if row.cost_per_unit_minor > 0
                else "-"
            )
            cost_item.setTextAlignment(Qt.AlignTrailing | Qt.AlignVCenter)
            rm_btn = QPushButton(tr("common.remove"))
            rm_btn.setProperty("role", "danger")
            rm_btn.setEnabled(self._editable)
            rm_btn.clicked.connect(lambda _c=False, i=r_idx: self._remove_row(i))

            self.table.setItem(r_idx, 0, QTableWidgetItem(row.name))
            self.table.setItem(r_idx, 1, qty_item)
            self.table.setItem(r_idx, 2, stk_item)
            self.table.setItem(r_idx, 3, cost_item)
            self.table.setCellWidget(r_idx, 4, rm_btn)
            self.table.setRowHeight(r_idx, 34)

        self.cost_lbl.setText(
            tr(
                "inventory.recipe.total_cost",
                cost=format_minor(total_cost, self.config.currency),
            )
        )


class OptionRecipeDialog(QDialog):
    """Modal editor for a modifier option's ingredient recipe (e.g., Oat Milk / Extra Shot)."""

    def __init__(
        self,
        db: Database,
        config: BusinessConfig,
        option_id: int | None,
        option_name: str,
        parent: QWidget | None = None,
        *,
        initial_entries: list[tuple[int, int]] | None = None,
        defer_save: bool = False,
    ) -> None:
        super().__init__(parent)
        self.db = db
        self.config = config
        self.option_id = int(option_id) if option_id is not None else None
        self.defer_save = bool(defer_save or self.option_id is None)
        self.result_entries: list[tuple[int, int]] = []
        self._faded_once = False

        self.setModal(True)
        self.setLayoutDirection(layout_direction())
        self.setWindowTitle(tr("inventory.opt_recipe.title", name=option_name))
        self.resize(600, 380)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        hdr = QLabel(tr("inventory.opt_recipe.heading", name=option_name))
        hdr.setProperty("class", "SectionHeader")
        root.addWidget(hdr)

        self.editor = RecipeEditorWidget(self.db, self.config, parent=self)
        if self.defer_save or self.option_id is None:
            raw_init = list(initial_entries or [])
            if raw_init and isinstance(raw_init[0], tuple):
                self.editor.load_entry_pairs([(int(x[0]), int(x[1])) for x in raw_init])
            else:
                self.editor.load_entries(raw_init)  # type: ignore[arg-type]
        else:
            self.editor.load_entries(self.db.get_modifier_option_recipe(self.option_id))
        root.addWidget(self.editor, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        cancel_btn = QPushButton(tr("common.cancel"))
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton(tr("common.save"))
        save_btn.setProperty("role", "primary")
        save_btn.clicked.connect(self._on_save)
        row.addWidget(cancel_btn)
        row.addWidget(save_btn)
        root.addLayout(row)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if not self._faded_once:
            self._faded_once = True
            animate_window_fade_in(self, duration_ms=145)

    def _on_save(self) -> None:
        entries = self.editor.entries()
        if not self.defer_save and self.option_id is not None:
            try:
                self.db.set_modifier_option_recipe(self.option_id, entries)
            except Exception as exc:
                QMessageBox.warning(self, tr("common.validation_error"), str(exc))
                return
        self.result_entries = list(entries)
        self.accept()


class IngredientEditorDialog(QDialog):
    """Create or edit an ingredient definition."""

    def __init__(
        self,
        db: Database,
        config: BusinessConfig,
        ingredient: IngredientRecord | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.db = db
        self.config = config
        self.ingredient = ingredient
        self.saved_ingredient: IngredientRecord | None = None

        self.setModal(True)
        self.setLayoutDirection(layout_direction())
        self.setWindowTitle(
            tr("inventory.ing_editor.edit_title")
            if ingredient
            else tr("inventory.ing_editor.add_title")
        )
        self.setMinimumWidth(440)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        form = QFormLayout()
        form.setSpacing(8)
        self.name_en_edit = QLineEdit(ingredient.name_en if ingredient else "")
        self.name_ar_edit = QLineEdit(ingredient.name_ar if ingredient else "")
        self.name_ar_edit.setLayoutDirection(Qt.RightToLeft)

        self.unit_combo = QComboBox()
        for u in INGREDIENT_UNITS:
            self.unit_combo.addItem(tr(f"inventory.unit.{u}"), u)
        if ingredient:
            uidx = self.unit_combo.findData(ingredient.unit)
            if uidx >= 0:
                self.unit_combo.setCurrentIndex(uidx)

        self.initial_stock_edit = QLineEdit(
            format_qty_milli(ingredient.stock_qty_milli) if ingredient else "0"
        )
        self.initial_stock_edit.setEnabled(ingredient is None)

        self.min_threshold_edit = QLineEdit(
            format_qty_milli(ingredient.min_threshold_milli)
            if ingredient
            else format_qty_milli(int(getattr(config, "default_low_stock_threshold_milli", 0) or 0))
        )
        self.cost_edit = QLineEdit(
            format_major_input(ingredient.cost_per_unit_minor, config.currency)
            if ingredient
            else "0"
        )
        self.track_chk = QCheckBox(tr("inventory.ing_editor.track_chk"))
        self.track_chk.setChecked(ingredient.track_stock if ingredient else True)
        self.active_chk = QCheckBox(tr("inventory.ing_editor.active_chk"))
        self.active_chk.setChecked(ingredient.is_active if ingredient else True)

        form.addRow(tr("inventory.ing_editor.name_en"), self.name_en_edit)
        form.addRow(tr("inventory.ing_editor.name_ar"), self.name_ar_edit)
        form.addRow(tr("inventory.ing_editor.unit"), self.unit_combo)
        if ingredient is None:
            form.addRow(tr("inventory.ing_editor.opening_stock"), self.initial_stock_edit)
        form.addRow(tr("inventory.ing_editor.min_threshold"), self.min_threshold_edit)
        form.addRow(tr("inventory.ing_editor.unit_cost", currency=config.currency), self.cost_edit)
        form.addRow("", self.track_chk)
        if ingredient is not None:
            form.addRow("", self.active_chk)
        root.addLayout(form)

        row = QHBoxLayout()
        row.addStretch(1)
        cancel_btn = QPushButton(tr("common.cancel"))
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton(tr("common.save"))
        save_btn.setProperty("role", "primary")
        save_btn.clicked.connect(self._on_save)
        row.addWidget(cancel_btn)
        row.addWidget(save_btn)
        root.addLayout(row)

    def _on_save(self) -> None:
        try:
            min_milli = parse_qty_to_milli(self.min_threshold_edit.text() or "0")
            cost_minor = parse_major_to_minor(self.cost_edit.text() or "0", self.config.currency)
            if cost_minor < 0:
                raise ValueError(tr("inventory.err.cost_nonneg"))
            unit = str(self.unit_combo.currentData() or "g")
            if self.ingredient is None:
                stock_milli = parse_qty_to_milli(
                    self.initial_stock_edit.text() or "0", allow_negative=False
                )
                self.saved_ingredient = self.db.create_ingredient(
                    self.name_en_edit.text(),
                    self.name_ar_edit.text(),
                    unit=unit,
                    stock_qty_milli=stock_milli,
                    min_threshold_milli=min_milli,
                    cost_per_unit_minor=cost_minor,
                    track_stock=self.track_chk.isChecked(),
                )
            else:
                self.saved_ingredient = self.db.update_ingredient(
                    self.ingredient.id,
                    name_en=self.name_en_edit.text(),
                    name_ar=self.name_ar_edit.text(),
                    unit=unit,
                    min_threshold_milli=min_milli,
                    cost_per_unit_minor=cost_minor,
                    track_stock=self.track_chk.isChecked(),
                    is_active=self.active_chk.isChecked(),
                )
        except Exception as exc:
            QMessageBox.warning(self, tr("common.validation_error"), str(exc))
            return
        self.accept()


class StockAdjustDialog(QDialog):
    """Restock, manual adjustment (+/-), or record waste for an ingredient."""

    def __init__(
        self,
        db: Database,
        config: BusinessConfig,
        ingredient: IngredientRecord,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.db = db
        self.config = config
        self.ingredient = ingredient

        self.setModal(True)
        self.setLayoutDirection(layout_direction())
        self.setWindowTitle(tr("inventory.adjust.title", name=ingredient.display_name()))
        self.setMinimumWidth(420)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        cur_lbl = QLabel(
            tr(
                "inventory.adjust.current",
                qty=format_qty_milli(ingredient.stock_qty_milli),
                unit=ingredient.unit,
            )
        )
        cur_lbl.setProperty("class", "PrimaryBadge")
        root.addWidget(cur_lbl)

        form = QFormLayout()
        form.setSpacing(8)
        self.type_combo = QComboBox()
        self.type_combo.addItem(tr("inventory.mv.RESTOCK"), "RESTOCK")
        self.type_combo.addItem(tr("inventory.mv.ADJUSTMENT"), "ADJUSTMENT")
        self.type_combo.addItem(tr("inventory.mv.WASTE"), "WASTE")
        self.type_combo.currentIndexChanged.connect(self._sync_cost_visibility)

        self.qty_edit = QLineEdit()
        self.qty_edit.setPlaceholderText(f"0.0 ({ingredient.unit})")

        self.cost_lbl = QLabel(tr("inventory.adjust.new_cost", currency=config.currency))
        self.cost_edit = QLineEdit(
            format_major_input(ingredient.cost_per_unit_minor, config.currency)
            if ingredient.cost_per_unit_minor > 0
            else ""
        )
        self.notes_edit = QLineEdit()
        self.notes_edit.setMaxLength(180)

        form.addRow(tr("inventory.adjust.type_label"), self.type_combo)
        form.addRow(tr("inventory.adjust.qty_label", unit=ingredient.unit), self.qty_edit)
        form.addRow(self.cost_lbl, self.cost_edit)
        form.addRow(tr("inventory.adjust.notes_label"), self.notes_edit)
        root.addLayout(form)

        row = QHBoxLayout()
        row.addStretch(1)
        cancel_btn = QPushButton(tr("common.cancel"))
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton(tr("common.save"))
        save_btn.setProperty("role", "primary")
        save_btn.clicked.connect(self._on_save)
        row.addWidget(cancel_btn)
        row.addWidget(save_btn)
        root.addLayout(row)

    def _sync_cost_visibility(self) -> None:
        is_restock = self.type_combo.currentData() == "RESTOCK"
        self.cost_lbl.setVisible(is_restock)
        self.cost_edit.setVisible(is_restock)

    def _on_save(self) -> None:
        mtype = str(self.type_combo.currentData() or "RESTOCK")
        try:
            qty_milli = parse_qty_to_milli(
                self.qty_edit.text(), allow_negative=(mtype == "ADJUSTMENT")
            )
            unit_cost = None
            if mtype == "RESTOCK" and self.cost_edit.text().strip():
                unit_cost = parse_major_to_minor(self.cost_edit.text(), self.config.currency)
            self.db.adjust_ingredient_stock(
                self.ingredient.id,
                mtype,
                qty_milli,
                notes=self.notes_edit.text(),
                unit_cost_minor=unit_cost,
            )
        except Exception as exc:
            QMessageBox.warning(self, tr("common.validation_error"), str(exc))
            return
        self.accept()


class InventoryView(QWidget):
    """Ingredients stock list, low-stock filter, restock/waste actions, and movements log."""

    inventory_changed = Signal()
    status_message = Signal(str)

    def __init__(self, db: Database, config: BusinessConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.db = db
        self.config = config
        self._ingredients: list[IngredientRecord] = []

        self._build_ui()
        self.retranslate_ui()
        self.refresh()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)

        splitter = QSplitter(Qt.Vertical)
        splitter.setChildrenCollapsible(False)

        # Top: Ingredients Catalog Card
        top_card = QFrame()
        top_card.setProperty("class", "BoutiqueCard")
        top_lay = QVBoxLayout(top_card)
        top_lay.setContentsMargins(16, 14, 16, 14)
        top_lay.setSpacing(10)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        self.title_lbl = QLabel()
        self.title_lbl.setProperty("class", "PageHeading")
        self.low_badge = QLabel()
        self.low_badge.setProperty("class", "PeachBadge")
        self.low_badge.hide()

        self.search_input = SearchLineEdit("")
        self.search_input.setMinimumWidth(160)
        self.search_input.setMaximumWidth(240)
        self.search_input.textChanged.connect(self.refresh)

        self.low_only_chk = QCheckBox()
        self.low_only_chk.toggled.connect(self.refresh)
        self.show_archived_chk = QCheckBox()
        self.show_archived_chk.toggled.connect(self.refresh)

        self.add_ing_btn = QPushButton()
        self.add_ing_btn.setProperty("role", "primary")
        self.add_ing_btn.setIcon(
            get_svg_icon("plus", contrast_text_hex(self.config.accent_color), 14)
        )
        self.add_ing_btn.clicked.connect(self._open_add_dialog)
        attach_button_feedback(self.add_ing_btn)

        toolbar.addWidget(self.title_lbl)
        toolbar.addWidget(self.low_badge)
        toolbar.addStretch(1)
        toolbar.addWidget(self.search_input)
        toolbar.addWidget(self.low_only_chk)
        toolbar.addWidget(self.show_archived_chk)
        toolbar.addWidget(self.add_ing_btn)
        top_lay.addLayout(toolbar)

        self.ing_table = QTableWidget(0, 7)
        self.ing_table.verticalHeader().setVisible(False)
        self.ing_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.ing_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.ing_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.ing_table.setAlternatingRowColors(True)
        ih = self.ing_table.horizontalHeader()
        ih.setSectionResizeMode(0, QHeaderView.Stretch)
        ih.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        ih.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        ih.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        ih.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        ih.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        ih.setSectionResizeMode(6, QHeaderView.Fixed)
        self.ing_table.setColumnWidth(6, 238)
        top_lay.addWidget(self.ing_table, 1)

        # Bottom: Movements Log Card
        mv_card = QFrame()
        mv_card.setProperty("class", "BoutiqueCard")
        mv_lay = QVBoxLayout(mv_card)
        mv_lay.setContentsMargins(16, 12, 16, 12)
        mv_lay.setSpacing(8)

        mv_hdr = QHBoxLayout()
        mv_hdr.setSpacing(8)
        self.mv_title_lbl = QLabel()
        self.mv_title_lbl.setProperty("class", "SectionHeader")
        self.mv_type_filter = QComboBox()
        self.mv_type_filter.currentIndexChanged.connect(self.refresh_movements)
        mv_hdr.addWidget(self.mv_title_lbl)
        mv_hdr.addStretch(1)
        mv_hdr.addWidget(self.mv_type_filter)
        mv_lay.addLayout(mv_hdr)

        self.mv_table = QTableWidget(0, 7)
        self.mv_table.verticalHeader().setVisible(False)
        self.mv_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.mv_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.mv_table.setAlternatingRowColors(True)
        mh = self.mv_table.horizontalHeader()
        mh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        mh.setSectionResizeMode(1, QHeaderView.Stretch)
        mh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        mh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        mh.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        mh.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        mh.setSectionResizeMode(6, QHeaderView.Stretch)
        mv_lay.addWidget(self.mv_table, 1)

        splitter.addWidget(top_card)
        splitter.addWidget(mv_card)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        outer.addWidget(splitter, 1)

    def set_config(self, config: BusinessConfig) -> None:
        self.config = config
        self.add_ing_btn.setIcon(
            get_svg_icon("plus", contrast_text_hex(config.accent_color), 14)
        )
        self.refresh()

    def set_low_stock_filter(self, enabled: bool) -> None:
        self.low_only_chk.setChecked(bool(enabled))
        self.refresh()

    def focus_search(self) -> None:
        self.search_input.setFocus()
        self.search_input.selectAll()

    def retranslate_ui(self) -> None:
        self.title_lbl.setText(tr("inventory.heading"))
        self.search_input.setPlaceholderText(tr("inventory.search_ph"))
        self.low_only_chk.setText(_esc_amp(tr("inventory.filter.low_only")))
        self.show_archived_chk.setText(_esc_amp(tr("inventory.filter.show_archived")))
        self.add_ing_btn.setText(_esc_amp(tr("inventory.add_btn")))
        self.ing_table.setHorizontalHeaderLabels(
            [
                tr("inventory.col.ingredient"),
                tr("inventory.col.unit"),
                tr("inventory.col.stock"),
                tr("inventory.col.min_threshold"),
                tr("inventory.col.unit_cost"),
                tr("inventory.col.status"),
                tr("common.actions"),
            ]
        )
        self.mv_title_lbl.setText(tr("inventory.movements.heading"))
        prev_mv = self.mv_type_filter.currentData() or ""
        self.mv_type_filter.blockSignals(True)
        self.mv_type_filter.clear()
        self.mv_type_filter.addItem(tr("inventory.mv.all"), "")
        for mt in MOVEMENT_TYPES:
            self.mv_type_filter.addItem(tr(f"inventory.mv.{mt}"), mt)
        m_idx = self.mv_type_filter.findData(prev_mv)
        self.mv_type_filter.setCurrentIndex(m_idx if m_idx >= 0 else 0)
        self.mv_type_filter.blockSignals(False)

        self.mv_table.setHorizontalHeaderLabels(
            [
                tr("inventory.mv.col.time"),
                tr("inventory.col.ingredient"),
                tr("inventory.mv.col.type"),
                tr("inventory.mv.col.delta"),
                tr("inventory.mv.col.balance"),
                tr("inventory.mv.col.reference"),
                tr("inventory.mv.col.notes"),
            ]
        )

    def refresh(self) -> None:
        try:
            all_items = self.db.list_ingredients(
                include_archived=self.show_archived_chk.isChecked(),
                search=self.search_input.text(),
            )
            low_items = self.db.get_low_stock_ingredients()
        except Exception:
            all_items = []
            low_items = []

        if low_items:
            self.low_badge.setText(tr("register.low_stock_badge", count=len(low_items)))
            self.low_badge.show()
        else:
            self.low_badge.hide()

        if self.low_only_chk.isChecked():
            self._ingredients = [i for i in all_items if i.is_low_stock]
        else:
            self._ingredients = all_items

        self.ing_table.setRowCount(len(self._ingredients))
        for r_idx, ing in enumerate(self._ingredients):
            name_item = QTableWidgetItem(ing.display_name())
            f = name_item.font()
            f.setBold(True)
            name_item.setFont(f)

            unit_item = QTableWidgetItem(ing.unit)
            stock_item = QTableWidgetItem(f"{format_qty_milli(ing.stock_qty_milli)} {ing.unit}")
            stock_item.setTextAlignment(Qt.AlignTrailing | Qt.AlignVCenter)
            min_item = QTableWidgetItem(f"{format_qty_milli(ing.min_threshold_milli)} {ing.unit}")
            min_item.setTextAlignment(Qt.AlignTrailing | Qt.AlignVCenter)
            cost_item = QTableWidgetItem(
                format_minor(ing.cost_per_unit_minor, self.config.currency)
                if ing.cost_per_unit_minor > 0
                else "-"
            )
            cost_item.setTextAlignment(Qt.AlignTrailing | Qt.AlignVCenter)

            if ing.is_archived:
                st_txt = tr("modifiers.status_archived")
            elif ing.is_low_stock:
                st_txt = tr("inventory.status.low")
            elif not ing.is_active:
                st_txt = tr("modifiers.status_disabled")
            else:
                st_txt = tr("inventory.status.ok")
            st_item = QTableWidgetItem(st_txt)

            self.ing_table.setItem(r_idx, 0, name_item)
            self.ing_table.setItem(r_idx, 1, unit_item)
            self.ing_table.setItem(r_idx, 2, stock_item)
            self.ing_table.setItem(r_idx, 3, min_item)
            self.ing_table.setItem(r_idx, 4, cost_item)
            self.ing_table.setItem(r_idx, 5, st_item)

            act_wrap = QWidget()
            al = QHBoxLayout(act_wrap)
            al.setContentsMargins(6, 4, 6, 4)
            al.setSpacing(4)
            adj_btn = QPushButton(tr("inventory.action.adjust"))
            adj_btn.setProperty("role", "secondary")
            adj_btn.setStyleSheet("padding: 3px 8px; font-size: 11.5px;")
            adj_btn.clicked.connect(lambda _c=False, i=ing: self._open_adjust_dialog(i))

            edit_btn = QPushButton(tr("products.action.edit"))
            edit_btn.setStyleSheet("padding: 3px 8px; font-size: 11.5px;")
            edit_btn.clicked.connect(lambda _c=False, i=ing: self._open_edit_dialog(i))

            arch_btn = QPushButton(
                tr("modifiers.restore_btn") if ing.is_archived else tr("inventory.action.archive")
            )
            arch_btn.setProperty("role", "danger")
            arch_btn.setStyleSheet("padding: 3px 8px; font-size: 11.5px;")
            arch_btn.clicked.connect(lambda _c=False, i=ing: self._toggle_archive(i))

            al.addWidget(adj_btn)
            al.addWidget(edit_btn)
            al.addWidget(arch_btn)
            self.ing_table.setCellWidget(r_idx, 6, act_wrap)
            self.ing_table.setRowHeight(r_idx, 42)

        self.refresh_movements()

    def refresh_movements(self) -> None:
        mtype = str(self.mv_type_filter.currentData() or "")
        try:
            mvs = self.db.list_stock_movements(movement_type=mtype or None, limit=120)
        except Exception:
            mvs = []
        self.mv_table.setRowCount(len(mvs))
        for r_idx, mv in enumerate(mvs):
            ing_name = (mv.ingredient_name_en or mv.ingredient_name_ar).strip()
            delta_s = format_qty_milli(mv.delta_qty_milli)
            if mv.delta_qty_milli > 0 and not delta_s.startswith("+"):
                delta_s = f"+{delta_s}"
            self.mv_table.setItem(r_idx, 0, QTableWidgetItem(format_local_timestamp(mv.created_at)))
            self.mv_table.setItem(r_idx, 1, QTableWidgetItem(ing_name))
            self.mv_table.setItem(r_idx, 2, QTableWidgetItem(tr(f"inventory.mv.{mv.movement_type}")))
            self.mv_table.setItem(r_idx, 3, QTableWidgetItem(f"{delta_s} {mv.unit}"))
            self.mv_table.setItem(
                r_idx, 4, QTableWidgetItem(f"{format_qty_milli(mv.balance_after_milli)} {mv.unit}")
            )
            self.mv_table.setItem(r_idx, 5, QTableWidgetItem(mv.reference_id or mv.reference_type or "-"))
            self.mv_table.setItem(r_idx, 6, QTableWidgetItem(mv.notes or mv.actor_name or "-"))
            self.mv_table.setRowHeight(r_idx, 32)

    def _open_add_dialog(self) -> None:
        dlg = IngredientEditorDialog(self.db, self.config, None, parent=self)
        win = self.window()
        if hasattr(win, "clamp_dialog_to_screen"):
            win.clamp_dialog_to_screen(dlg)
        if dlg.exec() == QDialog.Accepted:
            self.refresh()
            self.inventory_changed.emit()

    def _open_edit_dialog(self, ing: IngredientRecord) -> None:
        dlg = IngredientEditorDialog(self.db, self.config, ing, parent=self)
        win = self.window()
        if hasattr(win, "clamp_dialog_to_screen"):
            win.clamp_dialog_to_screen(dlg)
        if dlg.exec() == QDialog.Accepted:
            self.refresh()
            self.inventory_changed.emit()

    def _open_adjust_dialog(self, ing: IngredientRecord) -> None:
        dlg = StockAdjustDialog(self.db, self.config, ing, parent=self)
        win = self.window()
        if hasattr(win, "clamp_dialog_to_screen"):
            win.clamp_dialog_to_screen(dlg)
        if dlg.exec() == QDialog.Accepted:
            self.refresh()
            self.inventory_changed.emit()

    def _toggle_archive(self, ing: IngredientRecord) -> None:
        try:
            self.db.archive_ingredient(ing.id, restore=ing.is_archived)
        except (ValueError, AuthorizationError) as exc:
            QMessageBox.warning(self, tr("common.validation_error"), str(exc))
            return
        self.refresh()
        self.inventory_changed.emit()

