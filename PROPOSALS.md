# Harness change proposals

Changes from the audit of 2026-11-12. Read `WORKFLOW.md` before working on one, update
`LEDGER.md` after every benchmark run, and remove a proposal from this file once its
verdict is recorded and its default is set.

| # | Change | Flag | Variant | Status |
|---|--------|------|---------|--------|
| 2 | Show each control's current state in its option | `SHOW_STATE` | `state` | built, off; sim: no change (0-0), awaiting Windows benchmark |
| 3 | A "back" option, so a wrong turn isn't the end of the run | `ALLOW_BACK` | `back` | built, off; sim: worse (2-4), awaiting Windows benchmark |

Proposal 1 (traces and named variants) is done and removed. Both changes here are in the
code, switched off, and pass `test_offline.py`. Neither has run against a real desktop or
model. What's left for each is the benchmark and a verdict.

## Run this first

On the Windows machine, with Character Map, Snipping Tool and Calculator closed:

```
python benchmark.py
```

That is 5 repeats of 9 goals for the baseline and all three variants, 180 runs. To run
one variant against the baseline: `python benchmark.py 5 state`.

Then, for each variant, follow "Deciding" in `WORKFLOW.md`, write the entry in
`LEDGER.md`, and either flip the flag's default and delete the old path, or delete the
new code.

## Check on the first run

These were written without a Windows desktop and are guesses about real controls. Each
fails loudly rather than scoring wrongly, but look for them:

- **Skipped goals.** The two already-done goals print
  `SKIP its starting state couldn't be read` if `benchmark.font()` can't find a ComboBox
  with a value in Character Map, or `benchmark.advanced_view_off()` can't find a toggle
  named "Advanced view" that is off. Fix the finder, or swap the goal for another control
  that is already set when its app opens.
- **`"seen": "unknown"` in the trace** on the video capture goal means the toggle named
  "Capture mode" wasn't found, and that goal is still being scored on action names alone.
- **`Now:` lines in the `state` variant's prompts.** Search a trace for `Now:`. If none
  appear, `harness.state` isn't reading the patterns these apps expose. If a dropdown's
  value is missing, it may need `SelectionPattern` instead of `ValuePattern`.
- **The app's tree** shows a `back` pick's score on that level's quit row, since back has
  no row of its own. Cosmetic, and only with `ALLOW_BACK` on.

## Proposal 2: Show each control's current state in its option

**Hypothesis.** The model repeats or reverses actions because it can't see what is
already set. Telling it will cut overshoot and make already-done goals end with no action.

**What was built**

- `harness.state(control, verb)`: `on`, `off` or `mixed` for toggle; `selected` for
  select; the quoted current value for expand and type. With `SHOW_STATE` on, `describe`
  appends ` Now: <state>.` to the option. `label` is untouched.
- Benchmark goals are now `Goal` tuples with `watch` (reads the screen before the run)
  and `check(done, seen)` (reads it after).
- The video capture goal now also needs the toggle to end up changed, so toggling twice
  no longer passes.
- Two already-done goals, optimal turns 0: the font is already what's asked, and Advanced
  view is already unchecked. They pass only if the model itself quits with nothing done
  and the control unchanged. A stop by the harness (low confidence, off-format) is a MISS.

**Judge it on** the two already-done goals and on exact counts for the multi-step goals.
If the baseline already passes the already-done goals every time, the hypothesis is
wrong: record that and delete `state`, `SHOW_STATE` and the variant.

**Risks.** Longer option lines on long lists. Values are app text, so they're quoted and
cut to `PREVIEW_NAME` characters.

## Proposal 3: A "back" option, so a wrong turn isn't the end of the run

**Hypothesis.** Some MISSes are runs that opened the wrong branch and then, correctly
seeing nothing useful there, quit. Separating "not here" from "finished" will turn those
into successes.

**Look at the baseline first.** In the trace, count baseline MISSes that ended with the
model picking quit, or with "nothing usable here", below the root and before every wanted
step was done. If that is under about a fifth of baseline MISSes there is little for this
to fix, and a small paired win is more likely noise.

**What was built** (all only with `ALLOW_BACK` on)

- `back` is offered after `quit` at every level below the root. Picking it returns to the
  level above, which stops offering the branch just left until the next action restarts
  the walk. No permission prompt, nothing added to the history.
- Backing out of a window the walk restored minimizes it again; out of a dropdown it
  expanded, collapses it.
- A dead end below the root goes back by itself instead of stopping the run. It counts as
  a step, so `MAX_STEPS` still bounds it.
- Where back is offered the prompt uses `INSTRUCTIONS_BACK`. At the root the original
  instructions stay, so quit still covers an unreachable goal. This differs from the
  first spec, which used the new text everywhere.
- Pick events now carry a copy of the path, since the path can shrink.

**Judge it on** the paired split, and on "how runs ended": fewer early quits is the
point, and more step-limit stops or more confidence-floor stops is the cost.

**Risks.** A third option at every level gives an unsure model another place to put
probability, lowering confidence where the baseline was right. Low-confidence stops still
end the run; if they dominate after this, going back on low confidence is the follow-up.

## Audit findings still open

- **Options past 52 are dropped silently**, last first. Listed in `LEDGER.md` as an idea.
- **Benchmark and default settings differ.** The benchmark runs at floor 0.25 and 10
  actions; the harness defaults are 0.5 and 5. Results describe the benchmark's settings.
- **Five goals still pass on action names alone**, with no on-screen check.
