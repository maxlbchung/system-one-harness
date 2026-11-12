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
| History as node path plus action (`HISTORY_PATHS = True`) | claimed | It's the default and the benchmark was built to compare it with action-only history. **The result was never written down.** First thing to record once Proposal 1 lands. |
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
| Renormalising confidence over letter tokens only | reasoned, reproduced offline | A reply with 99% on a non-letter and 0.1% on `A` reports confidence 1.00. Crashes when no letter is in the top 20. Fix is Proposal 1. |
| Substring matching on action names as a success check | reasoned | Passes a toggle flipped twice. Proposal 2, step A. |
| Comparing against benchmark numbers from an earlier day | reasoned | Nothing is seeded or saved, and the desktop drifts. Pair within one run. |

## Known limits (accepted for now)

- Options past 52 at one level are dropped, last first.
- Past 20 options, confidence reads slightly high (Ollama's logprob cap).
- A menu that opens outside its button can't be reached.
- One confidence floor for every level, whatever the option count.
- No way back up the tree; a dead end or wrong branch ends the run (Proposal 3).
- The model sees no control state (Proposal 2).

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

No entries yet.
