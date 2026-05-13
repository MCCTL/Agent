# MCCTL Agent

MCCTL Agent is the local CLI process that runs beside a Minecraft server and connects outward to the MCCTL API.

## Package

- package name: `mcctl-agent`
- Python module: `mcctl_agent`
- console script: `mcctl-agent = "mcctl_agent.main:main"`
- supported Python: 3.11+

## Windows

Requirements: Windows 10 / 11, Python 3.11+, PowerShell.

```powershell
py -m pip install --user pipx
py -m pipx ensurepath
pipx install git+https://github.com/MCCTL/Agent.git
mcctl-agent
```

If `mcctl-agent` is not found after installation, open a new PowerShell window or run:

```powershell
& "$env:USERPROFILE\.local\bin\mcctl-agent.exe"
```

## Linux

Ubuntu / Debian:

```bash
sudo apt update
sudo apt install -y python3 python3-venv pipx
pipx ensurepath
pipx install git+https://github.com/MCCTL/Agent.git
mcctl-agent
```

## Connection Target

Normally, the agent connects to `https://api.mcctl.com` automatically.

Developers can point it at a local API with `MCCTL_API_BASE_URL`.

PowerShell:

```powershell
$env:MCCTL_API_BASE_URL="http://127.0.0.1:8000"
mcctl-agent
```

Linux:

```bash
MCCTL_API_BASE_URL=http://127.0.0.1:8000 mcctl-agent
```

## Pairing

When the agent starts without a saved token, it prints a pairing URL and code.

1. Open the pairing URL in a browser.
2. Sign in to MCCTL.
3. Choose a workspace.
4. Confirm the device.
5. Check that the device appears in Devices.

The pairing token expires after 10 minutes and can only be used once. After pairing, the agent stores its agent token locally and uses it for future WebSocket connections. Treat the agent token as a secret.

## Local Development

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e .[dev]
pytest
```

On Linux:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
pip install -e '.[dev]'
pytest
```
