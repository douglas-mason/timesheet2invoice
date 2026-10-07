"""Fetch time entries straight from a tracker's API (Harvest, Toggl Track, Clockify).

Each tracker uses a personal token the user creates in its web app. Tokens are read
from an environment variable (or `token` in the config) and never written anywhere.
"""

from __future__ import annotations

import base64
import http.client
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from . import __version__
from .billing import month_bounds
from .config import Source
from .parsing import Entry

USER_AGENT = f"timesheet2invoice/{__version__} (https://github.com/douglas-mason/timesheet2invoice)"

HARVEST_URL = "https://api.harvestapp.com/v2"
TOGGL_URL = "https://api.track.toggl.com/api/v9"
CLOCKIFY_URL = "https://api.clockify.me/api/v1"

TOKEN_HELP = {
    "harvest": "Create a personal access token at https://id.getharvest.com/developers",
    "toggl": "Copy your API token from https://track.toggl.com/profile",
    "clockify": "Generate an API key at https://app.clockify.me/user/preferences#advanced",
}


class ApiError(ValueError):
    """Raised when a tracker's API can't be reached or rejects the request."""


def _get(url: str, headers: dict[str, str]) -> Any:
    """GET a URL and decode its JSON body. Tests replace this function."""
    req = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": USER_AGENT, **headers}
    )
    host = urllib.parse.urlsplit(url).netloc
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            what = "token and account ID" if "harvest" in host else "token"
            raise ApiError(
                f"{host} rejected the credentials (HTTP {e.code}). Check the {what}."
            ) from None
        detail = e.read().decode("utf-8", "replace")[:300].strip()
        raise ApiError(f"{host} returned HTTP {e.code}: {detail or e.reason}") from None
    except urllib.error.URLError as e:
        raise ApiError(f"Could not reach {host}: {e.reason}") from None
    except (OSError, http.client.HTTPException) as e:  # timeouts, dropped connections
        raise ApiError(f"Could not reach {host}: {e or type(e).__name__}") from None
    except json.JSONDecodeError:
        raise ApiError(f"{host} returned a response that isn't JSON.") from None


def resolve_token(src: Source) -> str:
    token = os.environ.get(src.token_env, "").strip() or src.token
    if not token:
        raise ApiError(
            f"No {src.tracker} token found. Set the {src.token_env} environment variable "
            f"(or `token` under [source]). {TOKEN_HELP[src.tracker]}"
        )
    return token


def _local_date(ts: str) -> date:
    """ISO 8601 timestamp (UTC or with offset) -> the date in the local time zone."""
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return dt.astimezone().date() if dt.tzinfo else dt.date()


_ISO_DURATION = re.compile(
    r"^P(?:(?P<d>\d+)D)?(?:T(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+(?:\.\d+)?)S)?)?$"
)


def parse_iso_duration(value: str) -> Decimal:
    """'PT1H30M' -> Decimal('1.5') hours."""
    m = _ISO_DURATION.match(value or "")
    if not m:
        raise ApiError(f"Unrecognized duration {value!r}.")
    d, h, mi, s = (Decimal(m.group(k) or 0) for k in "dhms")
    return d * 24 + h + mi / 60 + s / 3600


def _client_matches(src: Source, name: str | None) -> bool:
    return not src.client or (name or "").strip().lower() == src.client.lower()


def fetch_harvest(src: Source, start: date, end: date) -> list[Entry]:
    account = os.environ.get("HARVEST_ACCOUNT_ID", "").strip() or src.account_id
    if not account:
        raise ApiError(
            "Harvest needs an account ID. Set `account_id` under [source] or the "
            "HARVEST_ACCOUNT_ID environment variable. It's shown next to your token at "
            "https://id.getharvest.com/developers"
        )
    headers = {"Authorization": f"Bearer {resolve_token(src)}", "Harvest-Account-Id": account}
    params: dict[str, Any] = {"from": start.isoformat(), "to": end.isoformat(), "per_page": 2000}
    if not src.all_users:  # admin tokens otherwise see the whole team's time
        params["user_id"] = _get(f"{HARVEST_URL}/users/me", headers)["id"]
    entries: list[Entry] = []
    page: int | None = 1
    while page:
        query = urllib.parse.urlencode({**params, "page": page})
        data = _get(f"{HARVEST_URL}/time_entries?{query}", headers)
        for t in data.get("time_entries", []):
            if t.get("is_running") or (src.billable_only and not t.get("billable")):
                continue
            if not _client_matches(src, (t.get("client") or {}).get("name")):
                continue
            entries.append(
                Entry(
                    date=date.fromisoformat(t["spent_date"]),
                    hours=Decimal(str(t.get("hours") or 0)),
                    project=((t.get("project") or {}).get("name") or "").strip(),
                    description=(t.get("notes") or (t.get("task") or {}).get("name") or "").strip(),
                )
            )
        page = data.get("next_page")
    return entries


def fetch_toggl(src: Source, start: date, end: date) -> list[Entry]:
    auth = base64.b64encode(f"{resolve_token(src)}:api_token".encode()).decode()
    headers = {"Authorization": f"Basic {auth}"}
    workspace = src.workspace_id or str(_get(f"{TOGGL_URL}/me", headers)["default_workspace_id"])
    # Pad the range by a day: Toggl filters in UTC, the invoice month is in local time.
    query = urllib.parse.urlencode(
        {
            "start_date": (start - timedelta(days=1)).isoformat(),
            "end_date": (end + timedelta(days=2)).isoformat(),
            "meta": "true",
        }
    )
    entries = []
    for t in _get(f"{TOGGL_URL}/me/time_entries?{query}", headers) or []:
        seconds = t.get("duration") or 0
        if seconds <= 0 or t.get("server_deleted_at"):  # negative = timer still running
            continue
        if str(t.get("workspace_id")) != workspace:
            continue
        if src.billable_only and not t.get("billable"):
            continue
        if not _client_matches(src, t.get("client_name")):
            continue
        entries.append(
            Entry(
                date=_local_date(t["start"]),
                hours=Decimal(seconds) / 3600,
                project=(t.get("project_name") or "").strip(),
                description=(t.get("description") or "").strip(),
            )
        )
    return [e for e in entries if start <= e.date <= end]


def fetch_clockify(src: Source, start: date, end: date, page_size: int = 1000) -> list[Entry]:
    headers = {"X-Api-Key": resolve_token(src)}
    user = _get(f"{CLOCKIFY_URL}/user", headers)
    workspace = src.workspace_id or user.get("activeWorkspace") or user.get("defaultWorkspace")
    if not workspace:
        raise ApiError("Clockify reports no active workspace. Set `workspace_id` under [source].")
    base = f"{CLOCKIFY_URL}/workspaces/{workspace}/user/{user['id']}/time-entries"
    entries: list[Entry] = []
    page = 1
    while True:
        query = urllib.parse.urlencode(
            {
                "start": f"{start - timedelta(days=1)}T00:00:00Z",
                "end": f"{end + timedelta(days=2)}T00:00:00Z",
                "hydrated": "true",
                "page": page,
                "page-size": page_size,
            }
        )
        batch = _get(f"{base}?{query}", headers) or []
        for t in batch:
            interval = t.get("timeInterval") or {}
            if not interval.get("end") or not interval.get("duration"):  # running timer
                continue
            if src.billable_only and not t.get("billable"):
                continue
            project = t.get("project") or {}
            if not _client_matches(src, project.get("clientName")):
                continue
            entries.append(
                Entry(
                    date=_local_date(interval["start"]),
                    hours=parse_iso_duration(interval["duration"]),
                    project=(project.get("name") or "").strip(),
                    description=(t.get("description") or "").strip(),
                )
            )
        if len(batch) < page_size:
            break
        page += 1
    return [e for e in entries if start <= e.date <= end]


FETCHERS = {"harvest": fetch_harvest, "toggl": fetch_toggl, "clockify": fetch_clockify}


def fetch_entries(src: Source, month: str) -> list[Entry]:
    """Download the entries for one billing month (YYYY-MM) from the configured tracker."""
    start, end = month_bounds(month)
    return FETCHERS[src.tracker](src, start, end)
