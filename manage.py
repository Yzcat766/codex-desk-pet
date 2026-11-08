"""Install a per-user release and merge its exact hook entries into Codex config."""
import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from hooks.codex_adapter import EVENTS
from pet.config import ROOT, run_dir
from pet.state_manager import atomic_bytes, file_lock, read_json, write_json

MANIFEST = "installation.json"
RELEASE_NAME = re.compile(r"[0-9a-f]{20}")
FILES = (
    "hooks/__init__.py", "hooks/codex_event.py", "hooks/codex_adapter.py", "pet/__init__.py",
    "pet/config.py", "pet/launch.py", "pet/pet.pyw", "pet/windows.py", "pet/session_manager.py",
    "pet/state_manager.py", "requirements.txt", "LICENSE",
)


def hook_groups(interpreter, app, directory):
    def quote(path):
        return "'" + str(path).replace("'", "''") + "'"

    script = f"& {quote(interpreter)} -I -S {quote(app / 'hooks/codex_event.py')} {quote(directory)}"
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    powershell = system_root / "System32/WindowsPowerShell/v1.0/powershell.exe"
    command = subprocess.list2cmdline([str(powershell), "-NoLogo", "-NoProfile",
                                       "-NonInteractive", "-EncodedCommand", encoded])
    return {name: {"hooks": [{"type": "command", "command": command, "timeout": 3}]} for name in EVENTS}


def read_bytes(path):
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def merge_groups(original, old, new):
    doc = json.loads(original.decode("utf-8-sig")) if original is not None else {}
    if not isinstance(doc, dict) or not isinstance(doc.get("hooks", {}), dict):
        raise ValueError("hooks.json must contain a hooks object")
    hooks = doc.setdefault("hooks", {})
    for name in old.keys() | new.keys():
        groups = hooks.get(name, [])
        if not isinstance(groups, list):
            raise ValueError(f"hooks.json has an invalid {name} entry")
        replacement = new.get(name)
        result = []
        for group in groups:
            if name in old and group == old[name]:
                if replacement is not None:
                    result.append(replacement)
                    replacement = None
            else:
                result.append(group)
        if replacement is not None and replacement not in result:
            result.append(replacement)
        if result:
            hooks[name] = result
        elif name in old:
            hooks.pop(name, None)
    return doc


def release_path(directory, key):
    if not isinstance(key, str) or not RELEASE_NAME.fullmatch(key):
        raise ValueError("Invalid installation release ID")
    base = directory / "releases"
    release = base / key
    if release.resolve() != release or base.resolve() != base:
        raise ValueError("Release path must not be redirected")
    return release


def remove_release(directory, key):
    release = release_path(directory, key)
    if release.is_dir():
        shutil.rmtree(release)


def load_manifest(directory):
    path = directory / MANIFEST
    if not path.exists():
        return {}
    data = read_json(path)
    if not isinstance(data, dict) or data.get("owner") != "codex-desk-pet":
        raise ValueError("Installation manifest is invalid")
    release_path(directory, data.get("release"))
    return data


def references_command(group, command):
    handlers = group.get("hooks") if isinstance(group, dict) else None
    if not isinstance(handlers, list):
        return False
    return any(isinstance(item, dict) and isinstance(item.get("command"), str) and
               (item["command"] == command or item["command"].startswith(command + " "))
               for item in handlers)


def apply_hook_change(path, old, new, commit, *, created=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path.parent / ".codex-desk-pet-hooks.lock", timeout=1):
        original = read_bytes(path)
        doc = merge_groups(original, old, new)
        old_commands = {group["hooks"][0]["command"] for group in old.values()}
        for name, groups in doc["hooks"].items():
            if isinstance(groups, list) and any(
                group != new.get(name) and any(references_command(group, command) for command in old_commands)
                for group in groups
            ):
                raise ValueError(f"An installed {name} hook was edited. Remove that entry and retry.")
        current = json.loads(original.decode("utf-8-sig")) if original is not None else None
        changed = doc != current and (original is not None or bool(new))
        output = (json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        if changed:
            if read_bytes(path) != original:
                raise OSError("hooks.json changed during the operation; retry")
            if created and doc == {"hooks": {}}:
                path.unlink()
                output = None
            else:
                atomic_bytes(path, output)
        try:
            commit(original is None)
        except Exception:
            if changed and read_bytes(path) == output:
                if original is None:
                    path.unlink()
                else:
                    atomic_bytes(path, original)
            raise


def installed_files():
    assets = [path.relative_to(ROOT).as_posix() for folder, extension in (
        ("pet/sprites", "*.png"), ("sounds", "*.wav")) for path in (ROOT / folder).glob(extension)]
    return sorted((*FILES, *assets))


def install(directory, codex_dir):
    from pet.windows import autostart_command, autostart_value, set_autostart

    old = load_manifest(directory)
    hooks_path = codex_dir / "hooks.json"
    if old and Path(old["hooks_path"]) != hooks_path:
        raise ValueError("Uninstall from the previous Codex home before changing it")
    interpreter = Path(sys.executable).resolve()
    import tkinter

    if tkinter.TkVersion < 8.6:
        raise RuntimeError("Tk 8.6 or newer is required")
    files = installed_files()
    digest = hashlib.sha256(str(interpreter).encode("utf-8"))
    for filename in files:
        digest.update(filename.encode("utf-8"))
        digest.update((ROOT / filename).read_bytes())
    key = digest.hexdigest()[:20]
    release = release_path(directory, key)
    groups = hook_groups(interpreter, release, directory)
    previous_groups = old.get("groups", {})
    releases = list(dict.fromkeys([*old.get("releases", [old["release"]] if old else []), key]))
    for existing in releases:
        release_path(directory, existing)
    previous_startup = old.get("startup_commands", [])
    startup = autostart_command(interpreter, release, directory)
    startup_commands = [command for command in previous_startup if command != startup] + [startup]
    created_release = not release.is_dir()
    try:
        if created_release:
            release.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".staging-", dir=release.parent) as folder:
                staged = Path(folder)
                for filename in files:
                    target = staged / filename
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(ROOT / filename, target)
                try:
                    import PIL
                    import PIL.ImageTk  # noqa: F401
                    pillow_ok = 10 <= int(PIL.__version__.split(".")[0]) < 13
                except ImportError:
                    pillow_ok = False
                if not pillow_ok:
                    subprocess.run([str(interpreter), "-m", "pip", "install", "--target", str(staged / "vendor"),
                                    "-r", str(staged / "requirements.txt")], check=True)
                os.replace(staged, release)

        def commit(created_hooks):
            write_json(directory / MANIFEST, {
                "owner": "codex-desk-pet", "release": key, "python": str(interpreter),
                "hooks_path": str(hooks_path), "groups": groups,
                "created_hooks": old.get("created_hooks", False) or created_hooks,
                "releases": releases, "startup_commands": startup_commands,
            })

        apply_hook_change(hooks_path, previous_groups, groups, commit)
    except Exception:
        if created_release:
            # A separate hook editor could have modified the config while rollback was in progress.
            remaining = read_bytes(hooks_path) or b""
            command = next(iter(groups.values()))["hooks"][0]["command"]
            encoded = json.dumps(command, ensure_ascii=False).encode("utf-8")
            if encoded not in remaining:
                remove_release(directory, key)
        raise
    if old and old["release"] != key:
        # A running or already-open Codex client can retain the previous command until reload.
        print("Updated. Restart Codex to pick up the new hooks. Previous files are retained until uninstall.")
        try:
            if autostart_value() in previous_startup:
                set_autostart(startup, startup_commands)
        except (OSError, ValueError):
            print("Could not update the Windows startup entry. Toggle it in the pet menu after restarting.")
    return release, hooks_path


def uninstall(directory):
    from pet.windows import autostart_value, set_autostart

    manifest = load_manifest(directory)
    if not manifest:
        return False
    path = Path(manifest["hooks_path"])
    old = manifest["groups"]
    releases = manifest.get("releases", [manifest["release"]])
    for key in releases:
        release_path(directory, key)
    startup_commands = manifest.get("startup_commands", [])

    def commit(_):
        if not startup_commands:
            return
        try:
            current = autostart_value()
        except PermissionError:
            try:
                settings = read_json(directory / "settings.json")
            except (OSError, ValueError):
                settings = {}
            if isinstance(settings, dict) and settings.get("autostart"):
                raise
            return
        if current in startup_commands:
            set_autostart(None, startup_commands)

    apply_hook_change(path, old, {}, commit, created=manifest["created_hooks"])
    for key in releases:
        remove_release(directory, key)
    (directory / MANIFEST).unlink()
    return True


def start(directory, release, interpreter):
    subprocess.run([str(interpreter), str(release / "pet/launch.py"), str(directory)], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "uninstall", "start"))
    parser.add_argument("--data-dir", type=Path, default=run_dir())
    parser.add_argument("--codex-home", type=Path,
                        default=Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex"))
    parser.add_argument("--no-launch", action="store_true")
    args = parser.parse_args()
    if os.name != "nt" or sys.version_info < (3, 10):
        parser.error("Windows and Python 3.10+ are required")
    directory = args.data_dir.resolve()
    if args.action == "uninstall" and not (directory / MANIFEST).exists():
        print("Codex Desk Pet is not installed.")
        return
    directory.mkdir(parents=True, exist_ok=True)
    try:
        with file_lock(directory / ".installer.lock", timeout=0):
            if args.action == "install":
                release, hooks = install(directory, args.codex_home.resolve())
                print(f"Installed: {release}\nHooks: {hooks}")
                print("Restart Codex, then review Codex Desk Pet hooks in Settings > Hooks or /hooks.")
                if not args.no_launch:
                    start(directory, release, sys.executable)
            elif args.action == "start":
                manifest = load_manifest(directory)
                if manifest:
                    start(directory, release_path(directory, manifest["release"]), manifest["python"])
                else:
                    start(directory, ROOT, sys.executable)
            else:
                with file_lock(directory / ".instance.lock", timeout=0):
                    uninstall(directory)
                print(f"Uninstalled. Settings and local session data are kept at {directory}")
    except TimeoutError:
        sys.exit("Close the desk pet and retry after any other installer has finished.")
    except (OSError, ValueError, RuntimeError, ImportError, subprocess.CalledProcessError) as exc:
        sys.exit(f"{args.action} failed: {exc}")


if __name__ == "__main__":
    main()
