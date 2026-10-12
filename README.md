# ProxBot

ProxBot is a local assistant and command-line toolkit for managing a Proxmox VE
lab. It gives an operator two ways to work: ask the chat assistant for help, or
run explicit CLI commands. The CLI provides predictable output, logs operations,
and puts safety checks around changes to VMs and containers.

This README describes the checkout in this directory. `chat.py` and the scripts
in `scripts/` are the runnable entry points; the implementation is organized in
`src/proxbot/`.

## What is in this folder?

```text
ProxBot/
├── chat.py                 # Interactive AI assistant
├── .env.example            # Sample configuration; copy and fill in locally
├── requirements.txt        # Python runtime dependencies
├── scripts/
│   ├── Proxmox-cli.py      # Proxmox management CLI
│   ├── Proxmox-console.py  # Guarded VM console broker
│   ├── BUGS.md             # Known and fixed issues
│   ├── IMPROVEMENTS.md     # Improvement ideas and history
│   └── logs/               # Runtime logs and chat sessions (ignored by Git)
├── src/proxbot/
│   ├── common/             # Configuration, API client, logging, safety, output
│   ├── cli/                # CLI parser, command dispatch, command handlers
│   ├── chat/               # Chat loop, tool definitions, approvals, executor
│   └── console/            # Console protocol, broker, and console commands
├── tests/                  # Unit tests and static checks
├── plans/                  # Design notes and project plans
├── AGENTS.md               # Detailed operating and contribution guidance
└── README.md               # This overview
```

Local configuration and state such as `.env`, `.git.env`, generated SSH keys,
Python caches, and logs are intentionally not part of the repository. Do not
commit credentials or private keys.

## Requirements and installation

Use Python 3 and `pip`. The runtime packages are listed in `requirements.txt`:
`Pillow`, `requests`, and `python-dotenv`.

A virtual environment keeps dependencies isolated from system Python:

```bash
cd /path/to/ProxBot
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Activate `.venv` again in a new terminal before running ProxBot. There is no
separate dependency-install script at present.

## Configure Proxmox and the chat model

Copy the sample configuration once, then edit `.env` with your own Proxmox API
URL and API-token credentials. Never paste real secrets into source files or
commit `.env`.

```bash
cp -n .env.example .env
$EDITOR .env
```

The sample also documents optional node-SSH access, console restrictions, and
LLM settings. Leave node-SSH credentials unset unless you intentionally want to
enable the `pct exec` transport. For chat, set `LLM_BASE_URL` to an OpenAI-
compatible local model endpoint and configure the model/key if needed.

## Run ProxBot

From the project directory, with the virtual environment active:

```bash
# Check local configuration and connectivity
python3 scripts/Proxmox-cli.py doctor --live

# Inspect the cluster
python3 scripts/Proxmox-cli.py --format json nodes list
python3 scripts/Proxmox-cli.py --format json lxcs list --node <node>

# Start the interactive assistant
python3 chat.py
```

Use the node names returned by `nodes list`; example node names are placeholders.
The CLI also accepts `--env-file /absolute/path/to/.env` when you need to choose
a specific configuration file. Use the CLI's `commands` catalog
(`python3 scripts/Proxmox-cli.py --format json commands`) to see supported syntax.

## Safety and access basics

- Read operations can be run directly. Changes default to a dry-run; inspect the
  plan, then add `--execute` to perform the operation.
- Destructive actions require additional confirmation flags such as
  `--force --confirm <vmid>`.
- Creating a fresh LXC from an OS tarball requires a root password. In chat,
  ProxBot collects it through a hidden prompt rather than asking for it in the
  conversation. SSH public-key access is optional but recommended. Clones
  inherit the source template's account setup.
- LXC command execution uses guest SSH first and may use `pct exec` over node SSH
  when that optional access is configured. Console attachment is explicit
  because an LXC console can be a shared terminal.
- VM console operations are handled separately by `Proxmox-console.py` and are
  opt-in and tier-guarded. See `AGENTS.md` for the detailed access workflows.

## Tests and development

Run the test suite from the repository root:

```bash
python3 -m unittest discover -s tests
python3 tests/check_undefined_names.py src/proxbot
```

The `tests/` directory also contains the mock Proxmox API and CLI equivalence
check. See `AGENTS.md` for the project’s branch, commit, safety, and verification
workflow. The `scripts/BUGS.md` and `scripts/IMPROVEMENTS.md` files track issues
and proposed changes.
