#!/usr/bin/env python3
"""Full GRUB edit workflow in one persistent VNC session:
  1. reset
  2. hold SHIFT to reach GRUB menu
  3. press 'e' to edit Ubuntu entry
  4. move cursor to the 'linux' line
  5. go to end of that line and append ' init=/bin/bash'
  6. screenshot for verification (does NOT boot unless --boot)
"""
import argparse, datetime, importlib.util, pathlib, struct, sys, time

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("pconsole", HERE / "Proxmox-console.py")
pconsole = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pconsole)

X = pconsole.X11_KEYSYM


def raw(vnc, keysym, down):
    vnc._send(struct.pack("!BBHI", 4, down, 0, keysym))


def tap(vnc, keysym, delay=0.08):
    raw(vnc, keysym, 1); time.sleep(0.02); raw(vnc, keysym, 0); time.sleep(delay)


def ctrl(vnc, ch, delay=0.15):
    raw(vnc, X["Control_L"], 1); time.sleep(0.02)
    raw(vnc, ord(ch), 1); time.sleep(0.02); raw(vnc, ord(ch), 0); time.sleep(0.02)
    raw(vnc, X["Control_L"], 0); time.sleep(delay)


def type_text(vnc, text, delay=0.05):
    for c in text:
        sym = ord(c)
        raw(vnc, sym, 1); time.sleep(0.02); raw(vnc, sym, 0); time.sleep(delay)


def shot(vnc, sd, vmid, tag):
    try:
        png = vnc.request_screenshot(stable=False)
        if png:
            ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            p = sd / f"{ts}-{vmid}-{tag}.png"
            pconsole._write_screenshot(png, p, vmid)
            print("shot", p)
    except Exception as e:
        print("shot err", e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--node", required=True)
    ap.add_argument("--vmid", required=True)
    ap.add_argument("--hold", type=float, default=6.0)
    ap.add_argument("--downs", type=int, default=14, help="Down presses to reach linux line")
    ap.add_argument("--append", default=" init=/bin/bash")
    ap.add_argument("--boot", action="store_true", help="Ctrl-X to boot after editing")
    args = ap.parse_args()

    env = pconsole.cli.load_env()
    config = pconsole.ConsoleConfig(env)
    logger = pconsole.Logger(config.log_dir, no_log=True, config=config)
    client = pconsole.ProxmoxClient(config, logger)
    node, vmid = args.node, args.vmid

    r = client.post(f"/nodes/{node}/qemu/{vmid}/vncproxy", {"websocket": 1})
    d = r.get("data") or {}
    ticket, password, port = d.get("ticket"), d.get("password"), d.get("port", "5900")
    sd = config.console_session_log_dir; sd.mkdir(parents=True, exist_ok=True)

    with pconsole._vnc_session(config, node, str(vmid), ticket, port,
                               config.timeout_seconds, password=password) as vnc:
        rr = client.post(f"/nodes/{node}/qemu/{vmid}/status/reset", {})
        print("reset ok=%s %s" % (rr.get("ok"), datetime.datetime.now().strftime("%H:%M:%S")))
        # hold SHIFT for GRUB menu
        t0 = time.time()
        while time.time() - t0 < args.hold:
            raw(vnc, X["Shift_L"], 1); time.sleep(0.1)
        raw(vnc, X["Shift_L"], 0); time.sleep(0.5)
        shot(vnc, sd, vmid, "01-menu")
        # edit
        tap(vnc, ord('e'), delay=1.0)
        shot(vnc, sd, vmid, "02-edit")
        # move to linux line
        for _ in range(args.downs):
            tap(vnc, X["Down"], delay=0.06)
        shot(vnc, sd, vmid, "03-online")
        # end of line, then append
        ctrl(vnc, 'e')  # end of line
        type_text(vnc, args.append)
        time.sleep(0.3)
        shot(vnc, sd, vmid, "04-appended")
        if args.boot:
            ctrl(vnc, 'x', delay=0.3)
            time.sleep(4.0)
            shot(vnc, sd, vmid, "05-booting")
    return 0


if __name__ == "__main__":
    sys.exit(main())
