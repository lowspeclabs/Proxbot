import argparse
import contextlib
import importlib.util
import io
import json
import pathlib
import tempfile
import unittest
from unittest import mock


SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "Proxmox-cli.py"
SPEC = importlib.util.spec_from_file_location("proxmox_cli", SCRIPT)
cli = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(cli)


def _fake_lxc_exec(result):
    """Build a stand-in for lxc_exec with a fixed return value."""
    def _fake(*_args, **_kwargs):
        return result
    return _fake


class CliContractTests(unittest.TestCase):
    def test_raw_delete_accepts_its_required_guards(self):
        args = cli.build_parser().parse_args([
            "api", "delete", "/nodes/pve/qemu/101", "--execute", "--force", "--confirm", "/nodes/pve/qemu/101",
        ])
        self.assertTrue(args.execute)
        self.assertTrue(args.force)
        self.assertEqual(args.confirm, "/nodes/pve/qemu/101")

    def test_vmid_is_required_for_vm_operations(self):
        with self.assertRaises(SystemExit):
            cli.build_parser().parse_args(["vm", "status", "--node", "pve"])

    def test_global_json_format_works_for_local_command_catalog(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = cli.main(["--format", "json", "commands", "--no-log"])
        self.assertEqual(exit_code, cli.EXIT_SUCCESS)
        result = json.loads(stdout.getvalue())
        self.assertTrue(result["ok"])
        self.assertEqual(result["command"], "commands")

    def test_relative_log_directory_is_repository_relative(self):
        config = cli.Config({"PROXMOX_LOG_DIR": "custom-logs"})
        self.assertEqual(config.log_dir, cli.REPO_ROOT / "custom-logs")

    def test_explicit_env_file_has_priority(self):
        path = pathlib.Path("/tmp/proxmox-test.env")
        self.assertEqual(cli.resolve_env_path(str(path)), path)

    def test_unrelated_cwd_env_does_not_override_skill_env(self):
        with tempfile.TemporaryDirectory() as directory:
            pathlib.Path(directory, ".env").write_text("SMALLCTL_MODEL=test\n", encoding="utf-8")
            with mock.patch("pathlib.Path.cwd", return_value=pathlib.Path(directory)):
                self.assertEqual(cli.resolve_env_path(), cli.ENV_PATH)

    def test_proxmox_cwd_env_overrides_skill_env(self):
        with tempfile.TemporaryDirectory() as directory:
            cwd_env = pathlib.Path(directory, ".env")
            cwd_env.write_text("PROXMOX_API_URL=https://pve.example/api2/json\n", encoding="utf-8")
            with mock.patch("pathlib.Path.cwd", return_value=pathlib.Path(directory)):
                self.assertEqual(cli.resolve_env_path(), cwd_env)

    def test_config_records_the_selected_env_file(self):
        config = cli.Config({})
        self.assertEqual(config.env_path, cli.ENV_PATH)

    def test_raw_write_dry_run_contains_validated_plan(self):
        args = argparse.Namespace(
            method="post",
            endpoint="/nodes/pve/qemu/101/config",
            data='{"memory": 2048}',
            execute=False,
            force=False,
            confirm=None,
            node=None,
            wait=False,
            wait_timeout=120,
            format="json",
        )
        config = cli.Config({
            "PROXMOX_API_URL": "https://pve.example/api2/json",
            "PROXMOX_API_TOKEN_ID": "agent@pve!test",
            "PROXMOX_API_TOKEN_SECRET": "secret",
        })
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = cli.cmd_api_raw(args, config, None, cli.Logger(config.log_dir, no_log=True, config=config))
        self.assertEqual(exit_code, cli.EXIT_SUCCESS)
        result = json.loads(stdout.getvalue())
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["data"]["body"], {"memory": 2048})

    def test_lifecycle_dry_run_contains_resource_target(self):
        args = argparse.Namespace(node="pve", vmid=101, execute=False, force=False, confirm=None, wait=False, wait_timeout=120, format="json")
        config = cli.Config({
            "PROXMOX_API_URL": "https://pve.example/api2/json",
            "PROXMOX_API_TOKEN_ID": "agent@pve!test",
            "PROXMOX_API_TOKEN_SECRET": "secret",
        })
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = cli.cmd_vm_start(args, config, None, cli.Logger(config.log_dir, no_log=True, config=config))
        self.assertEqual(exit_code, cli.EXIT_SUCCESS)
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["data"]["target"], {"node": "pve", "vmid": "101", "action": "start"})

    def test_filter_rows_applies_status_before_limit(self):
        args = argparse.Namespace(status="running", limit=1)
        rows = [{"vmid": 100, "status": "stopped"}, {"vmid": 101, "status": "running"}, {"vmid": 102, "status": "running"}]
        self.assertEqual(cli.filter_rows(rows, args), [{"vmid": 101, "status": "running"}])

    def test_resolve_returns_node_and_canonical_next_step(self):
        class Client:
            def get(self, endpoint):
                self.endpoint = endpoint
                return {"ok": True, "status_code": 200, "duration_ms": 1, "data": [{"vmid": 101, "type": "qemu", "node": "pve", "name": "lab"}], "error": None}

        args = argparse.Namespace(vmid=101, kind="vm", format="json")
        config = cli.Config({
            "PROXMOX_API_URL": "https://pve.example/api2/json",
            "PROXMOX_API_TOKEN_ID": "agent@pve!test",
            "PROXMOX_API_TOKEN_SECRET": "secret",
        })
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = cli.cmd_resolve(args, config, Client(), cli.Logger(config.log_dir, no_log=True, config=config))
        self.assertEqual(exit_code, cli.EXIT_SUCCESS)
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["data"][0]["node"], "pve")
        self.assertEqual(result["next_step"], "Use vm status --node pve --vmid 101.")

    def test_guest_network_uses_qemu_agent_endpoint(self):
        class Client:
            def get(self, endpoint):
                self.endpoint = endpoint
                return {"ok": True, "status_code": 200, "duration_ms": 1, "data": {"result": [{"name": "eth0"}]}, "error": None}

        args = argparse.Namespace(node="pve", vmid=101, format="json")
        config = cli.Config({
            "PROXMOX_API_URL": "https://pve.example/api2/json",
            "PROXMOX_API_TOKEN_ID": "agent@pve!test",
            "PROXMOX_API_TOKEN_SECRET": "secret",
        })
        client = Client()
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = cli.cmd_vm_guest_network(args, config, client, cli.Logger(config.log_dir, no_log=True, config=config))
        self.assertEqual(exit_code, cli.EXIT_SUCCESS)
        self.assertEqual(client.endpoint, "/nodes/pve/qemu/101/agent/network-get-interfaces")
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["target"], {"node": "pve", "vmid": 101})

    def test_guest_network_reports_acl_hint_for_permission_failures(self):
        class Client:
            def get(self, endpoint):
                return {"ok": False, "status_code": 403, "duration_ms": 1, "data": None, "error": "Permission check failed"}

        args = argparse.Namespace(node="pve", vmid=101, format="json")
        config = cli.Config({
            "PROXMOX_API_URL": "https://pve.example/api2/json",
            "PROXMOX_API_TOKEN_ID": "agent@pve!test",
            "PROXMOX_API_TOKEN_SECRET": "secret",
        })
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            exit_code = cli.cmd_vm_guest_network(args, config, Client(), cli.Logger(config.log_dir, no_log=True, config=config))
        self.assertEqual(exit_code, cli.EXIT_AUTH)
        result = json.loads(stderr.getvalue())
        self.assertIn("Permission check failed", result["error"])

    def test_guest_network_diagnoses_agent_not_configured_in_proxmox(self):
        mock_client = mock.MagicMock()
        mock_client.get.side_effect = [
            # Initial network-get-interfaces call fails
            {"ok": False, "status_code": 500, "duration_ms": 1, "data": None, "error": "QEMU guest agent is not configured"},
            # Diagnostic status call
            {"ok": True, "data": {"status": "running"}},
            # Diagnostic config call (agent option missing)
            {"ok": True, "data": {"cores": 4, "memory": 4096}},
        ]
        args = argparse.Namespace(node="pve", vmid=101, format="json")
        config = cli.Config({
            "PROXMOX_API_URL": "https://pve.example/api2/json",
            "PROXMOX_API_TOKEN_ID": "agent@pve!test",
            "PROXMOX_API_TOKEN_SECRET": "secret",
        })
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            exit_code = cli.cmd_vm_guest_network(args, config, mock_client, cli.Logger(config.log_dir, no_log=True, config=config))
        self.assertEqual(exit_code, cli.EXIT_API)
        result = json.loads(stderr.getvalue())
        self.assertFalse(result["ok"])
        diag = result["agent_diagnosis"]
        self.assertEqual(diag["agent_status"], "not_configured_in_proxmox")
        self.assertIn("agent: 1", diag["hint"])


    def test_lxc_exec_dry_run(self):
        args = argparse.Namespace(node="pve", vmid=100, command="uptime", timeout=30, execute=False, format="json")
        config = cli.Config({
            "PROXMOX_API_URL": "https://pve.example/api2/json",
            "PROXMOX_API_TOKEN_ID": "agent@pve!test",
            "PROXMOX_API_TOKEN_SECRET": "secret",
        })
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = cli.cmd_lxc_exec(args, config, None, cli.Logger(config.log_dir, no_log=True, config=config))
        self.assertEqual(exit_code, cli.EXIT_SUCCESS)
        result = json.loads(stdout.getvalue())
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["data"]["body"], {"command": "uptime", "user": "root", "console": False})
        # The console must not appear in the transport list unless requested.
        self.assertEqual(result["data"]["target"]["transports"], ["ssh"])

    def test_lxc_exec_live(self):
        class Client:
            def post(self, endpoint, body):
                return {"ok": True, "status_code": 200, "duration_ms": 1, "data": {"port": 5900, "ticket": "tick", "user": "root@pam"}, "error": None}

            def get(self, endpoint):
                if "/tasks/" in endpoint:
                    return {"ok": True, "status_code": 200, "duration_ms": 1,
                            "data": {"status": "stopped", "exitstatus": "OK"}, "error": None}
                return {"ok": True, "status_code": 200, "duration_ms": 1, "data": {}, "error": None}

        args = argparse.Namespace(node="pve", vmid=100, command="uname -r", console=True, timeout=30, execute=True, format="json")
        config = cli.Config({
            "PROXMOX_API_URL": "https://pve.example/api2/json",
            "PROXMOX_API_TOKEN_ID": "agent@pve!test",
            "PROXMOX_API_TOKEN_SECRET": "secret",
        })
        client = Client()
        stdout = io.StringIO()
        with unittest.mock.patch.dict(cli.cmd_lxc_exec.__globals__, {"lxc_exec": _fake_lxc_exec({"ok": True, "exit_code": 0, "stdout": "Linux 6.8\n", "stderr": "", "method": "termproxy"})}):
            with contextlib.redirect_stdout(stdout):
                exit_code = cli.cmd_lxc_exec(args, config, client, cli.Logger(config.log_dir, no_log=True, config=config))
        self.assertEqual(exit_code, cli.EXIT_SUCCESS)
        result = json.loads(stdout.getvalue())
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["stdout"], "Linux 6.8\n")
        self.assertEqual(result["data"]["exit_code"], 0)

    def test_lxc_exec_login_required(self):
        args = argparse.Namespace(node="pve", vmid=100, command="uname -r", user="root", password=None, no_auto_inject_key=False, key_file=None, console=True, timeout=30, execute=True, format="json")
        config = cli.Config({
            "PROXMOX_API_URL": "https://pve.example/api2/json",
            "PROXMOX_API_TOKEN_ID": "agent@pve!test",
            "PROXMOX_API_TOKEN_SECRET": "secret",
        })
        stderr = io.StringIO()
        with unittest.mock.patch.dict(cli.cmd_lxc_exec.__globals__, {"lxc_exec": _fake_lxc_exec({"ok": False, "status": "login_required", "prompt": "login: ", "vmid": "100", "node": "pve", "error": "Login required"})}):
            with contextlib.redirect_stderr(stderr):
                exit_code = cli.cmd_lxc_exec(args, config, None, cli.Logger(config.log_dir, no_log=True, config=config))
        self.assertEqual(exit_code, cli.EXIT_AUTH)
        result = json.loads(stderr.getvalue())
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "login_required")

    def test_lxc_exec_console_unavailable(self):
        args = argparse.Namespace(node="pve", vmid=100, command="uname -r", user="root", password="secretpassword", no_auto_inject_key=False, key_file=None, console=True, timeout=30, execute=True, format="json")
        config = cli.Config({
            "PROXMOX_API_URL": "https://pve.example/api2/json",
            "PROXMOX_API_TOKEN_ID": "agent@pve!test",
            "PROXMOX_API_TOKEN_SECRET": "secret",
        })
        stderr = io.StringIO()
        silent = {
            "ok": False,
            "status": "console_unavailable",
            "vmid": "100",
            "node": "pve",
            "error": "Container 100 console is silent - no login or password prompt was received on tty1",
            "method": "termproxy",
        }
        with unittest.mock.patch.dict(cli.cmd_lxc_exec.__globals__, {"lxc_exec": _fake_lxc_exec(silent)}):
            with contextlib.redirect_stderr(stderr):
                exit_code = cli.cmd_lxc_exec(args, config, None, cli.Logger(config.log_dir, no_log=True, config=config))
        # Not EXIT_AUTH: no credential was tested, so the chat harness must not
        # escalate to a getpass prompt.
        self.assertEqual(exit_code, cli.EXIT_INTERNAL)
        result = json.loads(stderr.getvalue())
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "console_unavailable")

    def test_lxc_exec_login_rejected_is_distinct_from_timeout(self):
        args = argparse.Namespace(node="pve", vmid=100, command="uptime", user="root", password=None, no_auto_inject_key=False, key_file=None, console=True, timeout=30, execute=True, format="json")
        config = cli.Config({
            "PROXMOX_API_URL": "https://pve.example/api2/json",
            "PROXMOX_API_TOKEN_ID": "agent@pve!test",
            "PROXMOX_API_TOKEN_SECRET": "secret",
        })
        stderr = io.StringIO()
        rejected = {
            "ok": False,
            "status": "login_failed",
            "vmid": "100",
            "node": "pve",
            "error": "Console login rejected by container 100 - wrong username or password.",
            "method": "termproxy",
        }
        with unittest.mock.patch.dict(cli.cmd_lxc_exec.__globals__, {"lxc_exec": _fake_lxc_exec(rejected)}):
            with contextlib.redirect_stderr(stderr):
                exit_code = cli.cmd_lxc_exec(args, config, None, cli.Logger(config.log_dir, no_log=True, config=config))
        self.assertEqual(exit_code, cli.EXIT_INTERNAL)
        result = json.loads(stderr.getvalue())
        self.assertEqual(result["status"], "login_failed")
        self.assertIn("rejected", result["error"])

    def test_lxc_exec_transport_failure_is_not_login_required(self):
        args = argparse.Namespace(node="pve", vmid=100, command="uptime", user="root", password=None, no_auto_inject_key=False, key_file=None, console=True, timeout=30, execute=True, format="json")
        config = cli.Config({
            "PROXMOX_API_URL": "https://pve.example/api2/json",
            "PROXMOX_API_TOKEN_ID": "agent@pve!test",
            "PROXMOX_API_TOKEN_SECRET": "secret",
        })
        stderr = io.StringIO()
        with unittest.mock.patch.dict(cli.cmd_lxc_exec.__globals__, {"lxc_exec": _fake_lxc_exec(None)}):
            with contextlib.redirect_stderr(stderr):
                exit_code = cli.cmd_lxc_exec(args, config, None, cli.Logger(config.log_dir, no_log=True, config=config))
        self.assertEqual(exit_code, cli.EXIT_INTERNAL)
        result = json.loads(stderr.getvalue())
        self.assertEqual(result["status"], "console_unavailable")

    def test_console_is_never_used_without_optin(self):
        # Without --console, termproxy must not be called at all. The console is
        # a single shared pty and attaching can type into a human's live shell.
        def _explode(*_args, **_kwargs):
            raise AssertionError("lxc_exec must not be called without --console")

        args = argparse.Namespace(node="pve", vmid=100, command="uptime", user="root", password=None,
                                  no_auto_inject_key=False, key_file=None, console=False, timeout=30,
                                  execute=True, format="json")
        config = cli.Config({
            "PROXMOX_API_URL": "https://pve.example/api2/json",
            "PROXMOX_API_TOKEN_ID": "agent@pve!test",
            "PROXMOX_API_TOKEN_SECRET": "secret",
        })
        stderr = io.StringIO()
        with unittest.mock.patch.dict(cli.cmd_lxc_exec.__globals__, {"lxc_exec": _explode}):
            with contextlib.redirect_stderr(stderr):
                exit_code = cli.cmd_lxc_exec(args, config, None, cli.Logger(config.log_dir, no_log=True, config=config))
        self.assertEqual(exit_code, cli.EXIT_INTERNAL)
        result = json.loads(stderr.getvalue())
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "console_not_requested")
        self.assertIn("--console", result["error"])

    def test_missing_console_attribute_defaults_to_no_console(self):
        # Older callers build a Namespace without `console`; absent must mean
        # "not requested", never "allowed".
        def _explode(*_args, **_kwargs):
            raise AssertionError("lxc_exec must not be called when console is absent")

        args = argparse.Namespace(node="pve", vmid=100, command="uptime", user="root", password=None,
                                  no_auto_inject_key=False, key_file=None, timeout=30, execute=True, format="json")
        config = cli.Config({
            "PROXMOX_API_URL": "https://pve.example/api2/json",
            "PROXMOX_API_TOKEN_ID": "agent@pve!test",
            "PROXMOX_API_TOKEN_SECRET": "secret",
        })
        stderr = io.StringIO()
        with unittest.mock.patch.dict(cli.cmd_lxc_exec.__globals__, {"lxc_exec": _explode}):
            with contextlib.redirect_stderr(stderr):
                exit_code = cli.cmd_lxc_exec(args, config, None, cli.Logger(config.log_dir, no_log=True, config=config))
        self.assertEqual(exit_code, cli.EXIT_INTERNAL)
        self.assertEqual(json.loads(stderr.getvalue())["status"], "console_not_requested")

    def test_console_optin_does_not_bypass_ssh(self):
        # With --console the SSH path still runs first and its result wins.
        class Client:
            def post(self, endpoint, body):
                return {"ok": True, "status_code": 200, "duration_ms": 1,
                        "data": {"port": 5900, "ticket": "tick", "user": "root@pam"}, "error": None}

            def get(self, endpoint):
                if endpoint.endswith("/interfaces"):
                    return {"ok": True, "status_code": 200, "duration_ms": 1,
                            "data": [{"name": "eth0", "inet": "10.0.0.5/24"}], "error": None}
                return {"ok": True, "status_code": 200, "duration_ms": 1, "data": {}, "error": None}

        def _explode(*_args, **_kwargs):
            raise AssertionError("termproxy must not be used when SSH succeeds")

        args = argparse.Namespace(node="pve", vmid=100, command="uptime", user="root", password="pw",
                                  no_auto_inject_key=False, key_file="/nonexistent/key", console=True,
                                  port=22, timeout=30, execute=True, format="json")
        config = cli.Config({
            "PROXMOX_API_URL": "https://pve.example/api2/json",
            "PROXMOX_API_TOKEN_ID": "agent@pve!test",
            "PROXMOX_API_TOKEN_SECRET": "secret",
        })
        stdout = io.StringIO()
        with unittest.mock.patch.dict(cli.cmd_ssh_exec.__globals__, {"_run_ssh_command": lambda **kw: (0, "uptime ok\n", "")}), \
             unittest.mock.patch.dict(cli.cmd_lxc_exec.__globals__, {"lxc_exec": _explode}):
            with contextlib.redirect_stdout(stdout):
                exit_code = cli.cmd_lxc_exec(args, config, Client(), cli.Logger(config.log_dir, no_log=True, config=config))
        self.assertEqual(exit_code, cli.EXIT_SUCCESS)
        self.assertIn("uptime ok", stdout.getvalue())

    def test_lxc_exec_with_password(self):
        args = argparse.Namespace(node="pve", vmid=100, command="uname -r", user="root", password="secretpassword", no_auto_inject_key=False, key_file=None, console=True, timeout=30, execute=True, format="json")
        config = cli.Config({
            "PROXMOX_API_URL": "https://pve.example/api2/json",
            "PROXMOX_API_TOKEN_ID": "agent@pve!test",
            "PROXMOX_API_TOKEN_SECRET": "secret",
        })
        stdout = io.StringIO()
        with unittest.mock.patch.dict(cli.cmd_lxc_exec.__globals__, {"lxc_exec": _fake_lxc_exec({"ok": True, "exit_code": 0, "stdout": "Linux 6.8\n", "stderr": "", "method": "termproxy_authenticated"})}):
            with contextlib.redirect_stdout(stdout):
                exit_code = cli.cmd_lxc_exec(args, config, None, cli.Logger(config.log_dir, no_log=True, config=config))
        self.assertEqual(exit_code, cli.EXIT_SUCCESS)
        result = json.loads(stdout.getvalue())
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["method"], "termproxy_authenticated")

    def test_ssh_exec_dry_run(self):
        args = argparse.Namespace(host="192.168.1.50", node=None, vmid=None, kind=None, command="whoami", user="root", port=22, key_file=None, timeout=30, execute=False, format="json")
        config = cli.Config({
            "PROXMOX_API_URL": "https://pve.example/api2/json",
            "PROXMOX_API_TOKEN_ID": "agent@pve!test",
            "PROXMOX_API_TOKEN_SECRET": "secret",
        })
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = cli.cmd_ssh_exec(args, config, None, cli.Logger(config.log_dir, no_log=True, config=config))
        self.assertEqual(exit_code, cli.EXIT_SUCCESS)
        result = json.loads(stdout.getvalue())
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["data"]["target"]["target"], "root@192.168.1.50:22")

    def test_ssh_exec_live_subprocess(self):
        args = argparse.Namespace(host="192.168.1.50", node=None, vmid=None, kind=None, command="whoami", user="root", port=22, key_file=None, timeout=30, execute=True, format="json")
        config = cli.Config({
            "PROXMOX_API_URL": "https://pve.example/api2/json",
            "PROXMOX_API_TOKEN_ID": "agent@pve!test",
            "PROXMOX_API_TOKEN_SECRET": "secret",
        })
        fake_proc = mock.Mock(returncode=0, stdout="root\n", stderr="")
        with mock.patch("subprocess.run", return_value=fake_proc) as mock_sub:
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = cli.cmd_ssh_exec(args, config, None, cli.Logger(config.log_dir, no_log=True, config=config))
            self.assertEqual(exit_code, cli.EXIT_SUCCESS)
            result = json.loads(stdout.getvalue())
            self.assertTrue(result["ok"])
            self.assertEqual(result["data"]["stdout"], "root\n")
            mock_sub.assert_called_once()


    def test_vm_exec_dry_run(self):
        args = argparse.Namespace(node="pve", vmid=101, command="uptime", timeout=30, execute=False, format="json")
        config = cli.Config({
            "PROXMOX_API_URL": "https://pve.example/api2/json",
            "PROXMOX_API_TOKEN_ID": "agent@pve!test",
            "PROXMOX_API_TOKEN_SECRET": "secret",
        })
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = cli.cmd_vm_exec(args, config, None, cli.Logger(config.log_dir, no_log=True, config=config))
        self.assertEqual(exit_code, cli.EXIT_SUCCESS)
        result = json.loads(stdout.getvalue())
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["data"]["target"]["node"], "pve")
        self.assertEqual(result["data"]["target"]["vmid"], "101")
        self.assertEqual(result["data"]["body"]["command"], "uptime")

    def test_vm_exec_live_success(self):
        args = argparse.Namespace(node="pve", vmid=101, command="uptime", timeout=30, execute=True, format="json")
        config = cli.Config({
            "PROXMOX_API_URL": "https://pve.example/api2/json",
            "PROXMOX_API_TOKEN_ID": "agent@pve!test",
            "PROXMOX_API_TOKEN_SECRET": "secret",
        })
        mock_client = mock.MagicMock()
        mock_client.post.return_value = {
            "ok": True,
            "status_code": 200,
            "duration_ms": 15,
            "data": {"pid": 9876},
            "error": None,
        }
        mock_client.get.return_value = {
            "ok": True,
            "status_code": 200,
            "duration_ms": 10,
            "data": {"exited": 1, "exitcode": 0, "out-data": "up 10 days\n", "err-data": ""},
            "error": None,
        }
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = cli.cmd_vm_exec(args, config, mock_client, cli.Logger(config.log_dir, no_log=True, config=config))
        self.assertEqual(exit_code, cli.EXIT_SUCCESS)
        result = json.loads(stdout.getvalue())
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["stdout"], "up 10 days\n")
        self.assertEqual(result["data"]["exit_code"], 0)
        self.assertEqual(result["data"]["pid"], 9876)

    def test_vm_exec_agent_not_configured_in_proxmox(self):
        args = argparse.Namespace(node="pve", vmid=101, command="uptime", timeout=30, execute=True, format="json")
        config = cli.Config({
            "PROXMOX_API_URL": "https://pve.example/api2/json",
            "PROXMOX_API_TOKEN_ID": "agent@pve!test",
            "PROXMOX_API_TOKEN_SECRET": "secret",
        })
        mock_client = mock.MagicMock()
        mock_client.post.return_value = {
            "ok": False,
            "status_code": 500,
            "duration_ms": 15,
            "data": None,
            "error": "QEMU guest agent is not configured",
        }
        # Diagnosing status and config
        mock_client.get.side_effect = [
            {"ok": True, "data": {"status": "running"}},
            {"ok": True, "data": {"cores": 2, "memory": 2048}},  # agent option missing
        ]
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            exit_code = cli.cmd_vm_exec(args, config, mock_client, cli.Logger(config.log_dir, no_log=True, config=config))
        self.assertEqual(exit_code, cli.EXIT_API)
        result = json.loads(stderr.getvalue())
        self.assertFalse(result["ok"])
        diag = result["agent_diagnosis"]
        self.assertEqual(diag["agent_status"], "not_configured_in_proxmox")
        self.assertIn("agent: 1", diag["hint"])
        self.assertIn("agent", diag["remedy_command"])

    def test_vm_exec_agent_not_running_in_guest(self):
        args = argparse.Namespace(node="pve", vmid=101, command="uptime", timeout=30, execute=True, format="json")
        config = cli.Config({
            "PROXMOX_API_URL": "https://pve.example/api2/json",
            "PROXMOX_API_TOKEN_ID": "agent@pve!test",
            "PROXMOX_API_TOKEN_SECRET": "secret",
        })
        mock_client = mock.MagicMock()
        mock_client.post.return_value = {
            "ok": False,
            "status_code": 500,
            "duration_ms": 15,
            "data": None,
            "error": "QEMU guest-agent is not running",
        }
        # Diagnosing status and config
        mock_client.get.side_effect = [
            {"ok": True, "data": {"status": "running"}},
            {"ok": True, "data": {"cores": 2, "memory": 2048, "agent": "1"}},  # agent option enabled
        ]
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            exit_code = cli.cmd_vm_exec(args, config, mock_client, cli.Logger(config.log_dir, no_log=True, config=config))
        self.assertEqual(exit_code, cli.EXIT_API)
        result = json.loads(stderr.getvalue())
        self.assertFalse(result["ok"])
        diag = result["agent_diagnosis"]
        self.assertEqual(diag["agent_status"], "not_running_in_guest")
        self.assertIn("qemu-guest-agent", diag["hint"])

    def test_vm_exec_vm_stopped(self):
        args = argparse.Namespace(node="pve", vmid=101, command="uptime", timeout=30, execute=True, format="json")
        config = cli.Config({
            "PROXMOX_API_URL": "https://pve.example/api2/json",
            "PROXMOX_API_TOKEN_ID": "agent@pve!test",
            "PROXMOX_API_TOKEN_SECRET": "secret",
        })
        mock_client = mock.MagicMock()
        mock_client.post.return_value = {
            "ok": False,
            "status_code": 500,
            "duration_ms": 15,
            "data": None,
            "error": "VM 101 is not running",
        }
        mock_client.get.side_effect = [
            {"ok": True, "data": {"status": "stopped"}},
        ]
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            exit_code = cli.cmd_vm_exec(args, config, mock_client, cli.Logger(config.log_dir, no_log=True, config=config))
        self.assertEqual(exit_code, cli.EXIT_API)
        result = json.loads(stderr.getvalue())
        self.assertFalse(result["ok"])
        diag = result["agent_diagnosis"]
        self.assertEqual(diag["agent_status"], "vm_stopped")
        self.assertIn("vm start", diag["hint"])


class ConsoleOutputParsingTests(unittest.TestCase):
    """The console transport must fail closed, never report a false exit 0."""

    BEG = "__PROXBOT_BEG_1__"
    END = "__PROXBOT_END_1__"

    def _parse(self, text, authenticated=False):
        return cli._parse_console_output(text, self.BEG, self.END, "170", "pveheavy", 30, authenticated)

    def test_complete_output_reports_exit_code(self):
        res = self._parse(f"{self.BEG}\nhello\n{self.END}:0\r\n")
        self.assertTrue(res["ok"])
        self.assertEqual(res["exit_code"], 0)
        self.assertEqual(res["stdout"], "hello")
        self.assertNotIn("status", res)

    def test_nonzero_exit_is_reported(self):
        res = self._parse(f"{self.BEG}\nboom\n{self.END}:127\r\n")
        self.assertFalse(res["ok"])
        self.assertEqual(res["exit_code"], 127)

    def test_missing_start_marker_is_output_lost(self):
        res = self._parse("just some scrollback\r\n")
        self.assertFalse(res["ok"])
        self.assertEqual(res["status"], "output_lost")
        self.assertIsNone(res["exit_code"])
        self.assertIn("start marker", res["error"])

    def test_missing_end_marker_is_output_lost_not_success(self):
        # Regression: this used to fall through to return None and be reported
        # as a transport failure, and an unterminated marker used to imply 0.
        res = self._parse(f"{self.BEG}\npartial output that scrolled\r\n")
        self.assertFalse(res["ok"])
        self.assertEqual(res["status"], "output_lost")
        self.assertIsNone(res["exit_code"])
        self.assertIn("end marker", res["error"])
        self.assertIn("partial output", res["observed"])

    def test_truncated_exit_status_is_unknown_not_zero(self):
        # Regression: exit_code defaulted to 0 when ':$?' was not captured.
        res = self._parse(f"{self.BEG}\noutput\r\n{self.END}")
        self.assertFalse(res["ok"])
        self.assertEqual(res["status"], "output_lost")
        self.assertIsNone(res["exit_code"])
        self.assertIn("exit status", res["error"])

    def test_exit_status_looking_like_other_digits_is_not_misread(self):
        # A prompt or scrollback containing "foo:12" after the end marker must
        # not be mistaken for the command's exit status.
        res = self._parse(f"{self.BEG}\nout\r\n{self.END} some prompt:12")
        self.assertEqual(res["status"], "output_lost")
        self.assertIsNone(res["exit_code"])

    def test_output_lost_is_distinct_from_transport_failure(self):
        args = argparse.Namespace(node="pve", vmid=100, command="uptime", user="root", password=None,
                                  no_auto_inject_key=False, key_file=None, console=True, timeout=30, execute=True, format="json")
        config = cli.Config({
            "PROXMOX_API_URL": "https://pve.example/api2/json",
            "PROXMOX_API_TOKEN_ID": "agent@pve!test",
            "PROXMOX_API_TOKEN_SECRET": "secret",
        })
        stderr = io.StringIO()
        lost = {"ok": False, "status": "output_lost", "vmid": "100", "node": "pve",
                "error": "Container 100: the command was sent to the console but its result could not be verified",
                "exit_code": None, "method": "termproxy"}
        with unittest.mock.patch.dict(cli.cmd_lxc_exec.__globals__, {"lxc_exec": _fake_lxc_exec(lost)}):
            with contextlib.redirect_stderr(stderr):
                exit_code = cli.cmd_lxc_exec(args, config, None, cli.Logger(config.log_dir, no_log=True, config=config))
        self.assertEqual(exit_code, cli.EXIT_INTERNAL)
        result = json.loads(stderr.getvalue())
        self.assertEqual(result["status"], "output_lost")
        self.assertIsNone(result["data"]["exit_code"])


class PctExecTests(unittest.TestCase):
    """The pct exec transport: harness-assembled argv, no general node shell."""

    def _config(self, **extra):
        raw = {
            "PROXMOX_API_URL": "https://pve.example/api2/json",
            "PROXMOX_API_TOKEN_ID": "agent@pve!test",
            "PROXMOX_API_TOKEN_SECRET": "secret",
        }
        raw.update(extra)
        return cli.Config(raw)

    def test_assemble_quotes_vmid_and_command(self):
        self.assertEqual(
            cli.assemble_pct_exec("170", "id"),
            "pct exec 170 -- /bin/sh -lc id",
        )

    def test_assemble_neutralises_shell_metacharacters_in_command(self):
        remote = cli.assemble_pct_exec("170", 'id; touch /tmp/pwned')
        # The whole command must be a single quoted argument, so the node shell
        # cannot split it into a second command.
        self.assertEqual(remote, "pct exec 170 -- /bin/sh -lc 'id; touch /tmp/pwned'")
        self.assertEqual(remote.count("'"), 2)

    def test_refuses_non_numeric_vmid_before_any_ssh(self):
        def _explode(*_a, **_k):
            raise AssertionError("must not SSH with an unvalidated vmid")

        args = argparse.Namespace(node="pve", vmid="170; rm -rf /", command="id", timeout=30,
                                  execute=True, format="json")
        with unittest.mock.patch("proxbot.cli.commands.exec._run_ssh_command", _explode):
            exit_code = cli.cmd_pct_exec(args, self._config(), None, cli.Logger(None, no_log=True))
        self.assertEqual(exit_code, cli.EXIT_VALIDATION)

    def test_refuses_when_node_ssh_unconfigured(self):
        def _explode(*_a, **_k):
            raise AssertionError("must not SSH when node access is unconfigured")

        args = argparse.Namespace(node="pve", vmid=170, command="id", timeout=30,
                                  execute=True, format="json")
        stderr = io.StringIO()
        with unittest.mock.patch("proxbot.cli.commands.exec._run_ssh_command", _explode):
            with contextlib.redirect_stderr(stderr):
                exit_code = cli.cmd_pct_exec(args, self._config(), None, cli.Logger(None, no_log=True))
        self.assertEqual(exit_code, cli.EXIT_VALIDATION)
        result = json.loads(stderr.getvalue())
        self.assertEqual(result["status"], "node_ssh_unconfigured")
        self.assertIn("PROXMOX_NODE_SSH_USER", result["error"])

    def test_runs_assembled_command_and_returns_real_exit_code(self):
        seen = {}

        def _fake_ssh(**kwargs):
            seen.update(kwargs)
            return (7, "partial output\n", "command not found\n")

        class Client:
            def get(self, endpoint):
                if endpoint == "/nodes":
                    return {"ok": True, "data": [{"node": "pve"}]}
                if endpoint == "/nodes/pve/network":
                    return {"ok": True, "data": [
                        {"iface": "vmbr0", "type": "bridge", "cidr": "10.0.0.1/24"},
                        {"iface": "eno1", "type": "eth", "cidr": None},
                    ]}
                return {"ok": True, "data": {}}

        args = argparse.Namespace(node="pve", vmid=170, command="nonexistent-binary", timeout=30,
                                  execute=True, format="json")
        config = self._config(PROXMOX_NODE_SSH_USER="root",
                              PROXMOX_NODE_SSH_KEY="/keys/node")
        stdout, stderr = io.StringIO(), io.StringIO()
        with unittest.mock.patch("proxbot.cli.commands.exec._run_ssh_command", _fake_ssh):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                exit_code = cli.cmd_pct_exec(args, config, Client(), cli.Logger(None, no_log=True))

        # Prefers the bridge address, assembles the remote command itself, and
        # does not turn a failing command into a success.
        self.assertEqual(seen["host"], "10.0.0.1")
        self.assertEqual(seen["user"], "root")
        self.assertEqual(seen["command"], "pct exec 170 -- /bin/sh -lc nonexistent-binary")
        self.assertEqual(exit_code, cli.EXIT_INTERNAL)
        result = json.loads(stdout.getvalue() or stderr.getvalue())
        self.assertFalse(result["ok"])
        self.assertEqual(result["data"]["exit_code"], 7)
        self.assertEqual(result["data"]["transport"], "pct_exec")

    def test_dry_run_by_default_shows_the_remote_command(self):
        args = argparse.Namespace(node="pve", vmid=170, command="id", timeout=30,
                                  execute=False, format="json")
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = cli.cmd_pct_exec(args, self._config(), None, cli.Logger(None, no_log=True))
        self.assertEqual(exit_code, cli.EXIT_SUCCESS)
        result = json.loads(stdout.getvalue())
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["data"]["target"]["remote"], "pct exec 170 -- /bin/sh -lc id")

    # --- password authentication ----------------------------------------
    def test_password_only_counts_as_configured(self):
        config = self._config(PROXMOX_NODE_SSH_USER="root", PROXMOX_NODE_SSH_PASSWORD="s3cret")
        self.assertTrue(config.node_ssh_configured)
        self.assertEqual(config.node_ssh_auth_method, "password")

    def test_key_wins_when_both_are_set(self):
        config = self._config(PROXMOX_NODE_SSH_USER="root", PROXMOX_NODE_SSH_KEY="/k",
                              PROXMOX_NODE_SSH_PASSWORD="s3cret")
        self.assertEqual(config.node_ssh_auth_method, "key")

    def test_user_alone_is_not_configured(self):
        config = self._config(PROXMOX_NODE_SSH_USER="root")
        self.assertFalse(config.node_ssh_configured)
        self.assertIn("PROXMOX_NODE_SSH_KEY or PROXMOX_NODE_SSH_PASSWORD", config.node_ssh_missing())

    def test_passes_configured_password_to_ssh(self):
        seen = {}

        def _fake(**kwargs):
            seen.update(kwargs)
            return (0, "root@node\n", "")

        class Client:
            def get(self, endpoint):
                if endpoint == "/nodes":
                    return {"ok": True, "data": [{"node": "pve"}]}
                if endpoint.endswith("/network"):
                    return {"ok": True, "data": [{"iface": "vmbr0", "type": "bridge", "cidr": "10.0.0.1/24"}]}
                return {"ok": True, "data": {}}

        args = argparse.Namespace(node="pve", vmid=170, command="id", node_password=None,
                                  timeout=30, execute=True, format="json")
        config = self._config(PROXMOX_NODE_SSH_USER="root", PROXMOX_NODE_SSH_PASSWORD="s3cret")
        with unittest.mock.patch("proxbot.cli.commands.exec._run_ssh_command", _fake):
            with contextlib.redirect_stdout(io.StringIO()):
                cli.cmd_pct_exec(args, config, Client(), cli.Logger(None, no_log=True))
        self.assertEqual(seen["password"], "s3cret")
        self.assertIsNone(seen["key_file"])

    def test_explicit_node_password_overrides_config(self):
        seen = {}

        def _fake(**kwargs):
            seen.update(kwargs)
            return (0, "ok\n", "")

        class Client:
            def get(self, endpoint):
                if endpoint == "/nodes":
                    return {"ok": True, "data": [{"node": "pve"}]}
                if endpoint.endswith("/network"):
                    return {"ok": True, "data": [{"iface": "vmbr0", "type": "bridge", "cidr": "10.0.0.1/24"}]}
                return {"ok": True, "data": {}}

        args = argparse.Namespace(node="pve", vmid=170, command="id", node_password="from-flag",
                                  timeout=30, execute=True, format="json")
        config = self._config(PROXMOX_NODE_SSH_USER="root", PROXMOX_NODE_SSH_PASSWORD="from-env")
        with unittest.mock.patch("proxbot.cli.commands.exec._run_ssh_command", _fake):
            with contextlib.redirect_stdout(io.StringIO()):
                cli.cmd_pct_exec(args, config, Client(), cli.Logger(None, no_log=True))
        self.assertEqual(seen["password"], "from-flag")

    def test_probe_reports_password_auth_without_leaking_it(self):
        def _fake(**kwargs):
            return (0, "proxmox-ve 9.1.5", "")

        class Client:
            def get(self, endpoint):
                if endpoint == "/nodes":
                    return {"ok": True, "data": [{"node": "pve"}]}
                if endpoint.endswith("/network"):
                    return {"ok": True, "data": [{"iface": "vmbr0", "type": "bridge", "cidr": "10.0.0.1/24"}]}
                return {"ok": True, "data": {}}

        config = self._config(PROXMOX_NODE_SSH_USER="root", PROXMOX_NODE_SSH_PASSWORD="s3cret")
        with unittest.mock.patch("proxbot.cli.commands.exec._run_ssh_command", _fake):
            report = cli.probe_node_ssh("pve", config, Client())
        self.assertTrue(report["usable"])
        self.assertEqual(report["auth_method"], "password")
        # The probe is surfaced to the operator via doctor, so it must not
        # contain the secret in any form.
        self.assertNotIn("s3cret", json.dumps(report))

    def test_node_password_is_redacted_in_argv(self):
        from proxbot.common.redact import _redact_argv
        redacted = _redact_argv(
            ["lxc", "pct-exec", "--node", "pve", "--node-password", "s3cret", "--execute"],
            self._config(),
        )
        self.assertNotIn("s3cret", redacted)
        self.assertIn("***REDACTED***", redacted)

    def test_node_password_is_redacted_in_the_command_string(self):
        # Regression: the argv form and the joined-string form were separate
        # patterns, so --node-password was redacted in one and leaked in the
        # other. Both must now use the same list.
        from proxbot.common.redact import _redact_command_str, redact_command_secrets
        for secret_flag in ("--node-password", "--password", "--token-secret",
                            "--api-token-secret", "--secret", "--private-key"):
            for sep in (" ", "="):
                raw = f"lxc pct-exec --vmid 170 {secret_flag}{sep}s3cret --command id"
                self.assertNotIn("s3cret", _redact_command_str(raw), f"{secret_flag}{sep} leaked via _redact_command_str")
                self.assertNotIn("s3cret", redact_command_secrets(raw), f"{secret_flag}{sep} leaked via redact_command_secrets")

    def test_argv_and_string_redaction_cover_the_same_flags(self):
        from proxbot.common.redact import SENSITIVE_ARGV_FLAGS, _redact_argv, redact_command_secrets
        for flag in SENSITIVE_ARGV_FLAGS:
            if flag == "--command":
                continue  # covered separately; it is a value carrier
            redacted = redact_command_secrets(f"cmd {flag} s3cret")
            self.assertNotIn("s3cret", redacted, f"{flag} leaks in the string form")
            argv_redacted = _redact_argv(["cmd", flag, "s3cret"])
            self.assertNotIn("s3cret", argv_redacted, f"{flag} leaks in the argv form")

    def test_probe_reports_unconfigured_without_ssh(self):
        def _explode(*_a, **_k):
            raise AssertionError("probe must not SSH when unconfigured")

        report = {}
        with unittest.mock.patch("proxbot.cli.commands.exec._run_ssh_command", _explode):
            report = cli.probe_node_ssh("pve", self._config(), None)
        self.assertFalse(report["usable"])
        self.assertFalse(report["configured"])
        self.assertIn("PROXMOX_NODE_SSH_KEY or PROXMOX_NODE_SSH_PASSWORD", report["missing"])

    def test_doctor_reports_transport_availability(self):
        args = argparse.Namespace(live=False, node_access=False, node=None, format="json")
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            cli.cmd_doctor(args, self._config(), None, cli.Logger(None, no_log=True))
        data = json.loads(stdout.getvalue())["data"]
        self.assertIn("pct_exec", data["exec_transports"])
        self.assertIn("opt-in", data["exec_transports"]["console"])
        self.assertFalse(data["node_ssh"]["configured"])


class NodeTargetGuardTests(unittest.TestCase):
    """`ssh exec` must not double as a host shell that bypasses pct exec."""

    def _config(self, **extra):
        raw = {
            "PROXMOX_API_URL": "https://pve.example/api2/json",
            "PROXMOX_API_TOKEN_ID": "agent@pve!test",
            "PROXMOX_API_TOKEN_SECRET": "secret",
        }
        raw.update(extra)
        return cli.Config(raw)

    class Client:
        def get(self, endpoint):
            if endpoint == "/nodes":
                return {"ok": True, "data": [{"node": "pveheavy"}, {"node": "pvestore"}]}
            if endpoint.endswith("/network"):
                return {"ok": True, "data": [
                    {"iface": "vmbr0", "type": "bridge", "cidr": "10.0.0.1/24"},
                ]}
            return {"ok": True, "data": {}}

    def _refused(self, host, config=None, client=None):
        def _explode(*_a, **_k):
            raise AssertionError("must not SSH to a Proxmox node")

        args = argparse.Namespace(host=host, node=None, vmid=None, command="id", user="root",
                                  password=None, port=22, key_file=None, timeout=10,
                                  no_auto_inject_key=True, execute=True, format="json")
        out = io.StringIO()
        with unittest.mock.patch("proxbot.cli.commands.exec._run_ssh_command", _explode):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                code = cli.cmd_ssh_exec(args, config or self._config(), client or self.Client(),
                                        cli.Logger(None, no_log=True))
        return code, out.getvalue()

    def test_refuses_node_name(self):
        code, out = self._refused("pveheavy")
        self.assertEqual(code, cli.EXIT_SAFETY)
        self.assertEqual(json.loads(out)["status"], "node_target_refused")

    def test_refuses_node_name_case_insensitively(self):
        code, _ = self._refused("PVEHEAVY")
        self.assertEqual(code, cli.EXIT_SAFETY)

    def test_refuses_node_address(self):
        code, _ = self._refused("10.0.0.1")
        self.assertEqual(code, cli.EXIT_SAFETY)

    def test_refuses_node_address_with_domain_suffix(self):
        # Guards against the FQDN bypass: 10.0.0.1.lan must not slip through.
        code, _ = self._refused("10.0.0.1.lan")
        self.assertEqual(code, cli.EXIT_SAFETY)

    def test_refuses_node_name_with_domain_suffix(self):
        code, _ = self._refused("pveheavy.example.net")
        self.assertEqual(code, cli.EXIT_SAFETY)

    def test_allows_explicitly_listed_target(self):
        config = self._config(PROXMOX_NODE_SSH_TARGETS="pveheavy,10.0.0.1")
        def _fake(**_kwargs):
            return (0, "ok\n", "")
        args = argparse.Namespace(host="pveheavy", node=None, vmid=None, command="id", user="root",
                                  password=None, port=22, key_file=None, timeout=10,
                                  no_auto_inject_key=True, execute=True, format="json")
        out = io.StringIO()
        with unittest.mock.patch("proxbot.cli.commands.exec._run_ssh_command", _fake):
            with contextlib.redirect_stdout(out):
                code = cli.cmd_ssh_exec(args, config, self.Client(), cli.Logger(None, no_log=True))
        self.assertEqual(code, cli.EXIT_SUCCESS)

    def test_does_not_block_a_guest(self):
        def _fake(**_kwargs):
            return (0, "guest ok\n", "")
        args = argparse.Namespace(host="10.0.0.99", node=None, vmid=None, command="id", user="root",
                                  password=None, port=22, key_file=None, timeout=10,
                                  no_auto_inject_key=True, execute=True, format="json")
        out = io.StringIO()
        with unittest.mock.patch("proxbot.cli.commands.exec._run_ssh_command", _fake):
            with contextlib.redirect_stdout(out):
                code = cli.cmd_ssh_exec(args, self._config(), self.Client(), cli.Logger(None, no_log=True))
        self.assertEqual(code, cli.EXIT_SUCCESS)
        self.assertIn("guest ok", out.getvalue())


class AsTemplateTests(unittest.TestCase):
    """`lxc create --as-template` builds a keyed base image for clones to inherit."""

    def _config(self):
        return cli.Config({
            "PROXMOX_API_URL": "https://pve.example/api2/json",
            "PROXMOX_API_TOKEN_ID": "agent@pve!test",
            "PROXMOX_API_TOKEN_SECRET": "secret",
        })

    def _args(self, **extra):
        base = dict(node="pve", vmid=90001, hostname="base", storage="local",
                    ostemplate="local:vztmpl/debian-13-standard_13.6-1_amd64.tar.zst",
                    ssh_public_keys="ssh-ed25519 AAAA test@host", ssh_keygen=False,
                    unprivileged=True, features=None, password=None, cores=None, memory=None,
                    swap=None, net0=None, rootfs=None, nameserver=None, onboot=None,
                    data=None, execute=True, wait=True, wait_timeout=60, as_template=True, format="json")
        base.update(extra)
        return argparse.Namespace(**base)

    def test_dry_run_shows_the_followup_template_call(self):
        args = self._args(execute=False)
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = cli.cmd_lxc_create(args, self._config(), None, cli.Logger(None, no_log=True))
        self.assertEqual(exit_code, cli.EXIT_SUCCESS)
        result = json.loads(stdout.getvalue())
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["data"]["target"]["then"], "POST /nodes/pve/lxc/90001/template")

    def test_create_then_converts_to_template(self):
        calls = []

        class Client:
            def post(self, endpoint, body):
                calls.append((endpoint, body))
                return {"ok": True, "status_code": 200, "duration_ms": 1,
                        "data": "UPID:pve:00001234", "error": None}

            def get(self, endpoint):
                if "/tasks/" in endpoint:
                    return {"ok": True, "status_code": 200, "duration_ms": 1,
                            "data": {"status": "stopped", "exitstatus": "OK"}, "error": None}
                return {"ok": True, "status_code": 200, "duration_ms": 1, "data": {}, "error": None}

        args = self._args()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            exit_code = cli.cmd_lxc_create(args, self._config(), Client(), cli.Logger(None, no_log=True))
        self.assertEqual(exit_code, cli.EXIT_SUCCESS)
        self.assertEqual(calls[0][0], "/nodes/pve/lxc")
        self.assertEqual(calls[0][1]["ssh-public-keys"], "ssh-ed25519 AAAA test@host")
        self.assertEqual(calls[1][0], "/nodes/pve/lxc/90001/template")

    def test_template_conversion_failure_is_reported(self):
        class Client:
            def post(self, endpoint, body):
                if endpoint.endswith("/template"):
                    return {"ok": False, "status_code": 500, "duration_ms": 1,
                            "data": None, "error": "unable to open template"}
                return {"ok": True, "status_code": 200, "duration_ms": 1,
                        "data": "UPID:pve:00001234", "error": None}

            def get(self, endpoint):
                if "/tasks/" in endpoint:
                    return {"ok": True, "status_code": 200, "duration_ms": 1,
                            "data": {"status": "stopped", "exitstatus": "OK"}, "error": None}
                return {"ok": True, "status_code": 200, "duration_ms": 1, "data": {}, "error": None}

        args = self._args()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            exit_code = cli.cmd_lxc_create(args, self._config(), Client(), cli.Logger(None, no_log=True))
        self.assertNotEqual(exit_code, cli.EXIT_SUCCESS)

    def test_without_flag_no_template_call(self):
        calls = []

        class Client:
            def post(self, endpoint, body):
                calls.append(endpoint)
                return {"ok": True, "status_code": 200, "duration_ms": 1,
                        "data": "UPID:pve:00001234", "error": None}

            def get(self, endpoint):
                if "/tasks/" in endpoint:
                    return {"ok": True, "status_code": 200, "duration_ms": 1,
                            "data": {"status": "stopped", "exitstatus": "OK"}, "error": None}
                return {"ok": True, "status_code": 200, "duration_ms": 1, "data": {}, "error": None}

        args = self._args(as_template=False)
        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_lxc_create(args, self._config(), Client(), cli.Logger(None, no_log=True))
        self.assertEqual(calls, ["/nodes/pve/lxc"])


class TemplateRegistryTests(unittest.TestCase):
    def test_registry_parser_args(self):
        args = cli.build_parser().parse_args([
            "templates", "registry", "--node", "pveheavy",
            "--section", "system", "--search", "debian", "--limit", "5",
        ])
        self.assertEqual(args.resource, "templates")
        self.assertEqual(args.action, "registry")
        self.assertEqual(args.node, "pveheavy")
        self.assertEqual(args.section, "system")
        self.assertEqual(args.search, "debian")
        self.assertEqual(args.limit, 5)

    def test_registry_download_parser_args(self):
        args = cli.build_parser().parse_args([
            "templates", "download", "--node", "pveheavy",
            "--storage", "local",
            "--template", "debian-13-standard_13.6-1_amd64.tar.zst",
        ])
        self.assertEqual(args.resource, "templates")
        self.assertEqual(args.action, "download")
        self.assertEqual(args.storage, "local")
        self.assertEqual(args.template, "debian-13-standard_13.6-1_amd64.tar.zst")
        self.assertFalse(args.execute)

    def test_registry_download_requires_template(self):
        with self.assertRaises(SystemExit):
            cli.build_parser().parse_args(["templates", "download", "--node", "pveheavy", "--storage", "local"])

    def test_registry_download_requires_node_at_runtime(self):
        args = argparse.Namespace(node=None, storage="local", template="debian-13-standard_13.6-1_amd64.tar.zst",
                                 execute=True, wait=False, format="json")
        config = cli.Config({
            "PROXMOX_API_URL": "https://pve.example/api2/json",
            "PROXMOX_API_TOKEN_ID": "agent@pve!test",
            "PROXMOX_API_TOKEN_SECRET": "secret",
        })
        mock_client = mock.MagicMock()
        with contextlib.redirect_stderr(io.StringIO()):
            exit_code = cli.cmd_templates_download(args, config, mock_client, cli.Logger(config.log_dir, no_log=True, config=config))
        self.assertEqual(exit_code, cli.EXIT_VALIDATION)
        mock_client.post.assert_not_called()

    def test_filter_registry_rows(self):
        rows = [
            {"template": "debian-13-standard_13.6-1_amd64.tar.zst", "section": "system", "os": "debian-13", "package": "debian-13-standard", "headline": "Debian 13", "description": "standard image"},
            {"template": "debian-12-turnkey-wordpress_18.0-1_amd64.tar.gz", "section": "turnkeylinux", "os": "debian-12", "package": "turnkey-wordpress", "headline": "WordPress", "description": "blog"},
            {"template": "alpine-3.19-default_20240101_amd64.tar.xz", "section": "system", "os": "alpine", "package": "alpine-3.19-default", "headline": "Alpine", "description": "minimal"},
        ]
        self.assertEqual(len(cli.filter_registry_rows(rows, None, None, None)), 3)
        self.assertEqual(len(cli.filter_registry_rows(rows, "system", None, None)), 2)
        self.assertEqual(len(cli.filter_registry_rows(rows, "SYSTEM", None, None)), 2)
        searched = cli.filter_registry_rows(rows, None, "wordpress", None)
        self.assertEqual(len(searched), 1)
        self.assertEqual(searched[0]["package"], "turnkey-wordpress")
        self.assertEqual(len(cli.filter_registry_rows(rows, "system", "debian", None)), 1)
        self.assertEqual(len(cli.filter_registry_rows(rows, None, None, 2)), 2)
        self.assertEqual(cli.filter_registry_rows(None, "system", None, None), None)

    def test_registry_lists_aplinfo_endpoint(self):
        args = argparse.Namespace(node="pveheavy", section=None, search=None, limit=None, format="json")
        config = cli.Config({
            "PROXMOX_API_URL": "https://pve.example/api2/json",
            "PROXMOX_API_TOKEN_ID": "agent@pve!test",
            "PROXMOX_API_TOKEN_SECRET": "secret",
        })
        mock_client = mock.MagicMock()
        mock_client.get.return_value = {"ok": True, "status_code": 200, "duration_ms": 10, "data": [{"template": "a", "section": "system"}]}
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = cli.cmd_templates_registry(args, config, mock_client, cli.Logger(config.log_dir, no_log=True, config=config))
        self.assertEqual(exit_code, cli.EXIT_SUCCESS)
        mock_client.get.assert_called_once_with("/nodes/pveheavy/aplinfo")
        self.assertTrue(json.loads(stdout.getvalue())["ok"])

    def test_registry_download_dry_run_by_default(self):
        args = cli.build_parser().parse_args([
            "templates", "download", "--node", "pveheavy",
            "--storage", "local", "--template", "debian-13-standard_13.6-1_amd64.tar.zst",
            "--format", "json", "--no-log",
        ])
        config = cli.Config({
            "PROXMOX_API_URL": "https://pve.example/api2/json",
            "PROXMOX_API_TOKEN_ID": "agent@pve!test",
            "PROXMOX_API_TOKEN_SECRET": "secret",
        })
        mock_client = mock.MagicMock()
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = cli.cmd_templates_download(args, config, mock_client, cli.Logger(config.log_dir, no_log=True, config=config))
        self.assertEqual(exit_code, cli.EXIT_SUCCESS)
        mock_client.post.assert_not_called()
        result = json.loads(stdout.getvalue())
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["data"]["endpoint"], "/nodes/pveheavy/aplinfo")
        self.assertEqual(result["data"]["body"]["template"], "debian-13-standard_13.6-1_amd64.tar.zst")


if __name__ == "__main__":
    unittest.main()
