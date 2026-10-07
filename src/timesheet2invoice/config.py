"""Load and validate the TOML configuration."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

GROUP_BY = ("project", "description", "day", "entry")
ROUNDING = ("nearest", "up")


class ConfigError(ValueError):
    """Raised when the config file is missing fields or has invalid values."""


@dataclass
class Party:
    name: str
    address: list[str] = field(default_factory=list)
    email: str = ""
    phone: str = ""
    contact: str = ""


@dataclass
class Config:
    business: Party
    client: Party
    hourly_rate: Decimal
    net_days: int = 15
    currency: str = "$"
    tax_percent: Decimal = Decimal("0")
    group_by: str = "project"
    round_minutes: int = 0
    rounding: str = "nearest"
    date_format: str = ""
    prefix: str = "INV"
    start_number: int = 1
    output_dir: Path = Path("invoices")
    ledger: Path = Path("invoices/ledger.csv")
    include_time_log: bool = True
    accent_color: str = "#1f3a5f"
    payment_instructions: list[str] = field(default_factory=list)
    notes: str = ""


def _party(data: dict, section: str) -> Party:
    if not isinstance(data, dict) or not data.get("name"):
        raise ConfigError(f"[{section}] must define at least `name`.")
    known = {"name", "address", "email", "phone", "contact"}
    party = Party(**{k: v for k, v in data.items() if k in known})
    if isinstance(party.address, str):
        party.address = [party.address]
    return party


def load_config(path: str | Path) -> Config:
    path = Path(path)
    if not path.exists():
        raise ConfigError(
            f"Config file not found: {path}. Run `timesheet2invoice init` to create one."
        )
    with path.open("rb") as f:
        try:
            raw = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"Could not parse {path}: {e}") from e

    billing = raw.get("billing", {})
    invoice = raw.get("invoice", {})
    if "hourly_rate" not in billing:
        raise ConfigError("[billing] must define `hourly_rate`.")

    base = path.resolve().parent
    cfg = Config(
        business=_party(raw.get("business", {}), "business"),
        client=_party(raw.get("client", {}), "client"),
        hourly_rate=Decimal(str(billing["hourly_rate"])),
        net_days=int(billing.get("net_days", 15)),
        currency=str(billing.get("currency", "$")),
        tax_percent=Decimal(str(billing.get("tax_percent", 0))),
        group_by=str(billing.get("group_by", "project")),
        round_minutes=int(billing.get("round_minutes", 0)),
        rounding=str(billing.get("rounding", "nearest")),
        date_format=str(billing.get("date_format", "")),
        prefix=str(invoice.get("prefix", "INV")),
        start_number=int(invoice.get("start_number", 1)),
        output_dir=base / invoice.get("output_dir", "invoices"),
        ledger=base / invoice.get("ledger", "invoices/ledger.csv"),
        include_time_log=bool(invoice.get("include_time_log", True)),
        accent_color=str(invoice.get("accent_color", "#1f3a5f")),
        payment_instructions=list(invoice.get("payment_instructions", [])),
        notes=str(invoice.get("notes", "")),
    )

    if cfg.hourly_rate <= 0:
        raise ConfigError("`hourly_rate` must be greater than 0.")
    if cfg.net_days < 0:
        raise ConfigError("`net_days` cannot be negative.")
    if cfg.group_by not in GROUP_BY:
        raise ConfigError(f"`group_by` must be one of {', '.join(GROUP_BY)}.")
    if cfg.rounding not in ROUNDING:
        raise ConfigError(f"`rounding` must be one of {', '.join(ROUNDING)}.")
    if cfg.round_minutes < 0 or (cfg.round_minutes and 60 % cfg.round_minutes):
        raise ConfigError("`round_minutes` must be 0 or a divisor of 60 (e.g. 6, 15, 30).")
    return cfg
