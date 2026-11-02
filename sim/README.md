# Simulated benchmark

`benchmark.py` needs Windows UI Automation, the real apps and OpenJev in Ollama. This folder runs the same goals without any of them:

- `winstub.py`: stand-ins for `uiautomation` and `_ctypes.COMError`, so `harness.py` and `benchmark.py` import unchanged.
- `desktop.py`: simulated UIA trees for Character Map, Snipping Tool and Calculator, plus Edge, File Explorer, the taskbar and Program Manager as distractors. Acting on a control changes state: a pick closes its dropdown, Close removes the window, Calculator computes.
- `proxy_model.py`: replaces `urlopen`, so `harness.ask_openjev` runs unchanged (prompt, shuffle, logprobs). It's a stateless lexical chooser with logit noise and a first-option bias. With noise 0 it solves every goal the tree allows.
- `sim_benchmark.py`: `benchmark.py`'s goals, settings and scoring, both history modes on paired seeds, 5 extra goals and a sweep over noise and confidence floor.

```bash
python sim/sim_benchmark.py 60
```

```bash
python sim/run_tests.py
```

These results measure the harness's tree handling, prompt, history and stopping logic with a fixed, imperfect chooser. They don't measure OpenJev's accuracy. Full output: `results.txt`.

## Results (60 repeats, noise 1.0, floor 0.25, action limit 10)

| Goal | final command | node path |
|---|---|---|
| Select button | 60/60 | 60/60 |
| Delay 5 s | 56/60 | 56/60 |
| Video capture mode | 60/60 (1.72 turns) | 59/60 (1.05 turns) |
| Arial Black, then Select | **10/60** | **58/60** |
| Delay 3 s + area Window | 41/60 (4.02 turns) | 48/60 (2.08 turns) |
| Select twice | 60/60 | 59/60 |
| Calculator 1 + 1 = | 37/60 | 29/60 |
| **Multi-step total** | **148/240** | **194/240** |

Sweep over all 7 goals:

| noise | floor 0.25, final / path | floor 0.5 (harness default), final / path |
|---|---|---|
| 0.0 | 86% / 100% | 83% / 100% |
| 1.0 | 79% / 88% | 58% / 72% |
| 2.0 | 63% / 66% | 48% / 39% |

## Findings

1. **Node-path history beats final-command history** (+19 points on multi-step goals, with near-optimal turns). With final commands only, `Select ListItem "Arial Black"` doesn't say it was the font, so the font step still looks undone. It fails even with a noise-free chooser.
2. **A floor of 0.5 costs 16–21 points** once the chooser is noisy (noise 1.0), and gains nothing when it isn't. Confidence also drops when two containers are both right, like "Number pad" and "Standard operators", so a high floor stops good runs. The benchmark's 0.25 is the better setting.
3. **Long goals compound errors.** Each action restarts the walk at the desktop, so 1 + 1 = takes about 16–18 model calls, and one wrong press can't be undone. In Calculator, 76 of 90 misses started with a correct button pressed out of order. The proxy's weak sense of order inflates this, but the harness also has no way to see the result: the display isn't in the prompt, only the action history.
4. **Stray actions end the run.** Up to 15% of runs per goal pick something in another window (taskbar running-app buttons, Edge's or the app's own title-bar Close). `permit` refuses it and the run ends as "denied" instead of trying again.
5. **Items scrolled out of view can't be reached.** `children()` drops offscreen items and there's no scroll action, so "set the font to Wingdings" fails 60/60.
6. **The preview cutoff hides deep targets** (Calculator's buttons are past the 24-name breadth-first preview, which window chrome and memory buttons fill). Raising it to 40 didn't change success here, so it isn't the bottleneck with this chooser.
