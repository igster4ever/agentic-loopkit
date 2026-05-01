"""
agentic_loopkit/adapters/clickup.py — ClickUp polling adapter.

Polls the ClickUp REST API for task updates and emits typed events onto the bus.
ClickUp does not push webhooks to arbitrary servers, so this adapter is
polling-only.  Call tick() on a schedule (e.g. every 5 minutes via APScheduler).

Authentication:
    Pass your ClickUp API token (personal or OAuth) as api_token.
    Obtain at: https://app.clickup.com/settings/apps

Cursor strategy:
    The cursor is a Unix timestamp in milliseconds — the `date_updated` of the
    most recently seen task.  On each tick the adapter fetches tasks updated
    *after* that timestamp, updates the cursor to the latest `date_updated`
    seen, and persists it.

Scoping:
    Provide either list_ids (poll specific lists) or team_id alone
    (poll the whole workspace).  list_ids is strongly preferred — workspace-wide
    polls are slow and can hit rate limits on large ClickUp accounts.

Event types:
    clickup.task_updated  — task modified (status, assignee, field, comment)
    clickup.task_created  — new task (date_created >= cursor window)

Usage:

    from agentic_loopkit.adapters.clickup import ClickUpAdapter, ClickUpEventType

    adapter = ClickUpAdapter(
        bus      = bus,
        api_token = os.environ["CLICKUP_API_TOKEN"],
        list_ids  = ["abc123", "def456"],
    )
    bus.add_adapter(adapter)

    # Tick from APScheduler or asyncio:
    await adapter.tick()
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Optional

from ..events.models import Event
from .base import PollingAdapter

log = logging.getLogger("agentic_loopkit.adapter.clickup")

# ── Event types ────────────────────────────────────────────────────────────────


class ClickUpEventType(StrEnum):
    """Domain events emitted by ClickUpAdapter.  Stream: 'clickup'."""
    TASK_UPDATED = "clickup.task_updated"
    TASK_CREATED = "clickup.task_created"


# ── Adapter ────────────────────────────────────────────────────────────────────


class ClickUpAdapter(PollingAdapter):
    """
    Polls ClickUp for task changes and emits clickup.task_* events.

    Attributes:
        name          adapter identifier (used in logs + cursor key)
        api_token     ClickUp personal or OAuth API token
        team_id       ClickUp workspace/team ID (required for workspace-wide polls)
        list_ids      list of ClickUp list IDs to poll (preferred over team_id)
        page_size     tasks per API page (default 100, ClickUp max 100)
    """

    name = "clickup"

    def __init__(
        self,
        bus,
        api_token: str,
        list_ids:  Optional[list[str]] = None,
        team_id:   Optional[str]       = None,
        page_size: int                 = 100,
    ) -> None:
        if not list_ids and not team_id:
            raise ValueError("ClickUpAdapter requires list_ids or team_id")
        self._api_token = api_token
        self._list_ids  = list_ids or []
        self._team_id   = team_id
        self._page_size = page_size
        super().__init__(bus)

    # ── PollingAdapter contract ────────────────────────────────────────────────

    async def poll(
        self, cursor: Optional[Any]
    ) -> tuple[list[Event], Optional[Any]]:
        """
        Fetch ClickUp tasks updated since cursor.

        Cursor is a Unix timestamp in milliseconds.
        Returns (events, new_cursor) where new_cursor is the latest date_updated
        seen across all fetched tasks, or None if nothing new.
        """
        # Default cursor: 24 hours ago to avoid flooding on first run
        since_ms: int = cursor if isinstance(cursor, int) else _now_ms() - 86_400_000

        raw_tasks = await self._fetch_all(since_ms)
        if not raw_tasks:
            return [], None

        events    = [self._task_to_event(t, since_ms) for t in raw_tasks]
        new_cursor = max(int(t.get("date_updated", 0)) for t in raw_tasks)

        log.debug(
            "[clickup] fetched %d task(s) updated after %s",
            len(raw_tasks),
            _ms_to_iso(since_ms),
        )
        return events, new_cursor

    # ── HTTP fetching ──────────────────────────────────────────────────────────

    async def _fetch_all(self, since_ms: int) -> list[dict]:
        """
        Fetch all tasks updated since since_ms across all configured lists.

        Handles ClickUp pagination (page 0, 1, 2 … until last_page=True).
        Returns a flat list of raw task dicts.
        """
        tasks: list[dict] = []

        if self._list_ids:
            for list_id in self._list_ids:
                tasks.extend(await self._fetch_list(list_id, since_ms))
        elif self._team_id:
            tasks.extend(await self._fetch_team(self._team_id, since_ms))

        # Deduplicate by task id (a task in multiple lists appears once per list)
        seen: set[str] = set()
        unique: list[dict] = []
        for t in tasks:
            tid = t.get("id")
            if tid and tid not in seen:
                seen.add(tid)
                unique.append(t)
        return unique

    async def _fetch_list(self, list_id: str, since_ms: int) -> list[dict]:
        """
        Paginate through GET /list/{list_id}/task?date_updated_gt={since_ms}.

        ClickUp API reference:
            https://clickup.com/api/clickupreference/operation/GetTasks/

        TODO: replace the stub below with a real aiohttp / httpx call.
        """
        import aiohttp  # noqa: PLC0415 — optional dep; only imported at call time

        url     = f"https://api.clickup.com/api/v2/list/{list_id}/task"
        headers = {"Authorization": self._api_token}
        tasks: list[dict] = []
        page  = 0

        async with aiohttp.ClientSession() as session:
            while True:
                params = {
                    "date_updated_gt": str(since_ms),
                    "order_by":        "updated",
                    "page":            str(page),
                    "page_size":       str(self._page_size),
                    "include_closed":  "true",
                }
                async with session.get(url, headers=headers, params=params) as resp:
                    if resp.status == 429:
                        log.warning("[clickup] rate limited on list %s — stopping pagination", list_id)
                        break
                    resp.raise_for_status()
                    data = await resp.json()

                batch = data.get("tasks", [])
                tasks.extend(batch)

                if data.get("last_page", True) or not batch:
                    break
                page += 1

        return tasks

    async def _fetch_team(self, team_id: str, since_ms: int) -> list[dict]:
        """
        Paginate through GET /team/{team_id}/task?date_updated_gt={since_ms}.

        ClickUp API reference:
            https://clickup.com/api/clickupreference/operation/GetFilteredTeamTasks/

        Workspace-wide polls are slower and more likely to hit rate limits.
        Prefer list_ids where possible.

        TODO: replace the stub below with a real aiohttp / httpx call.
        """
        import aiohttp  # noqa: PLC0415

        url     = f"https://api.clickup.com/api/v2/team/{team_id}/task"
        headers = {"Authorization": self._api_token}
        tasks: list[dict] = []
        page  = 0

        async with aiohttp.ClientSession() as session:
            while True:
                params = {
                    "date_updated_gt": str(since_ms),
                    "order_by":        "updated",
                    "page":            str(page),
                    "page_size":       str(self._page_size),
                    "include_closed":  "true",
                }
                async with session.get(url, headers=headers, params=params) as resp:
                    if resp.status == 429:
                        log.warning("[clickup] rate limited on team %s — stopping pagination", team_id)
                        break
                    resp.raise_for_status()
                    data = await resp.json()

                batch = data.get("tasks", [])
                tasks.extend(batch)

                if data.get("last_page", True) or not batch:
                    break
                page += 1

        return tasks

    # ── Event mapping ──────────────────────────────────────────────────────────

    def _task_to_event(self, task: dict, since_ms: int) -> Event:
        """
        Map a raw ClickUp task dict to a typed loopkit Event.

        Determines created vs updated by comparing date_created to the poll window.
        Uses the ClickUp task ID as correlation_id so all events for a task are
        traceable back to the same ticket.
        """
        task_id      = task.get("id", "")
        date_created = int(task.get("date_created", 0))
        event_type   = (
            ClickUpEventType.TASK_CREATED
            if date_created >= since_ms
            else ClickUpEventType.TASK_UPDATED
        )

        return Event(
            event_type     = event_type,
            source         = self.name,
            correlation_id = task_id,
            payload        = {
                "id":          task_id,
                "name":        task.get("name", ""),
                "status":      _status(task),
                "assignees":   [a.get("username") for a in task.get("assignees", [])],
                "list":        _list_name(task),
                "list_id":     _list_id(task),
                "url":         task.get("url", ""),
                "date_created": _ms_to_iso(date_created) if date_created else None,
                "date_updated": _ms_to_iso(int(task.get("date_updated", 0))),
                "priority":    _priority(task),
                "tags":        [t.get("name") for t in task.get("tags", [])],
                "custom_fields": _custom_fields(task),
            },
        )


# ── Helpers ────────────────────────────────────────────────────────────────────

def _now_ms() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp() * 1000)


def _ms_to_iso(ms: int) -> str:
    if not ms:
        return ""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _status(task: dict) -> str:
    return (task.get("status") or {}).get("status", "")


def _list_name(task: dict) -> str:
    return (task.get("list") or {}).get("name", "")


def _list_id(task: dict) -> str:
    return (task.get("list") or {}).get("id", "")


def _priority(task: dict) -> str | None:
    p = task.get("priority")
    if isinstance(p, dict):
        return p.get("priority")
    return None


def _custom_fields(task: dict) -> dict:
    """Flatten custom fields to {name: value} for easy downstream consumption."""
    result: dict = {}
    for cf in task.get("custom_fields", []):
        name  = cf.get("name", "")
        value = cf.get("value")
        if name and value is not None:
            result[name] = value
    return result
