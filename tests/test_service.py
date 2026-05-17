from pathlib import Path

import pytest

from mcctl_agent import service


def test_winsw_config_uses_absolute_agent_and_service_config() -> None:
    config = service.build_winsw_config(
        Path(r"C:\Users\beta\.local\bin\mcctl-agent.exe"),
        Path(r"C:\ProgramData\MCCTL\Agent\agent.json"),
        Path(r"C:\ProgramData\MCCTL\Agent\logs"),
    )

    assert "<id>MCCTLAgent</id>" in config
    assert "<name>MCCTL Agent</name>" in config
    assert r"C:\Users\beta\.local\bin\mcctl-agent.exe" in config
    assert "MCCTL_AGENT_CONFIG" in config
    assert "agent.json" in config


def test_service_install_requires_windows_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "is_windows", lambda: True)
    monkeypatch.setattr(service, "is_admin", lambda: False)

    with pytest.raises(service.ServiceError, match="管理者権限"):
        service.install_service()


def test_service_command_errors_on_non_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "is_windows", lambda: False)

    with pytest.raises(service.ServiceError, match="Windows専用"):
        service.service_status()
