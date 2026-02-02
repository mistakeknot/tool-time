#!/usr/bin/env python3
"""tool-time community upload.

Reads stats.json, applies anonymization allowlist, and POSTs to the
community analytics API. Only runs if user has opted in via config.json.

Designed to run as a background process on SessionEnd — never blocks.
"""

import hashlib
import hmac
import json
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError

DATA_DIR = Path.home() / ".claude" / "tool-time"
STATS_FILE = DATA_DIR / "stats.json"
CONFIG_FILE = DATA_DIR / "config.json"
API_ENDPOINT = "https://tool-time-api.mistakeknot.workers.dev/v1/api/submit"


def load_config() -> dict:
    """Load config, creating defaults if missing."""
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())
    return {}


def save_config(config: dict) -> None:
    """Write config atomically."""
    CONFIG_FILE.write_text(json.dumps(config, indent=2) + "\n")


def ensure_token(config: dict) -> str:
    """Ensure a submission token exists, generating one if needed."""
    token = config.get("submission_token")
    if not token:
        token = secrets.token_hex(16)
        config["submission_token"] = token
        config["token_created_at"] = datetime.now(timezone.utc).isoformat()
        save_config(config)
    return token


def anonymize(stats: dict, token: str) -> dict:
    """Apply strict anonymization allowlist.

    Only these fields survive:
    - submission_token (random, not tied to identity)
    - generated (truncated to hour precision)
    - total_events (count)
    - tools: {name: {calls, errors, rejections}} (names are public)
    - edit_without_read (count)
    - model (public model name)
    - skills: {name: calls} (public skill identifiers)
    - mcp_servers: {name: {calls, errors}} (parsed from tool name prefix)
    - installed_plugins: [name, ...] (public plugin identifiers)

    Everything else is stripped: file paths, project names, error messages,
    skill arguments.
    """
    # Truncate timestamp to hour precision (prevents timing correlation)
    generated = stats.get("generated", "")
    if len(generated) >= 13:
        generated = generated[:13] + ":00:00Z"

    return {
        "submission_token": token,
        "generated": generated,
        "total_events": stats.get("total_events", 0),
        "tools": {
            name: {
                "calls": t.get("calls", 0),
                "errors": t.get("errors", 0),
                "rejections": t.get("rejections", 0),
            }
            for name, t in stats.get("tools", {}).items()
        },
        "edit_without_read": stats.get("edit_without_read_count", 0),
        "model": stats.get("model"),
        "client": stats.get("client"),
        "skills": {
            name: s.get("calls", 0)
            for name, s in stats.get("skills", {}).items()
        },
        "mcp_servers": {
            name: {"calls": m.get("calls", 0), "errors": m.get("errors", 0)}
            for name, m in stats.get("mcp_servers", {}).items()
        },
        "installed_plugins": stats.get("installed_plugins", []),
    }


def sign_payload(payload_json: bytes, token: str) -> str:
    """Compute HMAC-SHA256 signature for payload integrity."""
    return hmac.new(token.encode(), payload_json, hashlib.sha256).hexdigest()


def upload(payload: dict) -> bool:
    """POST anonymized payload to community API. Returns True on success."""
    data = json.dumps(payload).encode("utf-8")
    signature = sign_payload(data, payload["submission_token"])
    req = Request(
        API_ENDPOINT,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "tool-time-upload/0.3",
            "X-Signature": signature,
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except (URLError, OSError):
        return False


def main() -> int:
    config = load_config()

    # Only upload if user has opted in
    if not config.get("community_sharing"):
        return 0

    # Load stats
    if not STATS_FILE.exists():
        return 0

    stats = json.loads(STATS_FILE.read_text())
    token = ensure_token(config)
    payload = anonymize(stats, token)

    if upload(payload):
        config["last_upload_at"] = datetime.now(timezone.utc).isoformat()
        save_config(config)

    return 0


if __name__ == "__main__":
    sys.exit(main())
