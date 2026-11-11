from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


class ProductTileButton(QPushButton):
    """Crisp commercial terminal product button showing name, category, and price."""

    def __init__(
        self,
        product_id: int,
        name: str,
        category: str,
        price_text: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.product_id = product_id
        self.setProperty("class", "ProductTile")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumWidth(0)
        self.setMinimumHeight(88)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAccessibleName(f"{name}, {category}, {price_text}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(11, 9, 11, 9)
        layout.setSpacing(4)

        cat_label = QLabel(category)
        cat_label.setStyleSheet("font-size: 11px; color: #6B655D; font-weight: 600;")
        cat_label.setAttribute(Qt.WA_TransparentForMouseEvents)

        name_label = QLabel(name)
        name_label.setWordWrap(True)
        name_label.setStyleSheet("font-size: 13px; color: #1F1D1A; font-weight: 700;")
        name_label.setAttribute(Qt.WA_TransparentForMouseEvents)

        price_label = QLabel(price_text)
        price_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        price_label.setProperty("role", "price")
        price_label.setStyleSheet("font-size: 13px; font-weight: 700;")
        price_label.setAttribute(Qt.WA_TransparentForMouseEvents)

        layout.addWidget(cat_label)
        layout.addWidget(name_label, 1)
        layout.addWidget(price_label)


class CartQtyWidget(QWidget):
    """Compact inline quantity stepper for order table rows."""

    decrement_clicked = Signal()
    increment_clicked = Signal()

    def __init__(self, quantity: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(4)
        layout.setAlignment(Qt.AlignCenter)

        dec_btn = QPushButton("-")
        dec_btn.setProperty("class", "QtyMiniBtn")
        dec_btn.setToolTip("Decrease quantity")
        dec_btn.clicked.connect(self.decrement_clicked.emit)

        qty_lbl = QLabel(str(quantity))
        qty_lbl.setAlignment(Qt.AlignCenter)
        qty_lbl.setMinimumWidth(22)
        qty_lbl.setStyleSheet("font-weight: 700;")

        inc_btn = QPushButton("+")
        inc_btn.setProperty("class", "QtyMiniBtn")
        inc_btn.setToolTip("Increase quantity")
        inc_btn.clicked.connect(self.increment_clicked.emit)

        layout.addWidget(dec_btn)
        layout.addWidget(qty_lbl)
        layout.addWidget(inc_btn)


def make_divider() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setFrameShadow(QFrame.Plain)
    line.setStyleSheet("color: #DDD7CD;")
    return line
