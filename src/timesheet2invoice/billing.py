"""Turn time entries into invoice line items and totals."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from .config import Config
from .parsing import Entry

CENT = Decimal("0.01")
FALLBACK_LABEL = "Software consulting services"


@dataclass(frozen=True)
class LineItem:
    description: str
    hours: Decimal
    rate: Decimal

    @property
    def amount(self) -> Decimal:
        return (self.hours * self.rate).quantize(CENT, ROUND_HALF_UP)


@dataclass(frozen=True)
class Invoice:
    number: str
    issue_date: date
    due_date: date
    period_start: date
    period_end: date
    lines: list[LineItem]
    entries: list[Entry]
    tax_percent: Decimal = Decimal(0)

    @property
    def hours(self) -> Decimal:
        return sum((li.hours for li in self.lines), Decimal(0))

    @property
    def subtotal(self) -> Decimal:
        return sum((li.amount for li in self.lines), Decimal(0))

    @property
    def tax(self) -> Decimal:
        return (self.subtotal * self.tax_percent / 100).quantize(CENT, ROUND_HALF_UP)

    @property
    def total(self) -> Decimal:
        return self.subtotal + self.tax


def month_bounds(ym: str) -> tuple[date, date]:
    """'2026-09' -> (2026-09-01, 2026-09-30)."""
    try:
        y, m = (int(p) for p in ym.split("-"))
        start = date(y, m, 1)
    except ValueError as e:
        raise ValueError(f"Month must look like YYYY-MM, got {ym!r}.") from e
    nxt = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
    return start, nxt - timedelta(days=1)


def previous_month(today: date) -> str:
    return (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")


def round_hours(hours: Decimal, minutes: int, mode: str = "nearest") -> Decimal:
    if not minutes:
        return hours.quantize(CENT, ROUND_HALF_UP)
    step = Decimal(minutes) / 60
    units = (hours / step).quantize(Decimal(1), ROUND_CEILING if mode == "up" else ROUND_HALF_UP)
    return (units * step).quantize(CENT, ROUND_HALF_UP)


def _key(e: Entry, group_by: str) -> str:
    if group_by == "description":
        return e.description or e.project or FALLBACK_LABEL
    if group_by == "day":
        return e.date.strftime("%b %d, %Y")
    return e.project or FALLBACK_LABEL


def build_lines(entries: list[Entry], cfg: Config) -> list[LineItem]:
    """Group entries per `cfg.group_by`, then round each line's hours."""
    ordered = sorted(entries, key=lambda e: e.date)
    if cfg.group_by == "entry":
        raw = [
            (f"{e.date:%b %d} - {e.description or e.project or FALLBACK_LABEL}", e.hours)
            for e in ordered
        ]
    else:
        groups: OrderedDict[str, Decimal] = OrderedDict()
        for e in ordered:
            k = _key(e, cfg.group_by)
            groups[k] = groups.get(k, Decimal(0)) + e.hours
        raw = list(groups.items())

    lines = [
        LineItem(desc, round_hours(h, cfg.round_minutes, cfg.rounding), cfg.hourly_rate)
        for desc, h in raw
    ]
    return [li for li in lines if li.hours > 0]


def build_invoice(
    entries: list[Entry], cfg: Config, month: str, issue_date: date, number: str
) -> Invoice:
    start, end = month_bounds(month)
    in_period = [e for e in entries if start <= e.date <= end]
    return Invoice(
        number=number,
        issue_date=issue_date,
        due_date=issue_date + timedelta(days=cfg.net_days),
        period_start=start,
        period_end=end,
        lines=build_lines(in_period, cfg),
        entries=sorted(in_period, key=lambda e: e.date),
        tax_percent=cfg.tax_percent,
    )
