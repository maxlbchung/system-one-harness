"""Self-check on a fake tree, no network or API key: python test_harness.py"""
import io
import json
import math
import urllib.request
from _ctypes import COMError

import benchmark
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
    """Lists nothing until it's opened, like a real dropdown. Picking an item can close it on its own."""

    def __init__(self, name, *items):
        super().__init__(name, acts={"expand": self.Expand})
        self.items, self.ExpandCollapseState = items, harness.uia.ExpandCollapseState.Collapsed

    def GetPattern(self, pattern):
        return self if pattern == harness.uia.PatternId.ExpandCollapsePattern else None

    def Expand(self):
        self.ExpandCollapseState, self.kids = harness.uia.ExpandCollapseState.Expanded, self.items
        self.acts = {"collapse": self.Collapse}

    def Collapse(self):
        self.ExpandCollapseState, self.kids = harness.uia.ExpandCollapseState.Collapsed, ()
        self.acts = {"expand": self.Expand}


def scripted(*names, confidence=0.9):
    """Stand-in model that replays option names and records what it was shown."""
    seen, done = [], []

    def ask(goal, path, opts, text, finished):
        seen.append(list(opts))
        done.append(list(finished))
        return {"choice": names[len(seen) - 1], "confidence": confidence}

    ask.seen, ask.done = seen, done
    return ask


harness.actions = lambda control: dict(control.acts)
harness.ACTION_WAIT = 0
refuse = lambda *_: False

clicks, typed = [], []
save = Node("Save", acts={"invoke": lambda: clicks.append("save")})
box = Node("Name", acts={"type": typed.append})
hidden = Node("Hidden", acts={"invoke": lambda: clicks.append("hidden")}, offscreen=True)
window = Node("App", save, box, hidden)
root = Node("Desktop", window, Node("Taskbar", Node("Start")))
OPEN, SAVE, TYPE = 'open Pane "App"', 'invoke Pane "Save"', 'type Pane "Name"'
SAVED = 'Click Pane "Save"'
WALKED = ['Pane "App"', SAVED]  # History keeps the nodes picked on the way, without the root.

# Descend, act, then start again from the desktop with the action on record, until the model quits.
ask = scripted(OPEN, SAVE, "quit")
assert harness.run("save it", root, ask) == "quit"
assert clicks == ["save"] and len(ask.seen) == 3
assert ask.seen[1] == ["quit", SAVE]  # hidden is offscreen, type has no text
assert ask.done == [[], [], [WALKED]]

# It keeps going for as long as the model does.
clicks.clear()
ask = scripted(OPEN, SAVE, OPEN, SAVE, OPEN, SAVE, "quit")
assert harness.run("save three times", root, ask) == "quit"
assert clicks == ["save"] * 3 and ask.done[-1] == [WALKED] * 3

# The action limit is the ceiling, and it stops right after the last allowed action.
clicks.clear()
events = []
limit, harness.MAX_ACTIONS = harness.MAX_ACTIONS, 2
assert harness.run("loop", root, scripted(*[OPEN, SAVE] * 5), on_event=events.append) == "quit"
assert clicks == ["save"] * 2 and events[-1] == {"kind": "stop", "reason": "reached the action limit of 2"}
assert [e["kind"] for e in events].count("restart") == 1
harness.MAX_ACTIONS = limit

# The step limit counts decisions across all the actions of a prompt.
clicks.clear()
events.clear()
limit, harness.MAX_STEPS = harness.MAX_STEPS, 3
assert harness.run("loop", root, scripted(*[OPEN, SAVE] * 5), on_event=events.append) == "quit"
assert clicks == ["save"] and events[-1] == {"kind": "stop", "reason": "reached the step limit of 3"}
harness.MAX_STEPS = limit
clicks.clear()

# The screen gets time to catch up before the next walk reads it.
naps = []
real_sleep, harness.time.sleep = harness.time.sleep, naps.append
harness.ACTION_WAIT = 2.5
assert harness.run("save it", root, scripted(OPEN, SAVE, "quit")) == "quit" and 2.5 in naps
harness.time.sleep, harness.ACTION_WAIT = real_sleep, 0
clicks.clear()

# Type uses the caller's text, is only offered with it, and only once per prompt.
ask = scripted(OPEN, TYPE, OPEN, "quit")
assert harness.run("name it", root, ask, text="hi") == "quit"
assert typed == ["hi"] and TYPE in ask.seen[1] and TYPE not in ask.seen[3]
assert ask.done[-1] == [['Pane "App"', 'Type "hi" into Pane "Name"']]

# Quit, a refusal and low confidence do nothing.
assert harness.run("nothing", root, scripted("quit")) == "quit"
assert harness.run("save it", root, scripted(OPEN, SAVE), permit=refuse) == "denied"
ask = scripted(OPEN, SAVE, confidence=harness.MIN_CONFIDENCE - 0.01)
assert harness.run("save it", root, ask) == "quit" and len(ask.seen) == 1
assert clicks == []

# A refusal can come after earlier actions, which stay done.
answers = iter([True, False])
ask = scripted(OPEN, SAVE, OPEN, SAVE)
assert harness.run("save twice", root, ask, permit=lambda *_: next(answers)) == "denied" and clicks == ["save"]
clicks.clear()

# The floor says what stopped the run, and reads the current value.
events = []
floor, harness.MIN_CONFIDENCE = harness.MIN_CONFIDENCE, 0.8
assert harness.run("save it", root, scripted(OPEN, confidence=0.75), on_event=events.append) == "quit"
assert events[-1] == {"kind": "stop", "reason": "confidence 0.75 is below the floor of 0.80"}
harness.MIN_CONFIDENCE = floor

# A refusal that came from a cancel reports as a cancel.
answers = iter([False, False, True])
assert harness.run("save it", root, scripted(OPEN, SAVE), permit=refuse, cancelled=lambda: next(answers)) == "cancelled"
assert clicks == []

# Permission is asked with what's about to happen, before it happens.
asked = []
assert harness.run("save it", root, scripted(OPEN, SAVE), permit=lambda *a: asked.append(a) or False) == "denied"
assert asked == [("invoke", save, None)] and clicks == []
assert harness.request("invoke", save, None) == 'Click Pane "Save"?'
assert harness.request("type", box, "hi") == 'Type "hi" into Pane "Name"?'

# A cancel during the model call stops before the action, and events trace the walk.
events = []
assert harness.run("save it", root, scripted(OPEN), on_event=events.append, cancelled=lambda: True) == "cancelled"
assert clicks == [] and [e["kind"] for e in events] == ["at"]
events.clear()
assert harness.run("save it", root, scripted(OPEN, SAVE, "quit"), on_event=events.append) == "quit"
assert [e["kind"] for e in events] == ["at", "pick", "at", "pick", "at", "done", "restart", "at", "pick"]
assert events[0]["node"] is root and events[4]["node"] is save and not events[1]["auto"]
assert events[5]["action"] == SAVED and events[7]["node"] is root
clicks.clear()

# A dead end stops the run, because there's no way back up.
other = Node("Other", Node("A", acts={"invoke": print}), Node("B", acts={"invoke": print}))
tree = Node("Desktop", Node("Mid", Node("Deep", Node("Label"))), other)
events.clear()
ask = scripted('open Pane "Mid"')
assert harness.run("find it", tree, ask, on_event=events.append) == "quit"
assert [e["auto"] for e in events if e["kind"] == "pick"] == [False, True]
assert events[-1] == {"kind": "stop", "reason": "nothing usable here"} and len(ask.seen) == 1

# Expanding is looking: the agent walks into the items, and permission is asked only for the final action.
picked = []
five = Node("5 seconds", acts={"select": lambda: picked.append("5")})
delay = Dropdown("Delay", Node("None", acts={"select": print}), five)
form = Node("Form", delay, Node("Other", Node("Thing", acts={"invoke": print})))
EXPAND, FIVE = 'expand Pane "Delay"', 'select Pane "5 seconds"'
assert EXPAND in harness.options(form) and 'open Pane "Delay"' not in harness.options(form)
asked.clear()
ask = scripted(EXPAND, FIVE, "quit")
assert harness.run("set the delay", form, ask, permit=lambda *a: asked.append(a) or True) == "quit"
assert ask.seen[1] == ["quit", 'select Pane "None"', FIVE] and picked == ["5"]
assert [a[0] for a in asked] == ["select"] and ask.done[2] == [['Pane "Delay"', 'Select Pane "5 seconds"']]
assert harness.is_collapsed(delay)  # It's closed again, because a web select stays open after picking.

# Answering the prompt can close a classic dropdown, so it's reopened for the action, then closed.
states = []
five.acts = {"select": lambda: states.append(delay.ExpandCollapseState)}
assert harness.run("x", form, scripted(EXPAND, FIVE, "quit"), permit=lambda *_: delay.Collapse() or True) == "quit"
assert states == [harness.uia.ExpandCollapseState.Expanded] and harness.is_collapsed(delay)

# Stopping inside a dropdown closes it, whichever way the run ends.
for picks, permit, ending in [((EXPAND, "quit"), lambda *_: True, "quit"), ((EXPAND, FIVE), refuse, "denied")]:
    assert harness.run("x", form, scripted(*picks), permit=permit) == ending
    assert harness.is_collapsed(delay)


class Stuck(Dropdown):
    """Opens without listing anything under itself, like a menu that appears elsewhere in its window."""

    def Expand(self):
        self.ExpandCollapseState = harness.uia.ExpandCollapseState.Expanded
        self.acts = {"collapse": self.Collapse}


# If nothing appears inside, the agent stays at this level with the row now offering collapse.
stuck = Stuck("Menu")
ask = scripted('expand Pane "Menu"', "quit")
assert harness.run("x", Node("Bar", stuck, Node("Other", Node("Thing", acts={"invoke": print}))), ask) == "quit"
assert 'collapse Pane "Menu"' in ask.seen[1] and harness.is_collapsed(stuck)

assert harness.has_children(form) and not harness.has_children(five) and not harness.is_collapsed(five)

# The app's own windows stay out of the agent's reach.
mine = Node("App window")
mine.ProcessId = harness.OWN_PROCESS
assert harness.listed(Node("Desktop", mine)) == [] and harness.children(Node("Desktop", mine)) == []


class Closing(Node):
    """Raises like a closing window for a set number of reads, then lists normally."""

    def __init__(self, failures, *kids):
        super().__init__("Closing", *kids)
        self.failures = failures

    def GetChildren(self):
        self.failures -= 1
        if self.failures >= 0:
            raise COMError(-2147220991, "dead element", (None, None, None, 0, None))
        return self.kids


# A window closing mid-read doesn't lose the list. A control that stays dead lists nothing.
harness.READ_RETRY = 0
assert harness.listed(Closing(2, save, box)) == [save, box]
assert harness.listed(Closing(99, save, box)) == []

# Offscreen items are listed, but the agent can't use them.
tucked = Node("Tucked away", offscreen=True)
assert harness.listed(Node("Desktop", tucked, save)) == [tucked, save]
assert harness.children(Node("Desktop", tucked, save)) == [save]

# A minimized Store app reports offscreen and lists nothing until it's restored, without taking focus.
iconic = {7}
shutter = Node("Shutter", acts={"invoke": lambda: clicks.append("shutter")})
camera = Node("Camera", offscreen=True)
camera.NativeWindowHandle = 7
commands = []


def show_window(handle, command):
    commands.append(command)
    restoring = command == harness.uia.SW.ShowNoActivate
    (iconic.discard if restoring else iconic.add)(handle)
    camera.kids, camera.IsOffscreen = ((shutter,), False) if restoring else ((), True)


harness.uia.IsIconic = lambda handle: handle in iconic
harness.uia.ShowWindow = show_window
harness.RESTORE_SETTLE = harness.RESTORE_WAIT = 0
desktop = Node("Desktop", camera, Node("Taskbar", Node("Start", acts={"invoke": print})))
OPEN_CAMERA, SHUTTER = 'open Pane "Camera"', 'invoke Pane "Shutter"'

assert harness.children(desktop)[0] is camera
assert list(harness.options(desktop)) == ["quit", OPEN_CAMERA, 'open Pane "Taskbar"']

# Entering it restores it, and acting there leaves it on screen, even after the model then quits.
ask = scripted(OPEN_CAMERA, SHUTTER, "quit")
assert harness.run("take a photo", desktop, ask) == "quit" and clicks == ["shutter"] and iconic == set()
assert ask.seen[1] == ["quit", SHUTTER] and commands == [harness.uia.SW.ShowNoActivate]
clicks.clear()
show_window(7, harness.uia.SW.ShowMinNoActive)

# Doing nothing there puts it back, whichever way the run ends, without taking focus.
for picks, permit, ending in [
    ((OPEN_CAMERA, "quit"), lambda *_: True, "quit"),
    ((OPEN_CAMERA, SHUTTER), refuse, "denied"),
]:
    commands.clear()
    assert harness.run("x", desktop, scripted(*picks), permit=permit) == ending
    assert iconic == {7} and commands[-1] == harness.uia.SW.ShowMinNoActive and clicks == []

# A look around after an earlier action is put back too, without touching what that action did.
ask = scripted('open Pane "Taskbar"', 'invoke Pane "Start"', OPEN_CAMERA, "quit")
assert harness.run("x", desktop, ask) == "quit" and iconic == {7} and ask.done[-1] == [['Pane "Taskbar"', 'Click Pane "Start"']]


def broken(goal, path, opts, text, done):
    if len(path) > 1:
        raise ConnectionError("model is down")
    return {"choice": OPEN_CAMERA, "confidence": 0.9}


try:
    harness.run("x", desktop, broken)
except ConnectionError:
    pass
assert iconic == {7}

# A single open-only child skips the model.
ask = scripted("quit")
assert harness.run("x", Node("Root", Node("Only", Node("Inner", acts={"invoke": print}))), ask) == "quit"
assert len(ask.seen) == 1

# Duplicate names stay distinct and the option limit holds.
twins = Node("Root", *[Node("Row", acts={"invoke": print}) for _ in range(300)])
opts = harness.options(twins)
assert len(opts) == harness.MAX_OPTIONS and len({o[1] for o in opts.values()}) == harness.MAX_OPTIONS

# A disabled control offers no actions, but a disabled container can still be entered.
off = Node("Off", acts={"invoke": print})
shell = Node("Shell", Node("Inside"), acts={"invoke": print})
off.IsEnabled = shell.IsEnabled = False
assert list(harness.options(Node("Bar", off, shell))) == ["quit", 'open Pane "Shell"']

# Clickable items aren't opened, and previews see past unnamed containers.
button = Node("Start", Node("Icon"), acts={"toggle": print})
assert list(harness.options(Node("Bar", button))) == ["quit", 'toggle Pane "Start"']
assert harness.preview(Node("Bar", Node("", Node("", button)))) == ["Start", "Icon"]

# The preview's size is a setting, and 0 turns previews off.
names, harness.PREVIEW_NAMES = harness.PREVIEW_NAMES, 1
assert harness.preview(Node("Bar", Node("", Node("", button)))) == ["Start"]
harness.PREVIEW_NAMES = 0
assert harness.preview(Node("Bar", Node("", Node("", button)))) == []
harness.PREVIEW_NAMES = names

# Passing through lone children to a dead end never calls the model.
ask = scripted()
assert harness.run("x", Node("Root", Node("Only", Node("Inner", Node("Label")))), ask) == "quit"
assert ask.seen == []

# Names can't break the option list.
assert harness.label(Node("two\n  lines" + "x" * 200)) == f'Pane "two lines{"x" * (harness.MAX_NAME - 9)}"'


# The answer is the likeliest listed letter, wherever the shuffle put the option.
prompts = []


def fake_urlopen(request, timeout):
    prompt = json.loads(request.data)["messages"][0]["content"]
    prompts.append(prompt)
    assert prompt.startswith("State:\n") and f"Location: Desktop > App\n\nQuestion: save it\n{harness.INSTRUCTIONS}\nOptions:\n[" in prompt
    assert "with: hi" in prompt
    save_letter = next(line[1] for line in prompt.splitlines() if SAVE in line)
    top = [("!", 0.9), (save_letter, 0.75), *[(k, 0.25 / 2) for k in "ABC" if k != save_letter]]
    top = [{"token": token, "logprob": math.log(p)} for token, p in top]
    return io.BytesIO(json.dumps({"logprobs": [{"top_logprobs": top}]}).encode())


urllib.request.urlopen = fake_urlopen
for _ in range(20):
    answer = harness.ask_openjev("save it", ["Desktop", "App"], harness.options(window, "hi"), "hi", [], "m")
    assert answer["choice"] == SAVE and abs(answer["confidence"] - 0.625) < 1e-9
    assert set(answer["scores"]) == {"quit", SAVE, TYPE} and abs(answer["scores"][SAVE] - 0.75) < 1e-9

# The prompt only mentions finished actions once there are some.
assert "Done so far" not in prompts[-1]
history = [["App", "Click X"], ["Click Y"]]
harness.ask_openjev("save it", ["Desktop", "App"], harness.options(window, "hi"), "hi", history, "m")
assert prompts[-1].startswith("State:\nDone so far: App > Click X; Click Y\nLocation: Desktop > App\n\nQuestion: save it\n")

# With paths off, it shows only the final commands.
harness.HISTORY_PATHS = False
harness.ask_openjev("save it", ["Desktop", "App"], harness.options(window, "hi"), "hi", history, "m")
assert prompts[-1].startswith("State:\nDone so far: Click X; Click Y\nLocation: Desktop > App\n\n")
harness.HISTORY_PATHS = True

# The model list comes from Ollama, and is empty if Ollama isn't running.
urllib.request.urlopen = lambda url, timeout: io.BytesIO(json.dumps({"models": [{"name": "a:latest"}, {"name": "b:q4"}]}).encode())
assert harness.installed_models() == ["a", "b:q4"]


def refused(url, timeout):
    raise OSError("connection refused")


urllib.request.urlopen = refused
assert harness.installed_models() == []

# Benchmark steps have to happen in order, and extra actions between them only cost turns.
wanted = ['"One"', '"Plus"', '"One"']
assert benchmark.reached(wanted, ['Click "One"', 'Click "Two"', 'Click "Plus"', 'Click "One"'])
assert not benchmark.reached(wanted, ['Click "One"', 'Click "Plus"'])
assert not benchmark.reached(wanted, ['Click "Plus"', 'Click "One"', 'Click "One"'])

print("ok")
