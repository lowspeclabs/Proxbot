#!/usr/bin/env python3
"""Helper: open one persistent VNC session and repeatedly send a key over a
window of time to catch short preboot menus (SeaBIOS / GRUB). Screenshots are
captured periodically. Reuses the vetted console broker VNC client.

Usage:
  python3 hold_key_boot.py --node <n> --vmid <id> --key Escape \
     --duration 18 --interval 0.4 --shot-every 3
"""
import argparse
import datetime
import importlib.util
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("pconsole", HERE / "Proxmox-console.py")
pconsole = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pconsole)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--node", required=True)
    ap.add_argument("--vmid", required=True)
    ap.add_argument("--key", default="Escape")
    ap.add_argument("--duration", type=float, default=18.0)
    ap.add_argument("--interval", type=float, default=0.4)
    ap.add_argument("--shot-every", type=float, default=3.0)
    ap.add_argument("--reset", action="store_true",
                    help="Issue a hard reset after VNC session is established")
    args = ap.parse_args()

    env = pconsole.cli.load_env()
    config = pconsole.ConsoleConfig(env)
    logger = pconsole.Logger(config.log_dir, no_log=True, config=config)
    client = pconsole.ProxmoxClient(config, logger)

    node = args.node
    vmid = args.vmid

    vnc_endpoint = f"/nodes/{node}/qemu/{vmid}/vncproxy"
    vnc_resp = client.post(vnc_endpoint, {"websocket": 1})
    if not vnc_resp["ok"]:
        print("vncproxy failed:", vnc_resp.get("error"))
        return 1
    d = vnc_resp.get("data") or {}
    ticket = d.get("ticket")
    password = d.get("password")
    port = d.get("port", "5900")

    events = []
    for k in pconsole._parse_key_sequence(args.key):
        events.extend(pconsole._key_name_to_events(k))

    session_dir = config.console_session_log_dir
    session_dir.mkdir(parents=True, exist_ok=True)

    shots = []
    start = time.time()
    last_shot = 0.0
    with pconsole._vnc_session(config, node, str(vmid), ticket, port,
                               config.timeout_seconds, password=password) as vnc:
        if args.reset:
            r = client.post(f"/nodes/{node}/qemu/{vmid}/status/reset", {})
            print("reset issued ok=%s at %s" % (r.get("ok"), datetime.datetime.now().strftime("%H:%M:%S.%f")))
        while time.time() - start < args.duration:
            pconsole._send_key_events(vnc, events)
            now = time.time()
            if now - last_shot >= args.shot_every:
                last_shot = now
                try:
                    png = vnc.request_screenshot(stable=False)
                    if png:
                        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
                        p = session_dir / f"{ts}-{vmid}-hold.png"
                        pconsole._write_screenshot(png, p, vmid)
                        shots.append(str(p))
                        print("shot", p, "t=%.1f" % (now - start))
                except Exception as e:
                    print("shot err", e)
            time.sleep(args.interval)
    print("done. shots:", len(shots))
    for s in shots:
        print(s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
