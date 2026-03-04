"""Chat route — Jarvis text interface in the dashboard."""

import logging

import anthropic
from fastapi import APIRouter
from fastapi.requests import Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select

from fastapi.templating import Jinja2Templates

from core.config import settings
from core.database import AsyncSessionLocal, EmailSummary, Task

logger = logging.getLogger("dashboard.chat")
router = APIRouter()

_TEMPLATES_DIR = str(__file__.replace("routes/chat.py", "templates"))
_templates = Jinja2Templates(directory=_TEMPLATES_DIR)


@router.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request):
    return _templates.TemplateResponse("chat.html", {"request": request})


class ChatMessage(BaseModel):
    message: str


async def _build_context() -> str:
    parts: list[str] = []

    try:
        from integrations.calendar.client import get_today_events
        events = await get_today_events()
        if events:
            lines = []
            for ev in events:
                if ev["is_all_day"]:
                    lines.append(f"All day: {ev['title']}")
                else:
                    lines.append(f"{ev['title']} ({ev['start']} - {ev['end']})")
            parts.append("Today's calendar:\n" + "\n".join(lines))
    except Exception:
        pass

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(EmailSummary).order_by(EmailSummary.created_at.desc()).limit(5)
        )
        emails = result.scalars().all()
    if emails:
        parts.append("Recent emails:\n" + "\n".join(
            f"- {e.sender}: {e.subject} — {e.summary}" for e in emails
        ))

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Task).where(Task.status == "pending").limit(10)
        )
        pending = result.scalars().all()
    if pending:
        parts.append("Pending tasks:\n" + "\n".join(f"- {t.title}" for t in pending))

    return "\n\n".join(parts)


@router.post("/api/chat")
async def chat_api(body: ChatMessage):
    """Stream a Jarvis reply to the dashboard chat."""

    async def generate():
        if not settings.anthropic_api_key:
            yield "Anthropic API key not configured."
            return

        context = await _build_context()
        system = (
            "You are Jarvis — a sophisticated personal AI assistant. "
            "You are professional, efficient, and direct. "
            "You have access to the user's calendar, emails, and tasks shown in the context. "
            "Reply in plain prose. Keep responses concise unless detail is requested."
        )
        if context:
            system += f"\n\nContext:\n{context}"

        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        try:
            async with client.messages.stream(
                model="claude-sonnet-4-6",
                max_tokens=800,
                system=system,
                messages=[{"role": "user", "content": body.message}],
            ) as stream:
                async for text in stream.text_stream:
                    yield text
        except Exception as exc:
            logger.error("Chat API error: %s", exc)
            yield f"Sorry, I hit an error: {exc}"

    return StreamingResponse(generate(), media_type="text/plain")
