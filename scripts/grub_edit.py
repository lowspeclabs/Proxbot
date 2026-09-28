#!/usr/bin/env python3
"""Reset VM, hold SHIFT to force GRUB menu, then press 'e' to edit the
highlighted Ubuntu entry. Leaves the VM sitting in the GRUB editor.
Single persistent VNC session.
"""
import argparse, datetime, importlib.util, pathlib, struct, sys, time

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("pconsole", HERE / "Proxmox-console.py")
pconsole = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pconsole)


def shot(vnc, session_dir, vmid, tag):
    try:
        png = vnc.request_screenshot(stable=False)
        if png:
            ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            p = session_dir / f"{ts}-{vmid}-{tag}.png"
            pconsole._write_screenshot(png, p, vmid)
            print("shot", p)
            return str(p)
    except Exception as e:
        print("shot err", e)
    return None


def send(vnc, keyname):
    events = []
    for k in pconsole._parse_key_sequence(keyname):
        events.extend(pconsole._key_name_to_events(k))
    pconsole._send_key_events(vnc, events)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--node", required=True)
    ap.add_argument("--vmid", required=True)
    ap.add_argument("--hold", type=float, default=6.0, help="seconds to hold SHIFT")
    args = ap.parse_args()

    env = pconsole.cli.load_env()
    config = pconsole.ConsoleConfig(env)
    logger = pconsole.Logger(config.log_dir, no_log=True, config=config)
    client = pconsole.ProxmoxClient(config, logger)
    node, vmid = args.node, args.vmid

    r = client.post(f"/nodes/{node}/qemu/{vmid}/vncproxy", {"websocket": 1})
    d = r.get("data") or {}
    ticket, password, port = d.get("ticket"), d.get("password"), d.get("port", "5900")
    session_dir = config.console_session_log_dir
    session_dir.mkdir(parents=True, exist_ok=True)

    shift_sym = pconsole.X11_KEYSYM["Shift_L"]
    with pconsole._vnc_session(config, node, str(vmid), ticket, port,
                               config.timeout_seconds, password=password) as vnc:
        rr = client.post(f"/nodes/{node}/qemu/{vmid}/status/reset", {})
        print("reset ok=%s %s" % (rr.get("ok"), datetime.datetime.now().strftime("%H:%M:%S")))
        # Hold SHIFT to force GRUB menu
        start = time.time()
        while time.time() - start < args.hold:
            vnc._send(struct.pack("!BBHI", 4, 1, 0, shift_sym))  # shift down
            time.sleep(0.1)
        vnc._send(struct.pack("!BBHI", 4, 0, 0, shift_sym))       # shift up
        time.sleep(0.4)
        shot(vnc, session_dir, vmid, "grub-menu")
        # Press 'e' to edit the highlighted (Ubuntu) entry
        send(vnc, "e")
        time.sleep(1.0)
        shot(vnc, session_dir, vmid, "grub-edit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
