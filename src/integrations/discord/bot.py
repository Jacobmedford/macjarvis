"""Discord bot for Jarvis.

Commands:
  !task <title>   — add a task
  !done <title>   — mark a task done
  !tasks          — list all tasks
  !voice on/off   — manually enable/disable Jarvis voice output
  !status         — show voice state and next meeting

Natural language:
  DM the bot or mention @Jarvis with any message — Jarvis replies via
  Claude Sonnet 4.6 with awareness of your calendar, emails, and tasks.

Maintains a pinned message in #tasks with live ✅/⬜ status.
Polls the database every 30s to sync voice-added tasks.

Run:
    uv run python -m integrations.discord.bot
"""

import asyncio
import json
import logging
from pathlib import Path

import anthropic
import discord
from discord.ext import commands, tasks
from sqlalchemy import select

from core.config import settings
from core.database import AgentEvent, AsyncSessionLocal, EmailSummary, Task, init_db
from core.jarvis_state import load_state, set_voice_enabled

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("discord_bot")

STATE_PATH = Path("data/discord_state.json")

intents = discord.Intents.default()
intents.message_content = True
intents.dm_messages = True
bot = commands.Bot(command_prefix="!", intents=intents)


# ─── State helpers ──────────────────────────────────────────────────────────


def _load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state))


# ─── Task board helpers ──────────────────────────────────────────────────────


async def _build_task_board() -> str:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Task).order_by(Task.created_at.asc()))
        task_list = result.scalars().all()

    if not task_list:
        return "**Task Board**\n_No tasks yet. Add one with_ `!task <title>`"

    lines = ["**Task Board**"]
    for t in task_list:
        mark = "✅" if t.status == "done" else "⬜"
        lines.append(f"{mark} {t.title}")
    return "\n".join(lines)


async def _update_pinned_message(channel: discord.TextChannel) -> None:
    state = _load_state()
    content = await _build_task_board()
    pin_id = state.get("pinned_message_id")

    if pin_id:
        try:
            msg = await channel.fetch_message(int(pin_id))
            await msg.edit(content=content)
            return
        except discord.NotFound:
            pass

    msg = await channel.send(content)
    try:
        await msg.pin()
    except discord.Forbidden:
        logger.warning("Bot lacks permission to pin messages.")
    state["pinned_message_id"] = str(msg.id)
    _save_state(state)


def _get_tasks_channel(guild: discord.Guild):
    for ch in guild.text_channels:
        if ch.name == "tasks":
            return ch
    return None


# ─── Natural language context builder ────────────────────────────────────────


async def _build_nl_context() -> str:
    """Gather calendar, email, and task context for NL replies."""
    parts: list[str] = []

    # Calendar
    try:
        from integrations.calendar.client import get_today_events

        events = await get_today_events()
        if events:
            ev_lines = []
            for ev in events:
                if ev["is_all_day"]:
                    ev_lines.append(f"All day: {ev['title']}")
                else:
                    ev_lines.append(f"{ev['title']} ({ev['start']} - {ev['end']})")
            parts.append("Today's calendar:\n" + "\n".join(ev_lines))
        else:
            parts.append("Calendar: No events today.")
    except Exception as exc:
        logger.debug("Calendar unavailable for NL context: %s", exc)

    # Emails
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(EmailSummary).order_by(EmailSummary.created_at.desc()).limit(5)
        )
        emails = result.scalars().all()

    if emails:
        email_lines = [f"- {e.sender}: {e.subject} — {e.summary}" for e in emails]
        parts.append("Recent emails:\n" + "\n".join(email_lines))
    else:
        parts.append("Emails: No recent summaries.")

    # Tasks
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Task).where(Task.status == "pending").limit(10))
        pending = result.scalars().all()

    if pending:
        task_lines = [f"- {t.title}" for t in pending]
        parts.append("Pending tasks:\n" + "\n".join(task_lines))
    else:
        parts.append("Tasks: None pending.")

    return "\n\n".join(parts)


async def _jarvis_nl_reply(user_message: str) -> str:
    """Call Claude Sonnet 4.6 with Jarvis persona and current context."""
    if not settings.anthropic_api_key:
        return "Anthropic API key not configured. I can't process natural language requests."

    context = await _build_nl_context()
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    try:
        message = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=600,
            system=(
                "You are Jarvis — a sophisticated personal AI assistant. "
                "You are professional, efficient, and concise. "
                "You have access to the user's calendar, emails, and tasks shown in the context. "
                "Reply in plain prose — no markdown beyond basic formatting. "
                "Keep responses under 300 words unless the user explicitly asks for detail."
            ),
            messages=[
                {
                    "role": "user",
                    "content": f"Context:\n{context}\n\nUser message: {user_message}",
                }
            ],
        )
        return message.content[0].text.strip()
    except Exception as exc:
        logger.warning("Sonnet NL reply failed: %s", exc)
        return f"I encountered an error processing your request: {exc}"


# ─── Bot events ──────────────────────────────────────────────────────────────


@bot.event
async def on_ready():
    logger.info("Discord bot ready as %s", bot.user)
    await init_db()
    sync_tasks.start()


@bot.event
async def on_message(message: discord.Message):
    # Ignore own messages
    if message.author == bot.user:
        return

    # Check if this is a DM or a mention
    is_dm = isinstance(message.channel, discord.DMChannel)
    is_mention = bot.user in message.mentions if message.mentions else False

    if is_dm or is_mention:
        # Strip the mention prefix if present
        content = message.content
        if is_mention and bot.user:
            content = content.replace(f"<@{bot.user.id}>", "").replace(
                f"<@!{bot.user.id}>", ""
            ).strip()

        # Don't handle if empty after stripping or starts with command prefix
        if content and not content.startswith("!"):
            async with message.channel.typing():
                reply = await _jarvis_nl_reply(content)
            await message.reply(reply)
            return

    # Fall through to command processing
    await bot.process_commands(message)


# ─── Commands ────────────────────────────────────────────────────────────────


@bot.command(name="task")
async def cmd_task(ctx, *, title: str):
    """Add a task: !task <title>"""
    async with AsyncSessionLocal() as session:
        task = Task(title=title, source="discord")
        session.add(task)
        event = AgentEvent(
            event_type="task_added",
            payload=json.dumps({"title": title, "source": "discord"}),
        )
        session.add(event)
        await session.commit()

    await ctx.send(f"⬜ Task added: **{title}**")
    tasks_channel = _get_tasks_channel(ctx.guild)
    if tasks_channel:
        await _update_pinned_message(tasks_channel)


@bot.command(name="done")
async def cmd_done(ctx, *, title: str):
    """Mark a task done: !done <title>"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Task).where(Task.title.ilike(f"%{title}%"), Task.status == "pending")
        )
        task = result.scalar_one_or_none()
        if not task:
            await ctx.send(f"No pending task matching **{title}**.")
            return
        task.status = "done"
        await session.commit()

    await ctx.send(f"✅ Marked done: **{task.title}**")
    tasks_channel = _get_tasks_channel(ctx.guild)
    if tasks_channel:
        await _update_pinned_message(tasks_channel)


@bot.command(name="tasks")
async def cmd_tasks(ctx):
    """List all tasks: !tasks"""
    content = await _build_task_board()
    await ctx.send(content)


@bot.command(name="voice")
async def cmd_voice(ctx, toggle: str = ""):
    """Enable or disable Jarvis voice output: !voice on | !voice off"""
    toggle = toggle.lower().strip()
    if toggle == "on":
        set_voice_enabled(True)
        await ctx.send("Jarvis voice output enabled.")
    elif toggle == "off":
        set_voice_enabled(False, "manual")
        await ctx.send("Jarvis voice output disabled (manual).")
    else:
        state = load_state()
        status = "enabled" if state["voice_enabled"] else f"disabled (reason: {state['muted_reason']})"
        await ctx.send(f"Voice is currently {status}. Use `!voice on` or `!voice off`.")


@bot.command(name="status")
async def cmd_status(ctx):
    """Show Jarvis voice state and next meeting: !status"""
    state = load_state()
    voice_status = "enabled" if state["voice_enabled"] else f"disabled — reason: {state['muted_reason']}"

    next_ev_str = "unknown"
    try:
        from integrations.calendar.client import get_next_event

        event = await get_next_event()
        if event:
            next_ev_str = f"{event['title']} at {event['start']}"
        else:
            next_ev_str = "no upcoming events"
    except Exception:
        next_ev_str = "calendar unavailable"

    await ctx.send(f"**Jarvis Status**\nVoice: {voice_status}\nNext event: {next_ev_str}")


# ─── Background task ─────────────────────────────────────────────────────────


@tasks.loop(seconds=30)
async def sync_tasks():
    """Sync voice-added tasks to Discord #tasks channel."""
    for guild in bot.guilds:
        channel = _get_tasks_channel(guild)
        if channel:
            try:
                await _update_pinned_message(channel)
            except Exception as exc:
                logger.warning("sync_tasks error for guild %s: %s", guild.name, exc)


async def main():
    if not settings.discord_bot_token:
        logger.error("DISCORD_BOT_TOKEN is not set. Exiting.")
        return

    await bot.start(settings.discord_bot_token)


if __name__ == "__main__":
    asyncio.run(main())
