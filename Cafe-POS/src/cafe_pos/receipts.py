from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QImage, QPageSize, QTextDocument
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWidgets import QDialog, QWidget

from .config import BusinessConfig
from .db import OrderRecord
from .money import format_minor
from .paths import get_asset_path


def format_local_timestamp(iso_str: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is not None:
            dt = dt.astimezone()
        return dt.strftime("%Y-%m-%d  %I:%M %p")
    except ValueError:
        return iso_str


def build_receipt_plain_text(order: OrderRecord) -> str:
    lines: list[str] = []
    lines.append(order.business_name.center(40))
    if order.business_address:
        lines.append(order.business_address.center(40))
    if order.business_phone:
        lines.append(order.business_phone.center(40))
    lines.append("-" * 40)
    lines.append(f"Receipt #: {order.order_number}")
    lines.append(f"Date:      {format_local_timestamp(order.created_at)}")
    lines.append("Payment:   Cash")
    lines.append("-" * 40)
    for item in order.items:
        total_str = format_minor(item.line_total_minor, order.currency)
        unit_str = format_minor(item.unit_price_minor, order.currency)
        lines.append(f"{item.quantity}x {item.product_name}")
        lines.append(f"   @ {unit_str:<18} {total_str:>15}")
    lines.append("-" * 40)
    lines.append(f"{'Subtotal':<22} {format_minor(order.subtotal_minor, order.currency):>17}")
    lines.append(f"{'TOTAL':<22} {format_minor(order.total_minor, order.currency):>17}")
    lines.append(f"{'Cash Received':<22} {format_minor(order.cash_received_minor, order.currency):>17}")
    lines.append(f"{'Change':<22} {format_minor(order.change_minor, order.currency):>17}")
    lines.append("-" * 40)
    lines.append("Thank you for your visit!".center(40))
    return "\n".join(lines)


def build_receipt_document(order: OrderRecord, cfg: BusinessConfig | None = None) -> QTextDocument:
    doc = QTextDocument()

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
            logo_block = (
                '<div style="text-align:center; margin-bottom:6px;">'
                '<img src="logo://business" width="44" height="44">'
                "</div>"
            )

    biz_name = html.escape(order.business_name)
    addr_html = (
        f'<div style="font-size:9pt; color:#555;">{html.escape(order.business_address)}</div>'
        if order.business_address
        else ""
    )
    phone_html = (
        f'<div style="font-size:9pt; color:#555;">{html.escape(order.business_phone)}</div>'
        if order.business_phone
        else ""
    )

    items_rows = []
    for item in order.items:
        name = html.escape(item.product_name)
        unit = html.escape(format_minor(item.unit_price_minor, order.currency))
        line_total = html.escape(format_minor(item.line_total_minor, order.currency))
        items_rows.append(
            f'<tr>'
            f'<td style="padding:4px 0; vertical-align:top;">'
            f'<b>{item.quantity}&times; {name}</b><br>'
            f'<span style="font-size:8.5pt; color:#555;">@ {unit}</span>'
            f'</td>'
            f'<td style="padding:4px 0; text-align:right; vertical-align:top; white-space:nowrap;">{line_total}</td>'
            f'</tr>'
        )

    receipt_html = f"""
    <html>
      <body style="font-family:'Consolas','Courier New',monospace; font-size:9.5pt; color:#111; margin:0; padding:8px;">
        {logo_block}
        <div style="text-align:center; margin-bottom:8px;">
          <div style="font-family:'Segoe UI',Arial,sans-serif; font-size:13pt; font-weight:bold;">{biz_name}</div>
          {addr_html}
          {phone_html}
        </div>
        <hr style="border:none; border-top:1px dashed #888; margin:8px 0;">
        <table width="100%" cellspacing="0" cellpadding="1" style="font-size:9pt;">
          <tr>
            <td>Receipt #:</td>
            <td align="right"><b>{html.escape(order.order_number)}</b></td>
          </tr>
          <tr>
            <td>Date:</td>
            <td align="right">{html.escape(format_local_timestamp(order.created_at))}</td>
          </tr>
          <tr>
            <td>Payment:</td>
            <td align="right">Cash</td>
          </tr>
        </table>
        <hr style="border:none; border-top:1px dashed #888; margin:8px 0;">
        <table width="100%" cellspacing="0" cellpadding="0" style="font-size:9.5pt;">
          {''.join(items_rows)}
        </table>
        <hr style="border:none; border-top:1px dashed #888; margin:8px 0;">
        <table width="100%" cellspacing="0" cellpadding="2" style="font-size:9.5pt;">
          <tr>
            <td>Subtotal</td>
            <td align="right">{html.escape(format_minor(order.subtotal_minor, order.currency))}</td>
          </tr>
          <tr>
            <td style="font-size:11pt; font-weight:bold; padding-top:4px;">Total</td>
            <td align="right" style="font-size:11pt; font-weight:bold; padding-top:4px;">{html.escape(format_minor(order.total_minor, order.currency))}</td>
          </tr>
          <tr>
            <td>Cash Received</td>
            <td align="right">{html.escape(format_minor(order.cash_received_minor, order.currency))}</td>
          </tr>
          <tr>
            <td>Change</td>
            <td align="right"><b>{html.escape(format_minor(order.change_minor, order.currency))}</b></td>
          </tr>
        </table>
        <hr style="border:none; border-top:1px dashed #888; margin:10px 0 8px 0;">
        <div style="text-align:center; font-size:9pt; color:#444;">
          Thank you for your visit!
        </div>
      </body>
    </html>
    """
    doc.setHtml(receipt_html)
    return doc


def print_receipt_native(
    parent: QWidget, order: OrderRecord, cfg: BusinessConfig | None = None
) -> bool:
    printer = QPrinter(QPrinter.HighResolution)
    printer.setPageSize(QPageSize(QPageSize.A4))
    dialog = QPrintDialog(printer, parent)
    dialog.setWindowTitle(f"Print Receipt — {order.order_number}")
    if dialog.exec() != QDialog.Accepted:
        return False

    doc = build_receipt_document(order, cfg)
    doc.print_(printer)
    return True


def export_receipt_pdf(
    dest_path: Path, order: OrderRecord, cfg: BusinessConfig | None = None
) -> None:
    printer = QPrinter(QPrinter.HighResolution)
    printer.setOutputFormat(QPrinter.PdfFormat)
    printer.setPageSize(QPageSize(QPageSize.A5))
    printer.setOutputFileName(str(dest_path))
    doc = build_receipt_document(order, cfg)
    doc.print_(printer)

