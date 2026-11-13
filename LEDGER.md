# Ledger: what works and what doesn't

The record of design and prompting choices for the harness. Update it after every
experiment, including losses and inconclusive runs. See `WORKFLOW.md` for how to run one.

Evidence levels, strongest first:

- **measured**: benchmark numbers are in the experiment log below.
- **claimed**: a code comment says it was observed, but no numbers were kept.
- **reasoned**: follows from how the code or API works; not benchmarked.
- **untested**: an idea.

Nothing is **measured** yet. Seeded on 2026-11-12 from the code and comments at `c06d9b3`.

## Works (in the harness now)

| Method | Evidence | Note |
|--------|----------|------|
| One stateless choice per tree level, read from first-token logprobs | reasoned | Gives a score for every option and a confidence without parsing text. The core design. |
| Shuffle options on every call | claimed | "This kind of model leans toward the first option" (`ask_openjev`). Size of the lean wasn't recorded. |
| `quit` always offered, listed first before the shuffle | reasoned | The model always has a way to stop. |
| Restart from the desktop after each action | reasoned | The tree may have changed, so nothing stale is reused. Costs a full descent per action. |
| History as node path plus action (`HISTORY_PATHS = True`) | claimed | It's the default and the benchmark was built to compare it with action-only history. **The result was never written down.** The default benchmark run now compares the two; record it as the first log entry. |
| Preview of names inside a container on its `open` option | claimed | Lets unnamed containers be told apart. 24 names, 30 characters, 60 nodes visited. |
| Skip the model when the only move is to open a lone child | reasoned | Saves a call; no decision to make. |
| No `open` on items that can be invoked, toggled or expanded | claimed | Inside is only an icon and caption, a dead end. |
| `expand` treated as looking: no permission prompt, undone if nothing follows | claimed | Dropdown items don't exist in UIA until it opens, and it closes when focus moves. |
| Text supplied by the caller, typed at most once per prompt | reasoned | The model emits one token, so it can't write. |
| `think: false`, `num_predict: 1`, temperature 0 | reasoned | One token is the whole answer. |
| Confidence floor stops the run | claimed | 0.5 "set from 60 dry runs on one desktop". The benchmark uses 0.25. |
| `127.0.0.1` instead of `localhost` for Ollama | claimed | About 2 s saved per call on Windows. |
| Minimized windows restored without focus, and minimized again if unused | claimed | Store apps list nothing while minimized. |
| `GetChildren` with retries for listing | claimed | See `FindAll` below. |

## Doesn't work

| Method | Evidence | Note |
|--------|----------|------|
| `FindAll` to list children | claimed | Reads a flatter tree than the walk on 30% of nodes (`listed`). |
| Renormalising confidence over letter tokens only | reasoned, reproduced offline | A reply with 99% on a non-letter and 0.1% on `A` reports confidence 1.00. Still how confidence works; `letter_mass` is now returned and traced beside it so the size of the problem can be measured. A reply with no letter at all used to crash and now stops the run. |
| Substring matching on action names as a success check | reasoned | Passes a toggle flipped twice. The video capture goal now reads the toggle; five goals still rely on names alone. |
| Comparing against benchmark numbers from an earlier day | reasoned | The desktop, apps and model build drift. Pair within one run; shuffles are seeded per repeat and goal for that. |

## Known limits (accepted for now)

- Options past 52 at one level are dropped, last first.
- Past 20 options, confidence reads slightly high (Ollama's logprob cap).
- A menu that opens outside its button can't be reached.
- One confidence floor for every level, whatever the option count.
- No way back up the tree; a dead end or wrong branch ends the run. `ALLOW_BACK` changes this, off until benchmarked.
- The model sees no control state. `SHOW_STATE` changes this, off until benchmarked.

## Open ideas (untested)

Promote to `PROPOSALS.md` only with a failure class from a trace to justify it.

- Tournament over chunks when a level has more than 52 options (or more than 20, to stay
  inside the logprob cap).
- Ask the same level under several shuffles and average the scores, to cancel position
  bias. Costs a model call per shuffle.
- A floor on `letter_mass`, to stop on off-format replies.
- Scale the confidence floor by option count.
- Go back, instead of stopping, on a low-confidence step.
- End the prompt with an explicit answer cue so a bare letter is the likeliest next token.
- Tell the model what an action changed (the before and after state) in the history.
- Cache previews within one walk to cut UIA reads.

## Experiment log

Newest first. One entry per benchmark comparison.

```
### YYYY-MM-DD  <variant> vs baseline
Commit:      <sha>
Model:       <ollama model name and quant>
Settings:    floor <x>, actions <n>, repeats <n>
Hypothesis:  <one sentence>
Result:      baseline <s>/<n> succeeded, <e> exact | variant <s>/<n> succeeded, <e> exact
Paired:      <b>-<c> (variant only - baseline only)
By class:    <failure class counts that moved>
Verdict:     adopted | rejected | inconclusive
Notes:       <what surprised you; trace file name>
```

### 2026-11-13  `final command`, `state`, `back` vs baseline (simulated desktop)
Commit:      see `sim/results_real_variants.txt` (run on this branch's harness)
Model:       openjev-flash-9b, OpenJev-Flash-9B-Q4_K_M.gguf, Ollama on CPU
Settings:    floor 0.25, actions 10, repeats 3; `python sim/sim_benchmark.py 3 --real`
             (benchmark.py's 9 goals plus 4 sim extras, on simulated UIA trees, not Windows)
Result:      baseline 22/39 | final command 18/39 | state 22/39 | back 20/39 succeeded
Paired:      final command 1-5, state 0-0, back 2-4
By class:    final command: lost capture mode x2, Arial Black x1, both 4-step Calculator goals
             (quits early once history drops the path). back: capture mode 0/3 (toggles to the
             action limit), 1+1= up 1->2, but 9-4= and 2+3+4= stall after one press.
             state: every pick identical to baseline; "Now: off." was in the prompt and the
             model still toggled Advanced view on the "make sure it's unchecked" goal.
Verdict:     inconclusive (simulated desktop, n=3); no variant beats baseline. Keep all flags off.
Notes:       Both already-done goals fail 0/3 in every variant: the model always acts
             (re-selects Arial, toggles Advanced view). "Select twice" is 0/3 everywhere.
             The proxy-model run (`sim/results_proxy.txt`) can't judge state or back.
             Still owed: the Windows run.

### 2026-11-12  `state` and `back` built (no run yet)
Not an experiment. `SHOW_STATE` and `ALLOW_BACK` are in the harness, both off, with
variants `state` and `back`. The benchmark gained two already-done goals and an on-screen
check on the video capture goal. Checked offline only; the readers for real controls are
unverified, see "Check on the first run" in `PROPOSALS.md`. **Owed: verdicts for both,
from the same run as the baseline below.**

### 2026-11-12  Benchmark instrumented (no run yet)
Not an experiment. The benchmark now takes named variants, seeds the option shuffle per
repeat and goal, writes every decision to `runs/<time>.jsonl`, and prints how runs ended
and the paired split. Checked offline only. **Owed: one run of `python benchmark.py` on
Windows**, recorded here as the first baseline and the `HISTORY_PATHS` comparison, with
the spread of `letter_mass` from the trace.
