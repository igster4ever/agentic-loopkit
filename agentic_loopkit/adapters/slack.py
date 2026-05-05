"""
agentic_loopkit/adapters/slack.py — Slack polling adapter.

Polls the Slack Web API conversations.history endpoint for new messages
in a configured list of channel IDs.  Call tick() on a schedule (e.g.
every 60 seconds via APScheduler).

Authentication:
    Pass a bot token (xoxb-...) with the channels:history and
    channels:read scopes.  Obtain at: https://api.slack.com/apps

Cursor strategy:
    The cursor is a dict mapping channel_id → latest message ts seen.
    Slack timestamps are opaque strings like "1715000000.123456" that
    sort lexicographically.  On each tick the adapter fetches messages
    posted *after* that ts and updates the per-channel cursor.

Scoping:
    Provide channel_ids — a list of Slack channel IDs (C...).
    The adapter polls each channel in turn; all messages go to the
    "slack" stream.

Event types:
    slack.message_received  — a new message posted to a channel

Usage:

    from agentic_loopkit.adapters.slack import SlackAdapter, SlackEventType

    adapter = SlackAdapter(
        bus         = bus,
        bot_token   = os.environ["SLACK_BOT_TOKEN"],
        channel_ids = ["C12345678", "C87654321"],
    )
    bus.add_adapter(adapter)
    await adapter.tick()
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Optional

from ..events.models import Event
from .base import PollingAdapter

log = logging.getLogger("agentic_loopkit.adapter.slack")

# ── Event types ────────────────────────────────────────────────────────────────


class SlackEventType(StrEnum):
    """Domain events emitted by SlackAdapter.  Stream: 'slack'."""
    MESSAGE_RECEIVED = "slack.message_received"


# ── Adapter ────────────────────────────────────────────────────────────────────


class SlackAdapter(PollingAdapter):
    """
    Polls Slack channels for new messages and emits slack.message_received events.

    Attributes:
        name          adapter identifier (used in logs + cursor key)
        bot_token     Slack bot token (xoxb-...)
        channel_ids   list of Slack channel IDs (C...) to poll
        page_size     messages per API page (default 100, Slack max 200)
    """

    name = "slack"

    def __init__(
        self,
        bus,
        bot_token:   str,
        channel_ids: list[str],
        page_size:   int = 100,
    ) -> None:
        if not channel_ids:
            raise ValueError("SlackAdapter requires at least one channel_id")
        self._bot_token   = bot_token
        self._channel_ids = channel_ids
        self._page_size   = min(page_size, 200)   # Slack hard cap
        super().__init__(bus)

    # ── PollingAdapter contract ────────────────────────────────────────────────

    async def poll(
        self, cursor: Optional[Any]
    ) -> tuple[list[Event], Optional[Any]]:
        """
        Fetch messages posted since cursor across all configured channels.

        Cursor is a dict {channel_id: ts_string}.  Returns (events, new_cursor)
        where new_cursor is the updated dict with the latest ts per channel,
        or None if nothing new was found in any channel.
        """
        # Default cursor: 24 hours ago (Slack ts = Unix float as string)
        default_ts = str((_now_unix() - 86_400.0))
        per_channel: dict[str, str] = cursor if isinstance(cursor, dict) else {}

        all_events: list[Event] = []
        new_cursor: dict[str, str] = dict(per_channel)  # start with existing

        for channel_id in self._channel_ids:
            oldest = per_channel.get(channel_id, default_ts)
            messages, latest_ts = await self._fetch_channel(channel_id, oldest)
            if messages:
                all_events.extend(
                    self._message_to_event(msg, channel_id) for msg in messages
                )
                new_cursor[channel_id] = latest_ts

        if not all_events:
            return [], None

        log.debug(
            "[slack] fetched %d message(s) across %d channel(s)",
            len(all_events),
            len(self._channel_ids),
        )
        return all_events, new_cursor

    # ── HTTP fetching ──────────────────────────────────────────────────────────

    async def _fetch_channel(
        self, channel_id: str, oldest: str
    ) -> tuple[list[dict], str]:
        """
        Fetch all messages in channel_id posted after oldest timestamp.

        Handles Slack cursor-based pagination (next_cursor in response_metadata).
        Returns (messages, latest_ts) — latest_ts is the ts of the most recent
        message seen, or oldest if none found.

        Slack API reference:
            https://api.slack.com/methods/conversations.history
        """
        import aiohttp  # noqa: PLC0415 — optional dep; only imported at call time

        url     = "https://slack.com/api/conversations.history"
        headers = {
            "Authorization": f"Bearer {self._bot_token}",
            "Content-Type":  "application/json",
        }
        messages: list[dict] = []
        next_cursor: str = ""
        latest_ts = oldest

        async with aiohttp.ClientSession() as session:
            while True:
                params: dict[str, Any] = {
                    "channel": channel_id,
                    "oldest":  oldest,
                    "limit":   str(self._page_size),
                    "inclusive": "false",
                }
                if next_cursor:
                    params["cursor"] = next_cursor

                async with session.get(url, headers=headers, params=params) as resp:
                    if resp.status == 429:
                        log.warning(
                            "[slack] rate limited on channel %s — stopping pagination",
                            channel_id,
                        )
                        break
                    resp.raise_for_status()
                    data = await resp.json()

                if not data.get("ok"):
                    error = data.get("error", "unknown")
                    log.warning("[slack] API error for channel %s: %s", channel_id, error)
                    break

                batch = data.get("messages", [])
                messages.extend(batch)

                if batch:
                    # Slack returns newest-first; batch[0].ts is the most recent
                    latest_ts = batch[0].get("ts", latest_ts)

                meta     = data.get("response_metadata", {})
                next_cursor = meta.get("next_cursor", "")
                if not next_cursor or not data.get("has_more", False):
                    break

        return messages, latest_ts

    # ── Event mapping ──────────────────────────────────────────────────────────

    def _message_to_event(self, message: dict, channel_id: str) -> Event:
        """
        Map a raw Slack message dict to a typed loopkit Event.

        Uses the message ts as correlation_id — unique per channel per message.
        """
        ts        = message.get("ts", "")
        user      = message.get("user") or message.get("bot_id", "")
        text      = message.get("text", "")
        subtype   = message.get("subtype")         # None for normal messages
        thread_ts = message.get("thread_ts")       # set if message is in a thread

        return Event(
            event_type     = SlackEventType.MESSAGE_RECEIVED,
            source         = self.name,
            correlation_id = f"{channel_id}:{ts}",
            payload        = {
                "channel_id":  channel_id,
                "ts":          ts,
                "user":        user,
                "text":        text,
                "subtype":     subtype,
                "thread_ts":   thread_ts,
                "timestamp":   _ts_to_iso(ts) if ts else None,
                "attachments": message.get("attachments", []),
                "blocks":      message.get("blocks", []),
                "reactions":   message.get("reactions", []),
            },
        )


# ── Helpers ────────────────────────────────────────────────────────────────────


def _now_unix() -> float:
    return datetime.now(tz=timezone.utc).timestamp()


def _ts_to_iso(ts: str) -> Optional[str]:
    """Convert a Slack ts (e.g. '1715000000.123456') to ISO 8601 UTC string."""
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    except (ValueError, OSError):
        return None
