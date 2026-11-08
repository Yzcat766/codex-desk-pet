"""Start the GUI in its own Windows process."""
import argparse
import logging
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pet.config import configure_log, run_dir


def launch(directory):
    interpreter = Path(sys.executable).with_name("pythonw.exe")
    if not interpreter.is_file():
        interpreter = Path(sys.executable)
    script = Path(__file__).with_name("pet.pyw")
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    with (directory / "launch.log").open("a", encoding="utf-8") as log:
        for creation_flags in (flags | subprocess.CREATE_BREAKAWAY_FROM_JOB, flags):
            try:
                process = subprocess.Popen([str(interpreter), str(script), str(directory)],
                                           stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                           creationflags=creation_flags, close_fds=True)
                logging.info("Desktop pet process started (pid=%s)", process.pid)
                return
            except OSError as exc:
                last_error = exc
        raise last_error


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data_dir", nargs="?", type=Path, default=run_dir())
    args = parser.parse_args()
    configure_log(args.data_dir, "pet-launch.log")
    launch(args.data_dir)
