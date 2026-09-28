"""Translates tool calls into Proxmox-cli.py subprocess invocations."""

import json
import pathlib
import subprocess
import sys
import os
from typing import Any, Dict, List, Optional

from .spec import HARNESS_ASSEMBLED_TOOLS
from .watchdog import validate_harness_command

class ProxmoxToolExecutor:
    """Executes tools by invoking scripts/Proxmox-cli.py as a subprocess."""

    def __init__(self, cli_path: pathlib.Path, env_path: pathlib.Path):
        self.cli_path = cli_path
        self.env_path = env_path

    def _get_cluster_nodes(self) -> List[str]:
        """Fetch list of online node names from cluster."""
        res = self._raw_execute(["nodes", "list"])
        nodes = []
        if res.get("ok") and isinstance(res.get("data"), list):
            for n in res["data"]:
                if n.get("status") == "online" and n.get("node"):
                    nodes.append(n["node"])
                elif n.get("node"):
                    nodes.append(n["node"])
        return nodes or ["pveheavy", "pvestore"]

    def _get_next_vmid(self) -> int:
        """Fetch next available free VMID."""
        res = self._raw_execute(["vms", "next-id"])
        if res.get("ok") and isinstance(res.get("data"), dict):
            nid = res["data"].get("next_id")
            if nid:
                return int(nid)
        return 100

    def _get_first_template(self, node: str) -> str:
        """Find an available LXC OS template."""
        res = self._raw_execute(["storage", "templates", "--node", node, "--storage", "local"])
        if res.get("ok") and isinstance(res.get("data"), list) and res["data"]:
            return res["data"][0].get("volid", "local:vztmpl/debian-12-standard_12.7-1_amd64.tar.zst")
        return "local:vztmpl/debian-12-standard_12.7-1_amd64.tar.zst"

    def _build_cli_args(self, tool_name: str, args: Dict[str, Any]) -> List[str]:
        """Map tool function name and arguments to Proxmox-cli CLI arguments."""
        node = args.get("node")
        vmid = args.get("vmid")
        name = args.get("name")
        hostname = args.get("hostname")
        storage = args.get("storage")
        ostemplate = args.get("ostemplate")
        memory = args.get("memory")
        cores = args.get("cores")
        net0 = args.get("net0")
        password = args.get("password")
        unprivileged = args.get("unprivileged")
        ssh_keygen = args.get("ssh_keygen")
        kind = args.get("kind")
        live = args.get("live", False)
        url = args.get("url")
        filename = args.get("filename")

        if tool_name == "nodes_list":
            return ["nodes", "list"]
        elif tool_name == "node_status":
            return ["node", "status", "--node", str(node)]
        elif tool_name == "resolve_vmid":
            cmd = ["resolve", "--vmid", str(vmid)]
            if kind:
                cmd.extend(["--kind", str(kind)])
            return cmd
        elif tool_name == "vms_next_id":
            return ["vms", "next-id"]
        elif tool_name == "lxcs_next_id":
            return ["lxcs", "next-id"]
        elif tool_name == "vms_list":
            cmd = ["vms", "list"]
            if node:
                cmd.extend(["--node", str(node)])
            return cmd
        elif tool_name == "lxcs_list":
            cmd = ["lxcs", "list"]
            if node:
                cmd.extend(["--node", str(node)])
            return cmd
        elif tool_name == "vm_status":
            return ["vm", "status", "--node", str(node), "--vmid", str(vmid)]
        elif tool_name == "vm_config":
            return ["vm", "config", "--node", str(node), "--vmid", str(vmid)]
        elif tool_name == "vm_guest_network":
            return ["vm", "guest-network", "--node", str(node), "--vmid", str(vmid)]
        elif tool_name == "lxc_status":
            return ["lxc", "status", "--node", str(node), "--vmid", str(vmid)]
        elif tool_name == "lxc_config":
            return ["lxc", "config", "--node", str(node), "--vmid", str(vmid)]
        elif tool_name == "storage_list":
            cmd = ["storage", "list"]
            if node:
                cmd.extend(["--node", str(node)])
            return cmd
        elif tool_name == "storage_content":
            cmd = ["storage", "content", "--storage", str(storage)]
            if node:
                cmd.extend(["--node", str(node)])
            return cmd
        elif tool_name == "storage_templates":
            cmd = ["storage", "templates", "--storage", str(storage)]
            if node:
                cmd.extend(["--node", str(node)])
            return cmd
        elif tool_name == "storage_download":
            cmd = ["storage", "template-download", "--storage", str(storage), "--url", str(url), "--filename", str(filename), "--execute"]
            if node:
                cmd.extend(["--node", str(node)])
            return cmd
        elif tool_name == "templates_registry":
            cmd = ["templates", "registry", "--node", str(node or self._get_cluster_nodes()[0])]
            if args.get("section"):
                cmd.extend(["--section", str(args["section"])])
            if args.get("search"):
                cmd.extend(["--search", str(args["search"])])
            return cmd
        elif tool_name == "templates_download":
            return ["templates", "download", "--node", str(node), "--storage", str(storage),
                    "--template", str(args["template"]), "--execute", "--wait"]
        elif tool_name == "tasks_recent":
            cmd = ["tasks", "recent"]
            if node:
                cmd.extend(["--node", str(node)])
            return cmd
        elif tool_name == "doctor":
            cmd = ["doctor"]
            if live:
                cmd.append("--live")
            return cmd
        elif tool_name == "health":
            return ["health"]

        # VM Management & Creation
        elif tool_name == "vm_create":
            vmid_val = vmid or self._get_next_vmid()
            cmd = ["vm", "create", "--node", str(node), "--vmid", str(vmid_val), "--name", str(name), "--execute", "--wait"]
            if memory:
                cmd.extend(["--memory", str(memory)])
            if cores:
                cmd.extend(["--cores", str(cores)])
            if storage:
                cmd.extend(["--storage", str(storage)])
            if net0:
                cmd.extend(["--net0", str(net0)])
            if args.get("ostype"):
                cmd.extend(["--ostype", str(args["ostype"])])
            return cmd
        elif tool_name == "vm_start":
            return ["vm", "start", "--node", str(node), "--vmid", str(vmid), "--execute", "--wait"]
        elif tool_name == "vm_shutdown":
            return ["vm", "shutdown", "--node", str(node), "--vmid", str(vmid), "--execute", "--wait"]
        elif tool_name == "vm_reboot":
            return ["vm", "reboot", "--node", str(node), "--vmid", str(vmid), "--execute", "--wait"]
        elif tool_name == "vm_snapshot_create":
            return ["vm", "snapshot", "create", "--node", str(node), "--vmid", str(vmid), "--name", str(name), "--execute"]
        elif tool_name == "vm_snapshot_delete":
            return ["vm", "snapshot", "delete", "--node", str(node), "--vmid", str(vmid), "--name", str(name), "--execute", "--force", "--confirm", str(vmid)]
        elif tool_name == "vm_stop":
            return ["vm", "stop", "--node", str(node), "--vmid", str(vmid), "--execute", "--force", "--confirm", str(vmid)]
        elif tool_name == "vm_delete":
            return ["vm", "delete", "--node", str(node), "--vmid", str(vmid), "--execute", "--force", "--confirm", str(vmid)]
        elif tool_name == "vm_exec":
            cmd = ["vm", "exec", "--node", str(node), "--vmid", str(vmid), "--command", str(args["command"]), "--execute"]
            if args.get("timeout"):
                cmd.extend(["--timeout", str(args["timeout"])])
            return cmd

        # LXC Management & Creation
        elif tool_name == "lxc_create":
            vmid_val = vmid or self._get_next_vmid()
            tmpl_val = ostemplate or self._get_first_template(node)
            stg_val = storage or "local-lvm"
            cmd = [
                "lxc", "create",
                "--node", str(node),
                "--vmid", str(vmid_val),
                "--hostname", str(hostname),
                "--ostemplate", str(tmpl_val),
                "--storage", str(stg_val),
                "--execute",
                "--wait",
            ]
            if memory:
                cmd.extend(["--memory", str(memory)])
            if cores:
                cmd.extend(["--cores", str(cores)])
            if net0:
                cmd.extend(["--net0", str(net0)])
            else:
                cmd.extend(["--net0", "name=eth0,bridge=vmbr0,ip=dhcp"])
            if password:
                cmd.extend(["--password", str(password)])
            if unprivileged is not None:
                cmd.append("--unprivileged" if unprivileged else "--no-unprivileged")
            if ssh_keygen:
                cmd.append("--ssh-keygen")
            return cmd
        elif tool_name == "lxc_clone":
            new_vmid = args.get("new_vmid") or vmid or self._get_next_vmid()
            cmd = [
                "lxc", "clone",
                "--node", str(node),
                "--vmid", str(vmid),
                "--new-vmid", str(new_vmid),
                "--execute",
                "--wait",
            ]
            if hostname:
                cmd.extend(["--hostname", str(hostname)])
            if storage:
                cmd.extend(["--storage", str(storage)])
            if args.get("pool"):
                cmd.extend(["--pool", str(args["pool"])])
            if args.get("snapname"):
                cmd.extend(["--snapname", str(args["snapname"])])
            return cmd
        elif tool_name == "vm_clone":
            new_vmid = args.get("new_vmid") or vmid or self._get_next_vmid()
            cmd = [
                "vm", "clone",
                "--node", str(node),
                "--vmid", str(vmid),
                "--new-vmid", str(new_vmid),
                "--execute",
                "--wait",
            ]
            if name:
                cmd.extend(["--name", str(name)])
            if storage:
                cmd.extend(["--storage", str(storage)])
            if args.get("full") is not None:
                cmd.append("--full" if args["full"] else "--no-full")
            return cmd
        elif tool_name == "lxc_start":
            return ["lxc", "start", "--node", str(node), "--vmid", str(vmid), "--execute", "--wait"]
        elif tool_name == "lxc_shutdown":
            return ["lxc", "shutdown", "--node", str(node), "--vmid", str(vmid), "--execute", "--wait"]
        elif tool_name == "lxc_reboot":
            return ["lxc", "reboot", "--node", str(node), "--vmid", str(vmid), "--execute", "--wait"]
        elif tool_name == "lxc_snapshot_create":
            return ["lxc", "snapshot", "create", "--node", str(node), "--vmid", str(vmid), "--name", str(name), "--execute"]
        elif tool_name == "lxc_snapshot_delete":
            return ["lxc", "snapshot", "delete", "--node", str(node), "--vmid", str(vmid), "--name", str(name), "--execute", "--force", "--confirm", str(vmid)]
        elif tool_name == "lxc_stop":
            return ["lxc", "stop", "--node", str(node), "--vmid", str(vmid), "--execute", "--force", "--confirm", str(vmid)]
        elif tool_name == "lxc_delete":
            return ["lxc", "delete", "--node", str(node), "--vmid", str(vmid), "--execute", "--force", "--confirm", str(vmid)]
        elif tool_name == "lxc_exec":
            cmd = ["lxc", "exec", "--node", str(node), "--vmid", str(vmid), "--command", str(args["command"]), "--execute"]
            if args.get("user"):
                cmd.extend(["--user", str(args["user"])])
            if args.get("password"):
                cmd.extend(["--password", str(args["password"])])
            if args.get("timeout"):
                cmd.extend(["--request-timeout", str(args["timeout"])])
            return cmd

        elif tool_name == "lxc_pct_exec":
            # Harness-assembled. The model supplies vmid and command only; the
            # node, the pct invocation and the shell are fixed here, and the
            # watchdog has already refused anything that is not exactly this.
            cmd = ["lxc", "pct-exec", "--vmid", str(vmid), "--command", str(args["command"]), "--execute"]
            if node and node != "N/A":
                cmd.extend(["--node", str(node)])
            if args.get("timeout"):
                cmd.extend(["--request-timeout", str(args["timeout"])])
            return cmd

        # SSH Command Execution
        elif tool_name == "ssh_exec":
            cmd = ["ssh", "exec", "--command", str(args["command"]), "--execute"]
            if args.get("host"):
                cmd.extend(["--host", str(args["host"])])
            if node and node != "N/A":
                cmd.extend(["--node", str(node)])
            if vmid and vmid != "Auto":
                cmd.extend(["--vmid", str(vmid)])
            if args.get("kind"):
                cmd.extend(["--kind", str(args["kind"])])
            if args.get("user"):
                cmd.extend(["--user", str(args["user"])])
            if args.get("port"):
                cmd.extend(["--port", str(args["port"])])
            if args.get("key_file"):
                cmd.extend(["--key-file", str(args["key_file"])])
            if args.get("password"):
                cmd.extend(["--password", str(args["password"])])
            if args.get("timeout"):
                cmd.extend(["--request-timeout", str(args["timeout"])])
            return cmd

        raise ValueError(f"Unknown tool name: {tool_name}")

    @staticmethod
    def _parse_cli_json_blobs(text: str) -> List[Dict[str, Any]]:
        """Parse one or more concatenated JSON documents from CLI output.

        Older CLI builds could emit two JSON docs (SSH fallback + termproxy)
        on the same stream. Prefer the LAST parseable object (the final result).
        """
        docs: List[Dict[str, Any]] = []
        if not text or not text.strip():
            return docs
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return [parsed]
        except json.JSONDecodeError:
            pass
        decoder = json.JSONDecoder()
        idx = 0
        text = text.strip()
        while idx < len(text):
            while idx < len(text) and text[idx] not in "{[":
                # Skip inter-document whitespace/garbage
                idx += 1
            if idx >= len(text):
                break
            try:
                obj, end = decoder.raw_decode(text[idx:])
                if isinstance(obj, dict):
                    docs.append(obj)
                idx += end
            except json.JSONDecodeError:
                idx += 1
        return docs

    def _raw_execute(self, cli_args: List[str], secret: Optional[str] = None) -> Dict[str, Any]:
        """Execute CLI command directly and parse JSON (stdout or stderr).

        `secret` is handed to the child on an inherited pipe file descriptor
        rather than in argv, so a prompted node password never appears in the
        process table or the environment. The data is buffered by the kernel
        before the child starts, so writing before the spawn cannot deadlock.
        """
        full_cmd = [
            sys.executable,
            str(self.cli_path),
            "--env-file", str(self.env_path),
            "--format", "json"
        ] + cli_args

        read_fd = write_fd = None
        if secret is not None:
            read_fd, write_fd = os.pipe()
            try:
                os.write(write_fd, secret.encode("utf-8")[:4096] + b"\n")
            except OSError:
                os.close(read_fd)
                os.close(write_fd)
                read_fd = write_fd = None
            else:
                os.close(write_fd)
                write_fd = None
                full_cmd = full_cmd + ["--node-password-fd", str(read_fd)]

        try:
            proc = subprocess.run(
                full_cmd,
                capture_output=True,
                text=True,
                timeout=180,
                pass_fds=(read_fd,) if read_fd is not None else (),
            )
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        finally:
            if read_fd is not None:
                try:
                    os.close(read_fd)
                except OSError:
                    pass

        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()
        # CLI sends failures to stderr, successes to stdout — check both.
        # Take the LAST parseable doc (final result wins over intermediate fallback output).
        for stream_text in (stdout, stderr):
            docs = self._parse_cli_json_blobs(stream_text)
            if docs:
                return docs[-1]
        combined = (stdout + stderr).strip()
        if combined:
            docs = self._parse_cli_json_blobs(combined)
            if docs:
                return docs[-1]
            return {"ok": proc.returncode == 0, "raw_output": combined[:2000], "returncode": proc.returncode}
        return {"ok": proc.returncode == 0, "error": f"Process exited with code {proc.returncode}"}

    def execute(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        secret: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Execute a tool command, automatically handling multi-node queries and auto-stop before delete.

        `secret` is forwarded to a harness-assembled transport (currently only
        `lxc_pct_exec`) over a pipe file descriptor, never through `arguments`.
        """

        # Second enforcement layer. The watchdog in agent.py already refuses
        # malformed harness-assembled calls, but the executor is also reachable
        # directly (tests, the broker, any future caller), so the check is
        # repeated here rather than assumed.
        if tool_name in HARNESS_ASSEMBLED_TOOLS:
            allowed, reason = validate_harness_command(tool_name, arguments)
            if not allowed:
                return {"ok": False, "error": reason, "command": None, "dry_run": False}

        # Multi-node aggregation for VMs and LXCs
        if tool_name in ("vms_list", "lxcs_list") and not arguments.get("node"):
            nodes = self._get_cluster_nodes()
            all_items: List[Dict[str, Any]] = []
            sub_res_type = "vms" if tool_name == "vms_list" else "lxcs"
            for n in nodes:
                sub_res = self._raw_execute([sub_res_type, "list", "--node", n])
                if sub_res.get("ok") and isinstance(sub_res.get("data"), list):
                    for item in sub_res["data"]:
                        if isinstance(item, dict):
                            item.setdefault("node", n)
                            all_items.append(item)
            return {"ok": True, "data": all_items, "command": f"{sub_res_type} list (all nodes)"}

        # Multi-node aggregation and capability annotation for storage_list
        if tool_name == "storage_list":
            node = arguments.get("node")
            target_nodes = [node] if node else self._get_cluster_nodes()
            all_storage: List[Dict[str, Any]] = []
            seen_pools = set()
            for n in target_nodes:
                sub_res = self._raw_execute(["storage", "list", "--node", n])
                if sub_res.get("ok") and isinstance(sub_res.get("data"), list):
                    for st in sub_res["data"]:
                        if isinstance(st, dict):
                            pool_name = st.get("storage")
                            unique_key = (n, pool_name) if not st.get("shared") else ("shared", pool_name)
                            if unique_key in seen_pools:
                                continue
                            seen_pools.add(unique_key)
                            st.setdefault("node", n)
                            # Annotate what this storage pool supports
                            content_str = st.get("content", "")
                            supports = []
                            if "rootdir" in content_str:
                                supports.append("LXC rootfs")
                            if "images" in content_str:
                                supports.append("VM disks")
                            if "vztmpl" in content_str:
                                supports.append("LXC Templates")
                            if "iso" in content_str:
                                supports.append("ISO Images")
                            if "backup" in content_str:
                                supports.append("Backups")
                            st["suitable_for"] = supports
                            all_storage.append(st)
            return {"ok": True, "data": all_storage, "command": f"storage list ({node or 'all nodes'})"}

        # Auto-stop running container/VM before deletion if user confirmed deletion
        if tool_name in ("lxc_delete", "vm_delete"):
            node = arguments.get("node") or self._get_cluster_nodes()[0]
            vmid = arguments.get("vmid")
            if node and vmid:
                res_type = "lxc" if tool_name == "lxc_delete" else "vm"
                st_res = self._raw_execute([res_type, "status", "--node", str(node), "--vmid", str(vmid)])
                if st_res.get("ok") and isinstance(st_res.get("data"), dict) and st_res["data"].get("status") == "running":
                    self._raw_execute([res_type, "stop", "--node", str(node), "--vmid", str(vmid), "--execute", "--force", "--confirm", str(vmid)])

        # Template Discovery: VM Templates, LXC Templates, and OS Tarball Templates
        if tool_name == "templates_list":
            kind = arguments.get("kind", "all")
            node = arguments.get("node")
            target_nodes = [node] if node else self._get_cluster_nodes()
            vm_templates: List[Dict[str, Any]] = []
            lxc_templates: List[Dict[str, Any]] = []
            os_tarballs: List[Dict[str, Any]] = []

            if kind in ("all", "vm"):
                for n in target_nodes:
                    sub = self._raw_execute(["vms", "list", "--node", n])
                    if sub.get("ok") and isinstance(sub.get("data"), list):
                        for item in sub["data"]:
                            if item.get("template") == 1 or item.get("template") is True:
                                item.setdefault("node", n)
                                item["creation_method"] = "vm_clone"
                                vm_templates.append(item)

            if kind in ("all", "lxc"):
                for n in target_nodes:
                    sub = self._raw_execute(["lxcs", "list", "--node", n])
                    if sub.get("ok") and isinstance(sub.get("data"), list):
                        for item in sub["data"]:
                            if item.get("template") == 1 or item.get("template") is True:
                                item.setdefault("node", n)
                                item["creation_method"] = "lxc_clone"
                                lxc_templates.append(item)

            if kind in ("all", "os_template"):
                for n in target_nodes:
                    sub = self._raw_execute(["storage", "templates", "--node", n, "--storage", "local"])
                    if sub.get("ok") and isinstance(sub.get("data"), list):
                        for item in sub["data"]:
                            item.setdefault("node", n)
                            item["creation_method"] = "lxc_create"
                            os_tarballs.append(item)

            return {
                "ok": True,
                "data": {
                    "vm_templates": vm_templates,
                    "lxc_templates": lxc_templates,
                    "lxc_os_tarballs": os_tarballs,
                },
                "total_vm_templates": len(vm_templates),
                "total_lxc_templates": len(lxc_templates),
                "total_os_tarballs": len(os_tarballs),
                "usage": "vm_templates -> vm_clone; lxc_templates (template:1 VMIDs) -> lxc_clone; lxc_os_tarballs (storage:vztmpl/...) -> lxc_create. Never pass a template VMID as --ostemplate.",
                "command": f"templates list ({kind})"
            }

        try:
            cli_args = self._build_cli_args(tool_name, arguments)
        except Exception as err:
            return {"ok": False, "error": f"Invalid tool arguments: {err}"}

        # Only transports that accept the pipe channel may be handed a secret.
        if secret is not None and tool_name not in HARNESS_ASSEMBLED_TOOLS:
            return {"ok": False, "error": f"{tool_name} does not accept a secret channel."}

        return self._raw_execute(cli_args, secret=secret)
