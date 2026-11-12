from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..auth import AuthorizationError, Permission, UserAccount, has_permission
from ..config import BusinessConfig
from ..db import Database, Product
from ..icons import get_bundled_product_asset_path, get_svg_icon
from ..i18n import is_rtl, tr, tr_category, tr_category_record, tr_count
from ..money import format_minor
from ..theme import contrast_text_hex
from .animations import attach_button_feedback
from .inventory_view import InventoryView
from .modifier_manager_dialog import ModifierManagerDialog
from .product_editor_dialog import ProductEditorDialog
from .widgets import EmptyStateWidget, SearchLineEdit, SquareThumbLabel


class ProductsView(QWidget):
    """Boutique Café catalog management view with transparent thumbnails, recipes, and inventory."""

    products_changed = Signal()
    categories_changed = Signal()
    inventory_changed = Signal()
    status_message = Signal(str)

    def __init__(self, db: Database, config: BusinessConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.db = db
        self.config = config
        self._products: list[Product] = []
        self._current_user: UserAccount | None = None

        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 10, 16, 12)
        outer.setSpacing(10)

        # Sub-section switcher: Menu Catalog | Inventory & Stock
        sub_bar = QHBoxLayout()
        sub_bar.setContentsMargins(0, 0, 0, 0)
        sub_bar.setSpacing(8)
        self.catalog_sub_btn = QPushButton(tr("products.sub.catalog"))
        self.catalog_sub_btn.setProperty("class", "CategoryChip")
        self.catalog_sub_btn.setProperty("selected", "true")
        self.catalog_sub_btn.setCursor(Qt.PointingHandCursor)
        self.catalog_sub_btn.clicked.connect(lambda: self.switch_subview(0))
        attach_button_feedback(self.catalog_sub_btn)

        self.inventory_sub_btn = QPushButton(tr("products.sub.inventory"))
        self.inventory_sub_btn.setProperty("class", "CategoryChip")
        self.inventory_sub_btn.setProperty("selected", "false")
        self.inventory_sub_btn.setCursor(Qt.PointingHandCursor)
        self.inventory_sub_btn.clicked.connect(lambda: self.switch_subview(1))
        attach_button_feedback(self.inventory_sub_btn)

        sub_bar.addWidget(self.catalog_sub_btn)
        sub_bar.addWidget(self.inventory_sub_btn)
        sub_bar.addStretch(1)
        outer.addLayout(sub_bar)

        self.view_stack = QStackedWidget()

        panel = QFrame()
        panel.setProperty("class", "BoutiqueCard")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(12)

        # Toolbar Header
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        self.title_lbl = QLabel(tr("products.heading"))
        self.title_lbl.setProperty("class", "PageHeading")

        self.count_badge = QLabel(tr_count("item", 0))
        self.count_badge.setProperty("class", "CountBadge")

        self.search_input = SearchLineEdit(tr("products.search_ph"))
        self.search_input.setMinimumWidth(156)
        self.search_input.setMaximumWidth(250)
        self.search_input.textChanged.connect(self.refresh)

        self.cat_filter = QComboBox()
        self.cat_filter.setMinimumWidth(112)
        self.cat_filter.currentIndexChanged.connect(self.refresh)

        self.modifiers_btn = QPushButton(tr("products.modifiers_btn"))
        self.modifiers_btn.setProperty("role", "secondary")
        self.modifiers_btn.setToolTip(tr("products.modifiers_tip"))
        self.modifiers_btn.setIcon(get_svg_icon("sliders", "#202820", 14))
        self.modifiers_btn.setIconSize(QSize(14, 14))
        self.modifiers_btn.clicked.connect(self.open_modifiers_dialog)
        attach_button_feedback(self.modifiers_btn)

        self.restore_btn = QPushButton(tr("products.restore_btn"))
        self.restore_btn.setToolTip(tr("products.restore_tip"))
        self.restore_btn.setIcon(get_svg_icon("refresh", "#202820", 14))
        self.restore_btn.setIconSize(QSize(14, 14))
        self.restore_btn.clicked.connect(self.restore_samples)

        self.add_btn = QPushButton(tr("products.add_btn"))
        self.add_btn.setProperty("role", "primary")
        self.add_btn.setIcon(get_svg_icon("plus", "#FFFFFF", 14))
        self.add_btn.setIconSize(QSize(14, 14))
        self.add_btn.clicked.connect(self.open_add_dialog)

        toolbar.addWidget(self.title_lbl)
        toolbar.addWidget(self.count_badge)
        toolbar.addStretch(1)
        toolbar.addWidget(self.search_input)
        toolbar.addWidget(self.cat_filter)
        toolbar.addWidget(self.modifiers_btn)
        toolbar.addWidget(self.restore_btn)
        toolbar.addWidget(self.add_btn)
        layout.addLayout(toolbar)

        # Catalog Table vs Empty State
        self.content_stack = QStackedWidget()

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            [
                tr("products.col.product"),
                tr("products.col.category"),
                tr("products.col.price"),
                tr("products.col.actions"),
            ]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.itemDoubleClicked.connect(self._on_row_double_clicked)

        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.Interactive)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.Fixed)
        self.table.setColumnWidth(1, 138)
        self.table.setColumnWidth(3, 196)

        self.empty_state = EmptyStateWidget(
            "products",
            tr("products.empty_default.title"),
            tr("products.empty_default.desc"),
            action_label=tr("products.add_btn"),
            accent_color=self.config.accent_color,
        )
        self.empty_state.action_triggered.connect(self.open_add_dialog)

        self.content_stack.addWidget(self.table)
        self.content_stack.addWidget(self.empty_state)
        layout.addWidget(self.content_stack, 1)

        self.inventory_view = InventoryView(self.db, self.config, parent=self)
        self.inventory_view.inventory_changed.connect(self.inventory_changed.emit)
        self.inventory_view.status_message.connect(self.status_message.emit)

        self.view_stack.addWidget(panel)
        self.view_stack.addWidget(self.inventory_view)
        outer.addWidget(self.view_stack, 1)

    def switch_subview(self, index: int) -> None:
        idx = 1 if int(index) == 1 else 0
        self.view_stack.setCurrentIndex(idx)
        self.catalog_sub_btn.setProperty("selected", "true" if idx == 0 else "false")
        self.inventory_sub_btn.setProperty("selected", "true" if idx == 1 else "false")
        for b in (self.catalog_sub_btn, self.inventory_sub_btn):
            b.style().unpolish(b)
            b.style().polish(b)
        if idx == 1:
            self.inventory_view.refresh()

    def open_low_stock_filter(self) -> None:
        self.switch_subview(1)
        self.inventory_view.set_low_stock_filter(True)

    def set_current_user(self, user: UserAccount | None) -> None:
        self._current_user = user

    def _can_manage_categories(self) -> bool:
        if self._current_user is None:
            return not self.db.has_any_users()
        return has_permission(self._current_user, Permission.MANAGE_CATEGORIES)

    def set_config(self, config: BusinessConfig) -> None:
        self.config = config
        self.empty_state.refresh_accent(config.accent_color)
        self.add_btn.setIcon(get_svg_icon("plus", contrast_text_hex(config.accent_color), 14))
        self.inventory_view.set_config(config)
        self.refresh()

    def retranslate_ui(self) -> None:
        self.catalog_sub_btn.setText(tr("products.sub.catalog"))
        self.inventory_sub_btn.setText(tr("products.sub.inventory"))
        self.inventory_view.retranslate_ui()
        self.title_lbl.setText(tr("products.heading"))
        self.search_input.setPlaceholderText(tr("products.search_ph"))
        self.modifiers_btn.setText(tr("products.modifiers_btn"))
        self.modifiers_btn.setToolTip(tr("products.modifiers_tip"))
        self.restore_btn.setText(tr("products.restore_btn"))
        self.restore_btn.setToolTip(tr("products.restore_tip"))
        self.add_btn.setText(tr("products.add_btn"))
        self.table.setHorizontalHeaderLabels(
            [
                tr("products.col.product"),
                tr("products.col.category"),
                tr("products.col.price"),
                tr("products.col.actions"),
            ]
        )
        self.empty_state.action_btn.setText(tr("products.add_btn"))
        self.refresh()

    def focus_search(self) -> None:
        if self.view_stack.currentIndex() == 1:
            self.inventory_view.focus_search()
            return
        self.search_input.setFocus()
        self.search_input.selectAll()

    def refresh(self) -> None:
        all_cat_records = self.db.list_category_records(include_archived=True)
        cat_by_id = {c.id: c for c in all_cat_records}
        active_cat_records = [c for c in all_cat_records if not c.is_archived]

        current_cat = self.cat_filter.currentData() or ""
        self.cat_filter.blockSignals(True)
        self.cat_filter.clear()
        self.cat_filter.addItem(tr("category.all"), "")
        for cat_rec in active_cat_records:
            self.cat_filter.addItem(tr_category_record(cat_rec), cat_rec.primary_name)
        idx = self.cat_filter.findData(current_cat)
        self.cat_filter.setCurrentIndex(idx if idx >= 0 else 0)
        self.cat_filter.blockSignals(False)

        selected_cat = self.cat_filter.currentData() or ""
        self._products = self.db.list_products(
            search=self.search_input.text(),
            category=selected_cat,
        )
        mod_counts = self.db.list_product_modifier_counts()

        count = len(self._products)
        self.count_badge.setText(tr_count("item", count))

        for r in range(self.table.rowCount()):
            for c in range(self.table.columnCount()):
                w = self.table.cellWidget(r, c)
                if w:
                    self.table.removeCellWidget(r, c)
                    w.setParent(None)
                    w.deleteLater()
        self.table.clearContents()

        if not self._products:
            self.table.setRowCount(0)
            has_filter = bool(self.search_input.text().strip()) or bool(selected_cat)
            if has_filter:
                self.empty_state.set_texts(
                    tr("products.empty_filtered.title"),
                    tr("products.empty_filtered.desc"),
                    tr("products.add_btn"),
                )
            else:
                self.empty_state.set_texts(
                    tr("products.empty_menu.title"),
                    tr("products.empty_menu.desc"),
                    tr("products.add_btn"),
                )
            self.content_stack.setCurrentIndex(1)
            return

        self.content_stack.setCurrentIndex(0)
        self.table.setRowCount(len(self._products))

        for row_idx, prod in enumerate(self._products):
            prod_cell = QWidget()
            prod_layout = QHBoxLayout(prod_cell)
            prod_layout.setContentsMargins(8, 6, 8, 6)
            prod_layout.setSpacing(10)

            asset_key = prod.name_en or prod.name
            display_name = prod.display_name()

            thumb = SquareThumbLabel(size=44, radius=8)
            thumb.set_product_image(
                prod.image_filename,
                prod.category,
                self.config.accent_color,
                product_name=asset_key,
            )
            prod_layout.addWidget(thumb)

            text_col = QVBoxLayout()
            text_col.setContentsMargins(0, 0, 0, 0)
            text_col.setSpacing(2)
            lead_align = (Qt.AlignRight if is_rtl() else Qt.AlignLeft) | Qt.AlignVCenter | Qt.AlignAbsolute
            name_lbl = QLabel(display_name)
            name_lbl.setAlignment(lead_align)
            name_lbl.setStyleSheet("font-size: 13px; font-weight: 700; color: #202820;")
            if prod.image_filename:
                sub_text = tr(
                    "products.photo_custom",
                    license=prod.image_license or tr("products.photo_custom_local"),
                )
            elif get_bundled_product_asset_path(asset_key) is not None:
                sub_text = tr("products.photo_studio")
            else:
                sub_text = tr("products.photo_artwork")
            mod_cnt = mod_counts.get(prod.id, 0)
            if mod_cnt > 0:
                sub_text = f"{sub_text} · {tr('products.modifiers_badge', count=mod_cnt)}"
            sub_lbl = QLabel(sub_text)
            sub_lbl.setProperty("class", "MutedLabel")
            sub_lbl.setAlignment(lead_align)
            text_col.addWidget(name_lbl)
            text_col.addWidget(sub_lbl)
            prod_layout.addLayout(text_col, 1)
            self.table.setCellWidget(row_idx, 0, prod_cell)

            cat_wrap = QWidget()
            cat_layout = QHBoxLayout(cat_wrap)
            cat_layout.setContentsMargins(8, 6, 8, 6)
            cat_layout.setSpacing(4)
            cat_rec = cat_by_id.get(prod.category_id) if prod.category_id is not None else None
            cat_label = tr_category_record(cat_rec) if cat_rec is not None else tr_category(prod.category)
            cat_pill = QLabel(cat_label)
            cat_pill.setProperty("class", "CategoryPill")
            cat_layout.addWidget(cat_pill)
            if cat_rec is not None and cat_rec.is_archived:
                arch_badge = QLabel(tr("cat_combo.archived_badge"))
                arch_badge.setProperty("class", "PeachBadge")
                cat_layout.addWidget(arch_badge)
            cat_layout.addStretch(1)
            self.table.setCellWidget(row_idx, 1, cat_wrap)

            price_item = QTableWidgetItem(format_minor(prod.price_minor, self.config.currency))
            price_item.setTextAlignment(Qt.AlignTrailing | Qt.AlignVCenter)
            font = price_item.font()
            font.setBold(True)
            price_item.setFont(font)
            self.table.setItem(row_idx, 2, price_item)

            actions_wrap = QWidget()
            actions_layout = QHBoxLayout(actions_wrap)
            actions_layout.setContentsMargins(8, 6, 8, 6)
            actions_layout.setSpacing(6)

            edit_btn = QPushButton(tr("products.action.edit"))
            edit_btn.setIcon(get_svg_icon("edit", "#202820", 13))
            edit_btn.setIconSize(QSize(13, 13))
            edit_btn.setStyleSheet("padding: 4px 10px; font-size: 12px;")
            edit_btn.clicked.connect(lambda _c=False, p=prod: self.open_edit_dialog(p))

            del_btn = QPushButton(tr("products.action.delete"))
            del_btn.setProperty("role", "danger")
            del_btn.setIcon(get_svg_icon("trash", "#B23A3A", 13))
            del_btn.setIconSize(QSize(13, 13))
            del_btn.setStyleSheet("padding: 4px 10px; font-size: 12px;")
            del_btn.clicked.connect(lambda _c=False, p=prod: self.delete_product(p))

            actions_layout.addWidget(edit_btn)
            actions_layout.addWidget(del_btn)
            self.table.setCellWidget(row_idx, 3, actions_wrap)

            self.table.setRowHeight(row_idx, 60)

        max_actions_w = 196
        for row_idx in range(self.table.rowCount()):
            w = self.table.cellWidget(row_idx, 3)
            if w is not None:
                max_actions_w = max(max_actions_w, w.sizeHint().width() + 14)
        self.table.setColumnWidth(3, max_actions_w)

    def _on_row_double_clicked(self, item: QTableWidgetItem) -> None:
        row = item.row()
        if 0 <= row < len(self._products):
            self.open_edit_dialog(self._products[row])

    def _on_inline_categories_mutated(self) -> None:
        self.refresh()
        self.categories_changed.emit()

    def open_add_dialog(self) -> None:
        dlg = ProductEditorDialog(
            self.config,
            self.db.list_categories(),
            product=None,
            parent=self,
            db=self.db,
            is_admin=self._can_manage_categories(),
        )
        dlg.categories_mutated.connect(self._on_inline_categories_mutated)
        if dlg.exec() == QDialog.Accepted:
            try:
                created = self.db.add_product(
                    dlg.result_name,
                    dlg.result_category,
                    dlg.result_price_minor,
                    image_filename=dlg.result_image_filename,
                    image_source=dlg.result_image_source,
                    image_attribution=dlg.result_image_attribution,
                    image_license=dlg.result_image_license,
                    image_source_url=dlg.result_image_source_url,
                    name_en=dlg.result_name_en,
                    name_ar=dlg.result_name_ar,
                    category_id=dlg.result_category_id,
                    pending_category_en=dlg.result_pending_cat_en,
                    pending_category_ar=dlg.result_pending_cat_ar,
                )
                if dlg.result_modifier_group_ids or dlg.result_option_price_overrides:
                    self.db.set_product_modifier_config(
                        created.id,
                        dlg.result_modifier_group_ids,
                        dlg.result_option_price_overrides,
                    )
                if dlg.result_recipe_entries and self._can_manage_categories():
                    self.db.set_product_recipe(created.id, dlg.result_recipe_entries)
                    self.inventory_changed.emit()
            except (ValueError, AuthorizationError) as exc:
                QMessageBox.warning(self, tr("common.validation_error"), str(exc))
                return
            self.refresh()
            self.products_changed.emit()
            if dlg.result_pending_cat_en or dlg.result_pending_cat_ar:
                self.categories_changed.emit()
            self.status_message.emit(tr("products.toast.added", name=created.display_name()))

    def open_edit_dialog(self, prod: Product) -> None:
        dlg = ProductEditorDialog(
            self.config,
            self.db.list_categories(),
            product=prod,
            parent=self,
            db=self.db,
            is_admin=self._can_manage_categories(),
        )
        dlg.categories_mutated.connect(self._on_inline_categories_mutated)
        if dlg.exec() == QDialog.Accepted:
            try:
                updated = self.db.update_product(
                    prod.id,
                    dlg.result_name,
                    dlg.result_category,
                    dlg.result_price_minor,
                    image_filename=dlg.result_image_filename,
                    image_source=dlg.result_image_source,
                    image_attribution=dlg.result_image_attribution,
                    image_license=dlg.result_image_license,
                    image_source_url=dlg.result_image_source_url,
                    name_en=dlg.result_name_en,
                    name_ar=dlg.result_name_ar,
                    category_id=dlg.result_category_id,
                    pending_category_en=dlg.result_pending_cat_en,
                    pending_category_ar=dlg.result_pending_cat_ar,
                )
                self.db.set_product_modifier_config(
                    updated.id,
                    dlg.result_modifier_group_ids,
                    dlg.result_option_price_overrides,
                )
                if self._can_manage_categories():
                    self.db.set_product_recipe(updated.id, dlg.result_recipe_entries)
                    self.inventory_changed.emit()
            except (ValueError, AuthorizationError) as exc:
                QMessageBox.warning(self, tr("common.validation_error"), str(exc))
                return
            self.refresh()
            self.products_changed.emit()
            if dlg.result_pending_cat_en or dlg.result_pending_cat_ar:
                self.categories_changed.emit()
            self.status_message.emit(tr("products.toast.updated", name=updated.display_name()))

    def delete_product(self, prod: Product) -> None:
        disp = prod.display_name()
        answer = QMessageBox.question(
            self,
            tr("products.confirm_delete_title"),
            tr("products.confirm_delete_body", name=disp),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self.db.delete_product(prod.id)
            self.refresh()
            self.products_changed.emit()
            self.status_message.emit(tr("products.toast.deleted", name=disp))

    def open_modifiers_dialog(self) -> None:
        dlg = ModifierManagerDialog(self.db, self.config, parent=self)
        mutated = False

        def _on_mutated() -> None:
            nonlocal mutated
            mutated = True
            self.refresh()
            self.products_changed.emit()

        dlg.modifiers_mutated.connect(_on_mutated)
        dlg.exec()
        if mutated:
            self.refresh()

    def restore_samples(self) -> None:
        answer = QMessageBox.question(
            self,
            tr("products.confirm_restore.title"),
            tr("products.confirm_restore.body"),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self.db.restore_sample_products()
            self.refresh()
            self.categories_changed.emit()
            self.products_changed.emit()
            self.status_message.emit(tr("products.toast.restored"))
