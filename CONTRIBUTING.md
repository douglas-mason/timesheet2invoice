# Contributing

Bug reports, new CSV formats and pull requests are welcome.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Before opening a PR

```bash
pytest
ruff check .
ruff format .
```

CI runs the same checks on Python 3.10 through 3.13, on Linux, macOS and Windows.

## Adding a time-tracker format

Column detection is in `src/timesheet2invoice/parsing.py`. Each `*_COLS` tuple lists header names in priority order, matched without regard to case. To support a new tracker:

1. Add a small anonymized export to `examples/`.
2. Add any new header names to the matching tuple.
3. Add the file to the `test_exports_skip_non_billable` parametrize list in `tests/test_parsing.py`.

## Layout

```
src/timesheet2invoice/
  cli.py        argument parsing and the commands
  config.py     TOML loading and validation
  parsing.py    CSV reading and column detection
  api.py        fetching entries from the Harvest, Toggl and Clockify APIs
  billing.py    grouping, rounding, totals, due dates
  ledger.py     invoice numbering and payment tracking
  render.py     the PDF layout
```
