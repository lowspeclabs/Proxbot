# ProxBot Project History

Consolidated record of where ProxBot came from, what the original plans asked for, and what is actually implemented in this checkout. It replaces reading nine overlapping files in `plans/` for day-to-day work.

Source plans consolidated here (kept verbatim in `plans/` for archaeology):

- `plans/spec.md` — canonical CLI contract (v1 scope, safety, logging, tests).
- `plans/prompt.md` — build loop and completion criteria ("Ralph loop").
- `plans/implementation-plan.md` — phased build order (Phases 1–8).
- `plans/proxmox-api-notes.md` — endpoint/auth/task notes plus Debian 13 console quirk.
- `plans/Spec.md` — tool-constrained agent vision ("Proxmox operating system", tiers, policy layer).
- `plans/chat-script-plan.md` — `chat.py` architecture, tool table, safety workflow, roadmap.
- `plans/agent-chat-loop.md` — current chat loop reference (outer REPL + inner LLM loop).
- `plans/console-access.md` — VNC console broker design (tiers, phases, acceptance criteria).
- `plans/SKILL.md` — skill copy (capabilities, command sequences, policies).

For live operator guidance use `AGENTS.md` (agent workflow) and `README.md` (human setup). For open defects use `scripts/BUGS.md`; for proposed work use `scripts/IMPROVEMENTS.md`.

## Timeline

1. **CLI foundation (`spec.md` + `prompt.md` + `implementation-plan.md`).** Single-file `scripts/Proxmox-cli.py` with `.env` config, JSONL logs in `scripts/logs/`, dry-run writes, destructive guards, markdown bug/improvement trackers, mocked + opt-in live tests.
2. **Agent harness vision (`Spec.md`).** Model must never build raw API URLs or run arbitrary shell. Small semantic tool layer → Python harness → Proxmox API / controlled SSH. Read / safe-mutation / dangerous tiers, policy gate between tool call and execution, `chat.py` as the agent brain.
3. **Chat implementation (`chat-script-plan.md` → `agent-chat-loop.md`).** `chat.py` REPL plus `ChatAgent` turn loop, `ProxmoxToolExecutor` subprocess bridge, `SafetyGate` approvals, `LoopWatchdog` loop suppression, `SessionLogger` JSONL sessions, `RollingThoughtStreamer` + markdown renderer, `TOOLS_SPEC` risk levels, slash commands, LLM configuration.
4. **Console broker (`console-access.md`).** Separate opt-in `scripts/Proxmox-console.py`: Tier 1 read-only, Tier 2 useful actions, Tier 3 controlled typing, Tier 4 blocked-by-design. Snapshot-before-change, input allowlists, ticket/secret redaction, session recording.
5. **Refactor + hardening (current shape).** Single scripts became thin shims over `src/proxbot/` (`common/`, `cli/`, `chat/`, `console/`). Added clone/create flows, in-guest exec transports, node-SSH confinement, keyed base templates, required LXC passwords, expanded console commands, and regression coverage. Details live in `scripts/BUGS.md` and `scripts/IMPROVEMENTS.md`.

## Consolidated spec (what the plans collectively require)

- **Config:** `.env` holds `PROXMOX_API_URL`, `PROXMOX_API_TOKEN_ID`, `PROXMOX_API_TOKEN_SECRET`; optional verify/timeout/default-node/format/log-dir plus node-SSH, console, and LLM keys. `.env` stays untracked; `.env.example` holds placeholders; `doctor` validates without leaking secrets; SSL defaults on and insecure mode is noisy.
- **CLI style:** `python scripts/Proxmox-cli.py <resource> <action> [options]`; global `--format md|json|table`, `--no-log`, `--dry-run`, `--execute`, timeouts; legacy aliases accepted but canonical nested commands preferred.
- **Commands:** meta (`commands`, `resolve`, `doctor`, `health`), nodes, VMs, LXCs, snapshots, storage, network, tasks, raw `api`, logs, templates registry/download, create/clone/delete, in-guest exec, SSH exec, key management, console broker.
- **Safety:** reads run directly; writes dry-run by default and require `--execute`; destructive actions require `--execute --force --confirm <id>`; no hidden bulk actions; non-interactive flag-based confirmation.
- **Logging:** one JSONL log per run under `scripts/logs/` with argv, dry-run flag, method, endpoint, status, duration, ok, task ID, redacted errors; `latest.jsonl` pointer; chat sessions under `scripts/logs/chat-sessions/`; console sessions under `scripts/logs/console-sessions/`.
- **Redaction:** token secrets, full `Authorization` headers, passwords, keys, VNC/termproxy tickets, and typed console text never appear in logs, stdout/stderr results, or model messages.
- **Output:** markdown default (human + agent readable), `json` always carries top-level `ok`, `table` for demos; errors go to stderr with type/message/hint/log path.
- **Exit codes:** `0` success, `1` validation, `2` config, `3` auth, `4` API, `5` timeout, `6` no-op, `10` safety, `99` internal.
- **Tests:** unit + fake-API tests runnable without Proxmox; live tests opt-in (`doctor --live`); positive and negative paths including guard refusal and secret redaction.
- **Docs:** `README.md`, `AGENTS.md`, `.env.example`, bug/improvement trackers, API notes.

## Implementation status (verified against this checkout)

Status key: **Complete** = implemented and covered; **Partial** = implemented with a known gap; **Open** = not done.

### CLI core — Complete

- `doctor` (local + `--live`, `--node-access`), `health`, `commands`, `resolve` — `src/proxbot/cli/commands/cluster.py`, parser/dispatch in `src/proxbot/cli/{parser,main}.py`.
- `nodes list`, `node status`, `vms list`, `vms next-id`, `lxcs list`, `lxcs next-id` — `cluster.py`.
- `vm status/config/guest-network/exec/start/shutdown/stop/reboot/create/clone/delete`, `vm snapshot list/create/delete` — `src/proxbot/cli/commands/vm.py`.
- `lxc status/config/start/shutdown/stop/create/clone/delete/exec/ssh-keygen/fix-console`, `lxc snapshot list/create/delete`, `lxc pct-exec` — `src/proxbot/cli/commands/{lxc,exec}.py`, `nodessh.py`.
- `ssh exec` with node-target refusal unless allowlisted — `exec.py`.
- `storage list/content/templates/template-download`, `templates registry/download`, `network list` — `storage.py`.
- `tasks recent`, `task status/wait`, `api get/post/put/delete`, `logs latest/path/tail` — `tasks.py`.
- Output formats `md/json/table`, dry-run plans, destructive guards, JSONL logging + redaction, exit-code map — `common/{render,safety,logging_,redact,constants}.py`.

### Chat harness — Complete

- Semantic tool layer (`TOOLS_SPEC`, ~40 tools; no raw API passthrough), read/write/destructive risk sets, `HARNESS_ASSEMBLED_TOOLS` — `src/proxbot/chat/spec.py`.
- Turn loop (streaming `/chat/completions`, reasoning split, tool-call accumulation, sequential dispatch, history pruning) — `chat/agent.py`, `chat.py` REPL.
- Subprocess bridge (`Proxmox-cli.py --env-file … --format json`, 180 s timeout, last-JSON-doc wins, multi-node fan-out, auto-stop before delete, template aggregation) — `chat/executor.py`.
- Approvals (`SafetyGate`), loop suppression + harness validation (`LoopWatchdog`, `validate_harness_command`), session JSONL logging + redaction (`SessionLogger`), thought streamer, markdown/terminal renderer, slash commands, LLM config + backend detection — `chat/{safety,watchdog,session,streamer,markdown,terminal,models}.py`.
- Credential escalation: `lxc_create` root password via `getpass` → `--password-fd`; `lxc_exec login_required` retry; `lxc_pct_exec node_password_required` cached per-node secret → `--node-password-fd`; `ssh_exec` auth-failure stop-and-ask — `chat/agent.py`, `chat/executor.py`.

### Console broker — Complete

- Tier 1: `doctor`, `status`, `screenshot`, `read-text`, `watch` (incl. `--until-stable`, `--until-unchanged-for`, `--screenshots`) — `console/commands/read.py`.
- Tier 2: `send-key`, `send-keys` (incl. `KeyxN`), `mouse-move`, `mouse-test`, `run-command` (allowlisted), `snapshot` — `console/commands/{input,mouse,admin}.py`.
- Tier 3: `type`, `paste-text`, `macro`, `install-preset debian-13-server-v1`, `session --stop`, `click` — `console/commands/{text,mouse,admin}.py`.
- VM eligibility (`PROXMOX_CONSOLE_ALLOWED_VMIDS`, empty denies all, exit 10), tier enforcement, snapshot-before-change (`auto-before-console-*` or `--auto-snapshot`), input/key allowlists + shell-metacharacter rules, ticket/secret redaction, session JSONL + screenshot recording (`scripts/logs/console-sessions/` + `/tmp` review copy) — `console/broker.py`, `console/keys.py`, `console/protocol.py`.

### Original v1 acceptance criteria — Complete

Folder layout (plus evolved `src/`, `tests/`, `plans/`), `.env.example` coverage, `doctor/health/nodes/vms/vm/api` flows, log creation + redaction, dry-run default, `--execute` writes, `--execute --force --confirm` destructive path, README/AGENTS rationale, markdown trackers — all present.

## Intentional divergences from the earliest plans

- **Package, not one file.** The spec suggested one `Proxmox-cli.py`; the code is now thin shims (`chat.py`, `scripts/Proxmox-cli.py`, `scripts/Proxmox-console.py`) over `src/proxbot/`. Keep old import paths working when moving code.
- **Superset of tools.** Chat plan listed ~21 tools; the harness now exposes ~40 (create/clone/exec/registry and friends). The original 21 remain the safe starting sequence.
- **Console grew.** Broker added `send-keys`, `paste-text`, `macro`, `mouse-test`, extended `watch`, and installer presets beyond the first console plan. Tier 4 stays structural (no raw-session command exists).
- **Config grew.** Beyond API keys: node-SSH (`PROXMOX_NODE_SSH_*`, `PROXMOX_NODE_SSH_TARGETS`), console (`PROXMOX_CONSOLE_*`), chat/LLM (`LLM_*`, `CHAT_*`). `.env`, `.git.env`, keys, caches, and logs stay untracked.
- **Dependencies.** Spec suggested `requests` + `python-dotenv`; `requirements.txt` also includes `Pillow` for console PNG screenshots.
- **Command surface.** `commands --format json` is the canonical catalog; legacy aliases and `--yes` remain for compatibility.

## Open work (not marked complete)

- **`lxc exec` IP validation (open bug).** `_resolve_guest_ip` can return an address owned by another host; fix by validating against `hwaddr`/hostname and failing loudly. Related `*-proxbot` keys on the affected host still need removal. See `scripts/BUGS.md`.
- **Cloned-template reachability (open bug).** Converted-template clones may lack a getty and collide with static node addresses; bake keys into the base template and fix DHCP scope overlap. See `scripts/BUGS.md`.
- **Console mouse in installers (open live-test issue).** Clicks may not register in some installers; `click --hold-ms` exists but needs a live retest. See `scripts/BUGS.md`.
- **KVM-less host (open live-test issue).** Hosts without `/dev/kvm` need `kvm: 0` (TCG) and slower console/install workflows. See `scripts/BUGS.md`.
- **Spec future enhancements (mostly untouched).** Cloud-init helpers, backups, ISO upload, storage-health summaries, HA status, cluster firewall inspection, shell completion, report generation, and a safe plan-file mode remain future work. VM/LXC creation, snapshots, raw API fallback, task waiting, template registry/download, and guest exec are the completed subset.
