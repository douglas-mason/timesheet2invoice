from datetime import date
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

import pytest

from timesheet2invoice import api
from timesheet2invoice.cli import main
from timesheet2invoice.config import ConfigError, Source, load_config


@pytest.fixture
def fake_http(monkeypatch):
    """Route api._get to a dict of {path: response or callable(query) -> response}."""
    calls = []
    routes = {}

    def fake_get(url, headers):
        parts = urlsplit(url)
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        calls.append((parts.netloc + parts.path, query, headers))
        resp = routes[parts.netloc + parts.path]
        return resp(query) if callable(resp) else resp

    monkeypatch.setattr(api, "_get", fake_get)
    for var in ("HARVEST_ACCESS_TOKEN", "HARVEST_ACCOUNT_ID", "TOGGL_API_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("CLOCKIFY_API_KEY", raising=False)
    return routes, calls


def harvest_entry(day, hours, **kw):
    return {
        "spent_date": day,
        "hours": hours,
        "notes": kw.get("notes"),
        "billable": kw.get("billable", True),
        "is_running": kw.get("is_running", False),
        "project": {"name": kw.get("project", "Website")},
        "task": {"name": "Development"},
        "client": {"name": kw.get("client", "Acme Corp")},
    }


def test_harvest_paginates_and_filters(fake_http, monkeypatch):
    routes, calls = fake_http
    monkeypatch.setenv("HARVEST_ACCESS_TOKEN", "tok")
    pages = {
        "1": {
            "time_entries": [
                harvest_entry("2026-09-02", 2.5, notes="Build login"),
                harvest_entry("2026-09-03", 1.0, billable=False),
                harvest_entry("2026-09-04", 3.0, client="Other Co"),
            ],
            "next_page": 2,
        },
        "2": {
            "time_entries": [
                harvest_entry("2026-09-30", 1.25),
                harvest_entry("2026-09-30", 0.5, is_running=True),
            ],
            "next_page": None,
        },
    }
    routes["api.harvestapp.com/v2/time_entries"] = lambda q: pages[q["page"]]

    src = Source("harvest", "HARVEST_ACCESS_TOKEN", account_id="42", client="acme corp")
    entries = api.fetch_entries(src, "2026-09")

    assert entries == [
        api.Entry(date(2026, 9, 2), Decimal("2.5"), "Website", "Build login"),
        api.Entry(date(2026, 9, 30), Decimal("1.25"), "Website", "Development"),
    ]
    _, query, headers = calls[0]
    assert query["from"] == "2026-09-01" and query["to"] == "2026-09-30"
    assert headers == {"Authorization": "Bearer tok", "Harvest-Account-Id": "42"}
    assert len(calls) == 2


def test_harvest_needs_account_id(fake_http, monkeypatch):
    monkeypatch.setenv("HARVEST_ACCESS_TOKEN", "tok")
    with pytest.raises(api.ApiError, match="account ID"):
        api.fetch_entries(Source("harvest", "HARVEST_ACCESS_TOKEN"), "2026-09")


def test_missing_token_points_to_developer_page(fake_http):
    src = Source("harvest", "HARVEST_ACCESS_TOKEN", account_id="42")
    with pytest.raises(api.ApiError, match="id.getharvest.com/developers"):
        api.fetch_entries(src, "2026-09")


def test_token_from_config_when_env_unset(fake_http):
    routes, calls = fake_http
    routes["api.harvestapp.com/v2/time_entries"] = {"time_entries": [], "next_page": None}
    src = Source("harvest", "HARVEST_ACCESS_TOKEN", token="cfgtok", account_id="42")
    assert api.fetch_entries(src, "2026-09") == []
    assert calls[0][2]["Authorization"] == "Bearer cfgtok"


def test_toggl(fake_http, monkeypatch):
    routes, calls = fake_http
    monkeypatch.setenv("TOGGL_API_TOKEN", "abc")
    routes["api.track.toggl.com/api/v9/me/time_entries"] = [
        {
            "start": "2026-09-02T12:00:00+00:00",
            "duration": 5400,
            "description": "Charts",
            "billable": True,
            "project_name": "Dashboard",
            "client_name": "Acme Corp",
            "workspace_id": 7,
        },
        {"start": "2026-09-03T12:00:00Z", "duration": -1726000000, "billable": True},  # running
        {"start": "2026-09-04T12:00:00Z", "duration": 3600, "billable": False},
        {"start": "2026-10-01T12:00:00Z", "duration": 3600, "billable": True},  # next month
        {"start": "2026-09-05T12:00:00Z", "duration": 3600, "billable": True, "workspace_id": 8},
    ]
    entries = api.fetch_entries(Source("toggl", "TOGGL_API_TOKEN", workspace_id="7"), "2026-09")
    assert entries == [api.Entry(date(2026, 9, 2), Decimal("1.5"), "Dashboard", "Charts")]
    _, query, headers = calls[0]
    assert headers["Authorization"] == "Basic YWJjOmFwaV90b2tlbg=="  # abc:api_token
    assert query["meta"] == "true"


def test_toggl_billable_only_off(fake_http, monkeypatch):
    routes, _ = fake_http
    monkeypatch.setenv("TOGGL_API_TOKEN", "abc")
    routes["api.track.toggl.com/api/v9/me/time_entries"] = [
        {"start": "2026-09-15T12:00:00Z", "duration": 1800, "billable": False},
    ]
    src = Source("toggl", "TOGGL_API_TOKEN", billable_only=False)
    assert [e.hours for e in api.fetch_entries(src, "2026-09")] == [Decimal("0.5")]


def test_clockify_paginates(fake_http, monkeypatch):
    routes, calls = fake_http
    monkeypatch.setenv("CLOCKIFY_API_KEY", "key")
    routes["api.clockify.me/api/v1/user"] = {"id": "u1", "activeWorkspace": "w1"}

    def entry(day, duration, **kw):
        return {
            "description": kw.get("description", "Review"),
            "billable": kw.get("billable", True),
            "project": {"name": "API", "clientName": "Acme Corp"},
            "timeInterval": {
                "start": f"{day}T12:00:00Z",
                "end": None if duration is None else f"{day}T14:00:00Z",
                "duration": duration,
            },
        }

    pages = {
        "1": [entry("2026-09-10", "PT1H45M"), entry("2026-09-11", None)],
        "2": [entry("2026-09-12", "PT30M", billable=False)],
    }
    path = "api.clockify.me/api/v1/workspaces/w1/user/u1/time-entries"
    routes[path] = lambda q: pages[q["page"]]

    entries = api.fetch_clockify(
        Source("clockify", "CLOCKIFY_API_KEY"), date(2026, 9, 1), date(2026, 9, 30), page_size=2
    )
    assert entries == [api.Entry(date(2026, 9, 10), Decimal("1.75"), "API", "Review")]
    assert calls[0][2] == {"X-Api-Key": "key"}
    assert [c[1].get("page") for c in calls[1:]] == ["1", "2"]


@pytest.mark.parametrize(
    "value,hours",
    [("PT1H30M", "1.5"), ("PT45M", "0.75"), ("PT2H", "2"), ("PT90S", "0.025"), ("P1DT1H", "25")],
)
def test_parse_iso_duration(value, hours):
    assert api.parse_iso_duration(value) == Decimal(hours)


def test_source_config(tmp_path):
    f = tmp_path / "c.toml"
    base = '[business]\nname="a"\n[client]\nname="b"\n[billing]\nhourly_rate=100\n'
    f.write_text(base + '[source]\ntracker="Harvest"\naccount_id=123\n')
    src = load_config(f).source
    assert src.tracker == "harvest" and src.account_id == "123"
    assert src.token_env == "HARVEST_ACCESS_TOKEN" and src.billable_only

    f.write_text(base + '[source]\ntracker="jira"\n')
    with pytest.raises(ConfigError, match="tracker"):
        load_config(f)


def test_generate_from_api(tmp_path, monkeypatch, fake_http, capsys):
    routes, _ = fake_http
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HARVEST_ACCESS_TOKEN", "tok")
    assert main(["init"]) == 0
    cfg = tmp_path / "timesheet2invoice.toml"
    cfg.write_text(cfg.read_text() + '\n[source]\ntracker = "harvest"\naccount_id = "42"\n')
    routes["api.harvestapp.com/v2/time_entries"] = {
        "time_entries": [harvest_entry("2026-09-02", 4.0), harvest_entry("2026-09-09", 6.0)],
        "next_page": None,
    }

    assert main(["generate", "-m", "2026-09", "--issue-date", "2026-10-01"]) == 0
    out = capsys.readouterr()
    assert "10.00 h  Website" in out.out
    assert "Fetching 2026-09 from Harvest" in out.err
    assert (tmp_path / "invoices" / "INV-2026-001_Client_Company_Inc_2026-09.pdf").exists()


def test_generate_without_csv_or_source(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["init"]) == 0
    assert main(["generate", "-m", "2026-09"]) == 2
    assert "[source]" in capsys.readouterr().err


def test_api_errors_are_reported(tmp_path, monkeypatch, fake_http, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["init"]) == 0
    cfg = tmp_path / "timesheet2invoice.toml"
    cfg.write_text(cfg.read_text() + '\n[source]\ntracker = "toggl"\n')
    assert main(["generate", "-m", "2026-09"]) == 2
    assert "TOGGL_API_TOKEN" in capsys.readouterr().err
