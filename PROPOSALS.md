# Harness change proposals

Three changes from the audit of 2026-11-12 (commit `c06d9b3`). Each is a self-contained
task for one agent. Read `WORKFLOW.md` before starting and update `LEDGER.md` when done.

| # | Change | Kind | Depends on | Status |
|---|--------|------|------------|--------|
| 1 | Decision traces and named variants in the benchmark | measurement | none | open |
| 2 | Show each control's current state in its option | prompt | 1 | open |
| 3 | A "back" option, so a wrong turn isn't the end of the run | control flow | 1 | open |

Do 1 first. 2 and 3 are behaviour changes, and without 1 there is no way to tell whether
they helped. 2 and 3 are independent of each other; benchmark them as separate variants,
never together in a first run.

## Audit findings

What the proposals are based on. Line numbers are for `c06d9b3`.

**Verified by running code** (offline, against a stubbed model reply):

- **F1. Off-format answers crash.** `harness.py:232-233`. If none of the top 20 tokens is
  a bare option letter (for example the model's first token is `[` or ` A`), `probs` is
  empty and `max()` raises `ValueError`. The benchmark swallows it as a generic `error` MISS.
- **F2. Confidence hides off-format answers.** `harness.py:232-238`. Scores are
  renormalised over letter tokens only. A reply with 99% on `!` and 0.1% on `A` reports
  choice `A` with confidence 1.00. The floor can't catch a model that didn't want to
  answer with a letter at all.

**Read from the code** (not yet measured on a desktop):

- **F3. The benchmark keeps nothing.** `benchmark.py` prints one line per run. Prompts,
  scores, confidences and per-step picks are discarded, so a MISS can't be diagnosed
  afterwards and no past result exists in the repo, including the one that made
  `HISTORY_PATHS = True` the default.
- **F4. Variants are hardcoded.** `benchmark.py:20,141-142` can only compare the two
  history formats. Testing any other mutation means editing the benchmark's loop.
- **F5. Runs aren't reproducible.** `harness.py:213` shuffles with an unseeded RNG.
  Temperature is 0, so the shuffle is the only source of variance between repeats, and the
  two variants in a pair see different shuffles.
- **F6. The model can't see state.** `VERBS` describes toggle as "Switch this item on or
  off" without saying which it is now. Selected items, current dropdown values and text
  box contents are also invisible. The only state the model has is its own history, so
  "turn on X" when X is already on gets toggled off.
- **F7. Success checks are loose.** Six of seven goals pass on substring matches against
  action names (`benchmark.py:41-49`). "turn on video capture mode" passes if any action
  mentions "Capture mode", including toggling it twice. No goal has a correct answer of
  "do nothing".
- **F8. "quit" means two things.** `INSTRUCTIONS` says quit when done *or* when the goal
  is unreachable. Deep in the wrong branch, the goal is unreachable *from here*, so quit is
  the right pick locally and ends the whole run. There is no way back up
  (`test_harness.py:166`), and a dead end or one low-confidence step also ends the run.
- **F9. Options past 52 are dropped silently**, last first (`harness.py:201-204`). Not
  proposed here; listed in `LEDGER.md` as an open idea.
- **F10. Benchmark and default settings differ.** The benchmark runs at floor 0.25 and
  10 actions; the harness defaults are 0.5 and 5. Benchmark results describe the
  benchmark's settings only.

---

## Proposal 1: Decision traces and named variants in the benchmark

**Fixes** F1, F2, F3, F4, F5. **Files** `harness.py`, `benchmark.py`, `test_harness.py`,
`.gitignore`, `app.py` (one guard).

**Why.** Every later change needs a way to be switched on by name, compared against the
baseline on the same shuffles, and diagnosed from a saved trace when it misses.

**Changes**

1. `ask_openjev` returns two more keys: `prompt` (the text sent) and `letter_mass` (sum of
   letter-token probabilities before renormalising, 0 to 1).
2. When no letter is in the top 20, `ask_openjev` returns `{"choice": None, "confidence": 0.0,
   "scores": {}, "letter_mass": 0.0, "prompt": ...}` instead of raising. `walk` checks for
   `choice is None` before the pick event, emits
   `{"kind": "stop", "reason": "the model didn't answer with an option"}` and returns `"quit"`.
   No pick event is emitted, so `app.py` never sees empty scores.
3. `ask_openjev` shuffles with a `random.Random` instance held in a module variable
   (`SHUFFLE = random.Random()`), so the benchmark can reseed it without touching the
   global RNG.
4. In `benchmark.py`, replace `HISTORIES` with
   `VARIANTS = {"baseline": {}, "final command": {"HISTORY_PATHS": False}}`: name to a dict
   of harness constants to override. Before each attempt, reset every overridden constant
   to its saved original, apply the variant's overrides, and call
   `harness.SHUFFLE.seed(f"{repeat}/{goal}")` so both variants of a pair start on the same
   shuffles. Accept variant names on the command line:
   `python benchmark.py [repeats] [variant ...]`, always including `baseline`.
5. Write a trace to `runs/<YYYYMMDD-HHMMSS>.jsonl` (add `runs/` to `.gitignore`). One JSON
   line per event:
   - `pick`: variant, repeat, goal, path, choice, confidence, letter_mass, auto, scores,
     prompt, option count.
   - `done`, `stop`: variant, repeat, goal, plus the event's own fields.
   - `result` (one per attempt): variant, repeat, goal, success, ending, stop reason,
     actions list, turns, optimal.
   Controls aren't JSON; log names only.
6. After the results table, print per variant: a count of each ending and stop reason, and
   the paired outcome against baseline as `wins-losses` over attempts where exactly one of
   the pair succeeded (see `WORKFLOW.md`, "Deciding").

**Tests to add** (`test_harness.py`, fake `urlopen`):

- A reply with no letter token returns `choice None` and a run on it stops with the new reason
  and takes no action.
- A reply with `("!", 0.99), (letter, 0.001)` reports `letter_mass` near 0.001.
- Two calls after `harness.SHUFFLE.seed("x")` give the same option order.

**Done when** `python test_offline.py` prints `ok`, and one benchmark run on Windows
writes a trace file whose `result` lines match the printed table. Paste that table into
`LEDGER.md` as the first recorded baseline, with the `HISTORY_PATHS` comparison.

**Not in scope** Changing the floor to use `letter_mass`. Record its distribution first;
if off-format replies turn out to be real, propose a floor on it then.

---

## Proposal 2: Show each control's current state in its option

**Fixes** F6, F7. **Files** `harness.py`, `benchmark.py`, `test_harness.py`.

**Hypothesis.** The model repeats or reverses actions because it can't see what is
already set. Telling it the current state will cut overshoot (runs that succeed but take
more than the optimal turns) and make "already done" goals end with no action.

**Step A: make the benchmark able to see the failure** (land and baseline this before B)

1. Let a goal's check see the run: call `check(done)` instead of `check()`.
2. Add an end-state check to "turn on video capture mode": read the toggle's
   `TogglePattern.ToggleState` after the run.
3. Add two goals whose right answer is no action, checked by `done == []`:
   one for a dropdown already at the asked value, one for a toggle already in the asked
   state. Pick them from the three test apps on the Windows machine and confirm the
   default state there; for example "make sure the snip delay is off in Snipping Tool" if
   its default is no delay. Their optimal turns is 0.
4. Run the benchmark. Record in `LEDGER.md` how baseline does on the new goals. If it
   already passes them all, stop: the hypothesis is wrong, record that and close this.

**Step B: the change**

1. Add `state(control, verb)` to `harness.py`, returning a short string or `""`:
   - toggle: `on`, `off` or `mixed` from `TogglePattern.ToggleState`
   - select: `selected` when `SelectionItemPattern.IsSelected`
   - expand: the current value, from `ValuePattern.Value` if the control has one
   - type: the current text from `ValuePattern.Value`, cut to `PREVIEW_NAME` characters
   Wrap reads in `except COMError` and return `""`.
2. In `describe`, append ` Now: {state}.` when state isn't empty and `SHOW_STATE` is true.
   Add `SHOW_STATE = False` as a module constant.
3. Put state in `describe` only, **not** in `label`. `label` feeds the path, the history,
   the permission prompt, the app's tree and the benchmark's `wanted` matches; changing it
   breaks all five.
4. Add variant `"state": {"SHOW_STATE": True}` to the benchmark.

**Tests to add.** `test_harness.py` replaces `harness.actions`; do the same for
`harness.state` (`lambda control, verb: control.states.get(verb, "")`). Assert the
prompt contains `Now: on.` for a toggle with the flag on, nothing with it off, and that
`label` output is unchanged either way.

**Done when** baseline and `state` have been run paired, the result is in `LEDGER.md`,
and `SHOW_STATE`'s default matches the verdict.

**Risks.** Longer option lines dilute attention on long lists. Values may contain text
that reads like an instruction; they are cut short and quoted for that reason.

---

## Proposal 3: A "back" option, so a wrong turn isn't the end of the run

**Fixes** F8. **Files** `harness.py`, `test_harness.py`, `benchmark.py`.

**Hypothesis.** Some MISSes are runs that opened the wrong branch and then, correctly
seeing nothing useful there, quit. Separating "not here" from "finished" will turn those
into successes.

**Gate: check the baseline first.** From a Proposal 1 trace, count MISS attempts that
ended by the model picking quit, or by "nothing usable here", at a path deeper than the
root and before every wanted step was done. If that is under about a fifth of all MISSes,
don't build this; record the count in `LEDGER.md` and close it.

**Changes**

1. Add a verb: `"back": "What's needed isn't here. Go back and look elsewhere."`
2. `options` takes the depth and offers `back` after `quit` when depth > 0 and
   `ALLOW_BACK` is true. Add `ALLOW_BACK = False` as a module constant.
3. `walk` keeps a stack of `(node, path)` instead of only the current pair. On `back`: pop
   to the parent, and add the node just left to a `skipped` set that `options` leaves out
   for the rest of this walk, so the model can't bounce between two branches. `skipped`
   clears on restart. Compare elements with `uia.ControlsAreSame`, not `is` (wrappers
   differ per read).
4. Going back out of something the walk restored or expanded reverses it: pop and run its
   `undo` entry.
5. A dead end ("nothing usable here") at depth > 0 with `ALLOW_BACK` on goes back
   automatically instead of stopping. It still counts as a step.
6. With `ALLOW_BACK` on, use this instruction text:
   `"Choose the best next step. Don't repeat what's done. Go back if what's needed isn't here. Quit only when all of it is done."`
   Keep the current text when it's off. At the root there is no back, so quit still covers
   "unreachable".
7. `back` is a look, not an action: no permission prompt, nothing added to `done`.
   `MAX_STEPS` already bounds it.
8. Add variant `"back": {"ALLOW_BACK": True}`.

**Tests to add**

- Wrong branch, then back, then the right branch, reaches the action; the skipped branch
  is missing from the second listing at the parent.
- `back` isn't offered at the root, or anywhere with the flag off.
- Back out of a restored minimized window minimizes it again; out of an expanded dropdown
  collapses it.
- A dead end at depth 2 backs out and the run continues; with the flag off the existing
  dead-end test still passes unchanged.
- Update the auto-descend check (`harness.py:281`): a lone child plus `back` should still
  descend without a model call.

**Done when** paired results for baseline and `back` are in `LEDGER.md`, with the stop
reason counts side by side, and `ALLOW_BACK`'s default matches the verdict.

**Risks.** A third always-present option gives an unsure model another place to put
probability: watch for more step-limit stops and for lower confidence at levels where the
baseline was right. Low-confidence stops are deliberately left alone here; if they
dominate the MISSes after this, going back on low confidence is the follow-up.
