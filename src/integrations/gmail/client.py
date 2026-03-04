"""Gmail OAuth2 client.

One-time setup:
    uv run python -m integrations.gmail.client

This opens a browser for OAuth consent and saves data/gmail_token.json.
"""

import asyncio
import base64
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(".env.local")

TOKEN_PATH = Path("data/gmail_token.json")
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]


def _build_service():
    """Build an authorised Gmail service (sync — wrap with asyncio.to_thread)."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            client_config = {
                "installed": {
                    "client_id": os.environ["GOOGLE_CLIENT_ID"],
                    "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
                    "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"],
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            }
            flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
            creds = flow.run_local_server(port=0)

        TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_PATH.write_text(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def _get_unread_messages_sync(max_results: int = 10) -> list[dict]:
    service = _build_service()
    result = service.users().messages().list(
        userId="me",
        labelIds=["INBOX", "UNREAD"],
        maxResults=max_results,
    ).execute()

    messages = result.get("messages", [])
    summaries = []
    for msg in messages:
        full = service.users().messages().get(
            userId="me", id=msg["id"], format="metadata",
            metadataHeaders=["From", "Subject"],
        ).execute()

        headers = {h["name"]: h["value"] for h in full.get("payload", {}).get("headers", [])}
        summaries.append({
            "id": msg["id"],
            "from": headers.get("From", ""),
            "subject": headers.get("Subject", "(no subject)"),
            "snippet": full.get("snippet", ""),
        })
    return summaries


def _send_message_sync(to: str, subject: str, body: str) -> dict:
    import email.mime.text

    service = _build_service()
    message = email.mime.text.MIMEText(body)
    message["to"] = to
    message["subject"] = subject
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    return service.users().messages().send(userId="me", body={"raw": raw}).execute()


async def get_unread_messages(max_results: int = 10) -> list[dict]:
    return await asyncio.to_thread(_get_unread_messages_sync, max_results)


async def send_message(to: str, subject: str, body: str) -> dict:
    return await asyncio.to_thread(_send_message_sync, to, subject, body)


if __name__ == "__main__":
    # One-time OAuth setup
    print("Starting Gmail OAuth flow…")
    _build_service()
    print(f"Token saved to {TOKEN_PATH}")
