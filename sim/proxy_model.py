"""A stand-in for OpenJev behind the harness's real Ollama call.

It replaces urllib.request.urlopen, so harness.ask_openjev runs unchanged: it builds the
prompt, shuffles the options, posts the body and reads top_logprobs back. Like the real
model, this one is stateless and sees only that prompt text.

The policy is lexical, with the world knowledge a small LM has (1 is "One", + is "Plus"):
  - The goal's content words, in order, minus every word the "Done so far" history covers,
    are what's left to do. Earlier words weigh more, so goals tend to run in order.
  - An option scores by the remaining words its name, AutomationId and (for open) its
    preview contain. Quit scores high once nothing is left and low otherwise.
  - Gaussian noise on every logit (NOISE) stands for the model's mistakes, and a bias toward
    the first listed option (FIRST_BIAS) stands for the position bias the harness shuffles away.
It is a proxy: results measure the harness's tree, prompt, history and stopping logic under a
fixed, imperfect chooser, not OpenJev's own accuracy.
"""
import io
import json
import math
import random
import re
import urllib.request

NOISE = 1.0  # Standard deviation of logit noise.
FIRST_BIAS = 0.5
MATCH, PREVIEW_MATCH = 5.0, 3.5  # Logit per remaining goal word found in the option, or only in its preview.
QUIT_DONE, QUIT_PENDING = 4.0, -2.0
DECAY = 0.5  # Weight of the i-th remaining word is 1 / (1 + DECAY * i).

STOP = set("""a an the to in on of and then it its is be for with into from find answer press set turn click
choose make please again button do all""".split())
SYNONYMS = {"+": "plus", "=": "equals", "-": "minus", "*": "multiply", "times": "multiply", "seconds": "second",
            **{str(i): n for i, n in enumerate("zero one two three four five six seven eight nine".split())}}
DIGITS = "zero one two three four five six seven eight nine".split()
CONCEPTS = {"number": DIGITS, "pad": DIGITS, "operators": ["plus", "minus", "multiply", "divide", "equals"],
            "font": ["arial", "black", "wingdings"], "delay": DIGITS, "rectangle": ["area"]}  # What a group's name implies it holds.
VERB_START = r"(Do nothing more|Go back up|Go one level inside|Activate this item|Switch this item|Select this item|Open this dropdown|Collapse this item|Replace this item)"


def words(text):
    text = re.sub(r"\bid=", " ", text)  # The label's id= isn't an equals sign.
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)  # Split AutomationIds like SnipDelayComboBox.
    found = []
    for token in re.findall(r"[a-z0-9]+|[-+=*]", text.lower()):
        token = SYNONYMS.get(token, token)
        if token not in STOP:
            found.append(token)
    return found


def named(step):
    """The names and ids in a step: verbs and control types say nothing about which item it was."""
    return " ".join(re.findall(r'"([^"]*)"|id=(\S+)', step) and [a or b for a, b in re.findall(r'"([^"]*)"|id=(\S+)', step)])


def remaining(goal, done):
    """The goal's words, in order, with each word a finished step mentions crossed off once."""
    left = words(goal)
    for step in done:
        for token in dict.fromkeys(words(named(step))):  # Once per step, though name and id may both say it.
            if token in left:
                left.remove(token)
    return left


def parse(prompt):
    goal = re.search(r"\nQuestion: (.*)\n", prompt).group(1)
    location = re.search(r"Location: (.*)\n", prompt).group(1).partition(" > ")[2]  # Every walk starts at the root.
    done_line = re.search(r"Done so far: (.*)\n", prompt)
    done = done_line.group(1).split("; ") if done_line else []
    options = []
    for line in prompt.split("\nOptions:\n", 1)[1].split("\n"):
        match = re.match(r"\[(\w)\] (.*?): " + VERB_START + r"(.*)$", line)
        letter, name, start, rest = match.groups()
        options.append((letter, name, rest.split("Contains: ", 1)[1] if "Contains: " in rest else ""))
    return goal, done + [location], options  # Where it already is counts as reached.


def judged(prompt, rng):
    """The yes/no done question: yes once nothing in the goal is left."""
    goal = re.search(r"\nQuestion: (.*)\n", prompt).group(1)
    done_line = re.search(r"Done so far: (.*)\n", prompt)
    left = remaining(goal, done_line.group(1).split("; ") if done_line else [])
    letters = dict(re.findall(r"^\[(\w)\] (yes|no):", prompt, re.MULTILINE))
    return {k: (QUIT_DONE if (v == "yes") != bool(left) else 0.0) + rng.gauss(0, NOISE) for k, v in letters.items()}


def logits(prompt, rng):
    if "\nIs every part of the question done?\n" in prompt:
        return judged(prompt, rng)
    goal, done, options = parse(prompt)
    left = remaining(goal, done)
    weight = {}
    for i, token in enumerate(left):
        weight[token] = weight.get(token, 0) + 1 / (1 + DECAY * i)
    scores = {}
    for position, (letter, name, contains) in enumerate(options):
        verb, _, label = name.partition(" ")
        if verb == "quit":
            score = QUIT_PENDING if left else QUIT_DONE
        else:
            own = set(words(re.sub(r"^\w+ ", "", label)))  # The control type isn't evidence.
            near = set(words(contains))
            seen = (near | {w for t in own | near for w in CONCEPTS.get(t, ())}) - own
            score = sum(MATCH * weight[t] for t in own if t in weight) + sum(PREVIEW_MATCH * weight[t] for t in seen if t in weight)
        scores[letter] = score + rng.gauss(0, NOISE) + (FIRST_BIAS if position == 0 else 0)
    return scores


def install(seed):
    """Route the harness's model calls here. Returns the call counter."""
    rng, calls = random.Random(seed), [0]

    def urlopen(request, timeout=None):
        body = json.loads(request.data)
        calls[0] += 1
        scores = logits(body["messages"][0]["content"], rng)
        top = sorted(scores.items(), key=lambda kv: -kv[1])[:body["top_logprobs"]]
        norm = math.log(sum(math.exp(s) for s in scores.values()))
        reply = {"logprobs": [{"top_logprobs": [{"token": k, "logprob": s - norm} for k, s in top]}]}
        return io.BytesIO(json.dumps(reply).encode())

    urllib.request.urlopen = urlopen
    return calls
