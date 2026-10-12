# AGENTS.md — Proxmox CLI Agent Guide

This file is the operating guide for agents working on `ProxBot`.

## Current Repository Layout

This checkout is organized as follows; keep documentation and changes aligned
with these actual paths:

```text
ProxBot/
├── chat.py                 # Chat assistant entry point
├── scripts/
│   ├── Proxmox-cli.py      # CLI entry point
│   ├── Proxmox-console.py  # Console broker entry point
│   ├── BUGS.md
│   ├── IMPROVEMENTS.md
│   └── logs/               # Runtime logs (gitignored)
├── src/proxbot/
│   ├── common/             # Configuration, API client, logging, safety
│   ├── cli/                # Parser, dispatch, and command handlers
│   ├── chat/               # Agent, tools, executor, approvals, session
│   └── console/            # Broker, VNC protocol, and console commands
├── tests/                  # Unit tests and static/equivalence checks
├── plans/                  # Design and planning documents
├── requirements.txt        # Python runtime dependencies
├── .env.example            # Safe configuration template
└── AGENTS.md / README.md   # Agent guide and human-facing overview
```

The three entry points above are thin shims; implementation belongs in
`src/proxbot/`. Local `.env`, `.git.env`, generated keys, caches, and logs are
not source files and must remain untracked. Python dependencies are declared in
`requirements.txt` (`Pillow`, `requests`, `python-dotenv`).

## Skill Location

This guide belongs to the local checkout at `/home/svaye/Scripts/ProxBot`. When the user names this guide or checkout, use its CLI and `.env`; do not substitute the installed OpenCode copy.

From any working directory, use this explicit prefix:

```bash
python3 /home/svaye/Scripts/ProxBot/scripts/Proxmox-cli.py --env-file /home/svaye/Scripts/ProxBot/.env
```

Never create, modify, overwrite, or relocate `.env` unless the user explicitly asks. If configuration is missing, report the path selected by `doctor`; never synthesize placeholder credentials.

## Mission

Maintain a safe CLI wrapper that allows an agent harness to inspect and manage Proxmox VE through predictable commands, `.env` configuration, strong logging, and explicit safety guards.

## First Steps for Any Agent

1. Read `README.md`.
2. Check `.env.example` but never print or expose real `.env` secrets.
3. Inspect `scripts/BUGS.md`.
4. Inspect `scripts/IMPROVEMENTS.md`.
5. Run `git status --short` — if the tree is dirty, stop and ask before editing.
6. Confirm secrets are ignored: `git check-ignore -q .env && echo safe`.
7. Run read-only checks before any write command.

Everything you change must end up on a branch and in a commit. See **Git
Workflow** below before the first edit.

## Setup

```bash
python3 /home/svaye/Scripts/ProxBot/scripts/Proxmox-cli.py --env-file /home/svaye/Scripts/ProxBot/.env doctor
```

Add `--live` to `doctor` to explicitly request the live API connectivity test.

Always pass the local checkout's explicit `--env-file` when this guide was selected. An unrelated `.env` in the harness working directory must not select the target cluster.

Use `--format json` for agent calls. `commands --format json` returns the canonical supported syntax and guard requirements. Prefer canonical nested commands over legacy aliases.

Required `.env` values:

```dotenv
PROXMOX_API_URL=https://proxmox.example.local:8006/api2/json
PROXMOX_API_TOKEN_ID=agent@pve!opencode
PROXMOX_API_TOKEN_SECRET=replace-me
```

Never commit `.env`. The full git rules — branching, staging, commit format, and
recovery — are in **Git Workflow** below and are mandatory for any code change.

## Common Read-Only Commands

```bash
python3 /home/svaye/Scripts/ProxBot/scripts/Proxmox-cli.py --env-file /home/svaye/Scripts/ProxBot/.env doctor
python3 /home/svaye/Scripts/ProxBot/scripts/Proxmox-cli.py --env-file /home/svaye/Scripts/ProxBot/.env health
python3 /home/svaye/Scripts/ProxBot/scripts/Proxmox-cli.py --env-file /home/svaye/Scripts/ProxBot/.env nodes list
python3 /home/svaye/Scripts/ProxBot/scripts/Proxmox-cli.py --env-file /home/svaye/Scripts/ProxBot/.env node status --node <node>
python3 /home/svaye/Scripts/ProxBot/scripts/Proxmox-cli.py --env-file /home/svaye/Scripts/ProxBot/.env vms list --node <node>
python3 /home/svaye/Scripts/ProxBot/scripts/Proxmox-cli.py --env-file /home/svaye/Scripts/ProxBot/.env vm status --node <node> --vmid 101
python3 /home/svaye/Scripts/ProxBot/scripts/Proxmox-cli.py --env-file /home/svaye/Scripts/ProxBot/.env vm config --node <node> --vmid 101
python3 /home/svaye/Scripts/ProxBot/scripts/Proxmox-cli.py --env-file /home/svaye/Scripts/ProxBot/.env vm guest-network --node <node> --vmid 101
python3 /home/svaye/Scripts/ProxBot/scripts/Proxmox-cli.py --env-file /home/svaye/Scripts/ProxBot/.env storage list --node <node>
python3 /home/svaye/Scripts/ProxBot/scripts/Proxmox-cli.py --env-file /home/svaye/Scripts/ProxBot/.env tasks recent --node <node>
```

Always use the node returned by `nodes list`; names such as `pve1` are placeholders and may not exist.

## Lookup / Discovery Commands

Use these to discover IDs, networks, storage, and templates before creating resources.

```bash
# Next free VM/container ID
python3 /home/svaye/Scripts/ProxBot/scripts/Proxmox-cli.py --env-file /home/svaye/Scripts/ProxBot/.env vms next-id
python3 /home/svaye/Scripts/ProxBot/scripts/Proxmox-cli.py --env-file /home/svaye/Scripts/ProxBot/.env lxcs next-id

# Networks (bridges, VLANs, physical interfaces)
python3 /home/svaye/Scripts/ProxBot/scripts/Proxmox-cli.py --env-file /home/svaye/Scripts/ProxBot/.env network list --node <node>

# Storage and content
python3 /home/svaye/Scripts/ProxBot/scripts/Proxmox-cli.py --env-file /home/svaye/Scripts/ProxBot/.env storage list --node <node>
python3 /home/svaye/Scripts/ProxBot/scripts/Proxmox-cli.py --env-file /home/svaye/Scripts/ProxBot/.env storage content --node <node> --storage local

# LXC templates available on a storage pool
python3 /home/svaye/Scripts/ProxBot/scripts/Proxmox-cli.py --env-file /home/svaye/Scripts/ProxBot/.env storage templates --node <node> --storage local

# Download an LXC template from a URL (state-changing, dry-run by default)
python3 /home/svaye/Scripts/ProxBot/scripts/Proxmox-cli.py --env-file /home/svaye/Scripts/ProxBot/.env storage template-download --node <node> --storage local \
  --url https://example.com/debian-12-standard.tar.zst \
  --filename debian-12-standard.tar.zst

# Print Debian 13 LXC console workaround for an existing container
python3 /home/svaye/Scripts/ProxBot/scripts/Proxmox-cli.py --env-file /home/svaye/Scripts/ProxBot/.env lxc fix-console --node <node> --vmid 109
```

## In-Container Command Execution

The Proxmox REST API has **no exec endpoint for LXC**. Its subdir index is
`clone config firewall interfaces migrate pending resize rrd rrddata snapshot
spiceproxy status termproxy vncproxy vncwebsocket` — no `exec`, no `mount`. (A
bogus path returns the same "Method not implemented" message as a real one, so
the index, not the error, is the authoritative check. For VMs the equivalent
`qemu/{vmid}/agent/exec` does exist — that is why `vm_exec` works.)

Three transports exist, and they are not equally good:

| Transport | Needs | Reliability | Use for |
|---|---|---|---|
| Guest SSH | Guest IP + installed key | Good | Primary |
| `pct exec` | Node SSH access | Good | Out-of-band; only path that works with no guest networking |
| Console (`termproxy`) | A getty, and nobody else attached | Poor | Human debugging only |

### Transport 1: guest SSH (default)

`cmd_lxc_exec` tries this first. Two silent failure modes to check for:

- **Unreachable or wrong IP.** `_resolve_guest_ip` returns the first non-loopback
  address from `/nodes/{node}/lxc/{vmid}/interfaces`. If a container was
  DHCP-assigned an address that another host holds statically, SSH succeeds
  against the *wrong machine* and its screen is returned as the container's
  output. Verify the address belongs to the container before trusting a result.
- **No key.** A clone only has a key if it was baked into its template; see
  "LXC SSH Key Management".

### Transport 2: `pct exec` over node SSH

`pct exec` runs on the node, so reaching it means SSH to the node. Configure it
in `.env` — a key or a password:

```dotenv
PROXMOX_NODE_SSH_USER=root
PROXMOX_NODE_SSH_KEY=/path/to/node_key
# or
PROXMOX_NODE_SSH_PASSWORD=...
```

`PROXMOX_NODE_SSH_USER` alone (no credential) means the harness will prompt for
a password with `getpass`, cached in memory for the session and dropped by
`clear_history`. Unset entirely means host-side execution is disabled and
`lxc pct-exec` refuses with `node_ssh_unconfigured` without attempting anything.

Confirm what is actually usable before relying on it:

```bash
python3 /home/svaye/Scripts/ProxBot/scripts/Proxmox-cli.py --env-file /home/svaye/Scripts/ProxBot/.env doctor --live --node-access
```

The probe runs `pct version` over node SSH per node and reports `usable` plus the
reason when it is not. `doctor` always reports which of the three transports are
available.

```bash
python3 /home/svaye/Scripts/ProxBot/scripts/Proxmox-cli.py --env-file /home/svaye/Scripts/ProxBot/.env lxc pct-exec --node <node> --vmid <id> --command "systemctl is-active nginx" --execute
```

Rules the implementation enforces, and which new code must not weaken:

- The remote command is assembled in exactly **one** place
  (`assemble_pct_exec`) as `pct exec <vmid> -- /bin/sh -lc <command>`, with
  `shlex.quote`. The node, the `pct` invocation and the shell are not
  caller-supplied. This is not a general node shell.
- `vmid` is validated numeric at runtime as well as by argparse, because it is
  interpolated into the node command line.
- A failing command reports its real exit code. It is never coerced to `0`.
- `ProxmoxToolExecutor.execute()` refuses secret channels except for explicitly
  approved harness tools and `lxc_create` (root password passed over an FD).

Node access is a real privilege increase. Prefer an unprivileged account scoped
to the containers you need over `root`. `ssh exec` refuses Proxmox node targets
unless listed in `PROXMOX_NODE_SSH_TARGETS`; if you find a route around that,
the model can bypass the `pct exec` allowlist with a plain SSH call.

**Secret handling.** Never put a node password in argv. argv is world-readable
via `/proc/*/cmdline`. `--node-password` exists for scripted use and is
documented as visible in the process table; the chat harness instead uses
`--node-password-fd` with an inherited pipe. Redaction of secret-bearing flags is
defined once in `redact_command_secrets` (`src/proxbot/common/redact.py`) and
shared by the argv form, the command-string form, `agent.py` and `session.py`.
Extend that list, never a copy — a copy is how `--node-password` leaked into the
dry-run plan.

### Transport 3: the console (explicit opt-in only)

The LXC console is a **single shared pty**. Attaching can land on a human's live
shell; nothing in the stream identifies whose session it is. Measured on
pveheavy/170, the prompt appeared on 2 of ~20 attachments and on 0 of 13 with no
other session attached. It is also usually blank: Debian 13's systemd 257
`console-getty.service` and `container-getty@.service` carry `ImportCredential`
directives that fail inside LXC, so no login prompt appears at all.
`lxc fix-console` prints the drop-in fix.

`lxc exec` never falls back to the console implicitly. `--console` is required,
and an absent `console` attribute means "not requested" so older callers cannot
silently re-enable it. Without it, a failed SSH attempt returns
`console_not_requested`.

Status vocabulary for console attempts — keep these distinct, the model makes
decisions from them:

| Status | Meaning |
|---|---|
| `login_required` | A login prompt *was* seen; a password would help |
| `login_failed` | Credential rejected, or accepted but no prompt within 4s |
| `console_unavailable` | No credential prompt on this attachment, or the transport never came up |
| `output_lost` | Command sent, result unverifiable — `exit_code` is `null` |
| `console_not_requested` | No `--console`, so the console was not touched |

`output_lost` must never be reported as success. A missing exit status is
`UNKNOWN`, not `0`: a false success is worse for an agent than an honest unknown.

## LXC SSH Key Management

Generate and inject SSH keys for LXC containers. Generated keys are stored on the machine running the CLI, not in the Proxmox API, and private keys are kept out of logs.

`lxc create` takes `--ssh-public-keys`/`--ssh-keygen`, but **`lxc clone` does
not** (its only optional flags are `--hostname --storage --pool --snapname`). A
clone is therefore reachable only if the key was baked into the template it came
from. Use `--as-template` to build such a base image; this is the recommended
alternative to granting node SSH, because it removes the need for both a console
login and `pct exec` for ordinary work.

Creating a fresh LXC from an OS tarball **requires a root password**. If the
user has not supplied one, the chat harness prompts securely with `getpass`
before creation; never ask them to paste it into chat or invent/assume a
credential. SSH public-key injection is optional but strongly recommended for
reliable remote access. This requirement does not apply to `lxc clone`, which
inherits the source container/template's account configuration. For interactive
CLI use, prefer `--password-fd` so the password does not appear in process argv;
`--password` remains available but is visible in the process table.

```bash
# Read a password without echoing it; each create example below passes it on FD 3.
read -rsp 'LXC root password: ' LXC_ROOT_PASSWORD; echo

# Build a keyed base template from a stock Debian 13 tarball
python3 /home/svaye/Scripts/ProxBot/scripts/Proxmox-cli.py --env-file /home/svaye/Scripts/ProxBot/.env lxc create --node <node> --vmid 900 \
  --hostname proxbot-base \
  --ostemplate local:vztmpl/debian-13-standard_13.6-1_amd64.tar.zst \
  --storage <store> \
  --password-fd 3 3<<<"$LXC_ROOT_PASSWORD" \
  --ssh-public-keys "$(cat ~/.ssh/lxc-base.pub)" \
  --as-template --execute --wait

# Clone it; the key is inherited
python3 /home/svaye/Scripts/ProxBot/scripts/Proxmox-cli.py --env-file /home/svaye/Scripts/ProxBot/.env lxc clone --node <node> --vmid 900 \
  --new-vmid 901 --hostname my-container --execute --wait
```

Notes:
- `ssh-public-keys` is redacted in dry-run output as key material. Confirm against the local `.pub` file rather than loosening the redaction rule.
- Converted templates (template:1) cannot be built with `lxc create --ostemplate`; that path fails with a misleading "Only root can pass arbitrary filesystem paths". `lxc clone` wraps the clone endpoint instead. See "LXC SSH Key Management" in the BUGS history.

```bash
# Generate a new Ed25519 key pair for a specific container
python3 /home/svaye/Scripts/ProxBot/scripts/Proxmox-cli.py --env-file /home/svaye/Scripts/ProxBot/.env lxc ssh-keygen --vmid 110 --keyfile ~/.ssh/lxc-110 --execute

# Create an LXC container and inject an existing public key
python3 /home/svaye/Scripts/ProxBot/scripts/Proxmox-cli.py --env-file /home/svaye/Scripts/ProxBot/.env lxc create --node pve1 --vmid 110 \
  --hostname my-container \
  --ostemplate local:vztmpl/debian-13-standard_13.1-2_amd64.tar.zst \
  --storage local-lvm \
  --password-fd 3 3<<<"$LXC_ROOT_PASSWORD" \
  --ssh-public-keys "ssh-ed25519 AAAAC3... user@host"

# Create an LXC container and generate + inject a fresh key on the fly
python3 /home/svaye/Scripts/ProxBot/scripts/Proxmox-cli.py --env-file /home/svaye/Scripts/ProxBot/.env lxc create --node pve1 --vmid 110 \
  --hostname my-container \
  --ostemplate local:vztmpl/debian-13-standard_13.1-2_amd64.tar.zst \
  --storage local-lvm \
  --password-fd 3 3<<<"$LXC_ROOT_PASSWORD" \
  --ssh-keygen --ssh-key-path ~/.ssh/lxc-110

# Create an LXC container with Docker-friendly settings
python3 /home/svaye/Scripts/ProxBot/scripts/Proxmox-cli.py --env-file /home/svaye/Scripts/ProxBot/.env lxc create --node pve1 --vmid 110 \
  --hostname docker-host \
  --ostemplate local:vztmpl/debian-13-standard_13.1-2_amd64.tar.zst \
  --storage local-lvm \
  --password-fd 3 3<<<"$LXC_ROOT_PASSWORD" \
  --rootfs local-lvm:16 \
  --features nesting=1 \
  --unprivileged \
  --net0 'name=eth0,bridge=vmbr0,ip=dhcp' \
  --nameserver 1.1.1.1 \
  --onboot \
  --ssh-public-keys "ssh-ed25519 AAAAC3... user@host"
```

Notes:
- Debian/Ubuntu LXC templates typically disable root password SSH login, so key injection is the preferred way to access the container.
- `lxc ssh-keygen` and `lxc create --ssh-keygen` only write files when `--execute` is used; in dry-run mode no key is generated.
- The generated public key is placed at the path specified by `--ssh-key-path` with a `.pub` suffix (default: `./.lxc-ssh-key-<VMID>.pub`).
- `--unprivileged` defaults to `True`. If you use `--features nesting=1` with an API token that is **not** `root@pam`, keep `--unprivileged` enabled; setting feature flags on a privileged container is only allowed for `root@pam`.
- `--nameserver` and `--onboot` are available as first-class flags for LXC and VM creation; `--data` still overrides them if you need custom JSON.
- Use `ssh -i ~/.ssh/lxc-110 root@<container-ip>` to connect.

## Console Broker

`scripts/Proxmox-console.py` is a separate, opt-in broker for VM console access. It is deliberately isolated from the main CLI so console access is easy to audit and disable.

Configuration:

```dotenv
PROXMOX_CONSOLE_ALLOWED_VMIDS=101,102
PROXMOX_CONSOLE_TIER=1
PROXMOX_CONSOLE_REQUIRE_SNAPSHOT=true
PROXMOX_CONSOLE_SESSION_LOG_DIR=scripts/logs/console-sessions
PROXMOX_CONSOLE_KEY_DELAY=1.5
PROXMOX_CONSOLE_WATCH_INTERVAL=5.0
```

Phase 1 commands:

```bash
python3 /home/svaye/Scripts/ProxBot/scripts/Proxmox-console.py doctor
python3 /home/svaye/Scripts/ProxBot/scripts/Proxmox-console.py status --node <node> --vmid 101
python3 /home/svaye/Scripts/ProxBot/scripts/Proxmox-console.py screenshot --node <node> --vmid 101
python3 /home/svaye/Scripts/ProxBot/scripts/Proxmox-console.py read-text --node <node> --vmid 101
python3 /home/svaye/Scripts/ProxBot/scripts/Proxmox-console.py watch --node <node> --vmid 101 --until-stable --timeout 300
```

Phase 2/3 commands:

```bash
python3 /home/svaye/Scripts/ProxBot/scripts/Proxmox-console.py send-key --node <node> --vmid 101 --key Enter --execute
python3 /home/svaye/Scripts/ProxBot/scripts/Proxmox-console.py send-keys --node <node> --vmid 101 \
  --keys "Space,Downx10,Space,Tab,Enter" --execute
python3 /home/svaye/Scripts/ProxBot/scripts/Proxmox-console.py mouse-move --node <node> --vmid 101 --x 100 --y 100 --execute
python3 /home/svaye/Scripts/ProxBot/scripts/Proxmox-console.py mouse-test --node <node> --vmid 101 --execute
python3 /home/svaye/Scripts/ProxBot/scripts/Proxmox-console.py run-command --node <node> --vmid 101 --command "ip addr" --execute
python3 /home/svaye/Scripts/ProxBot/scripts/Proxmox-console.py snapshot --node <node> --vmid 101 --name before-console --execute
python3 /home/svaye/Scripts/ProxBot/scripts/Proxmox-console.py type --node <node> --vmid 101 --text "systemctl status ssh" --execute --force --confirm 101
python3 /home/svaye/Scripts/ProxBot/scripts/Proxmox-console.py paste-text --node <node> --vmid 101 --text "systemctl status ssh" --execute --force --confirm 101
python3 /home/svaye/Scripts/ProxBot/scripts/Proxmox-console.py install-preset --node <node> --vmid 101 \
  --preset debian-13-server-v1 --tasksel-focus-confirmed --execute --force --confirm 101
python3 /home/svaye/Scripts/ProxBot/scripts/Proxmox-console.py macro --node <node> --vmid 101 \
  --actions '[{"action":"key","key":"Enter"},{"action":"wait","seconds":1}]' --execute --force --confirm 101
python3 /home/svaye/Scripts/ProxBot/scripts/Proxmox-console.py session --node <node> --vmid 101 --list
python3 /home/svaye/Scripts/ProxBot/scripts/Proxmox-console.py session --node <node> --vmid 101 --stop --session-id <id> --execute --force --confirm <id>
```

Console rules:

- If `PROXMOX_CONSOLE_ALLOWED_VMIDS` is empty, the console broker refuses all operations with exit code `10`.
- Tier 1 commands (`screenshot`, `read-text`, `status`, `watch`, `session --list`) work without `--execute`.
- Tier 2 commands (`send-key`, `send-keys`, `mouse-move`, `mouse-test`, `run-command`, `snapshot`) require `--execute` and a tier of `2` or higher.
- `mouse-test` is the preferred mouse diagnostic. It runs sequential pointer moves in one VNC session and returns per-step screenshots, framebuffer dimensions, and clamped coordinates.
- Tier 3 commands (`type`, `paste-text`, `macro`, `install-preset`, `session --stop`) require `--execute --force --confirm <vmid>` and a tier of `3` or higher.
- `type` requires a recent `auto-before-console-*` snapshot or `--auto-snapshot`; it rejects shell metacharacters unless `--unsafe` is used.
- `paste-text` follows the same safeguards as `type`, rejects Enter characters, and captures a screenshot. Review it before submitting separately with `send-key --key Enter`.
- `install-preset debian-13-server-v1` is valid only for the stock Debian 13 tasksel layout when the selected desktop task has focus. Take a screenshot and pass `--tasksel-focus-confirmed` only after visually verifying that precondition.
- `macro` accepts a JSON list of actions (`key`, `keys`, `type`, `click`, `move`, `wait`) via `--actions` or `--file`; it uses a single VNC session and follows the same snapshot/unsafe rules as `type`.
- `send-keys` accepts a comma-separated list of key names and supports repetition (`Downx10`). Only keys in the allowlist are permitted.
- Key names are normalized before sending. Common aliases work: `Esc`, `Del`, `PageUp`, `PageDown`, `Ctrl`, `Windows`/`Win`, bracketed notation like `[ctrl]+[alt]+[del]`, and QEMU-style hyphen combos like `ctrl-alt-del`.
- `watch` polls the console until `--until-stable` detects two consecutive identical frames (or `--timeout` expires). Use `--until-unchanged-for SECONDS` to require the screen to remain still for a longer interval. Use `--screenshots` to keep every captured frame.
- `send-key`, `send-keys`, `mouse-move`, and `type` wait `PROXMOX_CONSOLE_KEY_DELAY` seconds after input before capturing the result. Override with `--delay`.
- `run-command` only allows commands in the built-in allowlist of safe diagnostics.
- VNC tickets and serial console tickets are never printed to stdout or stderr; logs redact them.
- Values following `--text`, `--keys`, and `--actions` are redacted from command logs to avoid recording passwords or command text.
- Use VNC only until SSH or QEMU guest-agent access is available. After OS install, prefer SSH or `run-command` for shell commands; `send-keys` is for navigation keys, not arbitrary command text or passwords.
- If a click is ignored, retry only after confirming the target visually. `click --hold-ms 150` increases the pointer-button hold time for guests that miss the default 50 ms press.

Post-install transition: install and enable `openssh-server` and `qemu-guest-agent` in the guest through an approved guest access path, then run `vm guest-network --node <node> --vmid <vmid> --format json` to discover its interfaces. Switch to SSH after the guest IP is confirmed.
- Screenshots are saved to `PROXMOX_CONSOLE_SESSION_LOG_DIR` as PNG files; a review copy is also written to `/tmp/console-<vmid>-<filename>.png`.
- Session events are written as JSONL to the same directory.

BIOS/UEFI and preboot recovery guidance:

- Boot menus and recovery prompts often expire in seconds. Use a fresh screenshot immediately before input, then a short screenshot/watch loop after each key (`--interval 0.5 --timeout 5` to `10`) rather than long waits that can outlive the prompt.
- Prefer one verified navigation key at a time for firmware menus (`Esc`, `Del`, `F2`, `F10`, `F12`, arrows, `Enter`). Use a compact `send-keys` sequence only after the current focus is visually confirmed.
- For Ctrl/Alt/Windows combinations, use canonical plus notation (`Ctrl+Alt+Delete`, `Super+R`) or accepted aliases (`ctrl-alt-del`, `[ctrl]+[alt]+[del]`, `Win+R`).
- If a key appears ignored, take another screenshot before retrying; the VM may have advanced screens, lost focus, or passed the preboot timeout.

## Console Broker on KVM-less Nodes

If the Proxmox host is virtualized or has hardware virtualization disabled, `/dev/kvm` is unavailable and VMs must use TCG software emulation (`kvm: 0`). This is much slower than KVM acceleration.

Detect the situation:

```bash
python3 /home/svaye/Scripts/ProxBot/scripts/Proxmox-cli.py --env-file /home/svaye/Scripts/ProxBot/.env doctor
# or on the Proxmox node directly: ls /dev/kvm
```

If KVM is missing, set `kvm: 0` on the VM before starting:

```bash
python3 /home/svaye/Scripts/ProxBot/scripts/Proxmox-cli.py --env-file /home/svaye/Scripts/ProxBot/.env api post /nodes/pve1/qemu/101/config --data '{"kvm":"0"}' --execute
```

Workflow adjustments for TCG VMs:

- Increase `PROXMOX_CONSOLE_KEY_DELAY` (e.g., `2.0`) to avoid dropped keystrokes.
- Avoid installing a desktop environment through the Debian installer; use the minimal `server` preset (standard utilities + SSH server) to keep install times reasonable.
- Use `watch --until-stable` to wait for installer prompts instead of taking manual screenshots on a timer.
- Expect long-running steps (apt configuration, kernel package setup) to take many minutes.

## Write Command Rules

State-changing commands must dry-run by default.

`lxc exec` is not in the automatic transport chain any more: guest SSH first,
then `pct exec` if node access is configured, and the console only when
`--console` is passed. See "In-Container Command Execution".

Dry-run example:

```bash
python3 /home/svaye/Scripts/ProxBot/scripts/Proxmox-cli.py --env-file /home/svaye/Scripts/ProxBot/.env vm start --node pve1 --vmid 101
```

Real execution example:

```bash
python3 /home/svaye/Scripts/ProxBot/scripts/Proxmox-cli.py --env-file /home/svaye/Scripts/ProxBot/.env vm start --node pve1 --vmid 101 --execute --wait
```

Dangerous execution example:

```bash
python3 /home/svaye/Scripts/ProxBot/scripts/Proxmox-cli.py --env-file /home/svaye/Scripts/ProxBot/.env vm stop --node pve1 --vmid 101 --execute --force --confirm 101
```

Do not bypass safety guards.

## Prompt.md Compatibility

For compatibility with the original `prompt.md` planning file, the CLI also accepts these deprecated flat aliases and `--yes` as an alias for `--execute` on write commands. New agent workflows must use the canonical nested commands returned by `commands --format json`.

- `status` → `health`
- `nodes` → `nodes list`
- `vms` → `vms list`
- `containers` → `lxcs list`
- `vm-status` → `vm status`
- `container-status` → `lxc status`
- `start-vm` → `vm start`
- `reboot-vm` → `vm reboot`
- `stop-vm` → `vm stop`

Destructive aliases still require the full `--execute --force --confirm <vmid>` guard, with `--yes` usable in place of `--execute`. The `.env` loader also falls back to `PROXMOX_HOST`/`PROXMOX_PORT` and `PROXMOX_API_USER`/`PROXMOX_API_TOKEN_ID` formats.

## Logging Rules

All commands should log to:

```text
scripts/logs/
```

Logs must include:

- command argv,
- dry-run status,
- API method,
- endpoint,
- status code,
- duration,
- success/failure,
- task ID if returned,
- redacted error details.

Logs must never include:

- token secrets,
- full Authorization headers,
- passwords,
- private keys.

## Bug Tracking

When you find a bug, update:

```text
scripts/BUGS.md
```

Use this format:

```md
## YYYY-MM-DD — Short bug title

- Status: open | fixed | cannot-reproduce
- Found by: agent/human
- Command:
- Expected:
- Actual:
- Suspected cause:
- Fix notes:
- Related log file:
```

## Improvement Tracking

When you notice a useful improvement, update:

```text
scripts/IMPROVEMENTS.md
```

Use this format:

```md
## YYYY-MM-DD — Short improvement title

- Status: proposed | accepted | implemented | rejected
- Area: cli | docs | logging | safety | tests | api
- Reason:
- Suggested change:
- Tradeoffs:
- Related files:
```

## Git Workflow

This repository is under version control (branch `main`, baseline commit created
2026-09-28). Treat git as the primary safety mechanism for every code change.

### Non-negotiable rules

1. **Never commit secrets.** `.env`, `.lxc-ssh-key-*`, `logs/`, `.agent/`, and
   `.chat_history` are gitignored. Before the first commit of any session, verify:
   ```bash
   git check-ignore -q .env && echo "safe"
   ```
   If you ever add a new file that holds a token, password, or private key, add it
   to `.gitignore` **before** staging.
2. **Never rewrite published history.** No `git push --force`, no `git rebase` on
   a shared branch, no `git checkout --orphan` rewrites, no skipping hooks.
3. **Never commit code you cannot get back.** The one unrecoverable loss in this
   project's history was overwriting a file with no backup and no commit. Commit
   *before* any large move, rename, or refactor — not after.
4. **Never `git add -A` blindly.** Stage explicit paths and read the diff:
   ```bash
   git status --short
   git diff --cached
   ```

### Starting a change (feature or bug fix)

```bash
# 1. Confirm a clean starting point. If anything is dirty, stop and ask.
git status --short          # must be empty
git log --oneline -3

# 2. Branch. One branch per logical change.
git checkout -b fix/short-slug      # or: feat/short-slug, docs/short-slug

# 3. For anything beyond a small edit, checkpoint the pre-change state first so a
#    bad refactor is recoverable with `git checkout main`:
git tag pre-refactor-YYYYMMDD      # optional but cheap
```

### While working

- Make the smallest change that solves the problem. One concern per commit.
- If a change touches `src/proxbot/`, keep the old import path working: the
  entrypoints (`chat.py`, `scripts/Proxmox-cli.py`, `scripts/Proxmox-console.py`)
  are re-export shims, and tests plus the console broker load them by path.
- After moving code between modules, run the static reference check:
  ```bash
  python3 tests/check_undefined_names.py src/proxbot
  ```
  (`src/proxbot/cli/main.py` is expected to appear: it binds handlers
  dynamically from the registry.)

### Before committing

```bash
# 1. Full test suite must pass.
python3 -m unittest discover -s tests

# 2. Static checks.
python3 tests/check_undefined_names.py src/proxbot
python3 tests/verify_cli_equivalence.py    # see caveat below

# 2b. For exec-transport or secret-handling changes, also assert the leak tests.
python3 -m unittest tests.test_chat.BenignNoticeTests -v
python3 -m unittest tests.test_cli.PctExecTests -v

# 3. For CLI changes, confirm the machine-readable surface is unchanged.
python3 scripts/Proxmox-cli.py --format json commands --no-log

# 4. Exercise at least one read-only command against the live API.
python3 scripts/Proxmox-cli.py --format json nodes list
python3 scripts/Proxmox-cli.py --format json doctor   # exec_transports block

# 5. Update scripts/BUGS.md and/or scripts/IMPROVEMENTS.md in the SAME commit.

# 6. Stage deliberately and read what you staged.
git add <explicit paths>
git diff --cached
git diff --cached | grep -nEi "PVEAPIToken=|PRIVATE KEY|api_token_secret"
#    → must show only placeholders such as replace-me / USER@REALM!TOKENID
```

> Caveat: `tests/verify_cli_equivalence.py` compares the refactored CLI against a
> pre-refactor baseline at `/tmp/opencode/Proxmox-cli.py.orig`. That path is
> temporary and will disappear on reboot; the script then exits `2` and skips the
> check. If that happens, restore the baseline into the repo (e.g.
> `tests/baselines/Proxmox-cli.pre-refactor.py`) and update the constant at the
> top of the script.

### Committing

Commit messages in this repo use a short summary line, an optional body, and a
`Fixes:`/`Feature:` style breakdown. Match that style. Reference the tracked
files you touched.

```bash
git commit -F - <<'EOF'
Add lxc clone / vm clone for converted templates

Converted templates (template:1) cannot be built with `lxc create
--ostemplate`; that path failed with a misleading "Only root can pass
arbitrary filesystem paths". Wraps the clone endpoint instead.

Fixes:
  - lxc create validates --ostemplate against <storage>:vztmpl/*.tar.*
  - templates_list tags each entry with its creation_method

Verification:
  - 89 unittests pass
  - `commands --format json` unchanged
EOF
```

### After committing

```bash
git log --oneline -3
git status --short          # must be empty
```

Report to the user: what changed, why, how it was verified, and any residual risk.
If a change required reconstructing or guessing prior behavior, say so explicitly
rather than presenting it as a clean fix.

### Recovering

```bash
git diff                        # review uncommitted changes
git restore <path>              # discard changes to a file
git restore --staged <path>     # unstage
git switch -                    # back to previous branch
git revert <sha>                # undo a committed change with a new commit
git reset --hard <sha>          # DESTRUCTIVE: only on an unpushed local branch
```

## How the Chat Harness Reaches Containers

The model sees one exec tool per transport, and never a node shell:

| Tool | Transport | Notes |
|---|---|---|
| `lxc_exec` | guest SSH, then `pct exec` | Console is **not** in the chain; it requires an explicit opt-in the model does not have |
| `lxc_pct_exec` | `pct exec` over node SSH | Harness-assembled; see below |
| `ssh_exec` | direct SSH | Refuses Proxmox node targets unless listed in `PROXMOX_NODE_SSH_TARGETS` |

`lxc_pct_exec` takes only `vmid` and `command` (plus optional `node` and
`timeout`). The node, the `pct` invocation and the shell are assembled by the
harness, so there is no general node shell to escape into.

Three layers enforce that, and the middle one is the only load-bearing one:

1. **Structural** — the tool signature does not accept the dangerous fields.
2. **Hard validator** — `validate_harness_command()` in
   `src/proxbot/chat/watchdog.py` runs inside `LoopWatchdog.check_call()`, which
   `agent.py` calls **before** the approval prompt. It therefore cannot be
   satisfied by typing `y` or by `--auto-approve`, and a rejected call does not
   consume the per-turn budget. It **refuses rather than sanitises**: unknown
   arguments, a non-numeric `vmid`, a bool `vmid`, a non-string `command`, and a
   command starting with `pct`.
3. **Repeated in the executor** — `ProxmoxToolExecutor.execute()` re-checks,
   because it is reachable directly and must not depend on `agent.py` having been
   the caller.

Rules for new code:

- Extend the validator; never add a second path around it.
- A tool in `HARNESS_ASSEMBLED_TOOLS` must not accept caller-supplied node, user,
  shell, or remote argv.
- If you add a tool that can reach a host, close the equivalent `ssh exec` route
  in the same change, or the allowlist is decorative.

### Password escalation in chat

`lxc_exec` prompts with `getpass` when the console reports `login_required`.
`lxc_pct_exec` prompts when the CLI reports `node_password_required`, which it
emits only when a node user is configured but no credential is. With a key
configured, nothing is ever asked.

Both cache the secret in memory for the session and drop it on `clear_history`.
Neither ever logs it or puts it in the message history. For the node password the
harness passes the secret to the CLI on an inherited pipe
(`--node-password-fd`), never in argv.

### Reading the model's output

- `✓` green — ran, reported success.
- `✗` red — failed.
- `!` yellow — the only thing reported was routine `ssh(1)` chatter, which is not
  a failure. `_is_benign_notice()` decides this, and it requires *every* reported
  line to be benign, so a real error alongside a known-hosts notice stays red.

## Before Changing Code

- Check existing bugs and improvements.
- Confirm `git status --short` is empty; branch before editing.
- Prefer small, testable changes, one commit per concern.
- Preserve safety defaults.
- Do not add new dependencies unless they clearly reduce complexity.

## After Changing Code

- Run the full test suite: `python3 -m unittest discover -s tests`.
- Run `doctor` if a live `.env` is available.
- Test at least one read-only command.
- Confirm logs are created and secrets are redacted.
- Update `scripts/BUGS.md` or `scripts/IMPROVEMENTS.md` when appropriate.
- Commit with an explicit path list, then confirm `git status --short` is empty.
- Summarize what changed and why.
