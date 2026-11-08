"""Run Tk/Win32 assertions against a visible Windows desktop in an isolated data directory."""
import ctypes as ct
import importlib.machinery
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from ctypes import wintypes as wt
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pet import windows
from pet.config import ROOT, SPRITES

loader = importlib.machinery.SourceFileLoader("desk_pet_gui", str(ROOT / "pet" / "pet.pyw"))
spec = importlib.util.spec_from_loader(loader.name, loader)
gui = importlib.util.module_from_spec(spec)
loader.exec_module(gui)


def main():
    windows.set_dpi_awareness()
    with tempfile.TemporaryDirectory(prefix=".pet-GUI 测试 ", dir=ROOT) as folder:
        data = Path(folder)
        pet = gui.Pet(data)
        try:
            pet.root.update()
            assert windows.visible(pet.hwnd)
            assert pet.root.attributes("-topmost")
            assert str(pet.root.attributes("-transparentcolor")) == gui.KEY
            assert pet.root.overrideredirect()
            flags = wt.DWORD()
            layered_attributes = windows.bind(windows.user, "GetLayeredWindowAttributes", wt.BOOL,
                                              wt.HWND, wt.LPVOID, wt.LPVOID, ct.POINTER(wt.DWORD))
            keyed = layered_attributes(pet.hwnd, None, None, ct.byref(flags))
            assert keyed and flags.value & 1, "Native window has no color key"
            print("PASS: visible, borderless, topmost and Win32 color key")
            assert pet.menu.index("end") >= 5
            initial = pet.H
            pet.size_var.set("大")
            pet.resize()
            assert pet.H > initial
            x, y = windows.window_position(pet.hwnd)
            pet.on_press(SimpleNamespace(x_root=x, y_root=y))
            pet.on_drag(SimpleNamespace(x_root=x - 50, y_root=y - 50))
            pet.on_release(None)
            assert windows.window_position(pet.hwnd) != (x, y)
            assert pet.settings_path.is_file()
            pet.text = "在读 中文文件名超过一行时换行显示示例.txt"
            pet.draw_bubble()
            x0, y0, x1, y1 = pet.canvas.bbox(pet.bubble_text)
            assert 0 <= x0 <= x1 <= pet.W and 0 <= y0 <= y1 <= pet.H
            print("PASS: menu, size, drag, persisted position and wrapped Chinese bubble")
            pet.sound_var.set(False)
            pet.toggle("sound", pet.sound_var)
            assert not pet.settings["sound"]
            pet.play("done")
            pet.play("error")
            pet.play("not-found")
            assert pet.bad_assets
            art = data / "pet" / "sprites"
            art.mkdir(parents=True)
            shutil.copyfile(ROOT / "pet" / "sprites" / "smile.png", art / "smile.png")
            with patch.object(gui, "ROOT", data):
                images = pet.load_images(111)
            assert set(images) == set(SPRITES)
            assert len({str(image) for image in images.values()}) == 1
            print("PASS: WAV calls and missing WAV/PNG fallbacks")
            pet.hide_var.set(True)
            with patch.object(pet.windows, "in_foreground", return_value=True):
                pet.watch_window()
                pet.watch_window()
                assert pet.hidden
            with patch.object(pet.windows, "in_foreground", return_value=False):
                pet.watch_window()
                assert not pet.hidden
            print("PASS: hide/show with controlled foreground detection")
            candidates = windows.codex_windows()
            print(f"{'PASS' if candidates else 'UNTESTED'}: live MSIX window enumeration ({len(candidates)})")
        finally:
            pet.root.destroy()

    with tempfile.TemporaryDirectory(prefix=".pet-GUI lock ", dir=ROOT) as folder:
        directory = Path(folder)
        command = [sys.executable, str(ROOT / "pet" / "pet.pyw"), folder]
        flags = subprocess.CREATE_NO_WINDOW
        with subprocess.Popen(command, creationflags=flags) as first:
            try:
                log = directory / "pet.log"
                for _ in range(50):
                    if log.exists() and "Desktop pet ready" in log.read_text(encoding="utf-8"):
                        break
                    time.sleep(0.1)
                else:
                    raise AssertionError("Primary process did not start")
                subprocess.run(command, check=True, timeout=5, creationflags=flags)
                assert first.poll() is None
                assert "already running" in log.read_text(encoding="utf-8")
                print("PASS: second GUI process exited without replacing the first")
                owned = []

                @windows.EnumProc
                def visit(hwnd, _):
                    if (windows.window_pid(hwnd) == first.pid and windows.visible(hwnd)
                            and windows.title_length(hwnd)):
                        owned.append(hwnd)
                    return True

                windows.enum_windows(visit, 0)
                if len(owned) == 1:
                    close = windows.bind(windows.user, "PostMessageW", wt.BOOL, wt.HWND,
                                         wt.UINT, wt.WPARAM, wt.LPARAM)
                    assert close(owned[0], 0x0010, 0, 0)
                    assert first.wait(timeout=5) == 0
                    print("PASS: native close released the primary process")
                else:
                    print("UNTESTED: child window is on a different or ambiguous Win32 desktop")
            finally:
                if first.poll() is None:
                    first.terminate()


if __name__ == "__main__":
    if os.name != "nt":
        sys.exit("Windows only")
    main()
