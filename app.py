"""Desktop app for the harness: send one goal and watch the agent walk the UIA tree.

Every prompt starts fresh, and the app shows only the last message, last step, what that prompt
has completed and the current walk's trail of scores. Settings are kept in settings.json.
All UIA work runs on one worker thread, because UIA objects can't leave their COM apartment.
Tk stays on the main thread, so each side hands the other callables through a queue.

Setup: pip install pillow, which draws the score chips. Run without a console: pythonw app.py
"""
import ctypes
import json
import math
import os
import queue
import threading
import tkinter as tk
from _ctypes import COMError
from tkinter import ttk
from typing import NamedTuple

import uiautomation as uia
from PIL import Image, ImageDraw, ImageFont, ImageTk

import harness

SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")
POLL_MS = 30
LONG_MESSAGE, MAX_SHOWN = 80, 240
TRACKED_LINES = 5
EXTENDED_STYLE, NO_ACTIVATE = -20, 0x08000000  # Win32 GWL_EXSTYLE and WS_EX_NOACTIVATE.

BLUEPRINT, DEEP, BAND, TRAIL = "#14325C", "#0E2545", "#2A5490", "#1F4478"
CHALK, FADED, MARKER = "#EAF0F8", "#8FA6C4", "#FFD84D"
MESSAGE_FONT = ("Bahnschrift Light", 20)
STEP_FONT = ("Bahnschrift SemiBold", 13)
BODY_FONT = ("Bahnschrift", 11)
SMALL_FONT = ("Bahnschrift", 10)

NO_MESSAGE, NO_STEP = "Send a goal to start.", "No decision yet."


class Setting(NamedTuple):
    key: str  # Its name in settings.json.
    constant: str  # The harness constant it sets.
    label: str
    note: str
    low: float
    high: float
    step: float
    shown: str


SLIDERS = (
    Setting("confidence_floor", "MIN_CONFIDENCE", "Confidence floor", "The agent stops when it's less sure than this. At 0 it never stops for being unsure.", 0, 1, 0.05, "{:.2f}"),
    Setting("max_actions", "MAX_ACTIONS", "Action limit", "The most actions one prompt can take. At 1 it stops after its first action.", 1, 20, 1, "{:.0f}"),
    Setting("max_steps", "MAX_STEPS", "Step limit", "The most decisions one prompt can make, counting every level of the tree.", 10, 200, 10, "{:.0f}"),
    Setting("action_wait", "ACTION_WAIT", "Wait after an action", "Time for the screen to catch up before the agent looks again.", 0, 5, 0.25, "{:.2f} s"),
    Setting("preview_names", "PREVIEW_NAMES", "Names previewed", "How many names inside an item the model sees before opening it. More helps it choose but slows each decision.", 0, 40, 4, "{:.0f}"),
)
DEFAULTS = {setting.key: getattr(harness, setting.constant) for setting in SLIDERS} | {"model": harness.MODEL}


def ask_model(*args):
    return harness.ask_openjev(*args, harness.MODEL)


def key(control):
    """Tree row id. UIA wrappers differ per read, but the runtime id names the element."""
    return ".".join(map(str, control.GetRuntimeId()))


def quit_row(level):
    """Row id for a level's quit option, which has no UIA element of its own."""
    return f"{level}:quit"


def snap(setting, value):
    """A value on the setting's scale: in range, on a step, and a whole number if the steps are."""
    value = min(max(round(round(float(value) / setting.step) * setting.step, 2), setting.low), setting.high)
    return int(value) if setting.step >= 1 else value


def load_settings():
    """Apply the saved settings. Anything missing or unusable stays at its default."""
    try:
        with open(SETTINGS_FILE) as file:
            saved = json.load(file)
        for setting in SLIDERS:
            if setting.key in saved:
                setattr(harness, setting.constant, snap(setting, saved[setting.key]))
        if saved.get("model"):
            harness.MODEL = str(saved["model"])
    except (OSError, ValueError, TypeError, AttributeError):
        pass


def save_settings():
    with open(SETTINGS_FILE, "w") as file:
        json.dump({setting.key: getattr(harness, setting.constant) for setting in SLIDERS} | {"model": harness.MODEL}, file)


def percent(name, scores):
    """An option's score. Past the model's top 20 there's none, only the lowest score it did report as a ceiling."""
    value = scores.get(name, min(scores.values()))
    if value < 0.01:
        return "<1%"
    return f"{value:.0%}" if name in scores else f"up to {math.ceil(value * 100)}%"


class App(tk.Tk):
    def __init__(self, get_root=uia.GetRootControl, ask=ask_model):
        super().__init__()
        self.get_root, self.ask = get_root, ask
        self.jobs, self.updates, self.answers = queue.Queue(), queue.Queue(), queue.Queue()
        self.cancel = threading.Event()
        self.controls = {}  # Row id -> control. Worker thread only.
        self.stop_reason, self.done = None, []  # Worker thread only.
        self.running, self.here, self.prompt, self.settings = False, None, None, None
        self.chips = {}  # Row id -> (chips, image). Tk keeps no reference to the image itself.
        self.setting_values = {}  # Setting key -> (slider value, text shown). Only while Settings is open.
        load_settings()
        self.px = self.winfo_fpixels("1i") / 96
        self.chip_font = ImageFont.truetype("bahnschrift.ttf", round(self.winfo_fpixels("10p")))
        self.picked_font = ImageFont.truetype("bahnschrift.ttf", round(self.winfo_fpixels("10p")))
        self.picked_font.set_variation_by_name("SemiBold")
        self.build()
        self.protocol("WM_DELETE_WINDOW", self.close)
        threading.Thread(target=self.worker, daemon=True).start()
        self.jobs.put(self.load_root)
        self.poll()

    # Tk side.

    def build(self):
        self.title("System One")
        # Placed explicitly: Windows' default spot assumes a small window and can push the Send box off-screen.
        screen_width, screen_height = self.winfo_screenwidth(), self.winfo_screenheight()
        width, height = round(920 * self.px), min(round(780 * self.px), screen_height - round(120 * self.px))
        self.geometry(f"{width}x{height}+{(screen_width - width) // 2}+{(screen_height - height) // 3}")
        self.minsize(round(560 * self.px), round(480 * self.px))
        self.configure(background=BLUEPRINT)
        self.style()
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        gutter = round(20 * self.px)

        header = ttk.Frame(self)
        header.grid(row=0, sticky="ew", padx=gutter, pady=gutter)
        header.columnconfigure(0, weight=1)
        self.shown = tk.StringVar(value=NO_MESSAGE)
        self.message = ttk.Label(header, textvariable=self.shown, style="Message.TLabel", justify="left")
        self.message.grid(row=0, column=0, sticky="ew")
        self.message.bind("<Configure>", lambda e: self.message.configure(wraplength=e.width))
        ttk.Button(header, text="Settings", style="Quiet.TButton", command=self.open_settings).grid(row=0, column=1, sticky="ne", padx=(gutter, 0))

        band = ttk.Frame(self, style="Band.TFrame", padding=(gutter, round(12 * self.px)))
        band.grid(row=1, sticky="ew")
        band.columnconfigure(0, weight=1)
        self.step, self.confidence, self.status = tk.StringVar(value=NO_STEP), tk.StringVar(), tk.StringVar()
        step = ttk.Label(band, textvariable=self.step, style="Step.TLabel", justify="left")
        step.grid(row=0, column=0, sticky="ew")
        step.bind("<Configure>", lambda e: step.configure(wraplength=e.width))
        ttk.Label(band, textvariable=self.confidence, style="Band.TLabel").grid(row=0, column=1, sticky="ne")
        ttk.Label(band, textvariable=self.status, style="Band.TLabel").grid(row=1, column=0, columnspan=2, sticky="w")
        self.tracker = tk.StringVar()
        self.tracker_label = ttk.Label(band, textvariable=self.tracker, style="Band.TLabel", justify="left")
        self.tracker_label.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(round(6 * self.px), 0))
        self.tracker_label.bind("<Configure>", lambda e: self.tracker_label.configure(wraplength=e.width))
        self.tracker_label.grid_remove()

        well = ttk.Frame(self)
        well.grid(row=2, sticky="nsew")
        well.columnconfigure(0, weight=1)
        well.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(well, show="tree", selectmode="none")
        self.tree.column("#0", minwidth=round(1400 * self.px), stretch=True)
        self.tree.tag_configure("here", background=MARKER, foreground=BLUEPRINT, font=("Bahnschrift SemiBold", 11))
        self.tree.tag_configure("trail", background=TRAIL)
        self.tree.tag_configure("placeholder", foreground=FADED)
        self.tree.tag_configure("offscreen", foreground=FADED)
        self.tree.bind("<<TreeviewOpen>>", self.on_open)
        down = ttk.Scrollbar(well, orient="vertical", command=self.tree.yview)
        across = ttk.Scrollbar(well, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=down.set, xscrollcommand=across.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        down.grid(row=0, column=1, sticky="ns")
        across.grid(row=1, column=0, sticky="ew")

        composer = ttk.Frame(self, padding=gutter)
        composer.grid(row=3, sticky="ew")
        composer.columnconfigure(1, weight=1)
        self.draft, self.text = tk.StringVar(), tk.StringVar()
        self.entry = ttk.Entry(composer, textvariable=self.draft, font=BODY_FONT)
        self.entry.grid(row=0, column=0, columnspan=2, sticky="ew")
        self.entry.bind("<Return>", self.send)
        gap = round(8 * self.px)
        self.send_button = ttk.Button(composer, text="Send", command=self.send)
        self.send_button.grid(row=0, column=2, padx=(gap, 0))
        self.cancel_button = ttk.Button(composer, text="Cancel", style="Quiet.TButton", command=self.stop)
        self.cancel_button.grid(row=0, column=3, padx=(gap, 0))

        ttk.Label(composer, text="Text to type (optional)", style="Quiet.TLabel").grid(row=1, column=0, pady=(gap, 0))
        typed = ttk.Entry(composer, textvariable=self.text, font=BODY_FONT)
        typed.grid(row=1, column=1, sticky="ew", padx=(gap, 0), pady=(gap, 0))
        self.ask_first = tk.BooleanVar(value=True)
        check = ttk.Checkbutton(composer, text="Ask permission", variable=self.ask_first)
        check.grid(row=1, column=2, padx=(gap, 0), pady=(gap, 0), sticky="w")
        self.refresh_button = ttk.Button(composer, text="Refresh", style="Quiet.TButton", command=self.refresh)
        self.refresh_button.grid(row=1, column=3, padx=(gap, 0), pady=(gap, 0), sticky="ew")

        self.bind("<Escape>", self.on_escape)
        self.set_running(False)
        self.status.set("Ready.")
        self.entry.focus()
        self.color_title_bar(self)

    def style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(
            ".", background=BLUEPRINT, foreground=CHALK, font=BODY_FONT, bordercolor=BLUEPRINT,
            lightcolor=BLUEPRINT, darkcolor=BLUEPRINT, troughcolor=DEEP, arrowcolor=CHALK, focuscolor=MARKER,
        )
        style.configure("Message.TLabel", font=MESSAGE_FONT)
        style.configure("Long.TLabel", font=STEP_FONT)
        style.configure("Question.TLabel", font=STEP_FONT)
        style.configure("Quiet.TLabel", font=SMALL_FONT, foreground=FADED)
        style.configure("Band.TFrame", background=BAND)
        style.configure("Band.TLabel", background=BAND, font=SMALL_FONT)
        style.configure("Step.TLabel", background=BAND, font=STEP_FONT)
        style.configure("Treeview", background=DEEP, fieldbackground=DEEP, rowheight=round(26 * self.px), indent=round(16 * self.px))
        style.configure("Treeview.Item", padding=(round(12 * self.px), 0, 0, 0))
        style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        style.configure("TEntry", fieldbackground=DEEP, foreground=CHALK, insertcolor=CHALK, padding=round(7 * self.px), bordercolor=BAND)
        style.map("TEntry", bordercolor=[("focus", CHALK)], lightcolor=[("focus", CHALK)], darkcolor=[("focus", CHALK)])
        style.configure("TButton", background=CHALK, foreground=BLUEPRINT, padding=(round(16 * self.px), round(6 * self.px)), focusthickness=1)
        style.map("TButton", background=[("disabled", BLUEPRINT), ("active", "#FFFFFF")], foreground=[("disabled", FADED)], bordercolor=[("disabled", BAND)])
        style.configure("Quiet.TButton", background=BAND, foreground=CHALK)
        style.map("Quiet.TButton", background=[("disabled", BLUEPRINT), ("active", "#35639F")], foreground=[("disabled", FADED)], bordercolor=[("disabled", BAND)])
        style.configure(
            "TCheckbutton", font=SMALL_FONT, indicatorsize=round(14 * self.px), indicatorbackground=DEEP,
            indicatorforeground=CHALK, upperbordercolor=CHALK, lowerbordercolor=CHALK,
        )
        style.map("TCheckbutton", background=[("active", BLUEPRINT)])
        style.configure("TScrollbar", background=BAND, bordercolor=DEEP, lightcolor=BAND, darkcolor=BAND)
        style.map("TScrollbar", background=[("active", "#35639F"), ("disabled", DEEP)])
        style.configure("Horizontal.TScale", background=CHALK, troughcolor=DEEP, bordercolor=BAND, lightcolor=CHALK, darkcolor=CHALK)
        style.map("Horizontal.TScale", background=[("active", "#FFFFFF")])
        style.configure("TCombobox", background=BAND, bordercolor=BAND, arrowcolor=CHALK, padding=round(5 * self.px))
        style.map(
            "TCombobox", fieldbackground=[("readonly", DEEP)], foreground=[("readonly", CHALK)],
            selectbackground=[("readonly", DEEP)], selectforeground=[("readonly", CHALK)], bordercolor=[("focus", CHALK)],
        )
        self.option_add("*TCombobox*Listbox.background", DEEP)
        self.option_add("*TCombobox*Listbox.foreground", CHALK)
        self.option_add("*TCombobox*Listbox.selectBackground", BAND)
        self.option_add("*TCombobox*Listbox.selectForeground", CHALK)
        self.option_add("*TCombobox*Listbox.font", BODY_FONT)

    def color_title_bar(self, window):
        window.update_idletasks()
        handle = ctypes.windll.user32.GetParent(window.winfo_id())
        color = ctypes.c_int(int(BLUEPRINT[5:7] + BLUEPRINT[3:5] + BLUEPRINT[1:3], 16))  # COLORREF is BGR.
        ctypes.windll.dwmapi.DwmSetWindowAttribute(handle, 35, ctypes.byref(color), ctypes.sizeof(color))

    def poll(self):
        try:
            while True:
                self.updates.get_nowait()()
        except queue.Empty:
            pass
        finally:
            self.after(POLL_MS, self.poll)

    def set_running(self, running):
        self.running = running
        self.send_button.configure(state="disabled" if running else "normal")
        self.refresh_button.configure(state="disabled" if running else "normal")
        self.cancel_button.configure(state="normal" if running else "disabled")

    def send(self, _event=None):
        goal = self.draft.get().strip()
        if self.running or not goal:
            return
        text, ask_first = self.text.get() or None, self.ask_first.get()
        self.draft.set("")
        self.text.set("")
        self.shown.set(goal if len(goal) <= MAX_SHOWN else goal[:MAX_SHOWN - 1] + "…")
        self.message.configure(style="Long.TLabel" if len(goal) > LONG_MESSAGE else "Message.TLabel")  # Leaves room for the tree.
        self.step.set(NO_STEP)
        self.confidence.set("")
        self.status.set("Working.")
        self.show_done([])
        self.clear_tree()
        self.cancel.clear()
        self.set_running(True)
        self.jobs.put(lambda: self.walk(goal, text, ask_first))

    def clear_tree(self):
        self.tree.delete(*self.tree.get_children())
        self.chips.clear()
        self.here = None

    def show_done(self, actions):
        """The tracker: what this prompt has completed, oldest first, as many as fit."""
        lines = [f"{number}. {action}" for number, action in enumerate(actions, 1)]
        if len(lines) > TRACKED_LINES:
            lines[:-TRACKED_LINES + 1] = [f"…and {len(lines) - TRACKED_LINES + 1} earlier"]
        self.tracker.set("\n".join(lines))
        self.tracker_label.grid() if lines else self.tracker_label.grid_remove()

    def stop(self):
        self.cancel.set()
        self.cancel_button.configure(state="disabled")
        if self.prompt:
            self.answer(False)
        self.status.set("Cancelling after this decision.")

    def refresh(self):
        self.refresh_button.configure(state="disabled")
        opened, queue = [], list(self.tree.get_children())
        while queue:
            iid = queue.pop()
            if self.tree.item(iid, "open"):
                opened.append(iid)
                queue += self.tree.get_children(iid)
        self.jobs.put(lambda: self.reload(opened))

    def close(self):
        self.cancel.set()
        self.destroy()

    def finish(self, status):
        self.status.set(status)
        self.set_running(False)

    def open_settings(self):
        if self.settings and self.settings.winfo_exists():
            self.settings.lift()
            return
        self.settings = tk.Toplevel(self)
        self.settings.title("Settings")
        self.settings.configure(background=BLUEPRINT)
        self.settings.resizable(False, False)
        frame = ttk.Frame(self.settings, padding=round(20 * self.px))
        frame.grid()
        frame.columnconfigure(0, weight=1)
        for row, setting in enumerate(SLIDERS):
            self.add_slider(frame, setting, 3 * row)
        below = 3 * len(SLIDERS)
        gap = round(4 * self.px)
        ttk.Label(frame, text="Model", style="Question.TLabel").grid(row=below, column=0, columnspan=2, sticky="w")
        self.model_choice = tk.StringVar(value=harness.MODEL)
        models = sorted({*harness.installed_models(), harness.MODEL})
        chooser = ttk.Combobox(frame, textvariable=self.model_choice, values=models, state="readonly", font=BODY_FONT)
        chooser.grid(row=below + 1, column=0, columnspan=2, sticky="ew", pady=gap)
        chooser.bind("<<ComboboxSelected>>", lambda _: self.pick_model(self.model_choice.get()))
        note = "Installed Ollama models. Only OpenJev Flash 9B has been tested."
        ttk.Label(frame, text=note, style="Quiet.TLabel").grid(row=below + 2, column=0, columnspan=2, sticky="w")
        reset = ttk.Button(frame, text="Reset to defaults", style="Quiet.TButton", command=self.reset_settings)
        reset.grid(row=below + 3, column=0, columnspan=2, sticky="e", pady=(round(16 * self.px), 0))
        self.settings.transient(self)  # Keeps it above the app, by the button that opened it.
        self.settings.update_idletasks()
        margin = round(20 * self.px)
        left = self.winfo_rootx() + self.winfo_width() - self.settings.winfo_reqwidth() - margin
        top = min(self.winfo_rooty() + 3 * margin, self.winfo_screenheight() - self.settings.winfo_reqheight() - 4 * margin)
        self.settings.geometry(f"+{left}+{max(top, 0)}")
        self.color_title_bar(self.settings)

    def add_slider(self, frame, setting, row):
        value = getattr(harness, setting.constant)
        number, shown = tk.DoubleVar(value=value), tk.StringVar(value=setting.shown.format(value))
        self.setting_values[setting.key] = number, shown
        ttk.Label(frame, text=setting.label, style="Question.TLabel").grid(row=row, column=0, sticky="w")
        ttk.Label(frame, textvariable=shown, style="Question.TLabel").grid(row=row, column=1, sticky="e")
        scale = ttk.Scale(
            frame, from_=setting.low, to=setting.high, length=round(380 * self.px), variable=number,
            command=lambda picked: self.pick(setting, picked),
        )
        scale.grid(row=row + 1, column=0, columnspan=2, sticky="ew", pady=round(4 * self.px))
        scale.bind("<Button-1>", lambda e: self.click_scale(scale, setting, e))
        scale.bind("<Left>", lambda _: self.pick(setting, number.get() - setting.step) or "break")
        scale.bind("<Right>", lambda _: self.pick(setting, number.get() + setting.step) or "break")
        ttk.Label(frame, text=setting.note, style="Quiet.TLabel", wraplength=round(380 * self.px), justify="left").grid(
            row=row + 2, column=0, columnspan=2, sticky="w", pady=(0, round(12 * self.px)),
        )

    def click_scale(self, scale, setting, event):
        """Jump to the click. By default a click beside the handle runs to the end of the scale."""
        if "slider" not in scale.identify(event.x, event.y):
            self.pick(setting, scale.get(event.x, event.y))
            return "break"

    def pick(self, setting, value):
        """Snap to a step, use it from the next decision, and remember it."""
        value = snap(setting, value)
        number, shown = self.setting_values[setting.key]
        number.set(value)
        shown.set(setting.shown.format(value))
        if value != getattr(harness, setting.constant):
            setattr(harness, setting.constant, value)
            save_settings()

    def pick_model(self, name):
        self.model_choice.set(name)
        if name != harness.MODEL:
            harness.MODEL = name
            save_settings()

    def reset_settings(self):
        for setting in SLIDERS:
            self.pick(setting, DEFAULTS[setting.key])
        self.pick_model(DEFAULTS["model"])

    def ask_user(self, question):
        """Pop up a small prompt above everything else, which can't take focus: that would close an open dropdown."""
        if self.cancel.is_set():
            self.answers.put(False)
            return
        self.status.set("Waiting for your answer.")
        self.prompt = tk.Toplevel(self)
        self.prompt.withdraw()
        self.prompt.overrideredirect(True)
        self.prompt.attributes("-topmost", True)
        self.prompt.configure(background=BLUEPRINT, highlightthickness=round(2 * self.px), highlightbackground=CHALK)
        frame = ttk.Frame(self.prompt, padding=round(20 * self.px))
        frame.grid()
        ttk.Label(frame, text=question, style="Question.TLabel", wraplength=round(380 * self.px), justify="left").grid(columnspan=2, sticky="w")
        gap = round(14 * self.px)
        ttk.Button(frame, text="Yes", command=lambda: self.answer(True)).grid(row=1, column=0, sticky="ew", pady=(gap, 0), padx=(0, gap // 2))
        ttk.Button(frame, text="No", style="Quiet.TButton", command=lambda: self.answer(False)).grid(row=1, column=1, sticky="ew", pady=(gap, 0))
        frame.columnconfigure((0, 1), weight=1)
        self.prompt.update_idletasks()
        # Over the app, or mid-screen if the app is minimized and has no useful position.
        hidden = self.state() == "iconic"
        left = 0 if hidden else self.winfo_rootx()
        top = 0 if hidden else self.winfo_rooty()
        across = self.winfo_screenwidth() if hidden else self.winfo_width()
        down = self.winfo_screenheight() if hidden else self.winfo_height()
        self.prompt.geometry(f"+{left + (across - self.prompt.winfo_reqwidth()) // 2}+{top + (down - self.prompt.winfo_reqheight()) // 2}")
        user32 = ctypes.windll.user32
        handle = user32.GetParent(self.prompt.winfo_id())
        user32.SetWindowLongW(handle, EXTENDED_STYLE, user32.GetWindowLongW(handle, EXTENDED_STYLE) | NO_ACTIVATE)
        self.prompt.deiconify()

    def answer(self, allowed):
        self.prompt.destroy()
        self.prompt = None
        self.status.set("Working.")
        self.answers.put(allowed)

    def on_escape(self, _event):
        if self.prompt:
            self.answer(False)

    def show_root(self, iid, text, rows):
        if not self.tree.exists(iid):
            self.tree.delete(*self.tree.get_children())
            self.tree.insert("", "end", iid, open=True)
        self.tree.item(iid, text=text)  # The name changes with the virtual desktop.
        self.fill(iid, rows)

    def fill(self, parent, rows):
        """Make a row's children match rows. Those still there keep their scores and expansion."""
        if not self.tree.exists(parent):
            return
        wanted = {row[0] for row in rows} | {quit_row(parent)}
        self.tree.delete(*[iid for iid in self.tree.get_children(parent) if iid not in wanted])
        for index, (iid, text, usable, inside) in enumerate(rows):
            if self.tree.exists(iid):
                self.tree.move(iid, parent, index)
            else:
                self.tree.insert(parent, index, iid)
            # The placeholder is what draws the arrow, so only rows with something inside get one.
            kids = self.tree.get_children(iid)
            if inside and not kids:
                self.tree.insert(iid, "end", text="Loading", tags=("placeholder",))
            elif kids and not inside and self.tree.tag_has("placeholder", kids[0]):
                self.tree.delete(kids[0])
            tags = set(self.tree.item(iid, "tags")) - {"offscreen"}
            if not usable:
                text += " (offscreen)"
                tags.add("offscreen")
            self.tree.item(iid, text=text, tags=[*tags])

    def arrive(self, iid, text, rows):
        """Move the marker to where the agent is. Rows it passed through stay tinted."""
        if not self.tree.exists(iid):
            self.tree.insert(self.here or "", "end", iid, text=text)
        self.fill(iid, rows)
        self.tree.item(iid, text=text, open=True)
        if self.here and self.tree.exists(self.here):
            self.tree.item(self.here, tags=("trail",))
        self.tree.item(iid, tags=("here",))
        self.here = iid
        self.tree.see(iid)

    def show_step(self, choice, confidence, score):
        self.step.set(choice)
        self.confidence.set(f"picked at {score}, confidence {confidence:.2f}")

    def chip_width(self, text, picked):
        font = self.picked_font if picked else self.chip_font
        return round(font.getlength(text)) + round(14 * self.px)

    def strip_width(self, chips):
        return sum(self.chip_width(*chip) + round(5 * self.px) for chip in chips)

    def draw_chips(self, chips, width):
        """Draw a row's scores side by side, with the pick highlighted. Every chip is opaque, so it reads on any row."""
        height = round(19 * self.px)
        strip = Image.new("RGBA", (width, height))
        draw = ImageDraw.Draw(strip)
        left = 0
        for text, picked in chips:
            right = left + self.chip_width(text, picked)
            draw.rectangle((left, 0, right - 1, height - 1), fill=MARKER if picked else DEEP, outline=BLUEPRINT)
            font = self.picked_font if picked else self.chip_font
            draw.text(((left + right) / 2, height / 2), text, fill=BLUEPRINT if picked else CHALK, font=font, anchor="mm")
            left = right + round(5 * self.px)
        return ImageTk.PhotoImage(strip, master=self)

    def show_scores(self, level, scored):
        """Leave the model's scores beside this level's rows, in one column so they read as a list."""
        if not self.tree.exists(level):
            return
        if None in scored and not self.tree.exists(quit_row(level)):
            self.tree.insert(level, "end", quit_row(level), text="Do nothing more and stop")
        scored[quit_row(level)] = scored.pop(None, [])
        rows = {iid: scored.get(iid, []) for iid in self.tree.get_children(level)}
        width = max(1, *map(self.strip_width, rows.values()))
        for iid, chips in rows.items():
            self.chips[iid] = chips, self.draw_chips(chips, width)
            self.tree.item(iid, image=self.chips[iid][1])
            if any(picked for _, picked in chips):
                self.tree.see(iid)

    def on_open(self, _event):
        iid = self.tree.focus()
        rows = self.tree.get_children(iid)
        if rows and self.tree.tag_has("placeholder", rows[0]):
            self.jobs.put(lambda: self.expand(iid))

    # Worker side.

    def worker(self):
        with uia.UIAutomationInitializerInThread():
            while True:
                job = self.jobs.get()
                try:
                    job()
                except Exception as error:  # A failed job must never leave the app stuck on Working.
                    self.updates.put(lambda status=f"Error: {error}": self.finish(status))

    def rows(self, control):
        """Everything UIA lists, not just what the agent can use, as (row id, label, usable, has children).

        The controls are remembered so their rows can expand. Labels say why an empty-looking row is empty.
        """
        found = []
        for child, usable in harness.flagged(control):
            try:
                iid, text, inside = key(child), harness.label(child), harness.has_children(child)
                if harness.is_collapsed(child):
                    text += " (closed)"
                elif not inside and harness.is_minimized(child):
                    text += " (minimized)"
                found.append((iid, text, usable, inside))
                self.controls[iid] = child
            except COMError:  # The element vanished after it was listed.
                pass
        return found

    def load_root(self):
        root = self.get_root()
        row = key(root), harness.label(root), self.rows(root)
        self.updates.put(lambda: self.show_root(*row))

    def expand(self, iid):
        """Load a row the user opened. It had children when read, so an empty list means the tree is out of date."""
        if control := self.controls.get(iid):
            rows = self.rows(control) or [(f"{iid}:empty", "Nothing here any more. Press Refresh.", True, False)]
            self.updates.put(lambda: self.fill(iid, rows))

    def reload(self, opened):
        """Re-read the root and every open row, so everything showing matches the desktop."""
        self.load_root()
        for iid in opened:
            if control := self.controls.get(iid):
                rows = self.rows(control)
                self.updates.put(lambda iid=iid, rows=rows: self.fill(iid, rows))
        self.updates.put(lambda: self.refresh_button.configure(state="disabled" if self.running else "normal"))

    def permit(self, verb, control, text):
        """Hold the walk until the user answers the prompt."""
        question = harness.request(verb, control, text)
        self.updates.put(lambda: self.ask_user(question))
        return self.answers.get()

    def walk(self, goal, text, ask_first):
        self.controls.clear()
        self.stop_reason, self.done = None, []
        permit = self.permit if ask_first else lambda *_: True
        ending = harness.run(goal, self.get_root(), self.ask, text, permit, self.on_event, self.cancel.is_set)
        status = self.summary(ending)
        self.updates.put(lambda: self.finish(status))

    def summary(self, ending):
        """How the prompt ended, never claiming nothing happened once something has."""
        count = len(self.done)
        actions = f"{count} action{'' if count == 1 else 's'}"
        if ending == "cancelled":
            return f"Cancelled after {actions}." if count else "Cancelled. Nothing was done."
        if ending == "denied":
            return f"You said no, after {actions}." if count else "You said no. Nothing was done."
        if self.stop_reason:
            return f"Stopped after {actions}: {self.stop_reason}." if count else f"Stopped: {self.stop_reason}."
        return f"The model quit after {actions}." if count else "Stopped: the model chose to quit."

    def scored(self, event):
        """This level's options by row, as (chip text, picked). Quit has no element, so its row is None."""
        scores = event.get("scores")
        found = {}
        for name, (verb, control, _) in event["options"].items():
            if not scores and name != event["choice"]:  # The harness took the only way, so nothing else was weighed.
                continue
            try:
                row = key(control) if control else None
            except COMError:  # The element vanished while the model decided.
                continue
            chip = (f"{verb} {percent(name, scores)}", name == event["choice"]) if scores else ("only path", False)
            found.setdefault(row, []).append(chip)
        return found

    def on_event(self, event):
        if event["kind"] == "at":
            row = key(event["node"]), harness.label(event["node"]), self.rows(event["node"])
            self.updates.put(lambda: self.arrive(*row))
        elif event["kind"] == "stop":
            self.stop_reason = event["reason"]
        elif event["kind"] == "done":
            self.done.append(event["action"])
            actions = list(self.done)
            self.updates.put(lambda: self.show_done(actions))
        elif event["kind"] == "restart":  # The tree shows the current walk. The tracker keeps the history.
            self.controls.clear()
            self.updates.put(self.clear_tree)
        else:
            level, scored = key(event["node"]), self.scored(event)
            self.updates.put(lambda: self.show_scores(level, scored))
            if not event["auto"]:  # Automatic descents aren't the model's decisions.
                score = percent(event["choice"], event["scores"])
                self.updates.put(lambda: self.show_step(event["choice"], event["confidence"], score))


if __name__ == "__main__":
    App().mainloop()
