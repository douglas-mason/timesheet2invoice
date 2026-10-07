"""A CSV ledger of issued invoices: numbering, duplicate checks, and payment status."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .billing import Invoice

FIELDS = [
    "invoice_no",
    "client",
    "period",
    "issue_date",
    "due_date",
    "hours",
    "rate",
    "subtotal",
    "tax",
    "total",
    "status",
    "paid_date",
    "file",
]


@dataclass
class Ledger:
    path: Path

    def rows(self) -> list[dict[str, str]]:
        if not self.path.exists():
            return []
        with self.path.open(newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def _write(self, rows: list[dict[str, str]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(rows)

    def next_number(self, prefix: str, year: int, start: int) -> str:
        pattern = re.compile(rf"^{re.escape(prefix)}-\d{{4}}-(\d+)$")
        seqs = [int(m.group(1)) for r in self.rows() if (m := pattern.match(r["invoice_no"]))]
        seq = max(seqs) + 1 if seqs else start
        return f"{prefix}-{year}-{seq:03d}"

    def find_period(self, client: str, period: str) -> dict[str, str] | None:
        for r in self.rows():
            if r["client"] == client and r["period"] == period:
                return r
        return None

    def record(self, inv: Invoice, client: str, rate: str, file: Path) -> None:
        rows = self.rows()
        rows.append(
            {
                "invoice_no": inv.number,
                "client": client,
                "period": f"{inv.period_start:%Y-%m}",
                "issue_date": inv.issue_date.isoformat(),
                "due_date": inv.due_date.isoformat(),
                "hours": f"{inv.hours:.2f}",
                "rate": rate,
                "subtotal": f"{inv.subtotal:.2f}",
                "tax": f"{inv.tax:.2f}",
                "total": f"{inv.total:.2f}",
                "status": "sent",
                "paid_date": "",
                "file": file.name,
            }
        )
        self._write(rows)

    def mark_paid(self, invoice_no: str, paid_on: date) -> dict[str, str]:
        rows = self.rows()
        for r in rows:
            if r["invoice_no"] == invoice_no:
                r["status"] = "paid"
                r["paid_date"] = paid_on.isoformat()
                self._write(rows)
                return r
        raise KeyError(invoice_no)


def describe_status(row: dict[str, str], today: date) -> str:
    if row["status"] == "paid":
        return f"paid {row['paid_date']}"
    days = (date.fromisoformat(row["due_date"]) - today).days
    if days < 0:
        return f"OVERDUE {-days}d"
    if days == 0:
        return "due today"
    return f"due in {days}d"
