"""Render an Invoice to PDF with ReportLab."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .billing import Invoice
from .config import Config, Party

MUTED = colors.HexColor("#6b7280")
STRIPE = colors.HexColor("#f3f4f6")
RULE = colors.HexColor("#e5e7eb")


def _money(v: Decimal, sym: str) -> str:
    return f"{sym}{v:,.2f}"


def _party_html(p: Party, bold_size: int = 10, attn: bool = False) -> str:
    parts = [f'<b><font size="{bold_size}">{escape(p.name)}</font></b>']
    if attn and p.contact:
        parts.append(f"Attn: {escape(p.contact)}")
    parts += [escape(a) for a in p.address]
    parts += [escape(x) for x in (p.email, p.phone) if x]
    return "<br/>".join(parts)


def render_pdf(inv: Invoice, cfg: Config, out: Path) -> Path:
    sym = cfg.currency
    accent = colors.HexColor(cfg.accent_color)
    ss = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=ss["Normal"], fontSize=9, leading=12)
    label = ParagraphStyle("label", parent=body, textColor=MUTED)
    title = ParagraphStyle("title", parent=ss["Title"], alignment=2, fontSize=24, spaceAfter=6)

    out.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(out),
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.7 * inch,
        bottomMargin=0.7 * inch,
        title=f"Invoice {inv.number}",
        author=cfg.business.name,
    )

    width = doc.width - 12  # usable frame width (frames have 6pt padding per side)

    def footer(canvas, _doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(
            0.75 * inch + 6, 0.45 * inch, f"{cfg.business.name} · Invoice {inv.number}"
        )
        canvas.drawRightString(letter[0] - 0.75 * inch - 6, 0.45 * inch, f"Page {_doc.page}")
        canvas.restoreState()

    # Header
    meta = Table(
        [
            ["Invoice #", inv.number],
            ["Invoice date", f"{inv.issue_date:%b %d, %Y}"],
            ["Terms", f"NET {cfg.net_days}"],
            ["Due date", f"{inv.due_date:%b %d, %Y}"],
            ["Service period", f"{inv.period_start:%b %d} - {inv.period_end:%b %d, %Y}"],
        ],
        colWidths=[1.2 * inch, 1.9 * inch],
    )
    meta.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("TEXTCOLOR", (0, 0), (0, -1), MUTED),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("FONTNAME", (1, 3), (1, 3), "Helvetica-Bold"),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    head = Table(
        [[Paragraph(_party_html(cfg.business, 13), body), [Paragraph("INVOICE", title), meta]]],
        colWidths=[width - 3.1 * inch, 3.1 * inch],
    )
    head.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )

    story = [
        head,
        Spacer(1, 18),
        Paragraph("BILL TO", label),
        Paragraph(_party_html(cfg.client, attn=True), body),
        Spacer(1, 18),
    ]

    # Line items
    data = [["Description", "Hours", "Rate", "Amount"]]
    for li in inv.lines:
        data.append(
            [
                Paragraph(escape(li.description), body),
                f"{li.hours:,.2f}",
                _money(li.rate, sym),
                _money(li.amount, sym),
            ]
        )
    n_items = len(data)
    if inv.tax:
        data.append(["", "", "Subtotal", _money(inv.subtotal, sym)])
        data.append(["", "", f"Tax ({inv.tax_percent.normalize():f}%)", _money(inv.tax, sym)])
    data.append(["", f"{inv.hours:,.2f}", "Total due", _money(inv.total, sym)])
    last = len(data) - 1

    items = Table(
        data, colWidths=[width - 3.1 * inch, 0.8 * inch, 1.0 * inch, 1.3 * inch], repeatRows=1
    )
    items.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), accent),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ROWBACKGROUNDS", (0, 1), (-1, n_items - 1), [colors.white, STRIPE]),
                ("LINEABOVE", (0, n_items), (-1, n_items), 1, accent),
                ("FONTNAME", (0, last), (-1, last), "Helvetica-Bold"),
                ("FONTSIZE", (2, last), (-1, last), 11),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story += [items, Spacer(1, 22)]

    # Payment + notes
    story.append(Paragraph("PAYMENT", label))
    pay = [f"Please reference invoice <b>{escape(inv.number)}</b> with your payment."]
    pay += [escape(p) for p in cfg.payment_instructions]
    story.append(Paragraph("<br/>".join(pay), body))
    if cfg.notes:
        story += [Spacer(1, 10), Paragraph(escape(cfg.notes), body)]

    # Time log appendix
    if cfg.include_time_log and inv.entries:
        heading = f"Time log - Invoice {escape(inv.number)}"
        story += [PageBreak(), Paragraph(heading, ss["Heading2"])]
        rows = [["Date", "Project", "Description", "Hours"]]
        for e in inv.entries:
            rows.append(
                [
                    f"{e.date:%m/%d/%Y}",
                    Paragraph(escape(e.project), body),
                    Paragraph(escape(e.description), body),
                    f"{e.hours:.2f}",
                ]
            )
        raw_total = sum((e.hours for e in inv.entries), Decimal(0))
        rows.append(["", "", "Total (before rounding)", f"{raw_total:.2f}"])
        log = Table(
            rows, colWidths=[0.9 * inch, 1.6 * inch, width - 3.3 * inch, 0.8 * inch], repeatRows=1
        )
        log.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), accent),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                    ("ALIGN", (3, 0), (3, -1), "RIGHT"),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LINEBELOW", (0, 1), (-1, -2), 0.25, RULE),
                    ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ]
            )
        )
        story.append(log)

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return out
