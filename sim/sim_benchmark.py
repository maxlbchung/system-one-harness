"""benchmark.py's goals on simulated desktops: python sim/sim_benchmark.py [repeats] [--real] [--variants a,b]

Runs where benchmark.py can't (no Windows, no UIA). The harness code is unchanged: only
uiautomation, the desktop and, without --real, the model's HTTP endpoint are replaced (see
desktop.py and proxy_model.py). With --real, decisions come from the real OpenJev model in a
local Ollama. Goals, settings and scoring come from benchmark.py, plus an on-screen check for
every goal, so a run that toggles twice or clicks once too often doesn't pass on its action text.

Variants are harness experiments compared against the baseline on the same seeds. Extra goals
probe what benchmark.py doesn't. Without --real, the baseline is also swept over model noise and
the harness's own default confidence floor.
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

INSTRUCTIONS = ("Choose the best next step toward the question. Done so far lists the actions already taken: don't redo one "
                "unless the question asks for it again. Quit only when every step the question asks for is in Done so far, "
                "or if the goal is unreachable.")
VARIANTS = {
    "baseline": {},
    "state": {"SHOW_STATE": True},
    "instructions": {"INSTRUCTIONS": INSTRUCTIONS},
    "resume": {"RESUME": True},
    "effects": {"SHOW_EFFECTS": True},
    "state+effects": {"SHOW_STATE": True, "SHOW_EFFECTS": True},
    "done-check": {"DONE_CHECK": True},
    "state+done-check": {"SHOW_STATE": True, "DONE_CHECK": True},
}


def find(title, name=None, id=None):
    window = benchmark.top(title)
    return next((d for d in window.descendants() if (name is None or d.Name == name) and (id is None or d.AutomationId == id)), None) if window else None


def shows(value):
    def check():
        display = find("Calculator", id="CalculatorResults")
        return bool(display) and display.Name == f"Display is {value}"
    return check


def toggled(title, name):
    def check():
        box = find(title, name)
        return bool(box) and box.GetPattern(uia.PatternId.TogglePattern).ToggleState == 1
    return check


def picked(title, id, choice):
    def check():
        combo = find(title, id=id)
        return bool(combo) and combo.selected == choice
    return check


def copied(count, font=None):
    """Character Map's Select adds a character each time, so the box shows how many clicks there were."""
    def check():
        box = find("Character Map", id="104")
        return bool(box) and box.GetPattern(uia.PatternId.ValuePattern).Value == "!" * count and (not font or picked("Character Map", "105", font)())
    return check


def both(*checks):
    return lambda: all(check() for check in checks)


DELAY, AREA = "SnipDelayComboBox", "SnippingAreaComboBox"
CHECKS = {  # On-screen results for benchmark.py's goals, which mostly check only the action text.
    "press the Select button in Character Map": copied(1),
    "set the snip delay to 5 seconds in Snipping Tool": picked("Snipping Tool", DELAY, "Delay snip for 5 seconds"),
    "turn on video capture mode in Snipping Tool": toggled("Snipping Tool", "Capture mode"),
    "set the font to Arial Black in Character Map and then press the Select button": copied(1, "Arial Black"),
    "set the snip delay to 3 seconds and set the snipping area to Window in Snipping Tool":
        both(picked("Snipping Tool", DELAY, "Delay snip for 3 seconds"), picked("Snipping Tool", AREA, "Window")),
    "press the Select button in Character Map, then press Select again": copied(2),
    "in Calculator, press 1 + 1 = to find the answer": shows(2),
}
GOALS = [(app, goal, wanted, CHECKS.get(goal, check)) for app, goal, wanted, check in benchmark.GOALS]
EXTRA = [
    (benchmark.CHARMAP, "turn on Advanced view in Character Map", ['"Advanced view"'], toggled("Character Map", "Advanced view")),
    (benchmark.SNIPPING, "set the snipping area to Full screen in Snipping Tool", ["Full screen"], picked("Snipping Tool", AREA, "Full screen")),
    (benchmark.CALCULATOR, "in Calculator, press 9 - 4 = to find the answer", ['"Nine"', '"Minus"', '"Four"', '"Equals"'], shows(5)),
    (benchmark.CALCULATOR, "in Calculator, press 2 + 3 + 4 = to find the answer", ['"Two"', '"Plus"', '"Three"', '"Plus"', '"Four"', '"Equals"'], shows(9)),
    (benchmark.CHARMAP, "set the font to Wingdings in Character Map", ["Wingdings"], picked("Character Map", "105", "Wingdings")),
]


UNREACHABLE = {"set the font to Wingdings in Character Map"}  # Offscreen, with no scroll action. --real skips it to save hours.


def attempt(app, goal, wanted, check, seed, variant):
    """One scored run: (succeeded, turns, model calls, how it ended)."""
    root, mine, _ = desktop.desktop(TITLE[app])
    uia.GetRootControl = lambda: root
    random.seed(seed)  # The harness shuffles options; this gives every variant the same order to start.
    calls = [0] if REAL else proxy_model.install(seed)
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
        if REAL:  # The proxy counts its own.
            calls[0] += 1
        return harness.ask_openjev(*args, harness.MODEL)

    def judge(*args):
        if REAL:
            calls[0] += 1
        return harness.judge_openjev(*args, harness.MODEL)

    harness.actions = actions
    started = time.monotonic()
    try:
        ending = harness.run(goal, root, ask, permit=lambda verb, control, text: control.ProcessId in mine, on_event=on_event, judge=judge)
    finally:
        harness.actions = real_actions
    success = benchmark.reached(wanted, done) and (not check or check())
    reason = ending if ending != "quit" or not stops else ("low confidence" if stops[0].startswith("confidence") else "judged done" if stops[0].startswith("judged") else stops[0].removeprefix("reached the ").split(" of ")[0])
    if not success and reason in ("quit", "judged done"):
        reason = f"{reason} early" if not benchmark.reached(wanted, done) else "wrong result"
    if REAL:
        seconds = time.monotonic() - started
        print(f"      {'OK  ' if success else 'MISS'} {variant} {reason} {calls[0]} calls {seconds:.0f}s {[d[:40] for d in done]} | {goal}", flush=True)
    return success, len(done), calls[0], reason


def apply(variant):
    """Harness settings for a variant, on top of the defaults."""
    for name, value in DEFAULTS.items():
        setattr(harness, name, value)
    for name, value in VARIANTS[variant].items():
        setattr(harness, name, value)


def suite(goals, repeats, variants, seed0=0):
    results = {(goal, v): [] for _, goal, _, _ in goals for v in variants}
    for repeat in range(repeats):
        for g, (app, goal, wanted, check) in enumerate(goals):
            for v in variants:
                apply(v)
                results[goal, v].append(attempt(app, goal, wanted, check, seed0 + repeat * 1000 + g, v))
    apply("baseline")
    return results


def report(goals, results, variants):
    for _, goal, wanted, _ in goals:
        print(goal)
        for v in variants:
            runs = results[goal, v]
            endings = collections.Counter(r[3] for r in runs if not r[0])
            calls = sum(r[2] for r in runs) / len(runs)
            misses = ", ".join(f"{k} {n}" for k, n in endings.most_common()) or "-"
            print(f"  {v:16} {benchmark.row([r[:2] for r in runs], len(wanted))}, {calls:.1f} calls | misses: {misses}")


def totals(title, goals, results, variants):
    print(title)
    for v in variants:
        runs = [r for _, goal, _, _ in goals for r in results[goal, v]]
        print(f"  {v:16} {sum(r[0] for r in runs)}/{len(runs)} succeeded, {sum(r[2] for r in runs) / len(runs):.1f} calls per run")


def main(repeats, variants):
    harness.MIN_CONFIDENCE, harness.MAX_ACTIONS = benchmark.MIN_CONFIDENCE, benchmark.MAX_ACTIONS
    harness.ACTION_WAIT = 0
    model = f"real model {harness.MODEL}" if REAL else f"proxy model, noise {proxy_model.NOISE}"
    print(f"settings: floor {harness.MIN_CONFIDENCE}, action limit {harness.MAX_ACTIONS}, step limit {harness.MAX_STEPS}, "
          f"{model}, {repeats} repeats, variants {', '.join(variants)}\n", flush=True)
    print("===== benchmark.py goals =====")
    main_results = suite(GOALS, repeats, variants)
    report(GOALS, main_results, variants)
    print("\n===== extra goals =====")
    extra = [g for g in EXTRA if not (REAL and g[1] in UNREACHABLE)]
    extra_results = suite(extra, repeats, variants, seed0=10 ** 6)
    report(extra, extra_results, variants)
    print()
    totals("benchmark.py multi-step goals", [g for g in GOALS if len(g[2]) > 1], main_results, variants)
    totals("all goals", GOALS + extra, main_results | extra_results, variants)
    if REAL:
        return

    sweep_repeats = max(1, repeats // 3)
    print(f"\n===== baseline success on benchmark.py goals by model noise ({sweep_repeats} repeats each) =====")
    print(f"{'noise':>6} {'floor 0.25':>11} {'floor 0.5':>10}")
    for noise in (0.0, 0.5, 1.0, 1.5, 2.0):
        rates = []
        for floor in (benchmark.MIN_CONFIDENCE, 0.5):
            proxy_model.NOISE = noise
            harness.MIN_CONFIDENCE = floor
            results = suite(GOALS, sweep_repeats, ["baseline"], seed0=2 * 10 ** 6)
            rates.append(sum(r[0] for rs in results.values() for r in rs) / sum(map(len, results.values())))
            harness.MIN_CONFIDENCE = benchmark.MIN_CONFIDENCE
        print(f"{noise:>6} {rates[0]:>11.0%} {rates[1]:>10.0%}")


DEFAULTS = {name: getattr(harness, name) for v in VARIANTS.values() for name in v}

if __name__ == "__main__":
    harness.print = lambda *a, **k: None  # The harness's own "dropping options" notes.
    REAL = "--real" in sys.argv
    numbers = [a for a in sys.argv[1:] if a.isdigit()]
    chosen = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--variants=")), ",".join(VARIANTS)).split(",")
    main(int(numbers[0]) if numbers else REPEATS, chosen)
