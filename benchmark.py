"""Real goals, real model, the baseline against named variants: python benchmark.py [repeats] [variant ...]

A variant is a set of harness constants to override, and with none named they all run.
Every decision is kept in runs/<time>.jsonl, so a miss can be read back afterwards.

Clicks happen only inside test apps opened here, and each run is skipped if its app is
already open, since that one is the user's. A run succeeds if its steps all happened in
order, plus the on-screen result where there is one, so wasted actions cost turns rather
than the whole run. A goal that's already done succeeds only if the model quits without
acting. Exact counts the runs that also took the optimal turns.
A turn is one restart of the walk with a new action in its history.
"""
import collections
import json
import os
import re
import statistics
import subprocess
import sys
import time
from typing import Callable, NamedTuple

import uiautomation as uia

import harness

REPEATS = 5
MIN_CONFIDENCE, MAX_ACTIONS = 0.25, 10  # The user's settings. Room to overshoot a four step goal.
BASELINE = "baseline"
# Name -> harness constants to override. The baseline is the harness as it ships.
VARIANTS = {
    BASELINE: {},
    "final command": {"HISTORY_PATHS": False},
    "state": {"SHOW_STATE": True},
    "back": {"ALLOW_BACK": True},
}
RUNS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runs")
SEARCH_SECONDS = 1


class App:
    def __init__(self, title, start, process, wait):
        self.title, self.start, self.process, self.wait = title, start, process, wait


CHARMAP = App("Character Map", ["charmap.exe"], "charmap.exe", 1.3)
SNIPPING = App("Snipping Tool", 'start "" snippingtool.exe', "SnippingTool.exe", 3.2)
CALCULATOR = App("Calculator", ["calc.exe"], "CalculatorApp.exe", 2.5)


class Goal(NamedTuple):
    app: App
    text: str  # What the model is asked. {seen} is filled with what watch found.
    wanted: list  # What each action must mention, in order. Empty when the right answer is to do nothing.
    check: Callable = None  # check(done, seen) reads the screen after the run.
    watch: Callable = None  # Reads the screen before the run. The goal is skipped if it finds nothing.


def find(control, test, budget=400):
    """The first control under this one that passes test, breadth first, or None."""
    queue = harness.listed(control)
    while queue and budget:
        child, budget = queue.pop(0), budget - 1
        try:
            if test(child):
                return child
        except harness.COMError:  # The element vanished while it was read.
            continue
        queue += harness.listed(child)
    return None


def toggle_state(app, name):
    """Whether the app's toggle with this in its name is on, or None if there isn't one."""
    window = top(app.title)
    found = window and find(window, lambda c: name in c.Name and c.GetPattern(uia.PatternId.TogglePattern))
    return found.GetPattern(uia.PatternId.TogglePattern).ToggleState == uia.ToggleState.On if found else None


def font():
    """The font Character Map's dropdown shows, or None if it can't be read."""
    window = top(CHARMAP.title)
    box = window and find(window, lambda c: c.ControlTypeName == "ComboBoxControl" and c.GetPattern(uia.PatternId.ValuePattern))
    return (box.GetPattern(uia.PatternId.ValuePattern).Value or None) if box else None


def shows_two(done, seen):
    window = top(CALCULATOR.title)
    display = window.TextControl(AutomationId="CalculatorResults") if window else None
    return bool(display) and display.Exists(SEARCH_SECONDS) and display.Name == "Display is 2"


def capture_mode():
    """Snipping Tool's capture toggle, or "unknown" so the goal still runs on action names alone."""
    state = toggle_state(SNIPPING, "Capture mode")
    return "unknown" if state is None else state


def advanced_view_off():
    return True if toggle_state(CHARMAP, "Advanced view") is False else None


# The last two are already done when the run starts, so the only right answer is to quit without acting.
# Their checks read the same control again, so they pass only if nothing was done and nothing changed.
GOALS = [
    Goal(CHARMAP, "press the Select button in Character Map", ['"Select"']),
    Goal(SNIPPING, "set the snip delay to 5 seconds in Snipping Tool", ["5 second"]),
    # Toggling it twice mentions it twice and leaves it off, so the toggle has to end up changed.
    Goal(SNIPPING, "turn on video capture mode in Snipping Tool", ["Capture mode"], lambda done, seen: seen == "unknown" or capture_mode() != seen, capture_mode),
    Goal(CHARMAP, "set the font to Arial Black in Character Map and then press the Select button", ["Arial Black", '"Select"']),
    Goal(SNIPPING, "set the snip delay to 3 seconds and set the snipping area to Window in Snipping Tool", ["3 second", "Window"]),
    Goal(CHARMAP, "press the Select button in Character Map, then press Select again", ['"Select"', '"Select"']),
    Goal(CALCULATOR, "in Calculator, press 1 + 1 = to find the answer", ['"One"', '"Plus"', '"One"', '"Equals"'], shows_two),
    Goal(CHARMAP, "make sure the font is set to {seen} in Character Map", [], lambda done, seen: not done and font() == seen, font),
    Goal(CHARMAP, "make sure Advanced view is unchecked in Character Map", [], lambda done, seen: not done and advanced_view_off(), advanced_view_off),
]


def reached(wanted, done):
    """Each wanted step matches an action, in order, with anything else allowed in between."""
    steps = iter(done)
    return all(any(step in action for action in steps) for step in wanted)


def top(title):
    return next((c for c in uia.GetRootControl().GetChildren() if title in c.Name), None)


def running(app):
    return app.process.lower() in subprocess.run("tasklist", capture_output=True, text=True).stdout.lower()


def open_app(app, mine, windows):
    """Open the app and allow it, or return False if it's the user's or not on this desktop."""
    if running(app):
        return False
    shell = isinstance(app.start, str)  # Store apps start through the shell.
    subprocess.Popen(app.start, shell=shell, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(app.wait)
    window = top(app.title)
    if not window:
        close_app(app)
        return False
    windows.add(window.NativeWindowHandle)
    # A Store app's frame belongs to a host shared with others, so only what's inside it counts.
    framed = window.ClassName == "ApplicationFrameWindow"
    mine |= {child.ProcessId for child in harness.children(window)} - {window.ProcessId} if framed else {window.ProcessId}
    return True


def close_app(app):
    subprocess.run(["taskkill", "/IM", app.process, "/F"], capture_output=True)
    time.sleep(0.4)


def traced(event):
    """An event as it goes in the trace: names instead of controls, which aren't JSON."""
    kept = {key: value for key, value in event.items() if key not in ("node", "options")}
    if "options" in event:
        kept["option_count"] = len(event["options"])
    return kept


def outcome(ending, stops):
    """How a run ended, as one name to count. The confidence in a floor stop differs every time, so it's left out."""
    return re.sub(r"confidence \d+\.\d+ is", "confidence", stops[0]) if stops else ending


def paired(pairs, name):
    """(wins, losses) against the baseline, over the pairs where only one of the two succeeded."""
    both = [(pair[name], pair[BASELINE]) for pair in pairs.values() if name in pair and BASELINE in pair]
    return sum(ours and not theirs for ours, theirs in both), sum(theirs and not ours for ours, theirs in both)


def apply(overrides, originals):
    """Set the harness to one variant, undoing whatever the last one changed."""
    for constant, value in (originals | overrides).items():
        setattr(harness, constant, value)


def attempt(goal, mine, windows, log=lambda kind, **fields: None):
    """One scored run: (succeeded, turns, outcome), or a string saying why it was skipped."""
    mine.clear()
    if not open_app(goal.app, mine, windows):
        return "app already open or not on this desktop"
    done, stops, seen = [], [], None

    def on_event(event):
        if event["kind"] == "done":
            done.append(event["action"])
        elif event["kind"] == "stop":
            stops.append(event["reason"])
        if event["kind"] in ("pick", "done", "stop"):
            log(**traced(event))

    try:
        seen = goal.watch() if goal.watch else None
        if goal.watch and seen is None:
            return "its starting state couldn't be read, or wasn't what the goal assumes"
        ending = harness.run(
            goal.text.format(seen=seen), uia.GetRootControl(), lambda *a: harness.ask_openjev(*a, harness.MODEL),
            permit=lambda verb, control, text: control.ProcessId in mine, on_event=on_event,
        )
        success = reached(goal.wanted, done) and (not goal.check or bool(goal.check(done, seen)))
        # Doing nothing is only right when the model chose to stop, not when the harness gave up.
        success = success and (bool(goal.wanted) or (ending == "quit" and not stops))
    except Exception as error:  # A crashed run is a failed one, and the app still has to close.
        ending, success, stops = "error", False, [repr(error)[:80]]
    finally:
        close_app(goal.app)
    print(f"      {'OK  ' if success else 'MISS'} {ending}{' (' + stops[0] + ')' if stops else ''} {[d[:34] for d in done]}", flush=True)
    log("result", success=success, ending=ending, stop=stops[0] if stops else None, actions=done, turns=len(done), optimal=len(goal.wanted), seen=seen)
    return success, len(done), outcome(ending, stops)


def row(name, results, optimal):
    wins = [turns for success, turns, _ in results if success]
    exact = sum(turns == optimal for turns in wins)
    turns = f"{statistics.mean(wins):.2f}" if wins else "-"
    return f"{name:14} {len(wins)}/{len(results)} succeeded, {exact} exact, {turns} turns (optimal {optimal})"


def main(repeats, names):
    harness.MIN_CONFIDENCE, harness.MAX_ACTIONS = MIN_CONFIDENCE, MAX_ACTIONS
    variants = {name: VARIANTS[name] for name in VARIANTS if name == BASELINE or name in names or not names}
    originals = {constant: getattr(harness, constant) for overrides in variants.values() for constant in overrides}
    mine, windows, real_actions, real_show = set(), set(), harness.actions, uia.ShowWindow

    def actions(control):
        """Dropdowns open without asking, so keep the agent from opening any outside the test apps."""
        found = real_actions(control)
        if control.ProcessId not in mine:
            found.pop("expand", None)
        return found

    harness.actions = actions
    uia.ShowWindow = lambda handle, command: real_show(handle, command) if handle in windows else False
    results = {(goal.text, name): [] for goal in GOALS for name in variants}
    pairs = collections.defaultdict(dict)  # (repeat, goal) -> variant -> succeeded
    os.makedirs(RUNS, exist_ok=True)
    trace_path = os.path.join(RUNS, time.strftime("%Y%m%d-%H%M%S") + ".jsonl")
    with uia.UIAutomationInitializerInThread(), open(trace_path, "w", encoding="utf-8") as trace:
        for repeat in range(repeats):
            for goal in GOALS:
                print(f"\n[{repeat + 1}/{repeats}] {goal.text}", flush=True)
                for name, overrides in variants.items():  # Back to back, so they meet the same desktop.
                    apply(overrides, originals)
                    harness.SHUFFLE.seed(f"{repeat}/{goal.text}")  # And the same option order, for as long as they walk alike.
                    print(f"   {name}", flush=True)

                    def log(kind, **fields):
                        trace.write(json.dumps({"kind": kind, "variant": name, "repeat": repeat, "goal": goal.text} | fields) + "\n")
                        trace.flush()

                    result = attempt(goal, mine, windows, log)
                    if isinstance(result, str):
                        print(f"      SKIP {result}", flush=True)
                    else:
                        results[goal.text, name].append(result)
                        pairs[repeat, goal.text][name] = result[0]
    apply({}, originals)
    print("\n===== results =====")
    for goal in GOALS:
        print(goal.text)
        for name in variants:
            print("  " + row(name, results[goal.text, name], len(goal.wanted)))
    print("multi-step goals together, turns compared per goal above")
    for name in variants:
        runs = [results[goal.text, name] for goal in GOALS if len(goal.wanted) > 1]
        wins = sum(success for goal_runs in runs for success, _, _ in goal_runs)
        print(f"  {name:14} {wins}/{sum(map(len, runs))} succeeded")
    print("how runs ended")
    for name in variants:
        endings = collections.Counter(ending for goal in GOALS for _, _, ending in results[goal.text, name])
        print(f"  {name:14} " + ", ".join(f"{count} {ending}" for ending, count in endings.most_common()))
    print("paired against the baseline: runs only the variant won - runs only the baseline won")
    for name in variants:
        if name != BASELINE:
            print(f"  {name:14} {'-'.join(map(str, paired(pairs, name)))}")
    print(f"trace in {trace_path}")


if __name__ == "__main__":
    args = sys.argv[1:]
    repeats = int(args.pop(0)) if args and args[0].isdigit() else REPEATS
    if unknown := [name for name in args if name not in VARIANTS]:
        sys.exit(f"unknown variant {unknown[0]!r}, choose from: {', '.join(VARIANTS)}")
    main(repeats, args)
