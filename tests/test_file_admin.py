import asyncio
import os
import threading
import zipfile
from pathlib import Path

import pytest

from mcctl_agent.file_admin import (
    create_manual_backup,
    delete_backup,
    disable_plugin,
    enable_plugin,
    list_backups,
    list_editable_files,
    list_plugins,
    read_editable_file,
    restore_backup,
    write_editable_file,
)
from mcctl_agent.operations import OperationRegistry
from mcctl_agent import main as agent_main


def make_plugin(path: Path, metadata_name: str = "ExamplePlugin") -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("plugin.yml", f"name: {metadata_name}\nversion: 1.2.3\ndescription: Test plugin\n")


def test_plugin_jar_detection_and_metadata(tmp_path: Path) -> None:
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    make_plugin(plugins / "Example.jar")
    make_plugin(plugins / "Disabled.jar.disabled", "DisabledPlugin")

    result = list_plugins({"root_path": str(tmp_path)})

    assert {item["filename"] for item in result["plugins"]} == {"Example.jar", "Disabled.jar.disabled"}
    enabled = next(item for item in result["plugins"] if item["filename"] == "Example.jar")
    disabled = next(item for item in result["plugins"] if item["filename"] == "Disabled.jar.disabled")
    assert enabled["display_name"] == "ExamplePlugin"
    assert enabled["enabled"] is True
    assert enabled["restart_required"] is False
    assert enabled["pending_state"] == "none"
    assert disabled["enabled"] is False


def test_plugin_enable_disable_uses_rename(tmp_path: Path) -> None:
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    make_plugin(plugins / "Example.jar")

    disabled = disable_plugin({"root_path": str(tmp_path), "plugin_id": "Example.jar"})
    enabled = enable_plugin({"root_path": str(tmp_path), "plugin_id": "Example.jar.disabled"})

    assert disabled["filename"] == "Example.jar.disabled"
    assert disabled["pending_state"] == "restart_required"
    assert enabled["filename"] == "Example.jar"
    assert enabled["pending_state"] == "restart_required"
    assert (plugins / "Example.jar").exists()


def test_editable_file_read_write_and_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_dir = tmp_path / "agent-data"
    monkeypatch.setenv("MCCTL_AGENT_DATA_DIR", str(data_dir))
    (tmp_path / "server.properties").write_text("motd=old", encoding="utf-8")
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "config.yml").write_text("enabled: true", encoding="utf-8")

    listed = list_editable_files({"root_path": str(tmp_path)})
    read = read_editable_file({"root_path": str(tmp_path), "path": "server.properties"})
    written = write_editable_file(
        {"root_path": str(tmp_path), "server_id": "server-1", "path": "server.properties", "content": "motd=new"}
    )

    assert listed["files"]
    assert read["content"] == "motd=old"
    assert written["backup_id"]
    assert (tmp_path / "server.properties").read_text(encoding="utf-8") == "motd=new"
    assert list((data_dir / "backups" / "server-1" / "file-edits").glob("*.bak"))


def test_path_traversal_and_extension_are_rejected(tmp_path: Path) -> None:
    (tmp_path / "server.properties").write_text("ok", encoding="utf-8")
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "bad.exe").write_text("bad", encoding="utf-8")

    with pytest.raises(RuntimeError):
        read_editable_file({"root_path": str(tmp_path), "path": "../outside.txt"})
    with pytest.raises(RuntimeError):
        read_editable_file({"root_path": str(tmp_path), "path": "plugins/bad.exe"})


def test_symlink_escape_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-secret.txt"
    outside.write_text("secret", encoding="utf-8")
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    link = plugins / "escape.yml"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is not available in this environment")

    with pytest.raises(RuntimeError):
        read_editable_file({"root_path": str(tmp_path), "path": "plugins/escape.yml"})


def test_manual_backup_create_restore_and_delete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCCTL_AGENT_DATA_DIR", str(tmp_path / "agent-data"))
    (tmp_path / "server.properties").write_text("motd=before", encoding="utf-8")
    payload = {"root_path": str(tmp_path), "server_id": "server-1"}

    created = create_manual_backup(payload)
    (tmp_path / "server.properties").write_text("motd=after", encoding="utf-8")
    backups = list_backups(payload)
    restored = restore_backup({**payload, "backup_id": created["backup"]["backup_id"]})
    deleted = delete_backup({**payload, "backup_id": created["backup"]["backup_id"]})

    assert created["backup"]["backup_id"] in {backup["backup_id"] for backup in backups["backups"]}
    assert restored["safety_backup_id"]
    assert (tmp_path / "server.properties").read_text(encoding="utf-8") == "motd=before"
    assert deleted["status"] == "deleted"


def test_backup_excludes_runtime_directories(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCCTL_AGENT_DATA_DIR", str(tmp_path / "agent-data"))
    (tmp_path / "world").mkdir()
    (tmp_path / "world" / "level.dat").write_text("world", encoding="utf-8")
    for dirname in ("backups", "logs", "cache", "crash-reports"):
        (tmp_path / dirname).mkdir()
        (tmp_path / dirname / "ignored.txt").write_text("ignored", encoding="utf-8")
    (tmp_path / "session.lock").write_text("lock", encoding="utf-8")

    created = create_manual_backup({"root_path": str(tmp_path), "server_id": "server-1"})
    archive_path = tmp_path / "agent-data" / "backups" / "server-1" / f"{created['backup']['backup_id']}.zip"

    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())

    assert "world/level.dat" in names
    assert "logs/ignored.txt" not in names
    assert "cache/ignored.txt" not in names
    assert "crash-reports/ignored.txt" not in names
    assert "session.lock" not in names


def test_backup_permission_denied_returns_japanese_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCCTL_AGENT_DATA_DIR", str(tmp_path / "agent-data"))

    def raise_permission(*args, **kwargs):
        exc = PermissionError("denied")
        exc.filename = str(tmp_path / "server.properties")
        raise exc

    monkeypatch.setattr("mcctl_agent.file_admin._write_backup_zip", raise_permission)

    with pytest.raises(RuntimeError, match="バックアップを作成できません"):
        create_manual_backup({"root_path": str(tmp_path), "server_id": "server-1"})


def test_running_server_backup_uses_online_mode_and_save_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        commands: list[str] = []

        class FakeRuntime:
            def runtime(self, server_id: str) -> dict:
                return {"server_id": server_id, "status": "running"}

            async def send_command(self, server_id: str, command: str) -> dict:
                commands.append(command)
                return {"accepted": True}

        def fake_backup(payload: dict, *, kind: str = "manual", mode: str = "cold") -> dict:
            return {"backup": {"backup_id": "backup-1", "mode": mode}, "mode": mode}

        original_sleep = asyncio.sleep
        monkeypatch.setattr(agent_main, "runtime_manager", FakeRuntime())
        monkeypatch.setattr(agent_main, "create_manual_backup", fake_backup)
        monkeypatch.setattr(agent_main.asyncio, "sleep", lambda delay: original_sleep(0))

        result = await agent_main.run_backup_operation({"server_id": "server-1", "root_path": "unused"}, kind="manual")

        assert commands == ["save-off", "save-all flush", "save-on"]
        assert result["backup"]["mode"] == "online"

    asyncio.run(run())


def test_online_backup_failure_still_sends_save_on(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        commands: list[str] = []

        class FakeRuntime:
            def runtime(self, server_id: str) -> dict:
                return {"server_id": server_id, "status": "running"}

            async def send_command(self, server_id: str, command: str) -> dict:
                commands.append(command)
                return {"accepted": True}

        def fail_backup(payload: dict, *, kind: str = "manual", mode: str = "cold") -> dict:
            raise RuntimeError("zip failed")

        original_sleep = asyncio.sleep
        monkeypatch.setattr(agent_main, "runtime_manager", FakeRuntime())
        monkeypatch.setattr(agent_main, "create_manual_backup", fail_backup)
        monkeypatch.setattr(agent_main.asyncio, "sleep", lambda delay: original_sleep(0))

        with pytest.raises(RuntimeError, match="zip failed"):
            await agent_main.run_backup_operation({"server_id": "server-1", "root_path": "unused"}, kind="manual")

        assert commands == ["save-off", "save-all flush", "save-on"]

    asyncio.run(run())


def test_operation_registry_tracks_long_running_status() -> None:
    async def run() -> None:
        started = asyncio.Event()
        finish = asyncio.Event()
        registry = OperationRegistry()

        async def runner() -> dict:
            started.set()
            await finish.wait()
            return {"message": "done"}

        created = registry.start("backup create", runner)
        await started.wait()
        running = registry.get(created["operation_id"])
        finish.set()
        await asyncio.sleep(0)
        completed = registry.get(created["operation_id"])

        assert running["status"] == "running"
        assert completed["status"] == "success"
        assert completed["result"] == {"message": "done"}

    asyncio.run(run())


def test_long_running_backup_does_not_block_other_agent_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        started = threading.Event()
        finish = threading.Event()

        def slow_backup(payload: dict, **kwargs: object) -> dict:
            started.set()
            finish.wait(timeout=2)
            return {"backup": {"backup_id": "backup-1"}, "message": "done"}

        class StoppedRuntimeManager:
            def runtime(self, server_id: str) -> dict:
                return {"server_id": server_id, "status": "stopped"}

        monkeypatch.setattr(agent_main, "runtime_manager", StoppedRuntimeManager())
        monkeypatch.setattr(agent_main, "create_manual_backup", slow_backup)
        monkeypatch.setattr(agent_main, "list_backups", lambda payload: {"backups": []})

        created = await agent_main.dispatch_command("create_manual_backup", {"server_id": "server-1"})
        assert await asyncio.to_thread(started.wait, 1)

        running = await agent_main.dispatch_command(
            "get_operation_status",
            {"operation_id": created["operation_id"]},
        )
        listed = await agent_main.dispatch_command("list_backups", {"server_id": "server-1"})
        finish.set()
        await asyncio.sleep(0.05)
        completed = await agent_main.dispatch_command(
            "get_operation_status",
            {"operation_id": created["operation_id"]},
        )

        assert running["status"] == "running"
        assert listed == {"backups": []}
        assert completed["status"] == "success"

    asyncio.run(run())
