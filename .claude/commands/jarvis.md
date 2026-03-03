Start all Jarvis services in the project at /Users/jakemedford/agent-starter-python:
1. Dashboard: uv run uvicorn src.dashboard.app:app --host 0.0.0.0 --port 8080
2. Email worker: uv run python -m workers.email_summariser
3. Slack bot: uv run python -m integrations.slack.bot
4. Discord bot: uv run python -m integrations.discord.bot

Start each as a background task, wait ~5 seconds, then confirm each is running.
Report status in a clean table. Remind the user to run:
  uv run src/agent.py dev
in a terminal to start the voice agent (requires audio access, can't run headless).
