from dataclasses import dataclass, field
from pathlib import Path
import tomllib


PHASES_DIR = Path(__file__).parent.parent.parent / "phases"


@dataclass
class Phase:
    name: str
    system_prompt: str
    temperature: float
    token_budget: int
    tools_allowed: list[str] = field(default_factory=list)


def load_phase(name: str) -> Phase:
    path = PHASES_DIR / f"{name}.toml"
    if not path.exists():
        raise FileNotFoundError(f"Phase '{name}' not found at {path}")
    with open(path, "rb") as f:
        data = tomllib.load(f)
    pd = data["phase"]
    return Phase(
        name=pd["name"],
        system_prompt=pd["system_prompt"],
        temperature=pd["temperature"],
        token_budget=pd["token_budget"],
        tools_allowed=pd.get("tools_allowed", []),
    )


def list_phases() -> list[str]:
    if not PHASES_DIR.exists():
        return []
    return sorted(p.stem for p in PHASES_DIR.glob("*.toml"))
