"""Central defaults shared by the adapter and Tkinter UI."""
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPRITES = {
    "thinking": "smile-closed", "tool": "typing", "reading": "notes",
    "waiting": "knock", "celebrate": "confetti", "idle": "smile",
    "error": "panic", "aborted": "guilty", "stone": "stone",
    "approval": "knock", "offline": "smile",
}
TEXT = {
    "thinking": "让我想想……", "tool": "努力敲代码中！",
    "reading": "让我看看这个文件～", "waiting": "轮到你啦！",
    "celebrate": "完成啦，快夸夸我！", "error": "呜哇，出错了！",
    "aborted": "被打断了……", "stone": "CPU 烧干了……",
    "idle": "在发呆～ zZ", "approval": "需要你确认一下～",
    "offline": "Codex 休息中～",
}
ACTIVITY_TEXT = {
    "prompt": "收到任务啦！", "search": "在找资料～", "command": "在跑命令…",
    "edit": "正在改文件…", "test": "正在跑测试…", "agent": "小助手出发了！",
    "read_file": "在读 {file}", "edit_file": "在改 {file}",
}
CELEBRATE_FOR = 4
IDLE_AFTER = 10 * 60
STALE_AFTER = 2 * 86400


def run_dir():
    return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "CodexDeskPet"


def configure_log(directory, name, *, rotate=True):
    directory.mkdir(parents=True, exist_ok=True)
    handler = (
        RotatingFileHandler(directory / name, encoding="utf-8", maxBytes=256_000, backupCount=1)
        if rotate else logging.FileHandler(directory / name, encoding="utf-8")
    )
    logging.basicConfig(level=logging.INFO, handlers=[handler],
                        format="%(asctime)s %(levelname)s %(message)s")
