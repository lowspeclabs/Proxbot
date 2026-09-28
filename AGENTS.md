# AGENTS.md — Proxmox CLI Agent Guide

This file is the operating guide for agents working on `proxmox-opencode-lab`.

## Skill Location

This guide belongs to the local checkout at `/home/stephen/Scripts/9b-proxmox-manager/proxmox-manager`. When the user names this guide or checkout, use its CLI and `.env`; do not substitute the installed OpenCode copy.

From any working directory, use this explicit prefix:

```bash
python3 /home/stephen/Scripts/9b-proxmox-manager/proxmox-manager/scripts/Proxmox-cli.py --env-file /home/stephen/Scripts/9b-proxmox-manager/proxmox-manager/.env
```

Never create, modify, overwrite, or relocate `.env` unless the user explicitly asks. If configuration is missing, report the path selected by `doctor`; never synthesize placeholder credentials.

## Mission

Maintain a safe CLI wrapper that allows an agent harness to inspect and manage Proxmox VE through predictable commands, `.env` configuration, strong logging, and explicit safety guards.

## First Steps for Any Agent

1. Read `README.md`.
2. Check `.env.example` but never print or expose real `.env` secrets.
3. Inspect `scripts/BUGS.md`.
4. Inspect `scripts/IMPROVEMENTS.md`.
5. Run read-only checks before any write command.

## Setup

```bash
python3 /home/stephen/Scripts/9b-proxmox-manager/proxmox-manager/scripts/Proxmox-cli.py --env-file /home/stephen/Scripts/9b-proxmox-manager/proxmox-manager/.env doctor
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

Never commit `.env`.

## Common Read-Only Commands

```bash
python3 /home/stephen/Scripts/9b-proxmox-manager/proxmox-manager/scripts/Proxmox-cli.py --env-file /home/stephen/Scripts/9b-proxmox-manager/proxmox-manager/.env doctor
python3 /home/stephen/Scripts/9b-proxmox-manager/proxmox-manager/scripts/Proxmox-cli.py --env-file /home/stephen/Scripts/9b-proxmox-manager/proxmox-manager/.env health
python3 /home/stephen/Scripts/9b-proxmox-manager/proxmox-manager/scripts/Proxmox-cli.py --env-file /home/stephen/Scripts/9b-proxmox-manager/proxmox-manager/.env nodes list
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py node status --node <node>
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py vms list --node <node>
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py vm status --node <node> --vmid 101
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py vm config --node <node> --vmid 101
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py vm guest-network --node <node> --vmid 101
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py storage list --node <node>
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py tasks recent --node <node>
```

Always use the node returned by `nodes list`; names such as `pve1` are placeholders and may not exist.

## Lookup / Discovery Commands

Use these to discover IDs, networks, storage, and templates before creating resources.

```bash
# Next free VM/container ID
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py vms next-id
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py lxcs next-id

# Networks (bridges, VLANs, physical interfaces)
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py network list --node <node>

# Storage and content
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py storage list --node <node>
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py storage content --node <node> --storage local

# LXC templates available on a storage pool
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py storage templates --node <node> --storage local

# Download an LXC template from a URL (state-changing, dry-run by default)
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py storage template-download --node <node> --storage local \
  --url https://example.com/debian-12-standard.tar.zst \
  --filename debian-12-standard.tar.zst

# Print Debian 13 LXC console workaround for an existing container
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py lxc fix-console --node <node> --vmid 109
```

## LXC SSH Key Management

Generate and inject SSH keys for LXC containers. Generated keys are stored on the machine running the CLI, not in the Proxmox API, and private keys are kept out of logs.

```bash
# Generate a new Ed25519 key pair for a specific container
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py lxc ssh-keygen --vmid 110 --keyfile ~/.ssh/lxc-110 --execute

# Create an LXC container and inject an existing public key
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py lxc create --node pve1 --vmid 110 \
  --hostname my-container \
  --ostemplate local:vztmpl/debian-13-standard_13.1-2_amd64.tar.zst \
  --storage local-lvm \
  --ssh-public-keys "ssh-ed25519 AAAAC3... user@host"

# Create an LXC container and generate + inject a fresh key on the fly
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py lxc create --node pve1 --vmid 110 \
  --hostname my-container \
  --ostemplate local:vztmpl/debian-13-standard_13.1-2_amd64.tar.zst \
  --storage local-lvm \
  --ssh-keygen --ssh-key-path ~/.ssh/lxc-110

# Create an LXC container with Docker-friendly settings
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py lxc create --node pve1 --vmid 110 \
  --hostname docker-host \
  --ostemplate local:vztmpl/debian-13-standard_13.1-2_amd64.tar.zst \
  --storage local-lvm \
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
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-console.py doctor
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-console.py status --node <node> --vmid 101
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-console.py screenshot --node <node> --vmid 101
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-console.py read-text --node <node> --vmid 101
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-console.py watch --node <node> --vmid 101 --until-stable --timeout 300
```

Phase 2/3 commands:

```bash
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-console.py send-key --node <node> --vmid 101 --key Enter --execute
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-console.py send-keys --node <node> --vmid 101 \
  --keys "Space,Downx10,Space,Tab,Enter" --execute
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-console.py mouse-move --node <node> --vmid 101 --x 100 --y 100 --execute
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-console.py mouse-test --node <node> --vmid 101 --execute
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-console.py run-command --node <node> --vmid 101 --command "ip addr" --execute
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-console.py snapshot --node <node> --vmid 101 --name before-console --execute
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-console.py type --node <node> --vmid 101 --text "systemctl status ssh" --execute --force --confirm 101
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-console.py paste-text --node <node> --vmid 101 --text "systemctl status ssh" --execute --force --confirm 101
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-console.py install-preset --node <node> --vmid 101 \
  --preset debian-13-server-v1 --tasksel-focus-confirmed --execute --force --confirm 101
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-console.py macro --node <node> --vmid 101 \
  --actions '[{"action":"key","key":"Enter"},{"action":"wait","seconds":1}]' --execute --force --confirm 101
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-console.py session --node <node> --vmid 101 --list
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-console.py session --node <node> --vmid 101 --stop --session-id <id> --execute --force --confirm <id>
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
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py doctor
# or on the Proxmox node directly: ls /dev/kvm
```

If KVM is missing, set `kvm: 0` on the VM before starting:

```bash
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py api post /nodes/pve1/qemu/101/config --data '{"kvm":"0"}' --execute
```

Workflow adjustments for TCG VMs:

- Increase `PROXMOX_CONSOLE_KEY_DELAY` (e.g., `2.0`) to avoid dropped keystrokes.
- Avoid installing a desktop environment through the Debian installer; use the minimal `server` preset (standard utilities + SSH server) to keep install times reasonable.
- Use `watch --until-stable` to wait for installer prompts instead of taking manual screenshots on a timer.
- Expect long-running steps (apt configuration, kernel package setup) to take many minutes.

## Write Command Rules

State-changing commands must dry-run by default.

Dry-run example:

```bash
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py vm start --node pve1 --vmid 101
```

Real execution example:

```bash
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py vm start --node pve1 --vmid 101 --execute --wait
```

Dangerous execution example:

```bash
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py vm stop --node pve1 --vmid 101 --execute --force --confirm 101
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

## Before Changing Code

- Check existing bugs and improvements.
- Prefer small, testable changes.
- Preserve safety defaults.
- Do not add new dependencies unless they clearly reduce complexity.

## After Changing Code

- Run the safest available tests.
- Run `doctor` if a live `.env` is available.
- Test at least one read-only command.
- Confirm logs are created and secrets are redacted.
- Update `scripts/BUGS.md` or `scripts/IMPROVEMENTS.md` when appropriate.
- Summarize what changed and why.
