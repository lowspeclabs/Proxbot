# Historical Plan: VNC Console Broker for Proxmox VMs

> Historical design document. The console broker has been implemented; use `AGENTS.md` or `SKILL.md` for the current operational interface.

> TL;DR — Add a secondary CLI script, `scripts/Proxmox-console.py`, that acts as a **broker** between an agent harness and Proxmox VM consoles. The agent never receives raw VNC access. Instead, the broker exposes safe, guard-railed tools and logs every interaction.

## 1. Why a broker instead of raw VNC?

VNC / noVNC / SPICE are full remote-control interfaces: mouse, keyboard, and clipboard can all be sent to the guest. A raw VNC connection is effectively handing an unpredictable junior admin a keyboard at the physical machine. The broker model prevents this by:

- exposing only narrow, read-first operations by default,
- requiring explicit approval for any keyboard input,
- enforcing snapshot-before-change policies,
- recording sessions and command logs in `scripts/logs/`,
- never leaking VNC tickets or credentials.

## 2. New script

```text
scripts/Proxmox-console.py
```

`Proxmox-console.py` is a separate, deliberately focused script so that:

- console access is opt-in and easy to audit,
- the main `Proxmox-cli.py` remains the safe, read-only-first entry point,
- console-specific configuration and safety tiers can be isolated.

Both scripts share the same `.env` loader, config model, redaction, and logging helpers defined in `scripts/Proxmox-cli.py` (recommended: extract a `lib/` module later, but phase one can import the shared utilities from the existing script).

## 3. Proxmox API primitives

The broker uses two Proxmox API mechanisms:

1. **VNC ticket** — `POST /nodes/{node}/qemu/{vmid}/vncproxy` returns a ticket and optional websocket endpoint. This is only used by the broker to establish the session; the agent never sees the ticket.
2. **SPICE / noVNC** — Proxmox provides a websocket VNC proxy. The broker can capture a screenshot (via an external VNC client) or buffer keyboard input and send it through the VNC session.

Important: The broker should **never** print the ticket or open an unauthenticated VNC port. Tickets should be obtained per-command and discarded immediately.

## 4. Permission tiers

The broker supports four permission tiers. The active tier is controlled by an environment variable or CLI flag.

### Tier 1 — Safe (default)

No keyboard control. The agent can:

```text
console screenshot --node pve1 --vmid 101
console read-text --node pve1 --vmid 101
console status --node pve1 --vmid 101
```

- `screenshot` captures the current VM display and saves it as a PNG. The agent receives the file path, not the raw pixel stream.
- `read-text` captures serial console text (if the VM has a serial device) or OCR output from the screenshot.
- `status` returns whether the VM is running, whether a console session is active, and recent snapshot state.

### Tier 2 — Useful

Approved single actions with logging. The agent can:

```text
console send-key --node pve1 --vmid 101 --key Enter
console send-key --node pve1 --vmid 101 --key Ctrl+C
console run-command --node pve1 --vmid 101 --command "ls -la" --execute
```

- `send-key` only accepts an allowlist of keys: `Enter`, `Tab`, `Ctrl+C`, `Ctrl+D`, `Escape`, `Up`, `Down`, `Left`, `Right`, `Space`, and printable ASCII single characters.
- `run-command` is only allowed when the VM is known to have a command-channel agent (e.g., QEMU guest agent, serial SSH, or cloud-init). It is **not** a free-type tool; the command is checked against an allowlist of safe diagnostics.

### Tier 3 — Controlled

Full keyboard input is permitted only when:

```text
console type --node pve1 --vmid 101 --text "systemctl status ssh" --execute --force --confirm 101
```

- A snapshot named `auto-before-console-<timestamp>` already exists for the VM, or the command automatically creates one first.
- The session is recorded (screenshots + key log).
- The command is logged.
- The `--force` and `--confirm <vmid>` destructive-operation guards are satisfied.
- Network egress on the VM is restricted (outside the scope of the script, but configuration enforces this expectation).
- Secrets and passwords are masked in the text input and in logs.

### Tier 4 — Disallowed

The broker will refuse to:

- open a raw, interactive VNC/SPICE session to the agent,
- expose the host Proxmox console,
- allow unrestricted paste,
- operate on VMs not explicitly tagged as `console-allowed` in `.env` or the command args,
- run on production VMs.

## 5. Proposed CLI commands

```text
python scripts/Proxmox-console.py doctor
python scripts/Proxmox-console.py screenshot --node pve1 --vmid 101
python scripts/Proxmox-console.py read-text --node pve1 --vmid 101
python scripts/Proxmox-console.py status --node pve1 --vmid 101
python scripts/Proxmox-console.py send-key --node pve1 --vmid 101 --key Enter --execute
python scripts/Proxmox-console.py type --node pve1 --vmid 101 --text "systemctl status ssh" --execute --force --confirm 101
python scripts/Proxmox-console.py run-command --node pve1 --vmid 101 --command "ip addr" --execute
python scripts/Proxmox-console.py snapshot --node pve1 --vmid 101 --name before-console --execute
python scripts/Proxmox-console.py session --node pve1 --vmid 101 --list
python scripts/Proxmox-console.py session --node pve1 --vmid 101 --stop --session-id <id> --execute --force --confirm <id>
```

### Command semantics

- `screenshot`, `read-text`, and `status` are read-only.
- `send-key` is a Tier 2 write; requires `--execute` and stays within the allowlist.
- `type` is a Tier 3 write; requires `--execute --force --confirm <vmid>` and a pre-existing snapshot.
- `run-command` delegates to a safer channel (guest agent or SSH) if available; never uses VNC keystrokes unless the VM is explicitly a disposable lab VM and all Tier 3 guards are met.
- `snapshot` creates a snapshot before any risky console work.
- `session --list` and `session --stop` let a human inspect or terminate active broker sessions.

## 6. Safety guardrails

### 6.1 VM eligibility

Only VMs explicitly allowed in the console configuration are operable. Examples:

```dotenv
# .env
PROXMOX_CONSOLE_ALLOWED_VMIDS=101,102
PROXMOX_CONSOLE_TIER=1
PROXMOX_CONSOLE_REQUIRE_SNAPSHOT=true
PROXMOX_CONSOLE_SESSION_LOG_DIR=scripts/logs/console-sessions
```

The CLI will refuse any console action on a VM not in `PROXMOX_CONSOLE_ALLOWED_VMIDS` with exit code `10` (`EXIT_SAFETY`).

### 6.2 Tier enforcement

- Tier 1 commands always work on allowed VMs.
- Tier 2 commands require `--execute` and a configured tier of `2` or higher.
- Tier 3 commands require `--execute --force --confirm <vmid>`, a configured tier of `3`, and a recent snapshot (or auto-snapshot creation).
- Tier 4 operations are blocked by design.

### 6.3 Snapshot-before-change

For Tier 3, the broker either:

1. verifies a snapshot matching `auto-before-console-*` or a user-specified snapshot exists within the configured retention window, or
2. creates one automatically when `--auto-snapshot` is passed, then waits for the task to complete before typing.

### 6.4 Input allowlist

- `send-key` accepts only the keys listed in Tier 2.
- `type` accepts only printable ASCII text and a small set of escaped control sequences (e.g., `\n`, `\t`).
- `type` rejects lines that contain shell metacharacters or credential patterns unless explicitly approved with `--unsafe` (which itself requires a second confirmation flag and logs a warning).

### 6.5 Secret redaction

All log entries must redact VNC tickets, passwords, and any text matching `SENSITIVE_KEYS_RE`. Re-use the redaction helpers already present in `scripts/Proxmox-cli.py`.

### 6.6 Session recording

For Tier 2 and Tier 3:

- each action is written as a JSONL event to `scripts/logs/console-sessions/`,
- screenshots are saved to `scripts/logs/console-sessions/YYYYMMDD-HHMMSS-<vmid>.png`,
- key/tap logs are saved in the same JSONL file with the typed text and timestamp.

## 7. Suggested implementation phases

### Phase 1 — Read-only broker

1. Create `scripts/Proxmox-console.py` using the existing `Config`, `ProxmoxClient`, `Logger`, and formatter helpers from `Proxmox-cli.py`.
2. Implement:
   - `doctor`
   - `status`
   - `screenshot` (using a lightweight VNC client or direct websocket frame capture)
   - `read-text` (serial console text if available, else screenshot OCR)
3. Add `.env` variables: `PROXMOX_CONSOLE_ALLOWED_VMIDS`, `PROXMOX_CONSOLE_TIER`, `PROXMOX_CONSOLE_SESSION_LOG_DIR`.
4. Add tests verifying eligibility checks and logging.

### Phase 2 — Tier 2 useful actions

1. Implement `send-key` with the key allowlist and `--execute` guard.
2. Implement `run-command` using the QEMU guest agent or a serial SSH channel when available.
3. Add session recording and per-action screenshots.

### Phase 3 — Tier 3 controlled typing

1. Implement `type` with the `--execute --force --confirm <vmid>` guard and snapshot verification.
2. Add `--auto-snapshot` to create the rollback snapshot before typing.
3. Implement `session --list` and `session --stop` so a human can monitor and kill active sessions.

### Phase 4 — Integration and documentation

1. Update `AGENTS.md` with console command examples and tier rules.
2. Add entries to `scripts/IMPROVEMENTS.md`.
3. Update `README.md` to mention the console broker.
4. Add tests and run the safest available test suite.

## 8. Configuration additions

Add these keys to `.env.example` (with placeholder values):

```dotenv
# Console broker settings (optional, but recommended for safety)
PROXMOX_CONSOLE_ALLOWED_VMIDS=101,102
PROXMOX_CONSOLE_TIER=1
PROXMOX_CONSOLE_REQUIRE_SNAPSHOT=true
PROXMOX_CONSOLE_SESSION_LOG_DIR=scripts/logs/console-sessions
```

Rules:

- If `PROXMOX_CONSOLE_ALLOWED_VMIDS` is empty, the console broker refuses all operations with exit code `10`.
- `PROXMOX_CONSOLE_TIER` defaults to `1` (read-only).
- If `PROXMOX_CONSOLE_REQUIRE_SNAPSHOT=true`, Tier 3 commands require a recent snapshot.

## 9. Logging and output format

Use the same JSONL format as the main CLI. Each console log entry should include:

```json
{
  "timestamp": "2026-07-09T12:00:00-04:00",
  "event": "console_type",
  "argv": ["type", "--node", "pve1", "--vmid", "101"],
  "dry_run": false,
  "node": "pve1",
  "vmid": 101,
  "tier": 3,
  "action": "type",
  "input_length": 24,
  "snapshot": "auto-before-console-20260709-115800",
  "session_id": "sess-20260709-120000-abc123",
  "ok": true,
  "screenshot_path": "scripts/logs/console-sessions/20260709-120000-101.png",
  "error": null
}
```

The `input_length` field is logged, not the raw text, to reduce secret exposure. Text may be included in a separate redacted/masked field only when necessary.

Output formats should match the main CLI: `md`, `json`, `table`. Errors go to `stderr`; results go to `stdout`.

## 10. Exit codes

Re-use the existing exit codes from `scripts/Proxmox-cli.py`:

```text
0   Success
1   Validation error
2   Missing config
3   Auth failure
4   API error
5   Timeout
10  Safety guard blocked operation
99  Unexpected internal error
```

Add a new internal error category only if needed; otherwise map console-specific failures to `EXIT_SAFETY` (10) or `EXIT_API` (4).

## 11. Acceptance criteria

- `scripts/Proxmox-console.py doctor` reports console-specific config and eligibility status.
- `screenshot` and `read-text` work in Tier 1 on allowed VMs without `--execute`.
- `send-key` rejects out-of-allowlist keys even with `--execute`.
- `type` dry-runs by default; executing requires `--execute --force --confirm <vmid>` and a snapshot.
- All console commands log to `scripts/logs/` with redacted VNC tickets and secrets.
- VMs not listed in `PROXMOX_CONSOLE_ALLOWED_VMIDS` are rejected with exit code 10.
- No raw VNC ticket or websocket URL is ever printed to stdout or stderr.
- `AGENTS.md`, `README.md`, and `scripts/IMPROVEMENTS.md` are updated.

## 12. Files to create or modify

Create:

```text
scripts/Proxmox-console.py
```

Modify:

```text
.env.example
AGENTS.md
README.md
scripts/IMPROVEMENTS.md
```

Optionally refactor later:

```text
scripts/Proxmox-cli.py   # extract shared helpers into scripts/lib/ or keep imports
```

## 13. References

- IBM Cloud, *Accessing virtual server instances by using VNC or serial console* — https://cloud.ibm.com/docs/vpc?topic=vpc-vsi_is_connecting_console
- Proxmox VE API: `POST /nodes/{node}/qemu/{vmid}/vncproxy`
- Existing safety model: `AGENTS.md` §Write Command Rules and `scripts/Proxmox-cli.py` §Safety guards

---

*Plan created for `proxmox-opencode-lab`.*
