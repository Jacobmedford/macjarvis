"""Presence monitor worker.

Polls Google Calendar every 5 minutes. Automatically mutes Jarvis voice
output during meetings and restores it when they end.

Only overrides the muted state if the reason is "meeting" — manual mutes
(reason="manual") are never auto-cleared by this worker.

Run:
    uv run python -m workers.presence_monitor
"""

import asyncio
import logging
import sys

from core.jarvis_state import load_state, set_voice_enabled

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("presence_monitor")

POLL_INTERVAL = 300  # 5 minutes


async def check_once() -> None:
    try:
        from integrations.calendar.client import is_in_meeting

        in_meeting = await is_in_meeting()
    except Exception as exc:
        logger.warning("Calendar check failed: %s", exc)
        return

    state = load_state()

    if in_meeting and state["voice_enabled"]:
        logger.info("Meeting detected — muting voice output.")
        set_voice_enabled(False, "meeting")

    elif not in_meeting and not state["voice_enabled"] and state["muted_reason"] == "meeting":
        logger.info("Meeting ended — restoring voice output.")
        set_voice_enabled(True)

    else:
        status = "in meeting" if in_meeting else "free"
        logger.debug("Status: %s, voice_enabled=%s, reason=%s", status, state["voice_enabled"], state["muted_reason"])


async def main() -> None:
    logger.info("Presence monitor started. Poll interval: %ds", POLL_INTERVAL)
    while True:
        await check_once()
        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Presence monitor stopped.")
        sys.exit(0)
