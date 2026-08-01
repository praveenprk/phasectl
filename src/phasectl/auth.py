import os
import shutil
import subprocess
import sys
from pathlib import Path

from .config import get_config_dir


class AuthError(Exception):
    """Base auth error. Carries a clean one-line message and an exit code."""

    exit_code = 1


class NoAPIKey(AuthError):
    exit_code = 1

    def __init__(self) -> None:
        super().__init__("phasectl: no API key configured. Run: phasectl auth set")


class InvalidAPIKey(AuthError):
    exit_code = 2

    def __init__(self) -> None:
        super().__init__("phasectl: API key is invalid. Run: phasectl auth set")


_KEYCHAIN_SERVICE = "phasectl"
_KEYCHAIN_ACCOUNT = "api_key"
_SECRET_ATTRS = ("service", "phasectl", "key", "api_key")
_SECRET_LABEL = "phasectl API key"

_ENV_VARS = ("PHASECTL_API_KEY", "ANTHROPIC_API_KEY")


def get_credentials_file() -> Path:
    return get_config_dir() / "credentials"


def _read_credentials_file() -> str:
    path = get_credentials_file()
    if not path.exists():
        return ""
    return path.read_text().strip()


def _write_credentials_file(key: str) -> Path:
    path = get_credentials_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(key.strip() + "\n")
    path.chmod(0o600)
    return path


def _delete_credentials_file() -> bool:
    path = get_credentials_file()
    if path.exists():
        path.unlink()
        return True
    return False


def _has_security_cli() -> bool:
    return sys.platform == "darwin" and shutil.which("security") is not None


def _has_secret_tool() -> bool:
    return sys.platform.startswith("linux") and shutil.which("secret-tool") is not None


def _keychain_get() -> str | None:
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", _KEYCHAIN_SERVICE,
             "-a", _KEYCHAIN_ACCOUNT, "-w"],
            capture_output=True, text=True, check=False,
        )
    except (FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    val = result.stdout.strip()
    return val or None


def _keychain_set(key: str) -> bool:
    try:
        result = subprocess.run(
            ["security", "add-generic-password", "-U",
             "-s", _KEYCHAIN_SERVICE, "-a", _KEYCHAIN_ACCOUNT, "-w", key],
            capture_output=True, text=True, check=False,
        )
    except (FileNotFoundError, OSError):
        return False
    return result.returncode == 0


def _keychain_delete() -> bool:
    try:
        result = subprocess.run(
            ["security", "delete-generic-password",
             "-s", _KEYCHAIN_SERVICE, "-a", _KEYCHAIN_ACCOUNT],
            capture_output=True, text=True, check=False,
        )
    except (FileNotFoundError, OSError):
        return False
    return result.returncode == 0


def _secret_service_get() -> str | None:
    try:
        result = subprocess.run(
            ["secret-tool", "lookup", *_SECRET_ATTRS],
            capture_output=True, text=True, check=False,
        )
    except (FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    val = result.stdout.strip()
    return val or None


def _secret_service_set(key: str) -> bool:
    try:
        result = subprocess.run(
            ["secret-tool", "store", "--label", _SECRET_LABEL, *_SECRET_ATTRS],
            input=key, capture_output=True, text=True, check=False,
        )
    except (FileNotFoundError, OSError):
        return False
    return result.returncode == 0


def _secret_service_delete() -> bool:
    try:
        result = subprocess.run(
            ["secret-tool", "clear", *_SECRET_ATTRS],
            capture_output=True, text=True, check=False,
        )
    except (FileNotFoundError, OSError):
        return False
    return result.returncode == 0


def _env_key() -> tuple[str, str] | None:
    for name in _ENV_VARS:
        val = os.environ.get(name, "").strip()
        if val:
            return name, val
    return None


def get_api_key() -> str | None:
    env = _env_key()
    if env:
        return env[1]
    if _has_security_cli():
        val = _keychain_get()
        if val:
            return val
    elif _has_secret_tool():
        val = _secret_service_get()
        if val:
            return val
    file_key = _read_credentials_file()
    if file_key:
        return file_key
    return None


def key_source() -> str:
    """Return a human-readable label for where the key was found, or 'not set'."""
    env = _env_key()
    if env:
        return f"environment (${env[0]})"
    if _has_security_cli() and _keychain_get():
        return "macOS Keychain"
    if _has_secret_tool() and _secret_service_get():
        return "freedesktop secret-service"
    if _read_credentials_file():
        return "credentials file"
    return "not set"


def save_api_key(key: str) -> str:
    """Store the key in the best available backend. Returns a label describing where."""
    key = key.strip()
    if _has_security_cli():
        if _keychain_set(key):
            return "macOS Keychain"
    elif _has_secret_tool():
        if _secret_service_set(key):
            return "freedesktop secret-service"
    path = _write_credentials_file(key)
    return str(path)


def remove_api_key() -> str:
    """Remove the key from wherever it's stored. Returns a label, or 'not set'."""
    removed = []
    if _has_security_cli() and _keychain_get() is not None:
        if _keychain_delete():
            removed.append("macOS Keychain")
    if _has_secret_tool() and _secret_service_get() is not None:
        if _secret_service_delete():
            removed.append("freedesktop secret-service")
    if _read_credentials_file():
        if _delete_credentials_file():
            removed.append("credentials file")
    if not removed:
        return "not set"
    return ", ".join(removed)
