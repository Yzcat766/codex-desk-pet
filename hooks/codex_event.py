"""One Codex command-hook invocation. No GUI, credential access or raw-event logging."""
import json
import logging
import sys
import time
from pathlib import Path

# Capture order at process ingress, before imports and payload parsing.
RECEIVED_NS = time.time_ns()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hooks.codex_adapter import adapt
from pet.config import configure_log, run_dir
from pet.session_manager import SessionManager


def main():
    directory = Path(sys.argv[1]) if len(sys.argv) > 1 else run_dir()
    event = None
    try:
        raw = sys.stdin.buffer.read(16 * 1024 * 1024 + 1)
        if len(raw) > 16 * 1024 * 1024:
            raise ValueError("Hook payload exceeds limit")
        event = adapt(json.loads(raw), RECEIVED_NS)
        if event:
            SessionManager(directory).record(event)
    except Exception as exc:
        try:
            # Separate processes can append diagnostics independently. Keep details allow-listed.
            configure_log(directory, "hook-error.log", rotate=False)
            logging.warning("%s: %s (errno=%s)", event.name if event else "stdin",
                            type(exc).__name__, getattr(exc, "errno", None))
        except Exception:
            pass
    # An empty JSON object is an advisory no-op, including for Stop.
    print("{}")


if __name__ == "__main__":
    main()
