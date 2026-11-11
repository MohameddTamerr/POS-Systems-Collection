from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..config import BusinessConfig
from ..db import Database, OrderRecord
from ..theme import apply_theme, scaled_logo_pixmap
from .products_view import ProductsView
from .receipt_dialog import ReceiptDialog
from .register_view import RegisterView
from .sales_view import SalesView
from .settings_view import SettingsView
from .setup_dialog import SetupDialog


class MainWindow(QMainWindow):
    """Primary desktop window for Café POS."""

    def __init__(self, db: Database, app: QApplication) -> None:
        super().__init__()
        self.db = db
        self.app = app
        self.config: BusinessConfig = self.db.get_settings()

        self.resize(1200, 760)
        self.setMinimumSize(940, 620)

        self._build_ui()
        self._register_shortcuts()
        self._apply_branding()

    def _build_ui(self) -> None:
        central = QWidget()
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Top Native Desktop Navigation Bar
        nav_bar = QFrame()
        nav_bar.setObjectName("TopNavBar")
        nav_bar.setFixedHeight(58)
        nav_layout = QHBoxLayout(nav_bar)
        nav_layout.setContentsMargins(16, 8, 16, 8)
        nav_layout.setSpacing(12)

        self.logo_label = QLabel()
        self.logo_label.setFixedSize(36, 36)
        self.logo_label.setAlignment(Qt.AlignCenter)

        brand_text_col = QVBoxLayout()
        brand_text_col.setSpacing(0)
        self.brand_title_lbl = QLabel()
        self.brand_title_lbl.setObjectName("BrandTitleLabel")
        self.brand_sub_lbl = QLabel()
        self.brand_sub_lbl.setObjectName("BrandSubLabel")
        brand_text_col.addWidget(self.brand_title_lbl)
        brand_text_col.addWidget(self.brand_sub_lbl)

        nav_layout.addWidget(self.logo_label)
        nav_layout.addLayout(brand_text_col)
        nav_layout.addStretch(1)

        self.nav_buttons: list[QPushButton] = []
        nav_specs = [
            ("Register (F1)", 0),
            ("Products (F2)", 1),
            ("Sales History (F3)", 2),
            ("Settings (F4)", 3),
        ]
        for label, index in nav_specs:
            btn = QPushButton(label)
            btn.setProperty("class", "NavTabButton")
            btn.clicked.connect(lambda _c=False, idx=index: self.switch_view(idx))
            self.nav_buttons.append(btn)
            nav_layout.addWidget(btn)

        root_layout.addWidget(nav_bar)

        # Stacked Views
        self.stack = QStackedWidget()
        self.register_view = RegisterView(self.db, self.config)
        self.products_view = ProductsView(self.db, self.config)
        self.sales_view = SalesView(self.db, self.config)
        self.settings_view = SettingsView(self.db, self.config)

        self.stack.addWidget(self.register_view)
        self.stack.addWidget(self.products_view)
        self.stack.addWidget(self.sales_view)
        self.stack.addWidget(self.settings_view)
        root_layout.addWidget(self.stack, 1)

        self.setCentralWidget(central)
        self.statusBar().showMessage(f"Ready · Database: {self.db.db_path}")

        # Wire Signals
        self.register_view.order_completed.connect(self._on_order_completed)
        self.register_view.status_message.connect(self._show_status)

        self.products_view.products_changed.connect(self._on_products_changed)
        self.products_view.status_message.connect(self._show_status)

        self.sales_view.status_message.connect(self._show_status)

        self.settings_view.settings_saved.connect(self._on_settings_saved)
        self.settings_view.data_restored.connect(self._on_data_restored)
        self.settings_view.reopen_wizard_requested.connect(
            lambda: self.run_setup_wizard(is_first_launch=False)
        )
        self.settings_view.status_message.connect(self._show_status)

        self.switch_view(0)

    def _register_shortcuts(self) -> None:
        QShortcut(QKeySequence("F1"), self).activated.connect(lambda: self.switch_view(0))
        QShortcut(QKeySequence("F2"), self).activated.connect(lambda: self.switch_view(1))
        QShortcut(QKeySequence("F3"), self).activated.connect(lambda: self.switch_view(2))
        QShortcut(QKeySequence("F4"), self).activated.connect(lambda: self.switch_view(3))
        QShortcut(QKeySequence("F12"), self).activated.connect(self._on_f12_shortcut)

        find_action = QAction(self)
        find_action.setShortcut(QKeySequence.Find)
        find_action.triggered.connect(self._focus_active_search)
        self.addAction(find_action)

    def _focus_active_search(self) -> None:
        idx = self.stack.currentIndex()
        if idx == 0:
            self.register_view.focus_search()
        elif idx == 1:
            self.products_view.focus_search()
        elif idx == 2:
            self.sales_view.focus_search()

    def _on_f12_shortcut(self) -> None:
        if self.stack.currentIndex() == 0:
            self.register_view.trigger_checkout()

    def switch_view(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        for i, btn in enumerate(self.nav_buttons):
            btn.setProperty("active", "true" if i == index else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

        if index == 1:
            self.products_view.refresh()
        elif index == 2:
            self.sales_view.refresh()
        elif index == 3:
            self.settings_view.sync_from_config(self.config)

    def _apply_branding(self) -> None:
        apply_theme(self.app, self.config)
        self.setWindowTitle(f"{self.config.business_name} — Café POS")
        self.brand_title_lbl.setText(self.config.business_name)
        self.brand_sub_lbl.setText(f"Café POS · {self.config.currency}")
        self.logo_label.setPixmap(scaled_logo_pixmap(self.config, 36, 36))

        self.register_view.set_config(self.config)
        self.products_view.set_config(self.config)
        self.sales_view.set_config(self.config)
        self.settings_view.sync_from_config(self.config)

    def _show_status(self, message: str) -> None:
        self.statusBar().showMessage(message, 5000)

    def _on_products_changed(self) -> None:
        self.register_view.refresh_catalog()
        self.register_view.sync_cart_with_catalog()

    def _on_order_completed(self, order: OrderRecord) -> None:
        self.sales_view.refresh()
        dlg = ReceiptDialog(order, self.config, parent=self)
        dlg.exec()

    def _on_settings_saved(self, new_config: BusinessConfig) -> None:
        self.config = new_config
        self._apply_branding()

    def _on_data_restored(self) -> None:
        self.config = self.db.get_settings()
        self.register_view.cart.clear()
        self._apply_branding()
        self.products_view.refresh()
        self.sales_view.refresh()

    def run_setup_wizard(self, is_first_launch: bool = True) -> None:
        dlg = SetupDialog(self.config, is_first_launch=is_first_launch, parent=self)
        if dlg.exec() == QDialog.Accepted and dlg.result_config is not None:
            self.config = self.db.save_settings(dlg.result_config)
            self._apply_branding()
            self._show_status(f"Business profile saved: {self.config.business_name}")
        elif is_first_launch and not self.config.setup_completed:
            # User closed dialog via window X button on first launch: mark demo defaults ready
            fallback = BusinessConfig(
                business_name=self.config.business_name,
                accent_color=self.config.accent_color,
                currency=self.config.currency,
                address=self.config.address,
                phone=self.config.phone,
                logo_blob=self.config.logo_blob,
                logo_ext=self.config.logo_ext,
                setup_completed=True,
            )
            self.config = self.db.save_settings(fallback)
            self._apply_branding()

