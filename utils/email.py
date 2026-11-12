import boto3
import os
import logging
import threading
import io
import zipfile
from typing import Optional
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from core_functions import operator_auth
from core_functions.operator_auth import require_operator
from utils import auth_token, signed_urls
from html import escape as _xml
from core_functions.db_function import get_db_connection, return_connection
from utils import branding
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle,
    Paragraph, Spacer, HRFlowable,
)
from reportlab.platypus import Image as RLImage
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
 
load_dotenv()
logger = logging.getLogger(__name__)
 
router = APIRouter(prefix="/invoice", tags=["email"])
 
 
# ── Register DejaVu fonts for ₹ glyph support ────────────────────────────────
_FONT      = 'Helvetica'
_FONT_BOLD = 'Helvetica-Bold'
 
for _p in ['/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
           '/usr/share/fonts/dejavu/DejaVuSans.ttf',
           'C:/Windows/Fonts/arial.ttf']:
    if os.path.exists(_p):
        try:
            pdfmetrics.registerFont(TTFont('DejaVu', _p))
            _FONT = 'DejaVu'
        except Exception:
            pass
        break
 
for _p in ['/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
           '/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf',
           'C:/Windows/Fonts/arialbd.ttf']:
    if os.path.exists(_p):
        try:
            pdfmetrics.registerFont(TTFont('DejaVu-Bold', _p))
            _FONT_BOLD = 'DejaVu-Bold'
        except Exception:
            pass
        break


def _seller_lines() -> list:
    """Plain-text legal-entity lines under the seller name (CIN, registered office, GSTIN, contact).
    Values come from the SELLER_* env settings (utils/branding.py); unset values are left out."""
    lines = []
    if branding.seller_cin():
        lines.append(f"CIN: {branding.seller_cin()}")
    lines.extend(branding.seller_address_lines())
    if branding.seller_gstin():
        lines.append(f"GSTIN: {branding.seller_gstin()}")
    if branding.seller_email():
        lines.append(f"Email: {branding.seller_email()}")
    if branding.seller_phone():
        lines.append(f"Phone: {branding.seller_phone()}")
    return lines


def _invoice_footer_html() -> str:
    """Footer links of the invoice e-mail: support/seller e-mail and website, each only when configured."""
    parts = []
    contact = branding.seller_contact_email()
    if contact:
        parts.append(f'<a href="mailto:{_xml(contact, quote=True)}" '
                     f'style="color:#2563EB;text-decoration:none;">{_xml(contact)}</a>')
    site = branding.website_url() or branding.support_url()
    if site:
        label = site.split('://', 1)[-1].rstrip('/')
        parts.append(f'<a href="{_xml(site, quote=True)}" '
                     f'style="color:#2563EB;text-decoration:none;">{_xml(label)}</a>')
    if not parts:
        parts.append(_xml(branding.app_name()))
    return '\n          &nbsp;|&nbsp;\n          '.join(parts)


def format_full_address(org_name: str, org_address: Optional[str] = None,
                         district: Optional[str] = None, state: Optional[str] = None,
                         pincode: Optional[str] = None) -> str:
    """Build a full multi-line billing/shipping address including pincode."""
    lines = [org_name or '']
    if org_address:
        lines.append(org_address)
    locality = ', '.join([p for p in [district, state] if p])
    if locality and pincode:
        lines.append(f"{locality} - {pincode}")
    elif locality:
        lines.append(locality)
    elif pincode:
        lines.append(f"PIN: {pincode}")
    return '\n'.join([l for l in lines if l])


DOWNLOAD_PATH = "/invoice/download"
BULK_DOWNLOAD_PATH = "/invoice/bulk-download"


def _download_link(row: dict, user_id: str, org_id: Optional[str]) -> str:
    """`download_url` of an invoice row: a signed, expiring link (15 minutes) when AUTH_TOKEN_SECRET is configured
    (utils/signed_urls.py), else the plain link of before."""
    params = {"user_id": row.get("user_id", user_id), "org_id": row.get("org_id", org_id) or "",
              "invoice_number": row.get("invoice_number") or ""}
    signed = signed_urls.sign_url(DOWNLOAD_PATH, params)
    if signed:
        return signed
    return f"{DOWNLOAD_PATH}?user_id={params['user_id']}&org_id={params['org_id']}&invoice_number={params['invoice_number']}"


def _invoice_row_to_dict(row: dict, user_id: str, org_id: Optional[str] = None) -> dict:
    """Normalize an invoice-table row for API responses."""
    return {
        "invoice_id": row.get("invoice_id"),
        "invoice_number": row.get("invoice_number"),
        "user_id": row.get("user_id", user_id),
        "org_id": row.get("org_id", org_id),
        "customer_name": row.get("customer_name"),
        "org_name": row.get("org_name"),
        "plan_name": row.get("plan_name"),
        "staff_count": row.get("staff_count"),
        "base_amount": float(row["base_amount"]) if row.get("base_amount") is not None else 0,
        "cgst": float(row["cgst"]) if row.get("cgst") is not None else 0,
        "sgst": float(row["sgst"]) if row.get("sgst") is not None else 0,
        "igst": float(row["igst"]) if row.get("igst") is not None else 0,
        "total_amount": float(row["total_amount"]) if row.get("total_amount") is not None else 0,
        "payment_id": row.get("payment_id"),
        "order_id": row.get("order_id"),
        "payment_date": str(row["payment_date"]) if row.get("payment_date") else None,
        "valid_till": str(row["valid_till"]) if row.get("valid_till") else None,
        "created_at": str(row["created_at"]) if row.get("created_at") else None,
        "download_url": _download_link(row, user_id, org_id),
    }
 
 
# ── Amount → Indian-English words ─────────────────────────────────────────────
def _num_to_words(n: float) -> str:
    ones = ['', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight',
            'Nine', 'Ten', 'Eleven', 'Twelve', 'Thirteen', 'Fourteen', 'Fifteen',
            'Sixteen', 'Seventeen', 'Eighteen', 'Nineteen']
    tens = ['', '', 'Twenty', 'Thirty', 'Forty', 'Fifty',
            'Sixty', 'Seventy', 'Eighty', 'Ninety']
 
    def below1000(x):
        if x < 20:   return ones[x]
        if x < 100:  return tens[x // 10] + (' ' + ones[x % 10] if x % 10 else '')
        return ones[x // 100] + ' Hundred' + (' ' + below1000(x % 100) if x % 100 else '')
 
    def convert(x):
        if x < 1000:      return below1000(x)
        if x < 100000:    return below1000(x // 1000) + ' Thousand' + (' ' + below1000(x % 1000) if x % 1000 else '')
        if x < 10000000:  return below1000(x // 100000) + ' Lakh'   + (' ' + convert(x % 100000) if x % 100000 else '')
        return below1000(x // 10000000) + ' Crore' + (' ' + convert(x % 10000000) if x % 10000000 else '')
 
    rupees = int(n)
    paise  = round((n - rupees) * 100)
    result = 'Indian Rupee ' + convert(rupees) if rupees else ''
    if paise:
        result += (' and ' if result else 'Indian Rupee ') + convert(paise) + ' Paise'
    return (result + ' Only') if result else 'Zero'
 
 
# ─────────────────────────────────────────────────────────────────────────────
def _generate_invoice_pdf(invoice_data: dict) -> bytes:
    """
    Generates a professional TAX INVOICE PDF matching the Zoho-style layout:
 
      • Header  : logo + company details (left)  |  TAX INVOICE (right, large)
      • Divider line
      • Meta    : Invoice No / Date / Terms / Due Date  |  Place of Supply
      • Divider line
      • Address : Bill To (left)  |  Ship To (right)
      • Items   : # | Item & Description | Qty | Rate | Amount
      • Bottom  : Total In Words + Terms & Conditions (left)
                  Summary totals + Authorized Signature (right)
 
    invoice_data keys
    -----------------
    company_name, company_address, company_gstin
    invoice_number, payment_date, due_date, terms, place_of_supply
    customer_name, bill_address, ship_address
    items          : list of dicts {name, description, sac, qty, unit, rate, amount}
    base_amount, discount, igst_pct, igst, cgst, sgst, total_amount
    authorized_by
    terms_conditions, new_plan, previous_plan
    """
 
    # ── Colour tokens ────────────────────────────────────────────
    BLACK  = colors.HexColor('#000000')
    MGRAY  = colors.HexColor('#555555')
    LGRAY  = colors.HexColor('#888888')
    BGRAY  = colors.HexColor('#F5F5F5')   # table header / alt-row / address bg
    BORDER = colors.HexColor('#CCCCCC')
    WHITE  = colors.white
    RS     = '₹'                           # requires DejaVu font
 
    PAGE_W = A4[0] - 30 * mm   # 180 mm usable width
 
    LOGO_PATH = branding.logo_path()   # static/<BRAND_LOGO_FILE>; text mark when the file is absent
 
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        rightMargin=15 * mm, leftMargin=15 * mm,
        topMargin=12 * mm, bottomMargin=12 * mm,
    )
    story = []
 
    # ── Style helper ──────────────────────────────────────────────
    def ps(name, size, bold=False, color=BLACK, align=TA_LEFT, leading=None):
        return ParagraphStyle(
            name, fontSize=size,
            fontName=_FONT_BOLD if bold else _FONT,
            textColor=color, alignment=align,
            leading=leading or size * 1.35,
            spaceAfter=0, spaceBefore=0,
        )
 
    # ═════════════════════════════════════════════════════════════
    # 1.  HEADER  —  logo + company (left)  |  TAX INVOICE (right)
    # ═════════════════════════════════════════════════════════════
    # Use os.path.exists to avoid lazy-load exception from RLImage
    if os.path.exists(LOGO_PATH):
        logo_el = RLImage(LOGO_PATH, width=20 * mm, height=20 * mm, kind='proportional')
    else:
        logo_el = Paragraph(f'<b>{_xml(branding.initials())}</b>', ps('gi', 16, bold=True,
                                            color=colors.HexColor('#2563EB')))
 
    # Customer GSTIN (when the caller supplies one) is printed under Bill To below; the seller's own
    # legal-entity lines come from branding (SELLER_* env values), never from the invoice data.
    company_gstin = invoice_data.get('company_gstin', '')

    addr_text = '<br/>'.join(_xml(line) for line in _seller_lines())

    company_block = Table(
        [[Paragraph(f'<b>{_xml(branding.seller_name())}</b>',
                    ps('cn', 10, bold=True, color=BLACK))],
         [Paragraph(addr_text, ps('ca', 7, color=MGRAY, leading=10))]],
        colWidths=[108 * mm],
    )
    company_block.setStyle(TableStyle([
        ('TOPPADDING',   (0, 0), (-1, -1), 1),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 1),
        ('LEFTPADDING',  (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
    ]))
 
    left_block = Table([[logo_el, company_block]], colWidths=[22 * mm, 108 * mm])
    left_block.setStyle(TableStyle([
        ('VALIGN',       (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING',  (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (0, -1),  3),
        ('RIGHTPADDING', (1, 0), (1, -1),  4),
        ('TOPPADDING',   (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 0),
    ]))
 
    header = Table(
        [[left_block,
          Paragraph('TAX INVOICE', ps('ti', 24, color=BLACK, align=TA_RIGHT))]],
        colWidths=[135 * mm, 65 * mm],
    )
    header.setStyle(TableStyle([
        ('VALIGN',       (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING',  (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING',   (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 0),
    ]))
    story.append(header)
    story.append(Spacer(1, 4 * mm))
    story.append(HRFlowable(width='100%', thickness=0.8, color=BORDER))
    story.append(Spacer(1, 3 * mm))
 
    # ═════════════════════════════════════════════════════════════
    # 2.  INVOICE META
    # ═════════════════════════════════════════════════════════════
    invoice_number  = invoice_data.get('invoice_number', 'N/A')
    payment_date    = invoice_data.get('payment_date', 'N/A')
    due_date        = invoice_data.get('due_date', payment_date)
    terms           = invoice_data.get('terms', 'Net 0')
    place_of_supply = invoice_data.get('place_of_supply') or branding.seller_place_of_supply() or 'N/A'
 
    def mrow(label, value):
        return [Paragraph(label, ps(f'ml_{label}', 8, color=MGRAY)),
                Paragraph(f': {value}', ps(f'mv_{label}', 8, bold=True, color=BLACK))]
 
    meta_left = Table(
        [mrow('Invoice No.',   invoice_number),
         mrow('Invoice Date',  payment_date),
         mrow('Terms',         terms),
         mrow('Due Date',      due_date)],
        colWidths=[30 * mm, 65 * mm],
    )
    meta_left.setStyle(TableStyle([
        ('TOPPADDING',   (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 2),
        ('LEFTPADDING',  (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
    ]))
 
    meta_right = Table(
        [[Paragraph('Place Of Supply', ps('posl', 8, color=MGRAY)),
          Paragraph(f': {place_of_supply}', ps('posv', 8, bold=True, color=BLACK))]],
        colWidths=[30 * mm, 55 * mm],
    )
    meta_right.setStyle(TableStyle([
        ('TOPPADDING',   (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 2),
        ('LEFTPADDING',  (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
    ]))
 
    story.append(Table(
        [[meta_left, meta_right]], colWidths=[95 * mm, 85 * mm],
        style=[('VALIGN',      (0, 0), (-1, -1), 'TOP'),
               ('LEFTPADDING', (0, 0), (-1, -1), 0),
               ('RIGHTPADDING',(0, 0), (-1, -1), 0),
               ('TOPPADDING',  (0, 0), (-1, -1), 0),
               ('BOTTOMPADDING',(0,0), (-1, -1), 0)],
    ))
    story.append(Spacer(1, 3 * mm))
    story.append(HRFlowable(width='100%', thickness=0.8, color=BORDER))
    story.append(Spacer(1, 3 * mm))
 
    # ═════════════════════════════════════════════════════════════
    # 3.  BILL TO  |  SHIP TO
    # ═════════════════════════════════════════════════════════════
    customer_name = invoice_data.get('customer_name', 'N/A')
    bill_address  = invoice_data.get('bill_address', 'N/A')
    ship_address  = invoice_data.get('ship_address', bill_address)
    HW = PAGE_W / 2
 
    bill_c = Table(
        [[Paragraph('<b>Bill To</b>', ps('bth', 9, bold=True, color=BLACK))],
         [Spacer(1, 1 * mm)],
         [Paragraph(f'<b>{customer_name}</b>', ps('bn', 9, bold=True, color=BLACK))],
         [Paragraph(bill_address.replace('\n', '<br/>'), ps('ba', 8, color=MGRAY, leading=12))]]
        + ([[Paragraph(f'GSTIN: {_xml(company_gstin)}', ps('bg', 8, color=MGRAY, leading=12))]]
           if company_gstin else []),
        colWidths=[HW - 14 * mm],
    )
    bill_c.setStyle(TableStyle([
        ('TOPPADDING',   (0, 0), (-1, -1), 1),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 1),
        ('LEFTPADDING',  (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
    ]))
 
    ship_c = Table(
        [[Paragraph('<b>Ship To</b>', ps('sth', 9, bold=True, color=BLACK))],
         [Spacer(1, 1 * mm)],
         [Paragraph(ship_address.replace('\n', '<br/>'), ps('sa', 8, color=MGRAY, leading=12))]],
        colWidths=[HW - 14 * mm],
    )
    ship_c.setStyle(TableStyle([
        ('TOPPADDING',   (0, 0), (-1, -1), 1),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 1),
        ('LEFTPADDING',  (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
    ]))
 
    addr_card = Table([[bill_c, ship_c]], colWidths=[HW, HW])
    addr_card.setStyle(TableStyle([
        ('BOX',          (0, 0), (-1, -1), 0.5, BORDER),
        ('BACKGROUND',   (0, 0), (-1, -1), BGRAY),
        ('LINEAFTER',    (0, 0), (0, -1),  0.5, BORDER),
        ('TOPPADDING',   (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 8),
        ('LEFTPADDING',  (0, 0), (-1, -1), 10),
        ('RIGHTPADDING', (0, 0), (-1, -1), 10),
        ('VALIGN',       (0, 0), (-1, -1), 'TOP'),
    ]))
    story.append(addr_card)
    story.append(Spacer(1, 4 * mm))
 
    # ═════════════════════════════════════════════════════════════
    # 4.  LINE ITEMS  —  # | Item & Description | Qty | Rate | Amount
    # ═════════════════════════════════════════════════════════════
    items    = invoice_data.get('items', [])
    COL_W    = [8 * mm, 90 * mm, 22 * mm, 28 * mm, 32 * mm]
 
    def th(text, align=TA_LEFT):
        return Paragraph(f'<b>{text}</b>', ps(f'th_{text}', 8, bold=True, color=BLACK, align=align))
 
    rows = [[th('#'), th('Item & Description'), th('Qty', TA_CENTER),
             th('Rate', TA_RIGHT), th('Amount', TA_RIGHT)]]
 
    for idx, item in enumerate(items, 1):
        # Description cell: bold name + sub-text description + SAC
        desc = f'<b>{item.get("name", "")}</b>'
        if item.get('description'):
            desc += f'<br/><font size="7.5" color="#555555">{item["description"]}</font>'
        if item.get('sac'):
            desc += f'<br/><font size="7" color="#888888">SAC: {item["sac"]}</font>'
 
        # Qty cell: number + unit on second line
        qty = str(item.get('qty', ''))
        if item.get('unit'):
            qty += f'<br/><font size="7" color="#888888">{item["unit"]}</font>'
 
        rows.append([
            Paragraph(str(idx),                    ps(f'n{idx}', 8, color=MGRAY, align=TA_CENTER)),
            Paragraph(desc,                        ps(f'd{idx}', 8, color=BLACK, leading=12)),
            Paragraph(qty,                         ps(f'q{idx}', 8, color=BLACK, align=TA_CENTER)),
            Paragraph(f'{item.get("rate",0):,.2f}',   ps(f'r{idx}', 8, color=BLACK, align=TA_RIGHT)),
            Paragraph(f'{item.get("amount",0):,.2f}', ps(f'a{idx}', 8, color=BLACK, align=TA_RIGHT)),
        ])
 
    item_style = [
        ('BACKGROUND',   (0, 0), (-1, 0),  BGRAY),
        ('BOX',          (0, 0), (-1, -1), 0.5, BORDER),
        ('INNERGRID',    (0, 0), (-1, -1), 0.3, BORDER),
        ('LINEBELOW',    (0, 0), (-1, 0),  0.8, BORDER),
        ('VALIGN',       (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING',   (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 6),
        ('LEFTPADDING',  (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
    ]
    # Alternate row shading for data rows
    for r in range(1, len(rows)):
        if r % 2 == 0:
            item_style.append(('BACKGROUND', (0, r), (-1, r), BGRAY))
 
    items_tbl = Table(rows, colWidths=COL_W)
    items_tbl.setStyle(TableStyle(item_style))
    story.append(items_tbl)
    story.append(Spacer(1, 4 * mm))
 
    # ═════════════════════════════════════════════════════════════
    # 5.  BOTTOM SECTION
    #     Left : Total In Words  +  Terms & Conditions / New Plan / Previous Plan
    #     Right: Summary totals  +  Authorized Signature
    # ═════════════════════════════════════════════════════════════
    base_amount  = float(invoice_data.get('base_amount',  0))
    discount     = float(invoice_data.get('discount',     0))
    igst_pct     = invoice_data.get('igst_pct', 0)
    igst         = float(invoice_data.get('igst',         0))
    cgst         = float(invoice_data.get('cgst',         0))
    sgst         = float(invoice_data.get('sgst',         0))
    total_amount = float(invoice_data.get('total_amount', 0))
    terms_text   = invoice_data.get('terms_conditions', '')
    new_plan     = invoice_data.get('new_plan', '')
    prev_plan    = invoice_data.get('previous_plan', '')
    amount_words = _num_to_words(total_amount)
 
    # ── Left column ───────────────────────────────────────────────
    lrows = [
        [Paragraph('<b>Total In Words</b>', ps('tiwh', 8, bold=True, color=BLACK))],
        [Paragraph(f'<i>{amount_words}</i>', ps('tiwv', 8, color=BLACK, leading=12))],
        [Spacer(1, 4 * mm)],
    ]
 
    if terms_text or new_plan or prev_plan:
        lrows.append([Paragraph('<b>Terms &amp; Conditions</b>',
                                ps('tch', 8, bold=True, color=BLACK))])
        for line in (terms_text or '').split('\n'):
            if line.strip():
                lrows.append([Paragraph(line.strip(), ps('tcv', 8, color=MGRAY, leading=12))])
 
        if new_plan:
            lrows.append([Spacer(1, 2 * mm)])
            lrows.append([Paragraph('<b>New Plan</b>', ps('nph', 8, bold=True, color=BLACK))])
            for line in new_plan.split('\n'):
                if line.strip():
                    lrows.append([Paragraph(line.strip(), ps('npv', 8, color=MGRAY, leading=12))])
 
        if prev_plan:
            lrows.append([Spacer(1, 2 * mm)])
            for line in prev_plan.split('\n'):
                if line.strip():
                    lrows.append([Paragraph(line.strip(), ps('ppv', 8, color=MGRAY, leading=12))])
 
    left_tbl = Table(lrows, colWidths=[97 * mm])
    left_tbl.setStyle(TableStyle([
        ('TOPPADDING',   (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 2),
        ('LEFTPADDING',  (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('VALIGN',       (0, 0), (-1, -1), 'TOP'),
    ]))
 
    # ── Summary totals ────────────────────────────────────────────
    srows = []
    srows.append([Paragraph('Sub Total', ps('ss', 8, color=BLACK)),
                  Paragraph(f'{base_amount:,.2f}', ps('sv', 8, color=BLACK, align=TA_RIGHT))])
 
    if discount:
        srows.append([Paragraph('Discount', ps('ds', 8, color=BLACK)),
                      Paragraph(f'(-) {discount:,.2f}', ps('dv', 8, color=BLACK, align=TA_RIGHT))])
 
    if igst:
        lbl = f'IGST{igst_pct} ({igst_pct}%)' if igst_pct else 'IGST'
        srows.append([Paragraph(lbl, ps('is', 8, color=BLACK)),
                      Paragraph(f'{igst:,.2f}', ps('iv', 8, color=BLACK, align=TA_RIGHT))])
 
    if cgst:
        srows.append([Paragraph('CGST (9%)', ps('cs2', 8, color=BLACK)),
                      Paragraph(f'{cgst:,.2f}', ps('cv2', 8, color=BLACK, align=TA_RIGHT))])
 
    if sgst:
        srows.append([Paragraph('SGST (9%)', ps('gs2', 8, color=BLACK)),
                      Paragraph(f'{sgst:,.2f}', ps('gv2', 8, color=BLACK, align=TA_RIGHT))])
 
    srows.append([
        Paragraph('<b>Total</b>', ps('ts', 10, bold=True, color=BLACK)),
        Paragraph(f'<b>{RS} {total_amount:,.2f}</b>',
                  ps('tv', 10, bold=True, color=BLACK, align=TA_RIGHT)),
    ])
 
    sum_style = [
        ('BOX',          (0, 0), (-1, -1), 0.5, BORDER),
        ('LINEABOVE',    (0, -1),(-1, -1), 0.8, BORDER),
        ('TOPPADDING',   (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 5),
        ('LEFTPADDING',  (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('ALIGN',        (1, 0), (1, -1),  'RIGHT'),
        ('VALIGN',       (0, 0), (-1, -1), 'MIDDLE'),
    ]
    for r in range(len(srows) - 1):
        sum_style.append(('LINEBELOW', (0, r), (-1, r), 0.3, BORDER))
 
    sum_tbl = Table(srows, colWidths=[45 * mm, 36 * mm])
    sum_tbl.setStyle(TableStyle(sum_style))

        # ── Authorized Signature ──────────────────────────────────────
    SIGN_PATH = branding.signature_path()   # static/<BRAND_SIGNATURE_FILE>; blank space when absent
    if os.path.exists(SIGN_PATH):
        _img = RLImage(SIGN_PATH, width=40 * mm, height=15 * mm, kind='proportional')
        _img.hAlign = 'CENTER'
        sign_cell = _img
    else:
        sign_cell = Spacer(1, 15 * mm)
    sig_tbl = Table(
        [[sign_cell],
         [Spacer(1, 2 * mm)],
         [HRFlowable(width='100%', thickness=0.5, color=BORDER)],
         [Paragraph('Authorized Signature', ps('sl', 8, color=LGRAY, align=TA_CENTER))]],
        colWidths=[81 * mm],
    )
    sig_tbl.setStyle(TableStyle([
        ('BOX',          (0, 0), (-1, -1), 0.5, BORDER),
        ('TOPPADDING',   (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 4),
        ('LEFTPADDING',  (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('ALIGN',        (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN',       (0, 0), (-1, -1), 'MIDDLE'),
    ]))
 
    right_tbl = Table(
        [[sum_tbl], [Spacer(1, 4 * mm)], [sig_tbl]],
        colWidths=[81 * mm],
    )
    right_tbl.setStyle(TableStyle([
        ('TOPPADDING',   (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 0),
        ('LEFTPADDING',  (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('VALIGN',       (0, 0), (-1, -1), 'TOP'),
    ]))
 
    story.append(Table(
        [[left_tbl, right_tbl]], colWidths=[99 * mm, 81 * mm],
        style=[('VALIGN',       (0, 0), (-1, -1), 'TOP'),
               ('LEFTPADDING',  (0, 0), (-1, -1), 0),
               ('RIGHTPADDING', (0, 0), (-1, -1), 0),
               ('TOPPADDING',   (0, 0), (-1, -1), 0),
               ('BOTTOMPADDING',(0, 0), (-1, -1), 0)],
    ))
 
    doc.build(story)
    return buffer.getvalue()
 
 
# ─────────────────────────────────────────────────────────────────────────────
 
def _build_items_html(items: list) -> str:
    """Build items table rows - avoids nested f-string format spec issues"""
    rows = []
    for i, item in enumerate(items, 1):
        bg = '#F8FAFC' if i % 2 == 0 else '#fff'
        rate = '{:,.2f}'.format(item.get('rate', 0))
        amount = '{:,.2f}'.format(item.get('amount', 0))
        desc_html = ''
        if item.get('description'):
            desc_html += f'<br/><span style="font-size:11px;color:#64748b;">{item["description"]}</span>'
        if item.get('sac'):
            desc_html += f'<br/><span style="font-size:10px;color:#94a3b8;">SAC: {item["sac"]}</span>'
        qty_html = str(item.get('qty', ''))
        if item.get('unit'):
            qty_html += f'<br/><span style="font-size:10px;color:#94a3b8;">{item["unit"]}</span>'
        rows.append(f"""
            <tr style="background:{bg};">
              <td style="padding:8px 10px;border:1px solid #E2E8F0;color:#64748b;">{i}</td>
              <td style="padding:8px 10px;border:1px solid #E2E8F0;"><b>{item.get('name','')}</b>{desc_html}</td>
              <td style="padding:8px 10px;border:1px solid #E2E8F0;text-align:center;">{qty_html}</td>
              <td style="padding:8px 10px;border:1px solid #E2E8F0;text-align:right;">{rate}</td>
              <td style="padding:8px 10px;border:1px solid #E2E8F0;text-align:right;">{amount}</td>
            </tr>""")
    return ''.join(rows)


def _send_email_async(to_email: str, invoice_data: dict):
    try:
        email_user = os.getenv('EMAIL_USER')
        ses_region = os.getenv('AWS_SES_REGION', 'ap-south-1')
 
        if not email_user:
            logger.error("EMAIL_USER not configured")
            return
 
        pdf_bytes = _generate_invoice_pdf(invoice_data)
 
        msg = MIMEMultipart('mixed')
        msg['Subject'] = (
            f"Payment Successful - "
            f"{invoice_data.get('plan_name', 'Subscription')} Plan Invoice"
        )
        msg['From'] = email_user
        msg['To']   = to_email
 
        base_amount  = invoice_data.get('base_amount', 0)
        total_amount = invoice_data.get('total_amount', 0)

        app_name_html     = _xml(branding.app_name())
        seller_name_html  = _xml(branding.seller_name())
        seller_lines_html = '<br/>'.join(_xml(line) for line in _seller_lines())
        footer_html       = _invoice_footer_html()

        html_body = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Invoice – {app_name_html}</title>
</head>
<body style="margin:0;padding:0;font-family:Arial,sans-serif;background:#e5e7eb;">
  <div style="padding:24px;">
    <div style="max-width:680px;margin:0 auto;background:#fff;border-radius:10px;
                box-shadow:0 6px 20px rgba(0,0,0,0.09);overflow:hidden;">
 
      <!-- Header -->
      <div style="background:#1E293B;padding:24px 32px;">
        <table style="width:100%;border-collapse:collapse;">
          <tr>
            <td style="font-size:18px;font-weight:700;color:#fff;">
              {seller_name_html}</td>
            <td style="text-align:right;font-size:20px;font-weight:800;
                       color:#fff;letter-spacing:1px;">TAX INVOICE</td>
          </tr>
          <tr>
            <td style="font-size:10px;color:#94a3b8;padding-top:2px;line-height:1.5;">
              {seller_lines_html}</td>
            <td style="text-align:right;font-size:11px;color:#94a3b8;padding-top:2px;">
              # {invoice_data.get('invoice_number','N/A')}</td>
          </tr>
        </table>
      </div>
      <div style="height:3px;background:#2563EB;"></div>
 
      <!-- Meta -->
      <div style="padding:16px 32px;background:#F8FAFC;border-bottom:1px solid #E2E8F0;">
        <table style="width:100%;font-size:12px;border-collapse:collapse;">
          <tr>
            <td style="color:#64748b;padding:2px 0;">Invoice No.</td>
            <td style="font-weight:700;color:#1E293B;padding:2px 8px;">
              {invoice_data.get('invoice_number','N/A')}</td>
            <td style="color:#64748b;padding:2px 0;">Place of Supply</td>
            <td style="font-weight:700;color:#1E293B;padding:2px 8px;">
              {invoice_data.get('place_of_supply') or branding.seller_place_of_supply() or 'N/A'}</td>
          </tr>
          <tr>
            <td style="color:#64748b;padding:2px 0;">Invoice Date</td>
            <td style="font-weight:700;color:#1E293B;padding:2px 8px;">
              {invoice_data.get('payment_date','N/A')}</td>
            <td style="color:#64748b;padding:2px 0;">Due Date</td>
            <td style="font-weight:700;color:#1E293B;padding:2px 8px;">
              {invoice_data.get('due_date', invoice_data.get('payment_date','N/A'))}</td>
          </tr>
        </table>
      </div>
 
      <!-- Bill To / Ship To -->
      <table style="width:100%;border-collapse:collapse;border-bottom:1px solid #E2E8F0;">
        <tr>
          <td style="width:50%;padding:16px 24px;background:#F8FAFC;
                     border-right:1px solid #E2E8F0;vertical-align:top;">
            <div style="font-size:10px;font-weight:700;color:#1E293B;
                        text-transform:uppercase;margin-bottom:6px;">Bill To</div>
            <div style="font-size:13px;font-weight:700;color:#1E293B;">
              {invoice_data.get('customer_name','N/A')}</div>
            {f'<div style="font-size:11px;color:#64748b;margin-top:3px;">Phone: {invoice_data.get("phone_number","N/A")}</div>' if invoice_data.get('phone_number') else ''}
            {f'<div style="font-size:11px;color:#64748b;margin-top:3px;">Email: {invoice_data.get("customer_email", invoice_data.get("email", "N/A"))}</div>' if invoice_data.get('customer_email') or invoice_data.get('email') else ''}
            <div style="font-size:11px;color:#64748b;margin-top:3px;line-height:1.6;">
              {invoice_data.get('bill_address','').replace(chr(10),'<br/>')}</div>
          </td>
          <td style="width:50%;padding:16px 24px;background:#F8FAFC;vertical-align:top;">
            <div style="font-size:10px;font-weight:700;color:#1E293B;
                        text-transform:uppercase;margin-bottom:6px;">Ship To</div>
            <div style="font-size:11px;color:#64748b;line-height:1.6;">
              {invoice_data.get('ship_address', invoice_data.get('bill_address','')).replace(chr(10),'<br/>')}</div>
          </td>
        </tr>
      </table>
 
      <!-- Items -->
      <div style="padding:20px 24px 0;">
        <table style="width:100%;border-collapse:collapse;font-size:12px;">
          <thead>
            <tr style="background:#F1F5F9;">
              <th style="padding:8px 10px;text-align:left;border:1px solid #E2E8F0;">#</th>
              <th style="padding:8px 10px;text-align:left;border:1px solid #E2E8F0;">
                Item &amp; Description</th>
              <th style="padding:8px 10px;text-align:center;border:1px solid #E2E8F0;">Qty</th>
              <th style="padding:8px 10px;text-align:right;border:1px solid #E2E8F0;">Rate</th>
              <th style="padding:8px 10px;text-align:right;border:1px solid #E2E8F0;">Amount</th>
            </tr>
          </thead>
          <tbody>
            {_build_items_html(invoice_data.get('items', []))}
          </tbody>
        </table>
      </div>
 
      <!-- Totals -->
      <div style="padding:0 24px 20px;">
        <table style="width:100%;font-size:12px;border-collapse:collapse;margin-top:0;">
          <tr><td style="padding:6px 10px;border:1px solid #E2E8F0;color:#64748b;">
            Sub Total</td>
            <td style="padding:6px 10px;border:1px solid #E2E8F0;text-align:right;">
              Rs. {base_amount:,.2f}</td></tr>
          {f'<tr><td style="padding:6px 10px;border:1px solid #E2E8F0;color:#64748b;">Discount</td><td style="padding:6px 10px;border:1px solid #E2E8F0;text-align:right;">(-) Rs. {invoice_data.get("discount",0):,.2f}</td></tr>' if invoice_data.get('discount') else ''}
          {f'<tr><td style="padding:6px 10px;border:1px solid #E2E8F0;color:#64748b;">IGST ({invoice_data.get("igst_pct","")}%)</td><td style="padding:6px 10px;border:1px solid #E2E8F0;text-align:right;">Rs. {invoice_data.get("igst",0):,.2f}</td></tr>' if invoice_data.get('igst') else ''}
          {f'<tr><td style="padding:6px 10px;border:1px solid #E2E8F0;color:#64748b;">CGST (9%)</td><td style="padding:6px 10px;border:1px solid #E2E8F0;text-align:right;">Rs. {invoice_data.get("cgst",0):,.2f}</td></tr>' if invoice_data.get('cgst') else ''}
          {f'<tr><td style="padding:6px 10px;border:1px solid #E2E8F0;color:#64748b;">SGST (9%)</td><td style="padding:6px 10px;border:1px solid #E2E8F0;text-align:right;">Rs. {invoice_data.get("sgst",0):,.2f}</td></tr>' if invoice_data.get('sgst') else ''}
          <tr style="font-weight:700;font-size:13px;">
            <td style="padding:10px;border:1px solid #E2E8F0;">Total</td>
            <td style="padding:10px;border:1px solid #E2E8F0;text-align:right;">
              Rs. {total_amount:,.2f}</td>
          </tr>
        </table>
      </div>
 
      <!-- Footer -->
      <div style="background:#F8FAFC;border-top:1px solid #E2E8F0;padding:14px 32px;
                  text-align:center;">
        <p style="margin:0;font-size:12px;color:#64748b;">
          {footer_html}
        </p>
      </div>
 
    </div>
  </div>
</body>
</html>"""
 
        msg.attach(MIMEText(html_body, 'html'))
 
        pdf_part = MIMEApplication(pdf_bytes, _subtype='pdf')
        pdf_part.add_header('Content-Disposition', 'attachment',
                            filename=branding.invoice_pdf_filename())
        msg.attach(pdf_part)
 
        ses = boto3.client('ses', region_name=ses_region)
        ses.send_raw_email(
            Source=email_user,
            Destinations=[to_email],
            RawMessage={'Data': msg.as_string()},
        )
        logger.info(f"Invoice email sent to {to_email}")
 
    except Exception as e:
        logger.error(f"Failed to send email to {to_email}: {str(e)}")
 
 
def send_invoice_email(to_email: str, invoice_data: dict):
    """Fire-and-forget: send invoice email in a background thread."""
    thread = threading.Thread(target=_send_email_async, args=(to_email, invoice_data))
    thread.daemon = True
    thread.start()
    logger.info(f"Email sending initiated for {to_email}")
 
 
# ─────────────────────────────────────────────────────────────────────────────
 
@router.get("/details")
async def get_invoice_details(user_id: str, org_id: Optional[str] = None):
    conn   = None
    cursor = None
    try:
        conn   = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
 
        cursor.execute("""
            SELECT
                u.name              AS client_name,
                u.mobile_number     AS phone,
                u.email,
                ps.start_date       AS plan_start_date,
                ps.end_date         AS plan_end_date,
                gp.plan_name,
                gp.plan_month       AS plan_duration_months,
                gi.total_amount     AS amount_paid,
                gi.invoice_number,
                gi.base_amount,
                gi.cgst,
                gi.sgst,
                gi.payment_date,
                gi.igst,
                gi.razorpay_payment_id AS payment_id,
                gi.razorpay_order_id   AS order_id,
                gi.valid_till,
                gi.created_at
            FROM users u
            LEFT JOIN org_info             o   ON u.org_id   = o.org_id
            LEFT JOIN punchapp_subscription ps  ON u.org_id   = ps.org_id
                                               AND ps.status  = 'ACTIVE'
            LEFT JOIN gen_plans             gp  ON ps.plan_id  = gp.plan_id
            LEFT JOIN gen_invoices          gi  ON gi.user_id  = u.user_id
            WHERE u.user_id = %s
              AND (%s IS NULL OR gi.org_id = %s)
            ORDER BY gi.created_at DESC
            LIMIT 1
        """, (user_id, org_id, org_id))

        result = cursor.fetchone()
        if not result:
            return {"success": False, "message": "No invoice found"}
 
        plan_name = result['plan_name'] or ''
        pln_lower = plan_name.lower()
        if 'business' in pln_lower:
            subscription_type = 'Business Premium'
        elif 'premium' in pln_lower:
            subscription_type = 'Premium'
        elif 'standard' in pln_lower:
            subscription_type = 'Standard'
        elif 'basic' in pln_lower:
            subscription_type = 'Basic'
        else:
            subscription_type = plan_name
 
        months        = result['plan_duration_months'] or 1
        plan_duration = '1 Month' if months == 1 else f'{months} Months'
 
        total_amount = float(result['amount_paid'])  if result['amount_paid']  else 0
        base_amount  = float(result['base_amount'])  if result['base_amount']  else total_amount / 1.18
        igst         = float(result.get('igst') or 0)    # inter-state invoice: no CGST / SGST to back-fill
        cgst         = float(result['cgst'])         if result['cgst']         else (0.0 if igst else base_amount * 0.09)
        sgst         = float(result['sgst'])         if result['sgst']         else (0.0 if igst else base_amount * 0.09)
 
        return {
            "success": True,
            "data": {
                "client_name":       result['client_name'],
                "phone":             result['phone'],
                "email":             result['email'],
                "invoice_number":    result.get('invoice_number', 'N/A'),
                "payment_id":        result.get('payment_id', 'N/A'),
                "payment_date":      str(result['payment_date']) if result.get('payment_date') else None,
                "subscription_type": subscription_type,
                "plan_duration":     plan_duration,
                "plan_start_date":   str(result['plan_start_date']) if result['plan_start_date'] else None,
                "plan_end_date":     str(result['plan_end_date'])   if result['plan_end_date']   else None,
                "plan_amount":       base_amount,
                "cgst":              cgst,
                "sgst":              sgst,
                "amount_paid":       total_amount,
                "order_id":          result.get('order_id', 'N/A'),
                "valid_till":        str(result['valid_till']) if result.get('valid_till') else None,
            },
        }
 
    except Exception as e:
        logger.error("Error fetching invoice: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Error fetching invoice")
    finally:
        if cursor:
            cursor.close()
        if conn:
            return_connection(conn)


@router.get("/history")
async def get_invoice_history(user_id: str, org_id: str):
    conn = None
    cursor = None
    try:
        if not user_id or not org_id:
            raise HTTPException(status_code=400, detail="user_id and org_id are required")

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("""
            SELECT
                gi.invoice_id,
                gi.invoice_number,
                gi.user_id,
                gi.org_id,
                u.name                  AS customer_name,
                o.org_name,
                gi.plan_name,
                gi.staff_count,
                gi.base_amount,
                gi.cgst,
                gi.sgst,
                gi.total_amount,
                gi.igst,
                gi.razorpay_payment_id  AS payment_id,
                gi.razorpay_order_id    AS order_id,
                gi.payment_date,
                gi.valid_till,
                gi.created_at
            FROM gen_invoices gi
            JOIN users u ON u.user_id = gi.user_id
            JOIN org_info o ON o.org_id = gi.org_id
            WHERE gi.user_id = %s
              AND gi.org_id = %s
            ORDER BY gi.created_at DESC, gi.payment_date DESC
        """, (user_id, org_id))

        rows = cursor.fetchall()
        history = [_invoice_row_to_dict(row, user_id, org_id) for row in rows]

        return {
            "success": True,
            "count": len(history),
            "data": history,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error fetching invoice history: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Error fetching invoice history")
    finally:
        if cursor:
            cursor.close()
        if conn:
            return_connection(conn)


def _authorize_invoice_download(request: Request) -> str:
    """Who may fetch an invoice PDF (the route is public in auth_policy, this is its own gate):
      * a valid, unexpired signature (`exp` + `sig` of /invoice/history `download_url`)      -> "signed"
      * REQUIRE_AUTH off: the legacy unsigned call, as before                                -> "open"
      * REQUIRE_AUTH on: a valid app token of an administrator (the organisation of the invoice
        is checked once the row is read)                                                     -> "token"
      * REQUIRE_AUTH on: a platform operator (the admin console: operator session token or the key, see
        core_functions/operator_auth.py); an operator may fetch any organisation's invoice    -> "operator"
    otherwise 401 / 403."""
    state = signed_urls.verify(DOWNLOAD_PATH, dict(request.query_params))
    if state == "ok":
        return "signed"
    if not auth_token.require_auth_enabled():
        return "open"
    identity = getattr(request.state, "identity", None)
    if identity is None:
        # Not an app user: the console sends an operator session token (Authorization) or the operator key.
        # authorize() raises its own 401 / 403 / 429 when that credential is wrong.
        if request.headers.get("authorization") or request.headers.get(operator_auth.KEY_HEADER):
            operator_auth.authorize(request, always_check=True)
            return "operator"
        raise HTTPException(status_code=401, detail="Link expired" if state == "expired" else "Not authenticated",
                            headers={"WWW-Authenticate": "Bearer"})
    if identity.role_id != 1:
        raise HTTPException(status_code=403, detail="Forbidden")
    return "token"


@router.get("/download")
async def download_invoice(request: Request, user_id: str, org_id: Optional[str] = None, invoice_number: Optional[str] = None):
    access = _authorize_invoice_download(request)
    conn   = None
    cursor = None
    try:
        conn   = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
 
        cursor.execute("""
            SELECT
                u.name                         AS customer_name,
                o.org_name, o.org_address, o.state, o.district, o.pincode,
                gi.org_id,
                gi.invoice_number,
                gi.plan_name,
                gi.staff_count,
                gi.base_amount,
                gi.cgst,
                gi.sgst,
                gi.total_amount,
                gi.igst,
                gi.razorpay_payment_id         AS payment_id,
                gi.razorpay_order_id           AS order_id,
                gi.payment_date,
                gi.valid_till
            FROM gen_invoices gi
            JOIN users    u ON u.user_id  = gi.user_id
            JOIN org_info o ON o.org_id   = gi.org_id
            WHERE gi.user_id = %s
              AND (%s IS NULL OR gi.org_id = %s)
              AND (%s IS NULL OR gi.invoice_number = %s)
            ORDER BY gi.created_at DESC
            LIMIT 1
        """, (user_id, org_id, org_id, invoice_number, invoice_number))
 
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="No invoice found")
        if access == "token":
            from core_functions.auth_dependency import assert_same_org
            assert_same_org(request, row['org_id'])          # the invoice must belong to one of the caller's organisations

        base  = float(row['base_amount'])
        cgst  = float(row['cgst'])
        sgst  = float(row['sgst'])
        igst  = float(row.get('igst') or 0)      # inter-state invoice: IGST instead of CGST + SGST
        total = float(row['total_amount'])
 
        cursor.execute("""
            SELECT c.pan_number, c.gst_number, o.org_address
            FROM company_additional_info c
            JOIN org_info o ON o.org_id = c.org_id
            WHERE c.org_id = %s
            LIMIT 1
        """, (row['org_id'],))
        company_row = cursor.fetchone()
 
        company_gstin   = company_row['gst_number']  if company_row and company_row['gst_number']  else ''
        company_address = company_row['org_address'] if company_row and company_row['org_address'] else 'Bengaluru, Karnataka, India'
        full_address = format_full_address(
            row['org_name'], row.get('org_address'), row.get('district'),
            row.get('state'), row.get('pincode')
        )

        invoice_data = {
            'company_name':    row['org_name'],
            'company_address': company_address,
            'company_gstin':   company_gstin,
            'customer_name':   row['customer_name'],
            'org_name':        row['org_name'],
            'bill_address':    full_address,
            'ship_address':    full_address,
            'invoice_number':  row['invoice_number'],
            'plan_name':       row['plan_name'],
            'payment_date':    (row['payment_date'].strftime('%d/%m/%Y')
                                if row['payment_date'] else 'N/A'),
            'due_date':        (row['payment_date'].strftime('%d/%m/%Y')
                                if row['payment_date'] else 'N/A'),
            'valid_till':      (row['valid_till'].strftime('%d %b %Y')
                                if row['valid_till'] else 'N/A'),
            'items': [{
                'name':        branding.invoice_item_name(row['plan_name']),
                'description': '',
                'qty':         row['staff_count'],
                'unit':        'Staff',
                'rate':        round(base / max(row['staff_count'], 1), 2),
                'amount':      base,
            }],
            'base_amount':  base,
            'cgst':         cgst,
            'sgst':         sgst,
            'total_amount': total,
        }
        if igst:
            invoice_data['igst'] = igst
            invoice_data['igst_pct'] = 18
 
        pdf_bytes = _generate_invoice_pdf(invoice_data)
        filename  = f"{row['invoice_number']}.pdf"
 
        return Response(
            content=pdf_bytes,
            media_type='application/pdf',
            headers={'Content-Disposition': f'attachment; filename="{filename}"'},
        )
 
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error generating invoice PDF: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Error generating invoice PDF")
    finally:
        if cursor:
            cursor.close()
        if conn:
            return_connection(conn)


def _build_invoice_summary_workbook(rows: list) -> bytes:
    """Build a single consolidated Excel workbook summarizing all invoices — for audit/filing."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Invoices Summary"

    headers = [
        "Invoice Number", "Payment Date", "Organization", "GST Number",
        "Customer Name", "Customer Email", "Customer Mobile",
        "Plan Name", "Staff Count", "Coupon Code",
        "Base Amount", "CGST", "SGST", "Total Amount",
        "Payment ID", "Order ID", "Valid Till",
    ]
    ws.append(headers)

    header_fill = PatternFill(start_color="1F2937", end_color="1F2937", fill_type="solid")
    header_font = Font(bold=True, color="FFFFFF")
    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center")

    grand_total = 0.0
    for r in rows:
        total = float(r["total_amount"]) if r.get("total_amount") is not None else 0
        grand_total += total
        ws.append([
            r.get("invoice_number") or "",
            r["payment_date"].strftime("%d/%m/%Y") if r.get("payment_date") else "",
            r.get("org_name") or "",
            r.get("org_gst") or "",
            r.get("customer_name") or "",
            r.get("customer_email") or "",
            r.get("customer_mobile") or "",
            r.get("plan_name") or "",
            r.get("staff_count") or 0,
            r.get("coupon_code") or "",
            float(r["base_amount"]) if r.get("base_amount") is not None else 0,
            float(r["cgst"]) if r.get("cgst") is not None else 0,
            float(r["sgst"]) if r.get("sgst") is not None else 0,
            total,
            r.get("payment_id") or "",
            r.get("order_id") or "",
            r["valid_till"].strftime("%d/%m/%Y") if r.get("valid_till") else "",
        ])

    # Totals row
    total_row = ["", "", "", "", "", "", "", "", "", "TOTAL", "", "", "", grand_total, "", "", ""]
    ws.append(total_row)
    last_row = ws.max_row
    ws.cell(row=last_row, column=10).font = Font(bold=True)
    ws.cell(row=last_row, column=14).font = Font(bold=True)

    # Column widths
    widths = [18, 12, 24, 16, 20, 24, 14, 16, 10, 14, 12, 10, 10, 12, 22, 22, 12]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[chr(64 + i) if i <= 26 else "A"].width = w

    ws.freeze_panes = "A2"

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


@router.get("/bulk-download-link", dependencies=[Depends(require_operator)])
async def bulk_download_link(from_date: str, to_date: str):
    """Operator only: a signed, expiring link (15 minutes) for `/invoice/bulk-download`, so the zip can be opened in a
    browser tab or passed to a download manager that cannot send the operator header."""
    url = signed_urls.sign_url(BULK_DOWNLOAD_PATH, {"from_date": from_date, "to_date": to_date})
    if not url:
        raise HTTPException(status_code=503, detail="Signed links need AUTH_TOKEN_SECRET to be configured")
    return {"success": True, "url": url, "expires_in": signed_urls.ttl_seconds()}


@router.get("/bulk-download")
async def bulk_download_invoices(from_date: str, to_date: str, request: Request = None):
    """
    Zip up every invoice PDF issued in [from_date, to_date] (same PDF sent to
    clients via email) plus one consolidated Excel summary — for audit/filing.

    Operator route: the operator guard (core_functions/operator_auth.py) unless the link carries a valid signature
    minted by `/invoice/bulk-download-link`.
    """
    if request is not None and signed_urls.verify(BULK_DOWNLOAD_PATH, dict(request.query_params)) != "ok":
        operator_auth.authorize(request)          # (request is None only for a direct Python call, never over HTTP)
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("""
            SELECT
                gi.invoice_number, gi.plan_name, gi.staff_count,
                gi.base_amount, gi.cgst, gi.sgst, gi.igst, gi.total_amount,
                gi.razorpay_payment_id AS payment_id, gi.razorpay_order_id AS order_id,
                gi.payment_date, gi.valid_till, gi.coupon_code,
                u.name AS customer_name, u.email AS customer_email, u.mobile_number AS customer_mobile,
                o.org_name, o.org_gst, o.org_address, o.state, o.district, o.pincode
            FROM gen_invoices gi
            JOIN users    u ON u.user_id = gi.user_id
            JOIN org_info o ON o.org_id  = gi.org_id
            WHERE gi.payment_date::date BETWEEN %s AND %s
            ORDER BY gi.payment_date ASC
        """, (from_date, to_date))
        rows = cursor.fetchall()

        if not rows:
            raise HTTPException(status_code=404, detail="No invoices found for the selected date range")

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            used_names = set()
            for row in rows:
                base = float(row["base_amount"]) if row["base_amount"] is not None else 0
                cgst = float(row["cgst"]) if row["cgst"] is not None else 0
                sgst = float(row["sgst"]) if row["sgst"] is not None else 0
                total = float(row["total_amount"]) if row["total_amount"] is not None else 0
                staff_count = row["staff_count"] or 1
                full_address = format_full_address(
                    row["org_name"], row.get("org_address"), row.get("district"),
                    row.get("state"), row.get("pincode")
                )

                invoice_data = {
                    "company_name":    row["org_name"],
                    "company_address": row["org_address"] or "Bengaluru, Karnataka, India",
                    "company_gstin":   row["org_gst"] or "",
                    "customer_name":   row["customer_name"],
                    "org_name":        row["org_name"],
                    "bill_address":    full_address,
                    "ship_address":    full_address,
                    "invoice_number":  row["invoice_number"],
                    "plan_name":       row["plan_name"],
                    "payment_date":    row["payment_date"].strftime("%d/%m/%Y") if row["payment_date"] else "N/A",
                    "due_date":        row["payment_date"].strftime("%d/%m/%Y") if row["payment_date"] else "N/A",
                    "valid_till":      row["valid_till"].strftime("%d %b %Y") if row["valid_till"] else "N/A",
                    "items": [{
                        "name":        branding.invoice_item_name(row['plan_name']),
                        "description": "",
                        "qty":         staff_count,
                        "unit":        "Staff",
                        "rate":        round(base / max(staff_count, 1), 2),
                        "amount":      base,
                    }],
                    "base_amount":  base,
                    "cgst":         cgst,
                    "sgst":         sgst,
                    "total_amount": total,
                }
                igst = float(row.get("igst") or 0)
                if igst:
                    invoice_data["igst"] = igst
                    invoice_data["igst_pct"] = 18

                try:
                    pdf_bytes = _generate_invoice_pdf(invoice_data)
                except Exception as e:
                    logger.error(f"Failed to generate PDF for invoice {row['invoice_number']}: {str(e)}")
                    continue

                name = f"{row['invoice_number'] or 'invoice'}.pdf"
                # Guard against duplicate invoice numbers colliding inside the zip
                if name in used_names:
                    name = f"{row['invoice_number']}_{row['payment_id']}.pdf"
                used_names.add(name)
                zf.writestr(name, pdf_bytes)

            excel_bytes = _build_invoice_summary_workbook(rows)
            zf.writestr(branding.invoice_summary_filename(from_date, to_date), excel_bytes)

        zip_bytes = zip_buffer.getvalue()
        zip_filename = branding.invoice_zip_filename(from_date, to_date)

        return Response(
            content=zip_bytes,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{zip_filename}"'},
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error generating bulk invoices: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Error generating bulk invoices")
    finally:
        if cursor:
            cursor.close()
        if conn:
            return_connection(conn)
