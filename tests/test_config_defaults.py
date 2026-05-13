from mcctl_agent.config import DEFAULT_API_BASE_URL, AgentConfig, resolve_api_base_url


def test_default_api_base_url_points_to_production(monkeypatch):
    monkeypatch.delenv("MCCTL_API_BASE_URL", raising=False)

    assert DEFAULT_API_BASE_URL == "https://api.mcctl.com"
    assert AgentConfig().api_base_url == "https://api.mcctl.com"
    assert resolve_api_base_url() == "https://api.mcctl.com"


def test_api_base_url_can_be_overridden_for_development(monkeypatch):
    monkeypatch.setenv("MCCTL_API_BASE_URL", "http://127.0.0.1:8000")

    assert resolve_api_base_url() == "http://127.0.0.1:8000"
