from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import sys
import webbrowser
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import websockets
from websockets.exceptions import InvalidStatus

from mcctl_agent.api import claim_pairing_session, create_pairing_session, websocket_url
from mcctl_agent.autostart import (
    AutostartError,
    install_windows_autostart,
    uninstall_windows_autostart,
    windows_autostart_status,
)
from mcctl_agent.config import AgentConfig, default_config_path, resolve_api_base_url
from mcctl_agent.file_admin import (
    create_manual_backup,
    delete_backup,
    disable_plugin,
    enable_plugin,
    install_uploaded_plugin,
    list_backups,
    list_editable_files,
    list_plugins,
    read_editable_file,
    restore_backup,
    write_editable_file,
)
from mcctl_agent.java import detect_java_installations
from mcctl_agent.minecraft import inspect_server_directory
from mcctl_agent.operations import OperationRegistry
from mcctl_agent.runtime import ServerRuntimeManager


runtime_manager = ServerRuntimeManager()
operation_registry = OperationRegistry()


def agent_version() -> str:
    try:
        return version("mcctl-agent")
    except PackageNotFoundError:
        return "0.0.0-dev"


def main() -> None:
    parser = argparse.ArgumentParser(description="mcctl local agent")
    parser.add_argument("--api-url", default=resolve_api_base_url())
    parser.add_argument("--config", type=Path, default=default_config_path())
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("reset", help="clear saved agent token and device information")
    subparsers.add_parser("status", help="show local agent configuration without printing secrets")
    autostart_parser = subparsers.add_parser("autostart", help="manage Windows logon autostart")
    autostart_subparsers = autostart_parser.add_subparsers(dest="autostart_command", required=True)
    autostart_subparsers.add_parser("install", help="install Windows Task Scheduler autostart")
    autostart_subparsers.add_parser("uninstall", help="remove Windows Task Scheduler autostart")
    autostart_subparsers.add_parser("status", help="show Windows autostart status")
    args = parser.parse_args()

    if args.command == "reset":
        reset_agent_config(args.config)
        return
    if args.command == "status":
        print_agent_status(args.config, args.api_url)
        return
    if args.command == "autostart":
        handle_autostart(args.autostart_command)
        return

    config = AgentConfig.load(args.config)
    config.api_base_url = args.api_url
    config.save(args.config)
    warn_for_insecure_api(config.api_base_url)

    try:
        asyncio.run(run_agent(config, args.config))
    except KeyboardInterrupt:
        print("Agent stopped.")


async def run_agent(config: AgentConfig, config_path: Path) -> None:
    if not config.agent_token:
        await pair_agent(config, config_path)
    await connect_websocket(config, config_path)


def reset_agent_config(config_path: Path) -> bool:
    if not config_path.exists():
        print(f"No saved MCCTL Agent config found at {config_path}.")
        return False

    config = AgentConfig.load(config_path)
    config.agent_token = None
    config.device_id = None
    config.save(config_path)
    print(f"Cleared saved MCCTL Agent token and device information at {config_path}.")
    print("Start mcctl-agent again to create a new pairing URL.")
    return True


def print_agent_status(config_path: Path, api_base_url: str) -> None:
    config = AgentConfig.load(config_path)
    configured = bool(config.agent_token and config.device_id)
    print("MCCTL Agent status")
    print(f"Version: {agent_version()}")
    print(f"Configured: {'yes' if configured else 'no'}")
    print(f"Device ID: {config.device_id or 'not paired'}")
    print(f"API URL: {api_base_url}")
    print(f"Config path: {config_path}")
    print(f"Token saved: {'yes' if config.agent_token else 'no'}")
    print("Token value: hidden")


def handle_autostart(command: str) -> None:
    try:
        if command == "install":
            result = install_windows_autostart()
        elif command == "uninstall":
            result = uninstall_windows_autostart()
        elif command == "status":
            result = windows_autostart_status()
        else:
            raise AutostartError(f"Unknown autostart command: {command}")
    except AutostartError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
    print(result.message)


def warn_for_insecure_api(api_base_url: str) -> None:
    if api_base_url.startswith("https://"):
        return
    print(
        "Warning: MCCTL_API_BASE_URL is not HTTPS. Use this only for local development.",
        file=sys.stderr,
    )


async def pair_agent(config: AgentConfig, config_path: Path) -> None:
    session = await create_pairing_session(config.api_base_url, config.agent_fingerprint)
    print("MCCTL にこのデバイスを接続します")
    print(f"開く: {session.pairing_url}")
    print(f"接続コード: {session.public_code}")
    print("このコードは 10 分で失効します。")
    maybe_open_browser(session.pairing_url)

    while datetime.now(timezone.utc) < session.expires_at:
        claimed = await claim_pairing_session(config.api_base_url, session.token, session.waiting_token)
        if claimed is not None:
            device_id, agent_token = claimed
            config.device_id = device_id
            config.agent_token = agent_token
            config.save(config_path)
            print("Pairing completed. Agent token saved locally.")
            return
        await asyncio.sleep(2)
    raise RuntimeError("Pairing session expired before it was confirmed.")


def maybe_open_browser(url: str) -> None:
    system = platform.system().lower()
    should_open = system == "windows" or bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    if not should_open:
        return
    try:
        webbrowser.open(url)
    except Exception:
        return


async def connect_websocket(config: AgentConfig, config_path: Path) -> None:
    if not config.agent_token:
        raise RuntimeError("Agent token is missing.")

    url = websocket_url(config.api_base_url)
    print("Connecting to MCCTL API WebSocket.")
    backoff_seconds = 2
    while True:
        try:
            async with websockets.connect(
                url,
                additional_headers={
                    "X-MCCTL-Agent-Token": config.agent_token,
                    "X-MCCTL-Agent-Version": agent_version(),
                },
                ping_interval=None,
            ) as websocket:
                print("Agent connected.")
                backoff_seconds = 2
                send_lock = asyncio.Lock()

                async def emit_event(event: dict) -> None:
                    async with send_lock:
                        await websocket.send(json.dumps({"type": "agent_event", "data": event}))

                runtime_manager.event_handler = emit_event
                heartbeat_task = asyncio.create_task(send_heartbeats(websocket, send_lock))
                response_tasks: set[asyncio.Task[None]] = set()
                try:
                    async for message in websocket:
                        task = asyncio.create_task(handle_websocket_message(websocket, send_lock, message))
                        response_tasks.add(task)
                        task.add_done_callback(response_tasks.discard)
                finally:
                    heartbeat_task.cancel()
                    runtime_manager.event_handler = None
                    for task in response_tasks:
                        task.cancel()
                    await asyncio.gather(heartbeat_task, *response_tasks, return_exceptions=True)
        except Exception as exc:
            if is_auth_rejection(exc):
                print(
                    "Connection rejected with HTTP 401/403. The saved agent token may be invalid.",
                    file=sys.stderr,
                )
                print("Run `mcctl-agent reset`, then start the agent again to re-pair this device.", file=sys.stderr)
                print(f"Config path: {config_path}", file=sys.stderr)
                return
            print(f"Connection lost: {exc}", file=sys.stderr)
            await asyncio.sleep(backoff_seconds)
            backoff_seconds = min(backoff_seconds * 2, 60)


def is_auth_rejection(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None) or getattr(exc, "status_code", None)
    if status_code in {401, 403}:
        return True
    if isinstance(exc, InvalidStatus):
        return "401" in str(exc) or "403" in str(exc)
    return "HTTP 401" in str(exc) or "HTTP 403" in str(exc)


async def send_heartbeats(websocket, send_lock: asyncio.Lock) -> None:
    while True:
        async with send_lock:
            await websocket.send(json.dumps({"type": "heartbeat"}))
        await asyncio.sleep(20)


async def handle_websocket_message(websocket, send_lock: asyncio.Lock, message: str) -> None:
    if message in {"pong", ""}:
        return
    try:
        data = json.loads(message)
    except json.JSONDecodeError:
        return
    if data.get("type") in {"heartbeat_ack", "ack"}:
        return
    if data.get("type") != "command":
        return
    request_id = str(data.get("request_id") or "")
    command = str(data.get("command") or "")
    payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
    try:
        response_data = await dispatch_command(command, payload)
        async with send_lock:
            await websocket.send(
                json.dumps(
                    {
                        "type": "agent_response",
                        "request_id": request_id,
                        "ok": True,
                        "data": response_data,
                    }
                )
            )
    except Exception as exc:
        async with send_lock:
            await websocket.send(
                json.dumps(
                    {
                        "type": "agent_response",
                        "request_id": request_id,
                        "ok": False,
                        "error": {"message": str(exc), "code": command or "unknown_command"},
                    }
                )
            )


async def dispatch_command(command: str, payload: dict) -> dict:
    if command == "detect_java_installations":
        return {"java_candidates": [candidate.to_dict() for candidate in detect_java_installations()]}
    if command == "inspect_server_directory":
        return inspect_server_directory(str(payload.get("root_path") or ""))
    if command == "start_server":
        return await runtime_manager.start(payload)
    if command == "stop_server":
        return await runtime_manager.stop(payload)
    if command == "restart_server":
        return await runtime_manager.restart(payload)
    if command == "get_server_runtime":
        return runtime_manager.runtime(str(payload["server_id"]))
    if command == "get_console_tail":
        return runtime_manager.console_tail(
            str(payload["server_id"]),
            int(payload.get("lines") or 120),
        )
    if command == "send_console_command":
        return await runtime_manager.send_command(
            str(payload["server_id"]),
            str(payload.get("command") or ""),
        )
    if command == "list_plugins":
        return list_plugins(payload)
    if command == "inspect_plugin":
        return list_plugins(payload)
    if command == "prepare_plugin_upload":
        return {"status": "ready"}
    if command == "install_uploaded_plugin":
        return operation_registry.start(
            "plugin upload",
            lambda: install_uploaded_plugin(payload),
        )
    if command == "enable_plugin":
        return enable_plugin(payload)
    if command == "disable_plugin":
        return disable_plugin(payload)
    if command == "list_editable_files":
        return list_editable_files(payload)
    if command == "read_editable_file":
        return read_editable_file(payload)
    if command == "write_editable_file":
        return write_editable_file(payload)
    if command == "create_manual_backup":
        return operation_registry.start(
            "backup create",
            lambda: asyncio.to_thread(create_manual_backup, payload),
        )
    if command == "list_backups":
        return list_backups(payload)
    if command == "restore_backup":
        runtime = runtime_manager.runtime(str(payload["server_id"]))
        if runtime["status"] not in {"stopped", "error"}:
            raise RuntimeError("Server must be stopped before restore.")
        return operation_registry.start(
            "backup restore",
            lambda: asyncio.to_thread(restore_backup, payload),
        )
    if command == "delete_backup":
        return operation_registry.start(
            "backup delete",
            lambda: asyncio.to_thread(delete_backup, payload),
        )
    if command == "run_scheduled_backup":
        return operation_registry.start(
            "scheduled backup",
            lambda: asyncio.to_thread(create_manual_backup, payload, kind="scheduled"),
        )
    if command == "get_backup_schedule_state":
        return {"status": "ok", "enabled": bool(payload.get("enabled", False)), "next_run_at": payload.get("next_run_at")}
    if command == "update_runtime_automation_settings":
        return runtime_manager.update_automation_settings(payload)
    if command == "get_online_players":
        return await runtime_manager.get_online_players(str(payload["server_id"]))
    if command == "kick_player":
        return await runtime_manager.player_action(
            str(payload["server_id"]),
            "kick",
            str(payload.get("player_name") or ""),
            str(payload.get("reason") or ""),
        )
    if command == "ban_player":
        return await runtime_manager.player_action(
            str(payload["server_id"]),
            "ban",
            str(payload.get("player_name") or ""),
            str(payload.get("reason") or ""),
        )
    if command == "pardon_player":
        return await runtime_manager.player_action(
            str(payload["server_id"]),
            "pardon",
            str(payload.get("player_name") or ""),
        )
    if command == "get_operation_status":
        return operation_registry.get(str(payload["operation_id"]))
    raise RuntimeError(f"Unknown command: {command}")
