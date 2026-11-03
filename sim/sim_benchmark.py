"""benchmark.py's goals on simulated desktops: python sim/sim_benchmark.py [repeats] [--real]

With --real, decisions come from the real OpenJev model in a local Ollama instead of the proxy.

Runs where benchmark.py can't (no Windows, no UIA, no Ollama). The harness code is unchanged:
only uiautomation, the desktop and the model's HTTP endpoint are replaced (see desktop.py and
proxy_model.py). Scoring, settings and goals come from benchmark.py itself, and both history
modes meet the same seed, as benchmark.py pairs them on the same desktop.

Extra goals probe what benchmark.py doesn't: a toggle, another dropdown pick, a five-step
calculation and a font scrolled out of view. Then the success rate is swept over model noise
and the harness's own default confidence floor.
"""
import collections
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import winstub  # noqa: E402,F401
import uiautomation as uia  # noqa: E402

import benchmark  # noqa: E402
import desktop  # noqa: E402
import harness  # noqa: E402
import proxy_model  # noqa: E402

REPEATS = 30
REAL = False  # Set by --real.
TITLE = {benchmark.CHARMAP: "Character Map", benchmark.SNIPPING: "Snipping Tool", benchmark.CALCULATOR: "Calculator"}


def shows(value):
    def check():
        window = benchmark.top("Calculator")
        return bool(window) and window.TextControl(AutomationId="CalculatorResults").Name == f"Display is {value}"
    return check


def advanced_on():
    window = benchmark.top("Character Map")
    box = next((d for d in window.descendants() if d.Name == "Advanced view"), None) if window else None
    return bool(box) and box.GetPattern(uia.PatternId.TogglePattern).ToggleState == 1


EXTRA = [
    (benchmark.CHARMAP, "turn on Advanced view in Character Map", ['"Advanced view"'], advanced_on),
    (benchmark.SNIPPING, "set the snipping area to Full screen in Snipping Tool", ["Full screen"], None),
    (benchmark.CALCULATOR, "in Calculator, press 9 - 4 = to find the answer", ['"Nine"', '"Minus"', '"Four"', '"Equals"'], shows(5)),
    (benchmark.CALCULATOR, "in Calculator, press 2 + 3 + 4 = to find the answer", ['"Two"', '"Plus"', '"Three"', '"Plus"', '"Four"', '"Equals"'], shows(9)),
    (benchmark.CHARMAP, "set the font to Wingdings in Character Map", ["Wingdings"], None),
]


def attempt(app, goal, wanted, check, seed):
    """One scored run: (succeeded, turns, model calls, how it ended)."""
    root, mine, _ = desktop.desktop(TITLE[app])
    uia.GetRootControl = lambda: root
    if REAL:
        random.seed(seed)  # The harness shuffles options; this pairs both history modes on the same order.
        calls = [0]
    else:
        calls = proxy_model.install(seed)
    done, stops = [], []

    def on_event(event):
        if event["kind"] == "done":
            done.append(event["action"])
        elif event["kind"] == "stop":
            stops.append(event["reason"])

    real_actions = harness.actions

    def actions(control):  # As benchmark.py: no opening dropdowns outside the test app.
        found = real_actions(control)
        if control.ProcessId not in mine:
            found.pop("expand", None)
        return found

    def ask(*args):
        calls[0] += 1
        return harness.ask_openjev(*args, harness.MODEL)

    harness.actions = actions
    started = time.monotonic()
    try:
        ending = harness.run(goal, root, ask,
                             permit=lambda verb, control, text: control.ProcessId in mine, on_event=on_event)
    finally:
        harness.actions = real_actions
    success = benchmark.reached(wanted, done) and (not check or check())
    reason = ending if ending != "quit" or not stops else ("low confidence" if stops[0].startswith("confidence") else stops[0].removeprefix("reached the ").split(" of ")[0])
    if not success and reason == "quit":
        reason = "quit early" if not benchmark.reached(wanted, done) else "wrong result"
    if REAL:
        seconds = time.monotonic() - started
        print(f"      {'OK  ' if success else 'MISS'} {'paths' if harness.HISTORY_PATHS else 'final'} {reason} {calls[0]} calls "
              f"{seconds:.0f}s {[d[:40] for d in done]} | {goal}", flush=True)
    return success, len(done), calls[0], reason


def suite(goals, repeats, histories=benchmark.HISTORIES, seed0=0):
    results = {(goal, name): [] for _, goal, _, _ in goals for name in histories}
    for repeat in range(repeats):
        for g, (app, goal, wanted, check) in enumerate(goals):
            for name, paths in histories.items():
                harness.HISTORY_PATHS = paths
                results[goal, name].append(attempt(app, goal, wanted, check, seed=seed0 + repeat * 1000 + g))
    return results


def report(goals, results, histories=benchmark.HISTORIES):
    for _, goal, wanted, _ in goals:
        print(goal)
        for name in histories:
            runs = results[goal, name]
            endings = collections.Counter(r[3] for r in runs if not r[0])
            calls = sum(r[2] for r in runs) / len(runs)
            misses = ", ".join(f"{k} {v}" for k, v in endings.most_common()) or "-"
            print(f"  {benchmark.row(name, [r[:2] for r in runs], len(wanted))}, {calls:.1f} calls | misses: {misses}")


def rate(results, name):
    runs = [r for (goal, n), rs in results.items() if n == name for r in rs]
    return sum(r[0] for r in runs) / len(runs)


def quiet(event):
    pass


def main(repeats):
    harness.MIN_CONFIDENCE, harness.MAX_ACTIONS = benchmark.MIN_CONFIDENCE, benchmark.MAX_ACTIONS
    harness.ACTION_WAIT = 0
    model = f"real model {harness.MODEL}" if REAL else f"proxy model, noise {proxy_model.NOISE}"
    print(f"settings: floor {harness.MIN_CONFIDENCE}, action limit {harness.MAX_ACTIONS}, step limit {harness.MAX_STEPS}, "
          f"{model}, {repeats} repeats\n", flush=True)
    print("===== benchmark.py goals =====")
    main_results = suite(benchmark.GOALS, repeats)
    report(benchmark.GOALS, main_results)
    print("multi-step goals together")
    for name in benchmark.HISTORIES:
        runs = [r for _, goal, wanted, _ in benchmark.GOALS if len(wanted) > 1 for r in main_results[goal, name]]
        print(f"  {name:14} {sum(r[0] for r in runs)}/{len(runs)} succeeded")

    print("\n===== extra goals =====")
    report(EXTRA, suite(EXTRA, repeats, seed0=10 ** 6))
    if REAL:
        return

    sweep_repeats = max(1, repeats // 3)
    print(f"\n===== success on benchmark.py goals by model noise ({sweep_repeats} repeats each) =====")
    print(f"{'noise':>6} {'floor':>6}  " + "  ".join(f"{n:>14}" for n in benchmark.HISTORIES))
    for floor in (benchmark.MIN_CONFIDENCE, 0.5):
        for noise in (0.0, 0.5, 1.0, 1.5, 2.0):
            proxy_model.NOISE, harness.MIN_CONFIDENCE = noise, floor
            results = suite(benchmark.GOALS, sweep_repeats, seed0=2 * 10 ** 6)
            print(f"{noise:>6} {floor:>6}  " + "  ".join(f"{rate(results, n):>14.0%}" for n in benchmark.HISTORIES))


if __name__ == "__main__":
    harness.print = lambda *a, **k: None  # The harness's own "dropping options" notes.
    REAL = "--real" in sys.argv
    numbers = [a for a in sys.argv[1:] if a.isdigit()]
    main(int(numbers[0]) if numbers else REPEATS)
