from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env.local",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LiveKit
    livekit_url: str = ""
    livekit_api_key: str = ""
    livekit_api_secret: str = ""

    # OpenAI
    openai_api_key: str = ""

    # Deepgram / Cartesia
    deepgram_api_key: str = ""
    cartesia_api_key: str = ""

    # Anthropic (direct)
    anthropic_api_key: str = ""

    # OpenRouter (preferred — covers any model)
    openrouter_api_key: str = ""
    openrouter_model: str = "anthropic/claude-haiku-4-5"

    # Google / Gmail
    google_client_id: str = ""
    google_client_secret: str = ""

    # Slack
    slack_bot_token: str = ""
    slack_app_token: str = ""

    # Discord
    discord_bot_token: str = ""
    discord_webhook_url: str = ""  # Optional — for subagent completion notifications

    # Dashboard
    dashboard_port: int = 8080

    # Database path
    database_url: str = "sqlite+aiosqlite:///data/automation.db"


settings = Settings()
