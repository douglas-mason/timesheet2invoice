"""Command-line interface."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from importlib.resources import files
from pathlib import Path

from . import __version__
from .api import fetch_entries
from .billing import build_invoice, previous_month
from .config import ConfigError, load_config
from .ledger import Ledger, describe_status
from .parsing import ParseError, read_entries
from .render import render_pdf

DEFAULT_CONFIG = "timesheet2invoice.toml"


def _date_arg(s: str) -> date:
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {s!r}") from None


def cmd_init(args: argparse.Namespace) -> int:
    path = Path(args.path)
    if path.exists() and not args.force:
        print(f"{path} already exists (use --force to overwrite).", file=sys.stderr)
        return 1
    template = files("timesheet2invoice").joinpath("config_template.toml").read_text("utf-8")
    path.write_text(template, encoding="utf-8")
    print(f"Wrote {path}. Edit your business, client and payment details, then run:")
    print(f"  timesheet2invoice generate <export.csv> -c {path}")
    return 0


def cmd_generate(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    month = args.month or previous_month(date.today())
    issue = args.issue_date or date.today()
    ledger = Ledger(cfg.ledger)

    existing = ledger.find_period(cfg.client.name, month)
    if existing and not (args.force or args.dry_run):
        print(
            f"{existing['invoice_no']} already bills {cfg.client.name} for {month}. "
            "Use --force to issue another.",
            file=sys.stderr,
        )
        return 1

    if args.csv:
        entries, origin = read_entries(args.csv, cfg.date_format), str(args.csv)
    elif cfg.source:
        origin = cfg.source.tracker.capitalize()
        print(f"Fetching {month} from {origin}...", file=sys.stderr)
        entries = fetch_entries(cfg.source, month)
    else:
        print(
            "Give a CSV export, or add a [source] section to the config to pull hours "
            "from Harvest, Toggl or Clockify.",
            file=sys.stderr,
        )
        return 2

    number = ledger.next_number(cfg.prefix, issue.year, cfg.start_number)
    inv = build_invoice(entries, cfg, month, issue, number)
    if not inv.lines:
        print(f"No billable time found for {month} in {origin}.", file=sys.stderr)
        return 1

    sym = cfg.currency
    print(f"{inv.number}  {cfg.client.name}  {inv.period_start} to {inv.period_end}")
    for li in inv.lines:
        print(f"  {li.hours:>8.2f} h  {li.description}")
    print(f"  {'-' * 8}")
    print(f"  {inv.hours:>8.2f} h x {sym}{cfg.hourly_rate:,.2f} = {sym}{inv.subtotal:,.2f}")
    if inv.tax:
        print(f"  tax {sym}{inv.tax:,.2f}  total {sym}{inv.total:,.2f}")
    print(f"  Issued {inv.issue_date}, due {inv.due_date} (NET {cfg.net_days})")

    if args.dry_run:
        print("Dry run: nothing written.")
        return 0

    slug = "".join(c if c.isalnum() else "_" for c in cfg.client.name).strip("_")
    out = cfg.output_dir / f"{inv.number}_{slug}_{month}.pdf"
    render_pdf(inv, cfg, out)
    ledger.record(inv, cfg.client.name, f"{cfg.hourly_rate:.2f}", out)
    print(f"Wrote {out}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    rows = Ledger(cfg.ledger).rows()
    if not args.all:
        rows = [r for r in rows if r["status"] != "paid"]
    if not rows:
        print("Nothing outstanding." if not args.all else "No invoices yet.")
        return 0
    today = date.today()
    sym = cfg.currency
    print(f"{'Invoice':<14} {'Period':<8} {'Due':<10} {'Total':>11}  Status")
    for r in rows:
        total = f"{sym}{float(r['total']):,.2f}"
        print(
            f"{r['invoice_no']:<14} {r['period']:<8} {r['due_date']:<10} {total:>11}  "
            f"{describe_status(r, today)}"
        )
    return 0


def cmd_paid(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    try:
        row = Ledger(cfg.ledger).mark_paid(args.invoice, args.date or date.today())
    except KeyError:
        print(f"{args.invoice} not found in {cfg.ledger}.", file=sys.stderr)
        return 1
    print(f"Marked {row['invoice_no']} paid on {row['paid_date']}.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="timesheet2invoice",
        description="Turn a time-tracking CSV export into a PDF invoice.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    def with_config(sp: argparse.ArgumentParser) -> None:
        sp.add_argument(
            "-c",
            "--config",
            default=DEFAULT_CONFIG,
            help=f"config file (default: {DEFAULT_CONFIG})",
        )

    sp = sub.add_parser("init", help="write a starter config file")
    sp.add_argument("path", nargs="?", default=DEFAULT_CONFIG)
    sp.add_argument("--force", action="store_true", help="overwrite an existing file")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("generate", aliases=["gen"], help="create an invoice from a CSV or API")
    sp.add_argument(
        "csv",
        type=Path,
        nargs="?",
        help="CSV export from Toggl, Clockify, Harvest, etc. Omit to use [source] in the config",
    )
    with_config(sp)
    sp.add_argument("-m", "--month", help="month to bill, YYYY-MM (default: last month)")
    sp.add_argument("--issue-date", type=_date_arg, help="invoice date (default: today)")
    sp.add_argument("-n", "--dry-run", action="store_true", help="show totals, write nothing")
    sp.add_argument("--force", action="store_true", help="allow a second invoice for one month")
    sp.set_defaults(func=cmd_generate)

    sp = sub.add_parser("status", help="list unpaid invoices and what's overdue")
    with_config(sp)
    sp.add_argument("-a", "--all", action="store_true", help="include paid invoices")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("paid", help="mark an invoice as paid")
    sp.add_argument("invoice", help="invoice number, e.g. INV-2026-001")
    with_config(sp)
    sp.add_argument("--date", type=_date_arg, help="payment date (default: today)")
    sp.set_defaults(func=cmd_paid)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ConfigError, ParseError, ValueError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
