"""Jarvis Discord voice session.

Listens in a Discord voice channel, transcribes speech via Deepgram,
generates responses via GPT-4o-mini (with Jarvis persona + context),
and plays back TTS audio via OpenAI.

Architecture:
  - Records audio in 3-second chunks using discord.py WaveSink
  - Simple RMS energy threshold for speech detection
  - Deepgram REST API for STT
  - GPT-4o-mini for response (same model as LiveKit voice agent)
  - OpenAI TTS (tts-1, onyx voice) for playback
"""

import asyncio
import io
import logging
import struct
import tempfile

import discord
import httpx
from openai import AsyncOpenAI
from sqlalchemy import select

from core.config import settings
from core.database import AsyncSessionLocal, EmailSummary, Task

logger = logging.getLogger("discord_voice")

# Speech detection threshold (RMS energy of 16-bit PCM samples)
SPEECH_THRESHOLD = 50
# Seconds of audio to collect per chunk
CHUNK_SECONDS = 3
# Minimum bytes of speech content to bother transcribing (~0.3s at 48kHz stereo 16-bit)
MIN_SPEECH_BYTES = 48000 * 2 * 2 // 4


def _rms(wav_bytes: bytes) -> float:
    """Calculate RMS energy of raw PCM bytes (16-bit little-endian)."""
    if len(wav_bytes) < 2:
        return 0.0
    # Skip WAV header (44 bytes) if present
    data = wav_bytes[44:] if wav_bytes[:4] == b"RIFF" else wav_bytes
    if len(data) < 2:
        return 0.0
    n = len(data) // 2
    samples = struct.unpack(f"<{n}h", data[: n * 2])
    return (sum(s * s for s in samples) / n) ** 0.5


async def _transcribe(wav_bytes: bytes) -> str:
    """Send WAV audio to Deepgram and return the transcript."""
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
    """Gather calendar, email, and task context for Jarvis."""
    parts: list[str] = []

    try:
        from integrations.calendar.client import get_today_events

        events = await get_today_events()
        if events:
            titles = [ev["title"] for ev in events]
            parts.append("Today's meetings: " + ", ".join(titles))
    except Exception:
        pass

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(EmailSummary).order_by(EmailSummary.created_at.desc()).limit(3)
        )
        emails = result.scalars().all()
        if emails:
            parts.append(
                "Recent emails: "
                + "; ".join(f"{e.sender}: {e.subject}" for e in emails)
            )

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Task).where(Task.status == "pending").limit(5)
        )
        pending = result.scalars().all()
        if pending:
            parts.append("Pending tasks: " + "; ".join(t.title for t in pending))

    return "\n".join(parts) if parts else ""


async def _get_response(transcript: str) -> str:
    """Generate Jarvis reply via GPT-4o-mini."""
    context = await _build_context()
    client = AsyncOpenAI(api_key=settings.openai_api_key)

    system = (
        "You are Jarvis — a sophisticated personal AI assistant in a Discord voice channel. "
        "Be concise: 1-2 sentences max. Plain speech only — no lists, no markdown, no emojis."
    )
    if context:
        system += f"\n\nContext:\n{context}"

    response = await client.chat.completions.create(
        model="gpt-4o-mini",
        max_tokens=150,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": transcript},
        ],
    )
    return response.choices[0].message.content.strip()


async def _synthesize(text: str) -> bytes:
    """Convert text to MP3 audio using OpenAI TTS."""
    client = AsyncOpenAI(api_key=settings.openai_api_key)
    response = await client.audio.speech.create(
        model="tts-1",
        voice="onyx",
        input=text,
    )
    return response.content


class VoiceSession:
    """Manages Jarvis listening and speaking in one Discord voice channel."""

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
        logger.info("Voice session started in #%s", self.vc.channel.name)

    async def stop(self) -> None:
        self._active = False
        if self._loop_task:
            self._loop_task.cancel()
        if self.vc.is_playing():
            self.vc.stop()
        if self.vc.is_connected():
            await self.vc.disconnect()
        logger.info("Voice session stopped.")

    async def _conversation_loop(self) -> None:
        """Record in chunks, detect speech, transcribe, respond."""
        await self.text_channel.send("[debug] Conversation loop started.")
        loop = asyncio.get_event_loop()

        while self._active and self.vc.is_connected():
            try:
                # Skip recording while Jarvis is speaking
                if self._responding or self.vc.is_playing():
                    await asyncio.sleep(0.5)
                    continue

                # Record a chunk
                sink = discord.sinks.WaveSink()
                done = asyncio.Event()

                def _after(s, *_):
                    # Called from discord's audio thread — must use threadsafe call
                    loop.call_soon_threadsafe(done.set)

                self.vc.start_recording(sink, _after)
                await self.text_channel.send("[debug] Recording started...")
                await asyncio.sleep(CHUNK_SECONDS)
                self.vc.stop_recording()

                # Wait for recording to fully flush (up to 3s)
                try:
                    await asyncio.wait_for(done.wait(), timeout=3.0)
                except asyncio.TimeoutError:
                    await self.text_channel.send("[debug] Warning: recording done event timed out")

                users_found = list(sink.audio_data.keys())
                await self.text_channel.send(f"[debug] Chunk done. Users with audio: {users_found}")

                # Find user with the most speech energy
                best_user = None
                best_rms = 0.0

                for user_id, audio_data in sink.audio_data.items():
                    member = self.vc.guild.get_member(user_id)
                    if not member or member.bot:
                        continue
                    # Seek to beginning — BytesIO position may be at end after writing
                    audio_data.file.seek(0)
                    wav_bytes = audio_data.file.read()
                    energy = _rms(wav_bytes)
                    await self.text_channel.send(
                        f"[debug] {member.name}: {len(wav_bytes)} bytes, energy={energy:.0f}"
                    )
                    if energy > SPEECH_THRESHOLD and energy > best_rms:
                        best_rms = energy
                        best_user = (user_id, wav_bytes)

                if best_user:
                    _, wav_bytes = best_user
                    task = asyncio.create_task(self._handle_speech(wav_bytes))
                    self._bg_tasks.add(task)
                    task.add_done_callback(self._bg_tasks.discard)

            except Exception as exc:
                logger.exception("Error in conversation loop: %s", exc)
                await self.text_channel.send(f"[debug] Loop error: {exc}")

    async def _handle_speech(self, wav_bytes: bytes) -> None:
        """Transcribe audio, get response, play TTS."""
        self._responding = True
        try:
            transcript = await _transcribe(wav_bytes)
            if not transcript.strip():
                return

            logger.info("Heard: %s", transcript)
            await self.text_channel.send(f"**You:** {transcript}")

            reply = await _get_response(transcript)
            logger.info("Jarvis: %s", reply)
            await self.text_channel.send(f"**Jarvis:** {reply}")

            mp3_bytes = await _synthesize(reply)

            # Write to temp file — FFmpegPCMAudio needs a seekable source
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                f.write(mp3_bytes)
                tmp_path = f.name

            source = discord.FFmpegPCMAudio(tmp_path)
            self.vc.play(source)

            while self.vc.is_playing():
                await asyncio.sleep(0.1)

        except Exception as exc:
            logger.error("Voice handling error: %s", exc)
            await self.text_channel.send(f"Sorry, I hit an error: {exc}")
        finally:
            self._responding = False
