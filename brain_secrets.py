"""
brain_secrets.py — HappyPet credential loader.
Loads secrets from Brain vault_secrets. No file I/O, no .env required.
Usage:
    from brain_secrets import get_sheets_creds, get_secret
    creds = get_sheets_creds()   # google.oauth2.service_account.Credentials
    val   = get_secret('FACEBOOK_PAGE_TOKEN')
"""
from __future__ import annotations
import json
import sys
import tempfile
import os
from pathlib import Path

# Brain is in core-skills — resolve relative to this file's vault location
_BRAIN_DIR = Path(__file__).parent.parent / "utils" / "core-skills"
sys.path.insert(0, str(_BRAIN_DIR))
from brain import get_secret as _get_secret  # noqa: E402

SHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def get_secret(key: str, project: str = "HappyPet") -> str | None:
    """Fetch a secret from Brain vault_secrets for the HappyPet project."""
    return _get_secret(key, project) or _get_secret(key, "global")


def get_sheets_creds(scopes: list[str] | None = None):
    """
    Return a google.oauth2.service_account.Credentials object loaded
    from Brain vault_secrets (HAPPYPET_SHEETS_KEY, project=HappyPet).
    Raises RuntimeError if key is missing or google-auth is not installed.
    """
    try:
        from google.oauth2.service_account import Credentials
    except ImportError as e:
        raise RuntimeError(
            "google-auth not installed. Run: pip install google-auth --break-system-packages"
        ) from e

    key_json = _get_secret("HAPPYPET_SHEETS_KEY", "HappyPet")
    if not key_json:
        raise RuntimeError("HAPPYPET_SHEETS_KEY not found in Brain (project=HappyPet)")

    info = json.loads(key_json)
    return Credentials.from_service_account_info(info, scopes=scopes or SHEETS_SCOPES)
