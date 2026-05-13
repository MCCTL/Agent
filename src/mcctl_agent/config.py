from __future__ import annotations

import json
import os
import platform
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path


DEFAULT_API_BASE_URL = "https://api.mcctl.com"


def resolve_api_base_url() -> str:
    return os.environ.get("MCCTL_API_BASE_URL", DEFAULT_API_BASE_URL)


def default_config_path() -> Path:
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

    @classmethod
    def load(cls, path: Path) -> "AgentConfig":
        if not path.exists():
            return cls(agent_fingerprint=str(uuid.uuid4()))
        data = json.loads(path.read_text(encoding="utf-8"))
        config = cls(**data)
        if not config.agent_fingerprint:
            config.agent_fingerprint = str(uuid.uuid4())
        return config

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
