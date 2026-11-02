"""Self-check for the app on a fake tree, no model needed: python test_app.py"""
import ctypes
import json
import os
import tempfile
import threading
import time

import app
import harness


class Node:
    ControlTypeName, AutomationId, ProcessId, NativeWindowHandle = "PaneControl", "", 0, 0
    IsEnabled = True

    def __init__(self, name, *kids, acts=None, offscreen=False):
        self.Name, self.kids, self.acts, self.IsOffscreen = name, kids, acts or {}, offscreen

    def GetChildren(self):
        return self.kids

    def GetFirstChildControl(self):
        return self.kids[0] if self.kids else None

    def GetPattern(self, pattern):
        return None

    def GetRuntimeId(self):
        return (id(self),)


class Dropdown(Node):
    """Lists nothing until it's opened, like a real dropdown."""

    def __init__(self, name, *items):
        super().__init__(name, acts={"expand": self.Expand})
        self.items, self.ExpandCollapseState = items, harness.uia.ExpandCollapseState.Collapsed

    def GetPattern(self, pattern):
        return self if pattern == harness.uia.PatternId.ExpandCollapsePattern else None

    def Expand(self):
        self.ExpandCollapseState, self.kids = harness.uia.ExpandCollapseState.Expanded, self.items

    def Collapse(self):
        self.ExpandCollapseState, self.kids = harness.uia.ExpandCollapseState.Collapsed, ()


class Model:
    """Replays picks, holding each answer until the gate opens so tests can act mid-run."""

    def __init__(self):
        self.picks, self.calls, self.gate, self.done = [], 0, threading.Event(), []

    def __call__(self, goal, path, opts, text, done):
        self.calls += 1
        self.done = list(done)
        assert self.gate.wait(10)
        pick = self.picks.pop(0)
        if isinstance(pick, Exception):
            raise pick
        scores = {name: 0.05 for name in opts} | {pick[0]: pick[1]}
        return {"choice": pick[0], "confidence": pick[1], "scores": scores}


def pump(until):
    deadline = time.time() + 10
    while not until():
        assert time.time() < deadline, "timed out"
        window.update()
        time.sleep(0.005)


def send(goal, *picks, hold=False, allow=None):
    """Send a goal and, unless held, answer each prompt in turn and wait for the run to finish."""
    model.picks = list(picks)
    model.gate.clear() if hold else model.gate.set()
    window.draft.set(goal)
    window.send()
    if hold:
        return
    for answer in [allow] if isinstance(allow, bool) else allow or []:
        pump(lambda: window.prompt)
        window.answer(answer)
    pump(lambda: not window.running)


def enabled(button):
    return button.instate(["!disabled"])


def tagged(tag):
    return window.tree.tag_has(tag)


def chips(row):
    return window.chips[row][0]


def text(control):
    return window.tree.item(app.key(control), "text")


def question():
    return window.prompt.winfo_children()[0].winfo_children()[0].cget("text")


harness.actions = lambda control: dict(control.acts)
iconic = set()
harness.uia.IsIconic = lambda handle: handle in iconic
harness.uia.ShowWindow = lambda handle, command: iconic.discard(handle) if command == harness.uia.SW.ShowNoActivate else iconic.add(handle)
harness.RESTORE_SETTLE = harness.RESTORE_WAIT = harness.ACTION_WAIT = 0

clicks, typed = [], []
save = Node("Save", acts={"invoke": lambda: clicks.append("save")})
box = Node("Name", acts={"type": typed.append})
heading = Node("Heading")
panel = Node("App", save, box, heading)
start = Node("Start", acts={"invoke": print})
bar = Node("Taskbar", start)
tucked = Node("Hidden form", offscreen=True)
camera = Node("Camera", offscreen=True)
camera.NativeWindowHandle = 7
iconic.add(7)
root = Node("Desktop", panel, bar, tucked, camera)
OPEN, SAVE, TYPE = ('open Pane "App"', 0.9), ('invoke Pane "Save"', 0.8), ('type Pane "Name"', 0.8)
OPEN_CAMERA, QUIT = ('open Pane "Camera"', 0.9), ("quit", 0.9)
SAVED = 'Click Pane "Save"'

app.SETTINGS_FILE = os.path.join(tempfile.mkdtemp(), "settings.json")
model = Model()
window = app.App(get_root=lambda: root, ask=model)
window.withdraw()

# Startup shows the tree with nothing highlighted, only Send is on, and asking first is the default.
pump(lambda: window.tree.get_children())
assert window.tree.get_children(app.key(root)) and not tagged("here")
assert enabled(window.send_button) and not enabled(window.cancel_button) and window.ask_first.get()

# Only rows with something inside get an arrow.
assert window.tree.get_children(app.key(bar)) and window.tree.get_children(app.key(panel))
assert window.tree.get_children(app.key(tucked)) == () and window.tree.get_children(app.key(camera)) == ()

# The tree stays as read until something asks for a new read.
opened = Node("Calculator")
root.kids = (opened, *root.kids)
for _ in range(40):
    window.update()
    time.sleep(0.005)
assert not window.tree.exists(app.key(opened))

# Refresh picks it up.
window.refresh_button.invoke()
pump(lambda: window.tree.exists(app.key(opened)))
pump(lambda: enabled(window.refresh_button))
assert window.tree.index(app.key(opened)) == 0
root.kids = root.kids[1:]

# Minimized windows aren't dimmed, the label says why one looks empty, and other offscreen items are dimmed.
assert text(camera) == 'Pane "Camera" (minimized)' and "offscreen" not in window.tree.item(app.key(camera), "tags")
assert text(tucked).endswith("(offscreen)") and "offscreen" in window.tree.item(app.key(tucked), "tags")

# While running: the message shows, Send is locked out, the draft survives, the marker is at the root.
send("save it", OPEN, SAVE, hold=True)
pump(lambda: model.calls == 1 and tagged("here"))
assert window.shown.get() == "save it" and window.draft.get() == ""
assert not enabled(window.send_button) and enabled(window.cancel_button) and not enabled(window.refresh_button)
assert tagged("here") == (app.key(root),)
window.draft.set("too soon")
window.send()
assert window.shown.get() == "save it" and window.draft.get() == "too soon"
window.draft.set("")

# Reaching an action asks first, with the marker on the target and nothing done yet.
model.gate.set()
pump(lambda: window.prompt)
assert clicks == [] and window.status.get() == "Waiting for your answer." and window.running
assert tagged("here") == (app.key(save),) and not enabled(window.send_button)
assert question() == 'Click Pane "Save"?'

# The prompt can't take focus, because that would close an open dropdown.
pump(lambda: window.prompt.winfo_ismapped())
handle = ctypes.windll.user32.GetParent(window.prompt.winfo_id())
assert ctypes.windll.user32.GetWindowLongW(handle, app.EXTENDED_STYLE) & app.NO_ACTIVATE
assert window.prompt.overrideredirect()

# No stops the run and does nothing.
window.answer(False)
pump(lambda: not window.running)
assert clicks == [] and window.status.get() == "You said no. Nothing was done." and window.prompt is None
assert window.step.get() == SAVE[0] and window.confidence.get() == "picked at 80%, confidence 0.80"
assert tagged("here") == (app.key(save),) and set(tagged("trail")) == {app.key(root), app.key(panel)}
assert enabled(window.send_button) and not enabled(window.cancel_button)
assert window.tracker.get() == "" and not window.tracker_label.grid_info()

# Every option at every level keeps its score, with one pick per level. Quit gets a row of its own.
assert chips(app.key(panel)) == [("open 90%", True)] and chips(app.key(bar)) == [("open 5%", False)]
assert chips(app.quit_row(app.key(root))) == [("quit 5%", False)]
assert chips(app.key(save)) == [("invoke 80%", True)] and chips(app.quit_row(app.key(panel))) == [("quit 5%", False)]
assert chips(app.key(heading)) == [] and app.key(box) in window.chips

# Offscreen items were never offered, but a minimized window was.
assert chips(app.key(tucked)) == [] and chips(app.key(camera)) == [("open 5%", False)]

# Refresh re-reads what's open and keeps it open, along with the trail.
late = Node("Late button")
panel.kids = (*panel.kids, late)
assert not window.tree.exists(app.key(late))
window.refresh_button.invoke()
pump(lambda: window.tree.exists(app.key(late)))
pump(lambda: enabled(window.refresh_button))
assert window.tree.item(app.key(panel), "open") and tagged("here") == (app.key(save),)
assert window.tree.exists(app.quit_row(app.key(root))) and chips(app.key(panel)) == [("open 90%", True)]
panel.kids = panel.kids[:-1]

# Yes acts and the run carries on, and a new prompt forgets the last one.
send("save it for real", OPEN, SAVE, QUIT, hold=True)
assert window.shown.get() == "save it for real" and window.step.get() == app.NO_STEP and not tagged("here")
assert not window.chips
model.gate.set()
pump(lambda: window.prompt)
window.answer(True)
assert window.status.get() == "Working."
pump(lambda: not window.running)
assert clicks == ["save"] and window.status.get() == "The model quit after 1 action."
assert window.tracker.get() == f"1. {SAVED}" and window.tracker_label.grid_info()
assert model.done == [['Pane "App"', SAVED]]

# The tree shows the current walk. The tracker keeps what came before it.
assert app.key(save) not in window.chips and chips(app.quit_row(app.key(root))) == [("quit 90%", True)]

# With asking off, it acts without a prompt, for as long as the model keeps going.
window.ask_first.set(False)
send("save twice", OPEN, SAVE, OPEN, SAVE, QUIT)
assert clicks == ["save"] * 3 and window.prompt is None and window.status.get() == "The model quit after 2 actions."
assert window.tracker.get() == f"1. {SAVED}\n2. {SAVED}"

# A new prompt clears the tracker.
send("nothing", QUIT)
assert window.tracker.get() == "" and not window.tracker_label.grid_info() and model.done == []

# A long tracker keeps the latest actions on show.
harness.MAX_ACTIONS = 20
send("save a lot", *[OPEN, SAVE] * 7, QUIT)
assert window.status.get() == "The model quit after 7 actions."
assert window.tracker.get().splitlines() == ["…and 3 earlier", *[f"{n}. {SAVED}" for n in range(4, 8)]]
harness.MAX_ACTIONS = 5
clicks.clear()

# The action limit ends the run and says so, with the last walk still showing.
harness.MAX_ACTIONS = 2
send("save forever", *[OPEN, SAVE] * 4)
assert clicks == ["save"] * 2 and window.status.get() == "Stopped after 2 actions: reached the action limit of 2."
assert tagged("here") == (app.key(save),)
harness.MAX_ACTIONS = 5
clicks.clear()

# A low-confidence decision after an action stops the run without taking the action back.
send("save it", OPEN, SAVE, ("quit", 0.2))
assert window.status.get() == "Stopped after 1 action: confidence 0.20 is below the floor of 0.50."
clicks.clear()
window.ask_first.set(True)

# Saying no or cancelling after an action never claims nothing happened.
send("save twice", OPEN, SAVE, OPEN, SAVE, allow=[True, False])
assert clicks == ["save"] and window.status.get() == "You said no, after 1 action."
assert window.tracker.get() == f"1. {SAVED}"
clicks.clear()
send("save twice", OPEN, SAVE, OPEN, SAVE, hold=True)
model.gate.set()
pump(lambda: window.prompt)
window.answer(True)
pump(lambda: window.prompt)
window.stop()
pump(lambda: not window.running)
assert clicks == ["save"] and window.status.get() == "Cancelled after 1 action."
clicks.clear()

# Cancel mid-decision: Send stays locked until the run ends, and nothing happens.
send("save again", OPEN, hold=True)
calls = model.calls
pump(lambda: model.calls > calls)
window.stop()
assert not enabled(window.cancel_button) and not enabled(window.send_button)
assert window.status.get() == "Cancelling after this decision."
model.gate.set()
pump(lambda: not window.running)
assert clicks == [] and window.status.get() == "Cancelled. Nothing was done."
assert enabled(window.send_button)

# Cancel closes an open prompt, which counts as cancelling rather than No.
send("save again", OPEN, SAVE, hold=True)
model.gate.set()
pump(lambda: window.prompt)
window.stop()
assert window.status.get() == "Cancelling after this decision."
pump(lambda: not window.running)
assert window.prompt is None and clicks == [] and window.status.get() == "Cancelled. Nothing was done."

# Escape means No, and does nothing without a prompt.
send("save again", OPEN, SAVE, hold=True)
model.gate.set()
pump(lambda: window.prompt)
window.on_escape(None)
pump(lambda: not window.running)
assert clicks == [] and window.status.get() == "You said no. Nothing was done."
window.on_escape(None)

# Typing asks with the text and uses it, and the field is then cleared.
window.text.set("hi")
send("name it", OPEN, TYPE, QUIT, hold=True)
model.gate.set()
pump(lambda: window.prompt)
assert question() == 'Type "hi" into Pane "Name"?'
window.answer(True)
pump(lambda: not window.running)
assert typed == ["hi"] and window.text.get() == ""

# Quit, the confidence floor and a failed model call all end cleanly.
send("nothing", QUIT)
assert window.status.get() == "Stopped: the model chose to quit."
assert chips(app.quit_row(app.key(root))) == [("quit 90%", True)]
send("unsure", ('open Pane "App"', 0.49))
assert window.status.get() == "Stopped: confidence 0.49 is below the floor of 0.50."
assert tagged("here") == (app.key(root),)
send("offline", ConnectionError("model is down"))
assert window.status.get() == "Error: model is down" and enabled(window.send_button)

# Long messages shrink and end in an ellipsis, so the tree keeps its room.
send("x" * 500, QUIT)
assert len(window.shown.get()) == app.MAX_SHOWN and window.shown.get().endswith("…")
assert window.message.cget("style") == "Long.TLabel"
send("short", QUIT)
assert window.message.cget("style") == "Message.TLabel"

# A minimized window opens like any other, and goes back to minimized if nothing came of it.
camera.kids = (Node("Shutter", acts={"invoke": print}),)
send("take a photo", OPEN_CAMERA, QUIT)
assert app.key(camera.kids[0]) in window.chips and iconic == {7}

# Rows load their children when opened.
row = app.key(bar)
assert window.tree.tag_has("placeholder", window.tree.get_children(row)[0])
window.tree.focus(row)
window.on_open(None)
pump(lambda: window.tree.exists(app.key(start)))

# A row whose contents went away since it was read says so instead of opening onto nothing.
send("short", QUIT)
kept, bar.kids = bar.kids, ()
window.tree.focus(row)
window.on_open(None)
pump(lambda: window.tree.exists(f"{row}:empty"))
assert window.tree.item(f"{row}:empty", "text") == "Nothing here any more. Press Refresh."
bar.kids = kept

# Refresh drops the arrow from a row that's now empty.
send("short", QUIT)
assert window.tree.get_children(row)
bar.kids = ()
window.refresh_button.invoke()
pump(lambda: enabled(window.refresh_button) and not window.tree.get_children(row))
bar.kids = kept

# A closed dropdown says so. Choosing it walks into its items, asks once, and leaves it closed.
five = Node("5 seconds", acts={"select": lambda: clicks.append("five")})
delay = Dropdown("Delay", Node("None", acts={"select": print}), five)
form = Node("Desktop", delay, Node("Other", Node("Thing", acts={"invoke": print})))
window.get_root = lambda: form
window.refresh_button.invoke()
pump(lambda: window.tree.exists(app.key(delay)) and enabled(window.refresh_button))
assert text(delay) == 'Pane "Delay" (closed)' and window.tree.get_children(app.key(delay)) == ()
send("set the delay", ('expand Pane "Delay"', 0.9), ('select Pane "5 seconds"', 0.8), QUIT, hold=True)
model.gate.set()
pump(lambda: window.prompt)
assert question() == 'Select Pane "5 seconds"?'
assert text(delay) == 'Pane "Delay"' and chips(app.key(delay)) == [("expand 90%", True)]
assert chips(app.key(five)) == [("select 80%", True)] and tagged("here") == (app.key(five),)
window.answer(True)
pump(lambda: not window.running)
assert clicks[-1] == "five" and delay.ExpandCollapseState == harness.uia.ExpandCollapseState.Collapsed
window.get_root = lambda: root

# A level the harness passes through on its own says so, and wasn't scored.
only = Node("Only", Node("Inner", Node("Deep")), Node("Second", Node("Deep too")))
single = Node("Root", only)
window.get_root = lambda: single
send("x", QUIT)
assert chips(app.key(only)) == [("only path", False)] and not window.tree.exists(app.quit_row(app.key(single)))
assert chips(app.quit_row(app.key(only))) == [("quit 90%", True)]
window.get_root = lambda: root

# Scores render as percentages, and options past the model's top 20 show a ceiling.
assert app.percent("a", {"a": 0.004}) == "<1%" and app.percent("a", {"a": 0.984}) == "98%"
assert app.percent("b", {"a": 0.5, "c": 0.021}) == "up to 3%" and app.percent("b", {"a": 0.5, "c": 0.001}) == "<1%"

# Settings start at the defaults and open once.
floor, actions, steps, wait, names = app.SLIDERS
assert harness.MIN_CONFIDENCE == 0.5 and not os.path.exists(app.SETTINGS_FILE)
window.open_settings()
first = window.settings
window.open_settings()
assert window.settings is first
shown = lambda setting: window.setting_values[setting.key][1].get()
assert shown(floor) == "0.50" and shown(actions) == "5" and shown(steps) == "60" and shown(wait) == "0.00 s" and shown(names) == "24"

# Each setting snaps to its scale, stays in range, sets the harness and is saved.
window.pick(floor, 0.62)
assert harness.MIN_CONFIDENCE == 0.6 and shown(floor) == "0.60" and window.setting_values["confidence_floor"][0].get() == 0.6
window.pick(actions, 3.4)
assert harness.MAX_ACTIONS == 3 and isinstance(harness.MAX_ACTIONS, int) and shown(actions) == "3"
window.pick(steps, 73)
assert harness.MAX_STEPS == 70 and isinstance(harness.MAX_STEPS, int)
window.pick(wait, 1.1)
assert harness.ACTION_WAIT == 1.0 and shown(wait) == "1.00 s"
window.pick(names, 9)
assert harness.PREVIEW_NAMES == 8
window.pick_model("another-model")
assert harness.MODEL == "another-model" and window.model_choice.get() == "another-model"
saved = json.load(open(app.SETTINGS_FILE))
assert saved == {"confidence_floor": 0.6, "max_actions": 3, "max_steps": 70, "action_wait": 1.0, "preview_names": 8, "model": "another-model"}
window.pick(actions, 99)
assert harness.MAX_ACTIONS == 20
window.pick(actions, 0)
assert harness.MAX_ACTIONS == 1

# The limits really apply: at 1 action a prompt behaves like a single step.
harness.ACTION_WAIT = 0
window.pick(floor, 0)
window.ask_first.set(False)
send("save it", OPEN, SAVE, OPEN, SAVE)
assert window.status.get() == "Stopped after 1 action: reached the action limit of 1."
clicks.clear()
window.ask_first.set(True)

# Saved settings come back on the next launch.
for setting in app.SLIDERS:
    setattr(harness, setting.constant, app.DEFAULTS[setting.key])
harness.MODEL = app.DEFAULTS["model"]
app.load_settings()
assert harness.MAX_ACTIONS == 1 and harness.MAX_STEPS == 70 and harness.PREVIEW_NAMES == 8 and harness.MODEL == "another-model"

# Reset puts everything back, on screen and on disk.
window.reset_settings()
assert all(getattr(harness, s.constant) == app.DEFAULTS[s.key] for s in app.SLIDERS) and harness.MODEL == app.DEFAULTS["model"]
assert shown(actions) == "5" and json.load(open(app.SETTINGS_FILE))["max_actions"] == 5

# A closed settings window can be opened again.
window.settings.destroy()
window.update()
window.open_settings()
assert window.settings is not first
window.settings.destroy()

# A bad settings file leaves the defaults alone, and a wild value is clamped.
for bad in ("not json", "[1]", '{"max_actions": "lots"}', '{"model": null}'):
    with open(app.SETTINGS_FILE, "w") as file:
        file.write(bad)
    app.load_settings()
    assert harness.MAX_ACTIONS == app.DEFAULTS["max_actions"] and harness.MODEL == app.DEFAULTS["model"]
with open(app.SETTINGS_FILE, "w") as file:
    file.write('{"confidence_floor": 40, "max_actions": -3}')
app.load_settings()
assert harness.MIN_CONFIDENCE == 1 and harness.MAX_ACTIONS == 1

window.close()
print("ok")
