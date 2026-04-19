# farfield-cli

Simple local CLI bridge for controlling Codex App through a [Farfield](https://github.com/drewcotten/farfield) sidecar.

Huge thanks to [Drew Cotten](https://github.com/drewcotten) for open-sourcing Farfield. This project is designed to interoperate with Farfield, and it is open-sourced under the same MIT license family.

Simplified Chinese README: [README.zh-CN.md](README.zh-CN.md)

## Why this exists

The goal is simple:

- clone Farfield
- run `pnpm install` once
- enter the Farfield directory
- run `farfield-cli ...`

No dashboard. No extra front-end. No remote exposure. Just a small loopback-only CLI that can:

- auto-start the Farfield server sidecar
- list/read Codex threads
- send messages
- change collaboration mode
- submit pending user input
- interrupt the current run

This also makes it easy for another agent, script, or automation system to shell out to one stable CLI instead of implementing the Farfield HTTP contract itself.

## Install

```bash
pip install farfield-cli
```

Or from source:

```bash
git clone https://github.com/wuji419-bit/farfield-cli.git
cd farfield-cli
pip install -e .
```

## Prerequisites

1. Install Codex desktop on the same machine
2. Clone Farfield locally
3. Run `pnpm install` in the Farfield repo at least once

By default `farfield-cli` assumes Farfield should listen on:

```text
http://127.0.0.1:4311
```

## Zero-config usage

If your current directory is:

- the Farfield repo root, or
- a parent directory containing `farfield/`

then `farfield-cli` auto-discovers the repo and can auto-start the sidecar for you.

```bash
cd /path/to/farfield
farfield-cli status
farfield-cli list-threads
farfield-cli list-models
```

## Common commands

```bash
farfield-cli status
farfield-cli list-threads --limit 20
farfield-cli get-thread-state --thread-id thread_123 --include-stream-events
farfield-cli send-message --thread-id thread_123 --text "Continue this task"
farfield-cli interrupt --thread-id thread_123
```

For opaque JSON payloads:

```bash
farfield-cli set-collaboration-mode \
  --thread-id thread_123 \
  --json '{"mode":"default"}'

farfield-cli submit-user-input \
  --thread-id thread_123 \
  --request-id 7 \
  --json '{"kind":"text","text":"Continue"}'
```

## Configuration

Everything is optional.

Environment variables:

- `FARFIELD_CLI_BASE_URL`
- `FARFIELD_CLI_PROJECT_DIR`
- `FARFIELD_CLI_START_COMMAND`
- `FARFIELD_CLI_AUTOSTART`
- `FARFIELD_CLI_STARTUP_TIMEOUT`
- `FARFIELD_CLI_REQUEST_TIMEOUT`
- `CODEX_CLI_PATH`
- `CODEX_IPC_SOCKET`

Defaults:

```text
base_url           = http://127.0.0.1:4311
start_command      = pnpm --filter @farfield/server dev
autostart          = true
startup_timeout    = 20
request_timeout    = 30
```

Loopback only is enforced in v1. `127.0.0.1` and `localhost` are allowed; remote hosts are rejected on purpose.

## Output

All commands print JSON. Success responses look like:

```json
{
  "success": true,
  "bridge": {
    "base_url": "http://127.0.0.1:4311",
    "autostarted": false,
    "process_pid": null
  },
  "data": {}
}
```

Failures look like:

```json
{
  "success": false,
  "error": "human-readable message",
  "http_status": 409,
  "bridge": {
    "base_url": "http://127.0.0.1:4311",
    "autostarted": false,
    "process_pid": null
  },
  "details": {}
}
```

## Dev

Run tests:

```bash
python -m unittest discover -s tests -p "test_*.py"
```
