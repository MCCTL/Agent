from __future__ import annotations

import json
import os
import platform
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path


DEFAULT_API_BASE_URL = "https://api.mcctl.com"


def resolve_api_base_url() -> str:
    return os.environ.get("MCCTL_API_BASE_URL", DEFAULT_API_BASE_URL)


def default_config_path() -> Path:
    configured = os.environ.get("MCCTL_AGENT_CONFIG")
    if configured:
        return Path(configured).expanduser()
    if platform.system().lower() == "windows":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "mcctl" / "agent.json"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "mcctl" / "agent.json"


@dataclass
class AgentConfig:
    api_base_url: str = DEFAULT_API_BASE_URL
    agent_fingerprint: str = ""
    device_id: str | None = None
    agent_token: str | None = None
    allowed_roots: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "AgentConfig":
        if not path.exists():
            return cls(agent_fingerprint=str(uuid.uuid4()), allowed_roots=default_allowed_roots())
        data = json.loads(path.read_text(encoding="utf-8"))
        filtered = {key: value for key, value in data.items() if key in cls.__dataclass_fields__}
        config = cls(**filtered)
        if not config.agent_fingerprint:
            config.agent_fingerprint = str(uuid.uuid4())
        if not config.allowed_roots:
            config.allowed_roots = default_allowed_roots()
        return config

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass


def default_allowed_roots() -> list[str]:
    home = Path.home()
    system = platform.system().lower()
    if system == "windows":
        roots = [home, home / "minecraft", Path.cwd()]
        for drive in ("C:", "D:", "E:"):
            roots.append(Path(f"{drive}\\Minecraft"))
    else:
        roots = [home, home / "minecraft", Path("/srv/minecraft"), Path("/opt/minecraft"), Path.cwd()]
    result: list[str] = []
    seen: set[str] = set()
    for root in roots:
        try:
            text = str(root.expanduser())
        except OSError:
            continue
        key = text.lower() if system == "windows" else text
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result
