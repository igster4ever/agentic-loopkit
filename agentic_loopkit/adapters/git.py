"""
agentic_loopkit/adapters/git.py — Local git repository polling adapter.

Polls a local git repository for new commits using stdlib subprocess.
Zero external dependencies — git must be on the system PATH.

Cursor strategy:
    The cursor is the SHA of the last commit emitted.  On each tick the
    adapter runs ``git log {cursor}..HEAD`` to fetch only new commits.
    On first run (cursor=None) it fetches commits from the last 24 hours
    (configurable via initial_since_hours).

Event types:
    git.commit_added — a new commit appeared in the repository

Usage:

    from agentic_loopkit.adapters.git import LocalGitAdapter, GitEventType
    from pathlib import Path

    adapter = LocalGitAdapter(
        bus      = bus,
        repo_path = Path("/Users/ivan/projects/my-repo"),
    )
    bus.add_adapter(adapter)
    await adapter.tick()
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Optional

from ..events.models import Event
from .base import PollingAdapter

log = logging.getLogger("agentic_loopkit.adapter.git")

# Record-based format — each commit prefixed with a marker line.
# %n is a literal newline in git format; fields are on separate lines.
# LOOPKIT markers are chosen to be collision-free with real commit data.
_RECORD_START  = "LOOPKIT_COMMIT_START"
_FULL_FORMAT   = f"{_RECORD_START}%n%H%n%an%n%ae%n%aI%n%s%n%D%nLOOPKIT_COMMIT_END"

# ── Event types ────────────────────────────────────────────────────────────────


class GitEventType(StrEnum):
    """Domain events emitted by LocalGitAdapter.  Stream: 'git'."""
    COMMIT_ADDED = "git.commit_added"


# ── Adapter ────────────────────────────────────────────────────────────────────


class LocalGitAdapter(PollingAdapter):
    """
    Polls a local git repo for new commits and emits git.commit_added events.

    Attributes:
        name                 adapter identifier
        repo_path            absolute path to the git repository root
        branch               branch to follow (default: current HEAD)
        initial_since_hours  how far back to look on first run (default: 24h)
    """

    name = "git"

    def __init__(
        self,
        bus,
        repo_path:           Path,
        branch:              Optional[str] = None,
        initial_since_hours: int           = 24,
    ) -> None:
        self._repo_path           = Path(repo_path)
        self._branch              = branch
        self._initial_since_hours = initial_since_hours
        super().__init__(bus)

    # ── PollingAdapter contract ────────────────────────────────────────────────

    async def poll(
        self, cursor: Optional[Any]
    ) -> tuple[list[Event], Optional[Any]]:
        """
        Fetch commits added since cursor.

        Cursor is the SHA of the last seen commit.
        Returns (events, new_cursor) where new_cursor is the SHA of the newest
        commit seen, or None if no new commits were found.
        """
        commits = await self._fetch_commits(cursor)
        if not commits:
            return [], None

        events = [self._commit_to_event(c) for c in commits]
        # commits are newest-first (git default); cursor = newest SHA
        new_cursor = commits[0]["sha"]

        log.debug("[git] fetched %d commit(s) from %s", len(commits), self._repo_path)
        return events, new_cursor

    # ── Git fetching ──────────────────────────────────────────────────────────

    async def _fetch_commits(self, cursor: Optional[str]) -> list[dict]:
        """
        Run git log and parse results.  Returns commits newest-first.

        Two strategies:
          cursor = None  →  ``git log --since="<N> hours ago"``
          cursor = SHA   →  ``git log {sha}..HEAD`` (excludes cursor commit)
        """
        cmd = ["git", "-C", str(self._repo_path), "log"]

        if cursor:
            # Incremental: only commits after the cursor SHA
            rev_range = f"{cursor}..HEAD"
            if self._branch:
                rev_range = f"{cursor}..{self._branch}"
            cmd += [rev_range]
        else:
            # First run: last N hours
            since = (
                datetime.now(tz=timezone.utc) - timedelta(hours=self._initial_since_hours)
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
            cmd += [f"--since={since}"]
            if self._branch:
                cmd += [self._branch]

        cmd += [f"--format={_FULL_FORMAT}"]

        try:
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            log.error("[git] git log timed out for %s", self._repo_path)
            return []
        except FileNotFoundError:
            log.error("[git] git not found on PATH — cannot poll %s", self._repo_path)
            return []

        if result.returncode != 0:
            log.warning(
                "[git] git log returned %d for %s: %s",
                result.returncode,
                self._repo_path,
                result.stderr.strip(),
            )
            return []

        return _parse_git_log(result.stdout)

    # ── Event mapping ──────────────────────────────────────────────────────────

    def _commit_to_event(self, commit: dict) -> Event:
        """Map a parsed commit dict to a loopkit Event."""
        return Event(
            event_type     = GitEventType.COMMIT_ADDED,
            source         = self.name,
            correlation_id = commit["sha"],
            payload        = {
                "sha":          commit["sha"],
                "short_sha":    commit["sha"][:8],
                "author_name":  commit["author_name"],
                "author_email": commit["author_email"],
                "timestamp":    commit["timestamp"],
                "subject":      commit["subject"],
                "refs":         commit["refs"],
                "repo_path":    str(self._repo_path),
            },
        )


# ── Parsing ────────────────────────────────────────────────────────────────────


def _parse_git_log(output: str) -> list[dict]:
    """Parse git log output produced by _FULL_FORMAT.

    Each commit is delimited by LOOPKIT_COMMIT_START / LOOPKIT_COMMIT_END
    marker lines.  Fields inside each block are on separate lines in order:
    SHA, author name, author email, ISO timestamp, subject, refs.

    Returns a list of commit dicts, newest-first (git's default order).
    """
    commits: list[dict] = []

    # Split on the start marker; first element is empty or preamble
    for block in output.split(_RECORD_START + "\n"):
        block = block.strip()
        if not block:
            continue

        # Strip the end marker if present
        block = block.replace("LOOPKIT_COMMIT_END", "").strip()
        lines = [l.strip() for l in block.splitlines()]

        # Need at least 5 lines: sha, author_name, author_email, ts, subject.
        # refs (line 6) is optional — empty when no branch/tag decorations.
        if len(lines) < 5:
            log.warning("[git] unexpected git log block (got %d lines): %r", len(lines), block[:120])
            continue

        sha          = lines[0]
        author_name  = lines[1]
        author_email = lines[2]
        timestamp    = lines[3]
        subject      = lines[4]
        refs         = lines[5] if len(lines) > 5 else ""

        if not sha:
            continue

        commits.append({
            "sha":          sha,
            "author_name":  author_name,
            "author_email": author_email,
            "timestamp":    _normalise_timestamp(timestamp),
            "subject":      subject,
            "refs":         [r.strip() for r in refs.split(",") if r.strip()],
        })

    return commits


def _normalise_timestamp(ts: str) -> str:
    """Convert git ISO 8601 author date to UTC ISO string."""
    if not ts:
        return ""
    try:
        # git outputs dates like "2026-05-05T07:06:07+01:00"
        dt = datetime.fromisoformat(ts)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return ts
