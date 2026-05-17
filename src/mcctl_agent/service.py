from __future__ import annotations

import ctypes
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from html import escape
from pathlib import Path

from mcctl_agent.config import default_config_path

SERVICE_ID = "MCCTLAgent"
SERVICE_NAME = "MCCTL Agent"
SERVICE_DESCRIPTION = "Runs the MCCTL Agent in the background to manage Minecraft servers from MCCTL."
WINSW_DOWNLOAD_URL = os.environ.get(
    "MCCTL_WINSW_DOWNLOAD_URL",
    "https://github.com/winsw/winsw/releases/latest/download/WinSW-x64.exe",
)


class ServiceError(RuntimeError):
    pass


@dataclass(frozen=True)
class ServiceResult:
    ok: bool
    message: str


def is_windows() -> bool:
    return platform.system().lower() == "windows"


def is_admin() -> bool:
    if not is_windows():
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def program_data_dir() -> Path:
    return Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "MCCTL" / "Agent"


def service_config_path() -> Path:
    return program_data_dir() / "agent.json"


def service_winsw_dir() -> Path:
    return program_data_dir() / "winsw"


def service_winsw_exe() -> Path:
    return service_winsw_dir() / f"{SERVICE_ID}.exe"


def service_winsw_xml() -> Path:
    return service_winsw_dir() / f"{SERVICE_ID}.xml"


def service_log_dir() -> Path:
    return program_data_dir() / "logs"


def resolve_agent_executable() -> Path:
    return Path(sys.argv[0]).resolve()


def build_winsw_config(agent_executable: Path, config_path: Path, log_dir: Path) -> str:
    return "\n".join(
        [
            "<service>",
            f"  <id>{SERVICE_ID}</id>",
            f"  <name>{SERVICE_NAME}</name>",
            f"  <description>{SERVICE_DESCRIPTION}</description>",
            f"  <executable>{escape(str(agent_executable))}</executable>",
            f"  <workingdirectory>{escape(str(program_data_dir()))}</workingdirectory>",
            "  <startmode>Automatic</startmode>",
            "  <env name=\"MCCTL_AGENT_INSTALL_METHOD\" value=\"winsw\" />",
            f"  <env name=\"MCCTL_AGENT_CONFIG\" value=\"{escape(str(config_path))}\" />",
            f"  <logpath>{escape(str(log_dir))}</logpath>",
            "  <log mode=\"roll-by-size-time\">",
            "    <sizeThreshold>10485760</sizeThreshold>",
            "    <pattern>yyyyMMdd</pattern>",
            "  </log>",
            "</service>",
            "",
        ]
    )


def install_service() -> ServiceResult:
    _ensure_windows_admin()
    agent_executable = resolve_agent_executable()
    config_path = service_config_path()
    log_dir = service_log_dir()
    service_winsw_dir().mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    _copy_user_config_if_needed(config_path)
    _ensure_winsw_binary(service_winsw_exe())
    service_winsw_xml().write_text(build_winsw_config(agent_executable, config_path, log_dir), encoding="utf-8")
    _run_winsw("install")
    return ServiceResult(
        True,
        "Windows Serviceをインストールしました。次に `mcctl-agent service start` を実行してください。",
    )


def start_service() -> ServiceResult:
    _ensure_windows_admin()
    _run_winsw("start")
    return ServiceResult(True, "Windows Serviceを起動しました。")


def stop_service() -> ServiceResult:
    _ensure_windows_admin()
    _run_winsw("stop")
    return ServiceResult(True, "Windows Serviceを停止しました。")


def restart_service() -> ServiceResult:
    _ensure_windows_admin()
    _run_winsw("restart")
    return ServiceResult(True, "Windows Serviceを再起動しました。")


def uninstall_service() -> ServiceResult:
    _ensure_windows_admin()
    _run_winsw("uninstall")
    return ServiceResult(True, "Windows Serviceをアンインストールしました。")


def service_status() -> ServiceResult:
    if not is_windows():
        raise ServiceError("Windows ServiceはWindows専用です。Linuxではsystemdを利用してください。")
    winsw = service_winsw_exe()
    if not winsw.exists() or not service_winsw_xml().exists():
        return ServiceResult(False, "Windows Serviceはまだインストールされていません。")
    completed = subprocess.run([str(winsw), "status"], capture_output=True, text=True, check=False)
    output = (completed.stdout or completed.stderr or "").strip()
    return ServiceResult(completed.returncode == 0, output or "Windows Serviceの状態を取得しました。")


def service_summary() -> list[str]:
    if not is_windows():
        return ["Service installed: no (Windows only)", "Service running: no (Windows only)"]
    installed = service_winsw_exe().exists() and service_winsw_xml().exists()
    running = "unknown"
    if installed:
        try:
            status = service_status()
            running = "yes" if "Started" in status.message or "Running" in status.message else "no"
        except ServiceError:
            running = "unknown"
    return [f"Service installed: {'yes' if installed else 'no'}", f"Service running: {running}"]


def _ensure_windows_admin() -> None:
    if not is_windows():
        raise ServiceError("Windows ServiceはWindows専用です。Linuxではsystemdを利用してください。")
    if not is_admin():
        raise ServiceError(
            "Windows Serviceとして常駐化するには、管理者権限のPowerShellで実行してください。"
            "管理者権限がない場合は `mcctl-agent autostart install` を利用できます。"
        )


def _copy_user_config_if_needed(config_path: Path) -> None:
    if config_path.exists():
        return
    source = default_config_path()
    if source.exists() and source != config_path:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, config_path)


def _ensure_winsw_binary(path: Path) -> None:
    if path.exists():
        return
    try:
        with urllib.request.urlopen(WINSW_DOWNLOAD_URL, timeout=30) as response:
            path.write_bytes(response.read())
    except Exception as exc:
        raise ServiceError(
            "WinSWの取得に失敗しました。ネットワークを確認するか、WinSW-x64.exe を "
            f"{path} に配置してから再実行してください。"
        ) from exc


def _run_winsw(command: str) -> None:
    winsw = service_winsw_exe()
    if not winsw.exists():
        raise ServiceError("WinSW実行ファイルが見つかりません。先に `mcctl-agent service install` を実行してください。")
    completed = subprocess.run([str(winsw), command], capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        output = (completed.stderr or completed.stdout or "").strip()
        raise ServiceError(output or f"Windows Service command failed: {command}")
