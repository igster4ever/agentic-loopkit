"""
tests/adapters/test_git.py — LocalGitAdapter unit tests.

Uses a real temporary git repository where possible; subprocess calls
are mocked for error / edge-case tests.
"""

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentic_loopkit.bus import EventBus
from agentic_loopkit.adapters.git import (
    LocalGitAdapter,
    GitEventType,
    _parse_git_log,
    _normalise_timestamp,
    _RECORD_START,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_adapter(bus, repo_path, **kwargs) -> LocalGitAdapter:
    return LocalGitAdapter(bus=bus, repo_path=repo_path, **kwargs)


def _git_log_record(sha, author, email, ts, subject, refs="") -> str:
    """Construct one record in the _FULL_FORMAT shape."""
    return f"{_RECORD_START}\n{sha}\n{author}\n{email}\n{ts}\n{subject}\n{refs}\nLOOPKIT_COMMIT_END\n"


def _make_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with one commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@test.com"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"],
                   check=True, capture_output=True)
    (repo / "README.md").write_text("hello")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"],
                   check=True, capture_output=True)
    return repo


# ── Constructor ───────────────────────────────────────────────────────────────

def test_adapter_stores_repo_path(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    adapter = make_adapter(bus, repo_path=repo)
    assert adapter._repo_path == repo


def test_adapter_default_initial_since_hours(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    adapter = make_adapter(bus, repo_path=tmp_path)
    assert adapter._initial_since_hours == 24


# ── _parse_git_log ────────────────────────────────────────────────────────────

def test_parse_git_log_single_commit():
    record = _git_log_record(
        sha="abc123", author="Alice", email="a@a.com",
        ts="2026-05-05T07:00:00+00:00", subject="fix: thing", refs="HEAD -> main"
    )
    commits = _parse_git_log(record)
    assert len(commits) == 1
    c = commits[0]
    assert c["sha"] == "abc123"
    assert c["author_name"] == "Alice"
    assert c["author_email"] == "a@a.com"
    assert c["subject"] == "fix: thing"
    assert any("main" in r for r in c["refs"])


def test_parse_git_log_multiple_commits():
    output = (
        _git_log_record("sha1", "Alice", "a@a.com", "2026-05-05T07:00:00+00:00", "first", "")
        + _git_log_record("sha2", "Bob", "b@b.com", "2026-05-05T06:00:00+00:00", "second", "")
    )
    commits = _parse_git_log(output)
    assert len(commits) == 2
    assert commits[0]["sha"] == "sha1"
    assert commits[1]["sha"] == "sha2"


def test_parse_git_log_empty_output():
    assert _parse_git_log("") == []
    assert _parse_git_log("   \n  ") == []


def test_parse_git_log_skips_malformed_record():
    # Block with fewer than 5 lines (missing required fields)
    bad = f"{_RECORD_START}\nabc123\nAlice\nLOOPKIT_COMMIT_END\n"
    commits = _parse_git_log(bad)
    assert commits == []


# ── _normalise_timestamp ──────────────────────────────────────────────────────

def test_normalise_timestamp_with_offset():
    result = _normalise_timestamp("2026-05-05T08:00:00+01:00")
    assert result == "2026-05-05T07:00:00Z"


def test_normalise_timestamp_utc():
    result = _normalise_timestamp("2026-05-05T07:00:00+00:00")
    assert result == "2026-05-05T07:00:00Z"


def test_normalise_timestamp_empty():
    assert _normalise_timestamp("") == ""


def test_normalise_timestamp_invalid():
    result = _normalise_timestamp("not-a-date")
    assert result == "not-a-date"  # returned as-is


# ── Event mapping ─────────────────────────────────────────────────────────────

def test_commit_to_event_type(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    adapter = make_adapter(bus, repo_path=tmp_path)
    commit = {
        "sha": "abc123", "author_name": "Alice", "author_email": "a@a.com",
        "timestamp": "2026-05-05T07:00:00Z", "subject": "fix", "refs": [],
    }
    event = adapter._commit_to_event(commit)
    assert event.event_type == GitEventType.COMMIT_ADDED


def test_commit_to_event_stream_is_git(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    adapter = make_adapter(bus, repo_path=tmp_path)
    commit = {"sha": "abc123", "author_name": "A", "author_email": "a@a.com",
              "timestamp": "2026-05-05T07:00:00Z", "subject": "s", "refs": []}
    event = adapter._commit_to_event(commit)
    assert event.stream == "git"


def test_commit_to_event_correlation_is_sha(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    adapter = make_adapter(bus, repo_path=tmp_path)
    commit = {"sha": "deadbeef", "author_name": "A", "author_email": "a@a.com",
              "timestamp": "2026-05-05T07:00:00Z", "subject": "s", "refs": []}
    event = adapter._commit_to_event(commit)
    assert event.correlation_id == "deadbeef"


def test_commit_to_event_payload_fields(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    adapter = make_adapter(bus, repo_path=tmp_path)
    commit = {
        "sha": "abc123def456", "author_name": "Alice", "author_email": "alice@example.com",
        "timestamp": "2026-05-05T07:00:00Z", "subject": "feat: add thing", "refs": ["main"],
    }
    event = adapter._commit_to_event(commit)
    p = event.payload
    assert p["sha"]          == "abc123def456"
    assert p["short_sha"]    == "abc123de"
    assert p["author_name"]  == "Alice"
    assert p["author_email"] == "alice@example.com"
    assert p["subject"]      == "feat: add thing"
    assert p["refs"]         == ["main"]
    assert p["repo_path"]    == str(tmp_path)


# ── Poll behaviour ────────────────────────────────────────────────────────────

async def test_poll_no_commits_returns_empty(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    adapter = make_adapter(bus, repo_path=tmp_path)
    adapter._fetch_commits = AsyncMock(return_value=[])
    events, cursor = await adapter.poll(cursor=None)
    assert events == []
    assert cursor is None


async def test_poll_returns_events_and_newest_sha_as_cursor(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    adapter = make_adapter(bus, repo_path=tmp_path)
    commits = [
        {"sha": "new001", "author_name": "A", "author_email": "a@a.com",
         "timestamp": "2026-05-05T08:00:00Z", "subject": "new", "refs": []},
        {"sha": "old001", "author_name": "B", "author_email": "b@b.com",
         "timestamp": "2026-05-05T07:00:00Z", "subject": "old", "refs": []},
    ]
    adapter._fetch_commits = AsyncMock(return_value=commits)
    events, cursor = await adapter.poll(cursor=None)
    assert len(events) == 2
    # cursor = first in list (newest from git log)
    assert cursor == "new001"


# ── Integration: real git repo ────────────────────────────────────────────────

async def test_real_repo_first_run(tmp_path):
    """First-run poll on a real git repo returns at least the initial commit."""
    try:
        repo = _make_repo(tmp_path)
    except subprocess.CalledProcessError:
        pytest.skip("git not available")

    bus = EventBus(store_dir=tmp_path / "store")  # EventBus creates the dir
    adapter = LocalGitAdapter(
        bus=bus,
        repo_path=repo,
        initial_since_hours=24 * 365,   # far enough back to catch the fixture commit
    )
    events, cursor = await adapter.poll(cursor=None)
    assert len(events) >= 1
    assert cursor is not None
    assert events[0].event_type == GitEventType.COMMIT_ADDED
    assert events[0].payload["subject"] == "init"
