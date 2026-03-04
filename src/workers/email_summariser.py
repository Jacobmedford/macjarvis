"""Email summariser worker.

Fetches unread Gmail messages every 5 minutes, summarises them with Claude,
and writes results to the database.

Run:
    uv run python -m workers.email_summariser
"""

import asyncio
import json
import logging
import sys

from openai import AsyncOpenAI

from core.config import settings
from core.database import AgentEvent, AsyncSessionLocal, EmailSummary, init_db
from integrations.gmail.client import get_unread_messages

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("email_summariser")

POLL_INTERVAL = 300  # 5 minutes


def _make_client() -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=settings.openrouter_api_key,
        base_url="https://openrouter.ai/api/v1",
    )


async def summarise(client: AsyncOpenAI, from_: str, subject: str, snippet: str) -> str:
    response = await client.chat.completions.create(
        model=settings.openrouter_model,
        max_tokens=120,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Summarise this email in one concise sentence suitable for voice playback.\n\n"
                    f"From: {from_}\nSubject: {subject}\nSnippet: {snippet}"
                ),
            }
        ],
    )
    return response.choices[0].message.content.strip()


async def run_once(client: AsyncOpenAI) -> int:
    messages = await get_unread_messages(max_results=10)
    if not messages:
        logger.info("No unread messages.")
        return 0

    processed = 0
    async with AsyncSessionLocal() as session:
        for msg in messages:
            # Skip if already summarised
            from sqlalchemy import select
            existing = await session.scalar(
                select(EmailSummary).where(EmailSummary.message_id == msg["id"])
            )
            if existing:
                continue

            try:
                summary = await summarise(client, msg["from"], msg["subject"], msg["snippet"])
            except Exception as exc:
                logger.warning("Summarisation failed for %s: %s", msg["id"], exc)
                summary = msg["snippet"][:200]

            email_row = EmailSummary(
                message_id=msg["id"],
                sender=msg["from"],
                subject=msg["subject"],
                summary=summary,
                raw_snippet=msg["snippet"],
            )
            session.add(email_row)

            event = AgentEvent(
                event_type="email_summarised",
                payload=json.dumps({"from": msg["from"], "subject": msg["subject"]}),
            )
            session.add(event)
            processed += 1

        await session.commit()

    logger.info("Processed %d new emails.", processed)
    return processed


async def main():
    if not settings.openrouter_api_key:
        logger.error("OPENROUTER_API_KEY is not set. Exiting.")
        sys.exit(1)

    await init_db()
    client = _make_client()
    logger.info("Email summariser started. Model: %s  Poll interval: %ds", settings.openrouter_model, POLL_INTERVAL)

    while True:
        try:
            await run_once(client)
        except Exception as exc:
            logger.exception("Unexpected error in run_once: %s", exc)
        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
