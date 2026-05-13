from pathlib import Path

from mcctl_agent.config import DEFAULT_API_BASE_URL, AgentConfig, resolve_api_base_url
from mcctl_agent.main import reset_agent_config


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


def test_readme_keeps_localhost_out_of_normal_install_steps():
    readme = Path("README.md").read_text(encoding="utf-8")
    public_section = readme.split("## Developer API Override", 1)[0]

    assert "127.0.0.1" not in public_section
    assert "localhost" not in public_section.lower()
