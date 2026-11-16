"""Charts for EXPERIMENTS.md from experiments.csv: python sim/charts.py experiments.csv docs/charts (needs matplotlib)"""
import collections
import csv
import os
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402

rows = list(csv.DictReader(open(sys.argv[1])))
out = sys.argv[2]
os.makedirs(out, exist_ok=True)

SURFACE, INK, INK2, MUTED, GRID, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
BLUE, ORANGE, NEUTRAL = "#2a78d6", "#eb6834", "#c3c2b7"
RAMP = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "text.color": INK, "axes.labelcolor": INK2,
                     "xtick.color": MUTED, "ytick.color": INK2, "axes.edgecolor": AXIS, "figure.facecolor": SURFACE,
                     "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE, "figure.dpi": 150})


def style(ax, grid_axis="x"):
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.spines["left" if grid_axis == "x" else "bottom"].set_color(AXIS)
    ax.grid(axis=grid_axis, color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    ax.tick_params(length=0)


def rate(subset):
    return sum(int(r["success"]) for r in subset) / len(subset) if subset else 0


real = [r for r in rows if r["model"] == "real" and not r["experiment"].startswith("R0")]
# Latest measurement of each config: R2/R3 supersede R1's Calculator rows.
CONFIGS = [("baseline", "R2"), ("state", "R2"), ("instructions", "R1"), ("resume", "R1"), ("effects", "R2"),
           ("state+effects", "R2"), ("done-check", "R2"), ("state+done-check", "R2"), ("effects+done-check", "R3")]


def runs(variant, experiment):
    found = [r for r in real if r["variant"] == variant and r["experiment"].startswith(experiment)]
    if experiment == "R1":  # Its Calculator rows ran on the older simulated Calculator.
        found = [r for r in found if "Calculator" not in r["goal"]]
    return found


# 1. Success by configuration, all goals, latest round for each.
fig, ax = plt.subplots(figsize=(7.2, 3.8))
names, values, counts = [], [], []
for variant, experiment in [c for c in CONFIGS if c[1] != "R1"]:  # R1's two lack current Calculator runs.
    found = runs(variant, experiment)
    names.append(variant)
    values.append(rate(found) * 100)
    counts.append(f"{sum(int(r['success']) for r in found)}/{len(found)}")
order = sorted(range(len(names)), key=lambda i: values[i])
colors = [NEUTRAL if names[i] == "baseline" else BLUE for i in order]
bars = ax.barh([names[i] for i in order], [values[i] for i in order], color=colors, height=0.62)
for bar, i in zip(bars, order):
    ax.text(bar.get_width() + 1, bar.get_y() + bar.get_height() / 2, f"{values[i]:.0f}%  ({counts[i]})", va="center", color=INK2, fontsize=9)
base = values[names.index("baseline")]
ax.axvline(base, color=MUTED, linewidth=1, linestyle=(0, (3, 3)))
ax.set_xlim(0, 100)
ax.set_xlabel("Goals passed, % of 33 runs (real model, 11 goals × 3)")
ax.set_title("Harness configurations against the baseline", loc="left", fontsize=12, color=INK, pad=12)
style(ax)
fig.tight_layout()
fig.savefig(f"{out}/success_by_config.png")
plt.close(fig)

# 2. Per-goal heatmap.
goals = list(dict.fromkeys(r["goal"] for r in rows if r["experiment"].startswith("R2")))
SHORT = {
    "press the Select button in Character Map": "Select button",
    "set the snip delay to 5 seconds in Snipping Tool": "Delay 5 s",
    "turn on video capture mode in Snipping Tool": "Video capture mode",
    "set the font to Arial Black in Character Map and then press the Select button": "Arial Black, then Select",
    "set the snip delay to 3 seconds and set the snipping area to Window in Snipping Tool": "Delay 3 s + Window",
    "press the Select button in Character Map, then press Select again": "Select twice",
    "in Calculator, press 1 + 1 = to find the answer": "Calc 1 + 1 =",
    "turn on Advanced view in Character Map": "Advanced view",
    "set the snipping area to Full screen in Snipping Tool": "Full screen",
    "in Calculator, press 9 - 4 = to find the answer": "Calc 9 - 4 =",
    "in Calculator, press 2 + 3 + 4 = to find the answer": "Calc 2 + 3 + 4 =",
}
heat_configs = [c for c in CONFIGS if c[1] != "R1"]
grid = [[rate([r for r in runs(v, e) if r["goal"] == g]) for v, e in heat_configs] for g in goals]
fig, ax = plt.subplots(figsize=(8.6, 5.4))
cmap = LinearSegmentedColormap.from_list("blue", RAMP)
ax.imshow(grid, cmap=cmap, vmin=0, vmax=1, aspect="auto")
for y, row in enumerate(grid):
    for x, value in enumerate(row):
        n = len([r for r in runs(heat_configs[x][0], heat_configs[x][1]) if r["goal"] == goals[y]])
        ax.text(x, y, f"{round(value * n)}/{n}", ha="center", va="center", fontsize=9, color="#ffffff" if value > 0.5 else INK)
ax.set_xticks(range(len(heat_configs)), [v for v, _ in heat_configs], rotation=30, ha="right")
ax.set_yticks(range(len(goals)), [SHORT.get(g, g) for g in goals])
ax.set_xticks([x - 0.5 for x in range(1, len(heat_configs))], minor=True)
ax.set_yticks([y - 0.5 for y in range(1, len(goals))], minor=True)
ax.grid(which="minor", color=SURFACE, linewidth=2)
ax.tick_params(which="both", length=0)
for spine in ax.spines.values():
    spine.set_visible(False)
ax.set_title("Runs passed per goal (real model, 3 runs each)", loc="left", fontsize=12, pad=12)
fig.tight_layout()
fig.savefig(f"{out}/per_goal_heatmap.png")
plt.close(fig)

# 3. Proxy noise sweep.
sweep = [r for r in rows if r["experiment"].startswith("P2")]
fig, ax = plt.subplots(figsize=(6.4, 3.8))
for floor, color, label in (("0.25", BLUE, "floor 0.25 (benchmark)"), ("0.5", ORANGE, "floor 0.5 (harness default)")):
    noises = sorted({float(r["noise"]) for r in sweep})
    ys = [rate([r for r in sweep if float(r["noise"]) == n and float(r["floor"]) == float(floor)]) * 100 for n in noises]
    ax.plot(noises, ys, color=color, linewidth=2, marker="o", markersize=6, label=label)
    ax.text(noises[-1] + 0.06, ys[-1], f"{ys[-1]:.0f}%", va="center", color=INK2, fontsize=9)
ax.set_ylim(0, 105)
ax.set_xlim(-0.1, 2.35)
ax.set_xlabel("Proxy model noise (logit standard deviation)")
ax.set_ylabel("Goals passed, %")
ax.legend(frameon=False, loc="lower left")
ax.set_title("A higher confidence floor stops good runs once the model is noisy", loc="left", fontsize=11, pad=12)
style(ax, "y")
fig.tight_layout()
fig.savefig(f"{out}/noise_sweep.png")
plt.close(fig)

# 4. History modes (R0).
history = [r for r in rows if r["experiment"].startswith("R0")]
fig, ax = plt.subplots(figsize=(6.4, 2.4))
labels, vals, texts = [], [], []
for variant in ("final-command", "node-path"):
    multi = [r for r in history if r["variant"] == variant and int(r["optimal"]) > 1 and r["goal_set"] == "benchmark"]
    labels.append(variant)
    vals.append(rate(multi) * 100)
    texts.append(f"{sum(int(r['success']) for r in multi)}/{len(multi)}")
bars = ax.barh(labels, vals, color=[NEUTRAL, BLUE], height=0.55)
for bar, v, t in zip(bars, vals, texts):
    ax.text(bar.get_width() + 1, bar.get_y() + bar.get_height() / 2, f"{v:.0f}%  ({t})", va="center", color=INK2, fontsize=9)
ax.set_xlim(0, 100)
ax.set_xlabel("Multi-step benchmark goals passed, % (real model, R0)")
ax.set_title("History that shows the path to each action wins", loc="left", fontsize=11, pad=10)
style(ax)
fig.tight_layout()
fig.savefig(f"{out}/history_modes.png")
plt.close(fig)
print("charts written")
