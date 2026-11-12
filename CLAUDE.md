# system-one-harness

A computer-use agent that makes one stateless model choice per level of the Windows UIA tree.
`harness.py` is the agent, `app.py` its desktop UI, `benchmark.py` the real-desktop evaluation.

Before changing `harness.py` or `benchmark.py`:

- `WORKFLOW.md`: how to evaluate a change and how to make one. Follow it.
- `PROPOSALS.md`: the queued changes, with specs. Update the status table when you take or finish one.
- `LEDGER.md`: what has worked and what hasn't. Add an entry after every experiment, whatever the result.

Run `python test_offline.py` after every change; it works on any OS. The benchmark needs
Windows, Ollama and the model, so from a cloud session leave new behaviour behind a flag,
default off, and ask the user to run it.
