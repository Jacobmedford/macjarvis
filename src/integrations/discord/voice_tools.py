"""Voice tools for Discord task management — imported by the voice agent."""

import json

from livekit.agents import RunContext
from livekit.agents.llm import function_tool
from sqlalchemy import select

from core.database import AgentEvent, AsyncSessionLocal, Task


@function_tool
async def add_task(context: RunContext, title: str):
    """Add a task to the task list.

    Args:
        title: The title of the task to add.
    """
    async with AsyncSessionLocal() as session:
        task = Task(title=title, source="voice")
        session.add(task)
        event = AgentEvent(
            event_type="task_added",
            payload=json.dumps({"title": title, "source": "voice"}),
        )
        session.add(event)
        await session.commit()
    return f"Task added: {title}."


@function_tool
async def complete_task(context: RunContext, title: str):
    """Mark a task as done.

    Args:
        title: The title (or part of the title) of the task to mark as done.
    """
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Task).where(Task.title.ilike(f"%{title}%"), Task.status == "pending")
        )
        task = result.scalar_one_or_none()
        if not task:
            return f"No pending task matching '{title}'."
        task.status = "done"
        await session.commit()
    return f"Task '{task.title}' marked as done."


@function_tool
async def list_tasks(context: RunContext):
    """List all pending and completed tasks."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Task).order_by(Task.created_at.asc()))
        task_list = result.scalars().all()

    if not task_list:
        return "Your task list is empty."

    pending = [t.title for t in task_list if t.status == "pending"]
    done = [t.title for t in task_list if t.status == "done"]

    parts = []
    if pending:
        parts.append(f"Pending tasks: {', '.join(pending)}.")
    if done:
        parts.append(f"Completed tasks: {', '.join(done)}.")
    return " ".join(parts)
