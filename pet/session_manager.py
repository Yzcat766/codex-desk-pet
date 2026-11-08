"""Reduce events per session and expose stable snapshots to the renderer."""
import hashlib
import logging
import ntpath
import time
from pathlib import Path

from pet.config import ACTIVITY_TEXT, CELEBRATE_FOR, IDLE_AFTER, SPRITES, STALE_AFTER, TEXT
from pet.state_manager import file_lock, read_json, write_json

NANOSECONDS = 10**9
TERMINAL = {"Stop", "Interrupt", "SessionEnd"}
TURN_START = {"UserPromptSubmit", "PreModelRequest", "PreToolUse", "PermissionRequest"}
PROJECT_EVENTS = {"SessionStart", "UserPromptSubmit", "PreModelRequest", "PostModelRequest"}


def reduce_event(previous, event):
    name, turn, now = event.name, event.turn_id, event.timestamp_ns
    old = previous or {}
    if now <= old.get("event_ns", -1):
        return None
    if old.get("state") == "offline" and name not in TURN_START | {"SessionStart"}:
        return None

    current = old.get("active_turn")
    closed = list(old.get("closed_turns", []))
    if turn and turn in closed:
        return None
    if current and turn and turn != current:
        if name not in TURN_START:
            return None
        closed.append(current)
        current = None

    seen = list(old.get("seen", [])) if current else []
    dedup = None
    if event.tool_id and name in {"PreToolUse", "PostToolUse"}:
        dedup = [name, event.tool_id]
    elif name == "UserPromptSubmit" and turn:
        dedup = [name, turn]
    if dedup and dedup in seen:
        return None
    if name == "SessionStart" and current:
        return None
    if name in {"Stop", "Interrupt"} and not current and old.get("last_event") == name:
        return None

    cwd = old.get("cwd", "")
    if event.cwd and (name in PROJECT_EVENTS or not cwd):
        cwd = event.cwd
    project = ntpath.basename(cwd.rstrip("/\\")) or "Codex"
    state = "thinking"
    message = ""
    sound = None
    errors = old.get("error_count", 0) if current else 0
    tools = dict(old.get("active_tools", {})) if current else {}
    if name in {"SessionStart", "SessionEnd"}:
        state = "waiting" if name == "SessionStart" else "offline"
    elif name == "UserPromptSubmit":
        message = ACTIVITY_TEXT["prompt"]
    elif name == "PermissionRequest":
        state = "approval"
    elif name == "PreToolUse":
        state, message = event.state, event.text
        if event.tool_id:
            tools[event.tool_id] = [state, message]
    elif name == "PostToolUse":
        tools.pop(event.tool_id, None)
        if not event.failed:
            errors = 0
            if tools:
                state, message = next(reversed(tools.values()))
    elif name == "Stop":
        state, sound = "celebrate", "done"
    elif name == "Interrupt":
        state = "aborted"

    if event.failed:
        errors += 1
        state, sound = ("stone" if errors >= 3 else "error"), "error"

    start = old.get("turn_started_ns") if current else None
    if name != "SessionStart" and not start:
        start = now
    if name in TERMINAL:
        if turn or current:
            closed.append(turn or current)
        duration = ((now - start) // 1_000_000 if start is not None and
                    (current or name != "SessionEnd") else old.get("duration_ms"))
        start = None
        current = None
        tools.clear()
    else:
        current = turn or current

    usage = dict(old.get("usage", {}))
    if event.usage:
        for key, value in event.usage.items():
            usage[key] = usage.get(key, 0) + value

    sequence = old.get("sequence", 0) + 1
    return {
        "schema": 1, "source": "codex", "session_id": event.session_id, "project": project,
        "cwd": cwd, "client": "unknown", "state": state, "text": message or TEXT[state],
        "tool": event.tool or None, "timestamp": now / NANOSECONDS, "last_event_time": now / NANOSECONDS,
        "event_ns": now, "sequence": sequence, "last_event": name, "sound": sound,
        "offline_from": old.get("state") if state == "offline" else None,
        "notification": {"sound": sound, "event_ns": now} if sound else old.get("notification"),
        "active_turn": current, "turn_started_ns": start, "error_count": errors,
        "duration_ms": duration if name in TERMINAL else old.get("duration_ms"),
        "closed_turns": closed[-32:], "seen": (seen + ([dedup] if dedup else []))[-128:],
        "active_tools": dict(list(tools.items())[-32:]), "usage": usage,
    }


def display_state(snapshot, now):
    if not snapshot:
        return "offline", TEXT["offline"]
    state = snapshot["state"]
    age = now - snapshot["timestamp"]
    notice = snapshot.get("notification")
    if state in {"offline", "thinking"} and isinstance(notice, dict):
        notice_time = notice.get("event_ns")
        if type(notice_time) is int:
            elapsed = now - notice_time / NANOSECONDS
            if (state == "offline" and snapshot.get("offline_from") == "celebrate" and
                    notice.get("sound") == "done" and 0 <= elapsed < CELEBRATE_FOR):
                return "celebrate", TEXT["celebrate"]
            if state == "thinking" and notice.get("sound") == "error" and 0 <= elapsed < 0.8:
                return "error", TEXT["error"]
    if state == "offline" and snapshot.get("offline_from") == "aborted" and 0 <= age < 2:
        return "aborted", TEXT["aborted"]
    if state == "celebrate" and age >= CELEBRATE_FOR:
        state = "waiting"
    if state == "waiting" and age >= IDLE_AFTER:
        state = "idle"
    return state, snapshot["text"] if state == snapshot["state"] else TEXT[state]


def load_snapshot(path):
    try:
        data = read_json(path)
        if (isinstance(data, dict) and data.get("schema") == 1 and
                isinstance(data.get("session_id"), str) and
                isinstance(data.get("state"), str) and data["state"] in SPRITES and
                type(data.get("event_ns")) is int and type(data.get("sequence")) is int and
                type(data.get("timestamp")) in (int, float) and isinstance(data.get("text"), str) and
                isinstance(data.get("cwd", ""), str) and isinstance(data.get("closed_turns", []), list) and
                isinstance(data.get("seen", []), list) and isinstance(data.get("active_tools", {}), dict) and
                isinstance(data.get("usage", {}), dict) and type(data.get("error_count", 0)) is int and
                (data.get("turn_started_ns") is None or type(data["turn_started_ns"]) is int)):
            return data
    except (OSError, ValueError):
        pass
    return None


class SessionManager:
    def __init__(self, directory):
        self.directory = Path(directory) / "sessions"
        self.snapshots = {}
        self._cache = {}

    def _lock(self, key, timeout=0.25):
        return file_lock(self.directory / (".lock-" + key[:2]), timeout)

    def record(self, event):
        key = hashlib.sha256(event.session_id.encode("utf-8")).hexdigest()
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / (key + ".json")
        with self._lock(key):
            old = load_snapshot(path)
            updated = reduce_event(old, event)
            if updated:
                write_json(path, updated)
            return updated

    def refresh(self, now=None):
        now = time.time() if now is None else now
        snapshots, cache = {}, {}
        try:
            paths = list(self.directory.glob("*.json"))
        except OSError:
            return
        for path in paths:
            try:
                stat = path.stat()
                stamp = stat.st_mtime_ns, stat.st_size
                if now - stat.st_mtime > STALE_AFTER:
                    if len(path.stem) == 64:
                        with self._lock(path.stem, timeout=0):
                            if now - path.stat().st_mtime > STALE_AFTER:
                                path.unlink()
                    continue
                old = self._cache.get(path)
                data = old[1] if old and old[0] == stamp else load_snapshot(path)
                if data is None:
                    if old:
                        cache[path] = old
                        snapshots[old[1]["session_id"]] = old[1]
                    continue
                cache[path] = stamp, data
                snapshots[data["session_id"]] = data
            except FileNotFoundError:
                continue
            except OSError as exc:
                logging.debug("Session read/cleanup skipped (%s)", type(exc).__name__)
        self._cache, self.snapshots = cache, snapshots

    def select(self, pinned=None):
        if pinned in self.snapshots:
            return self.snapshots[pinned]
        now = time.time()
        return max(self.snapshots.values(), key=lambda data: (
            data["state"] != "offline" or display_state(data, now)[0] != "offline", data["event_ns"]),
            default=None)

    def project_usage(self, cwd):
        if not isinstance(cwd, str) or not cwd:
            return {}
        key = ntpath.normcase(ntpath.normpath(cwd))
        totals = {}
        for data in self.snapshots.values():
            path, usage = data.get("cwd"), data.get("usage")
            if not isinstance(path, str) or not isinstance(usage, dict):
                continue
            if ntpath.normcase(ntpath.normpath(path)) == key:
                for name, value in usage.items():
                    if type(value) is int and value >= 0:
                        totals[name] = totals.get(name, 0) + value
        return totals
