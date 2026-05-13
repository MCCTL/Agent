from __future__ import annotations

import asyncio
import os
import shlex
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable


STOPPED = "stopped"
STARTING = "starting"
RUNNING = "running"
STOPPING = "stopping"
ERROR = "error"


@dataclass
class ManagedServer:
    server_id: str
    process: asyncio.subprocess.Process | None = None
    status: str = STOPPED
    started_at: datetime | None = None
    stopped_at: datetime | None = None
    exit_code: int | None = None
    console: deque[str] = field(default_factory=lambda: deque(maxlen=500))
    reader_task: asyncio.Task[None] | None = None
    expected_stop: bool = False
    last_payload: dict[str, Any] | None = None
    automation_settings: dict[str, Any] = field(default_factory=dict)
    restart_attempts: deque[datetime] = field(default_factory=lambda: deque(maxlen=20))
    crash_events: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=50))


class ServerRuntimeManager:
    def __init__(self) -> None:
        self._servers: dict[str, ManagedServer] = {}
        self.event_handler: Callable[[dict[str, Any]], Awaitable[None]] | None = None

    async def start(self, payload: dict[str, Any]) -> dict[str, Any]:
        server_id = str(payload["server_id"])
        server = self._servers.setdefault(server_id, ManagedServer(server_id=server_id))
        if server.process and server.process.returncode is None:
            server.status = RUNNING
            return self.runtime(server_id, "Server is already running.")

        command = build_start_command(payload)
        root_path = Path(str(payload["root_path"])).expanduser()
        if _eula_exists_but_not_accepted(root_path):
            raise RuntimeError("eula.txt exists but eula=true was not found. Accept the Minecraft EULA before starting.")
        server.status = STARTING
        server.exit_code = None
        server.expected_stop = False
        server.last_payload = dict(payload)
        server.console.append(f"[mcctl] Starting server with {command[0]}")
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(root_path),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        server.process = process
        server.status = RUNNING
        server.started_at = datetime.now(timezone.utc)
        server.stopped_at = None
        server.reader_task = asyncio.create_task(self._read_console(server, process))
        return self.runtime(server_id, "Server started.")

    async def stop(self, payload: dict[str, Any]) -> dict[str, Any]:
        server_id = str(payload["server_id"])
        server = self._servers.setdefault(server_id, ManagedServer(server_id=server_id))
        process = server.process
        if process is None or process.returncode is not None:
            server.expected_stop = True
            server.status = STOPPED
            server.stopped_at = datetime.now(timezone.utc)
            return self.runtime(server_id, "Server is not running.")

        server.status = STOPPING
        server.expected_stop = True
        forced = False
        if process.stdin is not None:
            process.stdin.write(b"stop\n")
            await process.stdin.drain()
        try:
            await asyncio.wait_for(process.wait(), timeout=20)
        except asyncio.TimeoutError:
            forced = True
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=10)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()

        server.status = STOPPED
        server.stopped_at = datetime.now(timezone.utc)
        server.exit_code = process.returncode
        return self.runtime(server_id, "Server stopped.", forced=forced)

    async def restart(self, payload: dict[str, Any]) -> dict[str, Any]:
        await self.stop(payload)
        return await self.start(payload)

    def update_automation_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        server_id = str(payload["server_id"])
        server = self._servers.setdefault(server_id, ManagedServer(server_id=server_id))
        server.automation_settings = {
            "crash_restart_enabled": bool(payload.get("crash_restart_enabled", False)),
            "restart_delay_seconds": max(1, int(payload.get("restart_delay_seconds") or 30)),
            "max_restarts": max(1, int(payload.get("max_restarts") or 3)),
            "restart_window_seconds": max(60, int(payload.get("restart_window_seconds") or 600)),
            "notify_on_crash": bool(payload.get("notify_on_crash", True)),
            "notify_on_recovery": bool(payload.get("notify_on_recovery", True)),
        }
        return {"server_id": server_id, "status": "updated", "settings": server.automation_settings}

    def crash_events(self, server_id: str) -> dict[str, Any]:
        server = self._servers.setdefault(server_id, ManagedServer(server_id=server_id))
        return {"server_id": server_id, "events": list(server.crash_events)}

    def runtime(self, server_id: str, message: str | None = None, forced: bool | None = None) -> dict[str, Any]:
        server = self._servers.setdefault(server_id, ManagedServer(server_id=server_id))
        process = server.process
        if process and process.returncode is not None and server.status in {RUNNING, STARTING, STOPPING}:
            server.status = STOPPED if process.returncode == 0 else ERROR
            server.exit_code = process.returncode
            server.stopped_at = datetime.now(timezone.utc)
        return {
            "server_id": server_id,
            "status": server.status,
            "pid": process.pid if process and process.returncode is None else None,
            "started_at": _format_dt(server.started_at),
            "stopped_at": _format_dt(server.stopped_at),
            "exit_code": server.exit_code,
            "message": message,
            "forced": forced,
        }

    def console_tail(self, server_id: str, lines: int = 120) -> dict[str, Any]:
        server = self._servers.setdefault(server_id, ManagedServer(server_id=server_id))
        return {"server_id": server_id, "lines": list(server.console)[-lines:]}

    async def send_command(self, server_id: str, command: str) -> dict[str, Any]:
        server = self._servers.setdefault(server_id, ManagedServer(server_id=server_id))
        process = server.process
        if process is None or process.returncode is not None or process.stdin is None:
            raise RuntimeError("Server process is not running.")
        process.stdin.write((command.strip() + "\n").encode("utf-8"))
        await process.stdin.drain()
        server.console.append(f"[mcctl] Console command sent: {command.strip().split()[0] if command.strip() else ''}")
        return {"server_id": server_id, "accepted": True, "message": "Command sent."}

    async def get_online_players(self, server_id: str) -> dict[str, Any]:
        server = self._servers.setdefault(server_id, ManagedServer(server_id=server_id))
        if server.process is None or server.process.returncode is not None or server.process.stdin is None:
            return {"server_id": server_id, "players": []}
        await self.send_command(server_id, "list")
        await asyncio.sleep(0.25)
        return {"server_id": server_id, "players": [{"name": name} for name in _parse_players(server.console)]}

    async def player_action(
        self,
        server_id: str,
        action: str,
        player_name: str,
        reason: str = "",
    ) -> dict[str, Any]:
        if action not in {"kick", "ban", "pardon"}:
            raise RuntimeError("Unsupported player action.")
        command = f"{action} {player_name}"
        if action in {"kick", "ban"} and reason.strip():
            command = f"{command} {reason.strip()}"
        await self.send_command(server_id, command)
        return {"server_id": server_id, "player_name": player_name, "action": action, "accepted": True}

    async def _read_console(self, server: ManagedServer, process: asyncio.subprocess.Process) -> None:
        if process.stdout is None:
            return
        try:
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                server.console.append(line.decode("utf-8", errors="replace").rstrip())
        finally:
            await process.wait()
            if server.process is process:
                server.exit_code = process.returncode
                server.status = STOPPED if process.returncode == 0 else ERROR
                server.stopped_at = datetime.now(timezone.utc)
                if process.returncode != 0 and not server.expected_stop:
                    asyncio.create_task(self._handle_unexpected_exit(server, process.returncode))

    async def _handle_unexpected_exit(self, server: ManagedServer, exit_code: int | None) -> None:
        now = datetime.now(timezone.utc)
        settings = server.automation_settings
        event = {
            "detected_at": now.isoformat(),
            "exit_code": exit_code,
            "restart_attempted": False,
            "restart_succeeded": None,
            "restart_suppressed_reason": None,
        }
        if not settings.get("crash_restart_enabled") or server.last_payload is None:
            event["restart_suppressed_reason"] = "disabled"
            server.crash_events.append(event)
            await self._emit_crash_event(server, event)
            return

        window = timedelta(seconds=int(settings.get("restart_window_seconds") or 600))
        while server.restart_attempts and now - server.restart_attempts[0] > window:
            server.restart_attempts.popleft()
        max_restarts = int(settings.get("max_restarts") or 3)
        if len(server.restart_attempts) >= max_restarts:
            event["restart_suppressed_reason"] = "max_restarts_exceeded"
            server.crash_events.append(event)
            server.console.append("[mcctl] Crash auto restart suppressed: retry limit reached.")
            await self._emit_crash_event(server, event)
            return

        event["restart_attempted"] = True
        server.restart_attempts.append(now)
        server.crash_events.append(event)
        delay = int(settings.get("restart_delay_seconds") or 30)
        server.console.append(f"[mcctl] Unexpected exit detected. Restarting in {delay} seconds.")
        await asyncio.sleep(delay)
        try:
            await self.start(server.last_payload)
            event["restart_succeeded"] = True
            server.console.append("[mcctl] Crash auto restart completed.")
        except Exception as exc:
            event["restart_succeeded"] = False
            event["restart_suppressed_reason"] = str(exc)
            server.console.append(f"[mcctl] Crash auto restart failed: {exc}")
        await self._emit_crash_event(server, event)

    async def _emit_crash_event(self, server: ManagedServer, event: dict[str, Any]) -> None:
        if self.event_handler is None:
            return
        await self.event_handler({"event": "server_crash", "server_id": server.server_id, **event})


def build_start_command(payload: dict[str, Any]) -> list[str]:
    java_path = str(payload.get("java_path") or "").strip()
    jar_path = str(payload.get("jar_path") or "").strip()
    root_path = Path(str(payload.get("root_path") or "")).expanduser()
    if not java_path:
        raise RuntimeError("Java executable path is required.")
    if not jar_path:
        raise RuntimeError("Server jar path is required.")
    jar = Path(jar_path)
    if not jar.is_absolute():
        jar = root_path / jar
    command = [java_path]
    command.extend(_split_args(str(payload.get("jvm_args") or "")))
    command.extend(["-jar", str(jar)])
    command.extend(_split_args(str(payload.get("server_args") or "")))
    return command


def _split_args(value: str) -> list[str]:
    if not value.strip():
        return []
    return shlex.split(value, posix=os.name != "nt")


def _format_dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _eula_exists_but_not_accepted(root_path: Path) -> bool:
    eula_path = root_path / "eula.txt"
    if not eula_path.exists():
        return False
    try:
        for line in eula_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            normalized = line.strip().lower().replace(" ", "")
            if normalized.startswith("eula="):
                return normalized != "eula=true"
    except OSError:
        return True
    return True


def _parse_players(console: deque[str]) -> list[str]:
    for line in reversed(console):
        marker = "players online:"
        if marker not in line:
            continue
        names = line.split(marker, 1)[1].strip()
        if not names:
            return []
        return [name.strip() for name in names.split(",") if name.strip()]
    return []
