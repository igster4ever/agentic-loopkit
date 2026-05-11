"""
tests/govkit/test_module_boundaries.py — Module boundary enforcement.

Verifies the one-way dependency contract:
  agentic_govkit  → agentic_loopkit (public API only)
  agentic_loopkit → agentic_govkit  (NEVER)

These are structural tests — they catch import leaks at CI time rather than
at production runtime, matching the spirit of Spring Modulith's ArchUnit checks.
"""

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
GOVKIT_DIR = REPO_ROOT / "agentic_govkit"
LOOPKIT_DIR = REPO_ROOT / "agentic_loopkit"


def _grep(pattern: str, directory: Path) -> str:
    result = subprocess.run(
        ["grep", "-r", "--include=*.py", pattern, str(directory)],
        capture_output=True, text=True,
    )
    return result.stdout


def test_govkit_does_not_import_loopkit_private_internals():
    """govkit must only import from agentic_loopkit's public __init__ surface."""
    hits = _grep(r"from agentic_loopkit\._", GOVKIT_DIR)
    assert hits == "", (
        "agentic_govkit imports private loopkit internals — use the public API only:\n"
        + hits
    )


def test_loopkit_does_not_import_govkit():
    """Core loopkit must have zero knowledge of govkit — one-way dependency only."""
    hits = _grep("agentic_govkit", LOOPKIT_DIR)
    assert hits == "", (
        "agentic_loopkit references agentic_govkit — dependency must be one-way:\n"
        + hits
    )


def test_govkit_governance_stream_is_namespaced():
    """All GovernanceEventType values must start with 'governance.' prefix."""
    from agentic_govkit.events.models import GovernanceEventType
    for member in GovernanceEventType:
        assert str(member).startswith("governance."), (
            f"GovernanceEventType.{member.name} = '{member}' "
            "must start with 'governance.' to stay on its own stream"
        )
