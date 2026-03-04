"""Morning briefing generator.

Combines today's calendar, recent email summaries, and pending tasks,
then asks Claude Haiku to produce a 3-4 sentence spoken briefing.

Used by the voice agent's entrypoint to greet the user on first
daily connection.
"""

import logging
from datetime import datetime

import anthropic
from sqlalchemy import select

from core.config import settings
from core.database import AsyncSessionLocal, EmailSummary, Task

logger = logging.getLogger("morning_briefing")


async def _get_calendar_summary() -> str:
    """Return a plain-text summary of today's events, or empty string on failure."""
    try:
        from integrations.calendar.client import get_today_events

        events = await get_today_events()
        if not events:
            return "No meetings on the calendar today."
        parts = []
        for ev in events:
            if ev["is_all_day"]:
                parts.append(f"All day: {ev['title']}")
            else:
                try:
                    start = datetime.fromisoformat(ev["start"].replace("Z", "+00:00"))
                    parts.append(f"{ev['title']} at {start.strftime('%-I:%M %p')}")
                except Exception:
                    parts.append(ev["title"])
        return "Today's meetings: " + ", ".join(parts) + "."
    except Exception as exc:
        logger.debug("Calendar unavailable for briefing: %s", exc)
        return ""


async def _get_email_summary() -> str:
    """Return last 5 email summaries as a compact string."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(EmailSummary).order_by(EmailSummary.created_at.desc()).limit(5)
        )
        emails = result.scalars().all()

    if not emails:
        return "No recent email summaries."

    parts = [f"{e.sender.split('<')[0].strip() or e.sender}: {e.subject}" for e in emails]
    return f"{len(emails)} recent emails — " + "; ".join(parts) + "."


async def _get_task_summary() -> str:
    """Return pending tasks as a compact string."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Task).where(Task.status == "pending").limit(10))
        pending = result.scalars().all()

    if not pending:
        return "No pending tasks."

    titles = [t.title for t in pending]
    return f"{len(pending)} pending task{'s' if len(pending) != 1 else ''}: " + "; ".join(titles) + "."


async def build_morning_briefing() -> str:
    """Build a 3-4 sentence spoken morning briefing using Claude Haiku.

    Returns plain text suitable for voice playback.
    Falls back to a simple greeting if the API call fails.
    """
    calendar_str = await _get_calendar_summary()
    email_str = await _get_email_summary()
    task_str = await _get_task_summary()

    raw_data = "\n".join(filter(None, [calendar_str, email_str, task_str]))

    if not settings.anthropic_api_key:
        logger.warning("ANTHROPIC_API_KEY not set — using plain briefing.")
        return f"Good morning. Here is your briefing. {raw_data}"

    try:
        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        message = await client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=200,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "You are Jarvis, a voice assistant. Deliver a concise morning briefing "
                        "in 3-4 sentences. Use plain speech — no lists, no asterisks, no emojis. "
                        "Be warm but efficient.\n\n"
                        f"Data:\n{raw_data}"
                    ),
                }
            ],
        )
        return message.content[0].text.strip()
    except Exception as exc:
        logger.warning("Haiku briefing failed (%s) — falling back to raw data.", exc)
        return f"Good morning. {raw_data}"
