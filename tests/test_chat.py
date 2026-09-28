import io
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

# Add repo root to path
REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import chat


class MarkdownRendererTests(unittest.TestCase):
    def test_render_table(self):
        md = "| Node | Status |\n|---|---|\n| pve1 | online |\n| pve2 | offline |"
        rendered = chat.render_markdown(md)
        self.assertIn("┌", rendered)
        self.assertIn("pve1", rendered)
        self.assertIn("online", rendered)
        self.assertIn("pve2", rendered)
        self.assertIn("offline", rendered)
        self.assertIn("└", rendered)

    def test_render_code_block(self):
        md = "```python\nprint('hello')\n```"
        rendered = chat.render_markdown(md)
        clean = chat.strip_ansi(rendered)
        self.assertIn("┌── python", clean)
        self.assertIn("print('hello')", clean)
        self.assertIn("└──", clean)

    def test_render_headers_and_styles(self):
        md = "# Heading 1\n## Heading 2\n### Heading 3\n**bold** `code` *italic*"
        rendered = chat.render_markdown(md)
        self.assertIn("Heading 1", rendered)
        self.assertIn("Heading 2", rendered)
        self.assertIn("Heading 3", rendered)
        self.assertIn("bold", rendered)
        self.assertIn("code", rendered)

    def test_readline_prompt_wrapping(self):
        raw = f"{chat.BOLD}{chat.GREEN}proxbot{chat.RESET} > "
        wrapped = chat.readline_prompt(raw)
        self.assertEqual(wrapped, "proxbot > ")
        # strip_ansi should cleanly strip all ANSI escape sequences
        self.assertEqual(chat.strip_ansi(wrapped), "proxbot > ")

    def test_prompt_input_passes_prompt_to_input(self):
        with mock.patch("builtins.input", return_value="test input") as mock_input:
            prompt = f"{chat.BOLD}{chat.GREEN}proxbot{chat.RESET} > "
            result = chat.prompt_input(prompt)
            self.assertEqual(result, "test input")
            mock_input.assert_called_once_with("proxbot > ")


class SessionLoggerTests(unittest.TestCase):
    def test_logging_and_redaction(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = pathlib.Path(tmpdir)
            logger = chat.SessionLogger(log_dir, session_id="test-session")
            logger.log("USER", {"prompt": "List VMs"})
            logger.log("TOOL_CALL", {
                "tool": "vm_create",
                "arguments": {"password": "secret-pass", "token_secret": "xyz123", "node": "pve1"}
            })

            self.assertTrue(logger.log_file.exists())
            lines = logger.log_file.read_text(encoding="utf-8").strip().split("\n")
            self.assertEqual(len(lines), 2)

            entry1 = json.loads(lines[0])
            self.assertEqual(entry1["event"], "USER")
            self.assertEqual(entry1["data"]["prompt"], "List VMs")

            entry2 = json.loads(lines[1])
            self.assertEqual(entry2["event"], "TOOL_CALL")
            self.assertEqual(entry2["data"]["arguments"]["password"], "***REDACTED***")
            self.assertEqual(entry2["data"]["arguments"]["token_secret"], "***REDACTED***")
            self.assertEqual(entry2["data"]["arguments"]["node"], "pve1")

    def test_new_session_rotates_log_and_resets_stats(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = pathlib.Path(tmpdir)
            logger = chat.SessionLogger(log_dir, session_id="sess-one")
            logger.log("USER", {"prompt": "hello"})
            logger.stats["user_prompts"] = 3
            logger.stats["tool_calls"] = 5
            old_file = logger.log_file
            old_id = logger.session_id

            new_old_id = logger.new_session()

            self.assertEqual(new_old_id, old_id)
            self.assertNotEqual(logger.session_id, old_id)
            self.assertNotEqual(logger.log_file, old_file)
            self.assertEqual(logger.stats["user_prompts"], 0)
            self.assertEqual(logger.stats["tool_calls"], 0)
            # Boundary events in both files
            old_entries = [json.loads(l) for l in old_file.read_text(encoding="utf-8").strip().split("\n")]
            self.assertEqual(old_entries[-1]["event"], "SYSTEM")
            self.assertEqual(old_entries[-1]["data"]["action"], "session_end")
            new_entries = [json.loads(l) for l in logger.log_file.read_text(encoding="utf-8").strip().split("\n")]
            self.assertEqual(new_entries[0]["event"], "SYSTEM")
            self.assertEqual(new_entries[0]["data"]["action"], "session_start")
            self.assertEqual(new_entries[0]["data"]["previous"], old_id)


class SlashCommandTests(unittest.TestCase):
    def test_clear_screen_emits_escape_sequence(self):
        with mock.patch("sys.stdout") as mock_stdout:
            chat.clear_screen()
            written = "".join(c.args[0] for c in mock_stdout.write.call_args_list)
            self.assertIn("\033[2J", written)
            self.assertIn("\033[H", written)

    def test_help_lists_new_and_clear(self):
        with mock.patch("builtins.print") as mock_print:
            chat.print_help()
            output = " ".join(str(c.args[0]) for c in mock_print.call_args_list)
            self.assertIn("/new", output)
            self.assertIn("/clear", output)


class EmbeddedToolParserTests(unittest.TestCase):
    def test_xml_embedded_tool_call(self):
        text = 'Here is the command:\n<tool_call>{"name": "nodes_list", "arguments": {}}</tool_call>\nLet me know.'
        cleaned, calls = chat.extract_embedded_tool_calls(text)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["function"]["name"], "nodes_list")
        self.assertEqual(json.loads(calls[0]["function"]["arguments"]), {})
        self.assertNotIn("<tool_call>", cleaned)

    def test_markdown_json_embedded_tool_call(self):
        text = 'Executing:\n```json\n{"tool": "node_status", "parameters": {"node": "pve1"}}\n```'
        cleaned, calls = chat.extract_embedded_tool_calls(text)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["function"]["name"], "node_status")
        self.assertEqual(json.loads(calls[0]["function"]["arguments"]), {"node": "pve1"})


class LoopWatchdogTests(unittest.TestCase):
    def test_duplicate_call_suppression(self):
        dog = chat.LoopWatchdog(max_turn_calls=5, max_duplicate_calls=2)
        # 1st call: OK
        allowed, _ = dog.check_call("nodes_list", {})
        self.assertTrue(allowed)
        # 2nd call: OK
        allowed, _ = dog.check_call("nodes_list", {})
        self.assertTrue(allowed)
        # 3rd call: Suppressed
        allowed, reason = dog.check_call("nodes_list", {})
        self.assertFalse(allowed)
        self.assertIn("Duplicate tool call suppressed", reason)

    def test_max_turn_calls_exceeded(self):
        dog = chat.LoopWatchdog(max_turn_calls=3, max_duplicate_calls=5)
        dog.check_call("tool1", {"i": 1})
        dog.check_call("tool2", {"i": 2})
        dog.check_call("tool3", {"i": 3})
        allowed, reason = dog.check_call("tool4", {"i": 4})
        self.assertFalse(allowed)
        self.assertIn("Maximum tool executions per turn", reason)


class HarnessCommandValidationTests(unittest.TestCase):
    """The model may only ask for the command the harness would assemble itself."""

    def _ok(self, args):
        allowed, reason = chat.validate_harness_command("lxc_pct_exec", args)
        self.assertTrue(allowed, reason)

    def _refused(self, args, needle=None):
        allowed, reason = chat.validate_harness_command("lxc_pct_exec", args)
        self.assertFalse(allowed)
        if needle:
            self.assertIn(needle, reason)

    # --- accepted shapes -------------------------------------------------
    def test_accepts_minimal_call(self):
        self._ok({"vmid": 170, "command": "systemctl status nginx"})

    def test_accepts_node_and_timeout(self):
        self._ok({"vmid": 170, "command": "id", "node": "pveheavy", "timeout": 60})

    def test_accepts_numeric_string_vmid(self):
        self._ok({"vmid": "170", "command": "id"})

    # --- refused: attempts to steer the harness --------------------------
    def test_refuses_injected_host(self):
        self._refused({"vmid": 170, "command": "id", "host": "attacker.example"}, "does not accept host")

    def test_refuses_injected_user(self):
        self._refused({"vmid": 170, "command": "id", "user": "root"}, "does not accept user")

    def test_refuses_arbitrary_shell(self):
        self._refused({"vmid": 170, "command": "id", "shell": "/bin/bash"}, "does not accept shell")

    def test_refuses_arbitrary_remote_argv(self):
        self._refused({"vmid": 170, "command": "id", "remote": "rm -rf /"}, "does not accept remote")

    def test_refuses_non_numeric_vmid(self):
        self._refused({"vmid": "170; rm -rf /", "command": "id"}, "numeric vmid")

    def test_refuses_boolean_vmid(self):
        # bool is an int subclass; True must not become vmid 1.
        self._refused({"vmid": True, "command": "id"}, "integer vmid")

    def test_refuses_empty_command(self):
        self._refused({"vmid": 170, "command": "   "}, "non-empty string command")

    def test_refuses_non_string_command(self):
        self._refused({"vmid": 170, "command": ["rm", "-rf", "/"]}, "non-empty string command")

    def test_refuses_command_that_starts_with_pct(self):
        # Guards against the model treating this as a node shell, which would
        # otherwise surface as a confusing "command not found" in the guest.
        self._refused({"vmid": 170, "command": "pct exec 200 -- id"}, "starts with `pct`")
        self._refused({"vmid": 170, "command": "  /usr/sbin/pct status"}, "starts with `pct`")

    def test_allows_pct_appearing_inside_a_real_command(self):
        # Must not be a naive substring match: these are legitimate guest
        # commands that merely mention pct.
        self._ok({"vmid": 170, "command": "cat /etc/pct.conf 2>/dev/null"})
        self._ok({"vmid": 170, "command": "ls /usr/bin | grep -c pct"})
        self._ok({"vmid": 170, "command": "echo impacted; df -h"})

    def test_ignores_other_tools(self):
        allowed, _ = chat.validate_harness_command("ssh_exec", {"host": "10.0.0.5", "command": "id"})
        self.assertTrue(allowed)

    # --- enforcement actually happens in the watchdog --------------------
    def test_watchdog_blocks_before_the_approval_prompt(self):
        dog = chat.LoopWatchdog()
        allowed, reason = dog.check_call("lxc_pct_exec", {"vmid": 170, "command": "id", "host": "x"})
        self.assertFalse(allowed)
        self.assertIn("does not accept", reason)
        # Blocked calls must not consume the per-turn budget, so a rejected
        # attempt cannot be used to exhaust the turn and mask a later one.
        self.assertEqual(dog.total_calls_in_turn, 0)

    def test_watchdog_allows_a_wellformed_call(self):
        dog = chat.LoopWatchdog()
        allowed, reason = dog.check_call("lxc_pct_exec", {"vmid": 170, "command": "id"})
        self.assertTrue(allowed, reason)
        self.assertEqual(dog.total_calls_in_turn, 1)

    def test_executor_refuses_even_when_called_directly(self):
        # Bypassing agent.py must not bypass validation.
        executor = chat.ProxmoxToolExecutor(pathlib.Path("/nonexistent/cli.py"), pathlib.Path("/nonexistent/.env"))
        result = executor.execute("lxc_pct_exec", {"vmid": 170, "command": "id", "host": "attacker"})
        self.assertFalse(result["ok"])
        self.assertIn("does not accept", result["error"])

    def test_executor_builds_the_expected_argv(self):
        executor = chat.ProxmoxToolExecutor(pathlib.Path("/nonexistent/cli.py"), pathlib.Path("/nonexistent/.env"))
        argv = executor._build_cli_args("lxc_pct_exec", {"vmid": 170, "command": "id", "node": "pveheavy", "timeout": 45})
        self.assertEqual(argv, [
            "lxc", "pct-exec", "--vmid", "170", "--command", "id",
            "--execute", "--node", "pveheavy", "--request-timeout", "45",
        ])
        # The model cannot inject a shell or an extra flag into the argv.
        self.assertNotIn("--host", argv)
        self.assertNotIn("--shell", argv)

    def test_tool_is_advertised_to_the_model(self):
        from proxbot.chat import spec as spec_mod
        names = {t["function"]["name"] for t in spec_mod.TOOLS_SPEC}
        self.assertIn("lxc_pct_exec", names)
        self.assertIn("lxc_pct_exec", chat.WRITE_TOOLS)
        self.assertIn("lxc_pct_exec", chat.HARNESS_ASSEMBLED_TOOLS)


class SafetyGateTests(unittest.TestCase):
    def test_read_only_tools_pass(self):
        gate = chat.SafetyGate(auto_approve=False)
        approved, _ = gate.check_and_prompt("nodes_list", {})
        self.assertTrue(approved)

    def test_write_tool_interactive_approval(self):
        gate = chat.SafetyGate(auto_approve=False)
        with mock.patch("builtins.input", return_value="y"):
            approved, auto = gate.check_and_prompt("vm_start", {"node": "pve1", "vmid": 100})
            self.assertTrue(approved)
            self.assertFalse(auto)

    def test_write_tool_interactive_auto_all(self):
        gate = chat.SafetyGate(auto_approve=False)
        with mock.patch("builtins.input", return_value="a"):
            approved, auto = gate.check_and_prompt("vm_start", {"node": "pve1", "vmid": 100})
            self.assertTrue(approved)
            self.assertTrue(auto)
            self.assertTrue(gate.auto_approve)

    def test_write_tool_rejection(self):
        gate = chat.SafetyGate(auto_approve=False)
        with mock.patch("builtins.input", return_value="n"):
            approved, _ = gate.check_and_prompt("vm_start", {"node": "pve1", "vmid": 100})
            self.assertFalse(approved)

    def test_destructive_tool_requires_yes(self):
        gate = chat.SafetyGate(auto_approve=True)  # even with auto-approve, destructive prompts
        with mock.patch("builtins.input", return_value="no"):
            approved, _ = gate.check_and_prompt("vm_stop", {"node": "pve1", "vmid": 100})
            self.assertFalse(approved)

        with mock.patch("builtins.input", return_value="yes"):
            approved, _ = gate.check_and_prompt("vm_stop", {"node": "pve1", "vmid": 100})
            self.assertTrue(approved)


class ToolExecutorArgsTests(unittest.TestCase):
    def setUp(self):
        self.executor = chat.ProxmoxToolExecutor(chat.PROXMOX_CLI_PATH, chat.ENV_PATH)

    def test_build_cli_args_mapping(self):
        self.assertEqual(self.executor._build_cli_args("nodes_list", {}), ["nodes", "list"])
        self.assertEqual(self.executor._build_cli_args("node_status", {"node": "pve1"}), ["node", "status", "--node", "pve1"])
        self.assertEqual(self.executor._build_cli_args("resolve_vmid", {"vmid": 101, "kind": "vm"}), ["resolve", "--vmid", "101", "--kind", "vm"])
        self.assertEqual(self.executor._build_cli_args("vms_list", {"node": "pve1"}), ["vms", "list", "--node", "pve1"])
        self.assertEqual(self.executor._build_cli_args("vm_status", {"node": "pve1", "vmid": 101}), ["vm", "status", "--node", "pve1", "--vmid", "101"])
        self.assertEqual(self.executor._build_cli_args("vm_start", {"node": "pve1", "vmid": 101}), ["vm", "start", "--node", "pve1", "--vmid", "101", "--execute", "--wait"])
        self.assertEqual(self.executor._build_cli_args("vm_stop", {"node": "pve1", "vmid": 101}), ["vm", "stop", "--node", "pve1", "--vmid", "101", "--execute", "--force", "--confirm", "101"])
        self.assertEqual(self.executor._build_cli_args("lxc_stop", {"node": "pve1", "vmid": 200}), ["lxc", "stop", "--node", "pve1", "--vmid", "200", "--execute", "--force", "--confirm", "200"])
        self.assertEqual(self.executor._build_cli_args("vm_snapshot_create", {"node": "pve1", "vmid": 101, "name": "snap1"}), ["vm", "snapshot", "create", "--node", "pve1", "--vmid", "101", "--name", "snap1", "--execute"])
        self.assertEqual(self.executor._build_cli_args("lxc_exec", {"node": "pve1", "vmid": 105, "command": "uptime"}), ["lxc", "exec", "--node", "pve1", "--vmid", "105", "--command", "uptime", "--execute"])
        self.assertEqual(self.executor._build_cli_args("ssh_exec", {"host": "192.168.1.50", "command": "uname -a"}), ["ssh", "exec", "--command", "uname -a", "--execute", "--host", "192.168.1.50"])

    def test_build_cli_args_registry(self):
        with mock.patch.object(self.executor, "_get_cluster_nodes", return_value=["pveheavy"]):
            self.assertEqual(
                self.executor._build_cli_args("templates_registry", {}),
                ["templates", "registry", "--node", "pveheavy"],
            )
            self.assertEqual(
                self.executor._build_cli_args("templates_registry", {"node": "pvestore", "section": "system", "search": "debian"}),
                ["templates", "registry", "--node", "pvestore", "--section", "system", "--search", "debian"],
            )
        self.assertEqual(
            self.executor._build_cli_args("templates_download", {"node": "pveheavy", "storage": "local", "template": "debian-13-standard_13.6-1_amd64.tar.zst"}),
            ["templates", "download", "--node", "pveheavy", "--storage", "local",
             "--template", "debian-13-standard_13.6-1_amd64.tar.zst", "--execute", "--wait"],
        )

    def test_registry_tool_risk_classification(self):
        self.assertIn("templates_registry", chat.READ_ONLY_TOOLS)
        self.assertIn("templates_download", chat.WRITE_TOOLS)
        self.assertNotIn("templates_download", chat.READ_ONLY_TOOLS)


class RollingThoughtStreamerTests(unittest.TestCase):
    def test_lifecycle(self):
        streamer = chat.RollingThoughtStreamer()
        streamer.start()
        streamer.push("Analyzing cluster nodes...")
        streamer.push(" Checking if VM 101 is online.")
        time_to_wait = 0.1
        import time
        time.sleep(time_to_wait)
        streamer.finish()
        # Should finish cleanly without unhandled exceptions
        self.assertFalse(streamer._is_active)


class ChatAgentTests(unittest.TestCase):
    def test_chat_turn_with_tool_call(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = chat.SessionLogger(pathlib.Path(tmpdir))
            mock_executor = mock.MagicMock()
            mock_executor.execute.return_value = {"ok": True, "data": [{"node": "pve1", "status": "online"}]}
            gate = chat.SafetyGate(auto_approve=True)

            agent = chat.ChatAgent(
                base_url="http://mock-llm:1234/v1",
                api_key="test-key",
                model="test-model",
                temperature=0.1,
                max_turns=3,
                executor=mock_executor,
                safety_gate=gate,
                logger=logger,
            )

            # Turn 1 response: emits tool call for nodes_list
            chunk_tool = json.dumps({
                "choices": [{
                    "delta": {
                        "tool_calls": [{
                            "index": 0,
                            "id": "call_123",
                            "function": {"name": "nodes_list", "arguments": "{}"}
                        }]
                    }
                }]
            })
            resp1 = mock.MagicMock()
            resp1.status_code = 200
            resp1.iter_lines.return_value = [f"data: {chunk_tool}".encode(), b"data: [DONE]"]

            # Turn 2 response: final answer
            chunk_answer = json.dumps({
                "choices": [{
                    "delta": {
                        "content": "The cluster node `pve1` is online."
                    }
                }]
            })
            resp2 = mock.MagicMock()
            resp2.status_code = 200
            resp2.iter_lines.return_value = [f"data: {chunk_answer}".encode(), b"data: [DONE]"]

            with mock.patch("chat.requests.post", side_effect=[resp1, resp2]):
                answer = agent.chat("Check nodes status")
                self.assertIn("The cluster node `pve1` is online.", answer)
                mock_executor.execute.assert_called_once_with("nodes_list", {})
                self.assertEqual(logger.stats["tool_calls"], 1)
                self.assertEqual(logger.stats["user_prompts"], 1)

    def test_lxc_exec_password_escalation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = chat.SessionLogger(pathlib.Path(tmpdir))
            mock_executor = mock.MagicMock()
            # First call returns login_required, second call (with password) returns success
            mock_executor.execute.side_effect = [
                {"ok": False, "status": "login_required", "prompt": "netbox login: "},
                {"ok": True, "data": {"stdout": "Linux 6.8\n", "exit_code": 0}}
            ]
            gate = chat.SafetyGate(auto_approve=True)

            agent = chat.ChatAgent(
                base_url="http://mock-llm:1234/v1",
                api_key="test-key",
                model="test-model",
                temperature=0.1,
                max_turns=3,
                executor=mock_executor,
                safety_gate=gate,
                logger=logger,
            )

            chunk_tool = json.dumps({
                "choices": [{
                    "delta": {
                        "tool_calls": [{
                            "index": 0,
                            "id": "call_lxc_exec",
                            "function": {"name": "lxc_exec", "arguments": json.dumps({"node": "pveheavy", "vmid": 240, "command": "uname -r"})}
                        }]
                    }
                }]
            })
            resp1 = mock.MagicMock()
            resp1.status_code = 200
            resp1.iter_lines.return_value = [f"data: {chunk_tool}".encode(), b"data: [DONE]"]

            chunk_answer = json.dumps({
                "choices": [{
                    "delta": {
                        "content": "Command executed: Linux 6.8"
                    }
                }]
            })
            resp2 = mock.MagicMock()
            resp2.status_code = 200
            resp2.iter_lines.return_value = [f"data: {chunk_answer}".encode(), b"data: [DONE]"]

            with mock.patch("chat.requests.post", side_effect=[resp1, resp2]), \
                 mock.patch("getpass.getpass", return_value="mysecretpass"):
                answer = agent.chat("run uname on netbox")
                self.assertIn("Command executed: Linux 6.8", answer)
                self.assertEqual(mock_executor.execute.call_count, 2)
                # Second call should have password passed
                second_call_args = mock_executor.execute.call_args_list[1]
                self.assertEqual(second_call_args[0][1]["password"], "mysecretpass")

    def test_build_cli_args_vm_exec(self):
        executor = chat.ProxmoxToolExecutor(cli_path=pathlib.Path("/tmp/fake-cli.py"), env_path=pathlib.Path("/tmp/fake.env"))
        args = executor._build_cli_args("vm_exec", {"node": "pveheavy", "vmid": 105, "command": "uname -a", "timeout": 45})
        self.assertEqual(args, ["vm", "exec", "--node", "pveheavy", "--vmid", "105", "--command", "uname -a", "--execute", "--timeout", "45"])

    def test_vm_exec_tool_execution(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = chat.SessionLogger(pathlib.Path(tmpdir))
            mock_executor = mock.MagicMock()
            mock_executor.execute.return_value = {
                "ok": True,
                "data": {"stdout": "Linux debian-vm 6.1.0\n", "exit_code": 0, "pid": 1234}
            }
            gate = chat.SafetyGate(auto_approve=True)

            agent = chat.ChatAgent(
                base_url="http://mock-llm:1234/v1",
                api_key="test-key",
                model="test-model",
                temperature=0.1,
                max_turns=3,
                executor=mock_executor,
                safety_gate=gate,
                logger=logger,
            )

            chunk_tool = json.dumps({
                "choices": [{
                    "delta": {
                        "tool_calls": [{
                            "index": 0,
                            "id": "call_vm_exec",
                            "function": {"name": "vm_exec", "arguments": json.dumps({"node": "pveheavy", "vmid": 105, "command": "uname -a"})}
                        }]
                    }
                }]
            })
            resp1 = mock.MagicMock()
            resp1.status_code = 200
            resp1.iter_lines.return_value = [f"data: {chunk_tool}".encode(), b"data: [DONE]"]

            chunk_answer = json.dumps({
                "choices": [{
                    "delta": {
                        "content": "VM 105 kernel is Linux debian-vm 6.1.0"
                    }
                }]
            })
            resp2 = mock.MagicMock()
            resp2.status_code = 200
            resp2.iter_lines.return_value = [f"data: {chunk_answer}".encode(), b"data: [DONE]"]

            with mock.patch("chat.requests.post", side_effect=[resp1, resp2]):
                answer = agent.chat("run uname -a on vm 105")
                self.assertIn("VM 105 kernel is Linux debian-vm 6.1.0", answer)
                mock_executor.execute.assert_called_once_with("vm_exec", {"node": "pveheavy", "vmid": 105, "command": "uname -a"})


class ModelResolutionTests(unittest.TestCase):
    def _mock_models_response(self, ids, status_code=200):
        resp = mock.MagicMock()
        resp.status_code = status_code
        resp.json.return_value = {"data": [{"id": i} for i in ids]}
        return resp

    def test_explicit_request_wins_without_fetch(self):
        with mock.patch("chat.fetch_backend_models") as mock_fetch:
            model, notice = chat.resolve_model("http://x:1234/v1", "k", "my-model", "fallback")
            self.assertEqual(model, "my-model")
            self.assertIsNone(notice)
            mock_fetch.assert_not_called()

    def test_auto_keyword_forces_detection(self):
        with mock.patch("chat.requests.get", return_value=self._mock_models_response(["loaded-a", "other"])):
            model, notice = chat.resolve_model("http://x:1234/v1", "k", "auto", "fallback")
            self.assertEqual(model, "loaded-a")
            self.assertIsNone(notice)

    def test_detected_backend_model_used_by_default(self):
        with mock.patch("chat.requests.get", return_value=self._mock_models_response(["loaded-model"])):
            model, notice = chat.resolve_model("http://x:1234/v1", "k", None, "fallback")
            self.assertEqual(model, "loaded-model")
            self.assertIsNone(notice)

    def test_fallback_when_backend_empty(self):
        with mock.patch("chat.requests.get", return_value=self._mock_models_response([])):
            model, notice = chat.resolve_model("http://x:1234/v1", "k", None, "fallback")
            self.assertEqual(model, "fallback")
            self.assertIsNotNone(notice)

    def test_fallback_when_backend_unreachable(self):
        with mock.patch("chat.requests.get", side_effect=Exception("conn refused")):
            model, notice = chat.resolve_model("http://x:1234/v1", "k", None, "fallback")
            self.assertEqual(model, "fallback")
            self.assertIsNotNone(notice)

    def test_fetch_ignores_non_200(self):
        def _get(url, **kwargs):
            resp = mock.MagicMock()
            resp.status_code = 404 if url.endswith("/api/v0/models") else 500
            resp.json.return_value = {}
            return resp
        with mock.patch("chat.requests.get", side_effect=_get):
            self.assertEqual(chat.fetch_backend_models("http://x:1234/v1", "k"), [])

    def test_fetch_prefers_loaded_lm_studio_model(self):
        v0 = mock.MagicMock()
        v0.status_code = 200
        v0.json.return_value = {"data": [
            {"id": "catalog-first", "state": "not-loaded", "type": "llm"},
            {"id": "actually-loaded", "state": "loaded", "type": "llm"},
            {"id": "embed", "state": "loaded", "type": "embeddings"},
        ]}
        with mock.patch("chat.requests.get", return_value=v0) as mock_get:
            self.assertEqual(chat.fetch_backend_models("http://x:1234/v1", "k"), ["actually-loaded"])
            mock_get.assert_called_once_with("http://x:1234/api/v0/models", headers=mock.ANY, timeout=mock.ANY)

    def test_fetch_falls_back_to_v1_catalog(self):
        v0 = mock.MagicMock()
        v0.status_code = 404
        v0.json.return_value = {}
        v1 = mock.MagicMock()
        v1.status_code = 200
        v1.json.return_value = {"data": [{"id": "catalog-a"}, {"id": "catalog-b"}]}
        with mock.patch("chat.requests.get", side_effect=[v0, v1]):
            self.assertEqual(chat.fetch_backend_models("http://x:1234/v1", "k"), ["catalog-a", "catalog-b"])


if __name__ == "__main__":
    unittest.main()
