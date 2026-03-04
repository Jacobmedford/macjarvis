"""Jarvis persistent state manager.

Stores lightweight JSON state at data/jarvis_state.json.
State is shared between the voice agent, presence monitor, and Discord bot.
"""

import json
from pathlib import Path
from typing import Any

STATE_PATH = Path("data/jarvis_state.json")

_DEFAULT_STATE: dict[str, Any] = {
    "voice_enabled": True,
    "muted_reason": None,  # None | "meeting" | "manual"
    "last_briefing_date": None,
}


def load_state() -> dict[str, Any]:
    """Load state from disk, returning defaults for any missing keys."""
    if STATE_PATH.exists():
        try:
            data = json.loads(STATE_PATH.read_text())
            return {**_DEFAULT_STATE, **data}
        except (json.JSONDecodeError, OSError):
            pass
    return dict(_DEFAULT_STATE)


def save_state(state: dict[str, Any]) -> None:
    """Persist state to disk atomically."""
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2))


def set_voice_enabled(enabled: bool, reason: str | None = None) -> None:
    """Set voice enabled/disabled and persist."""
    state = load_state()
    state["voice_enabled"] = enabled
    state["muted_reason"] = None if enabled else reason
    save_state(state)
