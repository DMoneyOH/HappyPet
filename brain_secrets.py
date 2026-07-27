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

Unlock-key resolution (COGNITIVE_DB_KEY), in order:
    1. the process environment
    2. MaeveJarvis's secrets.env
    3. Windows Credential Manager (service 'maeve', name 'COGNITIVE_DB_KEY')
(3) exists so the key can be removed from the plaintext secrets.env without
this module going quiet. If none of the three has it AND there is positive
evidence a vault is meant to be reachable from this process, that is a
BrainSecretsUnavailable -- see _vault_is_configured_here for why "positive
evidence" is required rather than just raising.
"""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path

# Shared config: MaeveJarvis's secrets.env holds the vault's unlock key.
# This is a DATA reference (one line read out of a config file), not a code
# import -- same as pointing at a shared database's connection string.
#
# The MaeveJarvis checkout moved under `_archive/` in the 2026-07-21 vault
# reorganization. The pre-reorg path (`../MaeveJarvis/secrets.env`) no longer
# exists, and because _get_vault() is deliberately non-raising, missing it did
# not surface as an error -- get_secret() just returned None for every key and
# every caller silently fell back to its env var. That is indistinguishable
# from correct behaviour on CI, so it went unnoticed locally.
_SECRETS_ENV = Path(__file__).parent.parent / "_archive" / "MaeveJarvis" / "secrets.env"

# Absolute fallback for the vault DB. COGNITIVE_DB_PATH (env or secrets.env)
# still wins; this only replaces the old bare-filename default, which resolved
# against the CURRENT WORKING DIRECTORY. From this repo that pointed at a
# non-existent HappyPet/maeve-brain-v2.db, which sqlcipher3.connect() would
# happily create as an empty file -- the `SELECT 1 FROM secrets` probe below
# then failed and the vault silently degraded to None, same as above.
_DEFAULT_DB_PATH = Path(__file__).parent.parent / "brain" / "maeve-brain-v2.db"

_VAULT_KEY_NAME = "__vault_key__"
_DB_KEY_NAME = "COGNITIVE_DB_KEY"
_KEYRING_SERVICE = "maeve"

SHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

_vault = None
_vault_tried = False
# The lazy open happens once. Without remembering WHY it failed, the first
# caller would get the loud error and every caller after it a quiet None --
# which is the same silent degradation in a slower form.
_vault_error = None


class BrainSecretsUnavailable(RuntimeError):
    """A vault is configured for this process but cannot be unlocked.

    Deliberately NOT raised when there is simply no vault here (CI): callers
    -- chewy_lookup.py, post_pins.py, push_pins_to_sheets.py -- rely on
    get_secret() returning None there so they can fall back to env vars.
    This is the "the key moved and nobody noticed" case, which used to
    present as pins with no audit trail and Chewy links quietly missing.
    """


def _remember(exc: BrainSecretsUnavailable) -> BrainSecretsUnavailable:
    """Latch a configuration failure so every later caller sees it too, not
    just the one that happened to open the vault first."""
    global _vault_error
    _vault_error = exc
    return exc


def _warn(message: str) -> None:
    """Best-effort visibility. Wrapped so instrumentation can never be the
    thing that breaks a pipeline run."""
    try:
        sys.stderr.write(f"[brain_secrets] {message}\n")
    except Exception:  # noqa: BLE001 -- a broken stderr must not propagate
        pass


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


# Invisible characters that ride along with a pasted credential. Same set and
# same edge-only policy as secrets-migration/keyring_helper.py, which is what
# writes these entries: U+FEFF because PowerShell 5.1 prepends a BOM to a native
# process's stdin and str.strip() does not remove it; U+00A0 and the zero-width
# family because they come along when a value is copied out of a web UI.
# Edge-only on purpose -- silently rewriting the middle of a key would turn a
# visible failure into an invisible one.
_INVISIBLES = "﻿\xa0​‌‍⁠"


def _normalise(raw: str) -> str:
    return raw.strip().strip(_INVISIBLES).strip()


def _credman_get(name: str, service: str | None = None) -> tuple[str | None, str]:
    """Read one entry from Windows Credential Manager.

    `service` resolves at CALL time (not as a default argument, which would bind
    at import) so an integration test can point the whole resolver at a
    throwaway service name instead of the production one.

    Returns (value, diagnostic). Never raises: keyring is a win32-only optional
    dependency (see requirements.txt), so "not installed" and "no such entry"
    are both ordinary answers on CI. The diagnostic string is only used to make
    a later BrainSecretsUnavailable message specific -- it never carries a value.
    """
    service = service or _KEYRING_SERVICE
    try:
        import keyring
    except ImportError:
        return None, "the 'keyring' package is not installed in this interpreter"
    try:
        value = keyring.get_password(service, name)
    except Exception as exc:  # noqa: BLE001 -- pywin32/backend-specific
        return None, f"the Credential Manager backend errored: {type(exc).__name__}"
    if value is None:
        return None, f"no Credential Manager entry {service}/{name}"
    value = _normalise(value)
    if not value:
        return None, f"Credential Manager entry {service}/{name} is empty"
    return value, ""


def _resolve_db_key(file_values: dict[str, str]) -> tuple[str | None, str, str]:
    """Resolve the vault's unlock key. Returns (key, source, diagnostic).

    Order is env -> secrets.env -> Credential Manager, so adding the Credential
    Manager tier cannot change what an already-working host resolves.
    """
    from_env = os.environ.get(_DB_KEY_NAME)
    if from_env:
        return from_env, "environment", ""
    from_file = file_values.get(_DB_KEY_NAME)
    if from_file:
        return from_file, str(_SECRETS_ENV), ""
    from_credman, note = _credman_get(_DB_KEY_NAME)
    if from_credman:
        return from_credman, f"Credential Manager ({_KEYRING_SERVICE}/{_DB_KEY_NAME})", ""
    return None, "", note


def _is_nonempty_file(path) -> bool:
    """Does this path actually hold something? Decided BEFORE any connect().

    sqlcipher3.connect() creates a missing file, so "the path exists" is not
    evidence of a vault unless the file also has bytes in it -- a 0-byte file is
    the fingerprint of an earlier connect() to a wrong path, not a database.
    """
    try:
        return os.path.isfile(path) and os.path.getsize(path) > 0
    except OSError:
        return False


def _vault_is_configured_here(*, have_secrets_env: bool, have_db: bool, have_key: bool) -> bool:
    """Positive evidence that a vault is meant to be reachable from THIS process.

    Without this test, "no unlock key" would raise on GitHub Actions -- which has
    no vault at all by design, injects everything as env vars, and whose callers
    catch only ImportError. So the loud failure is armed by evidence, not by
    platform: a readable secrets.env, an on-disk vault DB, or an unlock key that
    resolved from somewhere. Any one of those means somebody set this up, so a
    dead end is a real fault rather than an absent feature.

    `have_db` is "some candidate path holds a non-empty file", not one specific
    path: since path selection became a fall-through list, no single path is the
    vault's location any more.
    """
    return have_secrets_env or have_key or have_db


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
    """Lazily open the vault.

    Returns None when there is no vault here at all (CI, a stripped checkout, a
    machine without the optional win32 deps) -- callers must keep treating that
    the same as a missing individual secret.

    Raises BrainSecretsUnavailable when a vault IS configured for this process
    but its unlock key cannot be found in the environment, in secrets.env, or in
    Windows Credential Manager. That combination is a fault, not an absence, and
    the old silent None turned it into pins with an empty audit trail.
    """
    global _vault, _vault_tried, _vault_error
    if _vault_tried:
        if _vault_error is not None:
            raise _vault_error
        return _vault
    _vault_tried = True
    _vault_error = None

    have_secrets_env = _SECRETS_ENV.is_file()
    file_values = _parse_env_file(_SECRETS_ENV)
    db_key, key_source, diagnostic = _resolve_db_key(file_values)

    # Candidates in precedence order: an explicit override still wins, but a
    # configured path that does not actually hold the vault no longer dead-ends
    # the whole reader -- it falls through to the canonical location.
    #
    # This matters because COGNITIVE_DB_PATH in the shared secrets.env is stale
    # post-reorg: it names the vault root, where the live DB no longer lives.
    candidates = [c for c in (
        os.environ.get("COGNITIVE_DB_PATH"),
        file_values.get("COGNITIVE_DB_PATH"),
        str(_DEFAULT_DB_PATH),
    ) if c]

    # Filtered BEFORE any connect(), because sqlcipher3.connect() CREATES a
    # missing file: a wrong path otherwise becomes a 0-byte "database" that
    # exists forever after, makes every later existence check pass, and fails
    # as an opaque OperationalError -- which reads as "bad key" and sends
    # whoever debugs it after the credential instead of the path.
    openable = [c for c in candidates if _is_nonempty_file(c)]

    configured = _vault_is_configured_here(
        have_secrets_env=have_secrets_env, have_db=bool(openable), have_key=bool(db_key)
    )

    if not db_key:
        if configured:
            raise _remember(BrainSecretsUnavailable(
                f"the vault unlock key {_DB_KEY_NAME} was not found in the environment, "
                f"in {_SECRETS_ENV} (present={have_secrets_env}), or in Windows Credential "
                f"Manager ({_KEYRING_SERVICE}/{_DB_KEY_NAME})"
                + (f" -- {diagnostic}" if diagnostic else "")
                + ". Store it with: secrets-migration/keyring_helper.py set "
                f"{_DB_KEY_NAME}"
            ))
        return None

    if not openable:
        # The key resolved but no candidate holds a vault -- not the override,
        # not secrets.env's path, not the canonical location. A single stale
        # COGNITIVE_DB_PATH no longer reaches this (it falls through above), so
        # getting here means every candidate is missing or 0 bytes. That is a
        # real fault on a host that has a key, and must not be a quiet None.
        raise _remember(BrainSecretsUnavailable(
            f"the vault unlock key resolved from {key_source} but no vault database "
            f"exists at any candidate path (COGNITIVE_DB_PATH, then the canonical "
            f"{str(_DEFAULT_DB_PATH)!r}); tried {candidates!r}"
        ))

    try:
        import sqlcipher3
        import keyring
    except ImportError:
        # Genuinely optional, win32-only deps (see requirements.txt). Degrading
        # is correct here, but say so -- this used to be completely silent.
        _warn(
            "sqlcipher3/keyring are not installed in this interpreter; the vault "
            "is unavailable and callers will fall back to environment variables"
        )
        return None

    for candidate in openable:
        try:
            conn = sqlcipher3.connect(candidate)
            conn.execute(f"PRAGMA key = '{db_key.replace(chr(39), chr(39) * 2)}'")
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("SELECT 1 FROM secrets LIMIT 1")  # fail fast on a bad key/path
        except Exception as exc:  # noqa: BLE001
            # Degrades rather than raising: a locked/busy brain DB (Maeve reads
            # it concurrently) is transient and must not take down a pipeline
            # run, and an earlier candidate failing is exactly what the
            # fall-through exists for. A wrong key lands here too, so the
            # warning names the source -- never the value -- to keep the two
            # distinguishable in a log instead of failing silently.
            _warn(
                f"could not open the vault at {candidate!r} with the key from "
                f"{key_source}: {type(exc).__name__}: {exc}"
            )
            continue

        # service passed explicitly: the constructor default binds at import,
        # which would leave a test unable to redirect the master-key lookup.
        _vault = _VaultReader(conn, keyring, service=_KEYRING_SERVICE)
        return _vault

    return None


def get_secret(key: str, project: str = "HappyPet") -> str | None:
    """Fetch a secret from the vault. Vault names are flat `PROJECT__KEY`
    (no separate project column) -- tries the given project, then GLOBAL.

    Returns None where there is no vault (CI) or the name is absent; raises
    BrainSecretsUnavailable if a vault IS configured here but cannot be
    unlocked. Callers that env-fall-back on None must NOT swallow that
    exception -- it means their credentials are misconfigured, not missing.
    """
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
