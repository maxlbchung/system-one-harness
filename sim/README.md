# Simulated benchmark

`benchmark.py` needs Windows UI Automation and the real apps. This folder runs the same goals without them:

- `winstub.py`: stand-ins for `uiautomation` and `_ctypes.COMError`, so `harness.py` and `benchmark.py` import unchanged.
- `desktop.py`: simulated UIA trees for Character Map, Snipping Tool and Calculator, plus Edge, File Explorer, the taskbar and Program Manager as distractors. Acting on a control changes state: a pick closes its dropdown, Close removes the window, Calculator computes.
- `proxy_model.py`: replaces `urlopen`, so `harness.ask_openjev` runs unchanged (prompt, shuffle, logprobs). It's a stateless lexical chooser with logit noise and a first-option bias. With noise 0 it solves every goal the tree allows.
- `sim_benchmark.py`: `benchmark.py`'s goals, settings and scoring, 5 extra goals, harness variants compared on the same seeds, and a sweep over noise and confidence floor. Every goal also gets an on-screen check (toggle on, the right item picked, the right number of Select clicks), because `benchmark.py` mostly checks only action text, which passes a run that toggled twice.

```bash
python sim/sim_benchmark.py 20
```

```bash
python sim/sim_benchmark.py 3 --real
```

```bash
python sim/run_tests.py
```

`--real` asks OpenJev-Flash-9B Q4_K_M in a local Ollama (CPU only, 5–10 s per decision). Start Ollama with `LLAMA_ARG_CACHE_RAM=0`: with the prompt cache on, llama-server grew to 13.9 GB and was OOM-killed. `--variants=baseline,resume` picks variants.

## Current variants

`sim_benchmark.py` now runs `benchmark.py`'s own goals, variants (`final command`, `state`,
`back`) and scoring, plus on-screen checks and 5 extra goals. Results on the real model:
`results_real_variants.txt` (summary in `LEDGER.md`). Proxy model: `results_proxy.txt`.

Real model setup on Linux: download `OpenJev-Flash-9B-Q4_K_M.gguf` from
huggingface.co/openjev/OpenJev-Flash-9B-GGUF, `ollama create openjev-flash-9b -f Modelfile`
with `FROM ./OpenJev-Flash-9B-Q4_K_M.gguf`, and serve with `LLAMA_ARG_CACHE_RAM=0`.
About 8 s per decision on 4 CPU cores; 3 repeats of 4 variants take about 3.5 hours.

## Earlier rounds (older harness fork, flags no longer in this harness)

## Variants

Harness flags, all off by default, each aimed at the biggest real-model failures. Round 1 tried the first three, round 2 the last two:

| Variant | Change | Aimed at |
|---|---|---|
| `state` | `SHOW_STATE`: labels carry toggle states and values (`(on)`, `value="Arial"`), the history records what an action set, and the location lists read-only text such as `Display is 2`. | Toggles pressed again and again; no feedback from Calculator |
| `instructions` | A new `INSTRUCTIONS`: don't redo an action unless the question asks for it again, and quit only when every step is in Done so far. | Early quits; "Select twice" |
| `resume` | `RESUME`: after an action, carry on where it happened, with a "back" option, instead of from the desktop. | 14+ calls per Calculator goal, each one a chance to go wrong |
| `effects` | `SHOW_EFFECTS`: snapshot the app window before and after each action, and add what changed to that action's history entry, such as `Click Button "Select" (changed: Edit "Characters to copy :" empty -> value="!")`. | The model judging progress from its actions alone |
| `done-check` | `DONE_CHECK`: quit leaves the option list. After each action, a separate yes/no question asks whether every part of the question is done. | Quitting as one option among dozens |

## Real model, round 1: state, instructions, resume

3 runs per goal and variant, on the same seeds. Wingdings is skipped because no variant can reach an offscreen item. Full output: `results_real_round1.txt`. Round 2 changed the simulated Calculator, so its Calculator rows supersede these.

| Goal | baseline | state | instructions | resume |
|---|---|---|---|---|
| Select button | 3/3 | 3/3 | 3/3 | 0/3 |
| Delay 5 s | 3/3 | 3/3 | 3/3 | 3/3 |
| Video capture mode | 2/3 | 3/3 | 2/3 | 0/3 |
| Arial Black, then Select | 3/3 | 2/3 | 1/3 | 1/3 |
| Delay 3 s + area Window | 3/3 | 3/3 | 3/3 | 3/3 |
| Select twice | 0/3 | 0/3 | 0/3 | 0/3 |
| Calculator 1 + 1 = | 1/3 | 1/3 | 2/3 | 0/3 |
| Advanced view | 3/3 | 3/3 | 3/3 | 3/3 |
| Full screen | 3/3 | 3/3 | 2/3 | 3/3 |
| Calculator 9 - 4 = | 1/3 | 1/3 | 0/3 | 0/3 |
| Calculator 2 + 3 + 4 = | 0/3 | 0/3 | 0/3 | 0/3 |
| **All goals** | **22/33** | **22/33** | **19/33** | **13/33** |
| Calls per run | 9.3 | 8.6 | 7.7 | 8.7 |

**None of the three beats the baseline.** Keep all three flags off.

1. **`state` ties (22/33).** The model sees `(on)` and `Display is 2` but doesn't act on them. It still toggled capture mode 3, 9 and 5 times (baseline: 5, 9, 10), and all 3 runs passed only because the counts happened to be odd. On every Calculator goal it made exactly the baseline's choices, so the display text never changed a pick. It's cheap and harmless, but this model doesn't use it.
2. **`instructions` is worse (19/33).** It got one more Calculator 1 + 1 = right, but it quit before acting on "Select twice" in all 3 runs, and lost two Arial Black runs. The stricter quit rule didn't stop early quits on the other Calculator goals.
3. **`resume` is much worse (13/33).** Left where it just acted, the model presses the same button again instead of quitting or going back: Select 10 times, capture mode 10 times, One until the action limit. Restarting from the desktop is what makes it re-read the goal and stop. Calls per run didn't drop either, because the runs ran to the action limit.

Three runs per cell is a small sample, so 1-run differences are noise. The `resume` drop and the `instructions` "Select twice" collapse were consistent in every run.

## Real model, round 2: effects and done-check, each on baseline and state

The same setup. The simulated Calculator now matches Windows: after an operator the display keeps the number, and an "Expression is 1 +" line appears. So baseline and `state` were rerun on Calculator, and their other results reused from round 1 (spot checks replayed identically). Full output: `results_real_round2.txt`.

| Goal | baseline | state | effects | state+effects | done-check | state+done-check |
|---|---|---|---|---|---|---|
| Select button | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| Delay 5 s | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| Video capture mode | 2/3 (7.0 turns) | 3/3 (5.7) | **3/3 (1.0)** | 2/3 (5.0) | 2/3 (2.0) | 3/3 (2.7) |
| Arial Black, then Select | 3/3 | 2/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| Delay 3 s + area Window | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| Select twice | 0/3 | 0/3 | **2/3** | **2/3** | 0/3 | 0/3 |
| Calculator 1 + 1 = | 1/3 | 1/3 | 1/3 | 1/3 | 1/3 | 0/3 |
| Advanced view | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| Full screen | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| Calculator 9 - 4 = | 1/3 | 1/3 | 1/3 | 1/3 | **3/3** | **3/3** |
| Calculator 2 + 3 + 4 = | 0/3 | 0/3 | **2/3** | **2/3** | 0/3 | 0/3 |
| **All goals** | **22/33** | **22/33** | **27/33** | **26/33** | **24/33** | **24/33** |
| Calls per run | 10.2 | 9.9 | 9.4 | 10.5 | 11.8 | 11.0 |

**`effects` is the first change to beat the baseline: 27/33 against 22/33, with fewer calls per run.** Turn `SHOW_EFFECTS` on.

1. **Seeing results fixes the repeat failures.** With "Capture mode (off) -> (on)" in the history, the model toggled once and stopped in all 3 runs. It used to toggle 5–10 times. "Select twice" went from 0/3 to 2/3, because "Characters to copy: value="!"" after one click shows one is not two. 2 + 3 + 4 = went from 0/3 to 2/3, the first time anything solved it. Both "Select twice" wins clicked Copy first, so they weren't exact.
2. **`done-check` helps where effects don't, and fails where they do (24/33).** Asked on its own, "is it done?" kept Calculator 9 - 4 = going to the end in 3/3 runs (baseline 1/3). But the judge sees only the history, without effects: it said "done" after one Select on "Select twice" in all 3 runs. On 2 + 3 + 4 = the model pressed an extra Plus after Four in all 3 runs, before Equals, and got the wrong result. It also costs about 1.5 more calls per run.
3. **`state` adds nothing on top of either change** (26/33 with effects, 24/33 with done-check). It's the same story as round 1: the model ignores state in labels, but uses the same information once it's written as a change in the history.

The two changes look complementary: the done judge reads the history, so it should get effects too. Combining `effects` + `done-check` is the obvious next test. The remaining misses are mostly Calculator 1 + 1 =, at 1/3 everywhere.

## Proxy model: baseline (20 repeats, noise 1.0, floor 0.25, action limit 10)

The proxy ignores wording and states, and never picks "back", so it can't judge the variants. It's useful for the tree and stopping logic. Full output: `results.txt`.

| Goal | baseline |
|---|---|
| Select button | 20/20 |
| Delay 5 s | 19/20 |
| Video capture mode | 19/20 |
| Arial Black, then Select | 19/20 |
| Delay 3 s + area Window | 16/20 |
| Select twice | 19/20 |
| Calculator 1 + 1 = | 12/20 |
| Calculator 9 - 4 = / 2 + 3 + 4 = | 13/20, 5/20 |
| Wingdings (offscreen) | 0/20 |

Sweep over `benchmark.py`'s 7 goals:

| noise | floor 0.25 | floor 0.5 (harness default) |
|---|---|---|
| 0.0 | 100% | 100% |
| 1.0 | 88% | 76% |
| 2.0 | 69% | 50% |

## Findings that hold for both models

1. **A floor of 0.5 costs 12–19 points** once the chooser is noisy, and gains nothing when it isn't. Confidence also drops when two containers are both right, like "Number pad" and "Standard operators". The benchmark's 0.25 is the better setting.
2. **Long goals compound errors.** Each action restarts at the desktop, so 1 + 1 = takes about 15 model calls, and one wrong press can't be undone.
3. **Stray actions end the run.** Some runs pick something in another window (taskbar running-app buttons, a title bar's Close). `permit` refuses it and the run ends as "denied" instead of trying again.
4. **Items scrolled out of view can't be reached.** `children()` drops offscreen items and there's no scroll action, so "set the font to Wingdings" always fails.
5. **`benchmark.py` scores by action text,** so a toggle pressed an even number of times still passes. The real model did that in 1 of 3 baseline capture-mode runs here, and 2 of 5 in an earlier run. A state check like `shows_two` would catch it on Windows too.
