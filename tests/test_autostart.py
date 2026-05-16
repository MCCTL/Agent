import platform
from pathlib import Path

import pytest

from mcctl_agent.autostart import (
    AutostartError,
    TASK_NAME,
    build_schtasks_install_command,
    build_schtasks_status_command,
    build_schtasks_uninstall_command,
    ensure_windows,
)


def test_windows_schtasks_install_command_uses_logon_task():
    command = build_schtasks_install_command(Path(r"C:\Users\beta\.local\bin\mcctl-agent.exe"))

    assert command[:2] == ["schtasks", "/Create"]
    assert "/TN" in command
    assert TASK_NAME in command
    assert "/SC" in command
    assert "ONLOGON" in command
    assert "/RL" in command
    assert "LIMITED" in command
    assert '"C:\\Users\\beta\\.local\\bin\\mcctl-agent.exe"' in command


def test_windows_schtasks_uninstall_and_status_commands():
    assert build_schtasks_uninstall_command() == ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"]
    assert build_schtasks_status_command() == ["schtasks", "/Query", "/TN", TASK_NAME]


def test_autostart_errors_clearly_on_non_windows():
    if platform.system().lower() == "windows":
        pytest.skip("non-Windows behavior only")

    with pytest.raises(AutostartError, match="systemd"):
        ensure_windows()
