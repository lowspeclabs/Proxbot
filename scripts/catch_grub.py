#!/usr/bin/env python3
"""Reset VM, catch SeaBIOS boot menu with ESC, then boot disk and hammer SHIFT
to force the hidden GRUB menu. Single persistent VNC session.
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
    args = ap.parse_args()

    env = pconsole.cli.load_env()
    config = pconsole.ConsoleConfig(env)
    logger = pconsole.Logger(config.log_dir, no_log=True, config=config)
    client = pconsole.ProxmoxClient(config, logger)

    node, vmid = args.node, args.vmid
    r = client.post(f"/nodes/{node}/qemu/{vmid}/vncproxy", {"websocket": 1})
    if not r["ok"]:
        print("vncproxy failed", r.get("error"));
        return 1
    d = r.get("data") or {}
    ticket, password, port = d.get("ticket"), d.get("password"), d.get("port", "5900")
    session_dir = config.console_session_log_dir
    session_dir.mkdir(parents=True, exist_ok=True)

    with pconsole._vnc_session(config, node, str(vmid), ticket, port,
                               config.timeout_seconds, password=password) as vnc:
        rr = client.post(f"/nodes/{node}/qemu/{vmid}/status/reset", {})
        print("reset ok=%s %s" % (rr.get("ok"), datetime.datetime.now().strftime("%H:%M:%S")))
        # Phase A: hammer ESC for a few seconds to land on SeaBIOS boot menu
        start = time.time()
        while time.time() - start < 4.5:
            send(vnc, "Escape")
            time.sleep(0.2)
        shot(vnc, session_dir, vmid, "A-seabios")
        # Phase B: select disk (Enter on default option 1) to boot, then
        # immediately hammer SHIFT to force the hidden GRUB menu.
        send(vnc, "Enter")
        time.sleep(0.3)
        # Hold SHIFT: send repeated keydown (down=1) so GRUB sees it held,
        # release at the end. Screenshot periodically.
        shift_sym = pconsole.X11_KEYSYM["Shift_L"]
        import struct as _struct
        def shift_down():
            vnc._send(_struct.pack("!BBHI", 4, 1, 0, shift_sym))
        def shift_up():
            vnc._send(_struct.pack("!BBHI", 4, 0, 0, shift_sym))
        start = time.time()
        i = 0
        while time.time() - start < 9.0:
            shift_down()
            i += 1
            if i % 15 == 0:
                shift_up()
                shot(vnc, session_dir, vmid, "B-grub")
            time.sleep(0.1)
        shift_up()
        shot(vnc, session_dir, vmid, "B-final")
    return 0


if __name__ == "__main__":
    sys.exit(main())
