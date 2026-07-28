import os
from pathlib import Path
import tomllib

try:
    import tomli_w
    TOMLI_W_AVAILABLE = True
except ImportError:
    TOMLI_W_AVAILABLE = False


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


def get_kuzu_path(config: dict | None = None) -> Path:
    if config is None:
        config = _load_config()
    raw = config.get("graph", {}).get("kuzu_path", "")
    if raw:
        return Path(os.path.expanduser(raw))
    return get_config_dir() / "graph.kuzu"


def get_index_path(project: str, config: dict | None = None) -> Path | None:
    if config is None:
        config = _load_config()
    raw = config.get("projects", {}).get(project, {}).get("index_path", "")
    if raw:
        return Path(os.path.expanduser(raw))
    return None


def _load_config() -> dict:
    config_file = get_config_file()
    if not config_file.exists():
        return {}
    with open(config_file, "rb") as f:
        return tomllib.load(f)


def set_index_path(project: str, path: str) -> None:
    if not TOMLI_W_AVAILABLE:
        return
    config_file = get_config_file()
    config = _load_config()
    if "projects" not in config:
        config["projects"] = {}
    if project not in config["projects"]:
        config["projects"][project] = {}
    config["projects"][project]["index_path"] = path
    config_file.write_text(tomli_w.dumps(config))