from __future__ import annotations

import os
import re
import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import httpx

from mcctl_agent.config import default_config_path

ALLOWED_CONFIG_EXTENSIONS = {".yml", ".yaml", ".json", ".toml", ".properties", ".txt", ".conf"}
ROOT_EDITABLE_FILES = {
    "server.properties",
    "bukkit.yml",
    "spigot.yml",
    "commands.yml",
    "help.yml",
    "permissions.yml",
}
EDITABLE_DIRS = {"config", "plugins"}
MAX_TEXT_FILE_BYTES = 512 * 1024
MAX_PLUGIN_BYTES = 67_108_864
BACKUP_EXCLUDED_ROOTS = {"backups", "logs", "cache"}
BACKUP_EXCLUDED_SUFFIXES = {".tmp", ".part", ".mcctl-upload"}


@dataclass(frozen=True)
class SafeTarget:
    root: Path
    relative_path: str
    path: Path


def list_plugins(payload: dict[str, Any]) -> dict[str, Any]:
    root = _safe_root(payload["root_path"])
    plugins_dir = _safe_child(root, "plugins", must_exist=False)
    plugins_dir.mkdir(parents=True, exist_ok=True)
    plugins = []
    for path in sorted([*plugins_dir.glob("*.jar"), *plugins_dir.glob("*.jar.disabled")], key=lambda item: item.name.lower()):
        if not _is_inside(root, path.resolve(strict=True)):
            continue
        plugins.append(_plugin_info(path))
    return {"plugins": plugins}


async def install_uploaded_plugin(payload: dict[str, Any]) -> dict[str, Any]:
    root = _safe_root(payload["root_path"])
    plugins_dir = _safe_child(root, "plugins", must_exist=False)
    plugins_dir.mkdir(parents=True, exist_ok=True)
    filename = _sanitize_plugin_filename(str(payload.get("filename") or "plugin.jar"))
    if not filename.lower().endswith(".jar"):
        raise RuntimeError("Only .jar uploads are allowed.")
    target_plugin_id = str(payload.get("target_plugin_id") or "").strip()
    target_name = _sanitize_plugin_filename(target_plugin_id) if target_plugin_id else filename
    if not target_name.lower().endswith((".jar", ".jar.disabled")):
        raise RuntimeError("Target plugin must be a jar file.")
    target_path = _safe_child(root, f"plugins/{target_name}", must_exist=False)
    temp_path = _safe_child(root, f"plugins/.{filename}.{datetime.now(timezone.utc).timestamp()}.mcctl-upload", must_exist=False)

    download_url = str(payload["download_url"])
    size = 0
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
            async with client.stream("GET", download_url) as response:
                response.raise_for_status()
                with temp_path.open("wb") as handle:
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > MAX_PLUGIN_BYTES:
                            raise RuntimeError("Plugin upload is too large.")
                        handle.write(chunk)
        if size <= 0:
            raise RuntimeError("Plugin upload is empty.")
        if zipfile.is_zipfile(temp_path) is False:
            raise RuntimeError("Uploaded file is not a valid jar archive.")
        if target_path.exists():
            _backup_existing_plugin(root, target_path)
        os.replace(temp_path, target_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()

    return {
        "plugin_id": target_path.name,
        "filename": target_path.name,
        "status": "installed",
        "restart_required": True,
        "pending_state": "restart_required",
        "message": "Plugin installed. Restart the server to apply the change.",
    }


def enable_plugin(payload: dict[str, Any]) -> dict[str, Any]:
    root = _safe_root(payload["root_path"])
    plugin_id = _sanitize_plugin_filename(str(payload["plugin_id"]))
    source = _safe_child(root, f"plugins/{plugin_id}", must_exist=True)
    if not source.name.endswith(".jar.disabled"):
        return {
            "plugin_id": source.name,
            "filename": source.name,
            "status": "enabled",
            "restart_required": True,
            "pending_state": "restart_required",
            "message": "Plugin is already enabled.",
        }
    target = source.with_name(source.name.removesuffix(".disabled"))
    if target.exists():
        raise RuntimeError("Enabled plugin file already exists.")
    os.replace(source, target)
    return {
        "plugin_id": target.name,
        "filename": target.name,
        "status": "enabled",
        "restart_required": True,
        "pending_state": "restart_required",
        "message": "Plugin enabled. Restart the server to apply the change.",
    }


def disable_plugin(payload: dict[str, Any]) -> dict[str, Any]:
    root = _safe_root(payload["root_path"])
    plugin_id = _sanitize_plugin_filename(str(payload["plugin_id"]))
    source = _safe_child(root, f"plugins/{plugin_id}", must_exist=True)
    if source.name.endswith(".jar.disabled"):
        return {
            "plugin_id": source.name,
            "filename": source.name,
            "status": "disabled",
            "restart_required": True,
            "pending_state": "restart_required",
            "message": "Plugin is already disabled.",
        }
    if not source.name.endswith(".jar"):
        raise RuntimeError("Only jar plugins can be disabled.")
    target = source.with_name(f"{source.name}.disabled")
    if target.exists():
        raise RuntimeError("Disabled plugin file already exists.")
    os.replace(source, target)
    return {
        "plugin_id": target.name,
        "filename": target.name,
        "status": "disabled",
        "restart_required": True,
        "pending_state": "restart_required",
        "message": "Plugin disabled. Restart the server to apply the change.",
    }


def list_editable_files(payload: dict[str, Any]) -> dict[str, Any]:
    root = _safe_root(payload["root_path"])
    entries = []
    for name in sorted(ROOT_EDITABLE_FILES):
        path = root / name
        if path.exists() and _is_editable_file(root, path):
            entries.append(_file_entry(root, path))
    for dirname in sorted(EDITABLE_DIRS):
        directory = root / dirname
        if directory.exists() and directory.is_dir():
            entries.append(_directory_entry(root, directory))
    return {"files": entries}


def read_editable_file(payload: dict[str, Any]) -> dict[str, Any]:
    target = _resolve_editable_target(payload["root_path"], str(payload["path"]))
    if not target.path.exists() or not target.path.is_file():
        raise RuntimeError("File not found.")
    size = target.path.stat().st_size
    if size > MAX_TEXT_FILE_BYTES:
        raise RuntimeError("File is too large to edit.")
    data = target.path.read_bytes()
    if b"\x00" in data:
        raise RuntimeError("Binary files cannot be edited.")
    content = data.decode("utf-8")
    return {
        "path": target.relative_path,
        "content": content,
        "size_bytes": size,
        "updated_at": _format_timestamp(target.path.stat().st_mtime),
    }


def write_editable_file(payload: dict[str, Any]) -> dict[str, Any]:
    content = str(payload.get("content") or "")
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_TEXT_FILE_BYTES:
        raise RuntimeError("File is too large to edit.")
    target = _resolve_editable_target(payload["root_path"], str(payload["path"]))
    if not target.path.exists() or not target.path.is_file():
        raise RuntimeError("File not found.")
    backup_id = _backup_file_before_write(str(payload["server_id"]), target)
    target.path.write_bytes(encoded)
    return {
        "path": target.relative_path,
        "status": "saved",
        "backup_id": backup_id,
        "message": "Saved. Plugin reload or server restart may be required.",
    }


def create_manual_backup(payload: dict[str, Any], *, kind: str = "manual") -> dict[str, Any]:
    root = _safe_root(payload["root_path"])
    server_id = str(payload["server_id"])
    backup_dir = _backup_dir(server_id)
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = _timestamp()
    backup_id = f"{kind}-{_safe_id(server_id)}-{timestamp}"
    path = backup_dir / f"{backup_id}.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for item in root.rglob("*"):
            if _should_exclude_from_backup(root, item):
                continue
            if item.is_file():
                archive.write(item, item.relative_to(root).as_posix())
    if kind == "scheduled":
        _prune_scheduled_backups(backup_dir, int(payload.get("retention_count") or 7))
    return {"backup": _backup_info(path, kind=kind), "message": "Backup created."}


def list_backups(payload: dict[str, Any]) -> dict[str, Any]:
    server_id = str(payload["server_id"])
    backup_dir = _backup_dir(server_id)
    backup_dir.mkdir(parents=True, exist_ok=True)
    backups = [_backup_info(path, kind=_kind_from_backup_id(path.stem)) for path in sorted(backup_dir.glob("*.zip"), reverse=True)]
    return {"backups": backups}


def restore_backup(payload: dict[str, Any]) -> dict[str, Any]:
    root = _safe_root(payload["root_path"])
    backup_id = _safe_id(str(payload["backup_id"]))
    path = _backup_dir(str(payload["server_id"])) / f"{backup_id}.zip"
    if not path.exists():
        raise RuntimeError("Backup not found.")
    safety = create_manual_backup(payload, kind="pre-restore")["backup"]
    with zipfile.ZipFile(path) as archive:
        for member in archive.infolist():
            target = _safe_restore_target(root, member.filename)
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open("wb") as destination:
                shutil.copyfileobj(source, destination)
    return {
        "backup_id": backup_id,
        "status": "restored",
        "safety_backup_id": safety["backup_id"],
        "message": "Backup restored.",
    }


def delete_backup(payload: dict[str, Any]) -> dict[str, Any]:
    backup_id = _safe_id(str(payload["backup_id"]))
    path = _backup_dir(str(payload["server_id"])) / f"{backup_id}.zip"
    if not path.exists():
        raise RuntimeError("Backup not found.")
    path.unlink()
    return {"backup_id": backup_id, "status": "deleted", "safety_backup_id": None, "message": "Backup deleted."}


def _plugin_info(path: Path) -> dict[str, Any]:
    enabled = path.name.endswith(".jar")
    metadata, metadata_error = _read_plugin_metadata(path)
    display_name = metadata.get("name") or path.name.removesuffix(".disabled").removesuffix(".jar")
    return {
        "plugin_id": path.name,
        "display_name": display_name,
        "filename": path.name,
        "version": metadata.get("version"),
        "description": metadata.get("description"),
        "enabled": enabled,
        "updated_at": _format_timestamp(path.stat().st_mtime),
        "size_bytes": path.stat().st_size,
        "restart_required": False,
        "pending_state": "none",
        "metadata_error": metadata_error,
    }


def _read_plugin_metadata(path: Path) -> tuple[dict[str, str], str | None]:
    try:
        with zipfile.ZipFile(path) as archive:
            for metadata_file in ("plugin.yml", "paper-plugin.yml"):
                try:
                    raw = archive.read(metadata_file)
                except KeyError:
                    continue
                return _parse_simple_yaml(raw.decode("utf-8", errors="replace")), None
    except zipfile.BadZipFile:
        return {}, "Jar metadata could not be read."
    return {}, None


def _parse_simple_yaml(content: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in content.splitlines():
        if ":" not in line or line.startswith((" ", "\t", "#")):
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key in {"name", "version", "description"} and value:
            result[key] = value
    return result


def _directory_entry(root: Path, path: Path) -> dict[str, Any]:
    children = []
    for child in sorted(path.iterdir(), key=lambda item: (item.is_file(), item.name.lower())):
        if child.is_symlink():
            try:
                if not _is_inside(root, child.resolve(strict=True)):
                    continue
            except OSError:
                continue
        if child.is_dir():
            children.append(_directory_entry(root, child))
        elif _is_editable_file(root, child):
            children.append(_file_entry(root, child))
    return {
        "path": path.relative_to(root).as_posix(),
        "name": path.name,
        "kind": "directory",
        "size_bytes": None,
        "updated_at": _format_timestamp(path.stat().st_mtime),
        "editable": False,
        "children": children,
    }


def _file_entry(root: Path, path: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "name": path.name,
        "kind": "file",
        "size_bytes": path.stat().st_size,
        "updated_at": _format_timestamp(path.stat().st_mtime),
        "editable": True,
        "children": [],
    }


def _resolve_editable_target(root_path: str, relative_path: str) -> SafeTarget:
    root = _safe_root(root_path)
    normalized = _normalize_relative_path(relative_path)
    path = _safe_child(root, normalized, must_exist=True)
    if not _is_editable_file(root, path):
        raise RuntimeError("File is not editable.")
    return SafeTarget(root=root, relative_path=normalized, path=path)


def _is_editable_file(root: Path, path: Path) -> bool:
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return False
    if not _is_inside(root, resolved) or not path.is_file():
        return False
    relative = path.relative_to(root).as_posix()
    first = relative.split("/", 1)[0]
    if relative in ROOT_EDITABLE_FILES:
        return path.suffix.lower() in ALLOWED_CONFIG_EXTENSIONS and path.stat().st_size <= MAX_TEXT_FILE_BYTES
    return first in EDITABLE_DIRS and path.suffix.lower() in ALLOWED_CONFIG_EXTENSIONS and path.stat().st_size <= MAX_TEXT_FILE_BYTES


def _safe_root(root_path: str) -> Path:
    root = Path(root_path).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise RuntimeError("Server root is not a directory.")
    return root


def _safe_child(root: Path, relative_path: str, *, must_exist: bool) -> Path:
    normalized = _normalize_relative_path(relative_path)
    path = root / normalized
    try:
        resolved = path.resolve(strict=must_exist)
    except OSError as exc:
        raise RuntimeError("Path could not be resolved.") from exc
    if not _is_inside(root, resolved):
        raise RuntimeError("Path escapes the server root.")
    return resolved


def _safe_restore_target(root: Path, relative_path: str) -> Path:
    normalized = _normalize_relative_path(relative_path)
    path = root / normalized
    parent = path.parent.resolve(strict=True) if path.parent.exists() else path.parent.resolve(strict=False)
    if not _is_inside(root, parent):
        raise RuntimeError("Backup entry escapes the server root.")
    return path


def _normalize_relative_path(value: str) -> str:
    value = value.replace("\\", "/").strip("/")
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in {"..", ""} for part in pure.parts):
        raise RuntimeError("Invalid relative path.")
    return pure.as_posix()


def _is_inside(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _backup_file_before_write(server_id: str, target: SafeTarget) -> str:
    backup_dir = _backup_dir(server_id) / "file-edits"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_id = f"file-edit-{_timestamp()}-{_safe_id(target.relative_path.replace('/', '-'))}"
    shutil.copy2(target.path, backup_dir / f"{backup_id}.bak")
    return backup_id


def _backup_existing_plugin(root: Path, target: Path) -> str:
    server_id = _safe_id(root.name)
    backup_dir = _backup_dir(server_id) / "plugin-updates"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_id = f"plugin-update-{_timestamp()}-{_safe_id(target.name)}"
    shutil.copy2(target, backup_dir / f"{backup_id}.jar")
    return backup_id


def _backup_dir(server_id: str) -> Path:
    return _agent_data_dir() / "backups" / _safe_id(server_id)


def _agent_data_dir() -> Path:
    configured = os.environ.get("MCCTL_AGENT_DATA_DIR")
    if configured:
        return Path(configured).expanduser()
    return default_config_path().parent / "data"


def _should_exclude_from_backup(root: Path, path: Path) -> bool:
    if path.is_symlink():
        return True
    relative = path.relative_to(root)
    first = relative.parts[0] if relative.parts else ""
    if first in BACKUP_EXCLUDED_ROOTS:
        return True
    return path.suffix.lower() in BACKUP_EXCLUDED_SUFFIXES


def _backup_info(path: Path, *, kind: str) -> dict[str, Any]:
    return {
        "backup_id": path.stem,
        "filename": path.name,
        "kind": kind,
        "created_at": _format_timestamp(path.stat().st_mtime) or datetime.now(timezone.utc).isoformat(),
        "size_bytes": path.stat().st_size,
        "status": "available",
    }


def _kind_from_backup_id(backup_id: str) -> str:
    if backup_id.startswith("pre-restore-"):
        return "pre-restore"
    if backup_id.startswith("scheduled-"):
        return "scheduled"
    if backup_id.startswith("manual-"):
        return "manual"
    return "unknown"


def _prune_scheduled_backups(backup_dir: Path, retention_count: int) -> None:
    retention_count = max(1, retention_count)
    backups = sorted(
        backup_dir.glob("scheduled-*.zip"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for stale in backups[retention_count:]:
        stale.unlink(missing_ok=True)


def _sanitize_plugin_filename(filename: str) -> str:
    name = Path(filename).name
    clean = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip(".-_")
    return clean or "plugin.jar"


def _safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-_") or "item"


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _format_timestamp(timestamp: float) -> str | None:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
