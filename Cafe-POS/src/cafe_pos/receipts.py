from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import QMarginsF, QSizeF, Qt, QUrl
from PySide6.QtGui import QImage, QPageLayout, QPageSize, QTextDocument
from PySide6.QtPrintSupport import QPrintDialog, QPrinter, QPrinterInfo
from PySide6.QtWidgets import QDialog, QWidget

from .config import BusinessConfig
from .db import ORDER_TYPE_DELIVERY, ORDER_TYPE_DINE_IN, OrderRecord
from .i18n import (
    bidi_ltr,
    format_timestamp_display,
    get_language,
    normalize_language,
    resolve_receipt_language,
    tr,
    tr_order_type,
    tr_payment_method,
    tr_payment_status,
)
from .money import format_minor
from .paths import get_asset_path
from .payments import METHOD_CASH, STATUS_CASH_SETTLED


def _effective_receipt_lang(cfg: BusinessConfig | None = None, lang: str | None = None) -> str:
    if lang is not None:
        return normalize_language(lang)
    if cfg is not None:
        return resolve_receipt_language(cfg.receipt_language, get_language())
    return get_language()


def format_local_timestamp(iso_str: str, lang: str | None = None) -> str:
    return format_timestamp_display(iso_str, lang=lang)


def _payment_display_fields(order: OrderRecord, eff: str) -> tuple[str, str, str, bool]:
    pays = list(getattr(order, "payments", ()) or ())
    if not pays:
        return (
            tr("receipt.payment_cash", lang=eff),
            tr_payment_status(STATUS_CASH_SETTLED, lang=eff),
            "",
            True,
        )
    if len(pays) > 1:
        parts = [
            f"{tr_payment_method(p.payment_method, custom_label=p.custom_method_label, provider=p.payment_provider, lang=eff)} ({format_minor(p.payment_amount_minor, order.currency, lang=eff)})"
            for p in pays
        ]
        refs = [p.external_reference for p in pays if p.external_reference]
        any_cash = any(p.payment_method == METHOD_CASH for p in pays)
        return (
            tr("receipt.payment_split", lang=eff) + ": " + " + ".join(parts),
            tr_payment_status(pays[0].payment_status, lang=eff),
            ", ".join(refs),
            any_cash,
        )
    pay = pays[0]
    method_txt = tr_payment_method(
        pay.payment_method,
        custom_label=pay.custom_method_label,
        provider=pay.payment_provider,
        lang=eff,
    )
    status_txt = tr_payment_status(pay.payment_status, lang=eff)
    is_cash = pay.payment_method == METHOD_CASH
    return method_txt, status_txt, pay.external_reference, is_cash


def build_receipt_plain_text(
    order: OrderRecord,
    cfg: BusinessConfig | None = None,
    *,
    lang: str | None = None,
    paper_width_mm: int | None = None,
) -> str:
    eff = _effective_receipt_lang(cfg, lang)
    width_mm = (
        int(paper_width_mm)
        if paper_width_mm in (58, 80)
        else int(getattr(cfg, "printer_paper_width_mm", 80) or 80)
    )
    cols = 32 if width_mm == 58 else 40
    show_details = bool(getattr(cfg, "show_business_details_on_receipt", True)) if cfg is not None else True
    show_cashier = (
        bool(getattr(cfg, "show_cashier_on_receipt", True))
        if cfg is not None
        else bool((order.cashier_name_snapshot or "").strip())
    )

    receipt_num = order.receipt_number or order.order_number
    pay_method_txt, pay_status_txt, pay_ref, is_cash = _payment_display_fields(order, eff)

    lines: list[str] = []
    lines.append(order.business_name.center(cols))
    if show_details:
        if order.business_address:
            lines.append(order.business_address.center(cols))
        if order.business_phone:
            lines.append(order.business_phone.center(cols))
    lines.append("-" * cols)
    lines.append(f"{tr('receipt.receipt_num_label', lang=eff)} {receipt_num}")
    lines.append(f"{tr('receipt.order_num_label', lang=eff)} {order.order_number}")
    lines.append(f"{tr('receipt.date_label', lang=eff)} {format_local_timestamp(order.created_at, lang=eff)}")
    lines.append(f"{tr('receipt.order_type_label', lang=eff)} {tr_order_type(order.order_type, lang=eff)}")
    if order.order_type == ORDER_TYPE_DINE_IN and order.table_number:
        lines.append(f"{tr('receipt.table_label', lang=eff)} {order.table_number}")
    elif order.order_type == ORDER_TYPE_DELIVERY:
        if order.customer_name:
            lines.append(f"{tr('receipt.customer_label', lang=eff)} {order.customer_name}")
        if order.customer_phone:
            lines.append(f"{tr('receipt.phone_label', lang=eff)} {order.customer_phone}")
        if order.delivery_address:
            lines.append(f"{tr('receipt.address_label', lang=eff)} {order.delivery_address}")
    if show_cashier:
        cashier_disp = (order.cashier_name_snapshot or "").strip() or tr(
            "receipt.cashier_unspecified", lang=eff
        )
        lines.append(f"{tr('receipt.cashier_label', lang=eff)} {cashier_disp}")
    lines.append(f"{tr('receipt.payment_label', lang=eff)} {pay_method_txt}")
    lines.append(f"{tr('receipt.payment_status_label', lang=eff)} {pay_status_txt}")
    if pay_ref:
        lines.append(f"{tr('receipt.payment_ref_label', lang=eff)} {pay_ref}")
    lines.append("-" * cols)
    for item in order.items:
        item_name = item.display_name(eff)
        total_str = format_minor(item.line_total_minor, order.currency, lang=eff)
        unit_str = format_minor(item.unit_price_minor, order.currency, lang=eff)
        if eff == "ar-EG":
            lines.append(f"{item.quantity}× {item_name}")
            for mod in item.modifiers:
                m_opt = mod.display_option_name(eff)
                if mod.price_delta_minor > 0:
                    d_str = format_minor(mod.price_delta_minor, order.currency, lang=eff)
                    lines.append(f"   + {m_opt} (+{d_str})")
                else:
                    lines.append(f"   + {m_opt}")
            lines.append(f"   ({unit_str})   {total_str}")
        else:
            lines.append(f"{item.quantity}x {item_name}")
            for mod in item.modifiers:
                m_opt = mod.display_option_name(eff)
                if mod.price_delta_minor > 0:
                    d_str = format_minor(mod.price_delta_minor, order.currency, lang=eff)
                    lines.append(f"   + {m_opt} (+{d_str})")
                else:
                    lines.append(f"   + {m_opt}")
            unit_w = max(10, cols - 20)
            lines.append(f"   @ {unit_str:<{unit_w}} {total_str:>15}")
    lines.append("-" * cols)
    lbl_w = max(14, cols - 18)
    lines.append(f"{tr('receipt.subtotal', lang=eff):<{lbl_w}} {format_minor(order.subtotal_minor, order.currency, lang=eff):>17}")
    if order.tax_minor > 0:
        lines.append(f"{tr('receipt.tax_label', lang=eff):<{lbl_w}} {format_minor(order.tax_minor, order.currency, lang=eff):>17}")
    if order.discount_minor > 0:
        d_lbl = tr("receipt.discount_label", lang=eff)
        if getattr(order, "discount_label", ""):
            d_lbl = f"{d_lbl} ({order.discount_label})"
        lines.append(f"{d_lbl:<{lbl_w}} -{format_minor(order.discount_minor, order.currency, lang=eff):>16}")
    lines.append(f"{tr('receipt.total_upper', lang=eff):<{lbl_w}} {format_minor(order.total_minor, order.currency, lang=eff):>17}")
    if len(getattr(order, "payments", ()) or ()) > 1:
        for pay_leg in order.payments:
            leg_lbl = tr_payment_method(
                pay_leg.payment_method,
                custom_label=pay_leg.custom_method_label,
                provider=pay_leg.payment_provider,
                lang=eff,
            )
            lines.append(f"  {leg_lbl:<{max(12, lbl_w - 2)}} {format_minor(pay_leg.payment_amount_minor, order.currency, lang=eff):>17}")
    if is_cash or order.cash_received_minor > 0:
        lines.append(f"{tr('receipt.cash_paid', lang=eff):<{lbl_w}} {format_minor(order.cash_received_minor, order.currency, lang=eff):>17}")
        lines.append(f"{tr('receipt.change_due', lang=eff):<{lbl_w}} {format_minor(order.change_minor, order.currency, lang=eff):>17}")
    if getattr(order, "refunded_total_minor", 0) > 0:
        lines.append(f"{tr('receipt.refunded_label', lang=eff):<{lbl_w}} -{format_minor(order.refunded_total_minor, order.currency, lang=eff):>16}")
    lines.append("-" * cols)
    lines.append(tr("receipt.thank_you", lang=eff).center(cols))
    return "\n".join(lines)


def build_receipt_document(
    order: OrderRecord,
    cfg: BusinessConfig | None = None,
    *,
    lang: str | None = None,
    paper_width_mm: int | None = None,
) -> QTextDocument:
    eff = _effective_receipt_lang(cfg, lang)
    rtl = eff == "ar-EG"
    width_mm = (
        int(paper_width_mm)
        if paper_width_mm in (58, 80)
        else int(getattr(cfg, "printer_paper_width_mm", 80) or 80)
    )
    compact_58 = width_mm == 58
    show_details = bool(getattr(cfg, "show_business_details_on_receipt", True)) if cfg is not None else True
    show_cashier = (
        bool(getattr(cfg, "show_cashier_on_receipt", True))
        if cfg is not None
        else bool((order.cashier_name_snapshot or "").strip())
    )

    doc = QTextDocument()
    opt = doc.defaultTextOption()
    opt.setTextDirection(Qt.RightToLeft if rtl else Qt.LeftToRight)
    doc.setDefaultTextOption(opt)

    logo_block = ""
    if cfg is not None:
        img = QImage()
        if cfg.logo_blob:
            img = QImage.fromData(cfg.logo_blob)
        if img.isNull():
            default_logo = get_asset_path("default_logo.png")
            if default_logo.exists():
                img = QImage(str(default_logo))
        if not img.isNull():
            doc.addResource(QTextDocument.ImageResource, QUrl("logo://business"), img)
            logo_sz = "34" if compact_58 else "44"
            logo_block = (
                '<div style="text-align:center; margin-bottom:6px;">'
                f'<img src="logo://business" width="{logo_sz}" height="{logo_sz}">'
                "</div>"
            )

    biz_name = html.escape(order.business_name)
    addr_raw = (order.business_address or "").strip() if show_details else ""
    if addr_raw:
        has_arabic_addr = any("\u0600" <= ch <= "\u06ff" for ch in addr_raw)
        addr_dir = "rtl" if (rtl and has_arabic_addr) else "ltr"
        addr_txt = html.escape(bidi_ltr(addr_raw) if (rtl and not has_arabic_addr) else addr_raw)
        addr_html = f'<div dir="{addr_dir}" style="font-size:{"8pt" if compact_58 else "9pt"}; color:#555;">{addr_txt}</div>'
    else:
        addr_html = ""

    phone_raw = (order.business_phone or "").strip() if show_details else ""
    phone_html = (
        f'<div dir="ltr" style="font-size:{"8pt" if compact_58 else "9pt"}; color:#555;">{html.escape(bidi_ltr(phone_raw) if rtl else phone_raw)}</div>'
        if phone_raw
        else ""
    )

    body_dir = "rtl" if rtl else "ltr"
    body_font = (
        "'Segoe UI Variable Text','Segoe UI','Tahoma','Arial',sans-serif"
        if rtl
        else "'Consolas','Courier New',monospace"
    )
    title_font = "'Segoe UI Variable Text','Segoe UI','Tahoma','Arial',sans-serif"

    def _td_cell(content_html: str, *, width: str, align: str, style: str = "", cell_dir: str = "") -> str:
        dir_attr = f' dir="{cell_dir}"' if cell_dir else ""
        return (
            f'<td width="{width}" align="{align}"{dir_attr} '
            f'style="text-align:{align}; line-height:140%; {style}">{content_html}</td>'
        )

    def _kv_row(
        label_html: str,
        val_html: str,
        *,
        label_style: str = "",
        val_style: str = "",
        label_w: str = "62%",
        val_w: str = "38%",
    ) -> str:
        if rtl:
            # QTextDocument places TD 0 on the physical LEFT and TD 1 on the physical RIGHT
            # regardless of <table dir="rtl">, while interpreting text-align inside dir="rtl"
            # logically (left=start=RIGHT edge, right=end=LEFT edge). Swapping both places
            # values flush LEFT and labels flush RIGHT cleanly edge-to-edge.
            left_td = _td_cell(
                val_html,
                width=val_w,
                align="right",
                cell_dir="rtl",
                style=f"vertical-align:top; white-space:nowrap; {val_style}",
            )
            right_td = _td_cell(
                label_html,
                width=label_w,
                align="left",
                cell_dir="rtl",
                style=f"vertical-align:top; {label_style}",
            )
            return f"<tr>{left_td}{right_td}</tr>"
        left_td = _td_cell(
            label_html,
            width=label_w,
            align="left",
            style=f"vertical-align:top; {label_style}",
        )
        right_td = _td_cell(
            val_html,
            width=val_w,
            align="right",
            style=f"vertical-align:top; white-space:nowrap; {val_style}",
        )
        return f"<tr>{left_td}{right_td}</tr>"

    items_rows: list[str] = []
    for item in order.items:
        name = html.escape(item.display_name(eff))
        unit_fmt = format_minor(item.unit_price_minor, order.currency, lang=eff)
        line_total_fmt = format_minor(item.line_total_minor, order.currency, lang=eff)
        mod_lines_html = ""
        if item.modifiers:
            mod_parts: list[str] = []
            for mod in item.modifiers:
                m_opt = html.escape(mod.display_option_name(eff))
                if mod.price_delta_minor > 0:
                    d_fmt = html.escape(format_minor(mod.price_delta_minor, order.currency, lang=eff))
                    delta_span = bidi_ltr(f"+{d_fmt}") if rtl else f"+{d_fmt}"
                    mod_parts.append(f"&bull; {m_opt} ({html.escape(delta_span)})")
                else:
                    mod_parts.append(f"&bull; {m_opt}")
            mod_lines_html = (
                '<br><span style="font-size:8.2pt; color:#555;">'
                + "<br>".join(mod_parts)
                + "</span>"
            )
        if rtl:
            qty_badge = html.escape(bidi_ltr(f"{item.quantity}×"))
            label_part = (
                f'<b>{qty_badge} {name}</b>{mod_lines_html}<br>'
                f'<span style="font-size:8.5pt; color:#555;">{html.escape(unit_fmt)}</span>'
            )
            items_rows.append(
                _kv_row(
                    label_part,
                    html.escape(line_total_fmt),
                    label_style="padding:5px 0;",
                    val_style="padding:5px 0;",
                    label_w="65%",
                    val_w="35%",
                )
            )
        else:
            label_part = (
                f'<b>{item.quantity}&times; {name}</b>{mod_lines_html}<br>'
                f'<span style="font-size:8.5pt; color:#555;">@ {html.escape(unit_fmt)}</span>'
            )
            items_rows.append(
                _kv_row(
                    label_part,
                    html.escape(line_total_fmt),
                    label_style="padding:4px 0;",
                    val_style="padding:4px 0;",
                    label_w="65%",
                    val_w="35%",
                )
            )

    cashier_row = ""
    if show_cashier:
        cashier_disp = (order.cashier_name_snapshot or "").strip() or tr(
            "receipt.cashier_unspecified", lang=eff
        )
        cashier_row = _kv_row(
            html.escape(tr("receipt.cashier_label", lang=eff)),
            html.escape(cashier_disp),
            label_style="padding:2px 0;",
            val_style="padding:2px 0;",
            label_w="45%",
            val_w="55%",
        )

    receipt_num = order.receipt_number or order.order_number
    pay_method_txt, pay_status_txt, pay_ref, is_cash = _payment_display_fields(order, eff)

    receipt_num_html = f"<b>{html.escape(bidi_ltr(receipt_num) if rtl else receipt_num)}</b>"
    order_num_html = f"<b>{html.escape(bidi_ltr(order.order_number) if rtl else order.order_number)}</b>"
    ts_str = format_local_timestamp(order.created_at, lang=eff)

    ref_row = ""
    if pay_ref:
        ref_val_html = html.escape(bidi_ltr(pay_ref) if rtl else pay_ref)
        ref_row = _kv_row(
            html.escape(tr("receipt.payment_ref_label", lang=eff)),
            ref_val_html,
            label_style="padding:2px 0;",
            val_style="padding:2px 0;",
            label_w="45%",
            val_w="55%",
        )

    order_type_rows = [
        _kv_row(
            html.escape(tr("receipt.order_type_label", lang=eff)),
            f"<b>{html.escape(tr_order_type(order.order_type, lang=eff))}</b>",
            label_style="padding:2px 0;",
            val_style="padding:2px 0;",
            label_w="42%",
            val_w="58%",
        )
    ]
    if order.order_type == ORDER_TYPE_DINE_IN and order.table_number:
        tbl_html = html.escape(bidi_ltr(order.table_number) if rtl else order.table_number)
        order_type_rows.append(
            _kv_row(
                html.escape(tr("receipt.table_label", lang=eff)),
                f"<b>{tbl_html}</b>",
                label_style="padding:2px 0;",
                val_style="padding:2px 0;",
                label_w="42%",
                val_w="58%",
            )
        )
    elif order.order_type == ORDER_TYPE_DELIVERY:
        if order.customer_name:
            order_type_rows.append(
                _kv_row(
                    html.escape(tr("receipt.customer_label", lang=eff)),
                    html.escape(order.customer_name),
                    label_style="padding:2px 0;",
                    val_style="padding:2px 0;",
                    label_w="42%",
                    val_w="58%",
                )
            )
        if order.customer_phone:
            ph_html = html.escape(bidi_ltr(order.customer_phone) if rtl else order.customer_phone)
            order_type_rows.append(
                _kv_row(
                    html.escape(tr("receipt.phone_label", lang=eff)),
                    ph_html,
                    label_style="padding:2px 0;",
                    val_style="padding:2px 0;",
                    label_w="42%",
                    val_w="58%",
                )
            )
        if order.delivery_address:
            order_type_rows.append(
                _kv_row(
                    html.escape(tr("receipt.address_label", lang=eff)),
                    html.escape(order.delivery_address),
                    label_style="padding:2px 0;",
                    val_style="padding:2px 0;",
                    label_w="42%",
                    val_w="58%",
                )
            )

    meta_rows = "".join(
        [
            _kv_row(
                html.escape(tr("receipt.receipt_num_label", lang=eff)),
                receipt_num_html,
                label_style="padding:2px 0;",
                val_style="padding:2px 0;",
                label_w="42%",
                val_w="58%",
            ),
            _kv_row(
                html.escape(tr("receipt.order_num_label", lang=eff)),
                order_num_html,
                label_style="padding:2px 0;",
                val_style="padding:2px 0;",
                label_w="42%",
                val_w="58%",
            ),
            _kv_row(
                html.escape(tr("receipt.date_label", lang=eff)),
                html.escape(ts_str),
                label_style="padding:2px 0;",
                val_style="padding:2px 0;",
                label_w="38%",
                val_w="62%",
            ),
            *order_type_rows,
            cashier_row,
            _kv_row(
                html.escape(tr("receipt.payment_label", lang=eff)),
                html.escape(pay_method_txt),
                label_style="padding:2px 0;",
                val_style="padding:2px 0;",
                label_w="45%",
                val_w="55%",
            ),
            _kv_row(
                html.escape(tr("receipt.payment_status_label", lang=eff)),
                html.escape(pay_status_txt),
                label_style="padding:2px 0;",
                val_style="padding:2px 0;",
                label_w="45%",
                val_w="55%",
            ),
            ref_row,
        ]
    )

    summary_parts = [
        _kv_row(
            html.escape(tr("receipt.subtotal", lang=eff)),
            html.escape(format_minor(order.subtotal_minor, order.currency, lang=eff)),
            label_style="padding:2px 0;",
            val_style="padding:2px 0;",
        )
    ]
    if order.tax_minor > 0:
        summary_parts.append(
            _kv_row(
                html.escape(tr("receipt.tax_label", lang=eff)),
                html.escape(format_minor(order.tax_minor, order.currency, lang=eff)),
                label_style="padding:2px 0;",
                val_style="padding:2px 0;",
            )
        )
    if order.discount_minor > 0:
        d_lbl = tr("receipt.discount_label", lang=eff)
        if getattr(order, "discount_label", ""):
            d_lbl = f"{d_lbl} ({order.discount_label})"
        summary_parts.append(
            _kv_row(
                html.escape(d_lbl),
                html.escape("-" + format_minor(order.discount_minor, order.currency, lang=eff)),
                label_style="padding:2px 0;",
                val_style="padding:2px 0;",
            )
        )
    summary_parts.append(
        _kv_row(
            html.escape(tr("receipt.total", lang=eff)),
            html.escape(format_minor(order.total_minor, order.currency, lang=eff)),
            label_style="font-size:11pt; font-weight:bold; padding:5px 0;",
            val_style="font-size:11pt; font-weight:bold; padding:5px 0;",
        )
    )
    if len(getattr(order, "payments", ()) or ()) > 1:
        for pay_leg in order.payments:
            leg_lbl = tr_payment_method(
                pay_leg.payment_method,
                custom_label=pay_leg.custom_method_label,
                provider=pay_leg.payment_provider,
                lang=eff,
            )
            if pay_leg.external_reference:
                leg_lbl = f"{leg_lbl} · {pay_leg.external_reference}"
            summary_parts.append(
                _kv_row(
                    html.escape(leg_lbl),
                    html.escape(format_minor(pay_leg.payment_amount_minor, order.currency, lang=eff)),
                    label_style="padding:1px 0; font-size:8.5pt; color:#444;",
                    val_style="padding:1px 0; font-size:8.5pt; color:#444;",
                )
            )
    if is_cash or order.cash_received_minor > 0:
        summary_parts.append(
            _kv_row(
                html.escape(tr("receipt.cash_paid", lang=eff)),
                html.escape(format_minor(order.cash_received_minor, order.currency, lang=eff)),
                label_style="padding:2px 0;",
                val_style="padding:2px 0;",
            )
        )
        summary_parts.append(
            _kv_row(
                html.escape(tr("receipt.change_due", lang=eff)),
                f'<b>{html.escape(format_minor(order.change_minor, order.currency, lang=eff))}</b>',
                label_style="padding:2px 0;",
                val_style="padding:2px 0;",
            )
        )
    if getattr(order, "refunded_total_minor", 0) > 0:
        summary_parts.append(
            _kv_row(
                html.escape(tr("receipt.refunded_label", lang=eff)),
                html.escape("-" + format_minor(order.refunded_total_minor, order.currency, lang=eff)),
                label_style="padding:2px 0; color:#B23A3A;",
                val_style="padding:2px 0; color:#B23A3A;",
            )
        )
    summary_rows = "".join(summary_parts)

    base_pt = "8.5pt" if compact_58 else "9.5pt"
    meta_pt = "8pt" if compact_58 else "9pt"
    title_pt = "11pt" if compact_58 else "13pt"
    body_pad = "4px" if compact_58 else "8px"

    receipt_html = f"""
    <html dir="{body_dir}">
      <body dir="{body_dir}" style="font-family:{body_font}; font-size:{base_pt}; color:#111; margin:0; padding:{body_pad};">
        {logo_block}
        <div style="text-align:center; margin-bottom:8px;">
          <div style="font-family:{title_font}; font-size:{title_pt}; font-weight:bold;">{biz_name}</div>
          {addr_html}
          {phone_html}
        </div>
        <hr style="border:none; border-top:1px dashed #888; margin:8px 0;">
        <table width="100%" cellspacing="0" cellpadding="2" style="width:100%; font-size:{meta_pt};">
          {meta_rows}
        </table>
        <hr style="border:none; border-top:1px dashed #888; margin:8px 0;">
        <table width="100%" cellspacing="0" cellpadding="0" style="width:100%; font-size:{base_pt};">
          {''.join(items_rows)}
        </table>
        <hr style="border:none; border-top:1px dashed #888; margin:8px 0;">
        <table width="100%" cellspacing="0" cellpadding="2" style="width:100%; font-size:{base_pt};">
          {summary_rows}
        </table>
        <hr style="border:none; border-top:1px dashed #888; margin:10px 0 8px 0;">
        <div dir="{body_dir}" style="text-align:center; font-size:{meta_pt}; color:#444; padding-bottom:4px;">
          {html.escape(tr("receipt.thank_you", lang=eff))}
        </div>
      </body>
    </html>
    """
    doc.setHtml(receipt_html)
    return doc


def list_available_printers() -> list[str]:
    try:
        names = [p.printerName() for p in QPrinterInfo.availablePrinters() if p.printerName()]
    except Exception:
        names = []
    return names


def configure_thermal_printer(printer: QPrinter, paper_width_mm: int, doc: QTextDocument) -> None:
    width_mm = 58 if int(paper_width_mm or 80) == 58 else 80
    printable_w_mm = width_mm - 6.0
    # Compute natural rolled length cleanly from document width
    doc.setTextWidth(printable_w_mm * 3.78)
    est_height_mm = max(90.0, min(600.0, (doc.size().height() / 3.78) + 18.0))
    page_size = QPageSize(QSizeF(float(width_mm), float(est_height_mm)), QPageSize.Millimeter)
    printer.setPageSize(page_size)
    printer.setPageMargins(QMarginsF(3.0, 4.0, 3.0, 6.0), QPageLayout.Millimeter)


def print_receipt_to_printer(
    order: OrderRecord,
    cfg: BusinessConfig | None = None,
    *,
    lang: str | None = None,
    printer_name: str = "",
    paper_width_mm: int | None = None,
) -> tuple[bool, str]:
    eff = _effective_receipt_lang(cfg, lang)
    target_name = (printer_name or getattr(cfg, "printer_name", "") or "").strip()
    width_mm = (
        int(paper_width_mm)
        if paper_width_mm in (58, 80)
        else int(getattr(cfg, "printer_paper_width_mm", 80) or 80)
    )
    info = None
    for candidate in QPrinterInfo.availablePrinters():
        if target_name and candidate.printerName().casefold() == target_name.casefold():
            info = candidate
            break
    if info is None and target_name:
        return False, tr("receipt.err.printer_not_found", lang=eff, name=target_name)
    if info is None:
        info = QPrinterInfo.defaultPrinter()
    if info.isNull():
        return False, tr("receipt.err.no_default_printer", lang=eff)

    printer = QPrinter(info, QPrinter.HighResolution)
    doc = build_receipt_document(order, cfg, lang=eff, paper_width_mm=width_mm)
    configure_thermal_printer(printer, width_mm, doc)
    doc.print_(printer)
    return True, info.printerName()


def print_test_thermal_receipt(
    cfg: BusinessConfig,
    *,
    lang: str | None = None,
    printer_name: str = "",
    paper_width_mm: int | None = None,
) -> tuple[bool, str]:
    from .db import OrderItemRecord, PaymentRecord, _utc_now_iso

    eff = _effective_receipt_lang(cfg, lang)
    sample = OrderRecord(
        id=0,
        order_number="TEST-001",
        receipt_number="RCP-TEST",
        created_at=_utc_now_iso(),
        currency=cfg.currency,
        subtotal_minor=450,
        total_minor=450,
        cash_received_minor=500,
        change_minor=50,
        business_name=cfg.business_name,
        business_address=cfg.address,
        business_phone=cfg.phone,
        cashier_name_snapshot="MoCoffee Test",
        items=[
            OrderItemRecord(
                product_id=None,
                product_name="Flat White",
                product_name_en="Flat White",
                product_name_ar="فلات وايت",
                unit_price_minor=450,
                quantity=1,
                line_total_minor=450,
            )
        ],
        payments=(
            PaymentRecord(
                id=0,
                order_id=0,
                payment_method=METHOD_CASH,
                payment_amount_minor=450,
                currency=cfg.currency,
            ),
        ),
    )
    return print_receipt_to_printer(
        sample,
        cfg,
        lang=eff,
        printer_name=printer_name,
        paper_width_mm=paper_width_mm,
    )


def print_receipt_native(
    parent: QWidget,
    order: OrderRecord,
    cfg: BusinessConfig | None = None,
    *,
    lang: str | None = None,
) -> bool:
    eff = _effective_receipt_lang(cfg, lang)
    printer_name = (getattr(cfg, "printer_name", "") or "").strip()
    width_mm = int(getattr(cfg, "printer_paper_width_mm", 80) or 80)
    info = None
    if printer_name:
        for candidate in QPrinterInfo.availablePrinters():
            if candidate.printerName().casefold() == printer_name.casefold():
                info = candidate
                break
    printer = QPrinter(info, QPrinter.HighResolution) if info is not None else QPrinter(QPrinter.HighResolution)
    doc = build_receipt_document(order, cfg, lang=eff, paper_width_mm=width_mm)
    configure_thermal_printer(printer, width_mm, doc)
    dialog = QPrintDialog(printer, parent)
    dialog.setWindowTitle(tr("receipt.print_dialog_title", lang=eff, num=order.order_number))
    if dialog.exec() != QDialog.Accepted:
        return False

    doc.print_(printer)
    return True


def export_receipt_pdf(
    dest_path: Path,
    order: OrderRecord,
    cfg: BusinessConfig | None = None,
    *,
    lang: str | None = None,
) -> None:
    eff = _effective_receipt_lang(cfg, lang)
    printer = QPrinter(QPrinter.HighResolution)
    printer.setOutputFormat(QPrinter.PdfFormat)
    printer.setPageSize(QPageSize(QPageSize.A5))
    printer.setOutputFileName(str(dest_path))
    doc = build_receipt_document(order, cfg, lang=eff)
    doc.print_(printer)

