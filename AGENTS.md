# AGENTS.md

## Project

MCCTL Agent is the public CLI agent for MCCTL.

Repository:
- public repo: https://github.com/MCCTL/Agent
- package name: mcctl-agent
- command: mcctl-agent
- default API URL: https://api.mcctl.com

## Context discipline

Use targeted reads.
Do not inspect build artifacts, virtualenvs, caches, binary files, or generated distributions unless required.

Avoid:
- .venv
- dist
- build
- __pycache__
- .pytest_cache
- *.egg-info
- coverage
- binary files
- large logs

Cap large command output:

```bash
COMMAND 2>&1 | head -c 4000
```

## Security

- Never print agent tokens.
- Never print pairing tokens.
- `mcctl-agent status` and `mcctl-agent version` must not display secrets.
- `mcctl-agent reset` may delete config, but must clearly show the config path.
- Default API URL must remain `https://api.mcctl.com`.
- `MCCTL_API_BASE_URL` is for development override only.

## Validation

Run before final report:

```bash
python -m pytest
python -m ruff check .
```

Package install check:

```bash
py -m pipx install --force git+https://github.com/MCCTL/Agent.git
```

Linux install check:

```bash
pipx install --force git+https://github.com/MCCTL/Agent.git
~/.local/bin/mcctl-agent status
```

## Windows behavior

Windows autostart uses Task Scheduler.

Commands:

```powershell
mcctl-agent autostart install
mcctl-agent autostart status
mcctl-agent autostart uninstall
```

If PATH is not updated:

```powershell
& "$env:USERPROFILE\.local\bin\mcctl-agent.exe"
```

## Linux behavior

Systemd service should use absolute path:

```ini
ExecStart=/home/YOUR_USER/.local/bin/mcctl-agent
```

## Editing rules

- Prefer small patches.
- Keep CLI output clear and Japanese docs understandable.
- Do not add arbitrary remote command execution.
- Web-triggered updates must use fixed agent-side behavior only.
