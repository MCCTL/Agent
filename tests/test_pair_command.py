from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from mcctl_agent.config import AgentConfig
from mcctl_agent.main import pair_agent


def test_pair_agent_saves_claimed_device_and_token(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "agent.json"
    config = AgentConfig(api_base_url="https://api.mcctl.com", agent_fingerprint="fingerprint")

    async def fake_create_pairing_session(api_base_url: str, fingerprint: str):
        assert api_base_url == "https://api.mcctl.com"
        assert fingerprint == "fingerprint"
        return SimpleNamespace(
            pairing_url="https://mcctl.com/pair/example",
            public_code="ABCD-EFGH",
            token="pair-token",
            waiting_token="waiting-token",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        )

    async def fake_claim_pairing_session(
        api_base_url: str, token: str, waiting_token: str
    ) -> tuple[str, str]:
        assert api_base_url == "https://api.mcctl.com"
        assert token == "pair-token"
        assert waiting_token == "waiting-token"
        return "device-id", "secret-agent-token"

    monkeypatch.setattr("mcctl_agent.main.create_pairing_session", fake_create_pairing_session)
    monkeypatch.setattr("mcctl_agent.main.claim_pairing_session", fake_claim_pairing_session)
    monkeypatch.setattr("mcctl_agent.main.maybe_open_browser", lambda _url: None)

    asyncio.run(pair_agent(config, config_path))

    saved = AgentConfig.load(config_path)
    assert saved.device_id == "device-id"
    assert saved.agent_token == "secret-agent-token"
    assert config_path.exists()
