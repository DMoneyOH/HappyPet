"""
brain_secrets.py — HappyPet credential loader.
Loads secrets from Maeve's encrypted SecretVault (MaeveJarvis/src/maeve/secrets),
which lives in the sibling MaeveJarvis repo, one level up from this one. No
plaintext file ever holds these values; the vault is SQLCipher + AES-256-GCM,
same as MaeveJarvis's own `maeve secrets` CLI uses.
Usage:
    from brain_secrets import get_sheets_creds, get_secret
    creds = get_sheets_creds()   # google.oauth2.service_account.Credentials
    val   = get_secret('IMPACT_ACCOUNT_SID')
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

_MAEVEJARVIS_DIR = Path(__file__).parent.parent / "MaeveJarvis"
_SECRETS_ENV = _MAEVEJARVIS_DIR / "secrets.env"
sys.path.insert(0, str(_MAEVEJARVIS_DIR / "src"))

SHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

_vault = None
_vault_tried = False


def _get_vault():
    """Lazily open the vault. Returns None (never raises) if MaeveJarvis isn't
    checked out next to this repo, its vault deps aren't installed, or the
    cognitive DB key is unset -- callers must treat a missing vault the same
    as a missing individual secret and degrade gracefully."""
    global _vault, _vault_tried
    if _vault_tried:
        return _vault
    _vault_tried = True
    if not _SECRETS_ENV.is_file():
        return None
    try:
        import sqlcipher3
        import keyring
        from maeve.config import load_settings
        from maeve.secrets.vault import SecretVault
    except ImportError:
        return None
    settings = load_settings(_SECRETS_ENV)
    if not settings.cognitive_db_key:
        return None
    conn = sqlcipher3.connect(settings.cognitive_db_path)
    conn.execute(f"PRAGMA key = '{settings.cognitive_db_key.replace(chr(39), chr(39) * 2)}'")
    conn.execute("PRAGMA busy_timeout = 5000")
    _vault = SecretVault(conn, keyring)
    return _vault


def get_secret(key: str, project: str = "HappyPet") -> str | None:
    """Fetch a secret from the vault. Vault names are flat `PROJECT__KEY`
    (no separate project column) -- tries the given project, then GLOBAL."""
    vault = _get_vault()
    if vault is None:
        return None
    for name in (f"{project.upper()}__{key}", f"GLOBAL__{key}"):
        try:
            return vault.use(name)
        except KeyError:
            continue
    return None


def get_sheets_creds(scopes: list[str] | None = None):
    """
    Return a google.oauth2.service_account.Credentials object loaded
    from the vault (HAPPYPET__HAPPYPET_SHEETS_KEY).
    Raises RuntimeError if the key is missing or google-auth is not installed.
    """
    try:
        from google.oauth2.service_account import Credentials
    except ImportError as e:
        raise RuntimeError(
            "google-auth not installed. Run: pip install google-auth --break-system-packages"
        ) from e

    key_json = get_secret("HAPPYPET_SHEETS_KEY", "HappyPet")
    if not key_json:
        raise RuntimeError("HAPPYPET_SHEETS_KEY not found in the vault (project=HappyPet)")

    info = json.loads(key_json)
    return Credentials.from_service_account_info(info, scopes=scopes or SHEETS_SCOPES)
