from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from timesheet2invoice.billing import (
    build_invoice,
    build_lines,
    month_bounds,
    previous_month,
    round_hours,
)
from timesheet2invoice.config import Config, Party
from timesheet2invoice.parsing import Entry


@pytest.fixture
def cfg():
    return Config(
        business=Party("Me LLC"),
        client=Party("Acme"),
        hourly_rate=Decimal("100"),
        net_days=15,
        round_minutes=15,
    )


def E(d, h, project="P", desc="D"):
    return Entry(date(2026, 9, d), Decimal(str(h)), project, desc)


@pytest.mark.parametrize(
    "hours,minutes,mode,expected",
    [
        ("7.4167", 15, "nearest", "7.50"),
        ("7.1", 15, "nearest", "7.00"),
        ("7.1", 15, "up", "7.25"),
        ("7.0", 15, "up", "7.00"),
        ("1.04", 6, "nearest", "1.00"),
        ("3.333", 0, "nearest", "3.33"),
    ],
)
def test_round_hours(hours, minutes, mode, expected):
    assert round_hours(Decimal(hours), minutes, mode) == Decimal(expected)


def test_month_bounds():
    assert month_bounds("2026-02") == (date(2026, 2, 1), date(2026, 2, 28))
    assert month_bounds("2026-12") == (date(2026, 12, 1), date(2026, 12, 31))
    with pytest.raises(ValueError):
        month_bounds("Sept")


def test_previous_month_wraps_year():
    assert previous_month(date(2027, 1, 5)) == "2026-12"


def test_group_by_project(cfg):
    lines = build_lines([E(1, 2, "A"), E(2, 1.5, "B"), E(3, 1, "A")], cfg)
    assert [(li.description, li.hours) for li in lines] == [
        ("A", Decimal("3.00")),
        ("B", Decimal("1.50")),
    ]


def test_group_by_entry_keeps_duplicates(cfg):
    cfg = replace(cfg, group_by="entry")
    lines = build_lines([E(1, 1, desc="Same"), E(1, 1, desc="Same")], cfg)
    assert len(lines) == 2


def test_blank_project_gets_fallback_label(cfg):
    lines = build_lines([E(1, 1, project="")], cfg)
    assert lines[0].description == "Software consulting services"


def test_invoice_totals_and_due_date(cfg):
    entries = [E(2, "3.3333"), E(3, "4.0833"), E(30, 1), Entry(date(2026, 10, 1), Decimal(5))]
    inv = build_invoice(entries, cfg, "2026-09", date(2026, 10, 1), "INV-2026-001")
    assert inv.due_date == date(2026, 10, 16)
    assert inv.hours == Decimal("8.50")  # Oct entry excluded; 8.4166 rounds to 8.50
    assert inv.total == Decimal("850.00")
    assert len(inv.entries) == 3


def test_tax(cfg):
    cfg = replace(cfg, tax_percent=Decimal("8.25"))
    inv = build_invoice([E(1, 10)], cfg, "2026-09", date(2026, 10, 1), "X")
    assert inv.subtotal == Decimal("1000.00")
    assert inv.tax == Decimal("82.50")
    assert inv.total == Decimal("1082.50")
