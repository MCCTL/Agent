# AI Context Optimization

This repository contains only the public MCCTL Agent package. Keep AI context focused on the CLI code, packaging, and tests.

## Default Workflow

1. Search exact command names, config keys, error messages, or module names with `rg`.
2. Read only the files and ranges needed for the task.
3. Use Repomix only for compact, task-specific bundles.
4. Never include local agent config, tokens, virtualenvs, or build artifacts.

Good first commands:

```bash
rg "MCCTL_API_BASE_URL" src tests -n
rg "autostart" src tests -n
git log --oneline -20
```

## Repomix Usage

The root `repomix.config.json` is configured for compact XML output and ignores build artifacts, virtualenvs, caches, logs, and generated distributions.

Generate a full source/test bundle:

```bash
npx repomix --include "src/mcctl_agent/**/*.py,tests/**/*.py,README.md,pyproject.toml" --output repomix-output.xml
```

Generate a narrow config/defaults bundle:

```bash
npx repomix --include "src/mcctl_agent/config.py,src/mcctl_agent/main.py,tests/test_config*.py,README.md" --output repomix-output.xml
```

Do not commit `repomix-output.*` or `repomix-*.xml`.

## What To Exclude

The ignore files intentionally exclude:

- `.venv/`, Python caches, and test caches
- `dist/`, `build/`, wheels, and source distributions
- local `.env` files and logs
- binary and archive files
- Repomix output files

These files are either reproducible, noisy, or unsafe for prompt context.

## AGENTS.md Role

`AGENTS.md` is the short operational guide for future agents. Keep it focused on:

- default production API behavior
- token and pairing-token safety
- validation commands
- Windows and Linux install/runtime notes
- editing rules

Use `README.md` for user-facing installation docs and this file for context-management policy.

## Validation For Context-Only Changes

For docs/config-only changes, run:

```bash
git diff --check
```

Agent tests are not required unless package or runtime code changes. If runtime code changes, run the validation commands in `AGENTS.md`.
