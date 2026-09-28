#!/usr/bin/env python3
"""
Proxmox Console Broker.

A safe, brokered interface for Proxmox VM console access. The agent never
receives raw VNC credentials. All console actions are logged, tiered, and
subject to VM eligibility guards.

Stable entrypoint: the implementation lives in the `proxbot` package
(src/proxbot/console/) and is re-exported here so existing callers, tests,
and the documented command surface keep working unchanged.

  broker.py    config, eligibility guards, sessions, capture helpers
  protocol.py  DES auth, WebSocket, and RFB/VNC wire protocol
  keys.py      key names, aliases, modifiers, RFB event encoding
  commands/    tier 1 (read), tier 2 (input/mouse), tier 3 (text), admin
"""

import pathlib
import sys

_SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from proxbot.console.broker import *  # noqa: F401,F403
from proxbot.console.broker import *  # noqa: F401,F403
from proxbot.console.broker import (  # noqa: F401
    CLI_PATH, ConsoleConfig, DEFAULT_CONSOLE_KEY_DELAY,
    DEFAULT_CONSOLE_SESSION_LOG_DIR, DEFAULT_CONSOLE_WATCH_INTERVAL, cli,
)
from proxbot.console.protocol import *  # noqa: F401,F403
from proxbot.console.protocol import (  # noqa: F401
    _SerialClient, _VNCClient, _WebSocketClient, _WebSocketFrame,
    _generate_websocket_key, _websocket_accept_key, _vnc_auth_response,
    _vnc_des_key,
)
from proxbot.console.keys import *  # noqa: F401,F403
from proxbot.console.keys import (  # noqa: F401
    BASE_KEY_ALLOWLIST, COMMON_COMBO_ALLOWLIST, INSTALL_PRESETS,
    KEY_TOKEN_ALIASES, RUN_COMMAND_ALLOWLIST, SEND_KEY_ALLOWLIST,
    SHELL_METACHARACTERS_RE, TYPE_ALLOWED_CONTROLS, US_SHIFTED_TYPE_CHARS,
    VNC_BUTTON_LEFT, VNC_BUTTON_MIDDLE, VNC_BUTTON_RIGHT,
    VNC_BUTTON_SCROLL_DOWN, VNC_BUTTON_SCROLL_UP, X11_KEYSYM,
    _MODIFIER_ALLOWLIST, _MODIFIER_KEYS, _canonical_key_token,
    _canonical_single_key_token, _combo_to_events, _is_valid_key_token,
    _key_name_to_events, _key_sequence_summary, _looks_like_modifier,
    _parse_key_sequence, _send_key_events, _split_key_combo,
    _strip_key_wrappers, _text_to_events, _type_text_has_shell_metacharacters,
    _send_key_events, _validate_macro_action,
)
from proxbot.console.commands.read import (  # noqa: F401
    _ensure_vm_running, cmd_doctor, cmd_read_text, cmd_screenshot, cmd_status, cmd_watch,
)
from proxbot.console.commands.input import (  # noqa: F401
    cmd_run_command, cmd_send_key, cmd_send_keys,
)
from proxbot.console.commands.mouse import (  # noqa: F401
    _input_vnc_session, _parse_mouse_points, cmd_click, cmd_mouse_move, cmd_mouse_test,
)
from proxbot.console.commands.text import (  # noqa: F401
    cmd_install_preset, cmd_macro, cmd_paste_text, cmd_type,
)
from proxbot.console.commands.admin import cmd_session, cmd_snapshot  # noqa: F401

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="proxmox-console",
        description="Safe console broker for Proxmox VMs.",
        epilog="Common options: --format {md,json,table}, --no-log, --verbose, --timeout SECONDS",
    )
    parser.add_argument("--log-level", choices=["debug", "info", "warning", "error"], default="info")
    parser.add_argument("--env-file", help="Configuration file (default: .env in the current directory, then the installed skill)")
    sub = parser.add_subparsers(dest="subcommand", required=True)

    doctor = sub.add_parser("doctor", help="Check console config and API connectivity")
    doctor.add_argument("--format", choices=["md", "json", "table"], default=None)
    doctor.add_argument("--no-log", action="store_true")
    doctor.add_argument("--verbose", action="store_true")

    for name in ("screenshot", "read-text", "status"):
        cmd = sub.add_parser(name, help=f"Console {name} command")
        cmd.add_argument("--node", help="Node name")
        cmd.add_argument("--vmid", type=int, required=True, help="VM ID")
        cmd.add_argument("--format", choices=["md", "json", "table"], default=None)
        cmd.add_argument("--no-log", action="store_true")
        cmd.add_argument("--verbose", action="store_true")
        cmd.add_argument("--timeout", type=int, help="Request timeout in seconds")

    watch = sub.add_parser("watch", help="Poll the VM console until a condition is met (Tier 1)")
    watch.add_argument("--node", help="Node name")
    watch.add_argument("--vmid", type=int, required=True, help="VM ID")
    watch.add_argument("--until-stable", action="store_true", help="Exit when the screen stops changing")
    watch.add_argument("--stable-cycles", type=int, default=2, help="Consecutive stable frames required (default: 2)")
    watch.add_argument("--until-unchanged-for", type=float, help="Exit when the screen has been unchanged for this many seconds")
    watch.add_argument("--interval", type=float, help="Seconds between screenshots (default: env or 5)")
    watch.add_argument("--screenshots", action="store_true", help="Save every screenshot captured during the watch")
    watch.add_argument("--format", choices=["md", "json", "table"], default=None)
    watch.add_argument("--no-log", action="store_true")
    watch.add_argument("--verbose", action="store_true")
    watch.add_argument("--timeout", type=int, help="Maximum seconds to watch")

    send_key = sub.add_parser("send-key", help="Send a single key to the VM console (Tier 2)")
    send_key.add_argument("--node", help="Node name")
    send_key.add_argument("--vmid", type=int, required=True, help="VM ID")
    send_key.add_argument("--key", required=True, help="Key to send")
    send_key.add_argument("--delay", type=float, help="Seconds to wait after the key (default: env or 1.5)")
    send_key.add_argument("--execute", "--yes", action="store_true", help="Allow key input")
    send_key.add_argument("--format", choices=["md", "json", "table"], default=None)
    send_key.add_argument("--no-log", action="store_true")
    send_key.add_argument("--verbose", action="store_true")
    send_key.add_argument("--timeout", type=int, help="Request timeout in seconds")

    send_keys = sub.add_parser("send-keys", help="Send a sequence of keys to the VM console (Tier 2)")
    send_keys.add_argument("--node", help="Node name")
    send_keys.add_argument("--vmid", type=int, required=True, help="VM ID")
    send_keys.add_argument("--keys", required=True, help="Comma-separated key sequence (e.g., 'Space,Downx10,Space,Tab,Enter')")
    send_keys.add_argument("--delay", type=float, help="Seconds to wait after the sequence (default: env or 1.5)")
    send_keys.add_argument("--execute", "--yes", action="store_true", help="Allow key input")
    send_keys.add_argument("--format", choices=["md", "json", "table"], default=None)
    send_keys.add_argument("--no-log", action="store_true")
    send_keys.add_argument("--verbose", action="store_true")
    send_keys.add_argument("--timeout", type=int, help="Request timeout in seconds")

    run_cmd = sub.add_parser("run-command", help="Run a safe diagnostic command via QEMU guest agent (Tier 2)")
    run_cmd.add_argument("--node", help="Node name")
    run_cmd.add_argument("--vmid", type=int, required=True, help="VM ID")
    run_cmd.add_argument("--command", required=True, help="Command to run")
    run_cmd.add_argument("--execute", "--yes", action="store_true", help="Allow command execution")
    run_cmd.add_argument("--format", choices=["md", "json", "table"], default=None)
    run_cmd.add_argument("--no-log", action="store_true")
    run_cmd.add_argument("--verbose", action="store_true")
    run_cmd.add_argument("--timeout", type=int, help="Request timeout in seconds")

    snapshot = sub.add_parser("snapshot", help="Create a snapshot before risky console work (Tier 2)")
    snapshot.add_argument("--node", help="Node name")
    snapshot.add_argument("--vmid", type=int, required=True, help="VM ID")
    snapshot.add_argument("--name", required=True, help="Snapshot name")
    snapshot.add_argument("--execute", "--yes", action="store_true", help="Create the snapshot")
    snapshot.add_argument("--wait", action="store_true", help="Wait for the snapshot task to complete")
    snapshot.add_argument("--format", choices=["md", "json", "table"], default=None)
    snapshot.add_argument("--no-log", action="store_true")
    snapshot.add_argument("--verbose", action="store_true")
    snapshot.add_argument("--timeout", type=int, help="Request timeout in seconds")

    type_cmd = sub.add_parser("type", help="Type text into the VM console (Tier 3)")
    type_cmd.add_argument("--node", help="Node name")
    type_cmd.add_argument("--vmid", type=int, required=True, help="VM ID")
    type_cmd.add_argument("--text", required=True, help="Text to type")
    type_cmd.add_argument("--snapshot", help="Required snapshot name")
    type_cmd.add_argument("--auto-snapshot", action="store_true", help="Create an auto-before-console snapshot if none exists")
    type_cmd.add_argument("--unsafe", action="store_true", help="Allow shell metacharacters in text")
    type_cmd.add_argument("--delay", type=float, help="Seconds to wait after typing (default: env or 1.5)")
    type_cmd.add_argument("--execute", "--yes", action="store_true", help="Allow typing")
    type_cmd.add_argument("--force", action="store_true", help="Confirm destructive intent")
    type_cmd.add_argument("--confirm", help="Confirm resource ID for destructive actions")
    type_cmd.add_argument("--format", choices=["md", "json", "table"], default=None)
    type_cmd.add_argument("--no-log", action="store_true")
    type_cmd.add_argument("--verbose", action="store_true")
    type_cmd.add_argument("--timeout", type=int, help="Request timeout in seconds")

    paste_text = sub.add_parser("paste-text", help="Type text without submitting it, then capture a screenshot (Tier 3)")
    for action in (paste_text,):
        action.add_argument("--node", help="Node name")
        action.add_argument("--vmid", type=int, required=True, help="VM ID")
        action.add_argument("--text", required=True, help="Text to type without Enter")
        action.add_argument("--snapshot", help="Required snapshot name")
        action.add_argument("--auto-snapshot", action="store_true", help="Create an auto-before-console snapshot if none exists")
        action.add_argument("--unsafe", action="store_true", help="Allow shell metacharacters in text")
        action.add_argument("--delay", type=float, help="Seconds to wait after typing (default: env or 1.5)")
        action.add_argument("--execute", "--yes", action="store_true", help="Allow typing")
        action.add_argument("--force", action="store_true", help="Confirm destructive intent")
        action.add_argument("--confirm", help="Confirm resource ID for destructive actions")
        action.add_argument("--format", choices=["md", "json", "table"], default=None)
        action.add_argument("--no-log", action="store_true")
        action.add_argument("--verbose", action="store_true")
        action.add_argument("--timeout", type=int, help="Request timeout in seconds")

    click_cmd = sub.add_parser("click", help="Send a mouse click to the VM console (Tier 3)")
    click_cmd.add_argument("--node", help="Node name")
    click_cmd.add_argument("--vmid", type=int, required=True, help="VM ID")
    click_cmd.add_argument("--x", type=int, required=True, help="X coordinate")
    click_cmd.add_argument("--y", type=int, required=True, help="Y coordinate")
    click_cmd.add_argument("--button", default="left", choices=["left", "middle", "right"], help="Mouse button")
    click_cmd.add_argument("--clicks", type=int, default=1, help="Number of clicks")
    click_cmd.add_argument("--hold-ms", type=int, default=50, help="Button hold duration in milliseconds")
    click_cmd.add_argument("--delay", type=float, help="Seconds to wait after the click (default: env or 1.5)")
    click_cmd.add_argument("--execute", "--yes", action="store_true", help="Allow mouse click")
    click_cmd.add_argument("--force", action="store_true", help="Confirm destructive intent")
    click_cmd.add_argument("--confirm", help="Confirm resource ID for destructive actions")
    click_cmd.add_argument("--format", choices=["md", "json", "table"], default=None)
    click_cmd.add_argument("--no-log", action="store_true")
    click_cmd.add_argument("--verbose", action="store_true")
    click_cmd.add_argument("--timeout", type=int, help="Request timeout in seconds")

    mouse_move_cmd = sub.add_parser("mouse-move", help="Move the mouse pointer on the VM console (Tier 2)")
    mouse_move_cmd.add_argument("--node", help="Node name")
    mouse_move_cmd.add_argument("--vmid", type=int, required=True, help="VM ID")
    mouse_move_cmd.add_argument("--x", type=int, required=True, help="X coordinate")
    mouse_move_cmd.add_argument("--y", type=int, required=True, help="Y coordinate")
    mouse_move_cmd.add_argument("--delay", type=float, help="Seconds to wait after the move (default: env or 1.5)")
    mouse_move_cmd.add_argument("--execute", "--yes", action="store_true", help="Allow mouse movement")
    mouse_move_cmd.add_argument("--format", choices=["md", "json", "table"], default=None)
    mouse_move_cmd.add_argument("--no-log", action="store_true")
    mouse_move_cmd.add_argument("--verbose", action="store_true")
    mouse_move_cmd.add_argument("--timeout", type=int, help="Request timeout in seconds")

    mouse_test_cmd = sub.add_parser("mouse-test", help="Run sequential mouse moves in one VNC session (Tier 2)")
    mouse_test_cmd.add_argument("--node", help="Node name")
    mouse_test_cmd.add_argument("--vmid", type=int, required=True, help="VM ID")
    mouse_test_cmd.add_argument("--points", help="Semicolon-separated x,y points (default: three viewport points)")
    mouse_test_cmd.add_argument("--delay", type=float, help="Seconds to wait after each move (default: env or 1.5)")
    mouse_test_cmd.add_argument("--execute", "--yes", action="store_true", help="Allow mouse movement")
    mouse_test_cmd.add_argument("--format", choices=["md", "json", "table"], default=None)
    mouse_test_cmd.add_argument("--no-log", action="store_true")
    mouse_test_cmd.add_argument("--verbose", action="store_true")
    mouse_test_cmd.add_argument("--timeout", type=int, help="Request timeout in seconds")

    macro_cmd = sub.add_parser("macro", help="Run a sequence of console actions in one VNC session (Tier 3)")
    macro_cmd.add_argument("--node", help="Node name")
    macro_cmd.add_argument("--vmid", type=int, required=True, help="VM ID")
    macro_cmd.add_argument("--actions", help="JSON list of actions (mutually exclusive with --file)")
    macro_cmd.add_argument("--file", help="Path to JSON file containing actions (mutually exclusive with --actions)")
    macro_cmd.add_argument("--snapshot", help="Required snapshot name")
    macro_cmd.add_argument("--auto-snapshot", action="store_true", help="Create an auto-before-console snapshot if none exists")
    macro_cmd.add_argument("--unsafe", action="store_true", help="Allow shell metacharacters in type actions")
    macro_cmd.add_argument("--delay", type=float, help="Seconds to wait after the macro completes (default: env or 1.5)")
    macro_cmd.add_argument("--execute", "--yes", action="store_true", help="Allow macro execution")
    macro_cmd.add_argument("--force", action="store_true", help="Confirm destructive intent")
    macro_cmd.add_argument("--confirm", help="Confirm resource ID for destructive actions")
    macro_cmd.add_argument("--format", choices=["md", "json", "table"], default=None)
    macro_cmd.add_argument("--no-log", action="store_true")
    macro_cmd.add_argument("--verbose", action="store_true")
    macro_cmd.add_argument("--timeout", type=int, help="Request timeout in seconds")

    install_preset = sub.add_parser("install-preset", help="Run a versioned, confirmed installer sequence (Tier 3)")
    install_preset.add_argument("--node", help="Node name")
    install_preset.add_argument("--vmid", type=int, required=True, help="VM ID")
    install_preset.add_argument("--preset", choices=sorted(INSTALL_PRESETS), required=True)
    install_preset.add_argument("--tasksel-focus-confirmed", action="store_true", help="Confirm the documented tasksel focus before running")
    install_preset.add_argument("--snapshot", help="Required snapshot name")
    install_preset.add_argument("--auto-snapshot", action="store_true", help="Create an auto-before-console snapshot if none exists")
    install_preset.add_argument("--delay", type=float, help="Seconds to wait after the preset completes")
    install_preset.add_argument("--execute", "--yes", action="store_true", help="Allow preset execution")
    install_preset.add_argument("--force", action="store_true", help="Confirm destructive intent")
    install_preset.add_argument("--confirm", help="Confirm resource ID for destructive actions")
    install_preset.add_argument("--format", choices=["md", "json", "table"], default=None)
    install_preset.add_argument("--no-log", action="store_true")
    install_preset.add_argument("--verbose", action="store_true")
    install_preset.add_argument("--timeout", type=int, help="Request timeout in seconds")

    session = sub.add_parser("session", help="Manage broker session files")
    session.add_argument("--node", help="Node name")
    session.add_argument("--vmid", type=int, required=True, help="VM ID")
    session.add_argument("--list", action="store_true", help="List session files")
    session.add_argument("--stop", action="store_true", help="Stop/delete a session")
    session.add_argument("--session-id", help="Session file name to stop")
    session.add_argument("--execute", "--yes", action="store_true", help="Allow destructive session stop")
    session.add_argument("--force", action="store_true", help="Confirm destructive intent")
    session.add_argument("--confirm", help="Confirm session ID for destructive actions")
    session.add_argument("--format", choices=["md", "json", "table"], default=None)
    session.add_argument("--no-log", action="store_true")
    session.add_argument("--verbose", action="store_true")
    session.add_argument("--timeout", type=int, help="Request timeout in seconds")

    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        parser = build_parser()
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else EXIT_VALIDATION

    env_path = cli.resolve_env_path(getattr(args, "env_file", None))
    env = cli.load_env(env_path)
    config = ConsoleConfig(env)
    config.env_path = env_path

    if getattr(args, "format", None):
        config.output_format = args.format
    if getattr(args, "timeout", None):
        config.timeout_seconds = args.timeout

    logger = Logger(config.log_dir, no_log=args.no_log, config=config)
    client = ProxmoxClient(config, logger, verbose=args.verbose)

    try:
        subcommand = args.subcommand
        if subcommand == "doctor":
            return cmd_doctor(args, config, client, logger)

        err = cli._require_config(config, logger)
        if err is not None:
            return err

        if subcommand == "status":
            return cmd_status(args, config, client, logger)
        if subcommand == "screenshot":
            return cmd_screenshot(args, config, client, logger)
        if subcommand == "read-text":
            return cmd_read_text(args, config, client, logger)
        if subcommand == "watch":
            return cmd_watch(args, config, client, logger)
        if subcommand == "send-key":
            return cmd_send_key(args, config, client, logger)
        if subcommand == "send-keys":
            return cmd_send_keys(args, config, client, logger)
        if subcommand == "run-command":
            return cmd_run_command(args, config, client, logger)
        if subcommand == "snapshot":
            return cmd_snapshot(args, config, client, logger)
        if subcommand == "type":
            return cmd_type(args, config, client, logger)
        if subcommand == "paste-text":
            return cmd_paste_text(args, config, client, logger)
        if subcommand == "click":
            return cmd_click(args, config, client, logger)
        if subcommand == "mouse-move":
            return cmd_mouse_move(args, config, client, logger)
        if subcommand == "mouse-test":
            return cmd_mouse_test(args, config, client, logger)
        if subcommand == "macro":
            return cmd_macro(args, config, client, logger)
        if subcommand == "install-preset":
            return cmd_install_preset(args, config, client, logger)
        if subcommand == "session":
            return cmd_session(args, config, client, logger)

        emit_error("unknown_command", f"Unknown command: {subcommand}", "", logger.file_path)
        return EXIT_VALIDATION
    except Exception as exc:
        msg = str(exc)
        if config.token_secret:
            msg = cli.redact_string(msg, config)
        logger.log(
            event="internal_error",
            argv=sys.argv[1:],
            ok=False,
            error=msg,
        )
        emit_error("internal_error", msg, "Check the log for details.", logger.file_path)
        return EXIT_INTERNAL
    finally:
        logger.symlink_latest()


if __name__ == "__main__":
    sys.exit(main())
