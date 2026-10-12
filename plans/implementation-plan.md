# Implementation Plan — proxmox-opencode-lab

## Goal
Build an agent-friendly Python CLI that lets an OpenCode-style agent harness inspect and safely manage Proxmox VE through the Proxmox REST API. The CLI should reduce brittle direct API calls by exposing stable commands, structured output, dry-run behavior, and command/result logs.

## Recommended Repository Layout

```text
proxmox-opencode-lab/
├─ AGENTS.md
├─ README.md
├─ spec.md
├─ .env.example
├─ scripts/
│  ├─ Proxmox-cli.py
│  ├─ BUGS.md
│  ├─ IMPROVEMENTS.md
│  └─ logs/
│     └─ .gitkeep
└─ docs/
   └─ proxmox-api-notes.md
```

## Phase 1 — Scaffold and Documentation

Deliverables:
- Create the folder structure.
- Add `.env.example` with safe placeholder values.
- Add `README.md` explaining what the project is, why it exists, the layout, and why files are placed where they are.
- Add `AGENTS.md` with usage instructions for agents.
- Add `scripts/BUGS.md` and `scripts/IMPROVEMENTS.md` for agent-maintained project notes.
- Add `docs/proxmox-api-notes.md` for Proxmox API references, endpoint notes, auth notes, and gotchas.

Acceptance criteria:
- A new agent can read `README.md` and understand the project purpose in under two minutes.
- A coding agent can read `AGENTS.md` and know exactly how to run, test, log, and document changes.
- No real credentials exist in the repo.

## Phase 2 — Config and API Client

Deliverables:
- Implement `.env` loading in `scripts/Proxmox-cli.py`.
- Support required config:
  - `PROXMOX_API_URL`
  - `PROXMOX_API_TOKEN_ID`
  - `PROXMOX_API_TOKEN_SECRET`
- Support optional config:
  - `PROXMOX_VERIFY_SSL`
  - `PROXMOX_TIMEOUT_SECONDS`
  - `PROXMOX_DEFAULT_NODE`
  - `PROXMOX_OUTPUT_FORMAT`
  - `PROXMOX_DRY_RUN_DEFAULT`
  - `PROXMOX_LOG_DIR`
- Build a reusable API client wrapper for `GET`, `POST`, `PUT`, and `DELETE`.
- Add secret redaction for logs and terminal output.

Acceptance criteria:
- `python scripts/Proxmox-cli.py doctor` validates the config without exposing secrets.
- `python scripts/Proxmox-cli.py api get /version` returns a parsed API response.
- Bad tokens, bad URLs, SSL errors, and timeouts produce clear exit codes and clear messages.

## Phase 3 — Logging Foundation

Deliverables:
- Create `scripts/logs/` automatically if missing.
- Write one JSONL log per run.
- Append a small human-readable markdown summary for each run if enabled.
- Log every CLI command, resolved action, HTTP method, endpoint, status code, duration, success/failure, and redacted response summary.
- Never log token secrets.

Acceptance criteria:
- Every command creates or appends to a log file under `scripts/logs/`.
- Logs are useful enough for a second agent to reconstruct what happened.
- Secrets are redacted in all log outputs.

## Phase 4 — Read-Only Proxmox Commands

Deliverables:
- Add safe inspection commands:
  - `health`
  - `nodes list`
  - `node status --node <name>`
  - `vms list [--node <name>]`
  - `vm status --node <name> --vmid <id>`
  - `vm config --node <name> --vmid <id>`
  - `lxcs list [--node <name>]`
  - `lxc status --node <name> --vmid <id>`
  - `storage list [--node <name>]`
  - `storage content --node <name> --storage <name>`
  - `tasks recent [--node <name>]`
  - `task status --node <name> --upid <upid>`
  - `logs latest`
- Support output formats:
  - `--format table`
  - `--format json`
  - `--format md`

Acceptance criteria:
- Read-only commands work without `--execute`.
- Output is deterministic and easy for an agent to parse.
- JSON output always includes a top-level `ok` boolean.

## Phase 5 — Safe Write Commands

Deliverables:
- Add guarded state-changing commands:
  - `vm start`
  - `vm shutdown`
  - `vm stop`
  - `vm reboot`
  - `vm snapshot create`
  - `vm snapshot list`
  - `vm snapshot delete`
  - `lxc start`
  - `lxc shutdown`
  - `lxc stop`
  - `lxc snapshot create`
  - `lxc snapshot list`
  - `lxc snapshot delete`
- Default all state-changing commands to dry-run unless `--execute` is passed.
- Require explicit `--node` and `--vmid` for write actions.
- Require stronger confirmation for destructive actions:
  - `--execute`
  - `--force`
  - `--confirm <vmid>`

Acceptance criteria:
- A write command without `--execute` prints the planned action but makes no API call.
- Destructive commands cannot run by accident.
- Idempotent handling exists for already-running or already-stopped resources where practical.

## Phase 6 — Task Tracking and Wait Logic

Deliverables:
- Capture Proxmox task IDs returned by write operations.
- Add `task wait --node <name> --upid <upid> [--timeout <seconds>]`.
- Add optional `--wait` to write commands.
- Log task polling results.

Acceptance criteria:
- `vm start --wait` waits for the Proxmox task to complete or times out clearly.
- Failed Proxmox tasks are surfaced with the relevant status and error message.

## Phase 7 — Tests and Harness Validation

Deliverables:
- Add tests for env loading, secret redaction, command parsing, output formatting, and logging.
- Add fake API client tests so CI does not require a live Proxmox host.
- Add optional live integration tests gated by `.env` and an explicit flag.

Acceptance criteria:
- Unit tests can run without Proxmox.
- Live tests are opt-in only.
- Tests cover both positive and negative paths.

## Phase 8 — Agent Hardening

Deliverables:
- Add examples in `AGENTS.md` for typical agent workflows.
- Add failure recovery guidance.
- Add rules for updating `scripts/BUGS.md` and `scripts/IMPROVEMENTS.md` after every meaningful run.
- Add a “known dangerous operations” section.

Acceptance criteria:
- A small model can follow the docs and avoid common mistakes.
- Agents leave behind useful markdown breadcrumbs for future agents.
- Logs and markdown trackers together explain what changed, what failed, and what should improve next.
