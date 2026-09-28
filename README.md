# proxmox-opencode-lab

`proxmox-opencode-lab` is an agent-friendly CLI wrapper for managing a Proxmox VE lab through stable, logged, safety-guarded commands.

This copy is installed as an opencode skill at `~/.config/opencode/skills/proxmox-manager/`. Use absolute paths or run commands from that directory.

The project is designed for OpenCode-style agent harnesses and small local models that need to inspect or manage Proxmox without hand-writing brittle raw API calls every time.

## Why This Exists

Direct Proxmox API calls are powerful, but they are easy for agents to misuse. This project gives agents a safer interface with:

- predictable commands,
- markdown and JSON output,
- dry-run defaults for write actions,
- explicit destructive-operation guards,
- `.env`-based API configuration,
- command/result logging,
- markdown bug and improvement tracking.

## Recommended Layout

The three entrypoints at the repository root are stable, thin shims. All
implementation lives in the `proxbot` package under `src/`.

```text
ProxBot/
├─ chat.py                      # interactive AI assistant (composition root)
├─ scripts/
│  ├─ Proxmox-cli.py            # agent-facing Proxmox CLI
│  ├─ Proxmox-console.py        # brokered VNC/serial console access
│  ├─ BUGS.md
│  ├─ IMPROVEMENTS.md
│  └─ logs/
├─ src/proxbot/
│  ├─ common/                   # shared by all three entrypoints
│  │  ├─ constants.py           # exit codes, paths, redaction constants
│  │  ├─ config.py              # .env loading, Config
│  │  ├─ redact.py              # secret redaction
│  │  ├─ logging_.py            # JSONL logger
│  │  ├─ client.py              # Proxmox REST client
│  │  ├─ render.py              # json/table/markdown output
│  │  └─ safety.py              # dry-run + destructive guards
│  ├─ cli/
│  │  ├─ parser.py              # argparse construction
│  │  ├─ registry.py            # command handler registry
│  │  ├─ main.py                # dispatch
│  │  └─ commands/              # cluster, vm, lxc, exec, storage, tasks
│  ├─ chat/                     # markdown, session, spec, executor,
│  │                            #   safety, agent, terminal, models
│  └─ console/
│     ├─ broker.py              # config, eligibility, sessions, capture
│     ├─ protocol.py            # DES auth, WebSocket, RFB/VNC
│     ├─ keys.py                # key aliases + RFB event encoding
│     └─ commands/              # read, input, mouse, text, admin
├─ tests/
│  ├─ test_cli.py
│  ├─ test_chat.py
│  ├─ test_console.py
│  └─ check_undefined_names.py  # static check for missing imports
└─ docs/
```

## Layout Rationale

- `chat.py`, `scripts/Proxmox-cli.py`, and `scripts/Proxmox-console.py` stay at
  stable paths so agent harnesses, tests, and the console broker can find them.
  They are composition roots: they set up `sys.path` and re-export the package.
- `src/proxbot/common/` holds the plumbing all three entrypoints share, so
  `Proxmox-console.py` no longer duplicates `Config`/`Logger`/redaction.
- `src/proxbot/cli/commands/` splits ~60 handlers by domain instead of one file.
- `src/proxbot/console/` isolates the console broker's protocol code so VNC/DES
  machinery can be audited separately from command policy and tier guards.
- No file exceeds ~700 lines.
- `tests/check_undefined_names.py` catches cross-module references broken by a
  move; run it after any refactor that relocates code.

### Development

```bash
python3 -m unittest discover -s tests            # full suite
python3 tests/check_undefined_names.py src/proxbot
```

When moving code between modules, keep the old path working (shim re-export)
and take a backup first — this project is not under version control.

## Quickstart

```bash
cp .env.example .env
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py doctor
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py health
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py nodes list
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py vms list --node <node>
```

Use the node name returned by `nodes list`; placeholder names in examples are not guaranteed to exist in your lab.

The CLI uses `.env` in the current directory before the installed skill's `.env`. Use `--env-file /absolute/path/to/.env` when the command must target a specific lab.

For agent calls, prefer JSON and inspect the canonical command catalog before acting:

```bash
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py --format json commands
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py nodes list --format json
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py resolve --vmid <id> --format json
```

State-changing commands must dry-run by default and require explicit execution flags.

```bash
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py vm start --node <node> --vmid 101
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py vm start --node <node> --vmid 101 --execute --wait
```

Dry-runs validate the request and return a redacted API plan. Inspect the plan before adding `--execute`. Use `--request-timeout` for individual API calls and `--wait-timeout` for asynchronous tasks.

Dangerous operations require stronger guards.

```bash
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py vm stop --node <node> --vmid 101 --execute --force --confirm 101
```

## Console Broker

`scripts/Proxmox-console.py` is a separate, opt-in broker for VM console access. It never exposes raw VNC tickets to the agent and enforces tiered permissions:

- Tier 1 (default): read-only `screenshot`, `read-text`, `status`, and `watch`.
- Tier 2: `send-key`, `send-keys`, `run-command`, and `snapshot`.
- Tier 3: `type` with snapshot and confirmation guards.
- Tier 4: raw interactive sessions are blocked by design.

Configure allowed VMIDs in `.env`:

```dotenv
PROXMOX_CONSOLE_ALLOWED_VMIDS=101,102
PROXMOX_CONSOLE_TIER=1
PROXMOX_CONSOLE_REQUIRE_SNAPSHOT=true
PROXMOX_CONSOLE_SESSION_LOG_DIR=scripts/logs/console-sessions
PROXMOX_CONSOLE_KEY_DELAY=1.5
PROXMOX_CONSOLE_WATCH_INTERVAL=5.0
```

Examples:

```bash
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-console.py doctor
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-console.py status --node <node> --vmid 101
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-console.py screenshot --node <node> --vmid 101
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-console.py read-text --node <node> --vmid 101
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-console.py watch --node <node> --vmid 101 --until-stable --timeout 300
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-console.py send-key --node <node> --vmid 101 --key Enter --execute
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-console.py send-keys --node <node> --vmid 101 \
  --keys "Space,Downx10,Space,Tab,Enter" --execute
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-console.py run-command --node <node> --vmid 101 --command "ip addr" --execute
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-console.py snapshot --node <node> --vmid 101 --name before-console --execute
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-console.py type --node <node> --vmid 101 --text "systemctl status ssh" --execute --force --confirm 101
```

Console screenshots are saved as PNG files to the session log directory and a review copy is also written to `/tmp/console-<vmid>-<filename>.png`.

Use VNC for boot menus, installers, and recovery screens. After the guest has SSH or QEMU guest-agent access, switch to those paths for shell commands; `send-keys` is for navigation keys and should not be used for passwords or arbitrary command text.

For BIOS/UEFI and preboot recovery prompts, use short screenshot/watch intervals after each key (`--interval 0.5 --timeout 5` to `10`) because boot prompts can expire before a long wait finishes. Key aliases such as `Esc`, `Del`, `PageDown`, `Windows`/`Win`, `[ctrl]+[alt]+[del]`, and `ctrl-alt-del` are normalized before sending.

## Safety Rule

Read first. Dry-run second. Execute only after the intended node, VMID, endpoint, and action are clear.
