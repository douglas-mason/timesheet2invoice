import shutil
from pathlib import Path

import pytest
from pypdf import PdfReader

from timesheet2invoice.cli import main
from timesheet2invoice.config import ConfigError, load_config

EXAMPLES = Path(__file__).parent.parent / "examples"


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    shutil.copy(EXAMPLES / "toggl.csv", tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(["init"]) == 0
    return tmp_path


def gen(*extra):
    return main(["generate", "toggl.csv", "-m", "2026-09", "--issue-date", "2026-10-01", *extra])


def pdf_text(path):
    return "\n".join(p.extract_text() for p in PdfReader(path).pages)


def test_init_refuses_to_overwrite(workdir):
    assert main(["init"]) == 1
    assert main(["init", "--force"]) == 0


def test_generate_end_to_end(workdir, capsys):
    assert gen() == 0
    pdfs = list((workdir / "invoices").glob("*.pdf"))
    assert [p.name for p in pdfs] == ["INV-2026-001_Client_Company_Inc_2026-09.pdf"]

    text = pdf_text(pdfs[0])
    assert "INV-2026-001" in text
    assert "Oct 16, 2026" in text  # NET 15
    assert "$2,025.00" in text  # 20.25 h at $100
    assert "Charts & filters" in text  # escaped correctly
    assert "Internal admin" not in text  # non-billable
    assert "Rate limiting" not in text  # October entry

    ledger = (workdir / "invoices" / "ledger.csv").read_text()
    assert "INV-2026-001" in ledger and "2025.00" in ledger


def test_dry_run_writes_nothing(workdir, capsys):
    assert gen("--dry-run") == 0
    assert not (workdir / "invoices").exists()
    assert "20.25 h" in capsys.readouterr().out


def test_duplicate_month_blocked_then_numbering_increments(workdir):
    assert gen() == 0
    assert gen() == 1
    assert gen("--force") == 0
    assert (workdir / "invoices" / "INV-2026-002_Client_Company_Inc_2026-09.pdf").exists()


def test_status_and_paid(workdir, capsys):
    gen()
    capsys.readouterr()
    assert main(["status"]) == 0
    assert "INV-2026-001" in capsys.readouterr().out

    assert main(["paid", "INV-2026-001", "--date", "2026-10-10"]) == 0
    assert main(["status"]) == 0
    assert "Nothing outstanding" in capsys.readouterr().out
    assert main(["paid", "INV-9999-999"]) == 1


def test_empty_month(workdir, capsys):
    assert main(["generate", "toggl.csv", "-m", "2026-01"]) == 1
    assert "No billable time" in capsys.readouterr().err


def test_missing_config_is_friendly(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["status"]) == 2
    assert "timesheet2invoice init" in capsys.readouterr().err


def test_config_validation(tmp_path):
    f = tmp_path / "c.toml"
    f.write_text(
        '[business]\nname="a"\n[client]\nname="b"\n[billing]\nhourly_rate=100\ngroup_by="week"\n'
    )
    with pytest.raises(ConfigError, match="group_by"):
        load_config(f)


def test_example_config_loads():
    cfg = load_config(EXAMPLES / "example.toml")
    assert cfg.net_days == 15 and cfg.hourly_rate == 100
