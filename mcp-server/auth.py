"""
Credential management and token refresh for the Sangam MCP server.

Credentials are stored at ~/.sangam/credentials.json and shared by the
daemon, CLI, and MCP server.
"""
import json
import httpx
from pathlib import Path
from datetime import datetime, timezone, timedelta
from config import CREDENTIALS_PATH, get_coordinator_url


# ---------------------------------------------------------------------------
# Low-level credential helpers
# ---------------------------------------------------------------------------

def load_credentials() -> dict:
    """Load credentials from disk. Raises FileNotFoundError if missing."""
    if not CREDENTIALS_PATH.exists():
        raise FileNotFoundError(
            f"No credentials found at {CREDENTIALS_PATH}. "
            "Run: sangam register"
        )
    return json.loads(CREDENTIALS_PATH.read_text())


def save_credentials(creds: dict) -> None:
    """Persist credentials to disk, creating parent dirs if needed."""
    CREDENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CREDENTIALS_PATH.write_text(json.dumps(creds, indent=2))


def credentials_exist() -> bool:
    """Return True if a credentials file exists on disk."""
    return CREDENTIALS_PATH.exists()


def is_expired(expires_at: str) -> bool:
    """Return True if the token expires within the next 60 seconds."""
    try:
        expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        return now >= expiry - timedelta(seconds=60)
    except (ValueError, AttributeError):
        # If we can't parse the timestamp, treat it as expired to be safe.
        return True


# ---------------------------------------------------------------------------
# Async token refresh (httpx — used by FastMCP server)
# ---------------------------------------------------------------------------

async def get_valid_token() -> str:
    """
    Return a valid access token, refreshing if necessary (async version).
    Uses httpx so it fits cleanly in an asyncio event loop alongside FastMCP.
    """
    creds = load_credentials()
    if is_expired(creds.get("expires_at", "")):
        refresh_token = creds.get("refresh_token")
        if not refresh_token:
            raise RuntimeError("No refresh token available. Run: sangam login")
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{get_coordinator_url()}/auth/refresh",
                json={"refresh_token": refresh_token},
                timeout=15.0,
            )
            resp.raise_for_status()
            data = resp.json()
        creds["access_token"] = data["access_token"]
        creds["refresh_token"] = data.get("refresh_token", refresh_token)
        creds["expires_at"] = data["expires_at"]
        save_credentials(creds)
    return creds["access_token"]
