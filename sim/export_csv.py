"""Every run of every experiment as one CSV row: python sim/export_csv.py experiments.csv

Real-model runs come from the results files (R0 from git history, since its history mode is gone).
Proxy runs are regenerated, which is deterministic for a seed. Needs matplotlib only for charts.py.
"""
import ast
import csv
import os
import subprocess
import sys

REPO = "/home/user/system-one-harness"
sys.path[:0] = [os.path.join(REPO, "sim"), REPO]
import sim_benchmark as s  # noqa: E402
import benchmark  # noqa: E402
import harness  # noqa: E402
import proxy_model  # noqa: E402

out = sys.argv[1]
FIELDS = ["experiment", "model", "variant", "noise", "floor", "goal", "goal_set", "repeat", "seed", "success", "turns",
          "optimal", "exact", "calls", "ending", "seconds", "scoring", "notes", "actions"]
GOAL_INDEX = {goal: ("benchmark", i, wanted) for i, (_, goal, wanted, _) in enumerate(s.GOALS)}
GOAL_INDEX |= {goal: ("extra", i, wanted) for i, (_, goal, wanted, _) in enumerate(s.EXTRA)}
VARIANT_NAMES = {"final": "final-command", "paths": "node-path"}


def NOTES(experiment, goal, variant):
    if experiment.startswith("R0"):
        return "only Calculator and Advanced view had on-screen checks; final-command history has since been removed"
    if experiment.startswith("R1") and "Calculator" in goal:
        return "older simulated Calculator (display showed the operator); superseded by R2"
    if experiment.startswith("R2") and "Calculator" not in goal and variant in ("baseline", "state"):
        return "baseline and state rows outside Calculator are the R1 runs, reused (deterministic replay)"
    return ""



def real_rows(experiment, text):
    seen = {}
    for line in text.splitlines():
        if not line.startswith("      ") or " | " not in line:
            continue
        head, goal = line.split(" | ", 1)
        parts = head.split()
        variant = VARIANT_NAMES.get(parts[1], parts[1])
        i = parts.index("calls")
        done = ast.literal_eval(head[head.index("["):])
        goal_set, g, wanted = GOAL_INDEX[goal]
        repeat = seen[goal, variant] = seen.get((goal, variant), -1) + 1
        ok = parts[0] == "OK"
        yield {"experiment": experiment, "model": "real", "variant": variant, "noise": "", "floor": benchmark.MIN_CONFIDENCE,
               "goal": goal, "goal_set": goal_set, "repeat": repeat,
               "seed": (10 ** 6 if goal_set == "extra" else 0) + repeat * 1000 + g,
               "success": int(ok), "turns": len(done), "optimal": len(wanted), "exact": int(ok and len(done) == len(wanted)),
               "calls": int(parts[i - 1]), "ending": "success" if ok else " ".join(parts[2:i - 1]),
               "seconds": parts[i + 1].rstrip("s"), "actions": " ; ".join(done),
               "scoring": "action text" if experiment.startswith("R0") else "action text + on-screen check",
               "notes": NOTES(experiment, goal, variant)}


def git(path, rev="HEAD"):
    return subprocess.run(["git", "-C", REPO, "show", f"{rev}:{path}"], capture_output=True, text=True, check=True).stdout


rows = []
rows += real_rows("R0 history modes", git("sim/results_real.txt", "e133c99"))
rows += real_rows("R1 state, instructions, resume", git("sim/results_real_round1.txt"))
rows += real_rows("R2 effects, done-check", git("sim/results_real_round2.txt"))
rows += real_rows("R3 effects + done-check", git("sim/results_real_round3.txt"))

# Proxy runs, regenerated with the current code (deterministic for a seed).
harness.print = lambda *a, **k: None
harness.MIN_CONFIDENCE, harness.MAX_ACTIONS, harness.ACTION_WAIT = benchmark.MIN_CONFIDENCE, benchmark.MAX_ACTIONS, 0


def proxy(experiment, goals, goal_set, variants, repeats, seed0, noise, floor):
    proxy_model.NOISE, harness.MIN_CONFIDENCE = noise, floor
    for repeat in range(repeats):
        for g, (app, goal, wanted, check) in enumerate(goals):
            for v in variants:
                s.apply(v)
                seed = seed0 + repeat * 1000 + g
                ok, turns, calls, ending = s.attempt(app, goal, wanted, check, seed, v)
                rows.append({"experiment": experiment, "model": "proxy", "variant": v, "noise": noise, "floor": floor,
                             "goal": goal, "goal_set": goal_set, "repeat": repeat, "seed": seed, "success": int(ok),
                             "turns": turns, "optimal": len(wanted), "exact": int(ok and turns == len(wanted)), "calls": calls,
                             "ending": "success" if ok else ending, "seconds": "", "actions": "",
                             "scoring": "action text + on-screen check", "notes": "proxy model (lexical stand-in, not OpenJev)"})
    s.apply("baseline")
    proxy_model.NOISE, harness.MIN_CONFIDENCE = 1.0, benchmark.MIN_CONFIDENCE


variants = list(s.VARIANTS)
proxy("P1 proxy variants", s.GOALS, "benchmark", variants, 20, 0, 1.0, benchmark.MIN_CONFIDENCE)
proxy("P1 proxy variants", s.EXTRA, "extra", variants, 20, 10 ** 6, 1.0, benchmark.MIN_CONFIDENCE)
for noise in (0.0, 0.5, 1.0, 1.5, 2.0):
    for floor in (benchmark.MIN_CONFIDENCE, 0.5):
        proxy("P2 proxy noise sweep", s.GOALS, "benchmark", ["baseline"], 20, 2 * 10 ** 6, noise, floor)

with open(out, "w", newline="") as f:
    writer = csv.DictWriter(f, FIELDS)
    writer.writeheader()
    writer.writerows(rows)
print(len(rows), "rows")
