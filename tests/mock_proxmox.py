#!/usr/bin/env python3
"""Small mock Proxmox server for tests."""

import http.server
import json
import socketserver
import sys
import threading


class MockProxmoxHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        path = self.path
        if path == "/api2/json/version":
            body = json.dumps({"data": {"version": "8.1.0"}}).encode()
            status = 200
        elif path == "/api2/json/cluster/status":
            body = json.dumps({"data": [{"type": "cluster", "name": "lab"}]}).encode()
            status = 200
        elif path.startswith("/api2/json/cluster/nextid"):
            body = json.dumps({"data": "900"}).encode()
            status = 200
        elif path == "/api2/json/nodes":
            body = json.dumps({"data": [{"node": "pve1", "status": "online"}]}).encode()
            status = 200
        elif path == "/api2/json/nodes/pve1/status":
            body = json.dumps({"data": {"cpu": 0.05, "memory": {"total": 17179869184, "used": 4294967296}}})
            body = body.encode()
            status = 200
        elif path == "/api2/json/nodes/pve1/qemu":
            body = json.dumps({"data": [{"vmid": 101, "name": "test-vm", "status": "running"}]}).encode()
            status = 200
        elif path == "/api2/json/nodes/pve1/qemu/101/status/current":
            body = json.dumps({"data": {"vmid": 101, "name": "test-vm", "qmpstatus": "running"}}).encode()
            status = 200
        elif path == "/api2/json/nodes/pve1/qemu/101/config":
            body = json.dumps({"data": {"cores": 2, "memory": 2048}}).encode()
            status = 200
        elif path == "/api2/json/nodes/pve1/qemu/404/status/current":
            self.send_error(404, "VM not found")
            return
        elif path == "/api2/json/nodes/pve1/lxc":
            body = json.dumps({"data": [{"vmid": 201, "name": "test-lxc", "status": "running"}]}).encode()
            status = 200
        elif path == "/api2/json/nodes/pve1/lxc/201/status/current":
            body = json.dumps({"data": {"vmid": 201, "name": "test-lxc", "status": "running"}}).encode()
            status = 200
        elif path == "/api2/json/nodes/pve1/lxc/201/config":
            body = json.dumps({"data": {"cores": 1, "memory": 512}}).encode()
            status = 200
        elif path == "/api2/json/nodes/pve1/storage":
            body = json.dumps({"data": [{"storage": "local", "type": "dir"}]}).encode()
            status = 200
        elif path.startswith("/api2/json/nodes/pve1/storage/local/content"):
            if "content=vztmpl" in path:
                body = json.dumps({"data": [{"volid": "local:vztmpl/debian-12-standard.tar.zst"}]}).encode()
            else:
                body = json.dumps({"data": [{"volid": "local:iso/debian.iso"}]}).encode()
            status = 200
        elif path == "/api2/json/nodes/pve1/network":
            body = json.dumps({"data": [{"iface": "vmbr0", "type": "bridge", "active": 1}]}).encode()
            status = 200
        elif path == "/api2/json/nodes/pve1/tasks":
            body = json.dumps({"data": [{"upid": "UPID:pve1:00000000:00000000:00000000:task:101:root@pam:", "status": "stopped"}]}).encode()
            status = 200
        elif path.startswith("/api2/json/nodes/pve1/tasks/") and path.endswith("/status"):
            body = json.dumps({"data": {"upid": "UPID:pve1:00000000:00000000:00000000:task:101:root@pam:", "status": "stopped", "exitstatus": "OK"}}).encode()
            status = 200
        elif path.startswith("/api2/json/nodes/pve1/tasks/") and path.endswith("/log"):
            body = json.dumps({"data": [{"t": "line1"}]}).encode()
            status = 200
        elif "/snapshot" in path:
            if "qemu/101" in path:
                body = json.dumps({"data": [{"name": "auto-before-console-20260709-100000"}]}).encode()
            else:
                body = json.dumps({"data": []}).encode()
            status = 200
        elif path == "/api2/json/auth_error":
            self.send_error(401, "Invalid token")
            return
        elif path == "/api2/json/forbidden":
            self.send_error(403, "Permission denied")
            return
        elif path == "/api2/json/server_error":
            self.send_error(500, "Internal server error")
            return
        else:
            body = json.dumps({"data": None}).encode()
            status = 200
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        path = self.path
        if path == "/api2/json/nodes/pve1/qemu/101/vncproxy":
            body = json.dumps({"data": {"ticket": "test-vnc-ticket", "port": "5900", "upid": "UPID:pve1:00000000:00000000:00000000:vncproxy:101:root@pam:"}}).encode()
        elif path == "/api2/json/nodes/pve1/qemu/101/termproxy":
            body = json.dumps({"data": {"ticket": "test-term-ticket", "port": "5900", "upid": "UPID:pve1:00000000:00000000:00000000:termproxy:101:root@pam:"}}).encode()
        elif path == "/api2/json/nodes/pve1/qemu/101/agent/exec":
            body = json.dumps({"data": {"pid": 12345}}).encode()
        elif path == "/api2/json/nodes/pve1/qemu/101/agent/exec-status":
            body = json.dumps({"data": {"exited": True, "exitcode": 0, "out-data": "aGVsbG8gd29ybGQ=", "err-data": ""}}).encode()
        elif "/snapshot" in path:
            body = json.dumps({"data": "UPID:pve1:00000000:00000000:00000000:snapshot:101:root@pam:"}).encode()
        else:
            body = json.dumps({"data": "UPID:pve1:00000000:00000000:00000000:task:101:root@pam:"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_DELETE(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"data": None}).encode())


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    with socketserver.TCPServer(("127.0.0.1", port), MockProxmoxHandler) as httpd:
        print(httpd.server_address[1], flush=True)
        httpd.serve_forever()


if __name__ == "__main__":
    main()
