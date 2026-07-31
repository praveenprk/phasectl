from dataclasses import dataclass, field
from importlib import resources
import tomllib

from ..config import get_config_dir


@dataclass
class Phase:
    name: str
    system_prompt: str
    temperature: float
    token_budget: int
    tools_allowed: list[str] = field(default_factory=list)


def _parse(text: str) -> Phase:
    data = tomllib.loads(text)
    pd = data["phase"]
    return Phase(
        name=pd["name"],
        system_prompt=pd["system_prompt"],
        temperature=pd["temperature"],
        token_budget=pd["token_budget"],
        tools_allowed=pd.get("tools_allowed", []),
    )


def load_phase(name: str) -> Phase:
    user = get_config_dir() / "phases" / f"{name}.toml"
    if user.exists():
        return _parse(user.read_text())

    bundled = resources.files("phasectl.phases").joinpath(f"{name}.toml")
    if bundled.is_file():
        return _parse(bundled.read_text())

    available = list_phases()
    raise FileNotFoundError(f"Phase '{name}' not found. Available: {available}")


def list_phases() -> list[str]:
    bundled = {
        p.name[:-5]
        for p in resources.files("phasectl.phases").iterdir()
        if p.name.endswith(".toml")
    }
    user_dir = get_config_dir() / "phases"
    user = {p.stem for p in user_dir.glob("*.toml")} if user_dir.exists() else set()
    return sorted(bundled | user)
