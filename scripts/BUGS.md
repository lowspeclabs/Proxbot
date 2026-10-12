# BUGS.md

Track bugs discovered while developing or using `scripts/Proxmox-cli.py`.

## Template

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

## Status

Known open issues are listed below.

### 2026-10-12 — Chat looped on read-only `lxc_config` when asked to set a static IP, then hit the turn limit

- Status: fixed
- Found by: human
- Command: chat "create a lxc from the debain-template already created, set its ip to 192.168.1.79 ..."
- Expected: Clone the template, set the static IP, start the container, SSH in, install nginx.
- Actual: After a successful `lxc_clone`, the model called read-only `lxc_config` with identical arguments 6+ times (empty reasoning), hit duplicate-suppression and then "Maximum tool executions per turn (12)". `lxc_start`/SSH/nginx never attempted.
- Suspected cause: No chat tool could write LXC config — `lxc_config` is GET-only, `lxc_clone` takes no `net0`, and the generic `api put` CLI path was not exposed to the model. The executor also silently drops extra args for `lxc_config`, so every retry collapsed to the same watchdog signature. The prompt additionally told the model to always pass `--storage` to `lxc_clone`, which fails for linked clones.
- Fix notes: Added `lxc set-config` CLI (PUT `/nodes/{node}/lxc/{vmid}/config`, `--execute` guarded) and `lxc_set_config` chat tool; `lxc_config` documented read-only. Prompt now says to omit `--storage` for linked clones and to use `lxc_set_config` for IP/hostname/DNS. Agent loop now stops the turn after 2 consecutive suppressions instead of burning all 12 calls.
- Related log file: `scripts/logs/chat-sessions/chat-20261012-085446-17f87b.jsonl`

### 2026-10-12 — Fresh LXC creation did not require guest credentials

- Status: fixed
- Found by: human
- Command: chat `lxc_create` from a Debian OS tarball without a password or SSH key
- Expected: Ask the operator for a root password before creation; recommend SSH public-key access.
- Actual: The container was created with neither configured, leaving no known operator credential and SSH root authentication failed.
- Suspected cause: Password was optional in the chat schema and CLI handler; the creation flow did not guard against its omission.
- Fix notes: Chat instructions now require the harness's secure `getpass` prompt (not a password pasted into chat); executor passes the password over an inherited FD, and the CLI create handler rejects missing passwords. SSH key injection remains optional and is explicitly recommended. Clones are unaffected.
- Related log file: `scripts/logs/chat-sessions/chat-20261012-063748-c6180b.jsonl`

### 2026-09-28 — `ssh exec` let the model reach a Proxmox node, bypassing every exec allowlist

- Status: fixed
- Found by: agent
- Command: `ssh exec --host pveheavy --command "<anything>" --execute` (reachable from the model as the `ssh_exec` tool)
- Expected: The model cannot obtain a host shell on a hypervisor node.
- Actual: `ssh_exec` is a generic tool with a `host` parameter, so the model could aim SSH at a cluster node and run any command. Under `CHAT_AUTO_APPROVE=true` there was no prompt at all. This made the `pct exec` allowlist decorative — the model could route straight around it with a plain SSH call.
- Suspected cause: No distinction was drawn between a guest target and a hypervisor target. The tool predates any notion of node access being privileged.
- Fix notes: `cmd_ssh_exec` now refuses a target that resolves to a cluster node, matched against node names, their API-resolved addresses, and the configured default node. Matching is case-insensitive and covers FQDN forms (`pveheavy.lan`, `192.168.1.65.lan`), which would otherwise bypass an equality check. Refusal returns `node_target_refused` with `EXIT_SAFETY` and names `lxc pct-exec` as the sanctioned path. Targets listed in `PROXMOX_NODE_SSH_TARGETS` are never blocked; guests are unaffected.
- Related log file: N/A

### 2026-09-28 — Console output loss was reported as a successful command

- Status: fixed
- Found by: agent
- Command: Any `lxc exec` that reached the termproxy fallback
- Expected: An unverifiable result is reported as unknown.
- Actual: Two fail-open paths. If the trailing `:$?` was not captured, `exit_code` silently defaulted to `0`, telling the agent a failed command had succeeded. If a boundary marker was missing, the function returned `None`, reported as "Could not establish a termproxy console session" — false, since the session had worked and only the result was unreadable.
- Suspected cause: Fail-open defaults in the output parser, and overloading `None` for both "transport never came up" and "transport worked, result lost".
- Fix notes: New `_parse_console_output()` reports `output_lost` with `exit_code: null` when the start marker, end marker, or exit status is missing, keeping partial output in `observed`. The exit status is matched anchored to the marker, so trailing scrollback such as `prompt:12` cannot be misread as the exit code. `output_lost` is distinct from `console_unavailable`, and the transport-failure message no longer asserts a session problem it cannot know about.
- Related log file: N/A

### 2026-09-28 — termproxy was an automatic exec fallback onto a shared pty

- Status: fixed
- Found by: agent
- Command: `lxc exec` when guest SSH was unavailable, before `--console` existed
- Expected: The agent does not attach to a terminal it may not own.
- Actual: When SSH failed, `lxc exec` attached to the container console without asking. The LXC console is a single shared pty, so the attach can land on a human's live shell: ProxBot would type into it, append its SSH public key to that account, and hand the terminal's scrollback to the model as "command output". The prompt test is a generic `#`/`$` terminator, so it cannot tell whose session it found.
- Suspected cause: The console was treated as a transport rather than as a shared interactive resource. Measured on pveheavy/170: the prompt appeared on only 2 of ~20 attachments, and on 0 of 13 with no other session holding the console.
- Fix notes: `--console` is now required. Without it, `lxc exec` tries SSH and then returns `console_not_requested`, naming `--console` and `pct exec` as the alternatives. An absent `console` attribute means "not requested", so older `Namespace` callers cannot silently re-enable it. The dry-run plan lists the transports that will actually be used and is labelled `LXC EXEC` rather than `TERMPROXY`, since SSH is the primary path.
- Related log file: N/A

### 2026-09-28 — `lxc clone` has no `--ssh-public-keys`, so clones come up unreachable

- Status: fixed
- Found by: agent
- Command: `lxc clone --node pveheavy --vmid 20300 --new-vmid 170 --execute`
- Expected: A clone of a template with a baked-in SSH key arrives reachable.
- Actual: Clones of template 20300 had no way in: no SSH key, no console login (see the getty entry below), and an address that collided with another node. `lxc create` supports `--ssh-public-keys`/`--ssh-keygen`, but `lxc clone` exposes only `--hostname --storage --pool --snapname`, so a key can only be baked in at create time.
- Suspected cause: Template authoring had no supported path — converting a container to a template was reachable only through the raw API.
- Fix notes: `lxc create --as-template` converts the new container after creation and prints the clone command to use it. The dry-run plan shows the follow-up `POST /nodes/{node}/lxc/<vmid>/template` call. With a stock `debian-13-standard` tarball this yields a base template whose clones arrive with working guest SSH, needing neither a console login nor node-side `pct exec`.
- Related log file: N/A

### 2026-09-28 — LXC console exec reports "incorrect password" without ever testing a credential

- Status: fixed
- Found by: human
- Command: `./chat.py` → `lxc_exec` against a container whose tty1 has no getty
- Expected: Either a successful console login, or an error that names the actual reason.
- Actual: `Console login failed (incorrect password or login timed out).` after ~13 s, repeated on every call. A manual test of the same password worked, so the diagnosis pointed at the operator.
- Suspected cause: The code classified "no credential prompt received" as "needs a password". The chat harness then prompted via `getpass`, the retry sent `root\r\n` and the password into a console with no prompt to answer, and the failure was reported as a bad credential. No credential was ever validated. The same misclassification on the password-less probe produced a bogus `login_required`, which is what triggered the prompt in the first place.
- Investigation (container 170 on pveheavy, pve-manager 9.1.5): the console is a **single shared pty whose prompt is not reliably replayed on attach**. A prompt was observed on only **2 of ~20 attachments**, and on **0 of 13** with no other session holding it. `Sec-WebSocket-Protocol` is *not* a factor — pveproxy accepts and echoes `binary`, `xtermjs` and `novnc`, and the one successful attach requested no subprotocol at all. Input echo was never observed: Enter, Ctrl-C and `root` produced no bytes back on any attach. `getty@tty1.service` is **inactive**; `agetty` runs on `pts/0` and `pts/2` instead, so the browser console and any automated client are not necessarily on the same pty. A *stopped* container (20300) echoes `lxc-console: 20300 is not running`, which confirms the transport itself is healthy.
- Fix notes: `src/proxbot/cli/commands/exec.py` — the console probe now waits on an idle deadline instead of a frame count; a missing credential prompt is reported as the distinct status `console_unavailable`; `login_failed` distinguishes a rejected credential (`login incorrect` / PAM denial) from "accepted but no shell prompt within 4s"; a termproxy transport failure also maps to `console_unavailable` instead of `login_required`, so a broken socket cannot trigger a pointless password prompt. The chat harness only escalates to `getpass` on `login_required`, so a console that yields no prompt no longer re-prompts on every call. `src/proxbot/chat/agent.py` — the prompt no longer hardcodes `root`; it uses `args.get("user") or "root"`, matching the CLI default. Added `cmd_lxc_exec`, `lxc_exec` to `KNOWN_CHANGES` in `tests/verify_cli_equivalence.py`.
- Follow-up: the `is_active_shell` branch remains a hazard. When ProxBot *does* win the race and matches a live prompt, it types into whatever session holds that pty — a human's shell, possibly — and parses that screen as command output. It cannot distinguish the two. The console should be demoted to an explicit opt-in, and automated exec should move to `pct exec` (out-of-band, never touches the pty). Not yet done.
- Related log file: `scripts/logs/chat-sessions/chat-20260928-152857-82994d.jsonl`

### 2026-09-28 — `lxc exec` SSH fallback ran commands on the Proxmox host instead of the container

- Status: open
- Found by: agent
- Command: `lxc exec --node pveheavy --vmid 170 --command "hostname" --execute`
- Expected: Output from container 170.
- Actual: Output from the **Proxmox node `pvestore`**: `hostname` → `pvestore`, `ls /sys/class/net` → host NICs (`eno1-4`, `vmbr0`, `tap120i0`, `fwbr*`), `df -h /` → `rpool/ROOT/pve-1` (host ZFS dataset). The result was reported as a successful container exec, and the chat agent then spent several turns debugging a non-existent "eth0 missing in the guest" problem. ProxBot also wrote its own public key into `pvestore`'s `/root/.ssh/authorized_keys` (4 `*-proxbot` entries) via the key-injection step.
- Suspected cause: `_resolve_guest_ip` returns the first non-loopback address from `GET /nodes/{node}/lxc/{vmid}/interfaces` without verifying that the address still belongs to that container. Container 170 was DHCP-assigned `192.168.1.65/24`, which is a **static address owned by the peer node `pvestore`** (`pveheavy` is `192.168.1.55`). With a duplicate address on the segment, SSH to `192.168.1.65` reaches whichever host answers ARP — `pvestore`.
- Fix notes: Not yet fixed. `_resolve_guest_ip` should validate the candidate against the container's `hwaddr` (or assert the expected hostname after connecting) and fail loudly instead of executing on an unrelated host. `cmd_lxc_exec` should also refuse to present a host-shell result as a container result. The `*-proxbot` keys in `pvestore:/root/.ssh/authorized_keys` need to be removed.
- Related log file: `scripts/logs/chat-sessions/chat-20260928-152857-82994d.jsonl`

### 2026-09-28 — Cloned template containers have no getty on tty1 and collide with a static node address

- Status: open
- Found by: agent
- Command: `lxc clone --node pveheavy --vmid 20300 --new-vmid 170 --execute` then `lxc start`
- Expected: A clone that is reachable over SSH with a console login available as a fallback.
- Actual: The clone boots and DHCP works, but (a) tty1 produces no `login:` prompt, so the termproxy console is unusable, and (b) it was assigned `192.168.1.65`, a static address already held by node `pvestore`.
- Suspected cause: Template `20300` (`Debian-Template-No-Docker`) is a converted template whose guest has no enabled `getty@tty1`, and the DHCP scope on `192.168.1.0/24` overlaps statically configured node addresses. Note `lxc clone` exposes no `--ssh-public-keys` (only `--hostname --storage --pool --snapname`), so a key cannot be injected at clone time the way `lxc create --ssh-public-keys` does.
- Fix notes: Not yet fixed. Options: bake an authorized key into template 20300 once so future clones are reachable, and exclude statically held addresses from the DHCP scope. In the meantime the only in-guest path that needs no guest credential is node SSH plus `pct exec <vmid> -- <cmd>` — `pveheavy:22` is open and `ssh exec --host <node> --command "pct exec <vmid> -- <cmd>"` already works, but it requires granting ProxBot root on the node and constraining it to `pct exec`.
- Related log file: N/A

### 2026-09-28 — chat.py markdown tables render broken (dropped rows, split boxes, misaligned borders)

- Status: fixed
- Found by: human
- Command: `./chat.py` → any assistant reply containing a markdown table (e.g. container-template inventory across pveheavy/pvestore)
- Expected: One aligned Unicode box table per markdown table, regardless of outer-pipe style, with intact template names and straight borders.
- Actual: (1) Rows missing a trailing `|` (e.g. `| pve1 | online`) were left as raw text while the header rendered as a box. (2) Tables without outer pipes were not detected at all. (3) A `|` inside `` `code` `` or `\|` split the row into phantom columns. (4) One ragged row widened the whole table with empty header cells. (5) A blank line mid-table split it into two boxes, the second with a bold "header". (6) `**bold**` headers showed raw markers and wrapped mid-word. (7) Wide glyphs (❌ U+274C) counted as 1 col so borders looked skewed. (8) `break_on_hyphens=True` could split template names/URLs at hyphens.
- Suspected cause: `render_markdown`/`flush_table` in `chat.py` required lines to start AND end with `|`, split naively on every `|`, sized columns with `len()` on raw markdown, styled headers inconsistently, flushed on blank lines, and wrapped with hyphen breaks.
- Fix notes: Tolerant `_is_table_line` (any line with `|`) + separator-row validation (shell prose like `echo a | b` falls back to inline text, no box); `_split_row` respecting `` `code` `` spans and `\|`; header defines column count (extras merged into last col); `_display_width` (ANSI-stripped, W/F + emoji ranges = 2) with `_strip_md_markers` for measurement; header markers stripped for display; blank lines deferred so one table stays one box; custom space-only wrapper (no hyphen breaks) with whole-para fast path. Verified: 9 reproduction cases render single aligned boxes; `python3 -m unittest discover -s tests` → 67 tests OK.
- Related log file: N/A

### 2026-09-19 — Unhandled KeyboardInterrupt traceback on manual exit / interrupt during streaming

- Status: fixed
- Found by: human
- Command: `./chat.py` (pressing `Ctrl+C` while agent is thinking or streaming response chunks)
- Expected: Clean interruption message `[Chat interrupted by user]` and prompt reset without Python traceback.
- Actual: Python traceback dumped: `KeyboardInterrupt` in `requests/models.py: iter_lines() -> generate()`.
- Suspected cause: `requests.post(..., stream=True)` and chunk generator inside `ChatAgent.chat()` did not catch `KeyboardInterrupt`.
- Fix notes: Wrapped streaming response parsing and turn loop in `ChatAgent.chat()` and `main()` in `chat.py` with `try ... except KeyboardInterrupt:`. Properly terminates the thought spinner/streamer and returns cleanly to prompt.
- Related log file: N/A

### 2026-09-19 — Proxmox LXC exec endpoint returns 501 Method not implemented on REST API

- Status: fixed
- Found by: human
- Command: `python3 scripts/Proxmox-cli.py lxc exec --node <node> --vmid <vmid> --command "<cmd>"`
- Expected: In-guest command execution inside LXC container.
- Actual: HTTP 501 (Method not implemented) because Proxmox VE REST API does not provide a standalone `/nodes/{node}/lxc/{vmid}/exec` REST endpoint.
- Suspected cause: Proxmox VE handles console command execution via the termproxy / vncwebsocket interactive API (`POST /nodes/{node}/lxc/{vmid}/termproxy` + RFC 6455 WebSocket) or SSH.
- Fix notes: Implemented `lxc_exec` in `scripts/Proxmox-cli.py` using standard-library RFC 6455 binary websocket frames and termproxy ticket authentication, with automatic fallback to SSH command execution via `cmd_ssh_exec`. Added comprehensive unit tests in `tests/test_cli.py`.
- Related log file: N/A

### 2026-09-19 — Raw ANSI escape codes displayed in interactive chat prompt

- Status: fixed
- Found by: human
- Command: `./chat.py`
- Expected: Interactive prompt renders cleanly with ANSI colors as `proxbot > `.
- Actual: Prompt displayed raw/corrupted ANSI codes: `[1m[32mproxbot[0m > `.
- Suspected cause: `readline_prompt()` wrapped each ANSI escape code with ASCII 0x01 (`\001`, `RL_PROMPT_START_IGNORE`) and 0x02 (`\002`, `RL_PROMPT_END_IGNORE`). In Python standard `input()`, especially on Python 3.13+/3.14 PyREPL or terminals that do not consume raw control bytes, 0x01 is passed directly to the terminal stdout stream where it disrupts the terminal emulator's escape-sequence state machine, causing the subsequent `ESC` (`0x1B`) to be dropped and leaving raw fragments (`[1m[32m` and `[0m`) rendered as text on the screen.
- Fix notes: Updated `prompt_input()` and `readline_prompt()` to sanitize and strip ANSI control codes before passing the prompt string to Python `input()`, and set `prompt_label = "proxbot > "`. This guarantees zero raw escape code artifacting (`[1m` / `[0m`) and accurate cursor/column tracking in GNU Readline and Python 3.13+/3.14 PyREPL across all terminal emulators. Updated unit tests in `tests/test_chat.py`.
- Related log file: N/A

### 2026-07-22 — Unrelated working-directory `.env` hijacks CLI configuration

- Status: fixed
- Found by: agent
- Command: Invoke either Proxmox CLI copy from `Harness-Redo`, whose `.env` contains SmallCTL rather than Proxmox settings.
- Expected: Ignore unrelated dotenv files and use the selected CLI's skill-root configuration unless `--env-file` explicitly selects another file.
- Actual: The CLI selected any cwd `.env`, then reported missing Proxmox keys even though the selected skill root had valid configuration.
- Suspected cause: `resolve_env_path()` checked only whether cwd `.env` existed, not whether it contained Proxmox settings.
- Fix notes: Cwd discovery now requires a Proxmox-specific key. Local agent guidance also passes the local `.env` explicitly and forbids synthesizing credentials.
- Related log file: `/home/stephen/Scripts/Harness-Redo/logs/22e37409-20260722-020439/tools.jsonl`

### 2026-07-21 — Installed CLI ignores the invoking project's `.env`

- Status: fixed
- Found by: agent
- Command: `cd /home/stephen/Scripts/9b-proxmox-manager/proxmox-manager && python3 ~/.config/opencode/skills/proxmox-manager/scripts/Proxmox-cli.py nodes list --format json`
- Expected: The command uses `/home/stephen/Scripts/9b-proxmox-manager/proxmox-manager/.env`, matching the project directory from which it was invoked.
- Actual: The installed executable derives `REPO_ROOT` from `__file__` and loads `~/.config/opencode/skills/proxmox-manager/.env`, connecting to that skill's configured cluster instead.
- Suspected cause: `ENV_PATH = REPO_ROOT / ".env"` binds configuration to the executable location, while the workflow documentation permits invoking the installed executable from a separate project with its own `.env`.
- Fix notes: The CLI now resolves `.env` from the current directory before the installed skill directory, supports `--env-file`, and reports the selected `.env_path` in `doctor` output. The installed copy was updated as well.
- Related log file: `logs/34f613a7-20260722-005808/tools.jsonl`

### 2026-07-21 — Project CLI parsed configured integer timeouts as `None`

- Status: fixed
- Found by: agent
- Command: Any command using `PROXMOX_TIMEOUT_SECONDS` or `PROXMOX_PORT` in the project-local CLI.
- Expected: Valid numeric configuration values are parsed as integers.
- Actual: An accidentally nested `_parse_int` implementation returned `None` for non-empty values.
- Suspected cause: `_positive_int` was inserted between the `None` guard and the conversion branch of `_parse_int`.
- Fix notes: Restored the conversion and fallback branch to `_parse_int`, and kept `_positive_int` as a separate validator.
- Related log file: N/A

### 2026-07-21 — Guest-network permission errors suggested guest installation

- Status: fixed
- Found by: agent
- Command: `python3 scripts/Proxmox-cli.py vm guest-network --node pve --vmid 101 --format json`
- Expected: A permission failure identifies the missing ACL rather than implying the QEMU guest agent is absent.
- Actual: The generic guest-agent installation hint was returned for a `VM.Monitor` permission failure.
- Suspected cause: The handler used one unavailable-agent hint for every API error.
- Fix notes: `vm guest-network` now recommends granting `VM.Monitor` for HTTP 401/403 or permission errors; other failures retain the guest-agent installation hint.
- Related log file: `scripts/logs/latest.jsonl`

### 2026-07-13 — `send-keys --keys` values are not redacted in command logs

- Status: fixed
- Found by: agent
- Command: `python3 scripts/Proxmox-console.py send-keys --node pvestore --vmid 3001 --keys T,e,m,p,@,P,a,s,s --execute`
- Expected: Values that can contain typed passwords or shell commands are not written literally to `scripts/logs/proxmox-cli-*.jsonl`.
- Actual: `--keys` was visible in command logs and console session events.
- Suspected cause: `SENSITIVE_ARGV_FLAGS` did not include `--keys`.
- Fix notes: `--keys` is now redacted in command argv logging. Historical logs may still contain the old values.
- Related log file: scripts/logs/proxmox-cli-20260710-211715.jsonl, scripts/logs/proxmox-cli-20260710-213844.jsonl

### 2026-07-13 — Printable symbol key events can be mis-entered in the guest terminal

- Status: fixed
- Found by: agent
- Command: `python3 scripts/Proxmox-console.py send-keys --node pvestore --vmid 3001 --keys s,u,d,o,...,s,s,h,d,_,c,o,n,f,i,g --execute`
- Expected: The guest receives symbols exactly, for example `Temp@Pass` should arrive as `Temp@Pass` and `/etc/ssh/sshd_config` should retain `_`.
- Actual: Raw printable keysyms for shifted punctuation were interpreted inconsistently by the Proxmox VNC/QEMU/guest keyboard path. In a live VM 3001 login test, manually typing the visible text `Temp@Pass` was rejected, while the physical sequence `Temp` + `Shift+2` + `Pass` worked. The broker also initially converted uppercase letters to shifted lowercase physical keys, which arrived lowercase in the login prompt.
- Suspected cause: `type --text` sent printable characters directly as X11 keysyms. For shifted symbols such as `@`, the guest path required the physical shifted base key sequence (`Shift+2`) instead of the literal `@` keysym. Uppercase alphabetic characters, however, were correctly handled by literal uppercase keysyms in this environment.
- Fix notes: Updated `_text_to_events()` in `scripts/Proxmox-console.py` to map shifted punctuation/symbols to US physical `Shift+<base>` key sequences while preserving uppercase letters as literal uppercase keysyms. Added regression tests for `@`, uppercase `T`/`P`, and `Temp@Pass`. Live-confirmed against VM 3001: revealed login field displayed exactly `Temp@Pass` after `type --text "Temp@Pass"`.
- Related log file: scripts/logs/proxmox-cli-20260710-213844.jsonl, scripts/logs/console-sessions/20260710-214804-3001.png, scripts/logs/console-sessions/20260710-220103-3001.png, scripts/logs/console-sessions/20260713-160406-3001.png

## Live-test issues

### 2026-07-10 — Console mouse clicks are not registered by the Ubuntu desktop installer

- Status: open
- Found by: agent
- Command: `python3 scripts/Proxmox-console.py click --node pvestore --vmid 3001 --x 1230 --y 870 --execute --force --confirm 3001`
- Expected: The installer's "Restart now" button activates and the VM restarts.
- Actual: Multiple `click` attempts at various coordinates produced no UI response; the install had to be driven entirely by keyboard.
- Suspected cause: The Proxmox VNC pointer events may not be processed by the Ubuntu/GTK installer environment, or the pointer must be moved/held differently. The VNC code itself produces valid PointerEvent messages.
- Fix notes: Worked around by using keyboard navigation and Proxmox API `vm stop`/`vm start` to restart. Added a `mouse-move` diagnostic command to help investigate cursor movement.
- Follow-up: `click --hold-ms` now allows a longer pointer-button press (for example, 150 ms). A live installer retest is still required before this issue can be closed.
- Related log file: scripts/logs/proxmox-cli-20260710-*.jsonl

### 2026-07-10 — `type` command logs typed text (including passwords) in command argv

- Status: fixed
- Found by: agent
- Command: `python3 scripts/Proxmox-console.py type --node pvestore --vmid 3001 --text <password> --execute --force --confirm 3001`
- Expected: The typed text is not written to logs.
- Actual: `scripts/logs/proxmox-cli-*.jsonl` contained the full `--text` value in the `argv` field.
- Suspected cause: `Proxmox-cli.py` `_redact_argv` did not include `--text` in `SENSITIVE_ARGV_FLAGS`.
- Fix notes: Added `--text` and `--actions` to `SENSITIVE_ARGV_FLAGS` in `scripts/Proxmox-cli.py`; the next argument after these flags is now replaced with `***REDACTED***` before the log entry is flushed. Existing log files from before this fix still contain the old values.
- Related log file: scripts/logs/proxmox-cli-20260710-*.jsonl

- Status: open
- Found by: agent
- Command: `python3 scripts/Proxmox-cli.py vm start --node pve --vmid 107 --execute --wait`
- Expected: VM starts with KVM acceleration.
- Actual: Task failed with `KVM virtualisation configured, but not available. Either disable in VM configuration or enable in BIOS.`
- Suspected cause: The Proxmox node is running virtualized or has Intel VT-x/AMD-V disabled in BIOS; `/dev/kvm` is not available.
- Fix notes: Worked around by setting `kvm: 0` on VM 107 via `api post /nodes/pve/qemu/107/config`. This enables TCG software emulation, which is slow but allowed the VM to boot for the live test.
- Related log file: scripts/logs/proxmox-cli-20260709-*.jsonl

### 2026-07-09 — Proxmox VNC server requires VNC authentication (DES); broker screenshot/type cannot complete

- Status: fixed
- Found by: agent
- Command: `python3 scripts/Proxmox-console.py screenshot --node pve --vmid 107`
- Expected: Screenshot is captured and saved to `scripts/logs/console-sessions/`.
- Actual: VNC ticket is obtained, but the websocket VNC handshake requires security type 2 (VNC authentication), which the broker did not implement because it requires DES encryption.
- Suspected cause: The built-in VNC client only supported None security (type 1). Proxmox's VNC proxy requires VNC auth by default.
- Fix notes: Added a pure-Python DES-ECB implementation and VNC-auth bit-reversed key derivation to `scripts/Proxmox-console.py`. Used the `password` field returned by `vncproxy` (falling back to the ticket) for VNC auth. URL-encoded the `vncticket` parameter to avoid Proxmox API 400 errors on tickets containing `;` or other special characters. Screenshot and type commands now work against the live Proxmox node.
- Related log file: scripts/logs/proxmox-cli-20260709-*.jsonl

### 2026-07-09 — WebSocket client sent unmasked frames, causing VNC server to close connection

- Status: fixed
- Found by: agent
- Command: `python3 scripts/Proxmox-console.py screenshot --node pve --vmid 107`
- Expected: After the VNC version exchange, the server continues with security-type negotiation.
- Actual: The VNC server closed the websocket immediately after the client sent its protocol version.
- Suspected cause: `_WebSocketClient._encode_frame()` omitted the MASK bit (bit 7 of the second byte) in client-to-server frames. Per RFC 6455, all client frames must be masked; servers drop unmasked frames and close the connection.
- Fix notes: Set the mask bit in the length byte and added regression tests (`test_encode_frame_masks_client_frame`, `test_encode_frame_medium_length`) to `tests/test_console.py`.
- Related log file: scripts/logs/proxmox-cli-20260709-*.jsonl

## 2026-09-25 — lxc exec SSH fallback double-emit breaks chat parsing, leaks password, appears stuck

- Status: fixed
- Found by: agent/human
- Command: `./chat.py` → `lxc_exec(node=pveheavy, vmid=266, command=...)` then with `password` after login prompt; `python3 scripts/Proxmox-cli.py --format json lxc exec --node pveheavy --vmid 266 --command ... --password ... --execute`
- Expected: Single JSON result on one stream; `login_required` triggers secure getpass retry; password never appears in output/logs; failure message is readable; exec shows progress.
- Actual: `cmd_lxc_exec` emitted SSH failure AND termproxy result (two JSON docs on stderr, since failures go to stderr). `chat.py:_raw_execute` only parsed stdout, so result became `{"ok": false, "error": "<two JSON blobs>"}`; login escalation check missed; error detail collapsed to `"{"`; user saw `✗ Failed in 52.70s: {` then a hang (3 serial ~50s exec calls, no progress). Raw `--password` also leaked into the CLI `command` field → chat session JSONL + LLM context.
- Suspected cause: `cmd_ssh_exec` always emitted even when invoked as a quiet fallback; `emit_output` built `command` from raw `sys.argv` without redaction; `_raw_execute` ignored stderr.
- Fix notes: `cmd_ssh_exec(..., emit=False)` for the lxc fallback path with fail-fast SSH timeout (min 10s) and single final emit (full stdout preserved via `_last_result`); `_redact_command_str()` applied in `emit_output`/`emit_dry_run`; `chat.py:_raw_execute` now parses stdout+stderr and takes the last JSON doc (handles legacy concatenated output); `_extract_error_detail`, login-required detection, and sanitization hardened; progress heartbeat printed for exec tools; `lxc_exec` tool description + system prompt now forbid pasting passwords in chat (use getpass flow); single-probe guidance added.
- Related log file: `scripts/logs/chat-sessions/chat-20260925-205756-ca59e4.jsonl`

## Fixed bugs

### 2026-07-13 — Console special-key aliases rejected or sent with brittle keysyms

- Status: fixed
- Found by: human
- Command: `python3 scripts/Proxmox-console.py send-key --key "[ctr]+[alt]+[del]" --execute`, `send-key --key Windows --execute`, BIOS/UEFI navigation with `Esc`/`Del` aliases
- Expected: Common operator spellings for Ctrl/Alt/Windows and BIOS keys are accepted and translated to VNC RFB key events.
- Actual: The broker only accepted a narrow set of exact names. Bracketed `[ctrl]` notation, `ctr`, `Windows`, lowercase aliases, and QEMU-style `ctrl-alt-del` failed validation; generic `Ctrl+A` style chords used uppercase letter keysyms.
- Suspected cause: Key parsing lacked a normalization layer and treated printable/name tokens literally.
- Fix notes: Added alias normalization, bracket/hyphen combo support, standalone modifier/special key mappings, and lowercase Ctrl-letter keysyms per RFB guidance. Added unit tests for Ctrl-letter, Ctrl+Alt+Delete aliases, Windows/Super, and sequence normalization.
- Related log file: N/A

### 2026-07-08 — Errors written to stdout instead of stderr

- Status: fixed
- Found by: agent
- Command: Any failing command (e.g., `python3 scripts/Proxmox-cli.py lxc status --node pve --vmid 999` or `python3 scripts/Proxmox-cli.py lxc create --node pve --vmid 106 ...` when validation fails)
- Expected: Error output is emitted to stderr (file descriptor 2) so callers can distinguish success from failure and pipe stdout safely.
- Actual: Both success and error output were printed to stdout, making it impossible to separate results from errors in scripts or pipelines.
- Suspected cause: All `print()` calls in the CLI used the default `sys.stdout`; there was no centralized output helper to route errors to `sys.stderr`.
- Fix notes: Added `emit_output()` and `emit_error()` helpers that print success/results to stdout and errors to stderr. Replaced all direct `print(render_output(...))` and `print(render_error_markdown(...))` call sites to use these helpers. Console fix instructions and other success messaging remain on stdout.
- Related log file: N/A (applies to all commands)

### 2026-07-08 — `logs tail` and `logs latest` return empty current-command log

- Status: fixed
- Found by: agent
- Command: `python3 scripts/Proxmox-cli.py logs tail --lines 2` and `python3 scripts/Proxmox-cli.py logs latest`
- Expected: `logs tail` shows the most recent meaningful log entries from the previous command, and `logs latest` returns the path to that log file.
- Actual: `logs tail` often produced no output because every command (including `logs tail` itself) creates an empty log file at startup; `latest_log_file()` selected that empty file by mtime and returned nothing useful. `logs latest` similarly returned the path to the empty file.
- Suspected cause: `latest_log_file()` chose the most recently modified file regardless of content. The `logs tail` command itself starts a new, empty log before reading the latest file.
- Fix notes: Updated `latest_log_file()` to prefer the `latest.jsonl` symlink and, when falling back to scanning, to skip empty log files. This ensures `logs tail` returns the previous non-empty command log and `logs latest` points to a useful file.
- Related log file: N/A

### 2026-09-28 — lxc create misused for converted LXC template VMID 20300 (clone vs create confusion)

- Status: fixed
- Found by: human
- Command: `chat.py` → "using the debian lxc template (20300) spin up a new lxc called ansible-controller" → agent called `lxc_create` with `ostemplate=20300` / `pveheavy:20300` across SSDStore/local-zfs/HDDStore
- Expected: Clone of template:1 container 20300 into new CT (e.g. vmid 109 `ansible-controller`) via `POST /nodes/{node}/lxc/{vmid}/clone`.
- Actual: `storage 'pveheavy' does not exist`, then `SSH key already exists at .lxc-ssh-key-109`, then 3x `Only root can pass arbitrary filesystem paths at PVE/Storage.pm line 651`. No `lxc_clone`/`vm_clone` tool existed, so the agent shoehorned the VMID into `lxc_create --ostemplate`.
- Suspected cause: `lxc_create` only supports `POST /nodes/{node}/lxc` (vztmpl tarballs); converted templates need the clone endpoint. `templates_list` merged clone/create sources without a creation-method annotation, the system prompt gave only `lxc_create` guidance, and there was no client-side `--ostemplate` format validation or ssh-key reuse.
- Fix notes: Added `lxc clone` / `vm clone` CLI commands (`_clone_execute`, `POST /nodes/{node}/lxc|qemu/{vmid}/clone`) + `lxc_clone`/`vm_clone` chat tools with prompt decision tree (template:1 → clone, vztmpl volid → create); `templates_list` now annotates `creation_method`; `lxc create` validates `--ostemplate` (`<storage>:vztmpl/*.tar.*`, numeric VMIDs rejected with clone hint); `--ssh-keygen` reuses existing `.pub` instead of failing. Verified: `lxc clone` dry-run hits `/nodes/pveheavy/lxc/20300/clone`; `lxc create --ostemplate 20300` fails validation exit 1; `commands` lists both clones; 70 unittest OK.
- Related log file: scripts/logs/chat-sessions/chat-20260928-112805-1b7bd9.jsonl

### 2026-09-28 — Console broker key-parsing functions lost during refactor (reconstructed)

- Status: fixed
- Found by: agent
- Command: Refactor of `scripts/Proxmox-console.py` into `src/proxbot/console/`
- Expected: Verbatim line-range extraction of all sections, with the original file preserved.
- Actual: The original console script was overwritten by its shim **without taking a backup first** (the repo is not a git repository and no other copy existed). The extraction ranges covered the data tables (`X11_KEYSYM`, `KEY_TOKEN_ALIASES`, `US_SHIFTED_TYPE_CHARS`, `_MODIFIER_KEYS`, `*_ALLOWLIST`, `INSTALL_PRESETS`) but the 14 key-parsing functions at lines 309-558 were not in any range and were destroyed: `_send_key_events`, `_strip_key_wrappers`, `_canonical_single_key_token`, `_looks_like_modifier`, `_split_key_combo`, `_canonical_key_token`, `_key_name_to_events`, `_combo_to_events`, `_is_valid_key_token`, `_parse_key_sequence`, `_key_sequence_summary`, `_text_to_events`, `_type_text_has_shell_metacharacters`, `_validate_macro_action`.
- Suspected cause: Line-range slicing was applied to a file that was simultaneously being replaced by its shim, and the ranges were derived from an AST map that had not been exhaustively checked for gaps. Compounding factor: no version control in the repo, so the operation was unrecoverable.
- Fix notes: Reconstructed all 14 functions in `src/proxbot/console/keys.py` from the surviving lookup tables plus the behavior documented in `AGENTS.md` (alias normalization, `[ctrl]+[alt]+[del]` bracket notation, `ctrl-alt-del` hyphen combos, `KeyxN` repetition, allowlist enforcement, shell-metacharacter blocking, RFB press/release event pairing with lowercase Ctrl-letter keysyms). Verified against every documented form and a 12-case metacharacter table. **These functions are a behavioral reconstruction, not the original source, and should be reviewed before relying on subtle edge cases.** Also fixed: `SHELL_METACHARACTERS_RE` was lost the same way and was recreated (behavior verified against 12 cases).
- Related log file: N/A

### 2026-09-28 — `EXIT_API_ERROR` used but never defined in `cmd_vm_exec`

- Status: fixed
- Found by: agent
- Command: Any failing `vm exec` (guest-agent error paths)
- Expected: Return an API error exit code.
- Actual: `NameError: name 'EXIT_API_ERROR' is not defined` at 3 sites, so the failure surfaced as an internal error (exit 99) with a misleading message.
- Suspected cause: Undefined constant; the intended alias was `EXIT_API`.
- Fix notes: Replaced all 3 uses with `EXIT_API`.
- Related log file: N/A

## 2026-09-28 — `ssh_exec` auth failure looped instead of asking for credentials

- Status: fixed
- Found by: human
- Command: `chat.py` → "try to access container 170's shell" → `lxc_exec` (console_not_requested) → `lxc_pct_exec` (node SSH unconfigured) → `ssh_exec root@192.168.1.164` × 2 → `Permission denied (publickey,password)` twice, user interrupted
- Expected: On publickey/password denial the harness stops and asks the operator for a corrected username, key file, or password instead of retrying identical credentials.
- Actual: `ssh_exec` had no auth-failure escalation (unlike `lxc_exec` login_required and `lxc_pct_exec` node_password_required), and the CLI/executor had no `--password` plumbing for `ssh exec`, so the model retried the same failing root login in a loop (watchdog allows 2 duplicates).
- Suspected cause: Auth-failure string was returned as a generic error with no `ssh_auth_failed` status and no getpass prompt path.
- Fix notes: `ChatAgent` now detects `Permission denied (publickey…)` / auth-failure patterns (including nested `data.stderr`), prompts for SSH user (default prior user), key file (`none` to clear), and password via getpass, retries once with updated creds, else returns `status=ssh_auth_failed` with an explicit do-not-retry instruction. Added `--password` to `ssh exec` parser + executor forwarding (redacted via existing `--password` rules; session logs redact `password`/`key` substrings). Updated `ssh_exec` spec + system prompt to forbid blind retries.
- Related log file: N/A (transcript in report)

## 2026-09-28 — `/doctor` crashed with `NameError: json is not defined`

- Status: fixed
- Found by: human
- Command: `./chat.py` → `/doctor`
- Expected: Cluster health JSON rendered as Markdown.
- Actual: `NameError: name 'json' is not defined` — the `chat.py` composition-root shim uses `json.dumps` in its `/doctor`, `/nodes`, `/vms`, `/lxcs`, `/templates`, and `/registry` handlers but never imported `json`.
- Suspected cause: Refactor moved the implementation into `src/proxbot/` while `main()` stayed in the shim; the import did not move with it.
- Fix notes: Added `import json` to `chat.py`. Verified: shim compiles, `--help` works, `/doctor` and `/nodes` render paths produce output, 169 unittests pass. Note: `chat.py: error: unrecognized arguments: --console` is expected — `--console` is a `Proxmox-cli.py lxc exec` flag, not a `chat.py` flag.
- Related log file: scripts/logs/chat-sessions/chat-20260928-184525-0f0379.jsonl
