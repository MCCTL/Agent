from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path

from mcctl_agent.java import detect_java_installations


@dataclass(frozen=True)
class JarCandidate:
    path: str
    name: str
    size_bytes: int | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


JAR_PATTERNS = [
    "server.jar",
    "paper*.jar",
    "purpur*.jar",
    "spigot*.jar",
    "fabric*.jar",
    "forge*.jar",
    "velocity*.jar",
    "waterfall*.jar",
    "*.jar",
]


def inspect_server_directory(root_path: str) -> dict[str, object]:
    root = Path(root_path).expanduser()
    exists = root.exists()
    is_directory = root.is_dir()
    readable = exists and os.access(root, os.R_OK)
    writable = exists and os.access(root, os.W_OK)
    eula_path = root / "eula.txt"
    server_properties = root / "server.properties"
    latest_log = root / "logs" / "latest.log"
    plugins_dir = root / "plugins"

    eula_exists = eula_path.exists()
    eula_accepted = _read_eula_accepted(eula_path) if eula_exists else None
    warnings: list[str] = []
    if exists and not is_directory:
        warnings.append("Path exists but is not a directory.")
    if not exists:
        warnings.append("Directory does not exist.")
    if eula_exists and eula_accepted is not True:
        warnings.append("eula.txt exists but eula=true was not found.")
    if not server_properties.exists():
        warnings.append("server.properties was not found.")
    if not find_jar_candidates(root):
        warnings.append("No server jar candidates were found.")

    return {
        "root_path": str(root),
        "exists": exists,
        "is_directory": is_directory,
        "readable": readable,
        "writable": writable,
        "eula_exists": eula_exists,
        "eula_accepted": eula_accepted,
        "server_properties_exists": server_properties.exists(),
        "latest_log_exists": latest_log.exists(),
        "plugins_dir_exists": plugins_dir.is_dir(),
        "jar_candidates": [candidate.to_dict() for candidate in find_jar_candidates(root)],
        "java_candidates": [candidate.to_dict() for candidate in detect_java_installations()],
        "warnings": warnings,
    }


def find_jar_candidates(root: Path) -> list[JarCandidate]:
    if not root.exists() or not root.is_dir():
        return []
    candidates: list[JarCandidate] = []
    seen: set[Path] = set()
    for pattern in JAR_PATTERNS:
        for path in root.glob(pattern):
            if not path.is_file():
                continue
            resolved = _resolve(path)
            if resolved in seen:
                continue
            seen.add(resolved)
            candidates.append(
                JarCandidate(
                    path=str(resolved),
                    name=path.name,
                    size_bytes=path.stat().st_size if path.exists() else None,
                )
            )
    return candidates


def _read_eula_accepted(path: Path) -> bool:
    try:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            normalized = line.strip().lower().replace(" ", "")
            if normalized.startswith("eula="):
                return normalized == "eula=true"
    except OSError:
        return False
    return False


def _resolve(path: Path) -> Path:
    try:
        return path.resolve(strict=True)
    except OSError:
        return path
