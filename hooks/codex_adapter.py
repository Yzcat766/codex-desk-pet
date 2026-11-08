"""Allow-list Codex hook input before anything reaches persistent state."""
import ntpath
import re
from dataclasses import dataclass
from typing import Optional

from pet.config import ACTIVITY_TEXT, TEXT

EVENTS = (
    "SessionStart", "SessionEnd", "UserPromptSubmit", "PreModelRequest", "PostModelRequest",
    "PreToolUse", "PostToolUse", "PermissionRequest", "Stop", "Interrupt",
)
READ_TOOLS = {"read", "read_file", "read_text_file", "read_multiple_files",
              "grep", "glob", "search", "search_files", "list_directory", "web_search", "web_fetch"}
EDIT_TOOLS = {"apply_patch", "edit", "write", "edit_file", "write_file"}
SHELL_TOOLS = {"bash", "powershell", "shell", "shell_command", "exec_command"}
READ_COMMANDS = {"cat", "head", "tail", "less", "more", "type", "get-content",
                 "rg", "grep", "findstr", "ls", "dir", "get-childitem", "select-string"}
TEST_COMMANDS = {"pytest", "vitest", "jest", "ctest", "tox"}
WRAPPERS = re.compile(
    r'''^\s*(?:"[^"\r\n]*[\\/]|'[^'\r\n]*[\\/]|[^\s"']*[\\/])?'''
    r'''(?:pwsh|powershell|bash|sh)(?:\.exe)?["']?\s+'''
    r'''(?:-[\w-]+\s+)*?(?:-Command|-c|-lc)\s+''', re.IGNORECASE,
)


@dataclass(frozen=True)
class Event:
    name: str
    session_id: str
    timestamp_ns: int
    cwd: str = ""
    turn_id: str = ""
    tool_id: str = ""
    tool: str = ""
    state: str = ""
    text: str = ""
    failed: bool = False
    usage: Optional[dict] = None


def safe_label(value, limit=80):
    if not isinstance(value, str):
        return ""
    return "".join(char for char in value if char.isprintable())[:limit]


def tool_activity(tool, arguments):
    short = tool.lower().rsplit("__", 1)[-1]
    kind = "tool"
    if short in READ_TOOLS:
        kind = "reading"
    elif short in EDIT_TOOLS:
        kind = "edit"
    elif short in {"spawn_agent", "agent", "wait_agent", "send_input"}:
        kind = "agent"
    elif short == "request_user_input":
        return "approval", TEXT["approval"]
    elif short in SHELL_TOOLS:
        command = arguments.get("command")
        if isinstance(command, str):
            command = WRAPPERS.sub("", command[:4096]).strip().strip("'\"")
            words = command.lower().split()
            if words and (words[0] in TEST_COMMANDS or
                          words[:2] in (["cargo", "test"], ["npm", "test"], ["dotnet", "test"]) or
                          words[:3] in (["python", "-m", "pytest"], ["python", "-m", "unittest"])):
                kind = "test"
            elif (words and words[0] in READ_COMMANDS and
                  not any(symbol in command for symbol in ";&|><`\n\r")):
                kind = "reading"
            else:
                kind = "command"
    path = arguments.get("file_path") or arguments.get("path")
    filename = safe_label(ntpath.basename(path.rstrip("\\/")), 28) if isinstance(path, str) else ""
    if filename and kind in {"reading", "edit"}:
        text = ACTIVITY_TEXT["read_file" if kind == "reading" else "edit_file"].format(file=filename)
    else:
        text = ACTIVITY_TEXT.get(kind, TEXT.get(kind, TEXT["tool"]))
    return ("reading" if kind == "reading" else "tool"), text


def tool_failed(response, shell):
    if not isinstance(response, dict):
        # Unified exec can supply plain command output without exit metadata.
        return False
    if response.get("isError") is True or response.get("is_error") is True:
        return True
    code = response.get("exit_code")
    return shell and type(code) is int and code != 0


def adapt(payload, timestamp_ns):
    if not isinstance(payload, dict):
        return None
    name = payload.get("hook_event_name")
    sid = payload.get("session_id")
    if name not in EVENTS or not isinstance(sid, str) or not 0 < len(sid) <= 256:
        return None
    if payload.get("agent_id") or payload.get("agent_type"):
        return None
    if name == "SessionStart" and payload.get("source") == "compact":
        return None
    args = payload.get("tool_input")
    tool = safe_label(payload.get("tool_name"), 160)
    if not re.fullmatch(r"[\w.-]+", tool):
        tool = ""
    state, text = tool_activity(tool, args if isinstance(args, dict) else {})
    failed = name == "PostToolUse" and tool_failed(payload.get("tool_response"), tool.lower() in SHELL_TOOLS)
    usage = None
    if name == "PostModelRequest":
        status = payload.get("status")
        if status not in ("completed", "failed"):
            return None
        failed = status == "failed"
        raw = payload.get("token_usage")
        if isinstance(raw, dict):
            counts = {key: raw.get(key) for key in ("input_tokens", "output_tokens", "cached_input_tokens")}
            usage = {key: value for key, value in counts.items() if type(value) is int and value >= 0} or None
    cwd = payload.get("cwd")
    return Event(name, sid, timestamp_ns, cwd if isinstance(cwd, str) and len(cwd) <= 32768 else "",
                 safe_label(payload.get("turn_id"), 256), safe_label(payload.get("tool_use_id"), 256),
                 tool, state, text, failed, usage)
