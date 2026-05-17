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
py -m pipx install git+https://github.com/MCCTL/Agent.git
& "$env:USERPROFILE\.local\bin\mcctl-agent.exe"
```

`py -m pipx ensurepath` updates PATH for new PowerShell windows. The current PowerShell may not see `pipx` or `mcctl-agent` yet.

After opening a new PowerShell window, this should work:

```powershell
mcctl-agent
```

### Windows autostart

The beta agent can create a per-user Task Scheduler entry that starts the agent when you log in:

```powershell
mcctl-agent autostart install
mcctl-agent autostart status
```

Remove the task:

```powershell
mcctl-agent autostart uninstall
```

If `mcctl-agent` is not on PATH in the current PowerShell, use the absolute path:

```powershell
& "$env:USERPROFILE\.local\bin\mcctl-agent.exe" autostart install
```

## Linux

Ubuntu / Debian:

```bash
sudo apt update
sudo apt install -y python3 python3-venv pipx
pipx ensurepath
pipx install git+https://github.com/MCCTL/Agent.git
~/.local/bin/mcctl-agent
```

If `mcctl-agent: command not found` appears, run the agent with the absolute path:

```bash
~/.local/bin/mcctl-agent
```

To refresh PATH:

```bash
pipx ensurepath
source ~/.bashrc
```

If it is still missing, add the user bin directory manually:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

## Connection Target

Normally, the agent connects to `https://api.mcctl.com` automatically. Users do not need to configure the API URL.

Check the local configuration without printing secrets:

```bash
mcctl-agent status
```

## Pairing

When the agent starts without a saved token, it prints a pairing URL and code.

1. Open the pairing URL in a browser.
2. Sign in to MCCTL.
3. Choose a workspace.
4. Confirm the device.
5. Check that the device appears in Devices.

The pairing token expires after 10 minutes and can only be used once. If it expires, restart the agent to create a new pairing URL.

After pairing, the agent stores its agent token locally and uses it for future WebSocket connections. Treat the agent token as a secret.

## Beginner Server Setup Support

The MCCTL web app can ask the Agent to help create a new Vanilla or Paper server. These are fixed Agent commands, not arbitrary shell execution:

- `detect_java`
- `list_directories`
- `create_directory`
- `validate_server_directory`
- `list_minecraft_versions`
- `list_server_builds`
- `check_port`
- `create_minecraft_server`

The Agent checks Java, restricts file operations to configured `allowed_roots`, rejects dangerous system paths, writes `eula.txt` only after the user agrees to Minecraft EULA in the web UI, writes beginner `server.properties`, downloads Vanilla or Paper server jars, and reports Japanese cause-specific setup errors back to MCCTL.

Default allowed roots include the user home directory, `~/minecraft`, `/srv/minecraft`, `/opt/minecraft`, and common Windows Minecraft folders. You can add `allowed_roots` to the config file if your server directory should live elsewhere.

## Token Reset And Re-Pairing

If WebSocket connection is rejected with HTTP 401 or 403, the saved agent token may be invalid. Reset the saved token and pair again:

```bash
mcctl-agent reset
```

Default config paths:

- Windows: `%APPDATA%\mcctl\agent.json`
- Linux: `~/.config/mcctl/agent.json`

`mcctl-agent reset` clears only the saved token and device id. It keeps the local agent fingerprint and API URL.

Manual removal if needed:

```powershell
Remove-Item -Force "$env:APPDATA\mcctl\agent.json" -ErrorAction SilentlyContinue
```

```bash
rm -f ~/.config/mcctl/agent.json
```

## systemd

Run the agent as a non-root user that has read/write access to the Minecraft server directory:

```ini
[Unit]
Description=MCCTL Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=YOUR_USER
WorkingDirectory=/home/YOUR_USER
ExecStart=/home/YOUR_USER/.local/bin/mcctl-agent
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Replace `YOUR_USER` with the actual user name. Check logs with:

```bash
journalctl -u mcctl-agent -f
```

## Developer API Override

Developers can point the agent at a development API with `MCCTL_API_BASE_URL`.

PowerShell:

```powershell
$env:MCCTL_API_BASE_URL="http://127.0.0.1:8000"
mcctl-agent
```

Linux:

```bash
MCCTL_API_BASE_URL=http://127.0.0.1:8000 mcctl-agent
```

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
