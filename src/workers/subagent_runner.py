"""Sub-agent runner.

Dispatches tasks to Claude models based on complexity:
- Haiku (claude-haiku-4-5): fast inline tasks
- Opus 4.6 (claude-opus-4-6): deep coding and research, async with DB tracking

Opus tasks send a Discord webhook notification on completion if
DISCORD_WEBHOOK_URL is configured.
"""

import asyncio
import logging
from datetime import datetime, timezone

import anthropic
import httpx

from core.config import settings
from core.database import AsyncSessionLocal, SubAgentTask, init_db

logger = logging.getLogger("subagent_runner")

# Keep strong references to background tasks to prevent premature GC
_background_tasks: set[asyncio.Task] = set()


def _make_client() -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)


async def run_haiku_task(description: str, context_data: str = "") -> str:
    """Run a quick task with Claude Haiku and return the result inline.

    Args:
        description: What to do.
        context_data: Optional extra context to include in the prompt.

    Returns:
        The model's response as a string.
    """
    client = _make_client()
    prompt = description
    if context_data:
        prompt = f"{description}\n\nContext:\n{context_data}"

    try:
        message = await client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text.strip()
    except Exception as exc:
        logger.warning("Haiku task failed: %s", exc)
        return f"Task failed: {exc}"


async def _send_discord_notification(task_id: int, description: str, output: str) -> None:
    """POST a completion notification to the configured Discord webhook."""
    if not settings.discord_webhook_url:
        return

    # Truncate output to fit Discord embed limits
    preview = output[:1000] + ("…" if len(output) > 1000 else "")
    payload = {
        "embeds": [
            {
                "title": f"Opus sub-agent #{task_id} complete",
                "description": f"**Task:** {description[:200]}\n\n**Result:**\n{preview}",
                "color": 0x5865F2,
            }
        ]
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(settings.discord_webhook_url, json=payload)
            resp.raise_for_status()
    except Exception as exc:
        logger.warning("Discord webhook notification failed: %s", exc)


async def run_opus_task(description: str, context_data: str = "") -> int:
    """Run a deep coding/research task with Claude Opus 4.6 asynchronously.

    Creates a SubAgentTask DB record, runs the task in the background,
    stores output, and sends a Discord notification when done.

    Args:
        description: What to do.
        context_data: Optional extra context.

    Returns:
        The SubAgentTask.id for tracking via /api/agents.
    """
    await init_db()

    # Create DB record
    async with AsyncSessionLocal() as session:
        task = SubAgentTask(
            description=description,
            model_used="claude-opus-4-6",
            status="queued",
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        task_id = task.id

    # Run in background so voice agent isn't blocked
    # Keep a reference to prevent garbage collection before the task completes
    bg_task = asyncio.create_task(_execute_opus_task(task_id, description, context_data))
    _background_tasks.add(bg_task)
    bg_task.add_done_callback(_background_tasks.discard)
    return task_id


async def _execute_opus_task(task_id: int, description: str, context_data: str) -> None:
    """Internal: run Opus, update DB, notify Discord."""
    # Mark running
    async with AsyncSessionLocal() as session:
        result = await session.get(SubAgentTask, task_id)
        if result:
            result.status = "running"
            await session.commit()

    client = _make_client()
    prompt = description
    if context_data:
        prompt = f"{description}\n\nContext:\n{context_data}"

    try:
        message = await client.messages.create(
            model="claude-opus-4-6",
            max_tokens=4096,
            system=(
                "You are Jarvis's deep-research and coding sub-agent. "
                "Provide thorough, high-quality solutions. "
                "Format code in markdown code blocks."
            ),
            messages=[{"role": "user", "content": prompt}],
        )
        output = message.content[0].text.strip()
        final_status = "done"
    except Exception as exc:
        output = f"Task failed: {exc}"
        final_status = "failed"
        logger.warning("Opus task %d failed: %s", task_id, exc)

    # Store result
    async with AsyncSessionLocal() as session:
        record = await session.get(SubAgentTask, task_id)
        if record:
            record.status = final_status
            record.output = output
            record.completed_at = datetime.now(timezone.utc)
            await session.commit()

    logger.info("Opus task %d %s.", task_id, final_status)
    await _send_discord_notification(task_id, description, output)
