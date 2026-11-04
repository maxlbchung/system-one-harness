"""System one computer use: one stateless OpenJev choice per UIA tree level.

Each call sees only the goal, what's been done so far, its location and the options
there. After each action the walk restarts from the desktop, until the model quits
or a limit is reached.

Setup: pip install uiautomation, download OpenJev-Flash-9B-Q4_K_M.gguf from
huggingface.co/openjev/OpenJev-Flash-9B-GGUF, write a Modelfile containing
"FROM ./OpenJev-Flash-9B-Q4_K_M.gguf", then run
"ollama create openjev-flash-9b -f Modelfile".
"""
import argparse
import json
import math
import os
import random
import string
import time
import urllib.request
from _ctypes import COMError

import uiautomation as uia

# 127.0.0.1, because localhost costs about 2s per call on Windows.
OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
MODEL = "openjev-flash-9b"
MODEL_TIMEOUT = 120  # Seconds. Loading the model cold took 13.
MAX_ACTIONS, MAX_STEPS = 5, 60  # Per prompt. A step is one decision, by the model or the harness.
ACTION_WAIT = 1  # Seconds for the screen to catch up before the next walk reads it.
OWN_PROCESS = os.getpid()
READ_ATTEMPTS, READ_RETRY = 3, 0.05  # Tries, seconds between them.
RESTORE_SETTLE, RESTORE_WAIT = 0.25, 2  # Seconds. Store apps list nothing inside until they're restored.
LETTERS = string.ascii_uppercase + string.ascii_lowercase
MAX_OPTIONS = len(LETTERS)  # Each option needs a one-token letter.
MAX_NAME = 100
PREVIEW_NAMES, PREVIEW_NAME, PREVIEW_VISITS = 24, 30, 60
# ponytail: one floor for every step, set from 60 dry runs on one desktop, retune on wider goals
MIN_CONFIDENCE = 0.5

INSTRUCTIONS = "Choose the best next step. Don't repeat what's done. Quit only when all of it is done or if goal is unreachable."
# Experiments, all off by default. See sim/README.md for how each one scored.
SHOW_STATE = False  # Labels show toggle states and values, and the location lists what it shows but can't be acted on.
RESUME = False  # After an action, carry on from where it happened, with a way back up, instead of from the desktop.
MAX_SHOWN = 5

VERBS = {
    "quit": "Do nothing more and stop.",
    "open": "Go one level inside this item to see what it contains.",
    "invoke": "Activate this item, like clicking it.",
    "toggle": "Switch this item on or off.",
    "select": "Select this item.",
    "expand": "Open this dropdown or list to see the items inside.",
    "collapse": "Collapse this item.",
    "type": "Replace this item's text with: {text}",
    "back": "Go back up one level, out of the current item.",
}

ASKS = {"invoke": "Click", "toggle": "Toggle", "select": "Select", "collapse": "Collapse"}


def actions(control):
    """Map verb -> callable for what this control supports."""
    found = {}
    if p := control.GetPattern(uia.PatternId.InvokePattern):
        found["invoke"] = p.Invoke
    if p := control.GetPattern(uia.PatternId.TogglePattern):
        found["toggle"] = p.Toggle
    if p := control.GetPattern(uia.PatternId.SelectionItemPattern):
        found["select"] = p.Select
    if p := control.GetPattern(uia.PatternId.ExpandCollapsePattern):
        collapsed = p.ExpandCollapseState == uia.ExpandCollapseState.Collapsed
        found["expand" if collapsed else "collapse"] = p.Expand if collapsed else p.Collapse
    if (p := control.GetPattern(uia.PatternId.ValuePattern)) and not p.IsReadOnly:
        found["type"] = p.SetValue
    return found


def listed(control):
    """Every child UIA reports, except our own windows, which must stay out of the agent's reach.

    A window that's closing raises and loses the whole list, so read again once it's gone.
    FindAll would avoid that, but it reads a flatter tree than this walk on 30% of nodes.
    """
    found = []
    for _ in range(READ_ATTEMPTS):
        try:
            found = control.GetChildren()
            break
        except COMError:
            time.sleep(READ_RETRY)
    alive = []
    for child in found:
        try:
            if child.ProcessId != OWN_PROCESS:
                alive.append(child)
        except COMError:  # The element vanished after it was listed.
            pass
    return alive


def is_minimized(control):
    return bool(uia.IsIconic(control.NativeWindowHandle))


def restore(window):
    """Bring a minimized window back without taking focus, then wait for its contents."""
    uia.ShowWindow(window.NativeWindowHandle, uia.SW.ShowNoActivate)
    time.sleep(RESTORE_SETTLE)
    deadline = time.monotonic() + RESTORE_WAIT
    while not listed(window) and time.monotonic() < deadline:
        time.sleep(READ_RETRY)


def minimize(window):
    uia.ShowWindow(window.NativeWindowHandle, uia.SW.ShowMinNoActive)


def has_children(control):
    try:
        return bool(control.GetFirstChildControl())
    except COMError:  # The element vanished while it was read.
        return False


def is_collapsed(control):
    """True for a closed dropdown, whose items don't exist in UIA until it opens."""
    pattern = control.GetPattern(uia.PatternId.ExpandCollapsePattern)
    return bool(pattern) and pattern.ExpandCollapseState == uia.ExpandCollapseState.Collapsed


def collapse(control):
    """Close a dropdown the agent opened. Picking an item often closes it already, and a web select never does."""
    try:
        pattern = control.GetPattern(uia.PatternId.ExpandCollapsePattern)
        if pattern and pattern.ExpandCollapseState != uia.ExpandCollapseState.Collapsed:
            pattern.Collapse()
    except COMError:  # The dropdown went away with its window.
        pass


def flagged(control):
    """Listed children, each with whether the agent can use it: on screen, or a minimized window."""
    found = []
    for child in listed(control):
        try:
            found.append((child, not child.IsOffscreen or is_minimized(child)))
        except COMError:  # The element vanished after it was listed.
            pass
    return found


def children(control):
    """What the agent can use."""
    return [child for child, usable in flagged(control) if usable]


def preview(control):
    """Names found inside control, breadth first, so unnamed containers can be told apart."""
    names, queue, visited = [], children(control), 0
    while queue and len(names) < PREVIEW_NAMES and visited < PREVIEW_VISITS:
        child = queue.pop(0)
        visited += 1
        if name := " ".join(child.Name.split())[:PREVIEW_NAME]:
            names.append(name)
        queue += children(child)
    return names


def describe(verb, control, text):
    if verb == "open" and (names := preview(control)):
        return f"{VERBS[verb]} Contains: {', '.join(names)}"
    return VERBS[verb].format(text=text)


def state(control):
    """What a control is set to, like ' (on)' or ' value="Arial"', or nothing."""
    try:
        if toggle := control.GetPattern(uia.PatternId.TogglePattern):
            return " (on)" if toggle.ToggleState == 1 else " (off)"
        if (value := control.GetPattern(uia.PatternId.ValuePattern)) and value.Value:
            return f' value="{" ".join(value.Value.split())[:40]}"'
    except COMError:  # The element vanished while it was read.
        pass
    return ""


def label(control):
    name = " ".join(control.Name.split())[:MAX_NAME]  # Names can hold newlines that would break the option list.
    text = f'{control.ControlTypeName.removesuffix("Control")} "{name}"'
    text = f"{text} id={control.AutomationId}" if control.AutomationId else text
    return text + state(control) if SHOW_STATE else text


def shown(node, opts):
    """Text at node the agent can read but not act on, like a calculator's display."""
    offered = {tuple(option[1].GetRuntimeId()) for option in opts.values() if option[1]}  # Wrappers differ per read.
    found = []
    for child in children(node):
        try:
            if tuple(child.GetRuntimeId()) not in offered and (child.ControlTypeName == "TextControl" or state(child)):
                if text := " ".join(child.Name.split())[:60] + state(child):
                    found.append(text)
        except COMError:
            pass
    return found[:MAX_SHOWN]


def options(node, text=None):
    """Options at node as name -> (verb, control, action). Quit comes first.

    The model can't write text, so type is offered only when the caller supplies it.
    """
    found = {"quit": ("quit", None, None)}
    for child in children(node):
        try:
            # Acting on a disabled control raises, but what's inside it can still be usable.
            moves = actions(child) if child.IsEnabled else {}
            # Inside something clickable there's only its icon and caption, so opening it is a dead end.
            # A closed dropdown's items only exist once it's expanded, so that's the way in.
            # A minimized Store app lists nothing at all, but entering it restores the contents.
            if (children(child) or is_minimized(child)) and not {"invoke", "toggle", "expand"} & moves.keys():
                moves = {"open": None} | moves
            if text is None:
                moves.pop("type", None)
            for verb, act in moves.items():
                name = f"{verb} {label(child)}"
                while name in found:
                    name += " (another)"
                found[name] = (verb, child, act)
        except COMError:  # The element vanished while it was read.
            pass
    if len(found) > MAX_OPTIONS:
        # ponytail: drops the last items, run a tournament over chunks if goals need those
        print(f"  dropping {len(found) - MAX_OPTIONS} options over the limit")
    return dict(list(found.items())[:MAX_OPTIONS])


def ask_openjev(goal, path, opts, text, done, model):
    """One stateless choice, read from the probabilities of the answer letter.

    Shuffled because this kind of model leans toward the first option. Scores are each
    option's share of the probability, and options outside the model's top 20 have none.
    """
    names = random.sample(list(opts), len(opts))
    letters = dict(zip(LETTERS, names))
    listing = "\n".join(f"[{k}] {n}: {describe(opts[n][0], opts[n][1], text)}" for k, n in letters.items())
    steps = [" > ".join(nodes) for nodes in done]  # The nodes picked on the way to each action, not just the action.
    finished = f"Done so far: {'; '.join(steps)}\n" if done else ""
    prompt = f"State:\n{finished}Location: {' > '.join(path)}\n\nQuestion: {goal}\n{INSTRUCTIONS}\nOptions:\n{listing}"
    body = {
        "model": model,
        "stream": False,
        "think": False,
        "logprobs": True,
        # ponytail: Ollama caps this at 20, so confidence runs slightly high past 20 options
        "top_logprobs": 20,
        "options": {"temperature": 0, "num_predict": 1, "num_ctx": 16384},
        "messages": [{"role": "user", "content": prompt}],
    }
    request = urllib.request.Request(OLLAMA_URL, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=MODEL_TIMEOUT) as response:
        top = json.load(response)["logprobs"][0]["top_logprobs"]
    probs = {t["token"]: math.exp(t["logprob"]) for t in top if t["token"] in letters}
    best, total = max(probs, key=probs.get), sum(probs.values())
    share, chance = probs[best] / total, 1 / len(names)
    return {
        "choice": letters[best],
        # Same confidence as Jev: 0 for an even spread, 1 for a single peak.
        "confidence": (share - chance) / (1 - chance),
        "scores": {letters[k]: p / total for k, p in probs.items()},
    }


def installed_models():
    """Names of the models Ollama has, or none if it isn't running."""
    try:
        with urllib.request.urlopen(OLLAMA_URL.replace("/chat", "/tags"), timeout=1) as response:
            return [model["name"].removesuffix(":latest") for model in json.load(response)["models"]]
    except (OSError, ValueError, KeyError):
        return []


def print_event(event):
    if event["kind"] == "pick":
        print(f"{' > '.join(event['path'])}\n  -> {event['choice']}  (confidence {event['confidence']:.2f})")
    elif event["kind"] == "done":
        print(f"  done: {event['action']}")
    elif event["kind"] == "stop":
        print(f"  {event['reason']}, stopping")


def request(verb, control, text):
    """The action in words, to ask the user before it happens."""
    if verb == "type":
        return f'Type "{text}" into {label(control)}?'
    return f"{ASKS[verb]} {label(control)}?"


def ask_terminal(verb, control, text):
    return input(f"{request(verb, control, text)} [y/N] ").strip().lower() == "y"


def walk(goal, root, ask, text, permit, on_event, cancelled, undo):
    nodes, path, done = [root], [label(root)], []
    expanded_at = None  # Depth of the first dropdown this walk opened, whose items go when it closes.
    for _ in range(MAX_STEPS):
        node = nodes[-1]
        on_event({"kind": "at", "node": node})
        opts = options(node, text)
        if RESUME and len(nodes) > 1:
            opts = {"quit": opts.pop("quit"), "back": ("back", None, None), **opts}
        moves = [option[0] for option in opts.values() if option[0] not in ("quit", "back")]
        if not moves:
            if RESUME and len(nodes) > 1:  # What it acted in has gone, so back out of it.
                nodes.pop()
                path.pop()
                continue
            on_event({"kind": "stop", "reason": "nothing usable here"})
            return "quit"
        # A lone child is the only way forward, so skip the model call.
        auto = moves == ["open"]
        if auto:
            pick = {"choice": next(k for k, o in opts.items() if o[0] == "open"), "confidence": 1.0}
        else:
            here = path
            if SHOW_STATE and (texts := shown(node, opts)):
                here = [*path[:-1], f"{path[-1]} showing {'; '.join(texts)}"]
            pick = ask(goal, here, opts, text, done)
        # Checked after the slow model call, so a cancel during it can't reach an action.
        if cancelled():
            return "cancelled"
        verb, control, act = opts[pick["choice"]]
        on_event({"kind": "pick", "node": node, "path": path, "auto": auto, "options": opts, **pick})
        if pick["confidence"] < MIN_CONFIDENCE:
            on_event({"kind": "stop", "reason": f"confidence {pick['confidence']:.2f} is below the floor of {MIN_CONFIDENCE:.2f}"})
            return "quit"
        if verb == "quit":
            return verb
        if verb == "back":
            nodes.pop()
            path.pop()
            # Levels with one way in would only send it straight back down.
            while len(nodes) > 1 and [o[0] for o in options(nodes[-1], text).values()] == ["quit", "open"]:
                nodes.pop()
                path.pop()
        elif verb == "open":
            if is_minimized(control):
                restore(control)
                undo.append((control, minimize))
            nodes.append(control)
            path.append(label(control))
        elif verb == "expand":
            # Looking, not acting, so the run carries on. A dropdown closes as soon as focus moves,
            # so stopping here would lose the items before the user could send the next prompt.
            on_event({"kind": "at", "node": control})
            act()
            undo.append((control, collapse))
            # ponytail: a menu can open outside its button, where the agent can't reach it from here
            if children(control):
                if expanded_at is None:
                    expanded_at = len(nodes)
                nodes.append(control)
                path.append(label(control))
        else:
            on_event({"kind": "at", "node": control})
            if not permit(verb, control, text):
                return "cancelled" if cancelled() else "denied"
            # Clicking the prompt closes a classic dropdown, and its items refuse clicks while hidden.
            for dropdown, reverse in undo:
                if reverse is collapse and is_collapsed(dropdown):
                    dropdown.GetPattern(uia.PatternId.ExpandCollapsePattern).Expand()
            finished = request(verb, control, text).removesuffix("?")  # Read first: acting can remove the element.
            act(text) if verb == "type" else act()
            if SHOW_STATE:  # Read again, so the history says what it's set to now.
                try:
                    finished = request(verb, control, text).removesuffix("?")
                except COMError:
                    pass
            done.append([*path[1:], finished])  # The root is where every walk starts, so nothing picked it.
            on_event({"kind": "done", "action": finished})
            # The window it acted in stays up, but a dropdown should close behind its pick.
            for opened, reverse in undo:
                if reverse is collapse:
                    collapse(opened)
            undo.clear()
            if len(done) >= MAX_ACTIONS:
                on_event({"kind": "stop", "reason": f"reached the action limit of {MAX_ACTIONS}"})
                return "quit"
            if verb == "type":
                text = None  # There's one text per prompt, so it can't be typed again.
            time.sleep(ACTION_WAIT)
            if RESUME:
                if expanded_at is not None:  # The dropdown closed, so carry on from where it was.
                    del nodes[expanded_at:], path[expanded_at:]
                try:
                    path = [label(n) for n in nodes]  # States may have changed.
                except COMError:  # Where it acted has gone.
                    nodes, path = [root], [label(root)]
            else:
                on_event({"kind": "restart"})
                nodes, path = [root], [label(root)]
            expanded_at = None
    on_event({"kind": "stop", "reason": f"reached the step limit of {MAX_STEPS}"})
    return "quit"


def run(goal, root, ask, text=None, permit=lambda *_: True, on_event=print_event, cancelled=lambda: False):
    """Keep acting until the model quits or a limit is reached. Returns "quit", "denied" or "cancelled".

    Every prompt starts with nothing done, and nothing carries over to the next one.
    on_event gets dicts of kind "at" (the agent reached a node), "pick" (a decision), "done" (an action completed),
    "restart" (a new walk from the desktop) and "stop" (why it gave up). permit(verb, control, text) can refuse an action.
    """
    undo = []
    try:
        return walk(goal, root, ask, text, permit, on_event, cancelled, undo)
    finally:
        # Looking around on the way to an action that never came leaves nothing behind.
        for control, reverse in reversed(undo):
            reverse(control)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("goal")
    parser.add_argument("--text", help="text the type action may enter; without it, typing isn't offered")
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--yes", action="store_true", help="act without asking first")
    args = parser.parse_args()
    permit = (lambda *_: True) if args.yes else ask_terminal
    with uia.UIAutomationInitializerInThread():
        run(args.goal, uia.GetRootControl(), lambda *a: ask_openjev(*a, args.model), args.text, permit)
