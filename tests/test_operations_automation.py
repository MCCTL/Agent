import asyncio
from datetime import datetime, timezone

from mcctl_agent.file_admin import create_manual_backup, list_backups
from mcctl_agent.runtime import ManagedServer, ServerRuntimeManager, _parse_players


def test_scheduled_backup_kind_is_reported(tmp_path, monkeypatch) -> None:
    root = tmp_path / "server"
    root.mkdir()
    (root / "server.properties").write_text("server-port=25565", encoding="utf-8")
    data_dir = tmp_path / "agent-data"
    monkeypatch.setenv("MCCTL_AGENT_DATA_DIR", str(data_dir))

    backup = create_manual_backup({"server_id": "server-1", "root_path": str(root)}, kind="scheduled")
    backups = list_backups({"server_id": "server-1"})["backups"]

    assert backup["backup"]["kind"] == "scheduled"
    assert backups[0]["kind"] == "scheduled"


def test_parse_online_players_from_console() -> None:
    server = ManagedServer(server_id="server-1")
    server.console.append("There are 2 of a max of 20 players online: Alex, Steve")

    assert _parse_players(server.console) == ["Alex", "Steve"]


def test_player_action_sends_console_command() -> None:
    async def run() -> None:
        manager = ServerRuntimeManager()
        sent: list[str] = []

        async def fake_send(server_id: str, command: str):
            sent.append(command)
            return {"accepted": True}

        manager.send_command = fake_send  # type: ignore[method-assign]
        result = await manager.player_action("server-1", "kick", "Alex", "rule")

        assert result["accepted"] is True
        assert sent == ["kick Alex rule"]

    asyncio.run(run())


def test_crash_restart_retry_suppression() -> None:
    async def run() -> None:
        manager = ServerRuntimeManager()
        server = ManagedServer(server_id="server-1", last_payload={"server_id": "server-1"})
        server.automation_settings = {
            "crash_restart_enabled": True,
            "restart_delay_seconds": 1,
            "max_restarts": 1,
            "restart_window_seconds": 600,
        }
        server.restart_attempts.append(datetime.now(timezone.utc))

        await manager._handle_unexpected_exit(server, 1)  # noqa: SLF001 - crash policy coverage

        assert server.crash_events[-1]["restart_suppressed_reason"] == "max_restarts_exceeded"

    asyncio.run(run())
