import os

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import AgentEvent, EmailSummary, SubAgentTask, Task, get_session

router = APIRouter(prefix="/api")


def _integration_configured(env_vars: list[str]) -> bool:
    return all(bool(os.getenv(v, "").strip()) for v in env_vars)


@router.get("/status")
async def get_status(session: AsyncSession = Depends(get_session)):
    task_count = await session.scalar(select(func.count()).select_from(Task))
    email_count = await session.scalar(select(func.count()).select_from(EmailSummary))
    event_count = await session.scalar(select(func.count()).select_from(AgentEvent))

    integrations = {
        "gmail": _integration_configured(["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"]),
        "slack": _integration_configured(["SLACK_BOT_TOKEN", "SLACK_APP_TOKEN"]),
        "discord": _integration_configured(["DISCORD_BOT_TOKEN"]),
        "anthropic": _integration_configured(["ANTHROPIC_API_KEY"]),
    }

    return {
        "task_count": task_count,
        "email_count": email_count,
        "event_count": event_count,
        "integrations": integrations,
    }


@router.get("/tasks")
async def list_tasks(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(Task).order_by(Task.created_at.desc()).limit(100))
    tasks = result.scalars().all()
    return [
        {
            "id": t.id,
            "title": t.title,
            "status": t.status,
            "source": t.source,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        }
        for t in tasks
    ]


@router.get("/emails")
async def list_emails(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(EmailSummary).order_by(EmailSummary.created_at.desc()).limit(50))
    emails = result.scalars().all()
    return [
        {
            "id": e.id,
            "sender": e.sender,
            "subject": e.subject,
            "summary": e.summary,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in emails
    ]


@router.get("/events")
async def list_events(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(AgentEvent).order_by(AgentEvent.created_at.desc()).limit(50))
    events = result.scalars().all()
    return [
        {
            "id": e.id,
            "event_type": e.event_type,
            "payload": e.payload,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in events
    ]


@router.get("/agents")
async def list_agents(session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(SubAgentTask).order_by(SubAgentTask.created_at.desc()).limit(50)
    )
    tasks = result.scalars().all()
    return [
        {
            "id": t.id,
            "description": t.description,
            "model_used": t.model_used,
            "status": t.status,
            "output": t.output,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "completed_at": t.completed_at.isoformat() if t.completed_at else None,
        }
        for t in tasks
    ]
