from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..config import BusinessConfig
from ..db import (
    CartLine,
    CartModifierSelection,
    MODIFIER_MODE_SINGLE,
    ModifierGroup,
    ModifierOption,
    Product,
)
from ..i18n import layout_direction, tr
from ..icons import get_svg_icon
from ..money import format_minor
from ..theme import contrast_text_hex
from .animations import attach_button_feedback
from .widgets import SquareThumbLabel


class ProductCustomizeDialog(QDialog):
    """Compact cashier customization modal for products with active modifier groups."""

    def __init__(
        self,
        config: BusinessConfig,
        product: Product,
        groups: list[ModifierGroup],
        *,
        initial_selections: tuple[CartModifierSelection, ...] | list[CartModifierSelection] = (),
        editing_cart_item: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.config = config
        self.product = product
        self.groups = [g for g in groups if g.is_active and not g.is_archived and g.active_options]
        self._editing_cart_item = bool(editing_cart_item)
        self._selected_option_ids_by_group: dict[int, list[int]] = {}
        self._option_buttons: dict[int, QPushButton] = {}
        self._group_rule_labels: dict[int, QLabel] = {}
        self._group_cards: dict[int, QFrame] = {}

        self.result_modifiers: tuple[CartModifierSelection, ...] = ()
        self.result_unit_price_minor: int = int(product.price_minor)

        initial_opt_ids = {int(m.option_id) for m in initial_selections if m.option_id is not None}
        for grp in self.groups:
            valid_init = [o.id for o in grp.active_options if o.id in initial_opt_ids]
            if grp.selection_mode == MODIFIER_MODE_SINGLE and len(valid_init) > 1:
                valid_init = valid_init[:1]
            self._selected_option_ids_by_group[grp.id] = valid_init

        self.setModal(True)
        self.setLayoutDirection(layout_direction())
        self.setWindowTitle(tr("customize.title", name=product.display_name()))
        self.resize(540, 520)
        self.setMinimumSize(460, 380)

        self._build_ui()
        self._refresh_selection_state()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        # Header Card: Product thumb + Name + Base price + Live Unit Price badge
        header = QFrame()
        header.setObjectName("CheckoutHeaderCard")
        hdr_lay = QHBoxLayout(header)
        hdr_lay.setContentsMargins(12, 10, 12, 10)
        hdr_lay.setSpacing(12)

        thumb = SquareThumbLabel(size=54, radius=10)
        thumb.set_product_image(
            self.product.image_filename,
            self.product.category,
            self.config.accent_color,
            product_name=(self.product.name_en or self.product.name),
        )
        hdr_lay.addWidget(thumb)

        info_col = QVBoxLayout()
        info_col.setSpacing(2)
        name_lbl = QLabel(self.product.display_name())
        name_lbl.setProperty("class", "SectionHeader")
        name_lbl.setWordWrap(True)
        base_lbl = QLabel(
            tr(
                "customize.base_price",
                price=format_minor(self.product.price_minor, self.config.currency),
            )
        )
        base_lbl.setProperty("class", "MutedLabel")
        self.extras_summary_lbl = QLabel(tr("customize.none_selected"))
        self.extras_summary_lbl.setProperty("class", "MutedLabel")
        self.extras_summary_lbl.setWordWrap(True)
        info_col.addWidget(name_lbl)
        info_col.addWidget(base_lbl)
        info_col.addWidget(self.extras_summary_lbl)
        hdr_lay.addLayout(info_col, 1)

        total_badge = QFrame()
        total_badge.setObjectName("CheckoutTotalBadge")
        tb_lay = QVBoxLayout(total_badge)
        tb_lay.setContentsMargins(12, 6, 12, 6)
        tb_lay.setSpacing(1)
        t_lbl = QLabel(tr("customize.unit_total_label"))
        t_lbl.setProperty("class", "MutedLabel")
        t_lbl.setAlignment(Qt.AlignCenter)
        self.unit_total_lbl = QLabel(format_minor(self.product.price_minor, self.config.currency))
        self.unit_total_lbl.setObjectName("TotalValueLabel")
        self.unit_total_lbl.setAlignment(Qt.AlignCenter)
        tb_lay.addWidget(t_lbl)
        tb_lay.addWidget(self.unit_total_lbl)
        hdr_lay.addWidget(total_badge)
        root.addWidget(header)

        # Scrollable Modifier Group Cards
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setObjectName("CheckoutScrollHost")
        host = QWidget()
        host.setObjectName("CheckoutScrollHost")
        body = QVBoxLayout(host)
        body.setContentsMargins(2, 2, 4, 2)
        body.setSpacing(10)

        for grp in self.groups:
            card = QFrame()
            card.setProperty("class", "CozySubCard")
            card_lay = QVBoxLayout(card)
            card_lay.setContentsMargins(12, 10, 12, 10)
            card_lay.setSpacing(7)

            top = QHBoxLayout()
            g_lbl = QLabel(grp.display_name())
            g_lbl.setStyleSheet("font-size: 13px; font-weight: 700; color: #202820;")
            req_badge = QLabel(
                tr("customize.required_badge") if grp.is_required else tr("customize.optional_badge")
            )
            req_badge.setProperty("class", "PeachBadge" if grp.is_required else "CountBadge")
            rule_lbl = QLabel(self._format_rule_text(grp))
            rule_lbl.setProperty("class", "MutedLabel")
            top.addWidget(g_lbl)
            top.addWidget(req_badge)
            top.addStretch(1)
            top.addWidget(rule_lbl)
            card_lay.addLayout(top)

            grid = QGridLayout()
            grid.setHorizontalSpacing(8)
            grid.setVerticalSpacing(7)
            grid.setColumnStretch(0, 1)
            grid.setColumnStretch(1, 1)
            for idx, opt in enumerate(grp.active_options):
                btn = QPushButton(self._option_button_text(opt))
                btn.setProperty("class", "ModifierOptionBtn")
                btn.setProperty("selected", "false")
                btn.setCursor(Qt.PointingHandCursor)
                btn.setMinimumHeight(38)
                btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
                btn.clicked.connect(
                    lambda _c=False, gid=grp.id, oid=opt.id: self._toggle_option(gid, oid)
                )
                attach_button_feedback(btn)
                self._option_buttons[opt.id] = btn
                grid.addWidget(btn, idx // 2, idx % 2)
            card_lay.addLayout(grid)
            self._group_cards[grp.id] = card
            self._group_rule_labels[grp.id] = rule_lbl
            body.addWidget(card)

        body.addStretch(1)
        scroll.setWidget(host)
        root.addWidget(scroll, 1)

        self.validation_lbl = QLabel("")
        self.validation_lbl.setProperty("class", "ValidationHint")
        self.validation_lbl.setWordWrap(True)
        self.validation_lbl.hide()
        root.addWidget(self.validation_lbl)

        # Footer buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addStretch(1)
        self.cancel_btn = QPushButton(tr("common.cancel"))
        self.cancel_btn.setMinimumHeight(38)
        self.cancel_btn.clicked.connect(self.reject)
        attach_button_feedback(self.cancel_btn)

        self.confirm_btn = QPushButton()
        self.confirm_btn.setProperty("role", "primary")
        self.confirm_btn.setMinimumHeight(38)
        self.confirm_btn.setMinimumWidth(190)
        self.confirm_btn.setIcon(
            get_svg_icon("check", contrast_text_hex(self.config.accent_color), 14)
        )
        self.confirm_btn.setIconSize(QSize(14, 14))
        self.confirm_btn.setDefault(True)
        self.confirm_btn.clicked.connect(self._on_confirm)
        attach_button_feedback(self.confirm_btn)

        btn_row.addWidget(self.cancel_btn)
        btn_row.addWidget(self.confirm_btn)
        root.addLayout(btn_row)

    @staticmethod
    def _format_rule_text(grp: ModifierGroup) -> str:
        if grp.selection_mode == MODIFIER_MODE_SINGLE:
            return tr("customize.rule_single_req") if grp.is_required else tr("customize.rule_single_opt")
        if grp.min_select > 0 and grp.max_select > 0:
            if grp.min_select == grp.max_select:
                return tr("customize.rule_multi_exact", min=grp.min_select)
            return tr("customize.rule_multi_range", min=grp.min_select, max=grp.max_select)
        if grp.min_select > 0:
            return tr("customize.rule_multi_min", min=grp.min_select)
        if grp.max_select > 0:
            return tr("customize.rule_multi_max", max=grp.max_select)
        return tr("customize.rule_multi_any")

    def _option_button_text(self, opt: ModifierOption) -> str:
        delta = opt.resolved_price_delta_minor
        name = opt.display_name().replace("&", "&&")
        if delta > 0:
            return f"{name}  (+{format_minor(delta, self.config.currency)})"
        return name

    def _group_by_id(self, gid: int) -> ModifierGroup | None:
        for g in self.groups:
            if g.id == gid:
                return g
        return None

    def _toggle_option(self, group_id: int, option_id: int) -> None:
        grp = self._group_by_id(group_id)
        if grp is None:
            return
        cur = list(self._selected_option_ids_by_group.get(group_id, []))
        if grp.selection_mode == MODIFIER_MODE_SINGLE:
            if option_id in cur:
                if not grp.is_required:
                    cur.remove(option_id)
            else:
                cur = [option_id]
        else:
            if option_id in cur:
                cur.remove(option_id)
            else:
                if grp.max_select > 0 and len(cur) >= grp.max_select:
                    # Smoothly swap out oldest choice when at cap of 1, or keep strictly capped
                    if grp.max_select == 1:
                        cur = [option_id]
                    else:
                        return
                else:
                    cur.append(option_id)
        self._selected_option_ids_by_group[group_id] = cur
        self._refresh_selection_state()

    def _refresh_selection_state(self) -> bool:
        selected_all: list[CartModifierSelection] = []
        delta_sum = 0
        invalid_groups: list[str] = []

        for grp in self.groups:
            chosen_ids = self._selected_option_ids_by_group.get(grp.id, [])
            chosen_set = set(chosen_ids)
            for opt in grp.active_options:
                btn = self._option_buttons.get(opt.id)
                is_sel = opt.id in chosen_set
                if btn is not None:
                    btn.setProperty("selected", "true" if is_sel else "false")
                    btn.style().unpolish(btn)
                    btn.style().polish(btn)
                    btn.update()
                if is_sel:
                    delta = opt.resolved_price_delta_minor
                    delta_sum += delta
                    selected_all.append(
                        CartModifierSelection(
                            group_id=grp.id,
                            option_id=opt.id,
                            group_name=grp.name_en or grp.name_ar,
                            group_name_en=grp.name_en,
                            group_name_ar=grp.name_ar,
                            option_name=opt.name_en or opt.name_ar,
                            option_name_en=opt.name_en,
                            option_name_ar=opt.name_ar,
                            price_delta_minor=delta,
                        )
                    )

            cnt = len(chosen_ids)
            min_req = max(1, grp.min_select) if grp.is_required else grp.min_select
            valid_grp = True
            if grp.is_required and cnt < min_req:
                valid_grp = False
            elif (not grp.is_required) and grp.min_select > 0 and 0 < cnt < grp.min_select:
                valid_grp = False
            elif grp.max_select > 0 and cnt > grp.max_select:
                valid_grp = False
            if not valid_grp:
                invalid_groups.append(grp.display_name())

        self.result_modifiers = tuple(selected_all)
        self.result_unit_price_minor = max(0, int(self.product.price_minor) + delta_sum)
        formatted_unit = format_minor(self.result_unit_price_minor, self.config.currency)
        self.unit_total_lbl.setText(formatted_unit)

        if delta_sum > 0:
            self.extras_summary_lbl.setText(
                tr("customize.extras_summary", price=format_minor(delta_sum, self.config.currency))
            )
        elif selected_all:
            self.extras_summary_lbl.setText(
                " · ".join(m.display_option_name() for m in selected_all)
            )
        else:
            self.extras_summary_lbl.setText(tr("customize.none_selected"))

        cta_key = "customize.update_btn" if self._editing_cart_item else "customize.add_btn"
        self.confirm_btn.setText(tr(cta_key, price=formatted_unit))

        if invalid_groups:
            self.validation_lbl.setText(
                tr("customize.err_group_rule", groups=", ".join(invalid_groups))
            )
            self.validation_lbl.show()
            self.confirm_btn.setEnabled(False)
            return False

        self.validation_lbl.hide()
        self.confirm_btn.setEnabled(True)
        return True

    def _on_confirm(self) -> None:
        if not self._refresh_selection_state():
            return
        self.accept()

    def build_cart_line(self, *, quantity: int = 1) -> CartLine:
        return CartLine(
            product_id=self.product.id,
            name=self.product.name,
            unit_price_minor=self.result_unit_price_minor,
            quantity=max(1, int(quantity)),
            image_filename=self.product.image_filename,
            category=self.product.category,
            name_en=self.product.name_en,
            name_ar=self.product.name_ar,
            modifiers=self.result_modifiers,
            base_unit_price_minor=int(self.product.price_minor),
        )

