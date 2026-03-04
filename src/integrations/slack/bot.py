"""Slack Socket Mode bot.

Handles @mentions and pushes task/email updates to #ai-assistant.

Run:
    uv run python -m integrations.slack.bot

Slack app setup:
  - OAuth scopes: chat:write, channels:read, channels:history, app_mentions:read
  - Enable Socket Mode
  - Subscribe to app_mention event
"""

import asyncio
import logging

from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp

from core.config import settings
from core.database import init_db
from integrations.slack.client import post_message

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("slack_bot")

app = AsyncApp(token=settings.slack_bot_token)


@app.event("app_mention")
async def handle_mention(event, say):
    user = event.get("user", "")
    text = event.get("text", "")
    logger.info("Mention from %s: %s", user, text)
    await say(f"Hi <@{user}>! I'm your automation assistant. Use the voice agent to manage tasks and emails.")


async def notify_task_added(title: str, channel: str = "#ai-assistant") -> None:
    """Post a task-added notification to Slack."""
    await post_message(channel, f"New task added: *{title}*")


async def notify_email_summary(sender: str, subject: str, summary: str, channel: str = "#ai-assistant") -> None:
    """Post an email summary notification to Slack."""
    await post_message(channel, f"Email from *{sender}*: _{subject}_\n{summary}")


async def main():
    if not settings.slack_bot_token or not settings.slack_app_token:
        logger.error("SLACK_BOT_TOKEN or SLACK_APP_TOKEN is not set. Exiting.")
        return

    await init_db()
    logger.info("Starting Slack Socket Mode bot…")
    handler = AsyncSocketModeHandler(app, settings.slack_app_token)
    await handler.start_async()


if __name__ == "__main__":
    asyncio.run(main())
