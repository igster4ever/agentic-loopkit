"""
agentic_loopkit/utils/time.py — Shared UTC datetime helpers.

All datetime arithmetic in the loopkit uses UTC.  This module is the
single source of truth for now(), formatting, and millisecond conversions
so that adapter-level helpers don't drift from each other.
"""

from __future__ import annotations

from datetime import datetime, timezone


def utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def iso_format(dt: datetime) -> str:
    """Format a datetime as ISO 8601 UTC string (``YYYY-MM-DDTHH:MM:SSZ``)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def now_ms() -> int:
    """Current UTC time as Unix milliseconds (ClickUp cursor format)."""
    return int(utc_now().timestamp() * 1000)


def now_unix() -> float:
    """Current UTC time as Unix seconds (Slack cursor format)."""
    return utc_now().timestamp()


def ms_to_iso(ms: int) -> str:
    """Convert Unix milliseconds to ISO 8601 UTC string."""
    if not ms:
        return ""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
