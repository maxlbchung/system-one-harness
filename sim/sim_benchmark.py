"""benchmark.py's goals on simulated desktops: python sim/sim_benchmark.py [repeats] [--real] [--variants=a,b]

Runs where benchmark.py can't (no Windows, no UIA). The harness code is unchanged: only
uiautomation, the desktop and, without --real, the model's HTTP endpoint are replaced (see
desktop.py and proxy_model.py). With --real, decisions come from the real OpenJev model in a
local Ollama. Goals, variants, settings and scoring come from benchmark.py. On top of each
goal's own check, every goal gets an on-screen check (the right item picked, the right number
of Select clicks), and five extra goals probe what benchmark.py doesn't.

The proxy model is lexical: it ignores state text and never picks "back", so only --real can
judge the state and back variants.
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

REPEATS = 20
REAL = False  # Set by --real.
Goal = benchmark.Goal


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
SCREEN = {  # On-screen results for benchmark.py's goals, which mostly check only the action text.
    "press the Select button in Character Map": copied(1),
    "set the snip delay to 5 seconds in Snipping Tool": picked("Snipping Tool", DELAY, "Delay snip for 5 seconds"),
    "turn on video capture mode in Snipping Tool": toggled("Snipping Tool", "Capture mode"),
    "set the font to Arial Black in Character Map and then press the Select button": copied(1, "Arial Black"),
    "set the snip delay to 3 seconds and set the snipping area to Window in Snipping Tool":
        both(picked("Snipping Tool", DELAY, "Delay snip for 3 seconds"), picked("Snipping Tool", AREA, "Window")),
    "press the Select button in Character Map, then press Select again": copied(2),
    "in Calculator, press 1 + 1 = to find the answer": shows(2),
}
EXTRA = [
    Goal(benchmark.CHARMAP, "turn on Advanced view in Character Map", ['"Advanced view"']),
    Goal(benchmark.SNIPPING, "set the snipping area to Full screen in Snipping Tool", ["Full screen"]),
    Goal(benchmark.CALCULATOR, "in Calculator, press 9 - 4 = to find the answer", ['"Nine"', '"Minus"', '"Four"', '"Equals"']),
    Goal(benchmark.CALCULATOR, "in Calculator, press 2 + 3 + 4 = to find the answer", ['"Two"', '"Plus"', '"Three"', '"Plus"', '"Four"', '"Equals"']),
    Goal(benchmark.CHARMAP, "set the font to Wingdings in Character Map", ["Wingdings"]),
]
SCREEN |= {
    EXTRA[0].text: toggled("Character Map", "Advanced view"),
    EXTRA[1].text: picked("Snipping Tool", AREA, "Full screen"),
    EXTRA[2].text: shows(5),
    EXTRA[3].text: shows(9),
    EXTRA[4].text: picked("Character Map", "105", "Wingdings"),
}
UNREACHABLE = {EXTRA[4].text}  # Offscreen, with no scroll action. --real skips it to save hours.


def attempt(goal, seed, variant):
    """One scored run, as benchmark.attempt scores it: (succeeded, turns, how it ended, model calls)."""
    root, mine, _ = desktop.desktop(goal.app.title)
    uia.GetRootControl = lambda: root
    harness.SHUFFLE.seed(seed)  # The same option order for every variant, as benchmark.py does.
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

    harness.actions = actions
    started = time.monotonic()
    seen = goal.watch() if goal.watch else None
    try:
        ending = harness.run(goal.text.format(seen=seen), root, ask, permit=lambda verb, control, text: control.ProcessId in mine, on_event=on_event)
    except Exception as error:  # noqa: BLE001  A crashed run is a failed one, as in benchmark.py.
        ending, stops = "error", [repr(error)[:80]]
    finally:
        harness.actions = real_actions
    success = ending != "error" and benchmark.reached(goal.wanted, done) and (not goal.check or bool(goal.check(done, seen)))
    success = success and (bool(goal.wanted) or (ending == "quit" and not stops))
    screen = SCREEN.get(goal.text)
    success = success and (not screen or screen())
    how = benchmark.outcome(ending, stops)
    if REAL:
        print(f"      {'OK  ' if success else 'MISS'} {variant:13} {how} {calls[0]} calls {time.monotonic() - started:.0f}s "
              f"{[d[:40] for d in done]} | {goal.text}", flush=True)
    return success, len(done), how, calls[0]


def suite(goals, repeats, variants, originals, seed0=0):
    results = {(goal.text, v): [] for goal in goals for v in variants}
    for repeat in range(repeats):
        for g, goal in enumerate(goals):
            for v in variants:
                benchmark.apply(benchmark.VARIANTS[v], originals)
                results[goal.text, v].append(attempt(goal, seed0 + repeat * 1000 + g, v))
    benchmark.apply({}, originals)
    return results


def report(goals, results, variants):
    for goal in goals:
        print(goal.text)
        for v in variants:
            runs = results[goal.text, v]
            misses = collections.Counter(r[2] for r in runs if not r[0])
            calls = sum(r[3] for r in runs) / len(runs)
            print(f"  {benchmark.row(v, [r[:3] for r in runs], len(goal.wanted))}, {calls:.1f} calls"
                  f" | misses: {', '.join(f'{k} {n}' for k, n in misses.most_common()) or '-'}")


def totals(goals, results, variants):
    print("all goals")
    for v in variants:
        runs = [r for goal in goals for r in results[goal.text, v]]
        wins = sum(r[0] and not b[0] for goal in goals for r, b in zip(results[goal.text, v], results[goal.text, benchmark.BASELINE]))
        losses = sum(b[0] and not r[0] for goal in goals for r, b in zip(results[goal.text, v], results[goal.text, benchmark.BASELINE]))
        print(f"  {v:14} {sum(r[0] for r in runs)}/{len(runs)} succeeded, {sum(r[3] for r in runs) / len(runs):.1f} calls per run, "
              f"paired vs baseline {wins}-{losses}")


def main(repeats, variants):
    harness.MIN_CONFIDENCE, harness.MAX_ACTIONS = benchmark.MIN_CONFIDENCE, benchmark.MAX_ACTIONS
    harness.ACTION_WAIT = 0
    variants = [benchmark.BASELINE] + [v for v in variants if v != benchmark.BASELINE]
    originals = {c: getattr(harness, c) for v in variants for c in benchmark.VARIANTS[v]}
    model = f"real model {harness.MODEL}" if REAL else f"proxy model, noise {proxy_model.NOISE}"
    print(f"settings: floor {harness.MIN_CONFIDENCE}, action limit {harness.MAX_ACTIONS}, step limit {harness.MAX_STEPS}, "
          f"{model}, {repeats} repeats, variants {', '.join(variants)}\n", flush=True)
    goals = benchmark.GOALS + [g for g in EXTRA if not (REAL and g.text in UNREACHABLE)]
    results = suite(goals, repeats, variants, originals)
    report(goals, results, variants)
    print()
    totals(goals, results, variants)


if __name__ == "__main__":
    harness.print = lambda *a, **k: None  # The harness's own "dropping options" notes.
    REAL = "--real" in sys.argv
    numbers = [a for a in sys.argv[1:] if a.isdigit()]
    chosen = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--variants=")), ",".join(benchmark.VARIANTS)).split(",")
    main(int(numbers[0]) if numbers else REPEATS, chosen)
