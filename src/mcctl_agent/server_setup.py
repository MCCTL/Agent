from __future__ import annotations

import hashlib
import os
import platform
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from mcctl_agent.config import default_allowed_roots
from mcctl_agent.java import detect_java_installations, major_from_version, parse_java_version
from mcctl_agent.minecraft import inspect_server_directory

USER_AGENT = "MCCTL-Agent/0.1 (https://mcctl.com/contact)"
MOJANG_MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
PAPER_PROJECT_URL = "https://fill.papermc.io/v3/projects/paper"

DANGEROUS_POSIX_PATHS = {
    Path("/"),
    Path("/etc"),
    Path("/bin"),
    Path("/sbin"),
    Path("/usr"),
    Path("/boot"),
    Path("/dev"),
    Path("/proc"),
    Path("/sys"),
    Path("/var/lib/postgresql"),
}

DEFAULT_PROPERTIES = {
    "server-port": "25565",
    "max-players": "20",
    "online-mode": "true",
    "difficulty": "normal",
    "gamemode": "survival",
    "motd": "A MCCTL Minecraft Server",
    "view-distance": "10",
    "simulation-distance": "10",
    "pvp": "true",
    "enable-command-block": "false",
}


@dataclass(frozen=True)
class SetupError(RuntimeError):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


def get_agent_capabilities(allowed_roots: list[str] | None = None) -> dict[str, Any]:
    return {
        "commands": [
            "detect_java",
            "list_directories",
            "create_directory",
            "validate_server_directory",
            "list_minecraft_versions",
            "list_server_builds",
            "check_port",
            "create_minecraft_server",
        ],
        "server_types": ["vanilla", "paper"],
        "allowed_roots": normalize_allowed_roots(allowed_roots),
        "platform": platform.system().lower(),
    }


def detect_java(required_major: int | None = None, manual_path: str | None = None) -> dict[str, Any]:
    candidates = detect_java_installations()
    if manual_path:
        manual = _manual_java_installation(manual_path)
        if manual:
            candidates.insert(0, manual)

    best = candidates[0] if candidates else None
    status = "missing"
    if best:
        if required_major and (best.get("major_version") or 0) < required_major:
            status = "insufficient"
        else:
            status = "ok"
    return {
        "status": status,
        "required_major": required_major,
        "selected": best,
        "java_candidates": candidates,
        "install_guidance": java_install_guidance(),
    }


def list_directories(payload: dict[str, Any], allowed_roots: list[str] | None = None) -> dict[str, Any]:
    roots = normalize_allowed_roots(allowed_roots)
    requested = str(payload.get("path") or roots[0])
    target = _expand_path(requested)
    _assert_not_dangerous(target)
    allowed, allowed_root = _is_under_allowed_root(target, roots, allow_nonexistent=False)
    if not allowed:
        return {
            "path": str(target),
            "parent_path": str(target.parent),
            "directories": [],
            "readable": False,
            "writable": False,
            "permission_error": "この場所はAgentの許可範囲外です。別のフォルダを選ぶか、Agent設定を変更してください。",
            "home_shortcut": str(Path.home()),
            "allowed_roots": roots,
        }
    try:
        resolved = target.resolve(strict=True)
    except OSError as exc:
        return {
            "path": str(target),
            "parent_path": str(target.parent),
            "directories": [],
            "readable": False,
            "writable": False,
            "permission_error": f"ディレクトリを開けません: {exc}",
            "home_shortcut": str(Path.home()),
            "allowed_roots": roots,
        }
    if not resolved.is_dir():
        raise SetupError("not_directory", "指定されたパスはディレクトリではありません。")

    directories: list[dict[str, Any]] = []
    permission_error = None
    try:
        for child in sorted(resolved.iterdir(), key=lambda item: item.name.lower()):
            if not child.is_dir() or child.is_symlink():
                continue
            child_allowed, _ = _is_under_allowed_root(child, roots, allow_nonexistent=False)
            directories.append(
                {
                    "name": child.name,
                    "path": str(child),
                    "readable": os.access(child, os.R_OK),
                    "writable": os.access(child, os.W_OK),
                    "allowed": child_allowed,
                }
            )
    except OSError as exc:
        permission_error = f"ディレクトリ一覧を取得できません: {exc}"

    return {
        "path": str(resolved),
        "parent_path": str(resolved.parent if resolved != allowed_root else resolved),
        "directories": directories,
        "readable": os.access(resolved, os.R_OK),
        "writable": os.access(resolved, os.W_OK),
        "permission_error": permission_error,
        "home_shortcut": str(Path.home()),
        "allowed_roots": roots,
    }


def create_directory(payload: dict[str, Any], allowed_roots: list[str] | None = None) -> dict[str, Any]:
    roots = normalize_allowed_roots(allowed_roots)
    path = _expand_path(str(payload.get("path") or ""))
    _assert_not_dangerous(path)
    allowed, _ = _is_under_allowed_root(path, roots, allow_nonexistent=True)
    if not allowed:
        raise SetupError("outside_allowed_roots", "この場所はAgentの許可範囲外です。別のフォルダを選ぶか、Agent設定を変更してください。")
    try:
        path.mkdir(parents=True, exist_ok=True)
    except PermissionError as exc:
        raise SetupError("permission_denied", "フォルダを作成する権限がありません。Agentを実行しているユーザーの権限を確認してください。") from exc
    except OSError as exc:
        raise SetupError("directory_create_failed", f"フォルダを作成できません: {exc}") from exc
    return {"path": str(path.resolve(strict=True)), "created": True, "writable": os.access(path, os.W_OK)}


def validate_server_directory(payload: dict[str, Any], allowed_roots: list[str] | None = None) -> dict[str, Any]:
    roots = normalize_allowed_roots(allowed_roots)
    path = _expand_path(str(payload.get("root_path") or payload.get("path") or ""))
    allowed, _ = _is_under_allowed_root(path, roots, allow_nonexistent=True)
    if not allowed:
        raise SetupError("outside_allowed_roots", "この場所はAgentの許可範囲外です。")
    if not path.exists():
        return {
            "root_path": str(path),
            "exists": False,
            "is_directory": False,
            "readable": False,
            "writable": False,
            "warnings": ["ディレクトリはまだ存在しません。セットアップ時に作成できます。"],
        }
    return inspect_server_directory(str(path))


def check_port(payload: dict[str, Any]) -> dict[str, Any]:
    port = int(payload.get("port") or 25565)
    if port < 1 or port > 65535:
        raise SetupError("invalid_port", "ポート番号は1から65535の範囲で指定してください。")
    host = str(payload.get("host") or "0.0.0.0")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError as exc:
            return {"port": port, "available": False, "message": f"ポート{port}はすでに使用されています。別のポートを指定してください。", "error": str(exc)}
    return {"port": port, "available": True, "message": f"ポート{port}は利用できます。"}


def list_minecraft_versions(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    server_type = str((payload or {}).get("server_type") or "vanilla").lower()
    with httpx.Client(timeout=20.0, headers={"User-Agent": USER_AGENT}) as client:
        if server_type == "paper":
            response = client.get(PAPER_PROJECT_URL)
            response.raise_for_status()
            data = response.json()
            versions = [version for values in data.get("versions", {}).values() for version in values]
            return {"server_type": "paper", "versions": versions[:80], "latest": versions[0] if versions else None}
        response = client.get(MOJANG_MANIFEST_URL)
        response.raise_for_status()
        data = response.json()
        versions = [item["id"] for item in data.get("versions", []) if item.get("type") == "release"]
        return {"server_type": "vanilla", "versions": versions[:80], "latest": data.get("latest", {}).get("release")}


def list_server_builds(payload: dict[str, Any]) -> dict[str, Any]:
    server_type = str(payload.get("server_type") or "vanilla").lower()
    version = str(payload.get("minecraft_version") or "")
    if server_type != "paper":
        return {"server_type": server_type, "minecraft_version": version, "builds": ["latest"], "latest": "latest"}
    with httpx.Client(timeout=20.0, headers={"User-Agent": USER_AGENT}) as client:
        response = client.get(f"{PAPER_PROJECT_URL}/versions/{version}/builds")
        response.raise_for_status()
        builds = response.json()
    stable = [item for item in builds if item.get("channel") == "STABLE"]
    build_ids = [str(item.get("id") or item.get("number")) for item in stable]
    return {"server_type": "paper", "minecraft_version": version, "builds": build_ids, "latest": build_ids[0] if build_ids else None}


def create_minecraft_server(payload: dict[str, Any], allowed_roots: list[str] | None = None) -> dict[str, Any]:
    server_type = str(payload.get("server_type") or "vanilla").lower()
    if server_type not in {"vanilla", "paper"}:
        raise SetupError("unsupported_server_type", "このサーバー種類はまだ対応していません。Vanilla または Paper を選択してください。")
    minecraft_version = str(payload.get("minecraft_version") or "").strip()
    if not minecraft_version:
        raise SetupError("missing_version", "Minecraftバージョンを選択してください。")
    if not bool(payload.get("eula_accepted")):
        raise SetupError("eula_not_accepted", "Minecraft EULAへの同意が必要です。")

    root = create_directory({"path": str(payload.get("root_path") or "")}, allowed_roots)["path"]
    root_path = Path(root)
    required_major = required_java_major(minecraft_version)
    java_status = detect_java(required_major, str(payload.get("java_path") or "") or None)
    if java_status["status"] == "missing":
        raise SetupError("java_missing", "Javaが見つかりません。Minecraftサーバーを起動するにはJavaが必要です。")
    if java_status["status"] == "insufficient":
        raise SetupError("java_insufficient", f"Javaのバージョンが不足しています。Minecraft {minecraft_version} ではJava {required_major}以上が必要です。")
    selected_java = _select_java(java_status["java_candidates"], str(payload.get("java_path") or ""))

    properties = normalize_properties(payload.get("properties") if isinstance(payload.get("properties"), dict) else {})
    port_status = check_port({"port": properties["server-port"]})
    if not port_status["available"]:
        raise SetupError("port_in_use", str(port_status["message"]))

    jar_path = root_path / ("paper.jar" if server_type == "paper" else "server.jar")
    _download_server_jar(server_type, minecraft_version, str(payload.get("paper_build") or "latest"), jar_path)
    _write_eula(root_path)
    _write_server_properties(root_path, properties)
    (root_path / "plugins").mkdir(exist_ok=True)

    return {
        "status": "ready",
        "root_path": str(root_path),
        "jar_path": str(jar_path),
        "java_path": selected_java.get("executable_path"),
        "properties": properties,
        "inspection": inspect_server_directory(str(root_path)),
        "message": "Minecraftサーバーを作成しました。MCCTLに登録して起動できます。",
    }


def required_java_major(minecraft_version: str) -> int:
    parts = _version_parts(minecraft_version)
    if parts >= (1, 20, 5):
        return 21
    if parts >= (1, 18, 0):
        return 17
    if parts >= (1, 17, 0):
        return 16
    return 8


def normalize_allowed_roots(roots: list[str] | None = None) -> list[str]:
    values = roots or default_allowed_roots()
    normalized: list[str] = []
    seen: set[str] = set()
    system = platform.system().lower()
    for value in values:
        if not value:
            continue
        path = _expand_path(value)
        key = str(path).lower() if system == "windows" else str(path)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(str(path))
    return normalized


def normalize_properties(values: dict[str, Any]) -> dict[str, str]:
    properties = dict(DEFAULT_PROPERTIES)
    for key in DEFAULT_PROPERTIES:
        if key in values and values[key] is not None:
            properties[key] = str(values[key]).strip()
    try:
        port = int(properties["server-port"])
    except ValueError as exc:
        raise SetupError("invalid_properties", "server-port は数値で指定してください。") from exc
    if port < 1 or port > 65535:
        raise SetupError("invalid_properties", "server-port は1から65535の範囲で指定してください。")
    try:
        max_players = int(properties["max-players"])
    except ValueError as exc:
        raise SetupError("invalid_properties", "max-players は数値で指定してください。") from exc
    if max_players < 1:
        raise SetupError("invalid_properties", "max-players は1以上で指定してください。")
    return properties


def java_install_guidance() -> dict[str, str]:
    return {
        "windows": "winget install EclipseAdoptium.Temurin.21.JRE",
        "linux": "sudo apt install openjdk-21-jre-headless",
    }


def _download_server_jar(server_type: str, minecraft_version: str, build: str, target: Path) -> None:
    with httpx.Client(timeout=120.0, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
        if server_type == "paper":
            url, checksum = _paper_download_url(client, minecraft_version, build)
        else:
            url, checksum = _vanilla_download_url(client, minecraft_version)
        temp = target.with_suffix(target.suffix + ".mcctl-download")
        try:
            with client.stream("GET", url) as response:
                response.raise_for_status()
                hasher = hashlib.sha1()
                with temp.open("wb") as handle:
                    for chunk in response.iter_bytes():
                        if not chunk:
                            continue
                        hasher.update(chunk)
                        handle.write(chunk)
            if checksum and len(checksum) == 40 and hasher.hexdigest().lower() != checksum.lower():
                raise SetupError("jar_checksum_mismatch", "server.jar の検証に失敗しました。再試行してください。")
            os.replace(temp, target)
        except SetupError:
            temp.unlink(missing_ok=True)
            raise
        except Exception as exc:
            temp.unlink(missing_ok=True)
            raise SetupError("jar_download_failed", f"server.jar のダウンロードに失敗しました: {exc}") from exc


def _vanilla_download_url(client: httpx.Client, minecraft_version: str) -> tuple[str, str | None]:
    manifest = client.get(MOJANG_MANIFEST_URL)
    manifest.raise_for_status()
    item = next((entry for entry in manifest.json().get("versions", []) if entry.get("id") == minecraft_version), None)
    if item is None:
        raise SetupError("version_not_found", "指定されたMinecraftバージョンが見つかりません。")
    metadata = client.get(item["url"])
    metadata.raise_for_status()
    server = metadata.json().get("downloads", {}).get("server")
    if not server:
        raise SetupError("jar_download_unavailable", "このバージョンのVanilla server.jarを取得できません。")
    return str(server["url"]), server.get("sha1")


def _paper_download_url(client: httpx.Client, minecraft_version: str, build: str) -> tuple[str, str | None]:
    builds_response = client.get(f"{PAPER_PROJECT_URL}/versions/{minecraft_version}/builds")
    builds_response.raise_for_status()
    builds = builds_response.json()
    candidates = [item for item in builds if item.get("channel") == "STABLE"]
    if build != "latest":
        candidates = [item for item in candidates if str(item.get("id") or item.get("number")) == build]
    if not candidates:
        raise SetupError("paper_build_not_found", "指定されたPaperビルドが見つかりません。")
    download = candidates[0].get("downloads", {}).get("server:default") or candidates[0].get("download")
    if not download or not download.get("url"):
        raise SetupError("jar_download_unavailable", "Paper server.jarのダウンロードURLを取得できません。")
    checksums = download.get("checksums") or {}
    return str(download["url"]), checksums.get("sha1")


def _write_eula(root: Path) -> None:
    (root / "eula.txt").write_text("eula=true\n", encoding="utf-8")


def _write_server_properties(root: Path, properties: dict[str, str]) -> None:
    lines = ["#Minecraft server properties", "#Generated by MCCTL"]
    lines.extend(f"{key}={value}" for key, value in properties.items())
    (root / "server.properties").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _manual_java_installation(value: str) -> dict[str, Any] | None:
    path = Path(value).expanduser()
    if not path.exists():
        return None
    try:
        result = subprocess.run([str(path), "-version"], capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    version_string, major_version, vendor = parse_java_version((result.stderr or "") + "\n" + (result.stdout or ""))
    return {
        "executable_path": str(path.resolve(strict=True)),
        "version_string": version_string,
        "major_version": major_version,
        "vendor": vendor,
        "source": "manual",
    }


def _select_java(candidates: list[dict[str, Any]], preferred: str) -> dict[str, Any]:
    if preferred:
        preferred_path = str(Path(preferred).expanduser())
        for candidate in candidates:
            if candidate.get("executable_path") == preferred_path:
                return candidate
        return {"executable_path": preferred_path, "version_string": None, "major_version": None, "vendor": None, "source": "manual"}
    if candidates:
        return candidates[0]
    raise SetupError("java_missing", "Javaが見つかりません。")


def _expand_path(value: str) -> Path:
    if not value.strip():
        raise SetupError("missing_path", "パスを入力してください。")
    return Path(value).expanduser()


def _assert_not_dangerous(path: Path) -> None:
    if platform.system().lower() == "windows":
        anchor = path.anchor.rstrip("\\/")
        normalized = str(path).rstrip("\\/")
        if normalized in {anchor, f"{anchor}\\Windows", f"{anchor}\\Program Files"}:
            raise SetupError("dangerous_path", "システム上重要な場所は選択できません。")
        return
    try:
        resolved = path.resolve(strict=False)
    except OSError:
        resolved = path
    if resolved in DANGEROUS_POSIX_PATHS:
        raise SetupError("dangerous_path", "システム上重要な場所は選択できません。")


def _is_under_allowed_root(path: Path, roots: list[str], *, allow_nonexistent: bool) -> tuple[bool, Path | None]:
    try:
        resolved = _resolve_candidate(path, allow_nonexistent=allow_nonexistent)
    except OSError:
        return False, None
    for value in roots:
        root = Path(value).expanduser()
        try:
            root_resolved = root.resolve(strict=root.exists())
            resolved.relative_to(root_resolved)
            return True, root_resolved
        except (OSError, ValueError):
            continue
    return False, None


def _resolve_candidate(path: Path, *, allow_nonexistent: bool) -> Path:
    if path.exists():
        return path.resolve(strict=True)
    if not allow_nonexistent:
        raise FileNotFoundError(str(path))
    missing_parts: list[str] = []
    current = path
    while not current.exists() and current != current.parent:
        missing_parts.append(current.name)
        current = current.parent
    base = current.resolve(strict=True) if current.exists() else current
    for name in reversed(missing_parts):
        base = base / name
    return base


def _version_parts(value: str) -> tuple[int, int, int]:
    parts = value.split(".")[:3]
    numbers: list[int] = []
    for index, part in enumerate(parts):
        if index == 0 and part.isdigit():
            numbers.append(int(part))
            continue
        try:
            major = major_from_version(value)
        except ValueError:
            major = None
        if major and index == 0:
            numbers.append(major)
        else:
            digits = "".join(ch for ch in part if ch.isdigit())
            numbers.append(int(digits or "0"))
    while len(numbers) < 3:
        numbers.append(0)
    return tuple(numbers[:3])  # type: ignore[return-value]
