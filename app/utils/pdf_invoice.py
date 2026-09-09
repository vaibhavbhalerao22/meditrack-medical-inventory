"""
Generates a printable PDF invoice for a Bill: shop name, address, date,
line items, and total — as requested for the Cashier billing module.
"""
import io
from reportlab.lib.pagesizes import A5
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle,
                                 Paragraph, Spacer, HRFlowable)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_RIGHT


def generate_invoice_pdf(bill, shop_name, shop_address, shop_phone, shop_gstin=""):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A5,
        topMargin=14 * mm, bottomMargin=14 * mm,
        leftMargin=12 * mm, rightMargin=12 * mm,
    )
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="ShopName", fontSize=16, leading=19,
                               alignment=TA_CENTER, fontName="Helvetica-Bold",
                               textColor=colors.HexColor("#0F3D3E")))
    styles.add(ParagraphStyle(name="Small", fontSize=8.5, leading=11,
                               alignment=TA_CENTER, textColor=colors.HexColor("#555555")))
    styles.add(ParagraphStyle(name="InvoiceMeta", fontSize=9, leading=13))
    styles.add(ParagraphStyle(name="RightSmall", fontSize=9, leading=13, alignment=TA_RIGHT))

    elements = []
    elements.append(Paragraph(shop_name, styles["ShopName"]))
    elements.append(Paragraph(shop_address, styles["Small"]))
    meta_line = f"Phone: {shop_phone}" + (f" &nbsp;|&nbsp; GSTIN: {shop_gstin}" if shop_gstin else "")
    elements.append(Paragraph(meta_line, styles["Small"]))
    elements.append(Spacer(1, 6))
    elements.append(HRFlowable(width="100%", color=colors.HexColor("#0F3D3E"), thickness=1.2))
    elements.append(Spacer(1, 6))

    meta_table = Table([
        [Paragraph(f"<b>Invoice No:</b> {bill.invoice_no}", styles["InvoiceMeta"]),
         Paragraph(f"<b>Date:</b> {bill.created_on.strftime('%d %b %Y, %I:%M %p')}", styles["RightSmall"])],
        [Paragraph(f"<b>Customer:</b> {bill.customer_name or 'Walk-in Customer'}", styles["InvoiceMeta"]),
         Paragraph(f"<b>Payment:</b> {bill.payment_mode}", styles["RightSmall"])],
    ], colWidths=[None, None])
    meta_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    elements.append(meta_table)
    elements.append(Spacer(1, 10))

    data = [["#", "Item", "Qty", "Price", "Amount"]]
    for idx, line in enumerate(bill.line_items, start=1):
        data.append([
            str(idx),
            line.item_name_snapshot,
            str(line.quantity),
            f"{float(line.unit_price):.2f}",
            f"{float(line.line_total):.2f}",
        ])

    item_table = Table(data, colWidths=[16 * mm, 62 * mm, 14 * mm, 22 * mm, 24 * mm])
    item_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0F3D3E")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (2, 0), (-1, -1), "CENTER"),
        ("ALIGN", (4, 0), (4, -1), "RIGHT"),
        ("ALIGN", (3, 0), (3, -1), "RIGHT"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CCCCCC")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F4F8F7")]),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    elements.append(item_table)
    elements.append(Spacer(1, 8))

    totals_data = [
        ["Subtotal", f"{float(bill.subtotal):.2f}"],
        ["Discount", f"- {float(bill.discount):.2f}"],
        ["Tax", f"+ {float(bill.tax):.2f}"],
        ["TOTAL", f"Rs. {float(bill.total):.2f}"],
    ]
    totals_table = Table(totals_data, colWidths=[100 * mm, 38 * mm])
    totals_table.setStyle(TableStyle([
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("FONTSIZE", (0, 0), (-1, -2), 9.5),
        ("FONTSIZE", (0, -1), (-1, -1), 12),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("LINEABOVE", (0, -1), (-1, -1), 0.8, colors.HexColor("#0F3D3E")),
        ("TOPPADDING", (0, -1), (-1, -1), 6),
    ]))
    elements.append(totals_table)
    elements.append(Spacer(1, 16))
    elements.append(HRFlowable(width="100%", color=colors.HexColor("#CCCCCC"), thickness=0.6))
    elements.append(Spacer(1, 6))
    elements.append(Paragraph(
        "Thank you for your purchase. Medicines once sold are non-returnable unless defective.",
        styles["Small"],
    ))

    doc.build(elements)
    buffer.seek(0)
    return buffer
