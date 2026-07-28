import os
from pathlib import Path


def get_config_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    d = base / "phasectl"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_config_file() -> Path:
    return get_config_dir() / "config.toml"


def get_db_path() -> Path:
    return get_config_dir() / "sessions.db"
