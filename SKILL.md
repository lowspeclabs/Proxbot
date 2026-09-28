---
name: proxmox-manager
description: Use when creating, managing, inspecting, or interacting with Proxmox VE labs. Trigger for Proxmox, VM, LXC, container, node, storage, snapshot, task, API, console, or Proxmox VE lifecycle tasks. Use ONLY for Proxmox VE management through the provided CLI.
---

# Proxmox Manager Skill

## What this skill does

This skill lets you inspect and manage a Proxmox VE lab through a controlled, safety-guarded CLI that returns structured output and logs every action.

This skill source is the local checkout at `/home/stephen/Scripts/9b-proxmox-manager/proxmox-manager`. When this local skill or one of its files is named, use its CLI with `--env-file /home/stephen/Scripts/9b-proxmox-manager/proxmox-manager/.env`; never substitute `~/.config/opencode/skills/proxmox-manager`.

A separate, opt-in console broker lives at `~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-console.py` for VM console screenshots, text capture, and controlled keyboard input.

Implemented capabilities:

- Health, version, node, VM, LXC, storage, network, and task discovery.
- Read-only inspection of nodes, VMs, LXCs, storage, networks, snapshots, and tasks.
- Managed VM/LXC lifecycle: start, shutdown, stop, reboot, create, delete.
- Snapshot create/delete for VMs and LXCs.
- LXC SSH key generation and injection.
- Raw API fallback with dry-run guards.
- VNC/serial console screenshots and text capture (read-only by default).
- Tiered console input controls with VM eligibility guards.
- Comprehensive logging under `~/.config/opencode/skills/proxmox-manager/scripts/logs/`.

## What this skill must not do

- Never expose API token secrets, passwords, or VNC tickets in responses or logs.
- Never run destructive operations without `--execute --force --confirm <id>`.
- Never run state-changing operations without `--execute`.
- Never send raw, improvised HTTP calls; always use the provided CLI.
- Never modify production clusters or resources outside the configured lab without explicit user confirmation.
- Never create or modify `.env` unless the user explicitly requests that exact change. Do not generate placeholder credentials after a config error.

## Required first command

Always run the following before making changes:

```bash
python3 /home/stephen/Scripts/9b-proxmox-manager/proxmox-manager/scripts/Proxmox-cli.py --env-file /home/stephen/Scripts/9b-proxmox-manager/proxmox-manager/.env doctor
```

`doctor` checks local configuration and the log directory without exposing secrets. Pass `--live` to explicitly confirm live API connectivity.

## Safe command sequence

1. `doctor`
2. `health`
3. `nodes list`
4. `vms list --node <node>` / `lxcs list --node <node>`
5. Read-only inspection: `vm status`, `lxc status`, `storage list`, `tasks recent`
6. Write operations with `--execute` and `--wait` when appropriate
7. Destructive operations with `--execute --force --confirm <id>`

## Read-only commands

```bash
python3 /home/stephen/Scripts/9b-proxmox-manager/proxmox-manager/scripts/Proxmox-cli.py --env-file /home/stephen/Scripts/9b-proxmox-manager/proxmox-manager/.env doctor
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py health
python3 /home/stephen/Scripts/9b-proxmox-manager/proxmox-manager/scripts/Proxmox-cli.py --env-file /home/stephen/Scripts/9b-proxmox-manager/proxmox-manager/.env nodes list
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py node status --node <node>
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py vms list --node <node>
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py vm status --node <node> --vmid 101
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py vm config --node <node> --vmid 101
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py vm guest-network --node <node> --vmid 101
python3 /home/stephen/Scripts/9b-proxmox-manager/proxmox-manager/scripts/Proxmox-cli.py --env-file /home/stephen/Scripts/9b-proxmox-manager/proxmox-manager/.env lxcs list --node <node>
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py lxc status --node <node> --vmid 201
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py storage list --node <node>
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py storage content --node <node> --storage local
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py network list --node <node>
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py tasks recent --node <node>
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py vms next-id
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py lxcs next-id
```

Use the node name returned by `nodes list`; example names are placeholders.

## Write commands

State-changing commands dry-run by default. Pass `--execute` to apply.

```bash
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py vm start --node <node> --vmid 101 --execute --wait
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py vm shutdown --node <node> --vmid 101 --execute --wait
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py vm reboot --node <node> --vmid 101 --execute --wait
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py vm snapshot create --node <node> --vmid 101 --name before-update --execute --wait
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py lxc start --node <node> --vmid 201 --execute --wait
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py lxc shutdown --node <node> --vmid 201 --execute --wait
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py lxc snapshot create --node <node> --vmid 201 --name before-update --execute --wait
```

## Destructive commands

These require `--execute --force --confirm <id>`:

```bash
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py vm stop --node <node> --vmid 101 --execute --force --confirm 101 --wait
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py vm delete --node <node> --vmid 101 --execute --force --confirm 101 --wait
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py vm snapshot delete --node <node> --vmid 101 --name before-update --execute --force --confirm 101 --wait
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py lxc stop --node <node> --vmid 201 --execute --force --confirm 201 --wait
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py lxc delete --node <node> --vmid 201 --execute --force --confirm 201 --wait
```

## LXC creation and SSH keys

```bash
# Generate an SSH key pair for a container
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py lxc ssh-keygen --vmid 110 --keyfile ~/.ssh/lxc-110 --execute

# Create a container and inject an existing public key
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py lxc create --node pve1 --vmid 110 \
  --hostname my-container \
  --ostemplate local:vztmpl/debian-12-standard.tar.zst \
  --storage local-lvm \
  --ssh-public-keys "ssh-ed25519 AAAAC3... user@host" \
  --execute --wait

# Create a container and generate + inject a fresh key on the fly
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py lxc create --node pve1 --vmid 110 \
  --hostname my-container \
  --ostemplate local:vztmpl/debian-12-standard.tar.zst \
  --storage local-lvm \
  --ssh-keygen --ssh-key-path ~/.ssh/lxc-110 \
  --execute --wait
```

## Console broker

`Proxmox-console.py` is an opt-in, tiered console broker. Configure allowed VMIDs in `~/.config/opencode/skills/proxmox-manager/.env`:

```dotenv
PROXMOX_CONSOLE_ALLOWED_VMIDS=101,102
PROXMOX_CONSOLE_TIER=1
PROXMOX_CONSOLE_REQUIRE_SNAPSHOT=true
PROXMOX_CONSOLE_SESSION_LOG_DIR=scripts/logs/console-sessions
PROXMOX_CONSOLE_KEY_DELAY=1.5
PROXMOX_CONSOLE_WATCH_INTERVAL=5.0
```

Tier 1 (read-only, no `--execute` required):

```bash
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-console.py doctor
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-console.py status --node <node> --vmid 101
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-console.py screenshot --node <node> --vmid 101
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-console.py read-text --node <node> --vmid 101
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-console.py watch --node <node> --vmid 101 --until-stable --timeout 300
```

Tier 2 (requires `--execute` and `PROXMOX_CONSOLE_TIER >= 2`):

```bash
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-console.py send-key --node <node> --vmid 101 --key Enter --execute
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-console.py send-keys --node <node> --vmid 101 \
  --keys "Space,Downx10,Space,Tab,Enter" --execute
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-console.py run-command --node <node> --vmid 101 --command "ip addr" --execute
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-console.py snapshot --node <node> --vmid 101 --name before-console --execute
```

Tier 3 (requires `--execute --force --confirm <vmid>` and `PROXMOX_CONSOLE_TIER >= 3`):

```bash
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-console.py type --node <node> --vmid 101 \
  --text "systemctl status ssh" --execute --force --confirm 101
```

Key input notes:

- `send-key` and `send-keys` normalize common aliases before sending: `Esc`, `Del`, `PageUp`, `PageDown`, `Windows`/`Win`, `[ctrl]+[alt]+[del]`, and `ctrl-alt-del` are accepted.
- Use VNC for BIOS/UEFI, boot menus, installers, and recovery screens only until SSH or QEMU guest-agent access is available.
- For preboot prompts with short timeouts, take a screenshot immediately before input and use short follow-up screenshot/watch intervals (`--interval 0.5 --timeout 5` to `10`) so changes are seen before the prompt expires.
- Prefer one verified navigation key at a time in firmware menus unless focus is visually confirmed.

Use VNC only until the guest has SSH or QEMU guest-agent access. After install, prefer SSH or `run-command` over VNC for shell commands; `send-keys` is for navigation keys, not arbitrary command text or passwords.

## Console broker on KVM-less nodes

If the Proxmox host lacks `/dev/kvm`, VMs must use TCG emulation (`kvm: 0`). Detect it with `doctor` or by checking `ls /dev/kvm` on the node. Increase `PROXMOX_CONSOLE_KEY_DELAY`, use `watch` for long waits, and use a minimal server install to keep install times reasonable.

## Raw API fallback

Use only for debugging or advanced workflows:

```bash
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py api get /version
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py api post /nodes/pve1/qemu/101/config --data '{"kvm":"0"}' --execute
python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py api delete /nodes/pve1/qemu/101 --execute --force --confirm /nodes/pve1/qemu/101
```

`api get` is allowed by default. `api post` and `put` require `--execute`. `api delete` additionally requires `--force --confirm <exact-endpoint>`. Use `--wait --wait-timeout 300` when a raw write returns a task ID.

## Configuration

Copy the example file and fill in real values:

```bash
cp ~/.config/opencode/skills/proxmox-manager/.env.example ~/.config/opencode/skills/proxmox-manager/.env
```

Required `.env` keys:

```dotenv
PROXMOX_API_URL=https://proxmox.example.local:8006/api2/json
PROXMOX_API_TOKEN_ID=agent@pve!opencode
PROXMOX_API_TOKEN_SECRET=replace-me
```

Optional `.env` keys:

```dotenv
PROXMOX_VERIFY_SSL=true
PROXMOX_TIMEOUT_SECONDS=30
PROXMOX_DEFAULT_NODE=
PROXMOX_OUTPUT_FORMAT=json
PROXMOX_LOG_DIR=scripts/logs
```

Never commit `.env`.

## Destructive action policy

- `vm stop`, `lxc stop`, and snapshot delete require `--execute --force --confirm <vmid>`.
- `vm delete` and `lxc delete` require `--execute --force --confirm <vmid>`.
- Raw `api delete` requires `--execute --force`.
- Bulk actions are not supported unless an explicit `--all` flag with `--confirm-all` is added in the future.
- State-changing commands dry-run by default.

## Logging

All actions are logged to `~/.config/opencode/skills/proxmox-manager/scripts/logs/` as JSONL files with names like `proxmox-cli-YYYYMMDD-HHMMSS.jsonl`. A `latest.jsonl` symlink points to the most recent log.

Console broker sessions write screenshots and JSONL session events to `~/.config/opencode/skills/proxmox-manager/scripts/logs/console-sessions/`.

Logs redact API token secrets, Authorization headers, passwords, and other sensitive values.

## Agent rules

- Always run `doctor` before making changes.
- Always read before writing; always dry-run before executing.
- Never expose token secrets, passwords, or VNC tickets.
- Never run state-changing commands without `--execute`.
- Never run destructive commands without `--execute --force --confirm <id>`.
- Always pass `--wait` for long-running tasks when you need to confirm completion.
- After a failed command, inspect the latest log file before retrying.
- Track bugs in `~/.config/opencode/skills/proxmox-manager/scripts/BUGS.md`.
- Track improvements in `~/.config/opencode/skills/proxmox-manager/scripts/IMPROVEMENTS.md`.

## Troubleshooting

- Run `python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py doctor`.
- Inspect `~/.config/opencode/skills/proxmox-manager/scripts/logs/latest.jsonl`.
- Verify the API URL, token ID, and token secret in `~/.config/opencode/skills/proxmox-manager/.env`.
- Ensure the API token has the required ACL permissions (e.g., VM.Audit, VM.PowerMgmt, VM.Console, VM.Snapshot, Datastore.Audit, Sys.Audit).
- If SSL verification fails, confirm the server certificate or set `PROXMOX_VERIFY_SSL=false` (lab only; warning will be shown).
- For console failures, confirm the VM is running and the VMID is in `PROXMOX_CONSOLE_ALLOWED_VMIDS`.

## Bug tracking

Track bugs in `~/.config/opencode/skills/proxmox-manager/scripts/BUGS.md`.

## Improvement tracking

Track improvements in `~/.config/opencode/skills/proxmox-manager/scripts/IMPROVEMENTS.md`.
