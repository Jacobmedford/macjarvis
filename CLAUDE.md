# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This is a LiveKit Agents voice AI starter project. It implements a voice pipeline agent using OpenAI (LLM), Deepgram (STT), Cartesia (TTS), Silero (VAD), and LiveKit's multilingual turn detector.

## Environment Setup

Copy `.env.example` to `.env.local` and populate:
- `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`
- `OPENAI_API_KEY`, `DEEPGRAM_API_KEY`, `CARTESIA_API_KEY`

## Commands

```bash
# Install dependencies
uv sync

# First run: download VAD and turn detection models
uv run src/agent.py download-files

# Run modes
uv run src/agent.py console   # Interactive terminal session
uv run src/agent.py dev       # Development server with auto-reload
uv run src/agent.py start     # Production worker

# Tests (require OPENAI_API_KEY; tests use real LLM calls)
uv run pytest -v
uv run pytest tests/test_agent.py::test_weather_tool  # Single test

# Linting / formatting
uv run ruff check .
uv run ruff format .
```

## Architecture

All agent logic lives in `src/agent.py`. There are two main components:

**`Assistant` class** (`Agent` subclass) — defines the agent's personality via `instructions` and its tools via `@function_tool`-decorated async methods. Add new capabilities here.

**`entrypoint` function** — constructs the `AgentSession` with the full voice pipeline (LLM → STT → TTS → VAD → turn detection), wires up event handlers (false interruption recovery, metrics), and connects to the LiveKit room. The `prewarm` function pre-loads Silero VAD in the worker process before jobs arrive.

The pipeline flow: user audio → Deepgram STT → GPT-4o-mini LLM (with function tools) → Cartesia TTS → audio output. Turn detection uses `MultilingualModel` with preemptive generation enabled so the LLM can start responding before the user finishes speaking.

## Testing Approach

Tests in `tests/test_agent.py` use LiveKit's built-in evaluation framework. Each test:
1. Starts an `AgentSession` with a real OpenAI LLM (text-only, no audio)
2. Calls `session.run(user_input=...)` to simulate a user turn
3. Uses `result.expect` to assert on event sequences (function calls, outputs, messages)
4. Uses `.judge(llm, intent=...)` for LLM-based evaluation of natural language responses

To mock tool outputs instead of calling real implementations, use `mock_tools(Assistant, {"tool_name": lambda: "mock_value"})`.

## Switching AI Providers

- **Realtime model** (instead of pipeline): Replace `AgentSession(llm=..., stt=..., tts=..., ...)` with `AgentSession(llm=openai.realtime.RealtimeModel())` — the commented example is in `entrypoint`.
- **Other STT/TTS/LLM providers**: See LiveKit Agents integration docs. Swap the plugin import and constructor.
- **Avatar**: Uncomment the `hedra.AvatarSession` block in `entrypoint`.
- **Self-hosting** (no LiveKit Cloud): Remove `noise_cancellation=noise_cancellation.BVC()` from `room_input_options`.
