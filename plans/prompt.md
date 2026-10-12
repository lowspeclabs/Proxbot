Your job is to implement this repository exactly from the provided planning files.

Project: `proxmox-opencode-lab`

Goal: Build a Python CLI interface that lets an agent harness safely manage Proxmox through the Proxmox API. The CLI must support read-only inspection first, controlled write operations later, command/result logging, `.env` API configuration, and markdown-based bug/improvement tracking.

You are not allowed to improvise a new architecture unless the existing docs are impossible to follow.

## Ralph Loop

Repeat this loop until the project passes its tests:

1. **READ**

   * Read `README.md`
   * Read `AGENTS.md`
   * Read `spec.md`
   * Read `implementation-plan.md`
   * Read `docs/proxmox-api-notes.md` if it exists
   * Inspect the project tree before editing anything

2. **PLAN**

   * Write a short plan before making changes
   * List the exact files you will create or edit
   * Keep the plan small: one phase at a time

3. **PATCH**

   * Prefer minimal patches
   * Do not rewrite unrelated files
   * Keep files simple and readable
   * Use plain Python unless the spec requires a dependency

4. **RUN**

   * Run syntax checks
   * Run any available tests
   * Run CLI help output
   * Test safe read-only commands first
   * Never run destructive Proxmox operations unless the CLI requires `--yes` and the spec allows it

5. **LOG**

   * Make sure the CLI writes logs into `scripts/logs/`
   * Logs must include timestamp, command/action, target, success/failure, and redacted result details
   * Never log API secrets, passwords, tokens, or full `.env` contents

6. **RECORD**

   * If you find a bug, add it to `scripts/BUGS.md`
   * If you notice an improvement, add it to `scripts/IMPROVEMENTS.md`
   * Mark completed fixes clearly
   * Do not hide failures

7. **REPORT**

   * At the end of each loop, report:

     * Files changed
     * Commands run
     * Tests passed
     * Tests failed
     * Remaining work

## Required Project Layout

The final repo should look like this:

```text
proxmox-opencode-lab/
├─ AGENTS.md
├─ README.md
├─ spec.md
├─ implementation-plan.md
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

## Main File To Build

Implement:

```text
scripts/Proxmox-cli.py
```

The CLI should load Proxmox API settings from `.env`.

Expected `.env` values:

```env
PROXMOX_HOST=
PROXMOX_PORT=8006
PROXMOX_VERIFY_SSL=false
PROXMOX_API_USER=
PROXMOX_API_TOKEN_ID=
PROXMOX_API_TOKEN_SECRET=
PROXMOX_DEFAULT_NODE=
```

Do not commit a real `.env`.

Only `.env.example` should be included.

## CLI Requirements

The CLI should support commands like:

```bash
python scripts/Proxmox-cli.py --help
python scripts/Proxmox-cli.py status
python scripts/Proxmox-cli.py nodes
python scripts/Proxmox-cli.py vms
python scripts/Proxmox-cli.py containers
python scripts/Proxmox-cli.py vm-status --node NODE --vmid VMID
python scripts/Proxmox-cli.py container-status --node NODE --vmid VMID
```

Optional write commands may include:

```bash
python scripts/Proxmox-cli.py start-vm --node NODE --vmid VMID --yes
python scripts/Proxmox-cli.py stop-vm --node NODE --vmid VMID --yes
python scripts/Proxmox-cli.py reboot-vm --node NODE --vmid VMID --yes
```

Write/destructive commands must require `--yes`.

If `--yes` is missing, refuse safely.

## Safety Rules

Follow these rules strictly:

* Default to read-only behavior
* Never expose token secrets
* Never print full secrets
* Never log full secrets
* Redact sensitive values as `***REDACTED***`
* Require `--yes` for state-changing actions
* Fail clearly if `.env` is missing
* Fail clearly if required API values are missing
* Handle API errors gracefully
* Do not crash with raw tracebacks during normal user errors
* Keep all logs under `scripts/logs/`

## Logging Requirements

Every CLI action should write a log entry.

Log files should be placed in:

```text
scripts/logs/
```

Use a filename like:

```text
proxmox-cli-YYYYMMDD.log
```

Each log entry should include:

```text
timestamp
command/action
target host
node if provided
vmid if provided
HTTP method/path if API call was made
success/failure
short result summary
error message if failed
```

Do not log secrets.

## API Requirements

Use the Proxmox REST API.

Use API token auth with the Authorization header format:

```text
Authorization: PVEAPIToken=USER@REALM!TOKENID=TOKEN_SECRET
```

Build a small reusable API client inside `Proxmox-cli.py`.

Required client behavior:

* Build base URL from `.env`
* Support GET and POST
* Support SSL verification toggle
* Parse JSON responses
* Handle non-200 responses
* Return useful error messages

## Implementation Order

Follow this order:

### Phase 1 — Skeleton

Create or verify:

* `scripts/Proxmox-cli.py`
* `scripts/logs/.gitkeep`
* `scripts/BUGS.md`
* `scripts/IMPROVEMENTS.md`

Add argparse CLI.

Make `--help` work.

### Phase 2 — Config

Implement `.env` loading.

Validate required fields.

Add safe redaction.

### Phase 3 — Logging

Implement log creation under `scripts/logs/`.

Log every command.

### Phase 4 — API Client

Implement Proxmox API client.

Add GET support.

Add POST support.

Handle errors safely.

### Phase 5 — Read Commands

Implement:

* `status`
* `nodes`
* `vms`
* `containers`
* `vm-status`
* `container-status`

### Phase 6 — Guarded Write Commands

Implement only if read commands are stable:

* `start-vm`
* `stop-vm`
* `reboot-vm`

All must require `--yes`.

### Phase 7 — Tests

At minimum run:

```bash
python -m py_compile scripts/Proxmox-cli.py
python scripts/Proxmox-cli.py --help
python scripts/Proxmox-cli.py status
```

If no real Proxmox server is available, confirm that missing `.env` and missing config errors are handled cleanly.

## Documentation Requirements

Update docs if implementation differs from the original plan.

`README.md` must explain:

* What the project is
* Why it exists
* Who it is for
* Project layout
* Basic setup
* Example commands
* Safety model
* Logging behavior

`AGENTS.md` must explain:

* How another agent should work on the repo
* How to run the CLI
* How to test it
* How to log bugs
* How to log improvements
* What not to do

## Completion Criteria

The task is complete only when:

* `python scripts/Proxmox-cli.py --help` works
* `.env.example` documents all required settings
* CLI refuses unsafe write commands without `--yes`
* Logs are written to `scripts/logs/`
* Secrets are redacted
* `BUGS.md` and `IMPROVEMENTS.md` exist
* README explains project purpose and layout
* AGENTS.md explains agent workflow
* Final report lists changed files, tests run, and remaining known issues

Do not claim success unless you actually ran the checks.

Begin by listing the repo tree, then read the markdown files, then implement Phase 1.
