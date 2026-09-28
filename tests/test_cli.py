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
        self.assertEqual(result["data"]["body"], {"command": "uptime"})

    def test_lxc_exec_live(self):
        class Client:
            def post(self, endpoint, body):
                return {"ok": True, "status_code": 200, "duration_ms": 1, "data": {"port": 5900, "ticket": "tick", "user": "root@pam"}, "error": None}

            def get(self, endpoint):
                return {"ok": True, "status_code": 200, "duration_ms": 1, "data": {}, "error": None}

        args = argparse.Namespace(node="pve", vmid=100, command="uname -r", timeout=30, execute=True, format="json")
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
        args = argparse.Namespace(node="pve", vmid=100, command="uname -r", user="root", password=None, no_auto_inject_key=False, key_file=None, timeout=30, execute=True, format="json")
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
        args = argparse.Namespace(node="pve", vmid=100, command="uname -r", user="root", password="secretpassword", no_auto_inject_key=False, key_file=None, timeout=30, execute=True, format="json")
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
        args = argparse.Namespace(node="pve", vmid=100, command="uptime", user="root", password=None, no_auto_inject_key=False, key_file=None, timeout=30, execute=True, format="json")
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
        args = argparse.Namespace(node="pve", vmid=100, command="uptime", user="root", password=None, no_auto_inject_key=False, key_file=None, timeout=30, execute=True, format="json")
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

    def test_lxc_exec_with_password(self):
        args = argparse.Namespace(node="pve", vmid=100, command="uname -r", user="root", password="secretpassword", no_auto_inject_key=False, key_file=None, timeout=30, execute=True, format="json")
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
