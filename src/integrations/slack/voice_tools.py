"""Voice tools for Slack — imported by the voice agent."""

from livekit.agents import RunContext
from livekit.agents.llm import function_tool

from integrations.slack.client import post_message


@function_tool
async def post_to_slack(context: RunContext, channel: str, message: str):
    """Post a message to a Slack channel.

    Args:
        channel: The Slack channel name (e.g. general) or ID to post to.
        message: The message text to post.
    """
    # Normalise channel name
    if not channel.startswith("#") and not channel.startswith("C"):
        channel = f"#{channel}"

    try:
        await post_message(channel=channel, text=message)
        return f"Message posted to {channel}."
    except Exception as e:
        return f"Failed to post to Slack: {e}"
