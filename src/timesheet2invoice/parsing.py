"""Read time entries from CSV exports (Toggl Track, Clockify, Harvest, or generic)."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

# Candidate header names, checked in order (case-insensitive).
DATE_COLS = ("start date", "date", "start_date", "day", "spent date")
HOURS_COLS = ("hours", "duration (decimal)", "time (decimal)", "duration (h)", "duration", "time")
PROJECT_COLS = ("project", "project name")
DESC_COLS = ("description", "notes", "note", "task", "task name")
BILLABLE_COLS = ("billable", "billable?")

DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d.%m.%Y", "%Y/%m/%d")
TRUTHY = {"yes", "true", "1", "y", "billable"}


class ParseError(ValueError):
    """Raised when a CSV can't be interpreted as a timesheet."""


@dataclass(frozen=True)
class Entry:
    date: date
    hours: Decimal
    project: str = ""
    description: str = ""


def _find_col(headers: list[str], candidates: tuple[str, ...]) -> str | None:
    lookup = {h.strip().lower(): h for h in headers}
    for c in candidates:
        if c in lookup:
            return lookup[c]
    return None


def parse_date(value: str, fmt: str = "") -> date:
    s = value.strip().split("T")[0].split(" ")[0]
    for f in (fmt,) if fmt else DATE_FORMATS:
        try:
            return datetime.strptime(s, f).date()
        except ValueError:
            continue
    hint = "" if fmt else " Set `billing.date_format` in your config to specify it."
    raise ParseError(f"Unrecognized date {value!r}.{hint}")


def parse_hours(value: str) -> Decimal:
    s = str(value).strip()
    if not s:
        return Decimal(0)
    try:
        if ":" in s:  # H:MM or H:MM:SS
            parts = [Decimal(p) for p in s.split(":")]
            parts += [Decimal(0)] * (3 - len(parts))
            return parts[0] + parts[1] / 60 + parts[2] / 3600
        return Decimal(s.replace(",", ""))
    except InvalidOperation as e:
        raise ParseError(f"Unrecognized duration {value!r}.") from e


def read_entries(path: str | Path, date_format: str = "") -> list[Entry]:
    """Parse a CSV into billable entries. Rows flagged non-billable are skipped."""
    with Path(path).open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        dcol = _find_col(headers, DATE_COLS)
        hcol = _find_col(headers, HOURS_COLS)
        if not dcol or not hcol:
            raise ParseError(
                "Could not find a date column and an hours/duration column. "
                f"Headers seen: {', '.join(headers) or '(none)'}"
            )
        pcol = _find_col(headers, PROJECT_COLS)
        ncol = _find_col(headers, DESC_COLS)
        bcol = _find_col(headers, BILLABLE_COLS)

        entries = []
        for line_no, row in enumerate(reader, start=2):
            if not (row.get(dcol) or "").strip():
                continue
            if bcol and str(row.get(bcol, "")).strip().lower() not in TRUTHY:
                continue
            try:
                entries.append(
                    Entry(
                        date=parse_date(row[dcol], date_format),
                        hours=parse_hours(row[hcol]),
                        project=(row.get(pcol) or "").strip() if pcol else "",
                        description=(row.get(ncol) or "").strip() if ncol else "",
                    )
                )
            except ParseError as e:
                raise ParseError(f"{path}, line {line_no}: {e}") from e
    return entries
