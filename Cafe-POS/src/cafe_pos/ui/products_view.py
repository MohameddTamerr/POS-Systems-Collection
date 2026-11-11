from __future__ import annotations

from PySide6.QtCore import Qt, Signal
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
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..config import BusinessConfig
from ..db import Database, Product
from ..money import format_major_input, format_minor, get_currency, parse_major_to_minor

STANDARD_CATEGORIES = [
    "Espresso",
    "Cold Drinks",
    "Tea & Matcha",
    "Bakery",
    "Kitchen",
    "Retail",
]


class ProductEditorDialog(QDialog):
    """Native desktop modal dialog to add or edit a product."""

    def __init__(
        self,
        currency_code: str,
        existing_categories: list[str],
        product: Product | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.currency_code = currency_code
        self.setWindowTitle("Edit Product" if product else "Add Product")
        self.setModal(True)
        self.setMinimumWidth(400)

        self.result_name: str = ""
        self.result_category: str = ""
        self.result_price_minor: int = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(14)

        form = QFormLayout()
        form.setSpacing(10)

        self.name_edit = QLineEdit(product.name if product else "")
        self.name_edit.setMaxLength(80)
        self.name_edit.setPlaceholderText("e.g. Iced Caramel Latte")
        form.addRow("Product Name *", self.name_edit)

        self.category_combo = QComboBox()
        self.category_combo.setEditable(True)
        merged_cats = list(dict.fromkeys(STANDARD_CATEGORIES + existing_categories))
        for cat in merged_cats:
            self.category_combo.addItem(cat)
        if product:
            idx = self.category_combo.findText(product.category)
            if idx >= 0:
                self.category_combo.setCurrentIndex(idx)
            else:
                self.category_combo.setEditText(product.category)
        form.addRow("Category *", self.category_combo)

        spec = get_currency(currency_code)
        self.price_edit = QLineEdit(
            format_major_input(product.price_minor, currency_code) if product else ""
        )
        self.price_edit.setPlaceholderText("0.00" if spec.decimals == 2 else "0")
        form.addRow(f"Price ({spec.code}) *", self.price_edit)

        layout.addLayout(form)

        btn_row = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)

        save_btn = QPushButton("Save Product")
        save_btn.setProperty("role", "primary")
        save_btn.setDefault(True)
        save_btn.clicked.connect(self._on_save)

        btn_row.addStretch(1)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

    def _on_save(self) -> None:
        name = self.name_edit.text().strip()
        category = self.category_combo.currentText().strip()
        if not name:
            QMessageBox.warning(self, "Validation Error", "Product name is required.")
            self.name_edit.setFocus()
            return
        if not category:
            QMessageBox.warning(self, "Validation Error", "Product category is required.")
            self.category_combo.setFocus()
            return
        try:
            price_minor = parse_major_to_minor(self.price_edit.text(), self.currency_code)
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid Price", str(exc))
            self.price_edit.setFocus()
            return

        if price_minor <= 0:
            QMessageBox.warning(self, "Invalid Price", "Price must be greater than zero.")
            self.price_edit.setFocus()
            return

        self.result_name = name
        self.result_category = category
        self.result_price_minor = price_minor
        self.accept()


class ProductsView(QWidget):
    """Products catalog management view."""

    products_changed = Signal()
    status_message = Signal(str)

    def __init__(self, db: Database, config: BusinessConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.db = db
        self.config = config
        self._products: list[Product] = []

        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(10)

        panel = QFrame()
        panel.setProperty("class", "TerminalPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        # Toolbar
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        title = QLabel("Products Management")
        title.setProperty("class", "SectionHeader")

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Filter products...")
        self.search_input.setMaximumWidth(260)
        self.search_input.textChanged.connect(self.refresh)

        self.cat_filter = QComboBox()
        self.cat_filter.currentIndexChanged.connect(self.refresh)

        self.add_btn = QPushButton("Add Product")
        self.add_btn.setProperty("role", "primary")
        self.add_btn.clicked.connect(self.open_add_dialog)

        self.edit_btn = QPushButton("Edit Selected")
        self.edit_btn.setEnabled(False)
        self.edit_btn.clicked.connect(self.open_edit_selected)

        self.delete_btn = QPushButton("Delete Selected")
        self.delete_btn.setProperty("role", "danger")
        self.delete_btn.setEnabled(False)
        self.delete_btn.clicked.connect(self.delete_selected)

        self.restore_btn = QPushButton("Restore Sample Menu")
        self.restore_btn.clicked.connect(self.restore_samples)

        toolbar.addWidget(title)
        toolbar.addStretch(1)
        toolbar.addWidget(self.search_input)
        toolbar.addWidget(self.cat_filter)
        toolbar.addWidget(self.restore_btn)
        toolbar.addWidget(self.edit_btn)
        toolbar.addWidget(self.delete_btn)
        toolbar.addWidget(self.add_btn)
        layout.addLayout(toolbar)

        # Table
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["ID", "Product Name", "Category", "Price"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.itemSelectionChanged.connect(self._update_selection_buttons)
        self.table.itemDoubleClicked.connect(lambda _item: self.open_edit_selected())

        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)

        layout.addWidget(self.table, 1)
        outer.addWidget(panel, 1)

    def set_config(self, config: BusinessConfig) -> None:
        self.config = config
        self.refresh()

    def focus_search(self) -> None:
        self.search_input.setFocus()
        self.search_input.selectAll()

    def refresh(self) -> None:
        current_cat = self.cat_filter.currentText() or "All"
        self.cat_filter.blockSignals(True)
        self.cat_filter.clear()
        self.cat_filter.addItem("All")
        for cat in self.db.list_categories():
            self.cat_filter.addItem(cat)
        idx = self.cat_filter.findText(current_cat)
        self.cat_filter.setCurrentIndex(idx if idx >= 0 else 0)
        self.cat_filter.blockSignals(False)

        selected_cat = self.cat_filter.currentText()
        self._products = self.db.list_products(
            search=self.search_input.text(),
            category="" if selected_cat == "All" else selected_cat,
        )

        self.table.setRowCount(len(self._products))
        for row_idx, prod in enumerate(self._products):
            id_item = QTableWidgetItem(str(prod.id))
            id_item.setTextAlignment(Qt.AlignCenter)
            name_item = QTableWidgetItem(prod.name)
            cat_item = QTableWidgetItem(prod.category)
            price_item = QTableWidgetItem(format_minor(prod.price_minor, self.config.currency))
            price_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)

            self.table.setItem(row_idx, 0, id_item)
            self.table.setItem(row_idx, 1, name_item)
            self.table.setItem(row_idx, 2, cat_item)
            self.table.setItem(row_idx, 3, price_item)

        self._update_selection_buttons()

    def _selected_product(self) -> Product | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        row = rows[0].row()
        if 0 <= row < len(self._products):
            return self._products[row]
        return None

    def _update_selection_buttons(self) -> None:
        has_sel = self._selected_product() is not None
        self.edit_btn.setEnabled(has_sel)
        self.delete_btn.setEnabled(has_sel)

    def open_add_dialog(self) -> None:
        dlg = ProductEditorDialog(
            self.config.currency,
            self.db.list_categories(),
            product=None,
            parent=self,
        )
        if dlg.exec() == QDialog.Accepted:
            created = self.db.add_product(
                dlg.result_name, dlg.result_category, dlg.result_price_minor
            )
            self.refresh()
            self.products_changed.emit()
            self.status_message.emit(f"Added product '{created.name}'.")

    def open_edit_selected(self) -> None:
        prod = self._selected_product()
        if not prod:
            return
        dlg = ProductEditorDialog(
            self.config.currency,
            self.db.list_categories(),
            product=prod,
            parent=self,
        )
        if dlg.exec() == QDialog.Accepted:
            updated = self.db.update_product(
                prod.id, dlg.result_name, dlg.result_category, dlg.result_price_minor
            )
            self.refresh()
            self.products_changed.emit()
            self.status_message.emit(f"Updated product '{updated.name}'.")

    def delete_selected(self) -> None:
        prod = self._selected_product()
        if not prod:
            return
        answer = QMessageBox.question(
            self,
            "Delete Product",
            f"Delete '{prod.name}' from the catalog?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self.db.delete_product(prod.id)
            self.refresh()
            self.products_changed.emit()
            self.status_message.emit(f"Deleted product '{prod.name}'.")

    def restore_samples(self) -> None:
        answer = QMessageBox.question(
            self,
            "Restore Sample Menu",
            "Replace the current product list with the default sample café menu?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self.db.restore_sample_products()
            self.refresh()
            self.products_changed.emit()
            self.status_message.emit("Sample café menu restored.")

