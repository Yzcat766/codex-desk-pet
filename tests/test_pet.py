import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from hooks.codex_adapter import adapt
from manage import apply_hook_change, hook_groups, install, uninstall
from pet.config import ROOT, SPRITES, STALE_AFTER
from pet.session_manager import SessionManager, display_state, reduce_event
from pet.state_manager import file_lock, read_json


def event(name, second=1, sid="first", turn="one", **fields):
    return adapt({"hook_event_name": name, "session_id": sid, "cwd": r"D:\测试 项目",
                  "turn_id": turn, **fields}, second * 10**9)


class AdapterTests(unittest.TestCase):
    def test_tool_categories_and_private_input(self):
        for tool, args, expected in (
            ("Bash", {"command": "Get-Content 'private path.txt'"}, "reading"),
            ("Bash", {"command": "rg pattern; Remove-Item file"}, "tool"),
            ("Bash", {"command": "pytest"}, "tool"),
            ("apply_patch", {"command": "private patch"}, "tool"),
            ("spawn_agent", {"message": "private prompt"}, "tool"),
            ("mcp__filesystem__read_file", {"path": r"C:\private\示例.txt"}, "reading"),
            ("unknown", {}, "tool"),
        ):
            with self.subTest(tool=tool, args=args):
                value = event("PreToolUse", tool_name=tool, tool_input=args)
                self.assertEqual(expected, value.state)
                self.assertNotIn("private", value.text)
        read = event("PreToolUse", tool_name="read_file", tool_input={"path": r"C:\private\示例.txt"})
        self.assertEqual("在读 示例.txt", read.text)
        self.assertEqual("approval", event("PreToolUse", tool_name="request_user_input").state)

    def test_failure_detection_and_bad_payloads(self):
        self.assertTrue(event("PostToolUse", tool_name="Bash", tool_response={"exit_code": 2}).failed)
        self.assertTrue(event("PostToolUse", tool_name="mcp__x__read", tool_response={"isError": True}).failed)
        self.assertFalse(event("PostToolUse", tool_name="Bash",
                               tool_response="Process exited with code 2").failed)
        self.assertIsNone(adapt([], 1))
        self.assertIsNone(adapt({"hook_event_name": "Stop"}, 1))
        self.assertIsNone(event("Stop", agent_id="subagent"))
        self.assertIsNone(event("SessionStart", source="compact"))
        self.assertIsNone(event("PostModelRequest", status="cancelled"))


class StateTests(unittest.TestCase):
    def test_turn_lifecycle(self):
        current = None
        for index, name, state in (
            (1, "SessionStart", "waiting"), (2, "UserPromptSubmit", "thinking"),
            (3, "PreModelRequest", "thinking"), (4, "PreToolUse", "tool"),
            (5, "PostToolUse", "thinking"), (6, "PermissionRequest", "approval"),
            (7, "Stop", "celebrate"), (8, "SessionEnd", "offline"),
        ):
            value = event(name, index, turn="" if name.startswith("Session") else "one",
                          tool_name="Bash", tool_use_id="tool")
            current = reduce_event(current, value)
            self.assertEqual(state, current["state"])
            self.assertEqual(index, current["sequence"])
            self.assertEqual("测试 项目", current["project"])
        self.assertEqual("done", current["notification"]["sound"])
        self.assertEqual("celebrate", display_state(current, 8)[0])
        self.assertEqual("offline", display_state(current, 20)[0])
        self.assertEqual(5000, current["duration_ms"])

    def test_parallel_tool_finish(self):
        value = reduce_event(None, event("PreToolUse", 1, tool_name="read_file", tool_use_id="a"))
        value = reduce_event(value, event("PreToolUse", 2, tool_name="Bash", tool_use_id="b"))
        value = reduce_event(value, event("PostToolUse", 3, tool_name="Bash", tool_use_id="b"))
        self.assertEqual("reading", value["state"])
        self.assertIsNone(reduce_event(value, event("PostToolUse", 4, tool_name="Bash", tool_use_id="b")))

    def test_duplicates_late_events_and_interrupt(self):
        value = reduce_event(None, event("UserPromptSubmit", 3))
        self.assertIsNone(reduce_event(value, event("UserPromptSubmit", 4)))
        self.assertIsNone(reduce_event(value, event("PreToolUse", 2)))
        value = reduce_event(value, event("Interrupt", 4))
        self.assertEqual("aborted", value["state"])
        self.assertIsNone(value["sound"])
        self.assertIsNone(reduce_event(value, event("Stop", 6)))
        value = reduce_event(value, event("UserPromptSubmit", 7, turn="two"))
        self.assertIsNone(reduce_event(value, event("PreToolUse", 8, turn="one")))
        self.assertEqual("thinking", display_state(value, 9999)[0])

    def test_stone_wait_and_token_usage(self):
        value = reduce_event(None, event("PreToolUse", 1, tool_name="Bash"))
        for index, expected in ((2, "error"), (3, "error"), (4, "stone")):
            value = reduce_event(value, event("PostToolUse", index, tool_name="Bash",
                                               tool_use_id=str(index), tool_response={"exit_code": 1}))
            self.assertEqual(expected, value["state"])
        value = reduce_event(value, event("PostModelRequest", 5, status="completed",
                                         token_usage={"input_tokens": 10, "output_tokens": 1}))
        self.assertEqual(10, value["usage"]["input_tokens"])
        value = reduce_event(value, event("Stop", 6))
        self.assertEqual("waiting", display_state(value, 11)[0])
        self.assertEqual("idle", display_state(value, 700)[0])


class SessionTests(unittest.TestCase):
    def test_restore_cleanup_and_corrupt_file(self):
        with tempfile.TemporaryDirectory(prefix="pet 测试 ") as folder:
            directory = Path(folder)
            writer, reader = SessionManager(directory), SessionManager(directory)
            reader.refresh()
            self.assertIsNone(reader.select())
            writer.record(event("UserPromptSubmit", 1, sid="a"))
            writer.record(event("PreToolUse", 2, sid="b"))
            writer.record(event("Stop", 3, sid="a"))
            reader.refresh()
            self.assertEqual("a", reader.select()["session_id"])
            self.assertEqual("tool", reader.select("b")["state"])
            writer.record(event("SessionEnd", 4, sid="a", turn=""))
            reader.refresh()
            self.assertEqual("b", reader.select()["session_id"])
            self.assertEqual("offline", reader.select("a")["state"])
            for path in (directory / "sessions").glob("*.json"):
                if read_json(path)["session_id"] == "a":
                    path.write_text("{", encoding="utf-8")
                else:
                    stamp = time.time() - STALE_AFTER - 10
                    os.utime(path, (stamp, stamp))
            reader.refresh()
            self.assertEqual("a", reader.select()["session_id"])
            self.assertNotIn("b", reader.snapshots)
            cold = SessionManager(directory)
            cold.refresh()
            self.assertIsNone(cold.select())

    def test_file_lock_and_concurrent_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            with file_lock(path / ".instance.lock", timeout=0):
                with self.assertRaises(TimeoutError):
                    with file_lock(path / ".instance.lock", timeout=0):
                        pass
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(lambda n: SessionManager(path).record(event("PreModelRequest", n)),
                              range(1, 51)))
            reader = SessionManager(path)
            reader.refresh()
            self.assertEqual(50 * 10**9, reader.select()["event_ns"])

    def test_hook_process_input_and_permissions(self):
        with tempfile.TemporaryDirectory(prefix="pet 中文 ") as folder:
            directory = Path(folder)
            hook = ROOT / "hooks" / "codex_event.py"
            raw = b'{"hook_event_name":"UserPromptSubmit","session_id":"cli","prompt":"PRIVATE"}'
            good = subprocess.run([sys.executable, str(hook), folder], input=raw, capture_output=True, check=True)
            self.assertEqual(b"{}", good.stdout.strip())
            self.assertEqual(b"", good.stderr)
            state_file = next((directory / "sessions").glob("*.json"))
            self.assertNotIn(b"PRIVATE", state_file.read_bytes())
            invalid = directory / "file"
            invalid.touch()
            bad = subprocess.run([sys.executable, str(hook), str(invalid)], input=raw,
                                 capture_output=True, check=True)
            self.assertEqual(b"{}", bad.stdout.strip())
            self.assertEqual(b"", bad.stderr)


@unittest.skipUnless(os.name == "nt", "Windows installation")
class InstallationTests(unittest.TestCase):
    def test_install_reinstall_uninstall_with_existing_hooks(self):
        with tempfile.TemporaryDirectory(prefix="pet 安装 ") as folder:
            root = Path(folder).resolve()
            directory, codex = root / "data", root / "codex"
            directory.mkdir()
            codex.mkdir()
            hooks = codex / "hooks.json"
            original = {"description": "其他配置", "hooks": {"Stop": [{"matcher": "other", "hooks": [
                {"type": "command", "command": "other"}]}]}}
            hooks.write_text(json.dumps(original), encoding="utf-8")
            directory.joinpath("settings.json").write_text('{"sound":false}', encoding="utf-8")
            first_release, _ = install(directory, codex)
            install(directory, codex)
            self.assertEqual(2, len(read_json(hooks)["hooks"]["Stop"]))
            self.assertTrue(first_release.joinpath("pet", "pet.pyw").exists())
            installed = hooks.read_bytes()
            edited = read_json(hooks)
            edited["hooks"]["Stop"][-1]["hooks"].append({"type": "command", "command": "third-party"})
            hooks.write_text(json.dumps(edited), encoding="utf-8")
            with self.assertRaises(ValueError):
                uninstall(directory)
            self.assertEqual(edited, read_json(hooks))
            hooks.write_bytes(installed)
            self.assertTrue(uninstall(directory))
            self.assertEqual(original, read_json(hooks))
            self.assertFalse(first_release.exists())
            self.assertFalse(read_json(directory / "settings.json")["sound"])

    def test_malformed_hook_file_and_rollback(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "hooks.json"
            groups = hook_groups(Path(sys.executable), ROOT, path.parent)
            path.write_bytes(b"{")
            with self.assertRaises(ValueError):
                apply_hook_change(path, {}, groups, lambda _: None)
            self.assertEqual(b"{", path.read_bytes())
            original = b'{ "hooks": {} }'
            path.write_bytes(original)
            with self.assertRaises(PermissionError):
                apply_hook_change(path, {}, groups, lambda _: (_ for _ in ()).throw(PermissionError()))
            self.assertEqual(original, path.read_bytes())


class AssetTests(unittest.TestCase):
    def test_all_state_assets_exist(self):
        for sprite in SPRITES.values():
            self.assertTrue(ROOT.joinpath("pet", "sprites", sprite + ".png").is_file())
        for sound in ("done", "error"):
            self.assertTrue(ROOT.joinpath("sounds", sound + ".wav").is_file())


if __name__ == "__main__":
    unittest.main()
