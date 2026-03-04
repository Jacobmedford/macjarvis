"""Google Calendar client.

Shared token file: data/google_token.json (covers Gmail + Calendar scopes).

One-time setup:
    uv run python -m integrations.calendar.client

This opens a browser for OAuth consent and saves data/google_token.json.
"""

import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(".env.local")

TOKEN_PATH = Path("data/google_token.json")

# Combined scopes for Gmail + Calendar — re-auth required if token only has Gmail scopes
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.readonly",
]


def _parse_dt(dt_str: str) -> datetime:
    """Parse ISO datetime string, handling 'Z' UTC suffix."""
    if dt_str.endswith("Z"):
        dt_str = dt_str[:-1] + "+00:00"
    return datetime.fromisoformat(dt_str)


def _build_service():
    """Build an authorised Google Calendar service (sync — wrap with asyncio.to_thread)."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            client_config = {
                "installed": {
                    "client_id": os.environ["GOOGLE_CLIENT_ID"],
                    "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
                    "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"],
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            }
            flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
            creds = flow.run_local_server(port=0)

        TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_PATH.write_text(creds.to_json())

    return build("calendar", "v3", credentials=creds)


def _get_today_events_sync() -> list[dict]:
    service = _build_service()
    now = datetime.now(timezone.utc)
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=0)

    result = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=start_of_day.isoformat(),
            timeMax=end_of_day.isoformat(),
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )

    events = []
    for item in result.get("items", []):
        is_all_day = "date" in item["start"] and "dateTime" not in item["start"]
        start = item["start"].get("dateTime", item["start"].get("date", ""))
        end = item["end"].get("dateTime", item["end"].get("date", ""))
        events.append(
            {
                "title": item.get("summary", "(no title)"),
                "start": start,
                "end": end,
                "is_all_day": is_all_day,
            }
        )
    return events


def _get_next_event_sync() -> dict | None:
    service = _build_service()
    now = datetime.now(timezone.utc)

    result = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=now.isoformat(),
            maxResults=1,
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )

    items = result.get("items", [])
    if not items:
        return None

    item = items[0]
    is_all_day = "date" in item["start"] and "dateTime" not in item["start"]
    start = item["start"].get("dateTime", item["start"].get("date", ""))
    end = item["end"].get("dateTime", item["end"].get("date", ""))
    return {
        "title": item.get("summary", "(no title)"),
        "start": start,
        "end": end,
        "is_all_day": is_all_day,
    }


def _is_in_meeting_sync() -> bool:
    """Return True if any calendar event is active right now."""
    service = _build_service()
    now = datetime.now(timezone.utc)

    # Fetch events from 12 hours ago to just past now — catches long ongoing meetings
    result = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=(now - timedelta(hours=12)).isoformat(),
            timeMax=(now + timedelta(minutes=1)).isoformat(),
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )

    for item in result.get("items", []):
        start_str = item["start"].get("dateTime")
        end_str = item["end"].get("dateTime")
        if not start_str or not end_str:
            continue  # Skip all-day events
        start = _parse_dt(start_str)
        end = _parse_dt(end_str)
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        if start <= now <= end:
            return True
    return False


def _get_free_slots_sync(hours: int = 2) -> str:
    """Return the next free block of at least `hours` hours."""
    service = _build_service()
    now = datetime.now(timezone.utc)
    end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=0)

    result = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=now.isoformat(),
            timeMax=end_of_day.isoformat(),
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )

    items = result.get("items", [])
    window_start = now

    for item in items:
        start_str = item["start"].get("dateTime")
        end_str = item["end"].get("dateTime")
        if not start_str or not end_str:
            continue
        start = _parse_dt(start_str)
        end = _parse_dt(end_str)
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        gap = (start - window_start).total_seconds() / 3600
        if gap >= hours:
            return (
                f"Free from {window_start.strftime('%I:%M %p')} "
                f"to {start.strftime('%I:%M %p')} "
                f"({gap:.1f} hours)."
            )
        window_start = max(window_start, end if end.tzinfo else end.replace(tzinfo=timezone.utc))

    remaining = (end_of_day - window_start).total_seconds() / 3600
    if remaining >= hours:
        return f"Free from {window_start.strftime('%I:%M %p')} for the rest of the day."
    return "No free blocks of that length found today."


async def get_today_events() -> list[dict]:
    return await asyncio.to_thread(_get_today_events_sync)


async def get_next_event() -> dict | None:
    return await asyncio.to_thread(_get_next_event_sync)


async def is_in_meeting() -> bool:
    return await asyncio.to_thread(_is_in_meeting_sync)


async def get_free_slots(hours: int = 2) -> str:
    return await asyncio.to_thread(_get_free_slots_sync, hours)


if __name__ == "__main__":
    # One-time OAuth setup — opens browser for consent
    print("Starting Google Calendar OAuth flow…")
    _build_service()
    print(f"Token saved to {TOKEN_PATH}")
    # Quick smoke-test
    events = _get_today_events_sync()
    print(f"Today's events ({len(events)}): {[e['title'] for e in events]}")
