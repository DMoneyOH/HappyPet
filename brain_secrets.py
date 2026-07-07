"""
brain_secrets.py — HappyPet credential loader.

Reads secrets from Maeve's encrypted SecretVault directly. This module owns
its own read-only decrypt logic (vendored from MaeveJarvis's
src/maeve/secrets/vault.py) -- HappyPet does not import MaeveJarvis's Python
package, so this repo's code has no dependency on that repo's internals
changing. The only thing shared is data: the vault DB file itself
(maeve-brain-v2.db, SQLCipher-encrypted, one level up from both repos) and
the unlock key in MaeveJarvis's secrets.env -- the same relationship any two
apps have to one shared database, not a source-code coupling. If the vault's
on-disk format ever changes, re-sync _VaultReader against the real one.

Usage:
    from brain_secrets import get_sheets_creds, get_secret
    creds = get_sheets_creds()   # google.oauth2.service_account.Credentials
    val   = get_secret('IMPACT_ACCOUNT_SID')
"""
from __future__ import annotations
import json
import os
from pathlib import Path

# Shared config: MaeveJarvis's secrets.env holds the vault's unlock key.
# This is a DATA reference (one line read out of a config file), not a code
# import -- same as pointing at a shared database's connection string.
_SECRETS_ENV = Path(__file__).parent.parent / "MaeveJarvis" / "secrets.env"

_VAULT_KEY_NAME = "__vault_key__"
_KEYRING_SERVICE = "maeve"

SHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

_vault = None
_vault_tried = False


def _parse_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


class _VaultReader:
    """Minimal read-only client for Maeve's SecretVault on-disk format.
    Deliberately has no put() -- if the OS keyring master key is missing,
    that's a real error, not something this reader should paper over by
    silently generating (and thereby desyncing from MaeveJarvis's own key)."""

    def __init__(self, conn, keyring_backend, *, service: str = _KEYRING_SERVICE):
        self._conn = conn
        self._kr = keyring_backend
        self._service = service

    def _master_key(self) -> bytes:
        existing = self._kr.get_password(self._service, _VAULT_KEY_NAME)
        if not existing:
            raise RuntimeError(f"No vault master key in OS keyring (service={self._service!r})")
        return bytes.fromhex(existing)

    def use(self, name: str) -> str:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        row = self._conn.execute(
            "SELECT ciphertext, nonce, tag FROM secrets WHERE name = ?", (name,)
        ).fetchone()
        if row is None:
            raise KeyError(name)
        ciphertext, nonce, tag = bytes(row[0]), bytes(row[1]), bytes(row[2])
        plain = AESGCM(self._master_key()).decrypt(nonce, ciphertext + tag, name.encode("utf-8"))
        return plain.decode("utf-8")


def _get_vault():
    """Lazily open the vault. Returns None -- never raises -- if the shared
    secrets.env isn't reachable, vault deps aren't installed, the DB key is
    unset, or the connection/key turns out to be bad. Callers must treat a
    missing vault the same as a missing individual secret."""
    global _vault, _vault_tried
    if _vault_tried:
        return _vault
    _vault_tried = True

    file_values = _parse_env_file(_SECRETS_ENV)
    db_key = os.environ.get("COGNITIVE_DB_KEY") or file_values.get("COGNITIVE_DB_KEY")
    db_path = (
        os.environ.get("COGNITIVE_DB_PATH")
        or file_values.get("COGNITIVE_DB_PATH")
        or "maeve-brain-v2.db"
    )
    if not db_key:
        return None

    try:
        import sqlcipher3
        import keyring
    except ImportError:
        return None

    try:
        conn = sqlcipher3.connect(db_path)
        conn.execute(f"PRAGMA key = '{db_key.replace(chr(39), chr(39) * 2)}'")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("SELECT 1 FROM secrets LIMIT 1")  # fail fast on a bad key/path
    except Exception:
        return None

    _vault = _VaultReader(conn, keyring)
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
