from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class JavaInstallation:
    executable_path: str
    version_string: str | None
    major_version: int | None
    vendor: str | None
    source: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def parse_java_version(output: str) -> tuple[str | None, int | None, str | None]:
    first_line = next((line.strip() for line in output.splitlines() if line.strip()), "")
    version_match = re.search(r'version\s+"([^"]+)"', output, re.IGNORECASE)
    version_string = version_match.group(1) if version_match else None
    major_version = major_from_version(version_string)
    vendor = None
    lower = output.lower()
    if "temurin" in lower or "adoptium" in lower:
        vendor = "Eclipse Adoptium"
    elif "openjdk" in lower:
        vendor = "OpenJDK"
    elif "oracle" in lower or "java(tm)" in lower:
        vendor = "Oracle"
    elif first_line:
        vendor = first_line.split()[0]
    return version_string, major_version, vendor


def major_from_version(version: str | None) -> int | None:
    if not version:
        return None
    if version.startswith("1."):
        parts = version.split(".")
        return int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
    match = re.match(r"(\d+)", version)
    return int(match.group(1)) if match else None


def detect_java_installations() -> list[JavaInstallation]:
    executable_name = "java.exe" if platform.system().lower() == "windows" else "java"
    candidates: list[tuple[Path, str]] = []

    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        candidates.append((Path(java_home) / "bin" / executable_name, "JAVA_HOME"))

    path_candidate = shutil.which(executable_name)
    if path_candidate:
        candidates.append((Path(path_candidate), "PATH"))

    candidates.extend((Path(path), "where java") for path in _command_paths(["where", "java"]))
    candidates.extend((Path(path), "which java") for path in _command_paths(["which", "java"]))

    if platform.system().lower() == "windows":
        candidates.extend((path, "common install path") for path in _windows_common_java_paths())

    installations: list[JavaInstallation] = []
    seen: set[str] = set()
    for path, source in candidates:
        if not path.exists():
            continue
        resolved = _resolve_path(path)
        dedupe_key = str(resolved).lower() if platform.system().lower() == "windows" else str(resolved)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        version_string, major_version, vendor = _read_java_version(resolved)
        installations.append(
            JavaInstallation(
                executable_path=str(resolved),
                version_string=version_string,
                major_version=major_version,
                vendor=vendor,
                source=source,
            )
        )
    installations.sort(key=lambda item: (item.major_version or 0, item.executable_path), reverse=True)
    return installations


def _read_java_version(path: Path) -> tuple[str | None, int | None, str | None]:
    try:
        result = subprocess.run(
            [str(path), "-version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, None, None
    return parse_java_version((result.stderr or "") + "\n" + (result.stdout or ""))


def _command_paths(command: list[str]) -> list[str]:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=3, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _windows_common_java_paths() -> list[Path]:
    roots = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
    ]
    vendors = ["Java", "Eclipse Adoptium", "Microsoft", "Amazon Corretto", "BellSoft"]
    paths: list[Path] = []
    for root in roots:
        for vendor in vendors:
            vendor_root = root / vendor
            if not vendor_root.exists():
                continue
            paths.extend(vendor_root.glob("**/bin/java.exe"))
    return paths


def _resolve_path(path: Path) -> Path:
    try:
        return path.resolve(strict=True)
    except OSError:
        return path
