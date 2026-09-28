# IMPROVEMENTS.md

Track proposed improvements while developing or using `scripts/Proxmox-cli.py`.

## 2026-09-19 — VM in-guest command execution via QEMU Guest Agent API (`vm exec`) & Agent Diagnostics

- Status: implemented
- Area: cli | safety | api | tests
- Reason: Agents and operators need native in-guest execution for QEMU/KVM virtual machines without requiring network routing or manual SSH setup, using Proxmox's built-in QEMU guest agent endpoints (`/nodes/{node}/qemu/{vmid}/agent/exec` and `exec-status`), plus actionable diagnostic feedback when a VM lacks proper agent configuration or exposure.
- Suggested change: Added `vm exec` command in `scripts/Proxmox-cli.py` guarded by dry-run/`--execute`, polling `exec-status` until command exits or timeout expires. Added `_diagnose_vm_agent` diagnostic engine to inspect VM status and Proxmox configuration, clearly reporting whether the VM is stopped (`vm_stopped`), the agent option is disabled in Proxmox VM options (`not_configured_in_proxmox`), or the daemon is not running inside the guest OS (`not_running_in_guest`), complete with exact remedy instructions. Registered `vm_exec` in `chat.py`.
- Tradeoffs: Requires `qemu-guest-agent` installed and active inside the VM, with agent enabled in VM options (`agent: 1`).
- Related files: `scripts/Proxmox-cli.py`, `chat.py`, `tests/test_cli.py`, `tests/test_chat.py`

## 2026-09-19 — LXC termproxy interactive login escalation and SSH key auto-provisioning

- Status: implemented
- Area: cli | safety | api | tests
- Reason: LXC containers in Proxmox VE expose virtual TTY login prompts over termproxy. When an agent attempts in-guest execution on an unauthenticated container, it needs to escalate securely to the operator for credentials, log in, and auto-provision an SSH key so subsequent operations run seamlessly.
- Suggested change: Added console login detection (`login_required`), secure operator password prompting (`getpass`), authenticated shell transition, and automatic SSH public key provisioning in `lxc_exec` / `cmd_lxc_exec` and `ChatAgent.chat`. Passwords and secrets are strictly redacted from logs, CLI argv, and LLM message histories.
- Tradeoffs: Initial interactive prompt requires operator root credentials for that container once.
- Related files: `scripts/Proxmox-cli.py`, `chat.py`, `tests/test_cli.py`, `tests/test_chat.py`

## 2026-09-19 — LXC in-guest API exec and multi-target SSH exec

- Status: implemented
- Area: cli | safety | api | tests
- Reason: Agents need direct, reliable in-guest execution to configure Linux containers and VMs without brittle manual console typing or mandatory SSH key setup for containers.
- Suggested change: Added `lxc exec` (`POST /nodes/{node}/lxc/{vmid}/exec` with polling via `exec-status`) and `ssh exec` (with automatic IP resolution for VMs/LXCs, key discovery, and dry-run execution guards). Registered `lxc_exec` and `ssh_exec` tools in `chat.py`.
- Tradeoffs: `lxc exec` requires Proxmox VE 8+ API support and a running container. `ssh exec` requires SSH access/keys to the guest.
- Related files: `scripts/Proxmox-cli.py`, `chat.py`, `tests/test_cli.py`, `tests/test_chat.py`

## 2026-07-21 — Agent-oriented command plans and discovery

- Status: implemented
- Area: cli | safety | tests
- Reason: A constrained model needs deterministic syntax, machine-readable results, and a validated preview before write execution.
- Suggested change: Added `commands`, global JSON selection, required target IDs, validated redacted plans for create/download/raw writes, working raw DELETE guards, raw task waiting, and separate request/task timeout flags.
- Tradeoffs: Legacy aliases remain supported for compatibility but are no longer the recommended interface.
- Related files: `scripts/Proxmox-cli.py`, `tests/test_cli.py`, `README.md`, `AGENTS.md`, `SKILL.md`

## 2026-07-21 — Resource resolution and bounded lists

- Status: implemented
- Area: cli | tests
- Reason: Agents should not guess a node for a VMID or consume an unbounded resource/task list.
- Suggested change: Added `resolve --vmid <id> [--kind vm|lxc]`, plus `--status` and positive `--limit` filters for VM, LXC, and task lists. All JSON output now has stable command, target, data, dry-run, task, next-step, and log-path fields.
- Tradeoffs: The resolver makes one cluster-wide read and may return multiple matches in a malformed cluster.
- Related files: `scripts/Proxmox-cli.py`, `tests/test_cli.py`, `README.md`

## 2026-07-21 — Local-only doctor and SSH key actions

- Status: implemented
- Area: cli | docs | safety
- Reason: `doctor --live` was documented as opt-in but always made an API request, while local SSH key generation was a write action without an execution guard.
- Suggested change: `doctor` performs only local checks unless `--live` is supplied. `lxc ssh-keygen` now previews by default and requires `--execute`; it remains usable without API credentials.
- Tradeoffs: Existing callers that expected live `doctor` checks must add `--live`.
- Related files: `scripts/Proxmox-cli.py`, `AGENTS.md`, `SKILL.md`

## Template

```md
## YYYY-MM-DD — Short improvement title

- Status: proposed | accepted | implemented | rejected
- Area: cli | docs | logging | safety | tests | api
- Reason:
- Suggested change:
- Tradeoffs:
- Related files:
```

## 2026-07-13 — Preserve final screenshots and diagnostics on watch timeout

- Status: implemented
- Area: cli | workflow | logging
- Reason: `watch --until-stable --timeout 30` can fail with `Watch did not reach a stable screen before timeout` and `screenshot_path: null`, forcing the next agent to run a separate screenshot command to understand the current state.
- Suggested change:
  - Always capture and save the last frame on watch timeout, even when `--screenshots` is not set.
  - Include `seconds_since_change`, `stable_count`, `iterations`, session ID, and final screenshot path in the error output.
- Tradeoffs: Adds one screenshot write on timeout; worth it because timeout is already an exceptional path that needs context. OCR remains a future enhancement.
- Related files: `scripts/Proxmox-console.py`, `tests/test_console.py`

## 2026-07-13 — Document verified GRUB console navigation workflow

- Status: implemented
- Area: cli | workflow | tests
- Reason: Password-reset agents had trouble navigating GRUB. Logs show failures around boot timing (`watch` timeout while trying to catch GRUB), ad-hoc `grub_shift.py` helper use, and brittle assumptions about fixed line counts in the GRUB editor. Live testing on VM 3001 confirmed the console broker sends GRUB keys correctly, but long wrapped GRUB lines make fixed `DownxN` navigation unreliable without screenshots.
- Suggested change:
  - Use `Down`/`Up` with screenshots to verify menu selection before pressing `e`.
  - In the GRUB editor, do not assume a fixed number of `Down` presses reaches the `linux` line; long `search --fs-uuid` lines wrap and consume visual rows. Move in small batches, screenshot, then use a reversible single-character probe only when necessary and remove it with `BackSpace` before booting/discarding.
  - Confirmed safe sequence on VM 3001: `Down` moves menu selection, `Up,e` enters editor, `Downx11` reaches the wrapped `search` area, `Downx4,End` reaches the end of the `linux` line, `BackSpace,Escape` removes probe/discards edits, `c` opens the GRUB command prompt, `type --text "help"` enters text, `Ctrl+U` clears the prompt, and `Escape` returns to the menu.
  - Added explicit test coverage for GRUB-relevant navigation/editing keys: `End`, `Ctrl+E`, and `Ctrl+U`.
- Tradeoffs: The verified sequence is specific to VM 3001's current GRUB entry layout. Agents should still verify with screenshots/OCR after each navigation batch instead of blindly replaying the sequence.
- Related files: `scripts/Proxmox-console.py`, `tests/test_console.py`, `scripts/logs/console-sessions/20260713-160830-3001.png`, `scripts/logs/console-sessions/20260713-161047-3001.png`, `scripts/logs/console-sessions/20260713-161206-3001.png`

## 2026-07-13 — Add post-install bootstrap for SSH and guest-agent access

- Status: implemented
- Area: cli | workflow
- Reason: After the Ubuntu install, agents continued using fragile VNC terminal input and one API probe failed with `QEMU guest agent is not running`. Once the OS boots, SSH or QEMU guest-agent access is more reliable than VNC for shell commands.
- Suggested change:
  - Added `vm guest-network`, a read-only QEMU guest-agent interface query that returns a helpful install hint when unavailable.
  - Document the preferred workflow: use VNC only until network/SSH/guest-agent is ready, then switch away from VNC for shell commands.
- Tradeoffs: Guest-agent installation is guest-OS-specific and may require package-manager access; the helper is read-only and transparent.
- Related files: `scripts/Proxmox-cli.py`, `AGENTS.md`, `SKILL.md`

## 2026-07-13 — Replace placeholder node examples with discovery-first guidance

- Status: implemented
- Area: docs | workflow
- Reason: Recent logs show failed attempts against `pve1` and `pveheavy` before using the real node `pvestore`. The current docs use `pve1` heavily as an example, which encourages hard-coded node names.
- Suggested change: Discovery-first guidance is retained in the README and AGENTS guide; commands use `<node>` where the value must be discovered.
- Tradeoffs: Examples become slightly less copy/paste-ready, but agents should discover live topology first.
- Related files: `SKILL.md`, `AGENTS.md`, `README.md`, `.env.example`

## 2026-07-13 — Add verified console text/paste path for shell commands

- Status: implemented
- Area: cli | safety | workflow
- Reason: Logs and screenshots show character-by-character `send-keys` being used for passwords and shell commands, causing secret leakage and symbol-entry errors. Shell commands should not depend on fragile per-character VNC key events once a terminal is available.
- Suggested change: Added Tier 3 `paste-text`, which uses the guarded text path but rejects Enter characters and captures a screenshot for visual review before a separate `send-key Enter` submission.
- Tradeoffs: Verification adds latency and may require OCR/terminal-state assumptions; it is safer than silently submitting malformed shell commands.
- Related files: `scripts/Proxmox-console.py`, `tests/test_console.py`, `AGENTS.md`

## 2026-07-08 — SSH key generation and injection for LXC create

- Status: implemented
- Area: cli | safety
- Reason: The CLI had no way to generate SSH keys or inject them during LXC creation, requiring users to pass `--data '{"ssh-public-keys":"..."}'` manually with a pre-generated key.
- Suggested change:
  - Added `lxc ssh-keygen` subcommand to generate Ed25519 key pairs and print the public key.
  - Added `--ssh-public-keys` argument to `lxc create` for direct key injection.
  - Added `--ssh-keygen` and `--ssh-key-path` arguments to `lxc create` to generate a key on the fly and inject the public key during container creation.
  - SSH key secret values are redacted in logs by the existing `SENSITIVE_KEYS_RE` pattern.
- Tradeoffs: Key generation uses the `ssh-keygen` binary (must be on PATH); keys are Ed25519 only via the auto-generation path.
- Related files: `scripts/Proxmox-cli.py` (parser, `cmd_lxc_create`, `cmd_lxc_ssh_keygen`, main dispatch)

## 2026-07-08 — Dedicated --unprivileged/--nameserver/--onboot flags for LXC create

- Status: implemented
- Area: cli | docs
- Reason: `lxc create --features nesting=1` failed with a non-root API token when `unprivileged` was not explicitly set. Also, `--nameserver` and `--onboot` were only reachable via `--data`, making the Docker-in-LXC example verbose and easy to get wrong.
- Suggested change:
  - Added `--unprivileged` / `--no-unprivileged` flag to `lxc create` (defaults to `True`), so nesting works out of the box with API tokens.
  - Added `--nameserver` flag to `lxc create` (maps to `nameserver`).
  - Added `--onboot` / `--no-onboot` flag to `lxc create` (maps to `onboot`).
  - Added `--onboot` / `--no-onboot` flag to `vm create` for consistency.
  - Explicit flags are added to the request body before `--data` is merged, so `--data` can still override when needed.
  - Updated `AGENTS.md` examples to use the new first-class flags.
- Tradeoffs: Defaulting `unprivileged` to `True` changes the CLI from not sending the field to sending `unprivileged=1`; users who want a privileged container must now pass `--no-unprivileged`. This matches Proxmox's modern default and is more secure, but needs documentation.
- Related files: `scripts/Proxmox-cli.py` (`cmd_lxc_create`, `cmd_vm_create`, parsers), `AGENTS.md`

## 2026-07-08 — Route error output to stderr and success output to stdout

- Status: implemented
- Area: cli | logging
- Reason: The CLI printed both success and error output to stdout, which breaks standard Unix conventions and makes it hard for scripts/pipelines to distinguish failures. Automated agents and shell callers expect stderr for diagnostics and stdout for results.
- Suggested change:
  - Introduced `emit_output()` helper that renders `render_output()` and writes to `sys.stdout` when `ok=True` and `sys.stderr` when `ok=False`.
  - Introduced `emit_error()` helper that always writes rendered error markdown to `sys.stderr`.
  - Replaced all `print(render_output(...))` and `print(render_error_markdown(...))` call sites with the helpers.
  - Left non-error informational messages (e.g., Debian 13 console fix instructions) on stdout.
- Tradeoffs: Callers already parsing stdout on failure will need to read stderr instead; this is the correct behavior and aligns with CLI conventions. JSON-mode error output now goes to stderr as well, so `--format json 2> errors.json` works as expected.
- Related files: `scripts/Proxmox-cli.py` (render helpers, all command handlers), `scripts/BUGS.md`

## 2026-07-08 — Skip empty log files in `logs tail` / `logs latest`

- Status: implemented
- Area: logging
- Reason: `logs tail` and `logs latest` frequently returned the empty log file created by the current command (e.g., `logs tail` itself), resulting in no visible output or a path to an empty file.
- Suggested change:
  - Updated `latest_log_file()` to prefer the `latest.jsonl` symlink when it points to a valid, non-empty file.
  - When scanning, skip files with `st_size == 0` so only meaningful command logs are considered.
- Tradeoffs: A command that legitimately logs nothing will not be selected as "latest" even if it is the most recent by mtime. This is generally desirable for a log inspection command.
- Related files: `scripts/Proxmox-cli.py` (`latest_log_file`), `scripts/BUGS.md`

## 2026-07-09 — VNC console broker for VM console access (Phases 1-3)

- Status: implemented
- Area: cli | safety | logging
- Reason: The project had no safe way for agents to inspect or interact with VM consoles. Raw VNC access would expose credentials and full remote control, which is too risky for an agent harness.
- Suggested change:
  - Added `scripts/Proxmox-console.py` as a separate, opt-in broker.
  - Reused `.env` loading, config, logging, redaction, and API client helpers from `scripts/Proxmox-cli.py`.
  - Added `ConsoleConfig` with `PROXMOX_CONSOLE_ALLOWED_VMIDS`, `PROXMOX_CONSOLE_TIER`, `PROXMOX_CONSOLE_REQUIRE_SNAPSHOT`, and `PROXMOX_CONSOLE_SESSION_LOG_DIR`.
  - Implemented Phase 1 read-only commands: `doctor`, `status`, `screenshot`, and `read-text`.
  - Implemented Phase 2 useful commands: `send-key` (allowlist + `--execute`), `run-command` (QEMU guest agent + allowlist), and `snapshot` (`--execute`).
  - Implemented Phase 3 controlled commands: `type` (`--execute --force --confirm <vmid>` + snapshot guards + `--auto-snapshot`), `session --list`, and `session --stop`.
  - Enforced VM eligibility and tier guards; empty `PROXMOX_CONSOLE_ALLOWED_VMIDS` blocks all console operations with exit code `10`.
  - Implemented a lightweight standard-library VNC-over-websocket client for screenshots (BMP output), key events, and text typing, plus a serial console websocket reader for `read-text`.
  - Ensured VNC and serial tickets are redacted in logs and terminal output.
  - Added session event logging to `scripts/logs/console-sessions/` as JSONL.
  - Added `tests/test_console.py` with unit and end-to-end tests using the existing mock Proxmox server.
  - Updated `.env.example`, `AGENTS.md`, and `README.md` with console broker settings and examples.
- Tradeoffs: The built-in VNC client supports VNC authentication (DES) using the `password` field returned by `vncproxy`, and raw encodings. Screenshots are saved as PNG (with a `/tmp` review copy) and can be stabilized before capture. `run-command` uses QEMU guest agent; VMs without the agent or a restrictive agent plugin will return an API error.
- Related files: `scripts/Proxmox-console.py`, `tests/test_console.py`, `tests/mock_proxmox.py`, `.env.example`, `AGENTS.md`, `README.md`

## 2026-07-09 — URL-encode VNC/serial tickets and use `password` for VNC auth

- Status: implemented
- Area: cli | safety
- Reason: The console broker's VNC websocket handshake failed because the `vncticket` query parameter contained characters like `;` and `/`, which Proxmox's API rejected with `400 Parameter verification failed`. Additionally, the VNC auth password must be the `password` field returned by `vncproxy`, not the URL ticket.
- Suggested change:
  - URL-encode the `vncticket`/`termproxy` ticket in websocket paths using `urllib.parse.quote(..., safe="")`.
  - Use `vncproxy` response `password` for VNC authentication, falling back to the ticket only when absent.
  - Propagate the password through `_vnc_session`, `_capture_vnc_screenshot`, and the screenshot/send-key/type commands.
- Tradeoffs: None significant; this aligns with Proxmox's noVNC implementation and prevents ticket characters from being misinterpreted as URL path/query delimiters.
- Related files: `scripts/Proxmox-console.py`

## 2026-07-09 — PNG screenshots with framebuffer stabilization and review copies

- Status: implemented
- Area: cli | logging
- Reason: BMP screenshots from the initial console broker were large and slow to write/inspect. Capturing mid-transition frames also produced blurry or partial images during installer animations, and there was no quick way to inspect the latest capture outside the session log directory.
- Suggested change:
  - Replaced BMP output with PNG encoding via Pillow (`PIL.Image`), which reduced file sizes dramatically and is broadly available.
  - Added a framebuffer-stabilization loop in `request_screenshot()` that waits until two consecutive framebuffer updates are identical before capturing, avoiding transitional frames.
  - Wrote a review copy of every screenshot to `/tmp/console-<vmid>-<filename>.png` so operators can inspect the latest frame without browsing the session log directory.
- Tradeoffs: Adds a soft dependency on Pillow (already installed in the lab environment). The stabilization loop can take up to 5 seconds and may not converge if the screen contains a constantly updating progress bar.
- Related files: `scripts/Proxmox-console.py`

## 2026-07-09 — Configurable post-key and typing delay

- Status: implemented
- Area: cli
- Reason: The console broker hardcoded a 1.5-second pause after each key event. That was appropriate for slow TCG-emulated VMs (e.g., VM 107 with `kvm: 0`) but unnecessarily slow on KVM-accelerated VMs.
- Suggested change:
  - Added `PROXMOX_CONSOLE_KEY_DELAY` env var and a `--delay` flag to `send-key`, `send-keys`, and `type` that controls the pause after each key event.
  - Default remains 1.5s so safety is preserved, but operators can lower it for fast VMs or raise it for slow TCG emulation.
- Tradeoffs: Lower delays on slow VMs may cause dropped keystrokes or incomplete input; documentation recommends raising the delay for TCG emulation.
- Related files: `scripts/Proxmox-console.py`, `.env.example`, `AGENTS.md`

## 2026-07-09 — Send key sequences in a single VNC session

- Status: implemented
- Area: cli | safety
- Reason: Navigating the Debian installer tasksel menu required 14 separate `send-key` invocations, each opening a new VNC websocket session and taking ~1.5 seconds. This was fragile, slow, and produced many redundant session logs.
- Suggested change:
  - Added a `send-keys` subcommand that accepts a `--keys` argument with a comma-separated list of key names and sends them all in one VNC session.
  - Supports repetition syntax: `Downx10` expands to ten `Down` keys.
  - Only allows keys already in `SEND_KEY_ALLOWLIST` / `X11_KEYSYM`; keeps the same tier-2 `--execute` guard as `send-key`.
- Tradeoffs: The command is more powerful than a single key, so it is logged with the full sequence and requires `--execute`.
- Related files: `scripts/Proxmox-console.py`, `AGENTS.md`

## 2026-07-09 — Console watch / poll mode with prompt detection

- Status: implemented
- Area: cli | workflow
- Reason: During the Debian install the only way to know when a prompt appeared was to take repeated screenshots and visually inspect them. This consumed agent time and generated many screenshots.
- Suggested change:
  - Added a `watch` command that periodically screenshots the console, compares the latest frame to the previous one, and exits when the screen is stable.
  - Supports `--until-stable`, `--stable-cycles`, `--interval`, `--timeout`, and `--screenshots`.
  - Uses a single VNC session for the polling loop to avoid repeated websocket handshakes.
- Tradeoffs: Requires a reasonable timeout and may be CPU/network intensive on slow VMs. Text-based prompt detection is not yet implemented.
- Related files: `scripts/Proxmox-console.py`, `AGENTS.md`

## 2026-07-09 — Common installer tasksel presets

- Status: implemented
- Area: cli | workflow
- Reason: Manually navigating tasksel to select "SSH server" and deselect "Debian desktop environment" is error-prone and slow over VNC. A declarative preset would be safer and faster for common server installs.
- Suggested change: Added the versioned `debian-13-server-v1` `install-preset` with the macro safety guards. It requires an explicit `--tasksel-focus-confirmed` acknowledgement after visual confirmation of the documented stock layout.
- Tradeoffs: Presets are installer-specific; they may break if Debian changes the tasksel UI order. Keep them minimal and versioned.
- Related files: `scripts/Proxmox-console.py`, `tests/test_console.py`, `AGENTS.md`

## 2026-07-09 — Document TCG emulation behavior and recommended workflows

- Status: implemented
- Area: docs
- Reason: The Proxmox lab node lacks KVM, so VMs must run with `kvm: 0` (TCG software emulation). Installations are very slow and console operations need longer delays; the desktop tasksel option would have taken an impractical amount of time.
- Suggested change:
  - Added a "KVM-less nodes" section to `AGENTS.md` explaining how to detect `/dev/kvm` absence, set `kvm: 0`, and adjust console delay and software selection accordingly.
  - Recommend minimal tasksel selections (standard utilities + SSH server) for TCG VMs to keep install times manageable.
- Tradeoffs: None; this is documentation-only and helps future agents avoid repeating the slow desktop-install path.
- Related files: `AGENTS.md`, `README.md`, `scripts/BUGS.md`

## 2026-07-10 — Redact `--text` and `--actions` values in command logs

- Status: implemented
- Area: logging | safety
- Reason: The `type` and new `macro` commands accept arbitrary text that can include passwords. The existing `SENSITIVE_ARGV_FLAGS` set did not cover these flags, so typed secrets appeared in `scripts/logs/proxmox-cli-*.jsonl`.
- Suggested change:
  - Added `--text` and `--actions` to `SENSITIVE_ARGV_FLAGS` in `scripts/Proxmox-cli.py`.
  - The value following these flags is replaced with `***REDACTED***` before the JSONL log entry is flushed.
  - Added unit tests `test_redact_argv_text` and `test_redact_argv_actions` in `tests/test_cli.py`.
- Tradeoffs: The literal command text is no longer visible in command logs, but the length and action count are still logged in the console session events. Users who need to debug typed text can use `--no-log`.
- Related files: `scripts/Proxmox-cli.py`, `tests/test_cli.py`

## 2026-07-10 — `mouse-move` diagnostic command

- Status: implemented
- Area: cli | workflow
- Reason: Console mouse clicks did not register in the Ubuntu installer, and there was no easy way to test whether pointer events reached the guest at all.
- Suggested change:
  - Added a `mouse-move` subcommand to `scripts/Proxmox-console.py` (Tier 2, requires `--execute`).
  - Moves the VNC pointer to the requested `(x, y)` coordinates and captures a stable screenshot so operators can inspect whether the guest cursor moves.
- Tradeoffs: Does not automatically detect cursor movement; requires visual comparison of screenshots. Does not click, so it cannot trigger UI actions.
- Related files: `scripts/Proxmox-console.py`, `AGENTS.md`

## 2026-07-10 — `macro` command for action sequences

- Status: implemented
- Area: cli | workflow
- Reason: Navigating the Ubuntu OOBE required many separate `type`/`send-key` commands, each opening a new VNC session and adding delay.
- Suggested change:
  - Added a `macro` subcommand (Tier 3, requires `--execute --force --confirm <vmid>`) that accepts a JSON list of actions via `--actions` or `--file`.
  - Supported actions: `key`, `keys`, `type`, `click`, `move`, `wait`.
  - All actions execute in a single VNC session, reducing handshake overhead and session log clutter.
  - Supports the same `--snapshot`, `--auto-snapshot`, and `--unsafe` guards as `type`.
- Tradeoffs: The JSON action list is redacted from command logs via `--actions` in `SENSITIVE_ARGV_FLAGS`; debugging requires reading the macro file or using `--no-log`. Complex macros can be hard to debug if a step fails.
- Related files: `scripts/Proxmox-console.py`, `AGENTS.md`

## 2026-07-10 — `watch --until-unchanged-for` duration option

- Status: implemented
- Area: cli | workflow
- Reason: The installer progress screen changed continuously for many minutes, causing `watch --until-stable` to time out even though the install was proceeding normally.
- Suggested change:
  - Added `--until-unchanged-for SECONDS` to the `watch` command.
  - When set, `watch` exits only after the screen has remained unchanged for at least the specified duration (in addition to the existing `--stable-cycles` requirement).
- Tradeoffs: Slightly longer wait times, but fewer false timeouts on long-running phases.
- Related files: `scripts/Proxmox-console.py`, `AGENTS.md`

## 2026-07-13 — Improve mouse diagnostics and reliability

- Status: implemented
- Area: cli | console | workflow
- Reason: Live testing on VM 3001 showed VNC pointer movement does reach the Ubuntu desktop, but diagnostics are weak. Parallel `mouse-move` invocations can overlap VNC sessions, and successful commands can return `screenshot_path: null` if the post-action stable screenshot capture returns no frame. Operators must visually compare screenshots to confirm cursor position.
- Suggested change:
  - Added a `mouse-test` command that runs sequential moves to known coordinates in one VNC session, captures after screenshots for every step, and returns a per-step result list.
  - Added fallback screenshot capture for mouse input commands so a stable-capture failure retries a single non-stable frame before returning `screenshot_path: null`.
  - Returned framebuffer dimensions, clamped coordinates, and screenshot mode in `mouse-move` and `click` output to make coordinate mistakes obvious.
  - Documented `mouse-test` as the preferred mouse diagnostic instead of parallel mouse commands.
- Follow-up: Add optional image-diff/cursor detection between before/after screenshots and report `cursor_moved_detected: true|false|unknown`.
- Tradeoffs: Cursor detection is not implemented yet because it can be unreliable across themes and hidden cursors; the new sequential test and per-step screenshots remove the most immediate ambiguity.
- Related files: `scripts/Proxmox-console.py`, `tests/test_console.py`, `AGENTS.md`

## 2026-09-28 — Separate exec transports from console attachment, and confine node access

- Status: implemented
- Area: cli | safety | docs | tests
- Reason: `lxc exec` conflated two different things. Guest SSH and node-side `pct exec` are execution mechanisms; termproxy is a terminal *attachment* mechanism with no ownership boundary. A single shared pty cannot be driven by an agent safely: measured on pveheavy/170, the prompt appeared on 2 of ~20 attachments and never echoed input, so an agent that attaches may be typing into a human's live shell and parsing their screen as output. Separately, the Proxmox REST API has no exec endpoint for LXC, so the only sound out-of-band path is `pct exec` on the node — which is a far more valuable target than a guest and needed its own guard.
- Suggested change:
  - Split the transport decision: SSH first, `pct exec` second, console only on explicit `--console`.
  - Added `lxc pct-exec`, with the remote command assembled in exactly one place as `pct exec <vmid> -- /bin/sh -lc <command>` and shlex-quoted, so callers cannot influence the node, the `pct` invocation, or the shell.
  - Added `doctor --node-access`, which probes each node with `pct version` and reports whether `pct exec` is actually usable, with the reason when not. `doctor` now always reports which of the three transports are available.
  - Added `validate_harness_command()`, called from `LoopWatchdog.check_call()` — which runs *before* the approval prompt, so it cannot be satisfied by typing `y` or by auto-approve. It refuses rather than sanitises, and is repeated in the executor because that is reachable directly.
  - `cmd_ssh_exec` now refuses Proxmox node targets, closing the route around the `pct exec` allowlist.
  - `lxc create --as-template` closes the template-authoring gap that left clones with no way in.
- Tradeoffs: Containers with no guest SSH and no node access are now unreachable to the agent, where previously the console would be tried and would usually type into someone's session. That is the intended trade: an honest refusal beats an unreliable and unsafe one. Node SSH also means ProxBot can hold root on a hypervisor, which is why it is off by default and separately gated.
- Follow-up: `PROXMOX_NODE_SSH_TARGETS` is currently all-or-nothing per target. Consider scoping it per command class, and consider an allowlist of `pct` subcommands rather than only subcommand-prefix checks.
- Related files: `src/proxbot/cli/commands/nodessh.py`, `src/proxbot/cli/commands/exec.py`, `src/proxbot/chat/watchdog.py`, `src/proxbot/chat/executor.py`, `src/proxbot/chat/spec.py`, `.env.example`, `tests/test_cli.py`, `tests/test_chat.py`
