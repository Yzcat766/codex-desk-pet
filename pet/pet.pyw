"""Codex Desk Pet's standalone Tkinter window."""
import logging
import math
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox
import winsound

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vendor"))
from pet import windows
from pet.config import ROOT, SPRITES, TEXT, configure_log, run_dir
from pet.session_manager import SessionManager, display_state
from pet.state_manager import file_lock, read_json, write_json

SIZES = {"小": 140, "中": 190, "大": 250}
KEY = "#ff00fe"
EDGE = (58, 36, 26)
ORANGE = "#e8743b"
SOUNDS = ROOT / "sounds"


def load_settings(path):
    try:
        settings = read_json(path)
        return settings if isinstance(settings, dict) else {}
    except (OSError, ValueError):
        return {}


def prepare(path, height):
    """Keep the upstream color-key mask and suppress magenta edge halos."""
    from PIL import Image

    with Image.open(path) as original:
        im = original.convert("RGBA")
    width = max(1, round(im.width * height / im.height))
    im = im.resize((width, height), Image.Resampling.LANCZOS)
    solid = Image.alpha_composite(Image.new("RGBA", im.size, EDGE + (255,)), im)
    key = Image.new("RGBA", im.size, KEY)
    mask = im.getchannel("A").point(lambda a: 255 if a >= 110 else 0)
    key.paste(solid, (0, 0), mask)
    return key.convert("RGB")


class Pet:
    def __init__(self, directory):
        self.settings_path = directory / "settings.json"
        self.settings = load_settings(self.settings_path)
        self.sessions = SessionManager(directory)
        self.windows = windows.CodexWindows()
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("Codex 桌宠")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-transparentcolor", KEY)
        self.root.configure(bg=KEY)
        self.scale = self.root.winfo_fpixels("1i") / 96

        self.canvas = tk.Canvas(self.root, bg=KEY, highlightthickness=0, bd=0)
        self.canvas.pack()
        self.state, self.text = "offline", TEXT["offline"]
        self.hop_at = time.monotonic()
        self.hidden = False
        self.focus_samples = 0
        self.drag = None
        self.dragged = False
        self.last_notes = {}
        self.first_poll = True
        self.bad_assets = set()
        self.image_cache = {}
        self.snapshot = None

        self.build()
        self.root.update_idletasks()
        self.hwnd = windows.top_level(self.root)
        self.place_window()
        self.root.deiconify()
        self.root.update_idletasks()
        self.place_window()
        self.make_menu()
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Double-Button-1>", self.on_double)
        self.canvas.bind("<Button-3>", self.popup)

        self.poll()
        self.animate()
        self.watch_window()

    def missing_asset(self, path):
        if path not in self.bad_assets:
            self.bad_assets.add(path)
            logging.warning("Asset missing or unreadable: %s", path.name)

    def load_images(self, height):
        from PIL import ImageTk

        if height not in self.image_cache:
            by_name = {}
            for name in dict.fromkeys(SPRITES.values()):
                path = ROOT / "pet" / "sprites" / (name + ".png")
                try:
                    by_name[name] = ImageTk.PhotoImage(prepare(path, height))
                except (OSError, ValueError):
                    self.missing_asset(path)
            if not by_name:
                raise RuntimeError("No readable PNG files in pet/sprites")
            fallback = by_name.get("smile") or next(iter(by_name.values()))
            if len(self.image_cache) >= 6:
                self.image_cache.clear()
            self.image_cache[height] = {state: by_name.get(name, fallback) for state, name in SPRITES.items()}
        return self.image_cache[height]

    def build(self):
        scale = self.scale
        size = self.settings.get("size")
        height = round((SIZES.get(size, 190) if isinstance(size, str) else 190) * scale)
        self.images = self.load_images(height)
        self.font = ("Microsoft YaHei UI", -round(14 * scale))
        self.W = max(max(img.width() for img in self.images.values()), round(215 * scale))
        self.H = round(82 * scale) + height
        self.canvas.config(width=self.W, height=self.H)
        self.canvas.delete("all")
        self.sprite = self.canvas.create_image(self.W // 2, self.H, anchor="s", image=self.images[self.state])
        self.draw_bubble()

    def draw_bubble(self):
        c, s = self.canvas, self.scale
        c.delete("bubble")
        # Reserve room for the animated dots even before the first animation frame.
        text = self.text + "..." if self.state == "thinking" else self.text
        tid = c.create_text(self.W // 2, round(10 * s), text=text, font=self.font,
                            width=self.W - round(28 * s), anchor="n", justify="center",
                            fill="#3a2a1a", tags="bubble")
        x0, _, x1, y1 = c.bbox(tid)
        pad = round(10 * s)
        x0, x1 = max(2, x0 - pad), min(self.W - 3, x1 + pad)
        y0, y1 = 2, y1 + round(7 * s)
        r = min(round(10 * s), (x1 - x0) // 4)
        mid = self.W // 2
        points = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
                  x1 - r, y1, mid + round(8 * s), y1, mid, y1 + round(10 * s),
                  mid - round(4 * s), y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
        c.create_polygon(points, smooth=True, fill="#fffaf3", outline=ORANGE,
                         width=max(2, round(2 * s)), tags="bubble")
        c.tag_raise(tid)
        self.bubble_text = tid

    def save(self):
        try:
            write_json(self.settings_path, self.settings)
        except (OSError, ValueError) as exc:
            logging.warning("Settings could not be saved (%s)", type(exc).__name__)

    def place_window(self):
        x, y = self.settings.get("x"), self.settings.get("y")
        if type(x) is not int or type(y) is not int or max(abs(x), abs(y)) > 1_000_000:
            _, _, right, bottom = windows.work_area(0, 0, 1, 1)
            x, y = right - self.W - round(30 * self.scale), bottom - self.H - round(25 * self.scale)
        windows.move(self.hwnd, *windows.clamp_position(x, y, self.W, self.H))

    def make_menu(self):
        self.menu = tk.Menu(self.root, tearoff=0, font=self.font)
        size_menu = tk.Menu(self.menu, tearoff=0, font=self.font)
        self.size_var = tk.StringVar(value=self.settings.get("size", "中"))
        for name in SIZES:
            size_menu.add_radiobutton(label=name + "尺寸", variable=self.size_var,
                                      value=name, command=self.resize)
        self.menu.add_cascade(label="大小", menu=size_menu)
        self.hide_var = tk.BooleanVar(value=bool(self.settings.get("hideOnFocus", False)))
        self.menu.add_checkbutton(label="Codex 在前台时隐藏", variable=self.hide_var,
                                  command=lambda: self.toggle("hideOnFocus", self.hide_var))
        self.sound_var = tk.BooleanVar(value=bool(self.settings.get("sound", True)))
        self.menu.add_checkbutton(label="提示音", variable=self.sound_var,
                                  command=lambda: self.toggle("sound", self.sound_var))
        self.session_menu = tk.Menu(self.menu, tearoff=0, font=self.font)
        pinned = self.settings.get("session")
        self.session_var = tk.StringVar(value=pinned if isinstance(pinned, str) else "")
        self.menu.add_cascade(label="关注会话", menu=self.session_menu)
        self.menu.add_command(label="查看当前状态", command=self.show_status)
        self.menu.add_command(label="打开 Codex", command=lambda: self.open_codex(launch=True))
        self.update_startup_menu()
        self.menu.add_separator()
        self.menu.add_command(label="退出桌宠", command=self.root.destroy)

    def update_startup_menu(self):
        manifest = load_settings(self.settings_path.with_name("installation.json"))
        if manifest.get("owner") != "codex-desk-pet":
            return
        commands = manifest.get("startup_commands")
        if not isinstance(commands, list) or not commands:
            return
        try:
            current = windows.autostart_value()
        except OSError:
            logging.warning("Could not read the Windows startup entry")
            return
        if current is not None and current not in commands:
            return
        self.startup_var = tk.BooleanVar(value=current is not None)
        self.menu.add_checkbutton(label="登录 Windows 时启动", variable=self.startup_var,
                                  command=lambda: self.toggle_startup(commands))

    def toggle_startup(self, commands):
        enabled = self.startup_var.get()
        try:
            windows.set_autostart(commands[-1] if enabled else None, commands)
        except (OSError, ValueError):
            self.startup_var.set(not enabled)
            messagebox.showerror("Codex 桌宠", "无法保存开机启动设置。", parent=self.root)
            return
        self.settings["autostart"] = enabled
        self.save()

    def popup(self, event):
        menu = self.session_menu
        menu.delete(0, "end")
        menu.add_radiobutton(label="自动选择最近活动", variable=self.session_var, value="",
                             command=self.pin_session)
        snapshots = sorted(self.sessions.snapshots.values(), key=lambda data: data["event_ns"], reverse=True)
        for data in snapshots:
            sid = data["session_id"]
            project = str(data.get("project", "Codex"))[:30]
            menu.add_radiobutton(label=f"{project} · {sid[-8:]}", variable=self.session_var,
                                 value=sid, command=self.pin_session)
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def pin_session(self):
        self.settings["session"] = self.session_var.get()
        self.save()

    def show_status(self):
        state, label = self.state, self.text
        rows = [f"{label}  ({state})"]
        if self.snapshot:
            rows.insert(0, str(self.snapshot.get("project", "Codex")))
            start = self.snapshot.get("turn_started_ns")
            duration = (max(0, (time.time_ns() - start) // 1_000_000)
                        if type(start) is int else self.snapshot.get("duration_ms"))
            if type(duration) is int:
                rows.append(f"任务用时 {duration / 1000:.1f} 秒")
            for label, usage in (("本会话", self.snapshot.get("usage")),
                                 ("本项目（近期会话）", self.sessions.project_usage(self.snapshot.get("cwd")))):
                if isinstance(usage, dict):
                    counts = [f"{title} {usage[key]:,}" for key, title in (
                        ("input_tokens", "输入"), ("output_tokens", "输出"),
                        ("cached_input_tokens", "缓存输入")) if type(usage.get(key)) is int]
                    if counts:
                        rows.append(f"{label} Token · " + " · ".join(counts))
        messagebox.showinfo("Codex 桌宠", "\n".join(rows), parent=self.root)

    def toggle(self, key, var):
        self.settings[key] = var.get()
        self.save()
        if key == "sound" and not var.get():
            winsound.PlaySound(None, 0)

    def resize(self):
        self.settings["size"] = self.size_var.get()
        self.rebuild()
        self.remember_position()

    def rebuild(self):
        x, y = windows.window_position(self.hwnd)
        bottom_x, bottom_y = x + self.W // 2, y + self.H
        self.build()
        self.root.update_idletasks()
        windows.move(self.hwnd, *windows.clamp_position(
            bottom_x - self.W // 2, bottom_y - self.H, self.W, self.H))

    def remember_position(self):
        self.settings["x"], self.settings["y"] = windows.window_position(self.hwnd)
        self.save()

    def on_press(self, event):
        x, y = windows.window_position(self.hwnd)
        self.drag = event.x_root - x, event.y_root - y
        self.dragged = False

    def on_drag(self, event):
        if self.drag:
            windows.move(self.hwnd, event.x_root - self.drag[0], event.y_root - self.drag[1])
            self.dragged = True

    def on_release(self, _):
        self.drag = None
        if self.dragged:
            x, y = windows.window_position(self.hwnd)
            windows.move(self.hwnd, *windows.clamp_position(x, y, self.W, self.H))
            self.remember_position()
        self.dragged = False

    def on_double(self, _):
        self.drag = None
        self.open_codex()

    def open_codex(self, launch=False):
        result = self.windows.activate()
        logging.info("Codex window activation: %s", result)
        if result == "active":
            return
        if result == "missing" and launch:
            try:
                self.windows.launch()
                return
            except OSError:
                pass
        if launch:
            info = {"ambiguous": "检测到多个 Codex 窗口，请先切到你想关注的窗口。",
                    "denied": "Windows 没有允许桌宠切换前台窗口。",
                    "missing": "未找到可打开的 Codex 桌面应用。"}
            messagebox.showinfo("打开 Codex", info[result], parent=self.root)

    def play(self, name):
        path = SOUNDS / (name + ".wav")
        if not path.is_file():
            self.missing_asset(path)
            return
        try:
            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        except (OSError, RuntimeError):
            self.missing_asset(path)

    def poll(self):
        self.sessions.refresh()
        self.snapshot = self.sessions.select(self.session_var.get())
        notes = {}
        for sid, data in self.sessions.snapshots.items():
            note = data.get("notification")
            if not isinstance(note, dict) or type(note.get("event_ns")) is not int:
                continue
            stamp = note["event_ns"]
            notes[sid] = stamp
            if (not self.first_poll and data is self.snapshot and stamp > self.last_notes.get(sid, 0)
                    and 0 <= time.time_ns() - stamp < 5_000_000_000 and self.sound_var.get()
                    and note.get("sound") in ("done", "error")):
                self.play(note["sound"])
        self.last_notes = notes
        self.first_poll = False
        state, text = display_state(self.snapshot, time.time())
        if (state, text) != (self.state, self.text):
            if state != self.state:
                self.hop_at = time.monotonic()
                self.canvas.itemconfig(self.sprite, image=self.images[state])
            self.state, self.text = state, text
            self.draw_bubble()
        self.root.after(300, self.poll)

    def animate(self):
        t, state, scale = time.monotonic(), self.state, self.scale
        if not self.hidden:
            if state == "stone":
                bob = 0
            elif state == "tool":
                bob = 2.5 * math.sin(t * 9)
            elif state == "error":
                bob = 2 * math.sin(t * 25)
            else:
                bob = 3 * math.sin(t * 2.2)
            since = t - self.hop_at
            hop = -14 * math.sin(since / 0.28 * math.pi) if since < 0.28 else 0
            self.canvas.coords(self.sprite, self.W // 2, self.H + round((bob + hop - 4) * scale))
            if state == "thinking":
                text = self.text + "." * (int(t * 2.5) % 3 + 1)
                if self.canvas.itemcget(self.bubble_text, "text") != text:
                    self.canvas.itemconfig(self.bubble_text, text=text)
        self.root.after(250 if self.hidden else (65 if state in ("idle", "waiting", "offline") else 33), self.animate)

    def watch_window(self):
        is_codex = self.windows.in_foreground()
        should_hide = self.hide_var.get() and is_codex and self.drag is None
        self.focus_samples = self.focus_samples + 1 if should_hide else 0
        if self.focus_samples >= 2 and not self.hidden:
            self.root.withdraw()
            self.hidden = True
        elif not should_hide and self.hidden:
            self.root.deiconify()
            self.root.attributes("-topmost", True)
            self.hidden = False
        if self.drag is None and not self.hidden:
            scale = windows.window_scale(self.hwnd)
            if abs(scale - self.scale) > 0.05:
                self.scale = scale
                self.rebuild()
            x, y = windows.window_position(self.hwnd)
            clamped = windows.clamp_position(x, y, self.W, self.H)
            if clamped != (x, y):
                windows.move(self.hwnd, *clamped)
        self.root.after(350, self.watch_window)


def main(directory):
    configure_log(directory, "pet.log")
    try:
        with file_lock(directory / ".instance.lock", timeout=0):
            windows.set_dpi_awareness()
            try:
                from PIL import ImageTk  # noqa: F401
            except ImportError:
                logging.error("Pillow is missing. Run install.ps1 or install requirements.txt.")
                messagebox.showerror("Codex 桌宠", "缺少 Pillow，请运行 install.ps1 安装依赖。")
                return 1
            pet = Pet(directory)
            logging.info("Desktop pet ready")
            pet.root.mainloop()
    except TimeoutError:
        logging.info("Desktop pet is already running")
    except Exception:
        logging.exception("Desktop pet startup/runtime failed")
        return 1
    return 0


if __name__ == "__main__":
    directory = Path(sys.argv[1]) if len(sys.argv) > 1 else run_dir()
    sys.exit(main(directory))
