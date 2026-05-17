from __future__ import annotations

import socket
from pathlib import Path

import pytest

from mcctl_agent.server_setup import (
    check_port,
    create_directory,
    create_minecraft_server,
    list_directories,
    normalize_properties,
    required_java_major,
    validate_server_directory,
)


def test_required_java_major_by_minecraft_version() -> None:
    assert required_java_major("1.20.5") == 21
    assert required_java_major("1.21.1") == 21
    assert required_java_major("1.18.2") == 17
    assert required_java_major("1.17.1") == 16
    assert required_java_major("1.16.5") == 8


def test_directory_list_and_create_respect_allowed_roots(tmp_path: Path) -> None:
    root = tmp_path / "minecraft"
    root.mkdir()
    created = create_directory({"path": str(root / "server1")}, [str(root)])
    assert created["created"] is True
    listing = list_directories({"path": str(root)}, [str(root)])
    assert listing["writable"] is True
    assert any(item["name"] == "server1" for item in listing["directories"])


def test_create_directory_rejects_outside_allowed_roots(tmp_path: Path) -> None:
    root = tmp_path / "minecraft"
    other = tmp_path / "other"
    root.mkdir()
    with pytest.raises(RuntimeError):
        create_directory({"path": str(other / "server1")}, [str(root)])


def test_validate_server_directory_allows_missing_inside_root(tmp_path: Path) -> None:
    root = tmp_path / "minecraft"
    root.mkdir()
    result = validate_server_directory({"root_path": str(root / "new")}, [str(root)])
    assert result["exists"] is False
    assert "まだ存在しません" in result["warnings"][0]


def test_properties_validation_and_port_check() -> None:
    properties = normalize_properties({"server-port": "25565", "online-mode": "false"})
    assert properties["server-port"] == "25565"
    assert properties["online-mode"] == "false"
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("0.0.0.0", 0))
        port = sock.getsockname()[1]
        result = check_port({"port": port})
    assert result["available"] is False


def test_create_minecraft_server_writes_eula_and_properties(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "minecraft"
    root.mkdir()

    monkeypatch.setattr(
        "mcctl_agent.server_setup.detect_java",
            lambda required_major=None, manual_path=None: {
            "status": "ok",
            "required_major": required_major,
            "selected": {"executable_path": "/usr/bin/java", "major_version": 21},
            "java_candidates": [{"executable_path": "/usr/bin/java", "major_version": 21}],
            "install_guidance": {},
        },
    )
    monkeypatch.setattr("mcctl_agent.server_setup.check_port", lambda payload: {"port": 25565, "available": True, "message": "ok"})
    monkeypatch.setattr("mcctl_agent.server_setup._download_server_jar", lambda server_type, version, build, target: target.write_bytes(b"jar"))

    result = create_minecraft_server(
        {
            "server_type": "vanilla",
            "minecraft_version": "1.21.1",
            "root_path": str(root / "server1"),
            "eula_accepted": True,
            "properties": {"motd": "hello"},
        },
        [str(root)],
    )

    server_root = Path(result["root_path"])
    assert (server_root / "server.jar").exists()
    assert (server_root / "eula.txt").read_text(encoding="utf-8") == "eula=true\n"
    assert "motd=hello" in (server_root / "server.properties").read_text(encoding="utf-8")
