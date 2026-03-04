import logging
import os
import sys
from datetime import date

import httpx
from dotenv import load_dotenv
from livekit.agents import (
    NOT_GIVEN,
    Agent,
    AgentFalseInterruptionEvent,
    AgentSession,
    JobContext,
    JobProcess,
    MetricsCollectedEvent,
    RoomInputOptions,
    RunContext,
    WorkerOptions,
    cli,
    metrics,
)
from livekit.agents.llm import function_tool
from livekit.plugins import cartesia, deepgram, noise_cancellation, openai, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

logger = logging.getLogger("agent")

# WMO weather interpretation codes → plain-English description
_WMO_CODES: dict[int, str] = {
    0: "clear sky",
    1: "mainly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "foggy",
    48: "icy fog",
    51: "light drizzle",
    53: "moderate drizzle",
    55: "heavy drizzle",
    61: "light rain",
    63: "moderate rain",
    65: "heavy rain",
    71: "light snow",
    73: "moderate snow",
    75: "heavy snow",
    77: "snow grains",
    80: "light showers",
    81: "moderate showers",
    82: "heavy showers",
    85: "light snow showers",
    86: "heavy snow showers",
    95: "thunderstorm",
    96: "thunderstorm with light hail",
    99: "thunderstorm with heavy hail",
}

load_dotenv(".env.local")

# Ensure src/ is importable when running directly
_src_dir = os.path.dirname(os.path.abspath(__file__))
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

# Collect optional integration tools
_extra_tools: list = []

try:
    from integrations.gmail.voice_tools import check_emails, reply_to_email
    _extra_tools.extend([check_emails, reply_to_email])
    logger.info("Gmail voice tools loaded.")
except ImportError:
    pass

try:
    from integrations.slack.voice_tools import post_to_slack
    _extra_tools.append(post_to_slack)
    logger.info("Slack voice tools loaded.")
except ImportError:
    pass

try:
    from integrations.discord.voice_tools import add_task, complete_task, list_tasks
    _extra_tools.extend([add_task, complete_task, list_tasks])
    logger.info("Discord voice tools loaded.")
except ImportError:
    pass

try:
    from integrations.calendar.voice_tools import check_calendar, next_meeting
    _extra_tools.extend([check_calendar, next_meeting])
    logger.info("Calendar voice tools loaded.")
except ImportError:
    pass


class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions="""You are Jarvis — Just A Rather Very Intelligent System — a sophisticated personal AI assistant.
            You are professional, efficient, and concise. You speak in a calm, confident tone without being verbose.
            Your responses contain no complex formatting, emojis, asterisks, or other symbols — plain speech only.
            You are aware of and can use all available integrations: Gmail (check emails, send replies),
            Slack (post messages to channels), task management (add, complete, and list tasks),
            Google Calendar (check today's schedule, next meeting), and real-time weather lookups.
            You can delegate complex coding or research tasks to a powerful sub-agent (Opus 4.6),
            and get quick summaries using a fast inline model (Haiku).
            When a user asks about their inbox, messages, tasks, or schedule,
            proactively use the appropriate tool rather than asking for clarification.""",
            tools=_extra_tools,
        )

    @function_tool
    async def lookup_weather(self, context: RunContext, location: str):
        """Use this tool to look up current weather information in the given location.

        If the location is not supported by the weather service, the tool will indicate this. You must tell the user the location's weather is unavailable.

        Args:
            location: The location to look up weather information for (e.g. city name)
        """
        logger.info(f"Looking up weather for {location}")

        async with httpx.AsyncClient() as client:
            # Geocode the city name to lat/lon
            geo = await client.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": location, "count": 1},
            )
            geo.raise_for_status()
            results = geo.json().get("results")
            if not results:
                return f"Weather data is not available for {location}."

            place = results[0]
            lat, lon = place["latitude"], place["longitude"]
            name = place.get("name", location)

            # Fetch current weather
            weather = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "current": "temperature_2m,weather_code,wind_speed_10m",
                    "temperature_unit": "fahrenheit",
                    "wind_speed_unit": "mph",
                },
            )
            weather.raise_for_status()
            current = weather.json()["current"]

        temp = round(current["temperature_2m"])
        wind = round(current["wind_speed_10m"])
        condition = _WMO_CODES.get(current["weather_code"], "unknown conditions")

        return f"{name}: {condition}, {temp}°F, wind {wind} mph."

    @function_tool
    async def mute_voice(self, context: RunContext):
        """Mute Jarvis voice output. Text-based interfaces (Discord) still work."""
        from core.jarvis_state import set_voice_enabled
        set_voice_enabled(False, "manual")
        return "Voice output muted. You can still reach me via Discord."

    @function_tool
    async def unmute_voice(self, context: RunContext):
        """Re-enable Jarvis voice output after a manual or meeting mute."""
        from core.jarvis_state import set_voice_enabled
        set_voice_enabled(True)
        return "Voice output restored."

    @function_tool
    async def spawn_coding_agent(self, context: RunContext, task: str):
        """Delegate a complex coding or research task to a powerful sub-agent (Opus 4.6).

        The sub-agent runs asynchronously. You will receive a task ID immediately,
        and a Discord notification will be sent when the result is ready.

        Args:
            task: Description of the coding or research task to perform.
        """
        from workers.subagent_runner import run_opus_task
        task_id = await run_opus_task(task)
        return (
            f"Sub-agent task {task_id} queued with Opus 4.6. "
            f"I'll notify you via Discord when it's done. "
            f"You can also check progress at /api/agents."
        )

    @function_tool
    async def quick_summary(self, context: RunContext, topic: str):
        """Get a fast summary or answer using Claude Haiku.

        Use this for quick lookups, brief explanations, or lightweight tasks
        that don't require the full power of Opus.

        Args:
            topic: What to summarise or explain.
        """
        from workers.subagent_runner import run_haiku_task
        result = await run_haiku_task(topic)
        return result


def prewarm(proc: JobProcess):
    proc.userdata["vad"] = silero.VAD.load()


async def entrypoint(ctx: JobContext):
    # Logging setup
    ctx.log_context_fields = {
        "room": ctx.room.name,
    }

    # Set up a voice AI pipeline using OpenAI, Cartesia, Deepgram, and the LiveKit turn detector
    session = AgentSession(
        llm=openai.LLM(model="gpt-4o-mini"),
        stt=deepgram.STT(model="nova-3", language="multi"),
        tts=cartesia.TTS(voice="6f84f4b8-58a2-430c-8c79-688dad597532"),
        turn_detection=MultilingualModel(),
        vad=ctx.proc.userdata["vad"],
        preemptive_generation=True,
    )

    @session.on("agent_false_interruption")
    def _on_agent_false_interruption(ev: AgentFalseInterruptionEvent):
        logger.info("false positive interruption, resuming")
        session.generate_reply(instructions=ev.extra_instructions or NOT_GIVEN)

    usage_collector = metrics.UsageCollector()

    @session.on("metrics_collected")
    def _on_metrics_collected(ev: MetricsCollectedEvent):
        metrics.log_metrics(ev.metrics)
        usage_collector.collect(ev.metrics)

    async def log_usage():
        summary = usage_collector.get_summary()
        logger.info(f"Usage: {summary}")

    ctx.add_shutdown_callback(log_usage)

    await session.start(
        agent=Assistant(),
        room=ctx.room,
        room_input_options=RoomInputOptions(
            noise_cancellation=noise_cancellation.BVC(),
        ),
    )

    # Morning briefing on first daily session; regular greeting otherwise
    try:
        from core.database import init_db
        from core.jarvis_state import load_state, save_state
        from workers.morning_briefing import build_morning_briefing

        await init_db()
        state = load_state()
        today = date.today().isoformat()

        if state.get("last_briefing_date") != today:
            briefing = await build_morning_briefing()
            session.generate_reply(
                instructions=f"Deliver this morning briefing naturally in your voice: {briefing}"
            )
            save_state({**state, "last_briefing_date": today})
        else:
            session.generate_reply(
                instructions="Greet the user as Jarvis, briefly introduce your capabilities"
            )
    except Exception as exc:
        logger.warning("Morning briefing failed (%s) — using default greeting.", exc)
        session.generate_reply(
            instructions="Greet the user as Jarvis, briefly introduce your capabilities"
        )

    await ctx.connect()


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, prewarm_fnc=prewarm))
