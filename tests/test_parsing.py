from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from timesheet2invoice.parsing import ParseError, parse_date, parse_hours, read_entries

EXAMPLES = Path(__file__).parent.parent / "examples"


@pytest.mark.parametrize(
    "value,expected",
    [
        ("03:20:00", Decimal(3) + Decimal(20) / 60),
        ("1:30", Decimal("1.5")),
        ("2.75", Decimal("2.75")),
        ("1,234.5", Decimal("1234.5")),
        ("", Decimal(0)),
    ],
)
def test_parse_hours(value, expected):
    assert parse_hours(value) == expected


def test_parse_hours_rejects_garbage():
    with pytest.raises(ParseError):
        parse_hours("about two")


@pytest.mark.parametrize(
    "value", ["2026-09-02", "09/02/2026", "2026-09-02T09:00:00", "2026-09-02 09:00"]
)
def test_parse_date_formats(value):
    assert parse_date(value) == date(2026, 9, 2)


def test_parse_date_custom_format():
    assert parse_date("02/09/2026", "%d/%m/%Y") == date(2026, 9, 2)


@pytest.mark.parametrize("name", ["toggl", "clockify", "harvest"])
def test_exports_skip_non_billable(name):
    entries = read_entries(EXAMPLES / f"{name}.csv")
    assert entries, name
    assert all(e.description != "Internal admin" for e in entries)
    assert entries[0].project == "API Integration"
    assert entries[0].description == "Auth endpoints"


def test_harvest_uses_raw_hours_not_rounded():
    entries = read_entries(EXAMPLES / "harvest.csv")
    assert entries[0].hours == Decimal("3.33")


def test_generic_csv_without_billable_column():
    entries = read_entries(EXAMPLES / "generic.csv")
    assert [e.hours for e in entries] == [Decimal("3.5"), Decimal("6.75")]


def test_missing_columns(tmp_path):
    f = tmp_path / "bad.csv"
    f.write_text("foo,bar\n1,2\n")
    with pytest.raises(ParseError, match="Headers seen: foo, bar"):
        read_entries(f)


def test_bad_row_reports_line_number(tmp_path):
    f = tmp_path / "bad.csv"
    f.write_text("date,hours\n2026-09-01,1\nnot-a-date,2\n")
    with pytest.raises(ParseError, match="line 3"):
        read_entries(f)
