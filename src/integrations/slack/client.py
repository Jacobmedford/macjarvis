"""Thin async wrapper around the Slack WebClient."""

import asyncio

from slack_sdk import WebClient

from core.config import settings


def _get_client() -> WebClient:
    return WebClient(token=settings.slack_bot_token)


async def post_message(channel: str, text: str) -> dict:
    """Post a message to a Slack channel."""
    client = _get_client()
    return await asyncio.to_thread(
        lambda: client.chat_postMessage(channel=channel, text=text)
    )


async def get_channels() -> list[dict]:
    """List public channels."""
    client = _get_client()
    result = await asyncio.to_thread(
        lambda: client.conversations_list(types="public_channel", limit=200)
    )
    return result.get("channels", [])
