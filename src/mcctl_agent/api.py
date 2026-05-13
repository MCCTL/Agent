from __future__ import annotations

import platform
import socket
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit

import httpx


AGENT_VERSION = "0.1.0"


@dataclass(frozen=True)
class PairingSession:
    public_code: str
    token: str
    waiting_token: str
    pairing_url: str
    expires_at: datetime


async def create_pairing_session(api_base_url: str, agent_fingerprint: str) -> PairingSession:
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            f"{api_base_url.rstrip('/')}/agent/pairing-sessions",
            json={
                "device_name": socket.gethostname(),
                "os_name": platform.system(),
                "hostname": socket.gethostname(),
                "agent_version": AGENT_VERSION,
                "agent_fingerprint": agent_fingerprint,
            },
        )
    response.raise_for_status()
    data = response.json()
    return PairingSession(
        public_code=data["public_code"],
        token=data["token"],
        waiting_token=data["waiting_token"],
        pairing_url=data["pairing_url"],
        expires_at=datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00")),
    )


async def claim_pairing_session(
    api_base_url: str, pairing_token: str, waiting_token: str
) -> tuple[str, str] | None:
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            f"{api_base_url.rstrip('/')}/agent/pairing-sessions/{pairing_token}/claim",
            headers={"X-MCCTL-Waiting-Token": waiting_token},
        )
    response.raise_for_status()
    data = response.json()
    if data["status"] != "paired":
        return None
    return data["device_id"], data["agent_token"]


def websocket_url(api_base_url: str) -> str:
    parsed = urlsplit(api_base_url.rstrip("/"))
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunsplit((scheme, parsed.netloc, "/agent/ws", "", ""))
