"""Jarvis Discord voice session.

Uses discord-ext-voice-recv for audio receiving (discord.py removed this).
Transcribes with Deepgram, responds with GPT-4o-mini, plays back via OpenAI TTS.
"""

import asyncio
import logging
import struct
import tempfile

import discord
from discord.ext import voice_recv
from openai import AsyncOpenAI
import httpx
from sqlalchemy import select

from core.config import settings
from core.database import AsyncSessionLocal, EmailSummary, Task

logger = logging.getLogger("discord_voice")

# Load libopus explicitly — discord.py searches by name only but Homebrew
# installs it at a non-standard path on Apple Silicon.
if not discord.opus.is_loaded():
    for _opus_path in (
        "/opt/homebrew/lib/libopus.dylib",
        "/usr/local/lib/libopus.dylib",
        "libopus",
    ):
        try:
            discord.opus.load_opus(_opus_path)
            logger.info("Loaded libopus from %s", _opus_path)
            break
        except OSError:
            pass

SPEECH_THRESHOLD = 50       # RMS energy threshold
CHUNK_SECONDS = 1.5         # Seconds per recording chunk
MIN_SPEECH_BYTES = 48000 * 2 * 2 // 8  # ~0.15s of audio


def _rms(pcm_bytes: bytes) -> float:
    """RMS energy of raw 16-bit little-endian PCM."""
    if len(pcm_bytes) < 2:
        return 0.0
    n = len(pcm_bytes) // 2
    samples = struct.unpack(f"<{n}h", pcm_bytes[: n * 2])
    return (sum(s * s for s in samples) / n) ** 0.5


class _AudioBuffer(voice_recv.AudioSink):
    """Collects raw PCM per user into a buffer."""

    def __init__(self):
        super().__init__()
        self.buffers: dict[int, bytearray] = {}
        self._decoders: dict[int, discord.opus.Decoder] = {}

    def wants_opus(self) -> bool:
        # Receive raw Opus and decode ourselves so we can swallow
        # corrupted-frame errors instead of crashing the router thread.
        return True

    def write(self, user, data: voice_recv.VoiceData):
        uid = user.id if user else 0
        opus_bytes = data.opus
        if not opus_bytes:
            return
        try:
            if uid not in self._decoders:
                self._decoders[uid] = discord.opus.Decoder()
            pcm = self._decoders[uid].decode(opus_bytes, fec=False)
        except Exception:
            return  # skip corrupt frames silently
        if uid not in self.buffers:
            self.buffers[uid] = bytearray()
        self.buffers[uid].extend(pcm)

    def cleanup(self):
        self.buffers.clear()
        self._decoders.clear()


async def _transcribe(pcm_bytes: bytes) -> str:
    """Send raw PCM to Deepgram as WAV and return transcript."""
    import io, wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(48000)
        wf.writeframes(pcm_bytes)
    wav_bytes = buf.getvalue()

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.deepgram.com/v1/listen",
            headers={
                "Authorization": f"Token {settings.deepgram_api_key}",
                "Content-Type": "audio/wav",
            },
            content=wav_bytes,
            params={"model": "nova-3", "language": "en"},
        )
        resp.raise_for_status()
        data = resp.json()
        return data["results"]["channels"][0]["alternatives"][0]["transcript"]


async def _build_context() -> str:
    async def _calendar() -> str:
        try:
            from integrations.calendar.client import get_today_events
            events = await get_today_events()
            if events:
                return "Today's meetings: " + ", ".join(e["title"] for e in events)
        except Exception:
            pass
        return ""

    async def _emails() -> str:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(EmailSummary).order_by(EmailSummary.created_at.desc()).limit(3)
            )
            emails = result.scalars().all()
        if emails:
            return "Recent emails: " + "; ".join(f"{e.sender}: {e.subject}" for e in emails)
        return ""

    async def _tasks() -> str:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Task).where(Task.status == "pending").limit(5)
            )
            pending = result.scalars().all()
        if pending:
            return "Pending tasks: " + "; ".join(t.title for t in pending)
        return ""

    results = await asyncio.gather(_calendar(), _emails(), _tasks())
    return "\n".join(r for r in results if r)


async def _get_response(transcript: str) -> str:
    context = await _build_context()
    client = AsyncOpenAI(api_key=settings.openai_api_key)
    system = (
        "You are Jarvis — a sophisticated personal AI assistant in a Discord voice channel. "
        "Be concise: 1-2 sentences. Plain speech only — no lists, no markdown, no emojis."
    )
    if context:
        system += f"\n\nContext:\n{context}"
    resp = await client.chat.completions.create(
        model="gpt-4o-mini",
        max_tokens=150,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": transcript},
        ],
    )
    return resp.choices[0].message.content.strip()


async def _synthesize(text: str) -> bytes:
    client = AsyncOpenAI(api_key=settings.openai_api_key)
    resp = await client.audio.speech.create(model="tts-1", voice="onyx", input=text)
    return resp.content


class VoiceSession:
    """Manages Jarvis in a Discord voice channel."""

    def __init__(self, vc: discord.VoiceClient, text_channel: discord.abc.Messageable):
        self.vc = vc
        self.text_channel = text_channel
        self._active = False
        self._responding = False
        self._loop_task: asyncio.Task | None = None
        self._bg_tasks: set[asyncio.Task] = set()

    async def start(self) -> None:
        self._active = True
        self._loop_task = asyncio.create_task(self._conversation_loop())
        self._bg_tasks.add(self._loop_task)
        self._loop_task.add_done_callback(self._bg_tasks.discard)
        await self.text_channel.send(
            "Jarvis is listening. Speak naturally — I process audio every few seconds."
        )

    async def stop(self) -> None:
        self._active = False
        if self._loop_task:
            self._loop_task.cancel()
        if self.vc.is_playing():
            self.vc.stop()
        if self.vc.is_connected():
            await self.vc.disconnect()

    async def _conversation_loop(self) -> None:
        while self._active and self.vc.is_connected():
            try:
                if self._responding or self.vc.is_playing():
                    await asyncio.sleep(0.5)
                    continue

                # Attach audio sink and collect a chunk
                sink = _AudioBuffer()
                self.vc.listen(sink)
                await asyncio.sleep(CHUNK_SECONDS)
                self.vc.stop_listening()

                # Find loudest speaker
                best_user_id = None
                best_rms = 0.0

                for uid, pcm in sink.buffers.items():
                    if len(pcm) < MIN_SPEECH_BYTES:
                        continue
                    energy = _rms(bytes(pcm))
                    if energy > SPEECH_THRESHOLD and energy > best_rms:
                        best_rms = energy
                        best_user_id = uid
                        best_pcm = bytes(pcm)

                if best_user_id:
                    task = asyncio.create_task(self._handle_speech(best_pcm))
                    self._bg_tasks.add(task)
                    task.add_done_callback(self._bg_tasks.discard)

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Voice loop error: %s", exc)
                await self.text_channel.send(f"[error] {exc}")
                await asyncio.sleep(1)

    async def _handle_speech(self, pcm_bytes: bytes) -> None:
        self._responding = True
        try:
            transcript = await _transcribe(pcm_bytes)
            if not transcript.strip():
                return

            logger.info("Heard: %s", transcript)
            await self.text_channel.send(f"**You:** {transcript}")

            reply = await _get_response(transcript)
            logger.info("Jarvis: %s", reply)
            await self.text_channel.send(f"**Jarvis:** {reply}")

            mp3_bytes = await _synthesize(reply)
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                f.write(mp3_bytes)
                tmp_path = f.name

            self.vc.play(discord.FFmpegPCMAudio(tmp_path))
            while self.vc.is_playing():
                await asyncio.sleep(0.1)

        except Exception as exc:
            logger.error("Speech handling error: %s", exc)
            await self.text_channel.send(f"Sorry, I hit an error: {exc}")
        finally:
            self._responding = False
