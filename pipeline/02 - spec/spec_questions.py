"""The spec judge's questions, written out from one spec: one question per room, picture and picture check.

Every question is worded in the positive, asks one thing, and carries the answer a good spec gets. Three levels:
- room:     for each exit the script cannot decide (no walking direction, no water way, not only magic words),
            whether the description mentions what its words name, in plain words from motion_words.yaml
            (expected: the spec's decision), and each drawn exit's label;
- picture:  per image, each picture decision (allowed?), each sentence of the game's text (drawn by the
            decisions it points to?), agreement with the description (not for an edit), the state it shows, whether its prompt
            names the room's movable items; for an edit, that each decision it replaces takes its new form;
- question: per check of an image, the answer a picture drawn from the prompt would give. For checks the LLM wrote
            itself (exit and decision), also whether it can be answered from the picture and asks one clear thing.
            Required checks are fixed by the pipeline, and an edit's kept checks were asked on its first picture,
            so those two are not asked for them. An edit's checks are asked of the first picture as the
            editing model changes it.
"""
import json
from pathlib import Path

from check_spec import MAGIC, MOTION_WORDS, negations, walkable


# what each kind of question is there for: the objective a repair call is given
OBJECTIVE = {
    "exit": "an exit is drawn only when the room description mentions a place or thing its words name",
    "label": "a drawn exit's label names something a player could see and tap in this room",
    "decision": "every picture decision is stated in the YAML or an allowed choice",
    "covered": "everything the prompt asks to draw is covered by the YAML or by the picture decisions",
    "agrees": "the prompt agrees with the room's or the object's description in the YAML",
    "state": "a picture made for one state shows that state as the YAML describes it",
    "edit-replaced": "each first-picture decision an edit replaces takes its new form in the edit's decisions",
    "no-items": "a scene's prompt names nothing that could be mistaken for a movable object of the room",
    "drawn": "a picture drawn exactly from the prompt answers each of its checks as expected",
    "sentence": "every sentence of the game's text is drawn by the decisions it points to",
    "visible": "each check can be answered by looking at the picture alone",
    "clear": "each check asks one clear thing that two people would read the same way",
}


def kind_of(q):
    """The kind of a question, from its id: room/exit/X → exit, …/q3/drawn → drawn, …/decision2 → decision."""
    parts = q["id"].split("/")
    return parts[1] if q["level"] == "room" else parts[-1].rstrip("0123456789")


def question(qid, level, target, text, expected=True):
    return {"id": qid, "level": level, "target": target, "question": text, "expected": expected}


def room_questions(spec):
    qs = []
    for e in spec.get("exits", []):
        verbs = [v for v in e.get("verbs", []) if v not in MAGIC]
        if not verbs or walkable(verbs, spec.get("text", "")):
            continue
        words = " or ".join(dict.fromkeys(MOTION_WORDS["words"].get(v, v.lower()) for v in verbs))
        qs.append(question(f"room/exit/{e['to']}", "room", e["to"],
                           f"Does the room description mention any place or thing called {words}?",
                           expected=bool(e.get("drawn"))))
    for e in spec.get("exits", []):
        if e.get("drawn"):
            qs.append(question(f"room/label/{e['to']}", "room", e["to"],
                               f"Is \"{e.get('label', '')}\" something a player could see and tap in this room?"))
    return qs


def state_of(im):
    """The state an image is made for: a scene's `state`, or the STATE in items/<OBJECT>.<STATE>.png."""
    if im.get("kind") == "scene":
        return im.get("state")
    parts = Path(im["file"]).stem.split(".")
    return parts[1] if len(parts) > 1 else None


def picture_questions(im, movable):
    f = im["file"]
    def text(d):  # a decision as the judge reads it: what is drawn, and why
        if not isinstance(d, dict):
            return d
        return f"{d['draw']} (why: {d['why']})" if d.get("draw") else d.get("decision", "")
    qs = [question(f"{f}/decision{n}", "picture", f,
                   f"Is this picture decision of {f} stated in the YAML or an allowed choice: \"{text(d)}\"?")
          for n, d in enumerate(im.get("picture_decisions", []), 1)]
    if not im.get("edit_of") and not any(isinstance(d, dict) and d.get("draw") for d in im.get("picture_decisions", [])):
        qs.append(question(f"{f}/covered", "picture", f,  # only for a prompt written apart from the decisions
                           f"Is every separate object or feature the generation_prompt of {f} asks to draw covered by "
                           "the YAML or by its picture decisions?"))
    if not im.get("edit_of"):  # an edit's prompt names only what its state changes, not the room or object:
        # judged on the state's own line (the state question) and its replaced decisions
        qs += [question(f"{f}/agrees", "picture", f,
                        f"Does the generation_prompt of {f} agree with the "
                        f"{'room' if im.get('kind') == 'scene' else 'object'}'s description in the YAML?")]
    if state_of(im):
        qs.append(question(f"{f}/state", "picture", f,
                           f"Does the generation_prompt of {f} show the state {state_of(im)} as the YAML describes it?"))
    if im.get("edit_of"):  # against the first picture's replaced decisions, shown in the state as `original`.
        # "Nothing else changed" is asked per kept check (check_questions), not here: a new state names new things
        # (the oil, the flame's glow), so an "only the replaced things" question would fail it.
        qs.append(question(f"{f}/edit-replaced", "picture", f,
                           f"Does each replaced decision of {im['edit_of']} take its new form for the state "
                           f"{state_of(im)} in the picture decisions of {f}?"))
    # each sentence of the game's text, against the draw sentences of the decisions it points to
    decisions = [d for d in im.get("picture_decisions", []) if isinstance(d, dict)]
    for n, e in enumerate(im.get("sentences") or [], 1):
        draws = " ".join(f'[{k}] "{decisions[k - 1].get("draw", "")}"' for k in e.get("decisions") or []
                         if isinstance(k, int) and 1 <= k <= len(decisions))
        qs.append(question(f"{f}/sentence{n}", "picture", f,
                           f"Do these draw sentences show what this sentence of the game says: "
                           f"\"{e.get('sentence', '')}\"? Draw sentences: {draws}"))
    if im.get("kind") == "scene" and movable:
        qs.append(question(f"{f}/no-items", "picture", f,
                           f"Does the generation_prompt of {f} name or describe any of these, or anything that could "
                           f"be mistaken for one: {', '.join(movable)}?", expected=False))
    return qs


def check_questions(im):
    f, qs = im["file"], []
    for n, c in enumerate(im.get("checks", []), 1):
        q, base = c["question"], f"{f}/q{n}"
        drawn = (f"If an editing model starts from the picture of {im['edit_of']} and changes only what the "
                 f"generation_prompt of {f} describes" if im.get("edit_of")
                 else f"If an image model draws exactly what the generation_prompt of {f} describes")
        qs.append(question(f"{base}/drawn", "question", f,
                           f"{drawn}, would the answer to this question be yes: \"{q}\"?", expected=c["expected"]))
        if c.get("origin") not in ("required", "kept"):
            qs.append(question(f"{base}/visible", "question", f,
                               f"Can this be answered by looking at the picture of {f} alone: \"{q}\"?"))
            qs.append(question(f"{base}/clear", "question", f,
                               f"Does this ask one clear thing, that two people would read the same way: \"{q}\"?"))
    return qs


def questions(spec_path):
    spec = json.loads(Path(spec_path).read_text())
    images = spec.get("images", [])
    movable = [i["name"].lower() for i in images if i.get("kind") == "item" and i.get("name")]
    movable = list(dict.fromkeys(movable))
    qs = room_questions(spec)
    for im in images:
        qs += picture_questions(im, movable)
    for im in images:
        qs += check_questions(im)
    return qs


if __name__ == "__main__":
    import sys
    from collections import Counter
    qs = questions(sys.argv[1])
    print(Counter(q["level"] for q in qs), len(qs))
    for q in qs:
        if negations(q["question"]):
            print(f"worded in the negative ({', '.join(negations(q['question']))}): {q['question']}")
