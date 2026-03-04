"""Voice tools for Google Calendar — imported by the voice agent."""

from datetime import datetime

from livekit.agents import RunContext
from livekit.agents.llm import function_tool


@function_tool
async def check_calendar(context: RunContext) -> str:
    """Check today's calendar schedule.

    Returns a spoken summary of all events on the user's calendar today.
    """
    from integrations.calendar.client import get_today_events

    try:
        events = await get_today_events()
    except Exception as e:
        return f"Unable to access calendar: {e}"

    if not events:
        return "Your calendar is clear today."

    lines = [f"You have {len(events)} event{'s' if len(events) != 1 else ''} today."]
    for ev in events:
        if ev["is_all_day"]:
            lines.append(f"All day: {ev['title']}.")
        else:
            try:
                start = datetime.fromisoformat(ev["start"].replace("Z", "+00:00"))
                end = datetime.fromisoformat(ev["end"].replace("Z", "+00:00"))
                lines.append(
                    f"{start.strftime('%-I:%M %p')} to {end.strftime('%-I:%M %p')}: {ev['title']}."
                )
            except Exception:
                lines.append(f"{ev['title']} at {ev['start']}.")

    return " ".join(lines)


@function_tool
async def next_meeting(context: RunContext) -> str:
    """Check when the next meeting or calendar event is."""
    from integrations.calendar.client import get_next_event

    try:
        event = await get_next_event()
    except Exception as e:
        return f"Unable to access calendar: {e}"

    if not event:
        return "You have no upcoming events scheduled."

    if event["is_all_day"]:
        return f"Your next event is all day: {event['title']}."

    try:
        start = datetime.fromisoformat(event["start"].replace("Z", "+00:00"))
        return f"Your next meeting is {event['title']} at {start.strftime('%-I:%M %p')}."
    except Exception:
        return f"Your next meeting is {event['title']} at {event['start']}."
