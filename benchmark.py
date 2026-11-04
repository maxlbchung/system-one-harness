"""Real goals, real model: python benchmark.py [repeats]

Clicks happen only inside test apps opened here, and each run is skipped if its app is
already open, since that one is the user's. A run succeeds if its steps all happened in
order, plus the on-screen result where there is one, so wasted actions cost turns rather
than the whole run. Exact counts the runs that also took the optimal turns.
A turn is one restart of the walk with a new action in its history.
"""
import statistics
import subprocess
import sys
import time

import uiautomation as uia

import harness

REPEATS = 5
MIN_CONFIDENCE, MAX_ACTIONS = 0.25, 10  # The user's settings. Room to overshoot a four step goal.
SEARCH_SECONDS = 1


class App:
    def __init__(self, title, start, process, wait):
        self.title, self.start, self.process, self.wait = title, start, process, wait


CHARMAP = App("Character Map", ["charmap.exe"], "charmap.exe", 1.3)
SNIPPING = App("Snipping Tool", 'start "" snippingtool.exe', "SnippingTool.exe", 3.2)
CALCULATOR = App("Calculator", ["calc.exe"], "CalculatorApp.exe", 2.5)


def shows_two():
    window = top(CALCULATOR.title)
    display = window.TextControl(AutomationId="CalculatorResults") if window else None
    return bool(display) and display.Exists(SEARCH_SECONDS) and display.Name == "Display is 2"


# App, goal, what each action must mention in order, on-screen check.
GOALS = [
    (CHARMAP, "press the Select button in Character Map", ['"Select"'], None),
    (SNIPPING, "set the snip delay to 5 seconds in Snipping Tool", ["5 second"], None),
    (SNIPPING, "turn on video capture mode in Snipping Tool", ["Capture mode"], None),
    (CHARMAP, "set the font to Arial Black in Character Map and then press the Select button", ["Arial Black", '"Select"'], None),
    (SNIPPING, "set the snip delay to 3 seconds and set the snipping area to Window in Snipping Tool", ["3 second", "Window"], None),
    (CHARMAP, "press the Select button in Character Map, then press Select again", ['"Select"', '"Select"'], None),
    (CALCULATOR, "in Calculator, press 1 + 1 = to find the answer", ['"One"', '"Plus"', '"One"', '"Equals"'], shows_two),
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


def attempt(app, goal, wanted, check, mine, windows):
    """One scored run: (succeeded, turns), or None if it was skipped."""
    mine.clear()
    if not open_app(app, mine, windows):
        return None
    done, stops = [], []

    def on_event(event):
        if event["kind"] == "done":
            done.append(event["action"])
        elif event["kind"] == "stop":
            stops.append(event["reason"])

    try:
        ending = harness.run(
            goal, uia.GetRootControl(), lambda *a: harness.ask_openjev(*a, harness.MODEL),
            permit=lambda verb, control, text: control.ProcessId in mine, on_event=on_event,
        )
        success = reached(wanted, done) and (not check or check())
    except Exception as error:  # A crashed run is a failed one, and the app still has to close.
        ending, success, stops = "error", False, [repr(error)[:80]]
    finally:
        close_app(app)
    print(f"      {'OK  ' if success else 'MISS'} {ending}{' (' + stops[0] + ')' if stops else ''} {[d[:34] for d in done]}", flush=True)
    return success, len(done)


def row(results, optimal):
    wins = [turns for success, turns in results if success]
    exact = sum(turns == optimal for turns in wins)
    turns = f"{statistics.mean(wins):.2f}" if wins else "-"
    return f"{len(wins)}/{len(results)} succeeded, {exact} exact, {turns} turns (optimal {optimal})"


def main(repeats):
    harness.MIN_CONFIDENCE, harness.MAX_ACTIONS = MIN_CONFIDENCE, MAX_ACTIONS
    mine, windows, real_actions, real_show = set(), set(), harness.actions, uia.ShowWindow

    def actions(control):
        """Dropdowns open without asking, so keep the agent from opening any outside the test apps."""
        found = real_actions(control)
        if control.ProcessId not in mine:
            found.pop("expand", None)
        return found

    harness.actions = actions
    uia.ShowWindow = lambda handle, command: real_show(handle, command) if handle in windows else False
    results = {goal: [] for _, goal, _, _ in GOALS}
    with uia.UIAutomationInitializerInThread():
        for repeat in range(repeats):
            for app, goal, wanted, check in GOALS:
                print(f"\n[{repeat + 1}/{repeats}] {goal}", flush=True)
                result = attempt(app, goal, wanted, check, mine, windows)
                if result:
                    results[goal].append(result)
                else:
                    print("      SKIP app already open or not on this desktop", flush=True)
    print("\n===== results =====")
    for _, goal, wanted, _ in GOALS:
        print(f"{goal}\n  {row(results[goal], len(wanted))}")
    runs = [results[goal] for _, goal, wanted, _ in GOALS if len(wanted) > 1]
    wins = sum(success for goal_runs in runs for success, _ in goal_runs)
    print(f"multi-step goals together: {wins}/{sum(map(len, runs))} succeeded")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else REPEATS)
