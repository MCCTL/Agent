from __future__ import annotations

import platform
import subprocess
from dataclasses import dataclass
from pathlib import Path

TASK_NAME = "MCCTL Agent"


class AutostartError(RuntimeError):
    pass


@dataclass(frozen=True)
class AutostartResult:
    ok: bool
    message: str


def default_windows_executable() -> Path:
    return Path.home() / ".local" / "bin" / "mcctl-agent.exe"


def ensure_windows() -> None:
    if platform.system().lower() != "windows":
        raise AutostartError("Windows autostart uses Task Scheduler. On Linux, use the systemd unit from the install guide.")


def build_schtasks_install_command(executable: Path | None = None) -> list[str]:
    path = executable or default_windows_executable()
    return [
        "schtasks",
        "/Create",
        "/TN",
        TASK_NAME,
        "/SC",
        "ONLOGON",
        "/TR",
        f'"{path}"',
        "/RL",
        "LIMITED",
        "/F",
    ]


def build_schtasks_uninstall_command() -> list[str]:
    return ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"]


def build_schtasks_status_command() -> list[str]:
    return ["schtasks", "/Query", "/TN", TASK_NAME]


def install_windows_autostart(executable: Path | None = None) -> AutostartResult:
    ensure_windows()
    command = build_schtasks_install_command(executable)
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise AutostartError((completed.stderr or completed.stdout or "Failed to create Task Scheduler entry.").strip())
    return AutostartResult(ok=True, message=f"Installed Windows autostart task: {TASK_NAME}")


def uninstall_windows_autostart() -> AutostartResult:
    ensure_windows()
    completed = subprocess.run(build_schtasks_uninstall_command(), capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise AutostartError((completed.stderr or completed.stdout or "Failed to remove Task Scheduler entry.").strip())
    return AutostartResult(ok=True, message=f"Removed Windows autostart task: {TASK_NAME}")


def windows_autostart_status() -> AutostartResult:
    ensure_windows()
    completed = subprocess.run(build_schtasks_status_command(), capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        return AutostartResult(ok=False, message=f"Windows autostart task is not installed: {TASK_NAME}")
    return AutostartResult(ok=True, message=f"Windows autostart task is installed: {TASK_NAME}")
