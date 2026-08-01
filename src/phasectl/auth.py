import os
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


def get_credentials_file() -> Path:
    return get_config_dir() / "credentials"


def _read_credentials_file() -> str:
    path = get_credentials_file()
    if not path.exists():
        return ""
    return path.read_text().strip()


def get_api_key() -> str | None:
    env_key = os.environ.get("ANTHROPIC_API_KEY")
    if env_key and env_key.strip():
        return env_key.strip()
    file_key = _read_credentials_file()
    if file_key:
        return file_key
    return None


def key_source() -> str:
    if os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return "environment"
    if _read_credentials_file():
        return "credentials file"
    return "not set"


def save_api_key(key: str) -> Path:
    path = get_credentials_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(key.strip() + "\n")
    path.chmod(0o600)
    return path
