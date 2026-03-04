"""Voice tools for Gmail — imported by the voice agent."""

from livekit.agents import RunContext
from livekit.agents.llm import function_tool
from sqlalchemy import select

from core.database import AsyncSessionLocal, EmailSummary


@function_tool
async def check_emails(context: RunContext):
    """Check your recent email summaries.

    Returns a spoken summary of unread emails that have been processed by the email worker.
    """
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(EmailSummary).order_by(EmailSummary.created_at.desc()).limit(5)
        )
        emails = result.scalars().all()

    if not emails:
        return "You have no email summaries yet. Make sure the email summariser worker is running."

    parts = [f"You have {len(emails)} recent email summaries."]
    for i, email in enumerate(emails, 1):
        parts.append(f"{i}. From {email.sender}: {email.subject}. {email.summary}")
    return " ".join(parts)


@function_tool
async def reply_to_email(context: RunContext, recipient: str, body: str):
    """Send an email reply.

    Args:
        recipient: The email address to send the reply to.
        body: The body of the reply email.
    """
    from integrations.gmail.client import send_message

    try:
        await send_message(to=recipient, subject="Re: (voice reply)", body=body)
        return f"Email sent to {recipient}."
    except Exception as e:
        return f"Failed to send email: {e}"
