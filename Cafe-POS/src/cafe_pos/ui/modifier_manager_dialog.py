from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from ..auth import AuthorizationError
from ..bilingual import suggest_bilingual_name
from ..config import BusinessConfig
from ..db import Database, MODIFIER_MODE_MULTI, MODIFIER_MODE_SINGLE, ModifierGroup, Product
from ..i18n import layout_direction, tr, tr_count
from ..icons import get_svg_icon
from ..money import format_major_input, get_currency, parse_major_to_minor
from ..theme import contrast_text_hex, get_tokens
from .animations import attach_button_feedback
from .inventory_view import OptionRecipeDialog


class _AssignedProductDelegate(QStyledItemDelegate):
    """Paints self-contained Boutique product rows with crisp green checkmarks (immune to theme/palette black boxes)."""

    _CHECK_GREEN = "#2E6B45"
    _CHECK_GREEN_BORDER = "#7FB092"
    _CHECK_GREEN_BG = "#EAF4EC"
    _CHECK_GREEN_TEXT = "#1D4D30"

    def __init__(self, accent_color: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._accent_color = accent_color

    def set_accent_color(self, accent_color: str) -> None:
        self._accent_color = accent_color

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        t = get_tokens(self._accent_color)
        raw_state = index.data(Qt.CheckStateRole)
        checked = raw_state in (Qt.Checked, Qt.Checked.value)
        hovered = bool(option.state & QStyle.State_MouseOver)
        focused = bool(option.state & QStyle.State_HasFocus)

        pill_rect = QRectF(option.rect).adjusted(1.5, 1.5, -1.5, -1.5)
        if checked:
            bg = QColor(self._CHECK_GREEN_BG)
            border = QColor(self._CHECK_GREEN)
            border_w = 1.5
        elif hovered:
            bg = QColor(t.surface_alt)
            border = QColor(self._CHECK_GREEN_BORDER)
            border_w = 1.2
        else:
            bg = QColor(t.surface)
            border = QColor(t.border_strong if focused else t.border)
            border_w = 1.1 if focused else 1.0

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(QPen(border, border_w))
        painter.setBrush(bg)
        painter.drawRoundedRect(pill_rect, 9.0, 9.0)

        # Draw 18x18 checkbox box directly (never relies on Fusion/QSS image URLs)
        rtl = option.direction == Qt.RightToLeft
        box_size = 18.0
        box_y = pill_rect.center().y() - box_size / 2.0
        if rtl:
            box_x = pill_rect.right() - 12.0 - box_size
        else:
            box_x = pill_rect.left() + 12.0
        box_rect = QRectF(box_x, box_y, box_size, box_size)

        if checked:
            painter.setPen(QPen(QColor(self._CHECK_GREEN), 1.5))
            painter.setBrush(QColor(self._CHECK_GREEN))
            painter.drawRoundedRect(box_rect, 5.0, 5.0)

            check_path = QPainterPath()
            check_path.moveTo(QPointF(box_x + 4.3, box_y + 9.4))
            check_path.lineTo(QPointF(box_x + 7.7, box_y + 12.8))
            check_path.lineTo(QPointF(box_x + 13.8, box_y + 5.6))
            check_pen = QPen(QColor("#FFFFFF"), 2.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            painter.setPen(check_pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(check_path)
        else:
            painter.setPen(
                QPen(
                    QColor(self._CHECK_GREEN if hovered else t.border_strong),
                    1.5,
                )
            )
            painter.setBrush(QColor("#FFFFFF"))
            painter.drawRoundedRect(box_rect, 5.0, 5.0)

        # Draw product label with clean typography and alignment
        font = option.font
        font.setBold(checked)
        painter.setFont(font)
        painter.setPen(QColor(self._CHECK_GREEN_TEXT if checked else t.text))

        if rtl:
            text_rect = QRectF(
                pill_rect.left() + 12.0,
                pill_rect.top(),
                max(20.0, (box_x - 10.0) - (pill_rect.left() + 12.0)),
                pill_rect.height(),
            )
            align = Qt.AlignRight | Qt.AlignVCenter
        else:
            text_rect = QRectF(
                box_x + box_size + 10.0,
                pill_rect.top(),
                max(20.0, (pill_rect.right() - 12.0) - (box_x + box_size + 10.0)),
                pill_rect.height(),
            )
            align = Qt.AlignLeft | Qt.AlignVCenter

        label = str(index.data(Qt.DisplayRole) or "")
        elided = painter.fontMetrics().elidedText(label, Qt.ElideRight, int(text_rect.width()))
        painter.drawText(text_rect, align, elided)
        painter.restore()


class AssignedProductsListWidget(QListWidget):
    """Checkable product list where clicking anywhere on a row toggles its assignment checkbox."""

    def __init__(self, accent_color: str = "#2E6B45", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pressed_item: QListWidgetItem | None = None
        self._pressed_state: Qt.CheckState | None = None
        self._delegate = _AssignedProductDelegate(accent_color, self)
        self.setItemDelegate(self._delegate)
        self.setObjectName("AssignedProductsList")
        self.setSelectionMode(QAbstractItemView.NoSelection)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setAlternatingRowColors(False)
        self.setSpacing(3)
        self.setUniformItemSizes(True)
        self.viewport().setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            item = self.itemAt(event.position().toPoint())
            self._pressed_item = item
            self._pressed_state = item.checkState() if item is not None else None
            if item is not None:
                self.setCurrentItem(item)
        else:
            self._pressed_item = None
            self._pressed_state = None
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        self.mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        pressed_item = self._pressed_item
        pressed_state = self._pressed_state
        self._pressed_item = None
        self._pressed_state = None
        super().mouseReleaseEvent(event)
        if event.button() != Qt.LeftButton or pressed_item is None or pressed_state is None:
            return
        released_item = self.itemAt(event.position().toPoint())
        if released_item is not pressed_item:
            return
        flags = pressed_item.flags()
        if not (flags & Qt.ItemIsUserCheckable) or not (flags & Qt.ItemIsEnabled):
            return
        # Clicking directly inside Qt's check indicator already toggled checkState in super();
        # clicking anywhere else on the row leaves checkState unchanged, so toggle it now.
        if pressed_item.checkState() == pressed_state:
            pressed_item.setCheckState(
                Qt.Unchecked if pressed_state == Qt.Checked else Qt.Checked
            )


class _OptionValidationError(ValueError):
    def __init__(self, message: str, focus_widget: QWidget | None = None) -> None:
        super().__init__(message)
        self.focus_widget = focus_widget


class ModifierManagerDialog(QDialog):
    """Admin dialog to create, edit, disable, archive, and assign reusable modifier groups."""

    modifiers_mutated = Signal()

    def __init__(
        self,
        db: Database,
        config: BusinessConfig,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.db = db
        self.config = config
        self._groups: list[ModifierGroup] = []
        self._products: list[Product] = []
        self._selected_group_id: int | None = None
        self._is_creating_new: bool = False

        self.setModal(True)
        self.setLayoutDirection(layout_direction())
        self.setWindowTitle(tr("modifiers.title"))
        self.resize(1000, 720)
        self.setMinimumSize(820, 560)

        self._build_ui()
        self._load_data()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        # Header
        hdr_row = QHBoxLayout()
        hdr_text = QVBoxLayout()
        hdr_text.setSpacing(2)
        self.title_lbl = QLabel(tr("modifiers.title"))
        self.title_lbl.setProperty("class", "PageHeading")
        self.sub_lbl = QLabel(tr("modifiers.subtitle"))
        self.sub_lbl.setProperty("class", "MutedLabel")
        self.sub_lbl.setWordWrap(True)
        hdr_text.addWidget(self.title_lbl)
        hdr_text.addWidget(self.sub_lbl)
        hdr_row.addLayout(hdr_text, 1)

        self.show_archived_chk = QCheckBox(tr("modifiers.show_archived"))
        self.show_archived_chk.toggled.connect(self._load_data)
        hdr_row.addWidget(self.show_archived_chk)
        root.addLayout(hdr_row)

        # Master-detail columns
        cols = QHBoxLayout()
        cols.setSpacing(12)

        # Left pane: Groups list
        left_card = QFrame()
        left_card.setProperty("class", "BoutiqueCard")
        left_card.setMinimumWidth(265)
        left_card.setMaximumWidth(315)
        left_lay = QVBoxLayout(left_card)
        left_lay.setContentsMargins(12, 12, 12, 12)
        left_lay.setSpacing(8)

        left_top = QHBoxLayout()
        self.group_count_badge = QLabel(tr_count("group", 0))
        self.group_count_badge.setProperty("class", "CountBadge")
        self.new_group_btn = QPushButton(tr("modifiers.new_group_btn"))
        self.new_group_btn.setProperty("role", "primary")
        self.new_group_btn.setIcon(
            get_svg_icon("plus", contrast_text_hex(self.config.accent_color), 13)
        )
        self.new_group_btn.setIconSize(QSize(13, 13))
        self.new_group_btn.clicked.connect(self._start_new_group)
        attach_button_feedback(self.new_group_btn)
        left_top.addWidget(self.group_count_badge)
        left_top.addStretch(1)
        left_top.addWidget(self.new_group_btn)
        left_lay.addLayout(left_top)

        self.group_list = QListWidget()
        self.group_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.group_list.setWordWrap(True)
        self.group_list.setSpacing(3)
        self.group_list.currentRowChanged.connect(self._on_group_selected)
        left_lay.addWidget(self.group_list, 1)
        cols.addWidget(left_card)

        # Right pane: Direct card layout so Options and Assigned Products are always visible
        right_card = QFrame()
        right_card.setProperty("class", "BoutiqueCard")
        right_outer = QVBoxLayout(right_card)
        right_outer.setContentsMargins(0, 0, 0, 0)
        right_outer.setSpacing(0)

        host = QWidget()
        host.setStyleSheet("background: transparent;")
        form_lay = QVBoxLayout(host)
        form_lay.setContentsMargins(16, 12, 16, 8)
        form_lay.setSpacing(8)

        rule_hdr = QHBoxLayout()
        self.details_hdr = QLabel(tr("modifiers.group_details"))
        self.details_hdr.setProperty("class", "SectionHeader")
        self.status_badge = QLabel(tr("modifiers.status_active"))
        self.status_badge.setProperty("class", "PrimaryBadge")
        rule_hdr.addWidget(self.details_hdr)
        rule_hdr.addStretch(1)
        rule_hdr.addWidget(self.status_badge)
        form_lay.addLayout(rule_hdr)

        names_row = QHBoxLayout()
        names_row.setSpacing(10)
        en_col = QVBoxLayout()
        en_col.setSpacing(3)
        en_lbl = QLabel(tr("modifiers.name_en_label"))
        en_lbl.setStyleSheet("font-weight: 600;")
        self.name_en_edit = QLineEdit()
        self.name_en_edit.setPlaceholderText(tr("modifiers.name_en_ph"))
        self.name_en_edit.setMaxLength(60)
        self.name_en_edit.textChanged.connect(self._clear_validation_error)
        self.name_en_edit.editingFinished.connect(self._auto_suggest_group_ar)
        en_col.addWidget(en_lbl)
        en_col.addWidget(self.name_en_edit)

        ar_col = QVBoxLayout()
        ar_col.setSpacing(3)
        ar_lbl = QLabel(tr("modifiers.name_ar_label"))
        ar_lbl.setStyleSheet("font-weight: 600;")
        self.name_ar_edit = QLineEdit()
        self.name_ar_edit.setPlaceholderText(tr("modifiers.name_ar_ph"))
        self.name_ar_edit.setLayoutDirection(Qt.RightToLeft)
        self.name_ar_edit.setMaxLength(60)
        self.name_ar_edit.textChanged.connect(self._clear_validation_error)
        ar_col.addWidget(ar_lbl)
        ar_col.addWidget(self.name_ar_edit)

        names_row.addLayout(en_col, 1)
        names_row.addLayout(ar_col, 1)
        form_lay.addLayout(names_row)

        rules_row = QHBoxLayout()
        rules_row.setSpacing(10)
        mode_col = QVBoxLayout()
        mode_col.setSpacing(3)
        mode_lbl = QLabel(tr("modifiers.mode_label"))
        mode_lbl.setStyleSheet("font-weight: 600;")
        self.mode_combo = QComboBox()
        self.mode_combo.addItem(tr("modifiers.mode_single"), MODIFIER_MODE_SINGLE)
        self.mode_combo.addItem(tr("modifiers.mode_multi"), MODIFIER_MODE_MULTI)
        self.mode_combo.currentIndexChanged.connect(self._sync_rule_controls)
        mode_col.addWidget(mode_lbl)
        mode_col.addWidget(self.mode_combo)

        min_col = QVBoxLayout()
        min_col.setSpacing(3)
        min_lbl = QLabel(tr("modifiers.min_label"))
        min_lbl.setStyleSheet("font-weight: 600;")
        self.min_spin = QSpinBox()
        self.min_spin.setRange(0, 20)
        self.min_spin.valueChanged.connect(self._clear_validation_error)
        min_col.addWidget(min_lbl)
        min_col.addWidget(self.min_spin)

        max_col = QVBoxLayout()
        max_col.setSpacing(3)
        max_lbl = QLabel(tr("modifiers.max_label"))
        max_lbl.setStyleSheet("font-weight: 600;")
        self.max_spin = QSpinBox()
        self.max_spin.setRange(0, 20)
        self.max_spin.setValue(1)
        self.max_spin.valueChanged.connect(self._clear_validation_error)
        max_col.addWidget(max_lbl)
        max_col.addWidget(self.max_spin)

        rules_row.addLayout(mode_col, 2)
        rules_row.addLayout(min_col, 1)
        rules_row.addLayout(max_col, 1)
        form_lay.addLayout(rules_row)

        checks_row = QHBoxLayout()
        checks_row.setSpacing(16)
        self.required_chk = QCheckBox(tr("modifiers.required_chk"))
        self.required_chk.toggled.connect(self._sync_rule_controls)
        self.active_chk = QCheckBox(tr("modifiers.active_chk"))
        self.active_chk.setChecked(True)
        checks_row.addWidget(self.required_chk)
        checks_row.addWidget(self.active_chk)
        checks_row.addStretch(1)
        form_lay.addLayout(checks_row)

        # Options Table
        opt_hdr_row = QHBoxLayout()
        opt_hdr = QLabel(tr("modifiers.options_hdr"))
        opt_hdr.setProperty("class", "SectionHeader")
        self.add_opt_btn = QPushButton(tr("modifiers.add_option_btn"))
        self.add_opt_btn.setProperty("role", "secondary")
        self.add_opt_btn.setIcon(get_svg_icon("plus", self.config.accent_color, 13))
        self.add_opt_btn.setIconSize(QSize(13, 13))
        self.add_opt_btn.clicked.connect(lambda: self._append_option_row())
        attach_button_feedback(self.add_opt_btn)
        opt_hdr_row.addWidget(opt_hdr)
        opt_hdr_row.addStretch(1)
        opt_hdr_row.addWidget(self.add_opt_btn)
        form_lay.addLayout(opt_hdr_row)

        spec = get_currency(self.config.currency)
        self.options_table = QTableWidget(0, 5)
        self.options_table.setMinimumHeight(118)
        self.options_table.verticalHeader().setVisible(False)
        self.options_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.options_table.setHorizontalHeaderLabels(
            [
                tr("modifiers.col.opt_en"),
                tr("modifiers.col.opt_ar"),
                tr("modifiers.col.price_delta", currency=spec.code),
                tr("modifiers.col.active"),
                tr("modifiers.col.actions"),
            ]
        )
        oh = self.options_table.horizontalHeader()
        oh.setSectionResizeMode(0, QHeaderView.Stretch)
        oh.setSectionResizeMode(1, QHeaderView.Stretch)
        oh.setSectionResizeMode(2, QHeaderView.Fixed)
        oh.setSectionResizeMode(3, QHeaderView.Fixed)
        oh.setSectionResizeMode(4, QHeaderView.Fixed)
        self.options_table.setColumnWidth(2, 138)
        self.options_table.setColumnWidth(3, 76)
        self.options_table.setColumnWidth(4, 130)
        form_lay.addWidget(self.options_table, 1)

        # Assigned Products
        assign_hdr_row = QHBoxLayout()
        assign_hdr_row.setSpacing(8)
        assign_hdr = QLabel(tr("modifiers.assign_hdr"))
        assign_hdr.setProperty("class", "SectionHeader")
        self.assigned_count_badge = QLabel(tr("modifiers.assigned_count", count=0, total=0))
        self.assigned_count_badge.setProperty("class", "CountBadge")

        self.select_all_products_btn = QPushButton(tr("modifiers.select_all_btn"))
        self.select_all_products_btn.setStyleSheet("padding: 3px 10px; font-size: 11.5px;")
        self.select_all_products_btn.setCursor(Qt.PointingHandCursor)
        self.select_all_products_btn.clicked.connect(lambda: self._set_all_products_checked(True))
        attach_button_feedback(self.select_all_products_btn)

        self.clear_products_btn = QPushButton(tr("modifiers.clear_assign_btn"))
        self.clear_products_btn.setStyleSheet("padding: 3px 10px; font-size: 11.5px;")
        self.clear_products_btn.setCursor(Qt.PointingHandCursor)
        self.clear_products_btn.clicked.connect(lambda: self._set_all_products_checked(False))
        attach_button_feedback(self.clear_products_btn)

        assign_hdr_row.addWidget(assign_hdr)
        assign_hdr_row.addWidget(self.assigned_count_badge)
        assign_hdr_row.addStretch(1)
        assign_hdr_row.addWidget(self.select_all_products_btn)
        assign_hdr_row.addWidget(self.clear_products_btn)

        assign_hint = QLabel(tr("modifiers.assign_hint"))
        assign_hint.setProperty("class", "MutedLabel")
        assign_hint.setWordWrap(True)
        form_lay.addLayout(assign_hdr_row)
        form_lay.addWidget(assign_hint)

        self.products_list = AssignedProductsListWidget(self.config.accent_color)
        self.products_list.setMinimumHeight(140)
        self.products_list.itemChanged.connect(self._on_product_item_changed)
        form_lay.addWidget(self.products_list, 1)

        right_outer.addWidget(host, 1)

        self.validation_lbl = QLabel("")
        self.validation_lbl.setProperty("class", "ValidationHint")
        self.validation_lbl.setWordWrap(True)
        self.validation_lbl.setContentsMargins(16, 4, 16, 0)
        self.validation_lbl.hide()
        right_outer.addWidget(self.validation_lbl)

        # Group action footer inside right card
        right_footer = QHBoxLayout()
        right_footer.setContentsMargins(16, 10, 16, 12)
        right_footer.setSpacing(8)
        self.archive_btn = QPushButton(tr("modifiers.archive_btn"))
        self.archive_btn.setProperty("role", "danger")
        self.archive_btn.setIcon(get_svg_icon("archive", "#B23A3A", 13))
        self.archive_btn.setIconSize(QSize(13, 13))
        self.archive_btn.clicked.connect(self._toggle_archive_group)
        attach_button_feedback(self.archive_btn)

        self.save_group_btn = QPushButton(tr("modifiers.save_group_btn"))
        self.save_group_btn.setProperty("role", "primary")
        self.save_group_btn.setIcon(
            get_svg_icon("check", contrast_text_hex(self.config.accent_color), 14)
        )
        self.save_group_btn.setIconSize(QSize(14, 14))
        self.save_group_btn.clicked.connect(self._save_current_group)
        attach_button_feedback(self.save_group_btn)

        right_footer.addWidget(self.archive_btn)
        right_footer.addStretch(1)
        right_footer.addWidget(self.save_group_btn)
        right_outer.addLayout(right_footer)

        cols.addWidget(right_card, 1)
        root.addLayout(cols, 1)

        # Bottom Close button
        bottom_row = QHBoxLayout()
        bottom_row.addStretch(1)
        self.close_btn = QPushButton(tr("common.close"))
        self.close_btn.clicked.connect(self.accept)
        attach_button_feedback(self.close_btn)
        bottom_row.addWidget(self.close_btn)
        root.addLayout(bottom_row)

    def _load_data(self, select_group_id: int | None = None) -> None:
        include_arch = self.show_archived_chk.isChecked()
        self._groups = self.db.list_modifier_groups(
            include_inactive=True, include_archived=include_arch
        )
        self._products = self.db.list_products(include_archived_categories=True)
        self.group_count_badge.setText(tr_count("group", len(self._groups)))

        target_id = select_group_id if select_group_id is not None else self._selected_group_id

        self.group_list.blockSignals(True)
        self.group_list.clear()
        target_row = -1
        for idx, g in enumerate(self._groups):
            status = (
                tr("modifiers.status_archived")
                if g.is_archived
                else (
                    tr("modifiers.status_active")
                    if g.is_active
                    else tr("modifiers.status_disabled")
                )
            )
            label = (
                f"{g.display_name() or g.name_en}  [{status}]\n"
                f"{tr_count('option', len(g.options))} · {tr_count('product', len(g.assigned_product_ids))}"
            )
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, g.id)
            self.group_list.addItem(item)
            if target_id is not None and g.id == target_id:
                target_row = idx
        self.group_list.blockSignals(False)

        if self._groups:
            if target_row < 0:
                target_row = 0
            self._is_creating_new = False
            self.group_list.blockSignals(True)
            self.group_list.setCurrentRow(target_row)
            self.group_list.blockSignals(False)
            self._populate_form(self._groups[target_row])
        else:
            self._start_new_group()

    def _on_group_selected(self, row: int) -> None:
        if 0 <= row < len(self._groups):
            self._is_creating_new = False
            self._populate_form(self._groups[row])

    def _clear_validation_error(self, *_args: object) -> None:
        if hasattr(self, "validation_lbl"):
            self.validation_lbl.clear()
            self.validation_lbl.hide()

    def _fail_validation(self, message: str, focus_widget: QWidget | None = None) -> None:
        self.validation_lbl.setText(message)
        self.validation_lbl.show()
        if focus_widget is not None:
            focus_widget.setFocus()
        QMessageBox.warning(self, tr("common.validation_error"), message)

    def _apply_product_item_visual(self, item: QListWidgetItem) -> None:
        t = get_tokens(self.config.accent_color)
        checked = item.checkState() == Qt.Checked
        font = item.font()
        font.setBold(checked)
        item.setFont(font)
        if checked:
            item.setBackground(QBrush(QColor(t.primary_tint)))
            item.setForeground(QBrush(QColor(t.primary)))
        else:
            item.setBackground(QBrush(QColor(t.surface)))
            item.setForeground(QBrush(QColor(t.text)))

    def _refresh_assigned_count_badge(self) -> None:
        total = self.products_list.count()
        checked = sum(
            1
            for i in range(total)
            if (it := self.products_list.item(i)) is not None
            and it.checkState() == Qt.Checked
        )
        self.assigned_count_badge.setText(
            tr("modifiers.assigned_count", count=checked, total=total)
        )
        badge_cls = "PrimaryBadge" if checked > 0 else "CountBadge"
        if self.assigned_count_badge.property("class") != badge_cls:
            self.assigned_count_badge.setProperty("class", badge_cls)
            self.assigned_count_badge.style().unpolish(self.assigned_count_badge)
            self.assigned_count_badge.style().polish(self.assigned_count_badge)
        self.select_all_products_btn.setEnabled(total > 0 and checked < total)
        self.clear_products_btn.setEnabled(checked > 0)

    def _on_product_item_changed(self, item: QListWidgetItem | None) -> None:
        self._clear_validation_error()
        if item is not None:
            self.products_list.blockSignals(True)
            self._apply_product_item_visual(item)
            self.products_list.blockSignals(False)
        self._refresh_assigned_count_badge()

    def _set_all_products_checked(self, checked: bool) -> None:
        target_state = Qt.Checked if checked else Qt.Unchecked
        self.products_list.blockSignals(True)
        for i in range(self.products_list.count()):
            item = self.products_list.item(i)
            if item is not None and (item.flags() & Qt.ItemIsUserCheckable):
                item.setCheckState(target_state)
                self._apply_product_item_visual(item)
        self.products_list.blockSignals(False)
        self._clear_validation_error()
        self._refresh_assigned_count_badge()

    def _populate_products_checklist(self, assigned_ids: set[int]) -> None:
        self.products_list.blockSignals(True)
        self.products_list.clear()
        for prod in self._products:
            item = QListWidgetItem(prod.display_name())
            item.setData(Qt.UserRole, prod.id)
            item.setFlags((item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsEnabled) & ~Qt.ItemIsAutoTristate)
            item.setSizeHint(QSize(0, 36))
            item.setCheckState(Qt.Checked if prod.id in assigned_ids else Qt.Unchecked)
            self._apply_product_item_visual(item)
            self.products_list.addItem(item)
        self.products_list.blockSignals(False)
        self._refresh_assigned_count_badge()

    def _start_new_group(self) -> None:
        self._clear_validation_error()
        self._is_creating_new = True
        self._selected_group_id = None
        self.group_list.blockSignals(True)
        self.group_list.setCurrentRow(-1)
        self.group_list.clearSelection()
        self.group_list.blockSignals(False)
        self.status_badge.setText(tr("modifiers.new_group_btn"))
        self.name_en_edit.clear()
        self.name_ar_edit.clear()
        self.mode_combo.setCurrentIndex(0)
        self.required_chk.setChecked(False)
        self.active_chk.setChecked(True)
        self.min_spin.setValue(0)
        self.max_spin.setValue(1)
        self._sync_rule_controls()

        self.options_table.setRowCount(0)
        self._append_option_row()
        self._populate_products_checklist(set())
        self.archive_btn.setVisible(False)
        self.name_en_edit.setFocus()

    def _populate_form(self, grp: ModifierGroup) -> None:
        self._clear_validation_error()
        self._selected_group_id = grp.id
        self.name_en_edit.setText(grp.name_en)
        self.name_ar_edit.setText(grp.name_ar)
        m_idx = 0 if grp.selection_mode == MODIFIER_MODE_SINGLE else 1
        self.mode_combo.blockSignals(True)
        self.mode_combo.setCurrentIndex(m_idx)
        self.mode_combo.blockSignals(False)

        self.required_chk.blockSignals(True)
        self.required_chk.setChecked(grp.is_required)
        self.required_chk.blockSignals(False)
        self.active_chk.setChecked(grp.is_active)
        self.min_spin.setValue(grp.min_select)
        self.max_spin.setValue(grp.max_select)
        self._sync_rule_controls()

        if grp.is_archived:
            self.status_badge.setText(tr("modifiers.status_archived"))
            self.archive_btn.setText(tr("modifiers.restore_btn"))
        else:
            self.status_badge.setText(
                tr("modifiers.status_active") if grp.is_active else tr("modifiers.status_disabled")
            )
            self.archive_btn.setText(tr("modifiers.archive_btn"))
        self.archive_btn.setVisible(True)

        self.options_table.setRowCount(0)
        for opt in grp.options:
            self._append_option_row(
                option_id=opt.id,
                name_en=opt.name_en,
                name_ar=opt.name_ar,
                delta_minor=opt.price_delta_minor,
                is_active=opt.is_active,
            )
        if not grp.options:
            self._append_option_row()

        self._populate_products_checklist(set(grp.assigned_product_ids))

    def _sync_rule_controls(self) -> None:
        is_single = (self.mode_combo.currentData() == MODIFIER_MODE_SINGLE)
        req = self.required_chk.isChecked()
        if is_single:
            self.max_spin.setValue(1)
            self.max_spin.setEnabled(False)
            self.min_spin.setValue(1 if req else 0)
            self.min_spin.setEnabled(False)
        else:
            self.max_spin.setEnabled(True)
            self.min_spin.setEnabled(True)
            if req and self.min_spin.value() < 1:
                self.min_spin.setValue(1)

    def _auto_suggest_group_ar(self) -> None:
        en_txt = self.name_en_edit.text().strip()
        if en_txt and not self.name_ar_edit.text().strip():
            sug = suggest_bilingual_name(
                en_txt,
                is_category=False,
                cache_lookup=self.db.get_cached_translation,
            )
            if sug.target_lang == "ar" and sug.target_text.strip():
                self.name_ar_edit.setText(sug.target_text.strip())

    def _append_option_row(
        self,
        *,
        option_id: int | None = None,
        name_en: str = "",
        name_ar: str = "",
        delta_minor: int = 0,
        is_active: bool = True,
    ) -> None:
        row = self.options_table.rowCount()
        self.options_table.insertRow(row)

        en_edit = QLineEdit(name_en)
        en_edit.setProperty("option_id", option_id)
        en_edit.setMaxLength(60)
        en_edit.textChanged.connect(self._clear_validation_error)
        ar_edit = QLineEdit(name_ar)
        ar_edit.setLayoutDirection(Qt.RightToLeft)
        ar_edit.setMaxLength(60)
        ar_edit.textChanged.connect(self._clear_validation_error)

        def _auto_ar(e_box=en_edit, a_box=ar_edit):
            txt = e_box.text().strip()
            if txt and not a_box.text().strip():
                sug = suggest_bilingual_name(
                    txt, is_category=False, cache_lookup=self.db.get_cached_translation
                )
                if sug.target_lang == "ar" and sug.target_text.strip():
                    a_box.setText(sug.target_text.strip())

        en_edit.editingFinished.connect(_auto_ar)

        price_edit = QLineEdit(format_major_input(delta_minor, self.config.currency))
        price_edit.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        price_edit.textChanged.connect(self._clear_validation_error)

        chk_wrap = QWidget()
        chk_lay = QHBoxLayout(chk_wrap)
        chk_lay.setContentsMargins(0, 0, 0, 0)
        chk_lay.setAlignment(Qt.AlignCenter)
        chk = QCheckBox()
        chk.setChecked(bool(is_active))
        chk.toggled.connect(self._clear_validation_error)
        chk_lay.addWidget(chk)

        rm_wrap = QWidget()
        rm_lay = QHBoxLayout(rm_wrap)
        rm_lay.setContentsMargins(2, 2, 2, 2)
        rm_lay.setSpacing(4)
        rm_lay.setAlignment(Qt.AlignCenter)

        staged_entries = (
            self.db.get_modifier_option_recipe(option_id)
            if option_id is not None
            else []
        )
        recipe_btn = QPushButton(
            tr("modifiers.option_recipe_btn")
            if not staged_entries
            else f"{tr('modifiers.option_recipe_btn')} ({len(staged_entries)})"
        )
        recipe_btn.setProperty("role", "secondary")
        recipe_btn.setStyleSheet("padding: 3px 7px; font-size: 11px;")
        recipe_btn.setEnabled(True)
        recipe_btn.setProperty("pending_recipe", list(staged_entries))
        recipe_btn.setToolTip(tr("modifiers.option_recipe_tip"))
        recipe_btn.clicked.connect(
            lambda _c=False, oid=option_id, rb=recipe_btn, e_box=en_edit, a_box=ar_edit: self._open_option_recipe(
                oid,
                e_box.text().strip() or a_box.text().strip(),
                recipe_btn=rb,
            )
        )
        rm_lay.addWidget(recipe_btn)

        rm_btn = QPushButton()
        rm_btn.setProperty("class", "RemoveRowBtn")
        rm_btn.setIcon(get_svg_icon("trash", "#B23A3A", 12))
        rm_btn.setIconSize(QSize(12, 12))
        rm_btn.clicked.connect(lambda _c=False, btn=rm_btn: self._remove_option_by_btn(btn))
        rm_lay.addWidget(rm_btn)

        self.options_table.setCellWidget(row, 0, en_edit)
        self.options_table.setCellWidget(row, 1, ar_edit)
        self.options_table.setCellWidget(row, 2, price_edit)
        self.options_table.setCellWidget(row, 3, chk_wrap)
        self.options_table.setCellWidget(row, 4, rm_wrap)
        self.options_table.setRowHeight(row, 38)

    def _open_option_recipe(
        self,
        option_id: int | None,
        opt_name: str,
        *,
        recipe_btn: QPushButton | None = None,
    ) -> None:
        initial = (
            list(recipe_btn.property("pending_recipe") or [])
            if recipe_btn is not None
            else []
        )
        dlg = OptionRecipeDialog(
            self.db,
            self.config,
            option_id,
            opt_name or tr("modifiers.new_group_btn"),
            parent=self,
            initial_entries=initial,
            defer_save=(option_id is None),
        )
        win = self.window()
        if hasattr(win, "clamp_dialog_to_screen"):
            win.clamp_dialog_to_screen(dlg)
        if dlg.exec() == QDialog.Accepted and recipe_btn is not None:
            entries = list(dlg.result_entries)
            recipe_btn.setProperty("pending_recipe", entries)
            recipe_btn.setText(
                tr("modifiers.option_recipe_btn")
                if not entries
                else f"{tr('modifiers.option_recipe_btn')} ({len(entries)})"
            )

    def _remove_option_by_btn(self, btn: QPushButton) -> None:
        for r in range(self.options_table.rowCount()):
            wrap = self.options_table.cellWidget(r, 4)
            if wrap is not None and btn in wrap.findChildren(QPushButton):
                self.options_table.removeRow(r)
                return

    def _collect_options(self) -> list[dict[str, object]]:
        opts: list[dict[str, object]] = []
        for r in range(self.options_table.rowCount()):
            en_w = self.options_table.cellWidget(r, 0)
            ar_w = self.options_table.cellWidget(r, 1)
            pr_w = self.options_table.cellWidget(r, 2)
            act_wrap = self.options_table.cellWidget(r, 3)
            if not isinstance(en_w, QLineEdit) or not isinstance(ar_w, QLineEdit) or not isinstance(pr_w, QLineEdit):
                continue
            o_en = en_w.text().strip()
            o_ar = ar_w.text().strip()
            act_cell = self.options_table.cellWidget(r, 4)
            pending_rec: list[tuple[int, int]] = []
            if act_cell is not None:
                for pb in act_cell.findChildren(QPushButton):
                    prop = pb.property("pending_recipe")
                    if isinstance(prop, list):
                        pending_rec = list(prop)
                        break

            raw_text = pr_w.text().strip()
            if not o_en and not o_ar:
                has_non_zero_price = False
                if raw_text:
                    try:
                        has_non_zero_price = (
                            parse_major_to_minor(raw_text, self.config.currency) != 0
                        )
                    except ValueError:
                        has_non_zero_price = True
                if has_non_zero_price or pending_rec:
                    raise _OptionValidationError(
                        tr("modifiers.err_opt_name_missing", row=r + 1),
                        en_w,
                    )
                continue

            raw_price = raw_text or "0"
            try:
                delta_minor = parse_major_to_minor(raw_price, self.config.currency)
            except ValueError as exc:
                raise _OptionValidationError(
                    tr("modifiers.err_opt_price_invalid", name=o_en or o_ar, row=r + 1),
                    pr_w,
                ) from exc
            if delta_minor < 0:
                raise _OptionValidationError(tr("backend.err.modifier_delta_neg"), pr_w)
            chk = act_wrap.findChild(QCheckBox) if act_wrap is not None else None
            opts.append(
                {
                    "id": en_w.property("option_id"),
                    "name_en": o_en,
                    "name_ar": o_ar,
                    "price_delta_minor": delta_minor,
                    "is_active": chk.isChecked() if chk is not None else True,
                    "display_order": (r + 1) * 10,
                    "_pending_recipe": pending_rec,
                }
            )
        return opts

    def _collect_assigned_product_ids(self) -> list[int]:
        pids: list[int] = []
        for i in range(self.products_list.count()):
            item = self.products_list.item(i)
            if item is not None and item.checkState() == Qt.Checked:
                pid = item.data(Qt.UserRole)
                if pid is not None:
                    pids.append(int(pid))
        return pids

    def _save_current_group(self) -> None:
        self._clear_validation_error()
        name_en = self.name_en_edit.text().strip()
        name_ar = self.name_ar_edit.text().strip()
        if not name_en and not name_ar:
            self._fail_validation(tr("modifiers.err_group_name"), self.name_en_edit)
            return

        mode = str(self.mode_combo.currentData() or MODIFIER_MODE_SINGLE)
        req = self.required_chk.isChecked()
        min_sel = self.min_spin.value()
        max_sel = self.max_spin.value()
        if mode == MODIFIER_MODE_MULTI and max_sel > 0 and min_sel > max_sel:
            self._fail_validation(tr("modifiers.err_minmax"), self.min_spin)
            return

        try:
            opts = self._collect_options()
        except _OptionValidationError as exc:
            self._fail_validation(str(exc), exc.focus_widget)
            return
        except ValueError as exc:
            self._fail_validation(str(exc))
            return

        if not opts:
            first_en = (
                self.options_table.cellWidget(0, 0)
                if self.options_table.rowCount() > 0
                else self.add_opt_btn
            )
            self._fail_validation(
                tr("modifiers.err_need_options"),
                first_en if isinstance(first_en, QWidget) else self.add_opt_btn,
            )
            return

        active_opt_count = sum(1 for o in opts if o.get("is_active", True))
        if req and active_opt_count < max(1, min_sel):
            self._fail_validation(tr("modifiers.err_required_min"))
            return
        if (not req) and mode == MODIFIER_MODE_MULTI and min_sel > active_opt_count:
            self._fail_validation(
                tr("modifiers.err_min_exceeds_options", min=min_sel, active=active_opt_count),
                self.min_spin,
            )
            return

        pids = self._collect_assigned_product_ids()
        pending_by_order: list[tuple[int, str, str, list[tuple[int, int]]]] = []
        clean_opts: list[dict[str, object]] = []
        for idx_o, od in enumerate(opts):
            copy_od = dict(od)
            p_rec = list(copy_od.pop("_pending_recipe", []) or [])
            if copy_od.get("id") is None and p_rec:
                pending_by_order.append(
                    (idx_o, str(copy_od.get("name_en") or ""), str(copy_od.get("name_ar") or ""), p_rec)
                )
            clean_opts.append(copy_od)

        try:
            if self._is_creating_new or self._selected_group_id is None:
                saved = self.db.create_modifier_group(
                    name_en,
                    name_ar=name_ar,
                    selection_mode=mode,
                    is_required=req,
                    min_select=min_sel,
                    max_select=max_sel,
                    is_active=self.active_chk.isChecked(),
                    options=clean_opts,
                    product_ids=pids,
                )
            else:
                saved = self.db.update_modifier_group(
                    self._selected_group_id,
                    name_en,
                    name_ar=name_ar,
                    selection_mode=mode,
                    is_required=req,
                    min_select=min_sel,
                    max_select=max_sel,
                    is_active=self.active_chk.isChecked(),
                    options=clean_opts,
                    product_ids=pids,
                )
        except (ValueError, AuthorizationError) as exc:
            self._fail_validation(str(exc))
            return

        if pending_by_order and saved.options:
            for idx_o, o_en, o_ar, p_rec in pending_by_order:
                target_opt = None
                if 0 <= idx_o < len(saved.options):
                    cand = saved.options[idx_o]
                    if (not o_en or cand.name_en == o_en) and (not o_ar or cand.name_ar == o_ar):
                        target_opt = cand
                if target_opt is None:
                    for cand in saved.options:
                        if cand.name_en == o_en and cand.name_ar == o_ar:
                            target_opt = cand
                            break
                if target_opt is not None:
                    try:
                        self.db.set_modifier_option_recipe(target_opt.id, p_rec)
                    except (ValueError, AuthorizationError):
                        pass

        self.modifiers_mutated.emit()
        self._load_data(select_group_id=saved.id)
        QMessageBox.information(
            self,
            tr("modifiers.title"),
            tr("modifiers.toast_saved", name=saved.display_name()),
        )

    def _toggle_archive_group(self) -> None:
        if self._selected_group_id is None:
            return
        grp = self.db.get_modifier_group(self._selected_group_id)
        if grp is None:
            return
        target_arch = not grp.is_archived
        try:
            updated = self.db.archive_modifier_group(grp.id, archived=target_arch)
        except (ValueError, AuthorizationError) as exc:
            QMessageBox.warning(self, tr("common.validation_error"), str(exc))
            return
        self.modifiers_mutated.emit()
        self._load_data(select_group_id=updated.id if not target_arch or self.show_archived_chk.isChecked() else None)

