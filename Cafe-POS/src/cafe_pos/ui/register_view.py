from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..config import BusinessConfig
from ..db import CartLine, Database, OrderRecord, Product
from ..money import format_major_input, format_minor, get_currency, parse_major_to_minor
from .widgets import CartQtyWidget, ProductTileButton


class RegisterView(QWidget):
    """Main Cashier Register: left product grid + right active order & cash checkout."""

    order_completed = Signal(object)  # emits OrderRecord
    status_message = Signal(str)

    def __init__(self, db: Database, config: BusinessConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.db = db
        self.config = config
        self.selected_category = "All"
        self.cart: dict[int, CartLine] = {}
        self._last_products: list[Product] = []

        self._build_ui()
        self.refresh_catalog()
        self.refresh_cart()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(0)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(10)

        # LEFT SECTION: Categories, Search, Products
        left_panel = QFrame()
        left_panel.setProperty("class", "TerminalPanel")
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(14, 14, 14, 14)
        left_layout.setSpacing(10)

        search_row = QHBoxLayout()
        search_row.setSpacing(8)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search products by name or category... (Ctrl+F)")
        self.search_input.textChanged.connect(self._on_search_changed)

        self.clear_search_btn = QPushButton("Clear")
        self.clear_search_btn.setVisible(False)
        self.clear_search_btn.clicked.connect(self._clear_search)

        search_row.addWidget(self.search_input, 1)
        search_row.addWidget(self.clear_search_btn)
        left_layout.addLayout(search_row)

        # Category strip inside scroll-safe horizontal layout
        cat_scroll = QScrollArea()
        cat_scroll.setWidgetResizable(True)
        cat_scroll.setFixedHeight(42)
        cat_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        cat_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.category_container = QWidget()
        self.category_container.setObjectName("CategoryStripHost")
        self.category_layout = QHBoxLayout(self.category_container)
        self.category_layout.setContentsMargins(0, 2, 0, 2)
        self.category_layout.setSpacing(6)
        cat_scroll.setWidget(self.category_container)
        left_layout.addWidget(cat_scroll)

        # Product grid area
        self.product_scroll = QScrollArea()
        self.product_scroll.setWidgetResizable(True)
        self.product_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.product_grid_host = QWidget()
        self.product_grid_host.setObjectName("ProductGridHost")
        self.product_grid = QGridLayout(self.product_grid_host)
        self.product_grid.setContentsMargins(0, 4, 6, 4)
        self.product_grid.setSpacing(10)
        self.product_grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.product_scroll.setWidget(self.product_grid_host)
        left_layout.addWidget(self.product_scroll, 1)

        # RIGHT SECTION: Active Order, Quantities, Cash, Change, Checkout
        right_panel = QFrame()
        right_panel.setProperty("class", "TerminalPanel")
        right_panel.setMinimumWidth(365)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        cart_header = QWidget()
        cart_header_layout = QHBoxLayout(cart_header)
        cart_header_layout.setContentsMargins(14, 12, 14, 10)
        cart_title = QLabel("Current Order")
        cart_title.setProperty("class", "SectionHeader")

        self.item_count_lbl = QLabel("0 items")
        self.item_count_lbl.setProperty("class", "MutedLabel")

        self.clear_cart_btn = QPushButton("Clear Order")
        self.clear_cart_btn.setProperty("role", "danger")
        self.clear_cart_btn.clicked.connect(self._confirm_clear_cart)

        cart_header_layout.addWidget(cart_title)
        cart_header_layout.addWidget(self.item_count_lbl)
        cart_header_layout.addStretch(1)
        cart_header_layout.addWidget(self.clear_cart_btn)
        right_layout.addWidget(cart_header)

        # Cart Table
        table_wrap = QWidget()
        table_wrap_layout = QVBoxLayout(table_wrap)
        table_wrap_layout.setContentsMargins(12, 0, 12, 10)

        self.cart_table = QTableWidget(0, 4)
        self.cart_table.setHorizontalHeaderLabels(["Item", "Qty", "Total", ""])
        self.cart_table.verticalHeader().setVisible(False)
        self.cart_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.cart_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.cart_table.setAlternatingRowColors(True)

        header = self.cart_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Fixed)
        self.cart_table.setColumnWidth(1, 108)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Fixed)
        self.cart_table.setColumnWidth(3, 44)

        table_wrap_layout.addWidget(self.cart_table)
        right_layout.addWidget(table_wrap, 1)

        # Summary & Cash Register Footer
        summary_box = QFrame()
        summary_box.setObjectName("OrderSummaryBox")
        summary_layout = QVBoxLayout(summary_box)
        summary_layout.setContentsMargins(14, 12, 14, 14)
        summary_layout.setSpacing(9)

        sub_row = QHBoxLayout()
        sub_row.addWidget(QLabel("Subtotal"))
        self.subtotal_lbl = QLabel("$0.00")
        self.subtotal_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        sub_row.addWidget(self.subtotal_lbl)
        summary_layout.addLayout(sub_row)

        tot_row = QHBoxLayout()
        tot_title = QLabel("Total Due")
        tot_title.setStyleSheet("font-size: 15px; font-weight: 700;")
        self.total_lbl = QLabel("$0.00")
        self.total_lbl.setObjectName("TotalValueLabel")
        self.total_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        tot_row.addWidget(tot_title)
        tot_row.addWidget(self.total_lbl)
        summary_layout.addLayout(tot_row)

        cash_row = QHBoxLayout()
        cash_row.setSpacing(8)
        self.cash_label = QLabel("Cash Received")
        self.cash_label.setStyleSheet("font-weight: 600;")
        self.cash_input = QLineEdit()
        self.cash_input.setPlaceholderText("0.00")
        self.cash_input.setAlignment(Qt.AlignRight)
        self.cash_input.textChanged.connect(self._evaluate_payment_state)
        self.cash_input.returnPressed.connect(self._attempt_checkout)
        cash_row.addWidget(self.cash_label)
        cash_row.addStretch(1)
        self.cash_input.setMaximumWidth(150)
        cash_row.addWidget(self.cash_input)
        summary_layout.addLayout(cash_row)

        self.quick_cash_layout = QHBoxLayout()
        self.quick_cash_layout.setSpacing(6)
        summary_layout.addLayout(self.quick_cash_layout)

        chg_row = QHBoxLayout()
        self.change_title_lbl = QLabel("Change Due")
        self.change_title_lbl.setStyleSheet("font-weight: 600;")
        self.change_val_lbl = QLabel("$0.00")
        self.change_val_lbl.setObjectName("ChangeValueLabel")
        self.change_val_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        chg_row.addWidget(self.change_title_lbl)
        chg_row.addStretch(1)
        chg_row.addWidget(self.change_val_lbl)
        summary_layout.addLayout(chg_row)

        self.checkout_btn = QPushButton("Complete Sale (F12)")
        self.checkout_btn.setObjectName("PrimaryActionBtn")
        self.checkout_btn.setMinimumHeight(42)
        self.checkout_btn.clicked.connect(self._attempt_checkout)
        summary_layout.addWidget(self.checkout_btn)

        right_layout.addWidget(summary_box)

        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([660, 420])

        outer.addWidget(splitter, 1)

    def set_config(self, config: BusinessConfig) -> None:
        self.config = config
        self.refresh_catalog()
        self.refresh_cart()

    def focus_search(self) -> None:
        self.search_input.setFocus()
        self.search_input.selectAll()

    def _on_search_changed(self, text: str) -> None:
        self.clear_search_btn.setVisible(bool(text.strip()))
        self._render_product_tiles()

    def _clear_search(self) -> None:
        self.search_input.clear()
        self.search_input.setFocus()

    def refresh_catalog(self) -> None:
        categories = ["All"] + self.db.list_categories()
        if self.selected_category not in categories:
            self.selected_category = "All"

        while self.category_layout.count():
            item = self.category_layout.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()

        for cat in categories:
            btn = QPushButton(cat.replace("&", "&&"))
            btn.setProperty("class", "CategoryButton")
            btn.setProperty("selected", "true" if cat == self.selected_category else "false")
            btn.clicked.connect(lambda _c=False, c_name=cat: self._select_category(c_name))
            self.category_layout.addWidget(btn)
        self.category_layout.addStretch(1)

        self._render_product_tiles()

    def _select_category(self, category: str) -> None:
        self.selected_category = category
        self.refresh_catalog()

    def _render_product_tiles(self) -> None:
        while self.product_grid.count():
            item = self.product_grid.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()

        products = self.db.list_products(
            search=self.search_input.text(),
            category=self.selected_category,
        )
        self._last_products = products

        if not products:
            empty_lbl = QLabel("No matching products found.")
            empty_lbl.setProperty("class", "MutedLabel")
            empty_lbl.setAlignment(Qt.AlignCenter)
            empty_lbl.setMinimumHeight(140)
            self.product_grid.addWidget(empty_lbl, 0, 0)
            return

        avail_width = max(340, self.product_scroll.viewport().width())
        cols = max(2, min(3, (avail_width - 20) // 185))
        for idx, product in enumerate(products):
            price_str = format_minor(product.price_minor, self.config.currency)
            tile = ProductTileButton(product.id, product.name, product.category, price_str)
            tile.clicked.connect(lambda _c=False, p=product: self.add_product_to_cart(p))
            row = idx // cols
            col = idx % cols
            self.product_grid.addWidget(tile, row, col)

        for col in range(cols):
            self.product_grid.setColumnStretch(col, 1)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._last_products:
            self._render_product_tiles()

    def add_product_to_cart(self, product: Product) -> None:
        existing = self.cart.get(product.id)
        if existing:
            self.cart[product.id] = CartLine(
                product_id=product.id,
                name=product.name,
                unit_price_minor=product.price_minor,
                quantity=existing.quantity + 1,
            )
        else:
            self.cart[product.id] = CartLine(
                product_id=product.id,
                name=product.name,
                unit_price_minor=product.price_minor,
                quantity=1,
            )
        self.refresh_cart()
        self.status_message.emit(f"Added {product.name} to order.")

    def update_cart_quantity(self, product_id: int, delta: int) -> None:
        line = self.cart.get(product_id)
        if not line:
            return
        new_qty = line.quantity + delta
        if new_qty <= 0:
            self.cart.pop(product_id, None)
        else:
            self.cart[product_id] = CartLine(
                product_id=line.product_id,
                name=line.name,
                unit_price_minor=line.unit_price_minor,
                quantity=new_qty,
            )
        self.refresh_cart()

    def remove_cart_item(self, product_id: int) -> None:
        self.cart.pop(product_id, None)
        self.refresh_cart()

    def _confirm_clear_cart(self) -> None:
        if not self.cart:
            return
        answer = QMessageBox.question(
            self,
            "Clear Order",
            "Remove all items from the current order?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self.cart.clear()
            self.cash_input.clear()
            self.refresh_cart()
            self.status_message.emit("Active order cleared.")

    def sync_cart_with_catalog(self) -> None:
        """Refresh active cart item names/prices or drop deleted items after product edits."""
        catalog_map = {p.id: p for p in self.db.list_products()}
        updated: dict[int, CartLine] = {}
        for pid, line in self.cart.items():
            prod = catalog_map.get(pid)
            if prod is not None:
                updated[pid] = CartLine(
                    product_id=prod.id,
                    name=prod.name,
                    unit_price_minor=prod.price_minor,
                    quantity=line.quantity,
                )
        self.cart = updated
        self.refresh_cart()

    def cart_totals_minor(self) -> tuple[int, int]:
        total_items = sum(line.quantity for line in self.cart.values())
        subtotal = sum(line.line_total_minor for line in self.cart.values())
        return total_items, subtotal

    def refresh_cart(self) -> None:
        lines = list(self.cart.values())
        for r in range(self.cart_table.rowCount()):
            for c in range(self.cart_table.columnCount()):
                w = self.cart_table.cellWidget(r, c)
                if w:
                    self.cart_table.removeCellWidget(r, c)
                    w.setParent(None)
                    w.deleteLater()
        self.cart_table.clearContents()
        self.cart_table.setRowCount(len(lines))

        for row_idx, line in enumerate(lines):
            item_cell = QTableWidgetItem(
                f"{line.name}\n{format_minor(line.unit_price_minor, self.config.currency)} each"
            )
            item_cell.setToolTip(line.name)
            self.cart_table.setItem(row_idx, 0, item_cell)

            qty_widget = CartQtyWidget(line.quantity)
            qty_widget.decrement_clicked.connect(
                lambda pid=line.product_id: self.update_cart_quantity(pid, -1)
            )
            qty_widget.increment_clicked.connect(
                lambda pid=line.product_id: self.update_cart_quantity(pid, 1)
            )
            self.cart_table.setCellWidget(row_idx, 1, qty_widget)

            line_total_item = QTableWidgetItem(
                format_minor(line.line_total_minor, self.config.currency)
            )
            line_total_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.cart_table.setItem(row_idx, 2, line_total_item)

            remove_wrap = QWidget()
            remove_layout = QHBoxLayout(remove_wrap)
            remove_layout.setContentsMargins(4, 4, 4, 4)
            remove_layout.setAlignment(Qt.AlignCenter)
            remove_btn = QPushButton("×")
            remove_btn.setProperty("class", "RemoveRowBtn")
            remove_btn.setProperty("role", "danger")
            remove_btn.setToolTip(f"Remove {line.name}")
            remove_btn.setFixedSize(28, 28)
            remove_btn.clicked.connect(lambda _c=False, pid=line.product_id: self.remove_cart_item(pid))
            remove_layout.addWidget(remove_btn)
            self.cart_table.setCellWidget(row_idx, 3, remove_wrap)

            self.cart_table.setRowHeight(row_idx, 52)

        item_count, total_minor = self.cart_totals_minor()
        self.item_count_lbl.setText(f"{item_count} {'item' if item_count == 1 else 'items'}")
        formatted_total = format_minor(total_minor, self.config.currency)
        self.subtotal_lbl.setText(formatted_total)
        self.total_lbl.setText(formatted_total)

        has_items = item_count > 0
        self.clear_cart_btn.setEnabled(has_items)
        self.cash_input.setEnabled(has_items)
        if not has_items:
            self.cash_input.clear()

        self._rebuild_quick_cash_buttons(total_minor, has_items)
        self._evaluate_payment_state()

    def _rebuild_quick_cash_buttons(self, total_minor: int, enabled: bool) -> None:
        while self.quick_cash_layout.count():
            item = self.quick_cash_layout.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()

        if not enabled or total_minor <= 0:
            self.quick_cash_layout.addStretch(1)
            return

        spec = get_currency(self.config.currency)
        factor = spec.minor_factor
        candidates = [total_minor]
        steps = [5 * factor, 10 * factor, 20 * factor, 50 * factor] if factor == 100 else [500, 1000, 2000, 5000]
        for step in steps:
            rounded = ((total_minor + step - 1) // step) * step
            if rounded > total_minor and rounded not in candidates:
                candidates.append(rounded)

        for idx, amount in enumerate(sorted(candidates)[:4]):
            label = "Exact" if idx == 0 else format_minor(amount, self.config.currency)
            btn = QPushButton(label)
            btn.setProperty("class", "QuickCashBtn")
            btn.clicked.connect(
                lambda _c=False, amt=amount: self.cash_input.setText(
                    format_major_input(amt, self.config.currency)
                )
            )
            self.quick_cash_layout.addWidget(btn)
        self.quick_cash_layout.addStretch(1)

    def _parse_cash_received(self) -> int | None:
        raw = self.cash_input.text().strip()
        if not raw:
            return None
        try:
            return parse_major_to_minor(raw, self.config.currency)
        except ValueError:
            return -1

    def _evaluate_payment_state(self) -> None:
        item_count, total_minor = self.cart_totals_minor()
        if item_count == 0:
            self.change_title_lbl.setText("Change Due")
            self.change_val_lbl.setProperty("insufficient", "false")
            self.change_val_lbl.setText(format_minor(0, self.config.currency))
            self.change_val_lbl.style().unpolish(self.change_val_lbl)
            self.change_val_lbl.style().polish(self.change_val_lbl)
            self.checkout_btn.setEnabled(False)
            return

        received_minor = self._parse_cash_received()
        if received_minor is None:
            self.change_title_lbl.setText("Change Due")
            self.change_val_lbl.setProperty("insufficient", "false")
            self.change_val_lbl.setText("Enter cash amount")
            self.checkout_btn.setEnabled(False)
        elif received_minor < 0:
            self.change_title_lbl.setText("Payment Error")
            self.change_val_lbl.setProperty("insufficient", "true")
            self.change_val_lbl.setText("Invalid amount")
            self.checkout_btn.setEnabled(False)
        elif received_minor < total_minor:
            short_by = total_minor - received_minor
            self.change_title_lbl.setText("Insufficient Cash")
            self.change_val_lbl.setProperty("insufficient", "true")
            self.change_val_lbl.setText(f"Short {format_minor(short_by, self.config.currency)}")
            self.checkout_btn.setEnabled(False)
        else:
            change_minor = received_minor - total_minor
            self.change_title_lbl.setText("Change Due")
            self.change_val_lbl.setProperty("insufficient", "false")
            self.change_val_lbl.setText(format_minor(change_minor, self.config.currency))
            self.checkout_btn.setEnabled(True)

        self.change_val_lbl.style().unpolish(self.change_val_lbl)
        self.change_val_lbl.style().polish(self.change_val_lbl)

    def trigger_checkout(self) -> None:
        self._attempt_checkout()

    def _attempt_checkout(self) -> None:
        item_count, total_minor = self.cart_totals_minor()
        if item_count == 0:
            return

        received_minor = self._parse_cash_received()
        if received_minor is None or received_minor < 0:
            QMessageBox.warning(self, "Invalid Cash Amount", "Please enter a valid cash amount received.")
            self.cash_input.setFocus()
            return
        if received_minor < total_minor:
            short = format_minor(total_minor - received_minor, self.config.currency)
            QMessageBox.warning(
                self,
                "Insufficient Cash",
                f"Cash received is short by {short}. Please enter at least {format_minor(total_minor, self.config.currency)}.",
            )
            self.cash_input.setFocus()
            return

        try:
            order = self.db.create_order(list(self.cart.values()), received_minor)
        except Exception as exc:
            QMessageBox.critical(self, "Checkout Error", f"Failed to record sale:\n{exc}")
            return

        self.cart.clear()
        self.cash_input.clear()
        self.refresh_cart()
        self.status_message.emit(
            f"Completed {order.order_number} — Total {format_minor(order.total_minor, order.currency)}"
        )
        self.order_completed.emit(order)

