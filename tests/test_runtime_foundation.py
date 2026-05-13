from pathlib import Path

from mcctl_agent.java import major_from_version, parse_java_version
from mcctl_agent.minecraft import find_jar_candidates, inspect_server_directory
from mcctl_agent.runtime import _eula_exists_but_not_accepted, build_start_command


def test_java_version_parser_handles_common_versions() -> None:
    cases = [
        ('java version "1.8.0_402"\nJava(TM) SE Runtime Environment', 8),
        ('openjdk version "11.0.22" 2024-01-16', 11),
        ('openjdk version "17.0.10" 2024-01-16', 17),
        ('openjdk version "21.0.2" 2024-01-16', 21),
    ]
    for output, major in cases:
        version_string, parsed_major, vendor = parse_java_version(output)
        assert version_string is not None
        assert parsed_major == major
        assert vendor is not None


def test_major_from_version_rejects_missing_value() -> None:
    assert major_from_version(None) is None
    assert major_from_version("not-a-version") is None


def test_jar_candidate_detection_deduplicates(tmp_path: Path) -> None:
    (tmp_path / "server.jar").write_bytes(b"jar")
    (tmp_path / "paper-1.21.jar").write_bytes(b"jar")

    candidates = find_jar_candidates(tmp_path)

    assert [candidate.name for candidate in candidates] == ["server.jar", "paper-1.21.jar"]


def test_server_directory_inspection(tmp_path: Path) -> None:
    (tmp_path / "server.jar").write_bytes(b"jar")
    (tmp_path / "server.properties").write_text("server-port=25565", encoding="utf-8")
    (tmp_path / "eula.txt").write_text("eula=true", encoding="utf-8")
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "latest.log").write_text("ready", encoding="utf-8")
    (tmp_path / "plugins").mkdir()

    result = inspect_server_directory(str(tmp_path))

    assert result["exists"] is True
    assert result["eula_accepted"] is True
    assert result["server_properties_exists"] is True
    assert result["latest_log_exists"] is True
    assert result["plugins_dir_exists"] is True
    assert result["jar_candidates"]


def test_start_command_uses_argument_array_without_shell() -> None:
    root_path = Path("/srv/minecraft")
    command = build_start_command(
        {
            "root_path": str(root_path),
            "java_path": "/usr/bin/java",
            "jar_path": "server.jar",
            "jvm_args": "-Xms1G -Xmx2G",
            "server_args": "nogui",
        }
    )

    assert command == ["/usr/bin/java", "-Xms1G", "-Xmx2G", "-jar", str(root_path / "server.jar"), "nogui"]


def test_eula_guard_detects_unaccepted_eula(tmp_path: Path) -> None:
    assert _eula_exists_but_not_accepted(tmp_path) is False
    (tmp_path / "eula.txt").write_text("eula=false", encoding="utf-8")
    assert _eula_exists_but_not_accepted(tmp_path) is True
    (tmp_path / "eula.txt").write_text("eula=true", encoding="utf-8")
    assert _eula_exists_but_not_accepted(tmp_path) is False
