from pathlib import Path

from mcctl_agent.config import DEFAULT_API_BASE_URL, AgentConfig, resolve_api_base_url
from mcctl_agent.main import (
    agent_metadata_headers,
    print_agent_status,
    print_agent_version,
    print_update_guidance,
    reset_agent_config,
    update_guidance,
    warn_for_insecure_api,
)


def test_default_api_base_url_points_to_production(monkeypatch):
    monkeypatch.delenv("MCCTL_API_BASE_URL", raising=False)

    assert DEFAULT_API_BASE_URL == "https://api.mcctl.com"
    assert AgentConfig().api_base_url == "https://api.mcctl.com"
    assert resolve_api_base_url() == "https://api.mcctl.com"


def test_api_base_url_can_be_overridden_for_development(monkeypatch):
    monkeypatch.setenv("MCCTL_API_BASE_URL", "http://127.0.0.1:8000")

    assert resolve_api_base_url() == "http://127.0.0.1:8000"


def test_reset_agent_config_clears_saved_token_and_device(tmp_path):
    config_path = tmp_path / "agent.json"
    AgentConfig(
        api_base_url="https://api.mcctl.com",
        agent_fingerprint="fingerprint",
        device_id="device-1",
        agent_token="secret-token",
    ).save(config_path)

    assert reset_agent_config(config_path) is True

    config = AgentConfig.load(config_path)
    assert config.agent_fingerprint == "fingerprint"
    assert config.agent_token is None
    assert config.device_id is None


def test_reset_agent_config_handles_missing_file(tmp_path):
    assert reset_agent_config(tmp_path / "missing.json") is False


def test_status_hides_saved_token(tmp_path, capsys):
    config_path = tmp_path / "agent.json"
    AgentConfig(
        api_base_url="https://api.mcctl.com",
        agent_fingerprint="fingerprint",
        device_id="device-1",
        agent_token="secret-token",
    ).save(config_path)

    print_agent_status(config_path, "https://api.mcctl.com")

    output = capsys.readouterr().out
    assert "device-1" in output
    assert "Token saved: yes" in output
    assert "secret-token" not in output


def test_version_and_update_commands_do_not_print_tokens(capsys):
    print_agent_version()
    print_update_guidance()

    output = capsys.readouterr().out
    assert "MCCTL Agent" in output
    assert "pipx install git+https://github.com/MCCTL/Agent.git" in output
    assert "token" not in output.lower()


def test_update_guidance_has_platform_specific_commands():
    assert "py -m pipx install" in update_guidance("Windows")
    assert "service install" in update_guidance("Windows")
    assert "~/.local/bin/mcctl-agent" in update_guidance("Linux")


def test_config_path_can_be_overridden_for_service(monkeypatch, tmp_path):
    config_path = tmp_path / "service-agent.json"
    monkeypatch.setenv("MCCTL_AGENT_CONFIG", str(config_path))

    from mcctl_agent.config import default_config_path

    assert default_config_path() == config_path


def test_agent_metadata_headers_include_runtime_without_token(monkeypatch):
    monkeypatch.setenv("MCCTL_AGENT_INSTALL_METHOD", "pipx-test")

    headers = agent_metadata_headers()

    assert headers["X-MCCTL-Agent-Version"]
    assert headers["X-MCCTL-Agent-Platform"]
    assert headers["X-MCCTL-Agent-Python-Version"]
    assert headers["X-MCCTL-Agent-Install-Method"] == "pipx-test"
    assert all("token" not in value.lower() for value in headers.values())


def test_insecure_api_warning_is_only_for_non_https(capsys):
    warn_for_insecure_api("https://api.mcctl.com")
    assert capsys.readouterr().err == ""

    warn_for_insecure_api("http://127.0.0.1:8000")
    assert "not HTTPS" in capsys.readouterr().err


def test_readme_keeps_localhost_out_of_normal_install_steps():
    readme = (Path(__file__).parents[1] / "README.md").read_text(encoding="utf-8")
    public_section = readme.split("## Developer API Override", 1)[0]

    assert "127.0.0.1" not in public_section
    assert "localhost" not in public_section.lower()
