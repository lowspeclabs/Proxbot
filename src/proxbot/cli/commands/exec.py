"""LXC console exec, in-container SSH, and remote SSH execution handlers."""

import argparse
import json
import re
import base64
import os
import pathlib
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from typing import Any

from ...common.client import ProxmoxClient
from ...common.config import Config
from ...common.constants import (
    REPO_ROOT,
    EXIT_API,
    EXIT_AUTH,
    EXIT_CONFIG,
    EXIT_INTERNAL,
    EXIT_NOOP,
    EXIT_SAFETY,
    EXIT_SUCCESS,
    EXIT_TIMEOUT,
    EXIT_VALIDATION,
    REDACTED,
)
from ...common.config import strip_ansi
from ...common.logging_ import Logger
from ...common.redact import redact_data, redact_string
from ...common.render import emit_error, emit_output, filter_rows, render_output
from ...common.safety import emit_dry_run, request_plan, require_destructive, require_execute
from ._shared import _exit_code_for, _extract_task, _require_config, _wait_for_task
from .vm import _diagnose_vm_agent

# Unambiguous console credential prompts. Used to stop the probe early: these
# tokens only appear when a real getty/login is on the other end.
_CONSOLE_CREDENTIAL_PROMPT_RE = re.compile(r"(?:login|username|password)\s*:", re.IGNORECASE)

# What agetty/PAM print when the credential is refused.
_CONSOLE_REJECTED_RE = re.compile(
    r"login incorrect|authentication failure|access denied|permission denied",
    re.IGNORECASE,
)


def lxc_exec(
    node: str,
    vmid: str,
    command: str,
    timeout: int,
    config: Config,
    client: ProxmoxClient,
    logger: Logger,
    user: str = "root",
    password: str | None = None,
    auto_inject_key: bool = True,
    key_file: str | None = None,
) -> dict[str, Any] | None:
    """Execute command via LXC termproxy WebSocket API with login detection and SSH key auto-provisioning."""
    termproxy_resp = client.post(f"/nodes/{node}/lxc/{vmid}/termproxy", {})
    if not termproxy_resp["ok"]:
        return None

    term_data = termproxy_resp.get("data") or {}
    port = term_data.get("port")
    pve_user = term_data.get("user")
    ticket = term_data.get("ticket")
    if not port or not ticket or not pve_user:
        return None

    parsed = urllib.parse.urlparse(config.api_url)
    host = parsed.hostname
    api_port = parsed.port or (443 if parsed.scheme == "https" else 80)
    use_ssl = (parsed.scheme == "https")

    ssl_context = ssl.create_default_context()
    if not config.verify_ssl:
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE

    quoted_ticket = urllib.parse.quote(ticket, safe="")
    ws_path = f"/api2/json/nodes/{node}/lxc/{vmid}/vncwebsocket?port={port}&vncticket={quoted_ticket}"

    try:
        sock = socket.create_connection((host, api_port), timeout=min(timeout, 10))
        if use_ssl:
            sock = ssl_context.wrap_socket(sock, server_hostname=host)

        ws_key = base64.b64encode(os.urandom(16)).decode("ascii")
        req_lines = [
            f"GET {ws_path} HTTP/1.1",
            f"Host: {host}:{api_port}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Key: {ws_key}",
            "Sec-WebSocket-Version: 13",
            f"Authorization: {config.auth_header()}",
            "", ""
        ]
        sock.sendall("\r\n".join(req_lines).encode("ascii"))

        resp_data = b""
        while b"\r\n\r\n" not in resp_data:
            chunk = sock.recv(1024)
            if not chunk:
                break
            resp_data += chunk

        if b"101" not in resp_data.split(b"\r\n")[0]:
            sock.close()
            return None

        def encode_frame(payload: bytes, opcode: int = 2) -> bytes:
            length = len(payload)
            mask = os.urandom(4)
            masked_payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if length <= 125:
                hdr = bytes([0x80 | opcode, 0x80 | length])
            elif length <= 65535:
                hdr = bytes([0x80 | opcode, 0x80 | 126]) + length.to_bytes(2, "big")
            else:
                hdr = bytes([0x80 | opcode, 0x80 | 127]) + length.to_bytes(8, "big")
            return hdr + mask + masked_payload

        def read_frame(s: socket.socket) -> bytes | None:
            hdr = s.recv(2)
            if len(hdr) < 2:
                return None
            b1, b2 = hdr[0], hdr[1]
            masked = bool(b2 & 0x80)
            length = b2 & 0x7F
            if length == 126:
                length = int.from_bytes(s.recv(2), "big")
            elif length == 127:
                length = int.from_bytes(s.recv(8), "big")
            if masked:
                m = s.recv(4)
            payload = b""
            while len(payload) < length:
                chunk = s.recv(length - len(payload))
                if not chunk:
                    break
                payload += chunk
            if masked:
                payload = bytes(b ^ m[i % 4] for i, b in enumerate(payload))
            return payload

        # Send authentication ticket
        auth_payload = f"{pve_user}:{ticket}\n".encode("utf-8")
        sock.sendall(encode_frame(auth_payload))

        # Read confirmation (OK)
        start = time.time()
        ok_received = False
        while (time.time() - start) < 2.0:
            sock.settimeout(0.5)
            try:
                f = read_frame(sock)
                if f and b"OK" in f:
                    ok_received = True
                    break
            except Exception:
                break

        if not ok_received:
            sock.close()
            return None

        # Probe console with a newline to inspect active state. Wait on an idle
        # deadline rather than a frame count: a slow getty must not be reported
        # as a silent console, and a silent console must not be reported as a
        # login prompt.
        sock.sendall(encode_frame(b"\n\n"))

        initial_buf = b""
        sock.settimeout(1.0)
        idle_deadline = time.time() + 2.0
        empty_reads = 0
        while time.time() < idle_deadline:
            try:
                f = read_frame(sock)
            except Exception:
                # Socket timeout with no data: keep waiting until the console has
                # been idle for the full window before calling it silent.
                continue
            if not f:
                empty_reads += 1
                if empty_reads >= 3:
                    break
                continue
            empty_reads = 0
            initial_buf += f
            idle_deadline = time.time() + 2.0
            if _CONSOLE_CREDENTIAL_PROMPT_RE.search(
                strip_ansi(initial_buf.decode("utf-8", errors="replace"))
            ):
                break

        initial_text = strip_ansi(initial_buf.decode("utf-8", errors="replace")).strip()
        is_active_shell = bool(re.search(r"(?:[#\$>~]|root@[^\s:]+:[^#]+#)\s*$", initial_text))
        login_prompt_detected = bool(re.search(r"(?:login|username)\s*:\s*$", initial_text, re.IGNORECASE))
        password_prompt_detected = bool(re.search(r"password\s*:\s*$", initial_text, re.IGNORECASE))
        prompt_detected = login_prompt_detected or password_prompt_detected

        def _no_console_login(reason: str) -> dict[str, Any]:
            """No credential prompt was observed, so no credential was tested."""
            sock.close()
            return {
                "ok": False,
                "status": "console_unavailable",
                "vmid": vmid,
                "node": node,
                "error": (
                    f"Container {vmid}: no login or password prompt was received on this console "
                    f"attachment, so {reason} The LXC console is a single shared pty whose prompt is "
                    f"not reliably replayed on attach, and another session may hold it, so this is "
                    f"not evidence of a bad password. Verify from inside the guest with "
                    f"`systemctl is-active getty@tty1`. For automated commands use SSH, or "
                    f"`pct exec {vmid} -- <cmd>` from the node."
                ),
                "method": "termproxy",
            }

        # Case 1: no password supplied. Only ask for one when a prompt is really
        # there to answer — otherwise the password would be typed into a void.
        if not password and not is_active_shell:
            if not prompt_detected:
                return _no_console_login("no credential was requested.")
            sock.close()
            prompt_str = initial_text.split("\n")[-1] if initial_text else f"LXC {vmid} login:"
            return {
                "ok": False,
                "status": "login_required",
                "vmid": vmid,
                "node": node,
                "prompt": prompt_str,
                "error": f"Container {vmid} console requires login credentials to establish in-guest access.",
                "method": "termproxy",
            }

        # Case 2: password supplied -> perform console login against a real prompt
        if password:
            if not prompt_detected:
                return _no_console_login(f"the supplied password was not submitted.")

            if login_prompt_detected or not password_prompt_detected:
                sock.sendall(encode_frame(f"{user or 'root'}\r\n".encode("utf-8")))
                pw_buf = b""
                sock.settimeout(3.0)
                start_pw = time.time()
                while (time.time() - start_pw) < 3.0:
                    try:
                        f = read_frame(sock)
                        if f:
                            pw_buf += f
                            if b"assword" in pw_buf.lower():
                                break
                    except Exception:
                        break

            # Send password
            sock.sendall(encode_frame(f"{password}\r\n".encode("utf-8")))
            time.sleep(0.5)

            # Check if login succeeded
            auth_chk_buf = b""
            sock.settimeout(4.0)
            start_chk = time.time()
            login_success = False
            login_rejected = False
            while (time.time() - start_chk) < 4.0:
                try:
                    f = read_frame(sock)
                    if f:
                        auth_chk_buf += f
                        chk_text = strip_ansi(auth_chk_buf.decode("utf-8", errors="replace"))
                        if _CONSOLE_REJECTED_RE.search(chk_text):
                            login_rejected = True
                            break
                        if any(chk_text.rstrip().endswith(s) for s in ("#", "$", "~#", "~$", ">")):
                            login_success = True
                            break
                except Exception:
                    break

            if not login_success:
                sock.close()
                error = (
                    f"Console login rejected by container {vmid} — wrong username or password."
                    if login_rejected
                    else f"Container {vmid} accepted the credentials but no shell prompt appeared within 4s."
                )
                return {
                    "ok": False,
                    "status": "login_failed",
                    "vmid": vmid,
                    "node": node,
                    "error": error,
                    "method": "termproxy",
                }

            # If auto-injecting SSH key:
            if auto_inject_key:
                try:
                    ssh_key_file = pathlib.Path(key_file).expanduser() if key_file else (pathlib.Path.home() / f".ssh/lxc-{vmid}")
                    pubkey_file = ssh_key_file.with_suffix(".pub")
                    if not ssh_key_file.exists() or not pubkey_file.exists():
                        ssh_key_file.parent.mkdir(parents=True, exist_ok=True)
                        subprocess.run(
                            ["ssh-keygen", "-t", "ed25519", "-C", f"lxc-{vmid}-proxbot", "-f", str(ssh_key_file), "-N", ""],
                            capture_output=True, timeout=10
                        )
                    if pubkey_file.exists():
                        pubkey_content = pubkey_file.read_text().strip()
                        key_cmd = f"mkdir -p /root/.ssh && echo '{pubkey_content}' >> /root/.ssh/authorized_keys && chmod 700 /root/.ssh && chmod 600 /root/.ssh/authorized_keys\r\n"
                        sock.sendall(encode_frame(key_cmd.encode("utf-8")))
                        time.sleep(0.5)
                except Exception:
                    pass

        # Send command wrapped with boundary markers
        beg_marker = f"__PROXBOT_BEG_{int(time.time())}__"
        end_marker = f"__PROXBOT_END_{int(time.time())}__"
        wrapped_cmd = f"echo {beg_marker}; {command}; echo {end_marker}:$?\r\n".encode("utf-8")
        sock.sendall(encode_frame(wrapped_cmd))

        collected = b""
        start_exec = time.time()
        while (time.time() - start_exec) < timeout:
            sock.settimeout(1.0)
            try:
                f = read_frame(sock)
                if f:
                    collected += f
                    if end_marker.encode("utf-8") in collected:
                        break
            except Exception:
                pass

        sock.close()

        text_out = collected.decode("utf-8", errors="replace")
        return _parse_console_output(text_out, beg_marker, end_marker, vmid, node, timeout, bool(password))
    except Exception:
        pass

    return None


def _parse_console_output(
    text_out: str,
    beg_marker: str,
    end_marker: str,
    vmid: str,
    node: str,
    timeout: int,
    authenticated: bool,
) -> dict[str, Any]:
    """Fail closed on console output.

    A shared/limited terminal can lose the boundary markers or the trailing
    exit status. Reporting a missing exit status as 0 tells the agent a failed
    command succeeded, which is worse than reporting that we do not know.
    """
    method = "termproxy_authenticated" if authenticated else "termproxy"

    def _lost(reason: str, observed: str = "") -> dict[str, Any]:
        return {
            "ok": False,
            "status": "output_lost",
            "vmid": vmid,
            "node": node,
            "error": (
                f"Container {vmid}: the command was sent to the console but its result could "
                f"not be verified — {reason}. The console is a shared terminal, so output can "
                f"scroll past or interleave with another session. The exit status is UNKNOWN; "
                f"do not assume the command succeeded. Re-run over SSH, or "
                f"`pct exec {vmid} -- <cmd>` from the node."
            ),
            "observed": observed,
            "exit_code": None,
            "method": method,
        }

    if beg_marker not in text_out:
        return _lost("the start marker was never observed, so the command may not have run at all")

    part = text_out.split(beg_marker, 1)[1]
    cmd_body, found_end, end_part = part.partition(end_marker)
    clean_stdout = strip_ansi(cmd_body).strip()

    if not found_end:
        return _lost(
            "the end marker was never observed, so the exit status is unknown",
            observed=clean_stdout[-2000:],
        )

    code_match = re.match(r"\s*:([0-9]+)", end_part)
    if not code_match:
        return _lost(
            "the exit status was truncated, so the exit code is unknown",
            observed=clean_stdout[-2000:],
        )

    exit_code = int(code_match.group(1))
    return {
        "ok": exit_code == 0,
        "exit_code": exit_code,
        "stdout": clean_stdout,
        "stderr": "",
        "method": method,
    }


def _run_ssh_command(
    host: str,
    port: int,
    user: str,
    command: str,
    key_file: str | None = None,
    password: str | None = None,
    timeout: int = 30,
) -> tuple[int, str, str]:
    """Execute command over SSH with key file or password (via SSH_ASKPASS), returning (exit_code, stdout, stderr)."""
    ssh_cmd = [
        "ssh",
        "-p", str(port),
        "-o", "StrictHostKeyChecking=accept-new",
        # With UserKnownHostsFile=/dev/null the host key is never actually
        # remembered, so "Permanently added ..." fires on every single
        # connection and tells the operator nothing. LogLevel=ERROR suppresses
        # that notice (and nothing at ERROR or above).
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "LogLevel=ERROR",
        "-o", "ConnectTimeout=10",
    ]
    if key_file and pathlib.Path(key_file).exists():
        ssh_cmd.extend(["-i", str(key_file)])

    env = dict(os.environ)
    askpass_file = None

    if password:
        ssh_cmd.extend(["-o", "PreferredAuthentications=publickey,password,keyboard-interactive"])
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".sh") as f:
            escaped_pw = password.replace('\\', '\\\\').replace('"', '\\"').replace('$', '\\$').replace('`', '\\`')
            f.write(f'#!/bin/sh\nprintf "%s\\n" "{escaped_pw}"\n')
            askpass_file = f.name
        os.chmod(askpass_file, 0o700)
        env["SSH_ASKPASS"] = askpass_file
        env["SSH_ASKPASS_REQUIRE"] = "force"
        env["DISPLAY"] = ":0"
    else:
        ssh_cmd.extend(["-o", "BatchMode=yes"])

    ssh_cmd.extend([f"{user}@{host}", command])

    try:
        proc = subprocess.run(
            ssh_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        return proc.returncode, proc.stdout, proc.stderr
    finally:
        if askpass_file and os.path.exists(askpass_file):
            try:
                os.unlink(askpass_file)
            except Exception:
                pass


def cmd_lxc_exec(args: argparse.Namespace, config: Config, client: ProxmoxClient, logger: Logger) -> int:
    node = args.node or config.default_node
    if not node:
        emit_error("validation", "--node is required", "Set PROXMOX_DEFAULT_NODE or pass --node.", logger.file_path)
        return EXIT_VALIDATION
    vmid = str(args.vmid)
    command = (getattr(args, "command", None) or "").strip()
    timeout = getattr(args, "timeout", None) or 30
    user = getattr(args, "user", None) or "root"
    password = getattr(args, "password", None)
    auto_inject_key = not getattr(args, "no_auto_inject_key", False)
    key_file = getattr(args, "key_file", None)

    if not command:
        emit_error("validation", "--command is required", "Provide a non-empty shell command to execute.", logger.file_path)
        return EXIT_VALIDATION

    endpoint = f"/nodes/{node}/lxc/{vmid}/termproxy"
    guard = require_execute(args, f"lxc_exec:{node}:{vmid}")
    if guard is not None:
        return emit_dry_run(args, logger, request_plan(
            "LXC EXEC",
            endpoint,
            {"command": command, "user": user, "console": bool(getattr(args, "console", False))},
            {
                "node": node,
                "vmid": vmid,
                "command": command,
                "transports": ["ssh"] + (["termproxy"] if getattr(args, "console", False) else []),
                "console_note": "termproxy is opt-in only; pass --console to allow the shared-pty fallback",
            },
        ))

    # 1. If password is provided or SSH key exists, attempt SSH first (quietly —
    # the fallback must emit exactly ONE final JSON document, otherwise chat.py
    # sees concatenated JSON on stderr and cannot parse status/login_required).
    # Fail fast: cap the SSH probe so a password-auth-disabled container does not
    # block termproxy for the full 30s.
    ip = _resolve_guest_ip(node, vmid, "lxc", client)
    if ip and (password or key_file or (pathlib.Path.home() / f".ssh/lxc-{vmid}").exists()):
        ssh_args = argparse.Namespace(
            host=ip,
            node=node,
            vmid=int(vmid),
            kind="lxc",
            command=command,
            user=user,
            password=password,
            port=getattr(args, "port", None) or 22,
            key_file=key_file,
            timeout=min(timeout, 10),
            auto_inject_key=auto_inject_key,
            execute=True,
            format=args.format,
            no_log=getattr(args, "no_log", False),
        )
        ssh_res = cmd_ssh_exec(ssh_args, config, client, logger, emit=False)
        if ssh_res == EXIT_SUCCESS:
            # Quiet run already executed the command; re-emit its full result
            # (including stdout) exactly once so chat.py parses a single JSON doc.
            quiet_result = getattr(ssh_args, "_last_result", None)
            quiet_target = getattr(ssh_args, "_last_target", f"{user}@{ip}:22")
            if isinstance(quiet_result, dict):
                emit_output(quiet_result, args.format, title=f"SSH Exec ({quiet_target})")
            else:
                emit_output({
                    "ok": True,
                    "data": {"node": node, "vmid": vmid, "method": "ssh", "host": ip, "command": command},
                }, args.format, title=f"LXC {vmid} Exec (ssh)")
            return EXIT_SUCCESS

    # 2. Console (termproxy) is opt-in only. The LXC console is a single shared
    # pty: attaching can land on a human's live shell, and ProxBot cannot tell
    # whose session it found. Without --console the operator has asserted that
    # nobody else holds it, so we never attach implicitly.
    if not getattr(args, "console", False):
        emit_output({
            "ok": False,
            "status": "console_not_requested",
            "data": {"vmid": vmid, "node": node, "transport": "none"},
            "error": (
                f"No usable exec transport for container {vmid} on {node}. SSH into the guest was "
                f"unavailable, and the console is opt-in because it is a single shared pty that may "
                f"belong to someone else. Re-run with --console if you have confirmed nobody else is "
                f"attached, or use `pct exec {vmid} -- <cmd>` from the node."
            ),
        }, args.format, title=f"LXC {vmid} Exec (console_not_requested)")
        return EXIT_INTERNAL

    term_res = lxc_exec(node, vmid, command, timeout, config, client, logger, user=user, password=password, auto_inject_key=auto_inject_key, key_file=key_file)
    if term_res is not None:
        if term_res.get("status") in ("login_required", "login_failed", "console_unavailable", "output_lost"):
            # If password was provided and both termproxy and SSH failed
            emit_output({
                "ok": False,
                "status": term_res["status"],
                "data": term_res,
                "error": term_res.get("error"),
            }, args.format, title=f"LXC {vmid} Exec ({term_res['status']})")
            return EXIT_AUTH if term_res["status"] == "login_required" else EXIT_INTERNAL

        result = {
            "ok": term_res["ok"],
            "data": {
                "node": node,
                "vmid": vmid,
                "method": term_res.get("method", "termproxy"),
                "exit_code": term_res["exit_code"],
                "stdout": term_res["stdout"],
                "stderr": term_res.get("stderr", ""),
                "command": command,
            },
            "error": term_res.get("stderr") if not term_res["ok"] else None,
        }
        logger.log(
            event="lxc_exec",
            argv=sys.argv[1:],
            ok=result["ok"],
            response_summary=f"node={node} vmid={vmid} method={term_res.get('method', 'termproxy')} exit_code={term_res['exit_code']}",
            extra={"exit_code": term_res["exit_code"], "method": term_res.get("method", "termproxy")},
        )
        emit_output(result, args.format, title=f"LXC {vmid} Exec (termproxy)")
        return EXIT_SUCCESS if term_res["ok"] else EXIT_INTERNAL

    # 3. The termproxy transport itself never came up: no ticket, no WebSocket
    # upgrade, or a broken frame stream. Distinct from output_lost, where the
    # session worked and the result could not be read back.
    emit_output({
        "ok": False,
        "status": "console_unavailable",
        "data": {"vmid": vmid, "node": node, "status": "console_unavailable"},
        "error": (
            f"Could not open a termproxy WebSocket to container {vmid} on {node} "
            f"(no ticket, no upgrade, or the frame stream failed). This is a transport "
            f"failure, not a credential problem."
        ),
    }, args.format, title=f"LXC {vmid} Exec (console_unavailable)")
    return EXIT_INTERNAL


def _resolve_guest_ip(node: str, vmid: str, kind: str | None, client: ProxmoxClient | None) -> str | None:
    """Attempt to discover the active IPv4 address for a VM or LXC container."""
    if not client:
        return None
    # Try LXC interfaces first if not explicitly a VM
    if kind != "vm":
        resp_lxc_if = client.get(f"/nodes/{node}/lxc/{vmid}/interfaces")
        if resp_lxc_if["ok"] and isinstance(resp_lxc_if.get("data"), list):
            for iface in resp_lxc_if["data"]:
                if isinstance(iface, dict):
                    inet = iface.get("inet")
                    if inet and "/" in inet:
                        ip = inet.split("/")[0].strip()
                        if ip and not ip.startswith("127."):
                            return ip
                    for addr_obj in iface.get("ip-addresses", []):
                        if isinstance(addr_obj, dict) and addr_obj.get("ip-address-type") == "ipv4":
                            ip = addr_obj.get("ip-address", "").strip()
                            if ip and not ip.startswith("127."):
                                return ip

        resp_lxc_cfg = client.get(f"/nodes/{node}/lxc/{vmid}/config")
        if resp_lxc_cfg["ok"] and isinstance(resp_lxc_cfg.get("data"), dict):
            for k, v in resp_lxc_cfg["data"].items():
                if k.startswith("net") and isinstance(v, str):
                    ip_match = re.search(r"\bip=([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)(?:/[0-9]+)?", v)
                    if ip_match:
                        ip = ip_match.group(1)
                        if not ip.startswith("127.") and ip != "dhcp":
                            return ip

    # Try VM QEMU Guest Agent if not explicitly an LXC
    if kind != "lxc":
        resp_vm_net = client.get(f"/nodes/{node}/qemu/{vmid}/agent/network-get-interfaces")
        if resp_vm_net["ok"] and isinstance(resp_vm_net.get("data"), dict):
            res = resp_vm_net["data"].get("result", [])
            for iface in res:
                for ip_obj in iface.get("ip-addresses", []):
                    if ip_obj.get("ip-address-type") == "ipv4":
                        ip = ip_obj.get("ip-address", "").strip()
                        if ip and not ip.startswith("127."):
                            return ip

        resp_vm_cfg = client.get(f"/nodes/{node}/qemu/{vmid}/config")
        if resp_vm_cfg["ok"] and isinstance(resp_vm_cfg.get("data"), dict):
            for k, v in resp_vm_cfg["data"].items():
                if k.startswith("ipconfig") and isinstance(v, str):
                    ip_match = re.search(r"\bip=([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)(?:/[0-9]+)?", v)
                    if ip_match:
                        ip = ip_match.group(1)
                        if not ip.startswith("127.") and ip != "dhcp":
                            return ip

    return None


def _node_addresses_for_target(client: ProxmoxClient | None) -> dict[str, str]:
    """node name -> IPv4 address, reusing the pct exec transport's resolution."""
    from .nodessh import _node_addresses

    try:
        return _node_addresses(client)
    except Exception:
        return {}


def _blocked_node_target(
    host: str,
    node: str | None,
    config: Config,
    client: ProxmoxClient | None,
) -> str | None:
    """Return the matched node name if `host` is a Proxmox node, else None.

    Matches on cluster node names, their resolved addresses, and the configured
    default node. An explicitly allowed target is never blocked.
    """
    target = str(host).strip().lower()
    if not target:
        return None

    allowed = {str(item).strip().lower() for item in config.node_ssh_targets}
    if target in allowed:
        return None

    candidates: dict[str, str] = {}
    if node:
        candidates[str(node).strip().lower()] = str(node)
    for name, address in _node_addresses_for_target(client).items():
        candidates[name.strip().lower()] = name
        candidates[address.strip().lower()] = name
    if config.default_node:
        candidates[str(config.default_node).strip().lower()] = str(config.default_node)

    if target in candidates:
        return candidates[target]

    # A node name may also be reachable as a bare or FQDN form; compare the
    # short label and any address followed by a domain suffix.
    short = target.split(".")[0]
    if short in candidates:
        return candidates[short]
    for name, address in _node_addresses_for_target(client).items():
        if address and target.startswith(address + "."):
            return name
    return None


def cmd_ssh_exec(args: argparse.Namespace, config: Config, client: ProxmoxClient, logger: Logger, emit: bool = True) -> int:
    """Execute SSH command. When emit=False (internal fallback from lxc exec),
    stay silent so the caller can emit a single final JSON document."""
    host = getattr(args, "host", None)
    node = getattr(args, "node", None) or config.default_node
    vmid = getattr(args, "vmid", None)
    command = (getattr(args, "command", None) or "").strip()
    user = getattr(args, "user", None) or "root"
    password = getattr(args, "password", None)
    port = getattr(args, "port", None) or 22
    key_file = getattr(args, "key_file", None)
    timeout = getattr(args, "timeout", None) or 30
    auto_inject_key = not getattr(args, "no_auto_inject_key", False)

    def _emit(result: dict[str, Any], title: str) -> None:
        if emit:
            emit_output(result, args.format, title=title)

    def _emit_err(error_type: str, message: str, hint: str) -> None:
        if emit:
            emit_error(error_type, message, hint, logger.file_path)

    if not command:
        _emit_err("validation", "--command is required", "Provide a non-empty shell command to execute.")
        return EXIT_VALIDATION

    # A Proxmox node is root on the hypervisor. `ssh exec` is a generic tool, so
    # without this guard the model can route around the pct exec allowlist by
    # aiming a plain SSH call at a node. Node targets must be named explicitly.
    if host:
        blocked = _blocked_node_target(host, node, config, client)
        if blocked is not None:
            _emit({
                "ok": False,
                "status": "node_target_refused",
                "data": {"host": host, "node": node, "allowed": config.node_ssh_targets},
                "error": (
                    f"Refusing to SSH to {host!r}: it looks like a Proxmox node. `ssh exec` is a "
                    f"generic tool and must not be usable as a host shell, or the pct exec "
                    f"allowlist can be bypassed. To reach a container out-of-band use "
                    f"`lxc pct-exec --vmid <id> --command <cmd>`, which the harness assembles and "
                    f"validates. To permit direct node SSH, list the target in "
                    f"PROXMOX_NODE_SSH_TARGETS."
                ),
            }, "SSH Exec")
            return EXIT_SAFETY

    # Resolve IP from VM or LXC if --host not explicitly provided
    if not host:
        if not vmid:
            _emit_err("validation", "Either --host or --vmid must be specified", "Provide --host <ip/hostname> or --vmid <id>.")
            return EXIT_VALIDATION
        if not node:
            _emit_err("validation", "--node is required when resolving IP for --vmid", "Provide --node <node>.")
            return EXIT_VALIDATION

        host = _resolve_guest_ip(node, str(vmid), getattr(args, "kind", None), client)
        if not host:
            diag = _diagnose_vm_agent(node, str(vmid), client) if getattr(args, "kind", None) != "lxc" and client else None
            hint = (diag.get("hint") if diag else None) or "Check guest agent status, ensure VM/LXC is running with network configured, or pass --host explicitly."
            _emit({
                "ok": False,
                "target": {"node": node, "vmid": vmid},
                "error": f"Could not determine IP address for VM/LXC {vmid} on node {node}.",
                "agent_diagnosis": diag,
                "hint": hint,
            }, "SSH Exec")
            return EXIT_VALIDATION

    # If key_file not provided, check default candidate locations
    if not key_file and vmid:
        candidates = [
            REPO_ROOT / f".lxc-ssh-key-{vmid}",
            pathlib.Path.home() / ".ssh" / f"lxc-{vmid}",
            pathlib.Path.home() / ".ssh" / f"id_lxc_{vmid}",
        ]
        for c in candidates:
            if c.exists():
                key_file = str(c)
                break

    target_desc = f"{user}@{host}:{port}"
    plan_body = {
        "host": host,
        "user": user,
        "port": port,
        "key_file": key_file,
        "command": command,
        "timeout": timeout,
    }

    guard = require_execute(args, f"ssh:{target_desc}")
    if guard is not None:
        return emit_dry_run(args, logger, request_plan("SSH", target_desc, plan_body, {"target": target_desc, "command": command}))

    start_time = time.time()
    try:
        # If password provided and auto_inject_key enabled, ensure local key pair exists and inject it
        if password and auto_inject_key and vmid:
            try:
                ssh_key_path = pathlib.Path(key_file) if key_file else (pathlib.Path.home() / f".ssh/lxc-{vmid}")
                pubkey_path = ssh_key_path.with_suffix(".pub")
                if not ssh_key_path.exists() or not pubkey_path.exists():
                    ssh_key_path.parent.mkdir(parents=True, exist_ok=True)
                    subprocess.run(
                        ["ssh-keygen", "-t", "ed25519", "-C", f"lxc-{vmid}-proxbot", "-f", str(ssh_key_path), "-N", ""],
                        capture_output=True, timeout=10
                    )
                if pubkey_path.exists():
                    pubkey_content = pubkey_path.read_text().strip()
                    inject_cmd = f"mkdir -p /root/.ssh && echo '{pubkey_content}' >> /root/.ssh/authorized_keys && chmod 700 /root/.ssh && chmod 600 /root/.ssh/authorized_keys"
                    _run_ssh_command(host, port, user, inject_cmd, password=password, timeout=10)
                    key_file = str(ssh_key_path)
            except Exception:
                pass

        exit_code, stdout, stderr = _run_ssh_command(
            host=host,
            port=port,
            user=user,
            command=command,
            key_file=key_file,
            password=password,
            timeout=timeout,
        )
        duration_ms = int((time.time() - start_time) * 1000)
        result = {
            "ok": exit_code == 0,
            "data": {
                "host": host,
                "user": user,
                "port": port,
                "exit_code": exit_code,
                "stdout": stdout,
                "stderr": stderr,
                "command": command,
                "duration_ms": duration_ms,
            },
            "error": stderr if exit_code != 0 else None,
        }
        logger.log(
            event="ssh_exec",
            argv=sys.argv[1:],
            ok=result["ok"],
            response_summary=f"target={target_desc} exit_code={exit_code}",
            extra={"exit_code": exit_code, "duration_ms": duration_ms},
        )
        try:
            setattr(args, "_last_result", result)
            setattr(args, "_last_target", target_desc)
        except Exception:
            pass
        _emit(result, f"SSH Exec ({target_desc})")
        return EXIT_SUCCESS if exit_code == 0 else EXIT_INTERNAL
    except subprocess.TimeoutExpired:
        _emit({
            "ok": False,
            "error": f"SSH command timed out after {timeout} seconds",
            "data": {"host": host, "command": command},
        }, f"SSH Exec ({target_desc})")
        return EXIT_TIMEOUT
    except FileNotFoundError:
        _emit({
            "ok": False,
            "error": "ssh binary not found on local PATH",
        }, "SSH Exec")
        return EXIT_INTERNAL
