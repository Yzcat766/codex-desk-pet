"""Exercise real Codex processes with an isolated home and a local Responses fixture."""
import argparse
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from manage import install
from pet.config import ROOT
from pet.session_manager import SessionManager
from pet.state_manager import read_json


class ModelFixture(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        if not self.path.endswith("/responses"):
            self.send_error(404)
            return
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.server.fail:
            self.send_error(500, "fixture model request failure")
            return
        outputs = {item.get("call_id"): item.get("output") for item in request["input"]
                   if item.get("type") == "function_call_output"}
        self.server.tool_outputs.update(outputs)
        if self.server.hold:
            self.server.entered.set()
            self.server.release.wait(30)
        name = "exec_command"
        available = {tool.get("name") for tool in request.get("tools", [])}
        if name not in available and "shell_command" in available:
            name = "shell_command"
        path = str(self.server.project / "probe.txt").replace("'", "''")
        args = {"cmd" if name == "exec_command" else "command": f"Get-Content -LiteralPath '{path}'",
                "workdir": str(self.server.project), "login": False}
        if "call-pet-error" in outputs:
            ident = "response-final"
            item = {"type": "message", "id": "message-pet", "role": "assistant",
                    "content": [{"type": "output_text", "text": "fixture complete"}]}
        else:
            failing = "call-pet-read" in outputs
            ident = "response-error" if failing else "response-read"
            if failing:
                args["cmd" if name == "exec_command" else "command"] = "exit 7"
            item = {"type": "function_call", "name": name,
                    "call_id": "call-pet-error" if failing else "call-pet-read",
                    "arguments": json.dumps(args)}
        events = [
            {"type": "response.created", "response": {"id": ident}},
            {"type": "response.output_item.done", "item": item},
            {"type": "response.completed", "response": {"id": ident,
                "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12,
                          "input_tokens_details": {"cached_tokens": 3}, "output_tokens_details": None}}},
        ]
        data = "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events)
        body = data.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass


class Rpc:
    def __init__(self, process):
        self.process = process
        self.messages = queue.Queue()
        self.id = 0
        self.observed = []
        threading.Thread(target=self.read, daemon=True).start()

    def read(self):
        for line in self.process.stdout:
            try:
                self.messages.put(json.loads(line))
            except ValueError:
                pass
        self.messages.put(None)

    def send(self, message):
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def wait(self, match):
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            message = self.messages.get(timeout=max(0.01, deadline - time.monotonic()))
            if message is None:
                raise RuntimeError("App Server exited before the response")
            if message.get("method", "").startswith("hook/"):
                self.observed.append(message)
            if match(message):
                return message
        raise TimeoutError("App Server response timed out")

    def call(self, method, params):
        self.id += 1
        request_id = self.id
        self.send({"id": request_id, "method": method, "params": params})
        message = self.wait(lambda item: item.get("id") == request_id)
        if "error" in message:
            raise RuntimeError(f"{method} failed: {message['error']}")
        return message["result"]


def snapshot(data, sid=None):
    sessions = SessionManager(data)
    sessions.refresh()
    return sessions.select(sid)


def wait_state(data, sid, state):
    for _ in range(50):
        current = snapshot(data, sid)
        if current and current["state"] == state:
            return current
        time.sleep(0.1)
    raise AssertionError(f"Expected {state}; got {current}")


def cli_test(codex, env, project, data, fixture):
    command = [codex, "exec", "--json", "--skip-git-repo-check", "-C", str(project),
               "Run the read-only fixture command."]
    def execute():
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            result = subprocess.run(command, env=env, cwd=project, stdin=subprocess.DEVNULL,
                                    stdout=stdout, stderr=stderr, timeout=70)
            stderr.seek(0)
            if result.returncode:
                raise RuntimeError(stderr.read().decode("utf-8", "replace")[-2000:])
            stdout.seek(0)
            return [json.loads(line) for line in stdout if line.startswith(b"{")]

    execute()
    assert snapshot(data) is None, "New non-managed hooks executed before review"
    print("PASS: untrusted hooks skipped")
    with app_connection(codex, env, project) as rpc:
        review_fixture_hooks(rpc, project, Path(env["CODEX_HOME"]), data)
    observed = set()
    complete = threading.Event()

    def watch():
        reader = SessionManager(data)
        while not complete.wait(0.03):
            reader.refresh()
            observed.update((row["last_event"], row["state"]) for row in reader.snapshots.values())

    thread = threading.Thread(target=watch)
    thread.start()
    try:
        events = execute()
    finally:
        complete.set()
        thread.join()
    assert any(row.get("type") == "turn.completed" for row in events), events
    assert "desk pet probe" in str(fixture.tool_outputs.get("call-pet-read")), fixture.tool_outputs
    assert "Process exited with code 7" in str(fixture.tool_outputs.get("call-pet-error")), fixture.tool_outputs
    current = snapshot(data)
    assert current and current.get("notification", {}).get("sound") == "done", current
    assert current["state"] in {"offline", "celebrate"}, current
    assert ("PreToolUse", "reading") in observed, observed
    print("PASS: CLI executed a real shell read and a command with exit code 7, then emitted completion")
    if ("PostToolUse", "error") in observed:
        print("PASS: CLI Shell PostToolUse provided structured failure metadata")
    else:
        print("UNSUPPORTED: Shell PostToolUse did not expose usable failure metadata in this CLI")
    print("CLI observed:", sorted(observed))
    print("CLI snapshot:", {key: current[key] for key in ("state", "last_event", "sequence", "usage", "duration_ms")})


@contextmanager
def app_connection(codex, env, project):
    command = [codex, "app-server"]
    with (project.parent / "app-server.log").open("w+", encoding="utf-8") as log:
        with subprocess.Popen(command, env=env, cwd=project, stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, stderr=log, encoding="utf-8") as process:
            try:
                rpc = Rpc(process)
                rpc.call("initialize", {"clientInfo": {"name": "desk-pet-test", "version": "0.1"}})
                rpc.send({"method": "initialized", "params": {}})
                yield rpc
            except Exception:
                log.seek(0)
                print(log.read()[-3000:], file=sys.stderr)
                raise
            finally:
                process.stdin.close()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.terminate()


def review_fixture_hooks(rpc, project, home, data):
    def list_hooks():
        entry = rpc.call("hooks/list", {"cwds": [str(project)]})["data"][0]
        assert not entry["errors"], entry["errors"]
        return entry["hooks"]

    hooks = list_hooks()
    expected = read_json(data / "installation.json")["groups"]
    command = expected["Stop"]["hooks"][0]["command"]
    assert len(hooks) == len(expected)
    for hook in hooks:
        assert Path(hook["sourcePath"]).resolve() == home / "hooks.json"
        assert hook["command"] == command
    if any(hook["trustStatus"] != "trusted" for hook in hooks):
        rpc.call("config/batchWrite", {
            "filePath": str(home / "config.toml"), "reloadUserConfig": True,
            "edits": [{"keyPath": "hooks.state", "mergeStrategy": "upsert",
                       "value": {hook["key"]: {"trusted_hash": hook["currentHash"]} for hook in hooks}}],
        })
    assert all(hook["trustStatus"] == "trusted" for hook in list_hooks())
    print("PASS: App Server discovered and reviewed", len(hooks), "fixture hooks by exact hash")


def app_test(codex, env, project, data, fixture):
    with app_connection(codex, env, project) as rpc:
        review_fixture_hooks(rpc, project, Path(env["CODEX_HOME"]), data)
        fixture.hold = True
        try:
            thread = rpc.call("thread/start", {"cwd": str(project)})["thread"]
            sid = thread["id"]
            turn = rpc.call("turn/start", {"threadId": sid,
                           "input": [{"type": "text", "text": "Wait for fixture response.", "textElements": []}]})
            assert fixture.entered.wait(20), "Model fixture was not reached"
            wait_state(data, sid, "thinking")
            rpc.call("turn/interrupt", {"threadId": sid, "turnId": turn["turn"]["id"]})
            rpc.wait(lambda item: item.get("method") == "turn/completed")
            wait_state(data, sid, "aborted")
            print("PASS: App Server turn/interrupt drove the real Interrupt hook")
            rpc.call("thread/archive", {"threadId": sid})
            wait_state(data, sid, "offline")
            print("PASS: App Server thread/archive drove the real SessionEnd hook")
            for item in rpc.observed:
                if item["method"] == "hook/completed":
                    run = item["params"]["run"]
                    print("Hook:", run["eventName"], run["status"], run.get("durationMs"))
        finally:
            fixture.release.set()
        fixture.hold = False
        fixture.fail = True
        sid = rpc.call("thread/start", {"cwd": str(project)})["thread"]["id"]
        rpc.call("turn/start", {"threadId": sid,
                 "input": [{"type": "text", "text": "Exercise the failing fixture.", "textElements": []}]})
        rpc.wait(lambda item: item.get("method") == "turn/completed")
        wait_state(data, sid, "error")
        print("PASS: App Server model failure drove the real PostModelRequest error state")


@contextmanager
def fixture_directory():
    # Python 3.13 gives mkdtemp directories a private Windows DACL that can
    # prevent Codex's restricted shell from reading its own fixture workspace.
    directory = ROOT / (".pet-fixture-" + uuid.uuid4().hex)
    directory.mkdir()
    try:
        yield directory
    finally:
        if directory.resolve() == directory and directory.parent == ROOT:
            shutil.rmtree(directory)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-vetted-fixture", action="store_true", required=True,
                        help="Allow this isolated invocation to run only the hooks installed by this checkout")
    parser.add_argument("--codex", default=shutil.which("codex"))
    parser.add_argument("--phase", choices=("cli", "app-server", "all"), default="all")
    args = parser.parse_args()
    if not args.codex:
        parser.error("Codex CLI is missing")
    with fixture_directory() as root:
        home, data, project, temp = (root / name for name in ("codex", "桌宠 data", "project", "temp"))
        for path in (home, data, project, temp):
            path.mkdir()
        (project / "probe.txt").write_text("desk pet probe", encoding="utf-8")
        env = dict(os.environ, CODEX_HOME=str(home), TEMP=str(temp), TMP=str(temp))
        with ThreadingHTTPServer(("127.0.0.1", 0), ModelFixture) as server:
            server.project = project
            server.tool_outputs = {}
            server.hold = False
            server.fail = False
            server.entered, server.release = threading.Event(), threading.Event()
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                url = f"http://127.0.0.1:{server.server_port}/v1"
                (home / "config.toml").write_text(
                    'model = "gpt-5.4"\nmodel_provider = "pet_fixture"\n'
                    'approval_policy = "never"\nsandbox_mode = "workspace-write"\n'
                    '[windows]\nsandbox = "unelevated"\n'
                    '[analytics]\nenabled = false\n'
                    '[features]\nplugins = false\napps = false\nplugin_discovery_prefetch = false\n'
                    '[model_providers.pet_fixture]\nname = "Desk Pet fixture"\n'
                    f'base_url = "{url}"\nwire_api = "responses"\n'
                    'request_max_retries = 0\nstream_max_retries = 0\n',
                    encoding="utf-8")
                install(data, home)
                if args.phase in ("all", "cli"):
                    cli_test(args.codex, env, project, data, server)
                if args.phase in ("all", "app-server"):
                    app_test(args.codex, env, project, data, server)
            finally:
                server.shutdown()


if __name__ == "__main__":
    main()
