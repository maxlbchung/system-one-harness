# How to evaluate and change the harness

For any agent or person changing `harness.py`. The loop is: measure, pick one failure,
change one thing, measure again paired against the baseline, record the result either way.

## What you can run where

| Check | Command | Needs | Tells you |
|-------|---------|-------|-----------|
| Mechanics | `python test_offline.py` | any OS, no installs | the walk, limits, undo and prompt format still work |
| Mechanics | `python test_harness.py`, `python test_app.py` | Windows, `uiautomation` | same, with the real UIA module |
| Quality | `python benchmark.py [repeats] [variant ...]` | Windows desktop, Ollama, the model | whether the agent reaches real goals |
| By eye | `pythonw app.py` | same as benchmark | where on the tree a run goes wrong |

The mechanics tests use a scripted stand-in for the model. Passing them says nothing about
whether a change helps. Only the benchmark does.

**If you are an agent in a cloud or Linux session, you can't run the benchmark.** Make the
change behind a flag (below), get `test_offline.py` green, push, and tell the user the
exact benchmark command to run. Leave the proposal's status as "awaiting benchmark" and
the flag off. Don't flip a default or write a verdict in `LEDGER.md` without numbers from
a real run.

## Evaluating

1. Close Character Map, Snipping Tool and Calculator. The benchmark skips any app that is
   already open, because that one is yours.
2. Run `python benchmark.py`, or name variants to run only those against the baseline
   (`python benchmark.py 5 "final command"`). It opens each test app, runs each goal once
   per variant back to back on the same option shuffles, and only permits actions inside
   the apps it opened.
3. Read three numbers per goal and variant: **succeeded** (all wanted steps happened in
   order and the screen check passed), **exact** (also in the optimal number of turns),
   and **turns**.
4. Read "how runs ended" and the paired line under the table.
5. Classify every MISS before changing anything, from the trace in `runs/<time>.jsonl`.
   Each line is one event with its variant, repeat and goal: `pick` (path, choice,
   confidence, `letter_mass`, scores, the full prompt), `done`, `stop`, and one `result`
   per run. `runs/` isn't committed, so name the trace file in the ledger entry and copy
   the numbers that matter.

| Failure class | How it shows | Usual lever |
|---------------|--------------|-------------|
| Wrong branch | opens a container that can't hold the target | previews, labels, a way back |
| Wrong action | right place, wrong control or verb | verb descriptions, state |
| Premature quit | quits before every step is done | history format, instructions |
| Overshoot | succeeds but takes extra turns, or hits the action limit | history, state, quit wording |
| Unsure | stops under the confidence floor | option count, prompt clarity, floor |
| Off-format | no letter in the reply, or low `letter_mass` | answer format |
| Not offered | the target wasn't in the options (offscreen, over 52, not listed) | `options`, `children` |
| Environment | COM error, app didn't open, timing | waits, retries. Not a model problem |

Change the thing behind the most common class. One MISS is an anecdote.

## Changing

1. **One variable per experiment.** If two ideas interact, test each alone first.
2. **Put it behind a module constant** in `harness.py`, default off, and add an entry to
   `VARIANTS` in `benchmark.py` (name to the constants it overrides) so the benchmark can
   flip it by name. Text changes count: keep the old string and select by flag.
3. **If the benchmark can't show the failure you're fixing, add the goal first** and
   record how the baseline does on it. A change that targets a failure no goal exposes
   can't be evaluated. Prefer goals with an on-screen check over name matching.
4. **Add a mechanics test** for the new behaviour, on and off.
5. **Run baseline and the variant in the same benchmark run.** Never compare against
   numbers from another day: the desktop, the app versions and the model build all drift.
6. **Record it in `LEDGER.md`**, win, loss or inconclusive.
7. **Clean up.** Winner: flip the default, delete the losing code path and its flag unless
   it's still worth benchmarking. Loser: delete the code, keep the ledger entry.

### Things that break quietly

- **`label()` is load-bearing.** It feeds the path, the history, the permission prompt,
  the app's tree and the benchmark's `wanted` matches. Put new per-option text in
  `describe()` instead.
- **The answer must stay one token.** `num_predict` is 1 and the choice is read from the
  first token's logprobs. Option keys have to be single tokens for the model, and the
  prompt has to end so that a bare letter is the natural next token.
- **Ollama returns at most 20 logprobs.** Past 20 options, unlisted options have no score
  and confidence reads slightly high.
- **Confidence is relative to the option count.** The same floor is stricter on a
  50-option level than a 3-option one. A change that adds an option to every level moves
  every confidence.
- **Every option line costs a model's attention and UIA reads.** `preview` walks up to 60
  nodes per container on every call.
- **The benchmark overrides settings** (floor 0.25, 10 actions). A change tuned there may
  behave differently at the defaults (0.5, 5).
- **The agent must never reach its own windows** (`OWN_PROCESS` in `listed`) and looking
  must leave nothing behind (`undo`). Any new way to move around the tree has to keep both.

## Deciding

The default run is 5 repeats of 9 goals: 45 paired attempts per variant. That is small, so compare
pairs, not totals. Count the pairs where exactly one side succeeded: `b` where only the
variant did, `c` where only the baseline did. The benchmark prints this as `b-c`.

- **Adopt** if the split is at least 4-0, 6-1 or 7-2 (one-sided sign test, p < 0.1), and
  no single goal went from mostly passing to mostly failing.
- **Reject** if `c > b`.
- **Otherwise inconclusive.** Run 10 repeats, or record it as inconclusive and move on.
  Don't adopt on a totals difference of one or two runs.
- Equal on success: prefer more exact runs, then fewer turns, then less code.

A change aimed at one failure class should also be judged on that class's count, not only
on overall success.

## Safety when experimenting

- Benchmark only through `benchmark.py`, which refuses actions outside the apps it opened.
- Don't use `--yes` on a desktop with real work open.
- New benchmark apps must be ones the benchmark can start and kill itself, with nothing to
  lose in them.
