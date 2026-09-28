import importlib.util
import pathlib
import unittest


SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "Proxmox-console.py"
SPEC = importlib.util.spec_from_file_location("proxmox_console", SCRIPT)
console = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(console)


class ConsoleContractTests(unittest.TestCase):
    def test_paste_text_parser_exposes_tier_three_guards(self):
        args = console.build_parser().parse_args([
            "paste-text", "--node", "pve", "--vmid", "101", "--text", "status",
            "--execute", "--force", "--confirm", "101",
        ])
        self.assertTrue(args.execute)
        self.assertTrue(args.force)
        self.assertEqual(args.confirm, "101")

    def test_server_preset_is_versioned_and_navigation_only(self):
        actions = console.INSTALL_PRESETS["debian-13-server-v1"]
        self.assertEqual([action["action"] for action in actions], ["key", "keys"])
        self.assertNotIn("type", [action["action"] for action in actions])

    def test_mouse_click_uses_configured_hold_duration(self):
        client = object.__new__(console._VNCClient)
        client.width, client.height = 640, 480
        events = []
        client._send = lambda payload: events.append(payload) or True
        self.assertTrue(client.mouse_click(10, 20, hold_seconds=0))
        self.assertEqual(events[0][1], 0)
        self.assertEqual(events[1][1], console.VNC_BUTTON_LEFT)
        self.assertEqual(events[2][1], 0)


class KeyNormalizationTests(unittest.TestCase):
    """Locks the key-alias behavior documented in AGENTS.md.

    These functions were reconstructed during the package refactor, so the
    documented contract is pinned here to catch any future drift.
    """

    def test_documented_alias_forms_normalize(self):
        cases = {
            "Esc": "Escape",
            "Del": "Delete",
            "PageUp": "Page_Up",
            "PageDown": "Page_Down",
            "enter": "Enter",
            "Windows": "Super",
            "Win": "Super",
            "Win+R": "Super+R",
            "Super+R": "Super+R",
            "Ctrl+Alt+Delete": "Ctrl+Alt+Delete",
            "ctrl-alt-del": "Ctrl+Alt+Delete",
            "[ctrl]+[alt]+[del]": "Ctrl+Alt+Delete",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(console._canonical_key_token(raw), expected)

    def test_key_sequence_expands_repetition(self):
        self.assertEqual(
            console._parse_key_sequence("Space,Downx10,Space,Tab,Enter"),
            ["Space"] + ["Down"] * 10 + ["Space", "Tab", "Enter"],
        )

    def test_key_sequence_rejects_unknown_key(self):
        self.assertIsNone(console._parse_key_sequence("Frobnicate"))

    def test_combo_events_pair_press_and_release(self):
        events = console._combo_to_events("Ctrl+Alt+Delete")
        self.assertEqual(len([1 for _, press in events if press]), len([1 for _, press in events if not press]))
        self.assertEqual(events[-1][1], 0)

    def test_letter_combo_uses_lowercase_keysym(self):
        # AGENTS.md: lowercase Ctrl-letter keysyms per RFB guidance.
        events = console._combo_to_events("Ctrl+c")
        self.assertEqual(events[1][0], ord("c"))

    def test_shell_metacharacters_are_detected(self):
        for text in ("a;b", "a|b", "a&b", "a`b", "a$b", "a(b)", "a[b]", "a\\b", "a'b", 'a"b', "a\nb"):
            with self.subTest(text=text):
                self.assertTrue(console._type_text_has_shell_metacharacters(text))
        for text in ("ls -la /tmp", "systemctl status ssh", "whoami"):
            with self.subTest(text=text):
                self.assertFalse(console._type_text_has_shell_metacharacters(text))

    def test_macro_action_validation(self):
        ok, normalized = console._validate_macro_action({"action": "key", "key": "enter"}, False)
        self.assertEqual(ok, "")
        self.assertEqual(normalized["key"], "Enter")

        err, _ = console._validate_macro_action({"action": "type", "text": "rm -rf /;x"}, False)
        self.assertIn("metacharacters", err)

        ok, _ = console._validate_macro_action({"action": "type", "text": "rm -rf /;x"}, True)
        self.assertEqual(ok, "")

        err, _ = console._validate_macro_action({"action": "wat"}, False)
        self.assertIn("unsupported", err)

    def test_strip_key_wrappers_removes_quotes_and_brackets(self):
        for raw, expected in [
            ("  enter  ", "enter"),
            ('"enter"', "enter"),
            ("`enter`", "enter"),
            ("[enter]", "enter"),
            ("[ctrl]+[alt]", "ctrl+alt"),
        ]:
            with self.subTest(raw=raw):
                self.assertEqual(console._strip_key_wrappers(raw), expected)

    def test_canonical_single_key_token_keeps_letters_verbatim(self):
        self.assertEqual(console._canonical_single_key_token("a"), "a")
        self.assertEqual(console._canonical_single_key_token("A"), "A")
        self.assertEqual(console._canonical_single_key_token("1"), "1")
        self.assertEqual(console._canonical_single_key_token("Enter"), "Enter")

    def test_looks_like_modifier(self):
        for name in ("ctrl", "Control", "alt", "Shift", "Super", "win"):
            with self.subTest(name=name):
                self.assertTrue(console._looks_like_modifier(name))
        for name in ("enter", "a", "F1"):
            with self.subTest(name=name):
                self.assertFalse(console._looks_like_modifier(name))

    def test_split_key_combo_respects_brackets(self):
        self.assertEqual(console._split_key_combo("Ctrl+Alt+Delete"), ["Ctrl", "Alt", "Delete"])
        self.assertEqual(console._split_key_combo("Ctrl+a"), ["Ctrl", "a"])

    def test_key_name_to_events_shifts_uppercase(self):
        lower = console._key_name_to_events("a")
        self.assertEqual([ks for ks, _ in lower], [ord("a"), ord("a")])

        upper = console._key_name_to_events("A")
        shift = console.X11_KEYSYM["Shift_L"]
        self.assertEqual([ks for ks, _ in upper], [shift, ord("a"), ord("a"), shift])

    def test_is_valid_key_token_rejects_garbage(self):
        self.assertTrue(console._is_valid_key_token("Enter"))
        self.assertTrue(console._is_valid_key_token("a"))
        self.assertTrue(console._is_valid_key_token("Ctrl+a"))
        self.assertFalse(console._is_valid_key_token("Frobnicate"))
        self.assertFalse(console._is_valid_key_token(""))

    def test_key_sequence_summary_counts_canonical_keys(self):
        summary = console._key_sequence_summary(["enter", "Down", "down", "esc"])
        self.assertEqual(summary, {"Enter": 1, "Down": 2, "Escape": 1})

    def test_text_to_events_uses_ascii_keysyms_and_shifts(self):
        events = console._text_to_events("ab")
        self.assertEqual([ks for ks, _ in events], [ord("a"), ord("a"), ord("b"), ord("b")])

        shifted = console._text_to_events("A")
        shift = console.X11_KEYSYM["Shift_L"]
        self.assertEqual([ks for ks, _ in shifted], [shift, ord("a"), ord("a"), shift])

    def test_text_to_events_maps_control_characters_to_named_keys(self):
        self.assertEqual(console._text_to_events("\n"), console._key_name_to_events("Enter"))
        self.assertEqual(console._text_to_events("\t"), console._key_name_to_events("Tab"))

    def test_send_key_events_emits_rfb_key_event_frames(self):
        # RFB 3.8 KeyEvent: U8 type, U8[3] pad, U32 keysym. 0=KeyDown, 1=KeyUp.
        import struct

        sent = []

        class FakeVNC:
            def _send(self, payload):
                sent.append(payload)
                return True

        self.assertTrue(console._send_key_events(FakeVNC(), console._key_name_to_events("Enter")))
        self.assertEqual(len(sent), 2)
        self.assertEqual([frame[0] for frame in sent], [0, 1])
        for frame in sent:
            self.assertEqual(len(frame), 8)
            self.assertEqual(struct.unpack("!I", frame[4:8])[0], console.X11_KEYSYM["Enter"])

    def test_send_key_events_handles_empty_input(self):
        class FakeVNC:
            def _send(self, payload):
                raise AssertionError("should not send")

        self.assertFalse(console._send_key_events(FakeVNC(), []))

    def test_shell_metacharacter_pattern_is_exposed(self):
        self.assertTrue(console.SHELL_METACHARACTERS_RE.search("a;b"))
        self.assertFalse(console.SHELL_METACHARACTERS_RE.search("ls -la"))


if __name__ == "__main__":
    unittest.main()
