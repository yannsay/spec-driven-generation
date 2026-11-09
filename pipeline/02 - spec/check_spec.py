"""Stage 2, the mechanical check: check a room spec against the rules of prompts/room_spec.md that a script can decide.

Usage: uv run check_spec.py "../01 - yaml/llm_input/<LOC>.yaml" <spec>.json

First works out, from the input YAML alone, what a correct spec must contain (exits, images, required
checks), then compares the spec with it. Prints FAIL, WARN and JUDGE lines and exits 1 on any FAIL.
WARN lines do not fail. JUDGE lines are exits whose verbs hold no plain walking direction: whether to
draw them is a judgment call, left to the LLM judge (prompts/spec_judge.md).
"""
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# rule 1 of room_spec.md, the plain walking directions only
WALK = {"NORTH", "SOUTH", "EAST", "WEST", "NE", "NW", "SE", "SW", "UPWAR", "DOWN", "DOWNW", "INWAR",
        "OUT", "OUTDO", "OUTSI", "ENTER", "EXIT", "CRAWL", "CLIMB", "FORWA", "BACK", "LEFT", "RIGHT", "JUMP"}
MOTION_WORDS = yaml.safe_load((Path(__file__).resolve().parents[1] / "01 - yaml" / "motion_words.yaml").read_text())
MAGIC = set(MOTION_WORDS["magic"])
# following the water is a walking way when the room has water (room_spec.md §1); the script decides it
WATER = {"STREA", "DOWNS", "UPSTR"}
WATER_WORDS = re.compile(r"\b(stream|spring|water|brook|river)s?\b", re.I)
# a drawn exit is an object (a doorway, an opening: asked "inside the picture", and counted per kind) or a path
# (a road, a gully: it runs off the frame, so only "visible" is asked). "Fully inside the picture" is not asked:
# the judge reads it as "no part touches an edge".
SHAPES = {"object", "path"}
# the rules of room_spec.md that make a picture depart from a game sentence on purpose
RULES = {"setting", "camera", "compass", "items"}


def walkable(verbs, text):
    """A walking direction, or following the water in a room whose description has water."""
    return bool(set(verbs) & WALK or set(verbs) & WATER and WATER_WORDS.search(text))
# compass = picture: an exit with one compass verb is drawn at this place, named in these words
PLACE = {"NORTH": "straight ahead", "SOUTH": "in the foreground", "EAST": "on the right", "WEST": "on the left",
         "NE": "ahead on the right", "NW": "ahead on the left",
         "SE": "in the foreground on the right", "SW": "in the foreground on the left"}
SCENE_SIZE, ITEM_SIZE = "2048x1152", "1024x1024"
PROMPT_WORDS = (60, 250)   # klein and Z-Image cut prompts at 512 tokens, the closing lines included
# words that make a question negative; quoted text (a description, a check, a reason) is not ours to lint
NEGATION = re.compile(r"\b(not|no|never|none|nothing|without|free of|missing|absent|hidden|gone|cut off|avoids?|leaves? out|lacks?)\b", re.I)


@dataclass
class Report:
    fail: list = field(default_factory=list)
    warn: list = field(default_factory=list)
    judge: list = field(default_factory=list)


def flat(s):
    return " ".join(s.split())


def name_of(key, o):
    return (o.get("inventory") or key).lstrip("*").lower()


def labels(src, spec):
    """The fixed checks name a movable object by the spec writer's `label` for it (the object
    as the picture shows it: "little bird", not the inventory's "little bird in cage"), not by its inventory name.
    Returns {file: {inventory name: label}}: an item picture uses its own label; a scene uses each object's first
    item picture's label."""
    _, _, objs = load_input(src)
    clean = lambda t: re.sub(r"^(a|an|the)\s+", "", " ".join(str(t).split()).lower())
    own, first = {}, {}
    for im in spec.get("images", []):
        oid = im.get("id")
        if im.get("kind") == "item" and oid in objs and im.get("label"):
            inv = name_of(oid, objs[oid])
            own[im["file"]] = {inv: clean(im["label"])}
            first.setdefault(inv, clean(im["label"]))
    return {im.get("file"): own.get(im.get("file"), first if im.get("kind") == "scene" else {})
            for im in spec.get("images", [])}


def relabel(question, names):
    """A fixed check with each inventory name replaced by its label."""
    for inv, label in names.items():
        question = question.replace(f"the {inv} ", f"the {label} ").replace(f"the {inv}?", f"the {label}?")
    return question


# ---- what a correct spec must contain, from the input YAML alone ----

def load_input(src):
    """The input is a YAML list: the room's entry first, then one entry per object that starts there."""
    entries = [next(iter(e.items())) for e in yaml.safe_load(Path(src).read_text())]
    loc, room = entries[0]
    return loc, room, dict(entries[1:])


def expected_exits(room):
    """The room's goto exits, merged by destination: {to: {"verbs": set, "cond": cond or None}}."""
    exits = {}
    for t in room.get("travel", []):
        if t["action"][0] == "goto":
            e = exits.setdefault(t["action"][1], {"verbs": set(), "cond": None})
            e["verbs"] |= set(t["verbs"])
            e["cond"] = e["cond"] or t.get("cond")
    return exits


# Every picture: the setting is in the style line the image model gets, and the judge's prompt says nothing of it,
# so the question carries it. Expected false. The materials list and the examples let the judge catch a metal pipe.
SETTING = ("The game takes place in a medieval fantasy world where everything is old and handmade: weathered stone, "
           "old brick, rough timber, wrought iron. Is anything out of its time or context visible in the picture, "
           "such as a metal pipe, concrete or sheet metal?", False)


def scene_checks(text, lit, movable, fixed):
    checks = [(f'Does the image match this description: "{text}"?', True)]
    checks += ([("Are large parts of the scene too dark to see?", False)] if lit
               else [("Is the scene lit by a single pool of warm light?", True),
                     ("Are the edges of the image dark?", True)])
    checks += [(f"Is the {name_of(k, o)} visible anywhere in the image?", False) for k, o in movable.items()]
    checks += [(f"Is the {name_of(k, o)} visible in the image?", True)
               for k, o in fixed.items() if not o.get("states")]
    return checks + [SETTING]


def item_checks(name):
    return [(f"Is the {name} visible in the image?", True),
            (f"Is the {name} inside the picture?", True),
            (f"Is a floor, wall or landscape visible behind the {name}?", False), SETTING]


def shows_text(*texts):
    """The first checks of every picture: one per sentence of the game's own text, so a
    judge cannot pass a long text that is only partly drawn."""
    return [(f'Does the picture show what this sentence describes: "{s}"?', True)
            for s in dict.fromkeys(s for t in texts if t for s in sentences(t))]


def image(kind, file, first, required):
    """One expected image. `first` is the first file of its group; any other member is an edit of it."""
    return {"kind": kind, "file": file, "edit_of": first if first != file else None,
            "size": SCENE_SIZE if kind == "scene" else ITEM_SIZE, "required": required}


def expected_images(loc, text, lit, objs):
    movable = {k: o for k, o in objs.items() if not o.get("immovable")}
    fixed = {k: o for k, o in objs.items() if o.get("immovable")}
    scene = scene_checks(text, lit, movable, fixed)

    # one scene per state of a fixed object the player can change (states and changes), else one scene
    changeable = [(k, o) for k, o in fixed.items() if o.get("states") and o.get("changes")]
    if changeable:
        k, o = changeable[0]
        files = [f"img/{loc}.{st}.jpg" for st in o["states"]]
        # no "does the X fit this description" for a state: the sentence checks quote the same line
        images = [image("scene", f, files[0], shows_text(text, o["descriptions"][i]) + scene)
                  for i, f in enumerate(files)]
    else:
        images = [image("scene", f"img/{loc}.jpg", f"img/{loc}.jpg", shows_text(text) + scene)]

    # one item per movable object, or one per state when the descriptions differ
    for k, o in movable.items():
        name, descs = name_of(k, o), o.get("descriptions") or []
        if o.get("states") and len(set(descs)) > 1:
            # states sharing a description share the image of the first of them
            firsts = [st for i, st in enumerate(o["states"]) if descs.index(descs[i]) == i]
            files = [f"items/{k}.{st}.png" for st in firsts]
            images += [image("item", f, files[0], shows_text(descs[o["states"].index(st)]) + item_checks(name))
                       for f, st in zip(files, firsts, strict=True)]
        else:
            images.append(image("item", f"items/{k}.png", f"items/{k}.png", shows_text(*descs) + item_checks(name)))

    # steps-like objects: states without changes. Which description fits this room is a judgment call,
    # so only the presence of a required "fits this description" check is checked.
    seen_from = [name_of(k, o) for k, o in fixed.items() if o.get("states") and not o.get("changes")]
    return images, seen_from


def marked(im):
    """The sentences of a picture that a rule changes on purpose, as `plain` text."""
    return {plain(e.get("sentence", "")) for e in im.get("sentences") or []
            if isinstance(e, dict) and e.get("rule") in RULES}


def waived(question, marks):
    """A fixed check that quotes a rule-changed sentence (the sentence itself, the full description, a
    'fits this description') is not asked: it fails on every correct picture. The checks of the decisions that
    sentence points to verify it as drawn."""
    quoted = re.findall(r'"([^"]*)"', question)
    return any(m and m in plain(q) for q in quoted for m in marks)


def add_checks(src, spec):
    """Complete each image's checks: the pipeline's fixed checks first, in place of any the LLM wrote, then
    the LLM's exit checks and its required check saying which description of a fixed object fits this room (a
    judgment), then the checks each picture decision carries, one or more. An edit then gets the checks of its
    first picture's decisions that its change does not replace (`replaces`), as `origin: kept`: it is checked on
    what it keeps too."""
    _, _, _, images, seen_from = expected(src)
    fixed = {i["file"]: i["required"] for i in images}
    by_file = {im.get("file"): im for im in spec.get("images", [])}
    names = labels(src, spec)
    for im in spec.get("images", []):
        # a fixed check a repair rewrote stands in for its template
        repaired = {c["template"]: c for c in im.get("checks", []) if c.get("repaired")}
        own = [c for c in im.get("checks", []) if c.get("origin") == "exit"
               or c.get("origin") == "required" and not c.get("repaired")
               and any(c["question"].startswith(f"Does the {n} fit this description: ") for n in seen_from)]
        decided = [{**c, "origin": "decision"} for d in im.get("picture_decisions", []) if isinstance(d, dict)
                   for c in d.get("checks") or [] if isinstance(c, dict)]
        first = by_file.get(im.get("edit_of")) if im.get("edit_of") else None
        marks = marked(im) | (marked(first) if first else set())  # an edit shows the same room: its first's marks hold
        im["checks"] = [repaired.get(q) or {"question": relabel(q, names.get(im.get("file"), {})), "expected": x,
                                            "origin": "required"}
                        for q, x in fixed.get(im.get("file"), []) if not waived(q, marks)] + own + decided
        if first and isinstance(im.get("replaces"), list):
            asked = {c["question"] for c in im["checks"]}
            for n, d in enumerate(first.get("picture_decisions", []), 1):
                if n in im["replaces"] or not isinstance(d, dict):
                    continue
                for c in d.get("checks") or []:
                    if isinstance(c, dict) and c.get("question") and c["question"] not in asked:
                        asked.add(c["question"])
                        im["checks"].append({**c, "origin": "kept", "decision": n})
    return spec


def expected(src):
    loc, room, objs = load_input(src)
    text = flat(room["description"]["long"])
    lit = bool(room.get("conditions", {}).get("LIT"))
    images, seen_from = expected_images(loc, text, lit, objs)
    return loc, text, expected_exits(room), images, seen_from


# ---- comparing the spec with it ----

def check_exits(spec_exits, exits, text, report):
    got = {e["to"]: e for e in spec_exits}
    for to, e in exits.items():
        g = got.pop(to, None)
        if not g:
            report.fail.append(f"exit to {to}: missing")
            continue
        if set(g.get("verbs", [])) != e["verbs"]:
            report.fail.append(f"exit to {to}: verbs {g.get('verbs')} != {sorted(e['verbs'])}")
        # a conditional exit sharing a verb with another exit is the same way, drawn once (rule 1)
        twin = e["cond"] and any(e["verbs"] & o["verbs"] for t2, o in exits.items() if t2 != to)
        walks = walkable(e["verbs"], text)
        if walks and not twin and not g.get("drawn"):
            report.fail.append(f"exit to {to}: has a walking direction but is not drawn")
        if twin and g.get("drawn"):
            report.fail.append(f"exit to {to}: shares a verb with another exit and has a cond, "
                               "so it is not drawn again")
        magic = e["verbs"] <= MAGIC
        if magic and g.get("drawn"):
            report.fail.append(f"exit to {to}: reached only by a magic word, so it is not drawn")
        if not walks and not magic:
            report.judge.append(f"exit to {to} ({', '.join(sorted(e['verbs']))}): drawn={g.get('drawn')}")
        if g.get("drawn") and not g.get("label"):
            report.fail.append(f"exit to {to}: drawn but has no label")
        if g.get("drawn") and g.get("shape") not in SHAPES:
            report.fail.append(f"exit to {to}: drawn, so its shape is 'object' or 'path', not {g.get('shape')!r}")
        kind = (g.get("kind") or "").lower()
        if g.get("drawn") and g.get("shape") == "object" and not (kind and kind in (g.get("label") or "").lower()):
            report.fail.append(f"exit to {to}: an object exit names its kind, a word of its label, not {g.get('kind')!r}")
    report.fail += [f"exit to {to}: not a goto exit of this room" for to in got]


def place_of(verbs):
    """Where an exit is drawn: the PLACE of its compass verb, or None with no compass verb or more than one."""
    compass = [v for v in verbs if v in PLACE]
    return PLACE[compass[0]] if len(compass) == 1 else None


def check_places(spec, report):
    """Each drawn exit with one compass verb names its place in its label and in the room's first scene prompt."""
    first = next((im for im in spec.get("images", []) if im.get("kind") == "scene" and not im.get("edit_of")), {})
    prompt = first.get("generation_prompt", "").lower()
    for e in spec.get("exits", []):
        place = place_of(e.get("verbs", []))
        if not (e.get("drawn") and place):
            continue
        if place not in e.get("label", "").lower():
            report.fail.append(f"exit to {e['to']}: label {e.get('label')!r} does not say {place!r}")
        near = place.replace("in the foreground", "in the near foreground")  # the same place
        if place not in prompt and near not in prompt:
            report.fail.append(f"exit to {e['to']}: {first.get('file')} prompt does not say {place!r}")


def join_edits(spec):
    """Build each prompt: its decisions' `draw` sentences, in the order listed (the spec writer lists them by
    importance). An edit's too: one text, written, judged, corrected and sent to the editing model."""
    for im in spec.get("images", []):
        draws = [d["draw"].strip() for d in im.get("picture_decisions", []) if isinstance(d, dict) and d.get("draw")]
        if draws:
            im["generation_prompt"] = " ".join(draws)
    return spec


def check_edits(spec, report):
    """Every edit lists the first picture's decisions it replaces."""
    for im in spec.get("images", []):
        if not im.get("edit_of"):
            continue
        f = im.get("file")
        first = next((i for i in spec.get("images", []) if i.get("file") == im["edit_of"]), {})
        n = len(first.get("picture_decisions") or [])
        rep = im.get("replaces")
        if not (isinstance(rep, list) and all(isinstance(k, int) and 1 <= k <= n for k in rep)):
            report.fail.append(f"{f}: `replaces` must list the numbers (1 to {n}) of the first picture's decisions "
                               f"this state replaces, [] for none; got {rep!r}")


def sentences(text):
    """A game text cut into its sentences, on one line each."""
    return [s for s in re.split(r"(?<=[.!?])\s+(?=[A-Z\"])", flat(text)) if s.strip()]


def plain(s):
    """A sentence for comparing: lower case, spaces collapsed, quotes and final punctuation dropped."""
    return re.sub(r"\s+", " ", re.sub(r"[\"'.,!?]", "", s.lower())).strip()


def game_sentences(src, im):
    """The sentences of the game's text a picture must draw: the first scene the room's description, a scene
    made for a state its state's line, an item its state's line or its description."""
    _, room, objs = load_input(src)
    out = []
    if im.get("kind") == "scene":
        if not im.get("edit_of"):
            out += sentences(room["description"]["long"])
        for o in objs.values():
            if im.get("state") and im["state"] in (o.get("states") or []) and o.get("descriptions"):
                out += sentences(o["descriptions"][o["states"].index(im["state"])])
    else:
        o = objs.get(im.get("id") or Path(im["file"]).stem.split(".")[0], {})
        stem = Path(im["file"]).stem.split(".")
        descs = o.get("descriptions") or []
        if len(stem) > 1 and stem[1] in (o.get("states") or []) and descs:
            out += sentences(descs[o["states"].index(stem[1])])
        else:
            out += [s for d in dict.fromkeys(d for d in descs if d) for s in sentences(d)]
    return list(dict.fromkeys(out))


def check_sentences(src, spec, report):
    """Every sentence of the game's text a picture must draw is in its `sentences` list, word for word, with
    the numbers of the decisions that draw it."""
    for im in spec.get("images", []):
        f, want = im.get("file"), game_sentences(src, im)
        decisions = [d for d in im.get("picture_decisions", []) if isinstance(d, dict)]
        listed = {plain(e.get("sentence", "")): e.get("decisions") or []
                  for e in im.get("sentences") or [] if isinstance(e, dict)}
        for s in want:
            ds = listed.get(plain(s))
            if ds is None:
                report.fail.append(f"{f}: the game's sentence {s!r} is missing from `sentences`")
            elif not ds or any(not isinstance(n, int) or not 1 <= n <= len(decisions) for n in ds):
                report.fail.append(f"{f}: the game's sentence {s!r} points to no picture decision")
        for e in im.get("sentences") or []:
            if isinstance(e, dict) and e.get("rule") and e["rule"] not in RULES:
                report.fail.append(f"{f}: sentence {e.get('sentence')!r} is marked with the rule {e['rule']!r}, "
                                   f"not one of {sorted(RULES)}")


def check_required(f, checks, want, seen_from, report):
    # a repaired fixed check stands for the template it replaces
    req = {(c.get("template") if c.get("repaired") else c["question"], c["expected"])
           for c in checks if c.get("origin") == "required"}
    wanted_questions = {q for q, _ in want}
    for q, x in want:
        if (q, x) not in req:
            wrong = any(c["question"] == q for c in checks)
            report.fail.append(f"{f}: required check {'has a wrong expected or origin' if wrong else 'missing'}: {q}")
    for n in seen_from:
        if not any(q.startswith(f"Does the {n} fit this description: ") and x for q, x in req):
            report.fail.append(f"{f}: no required check that the {n} fits this room's description")
    for q, _ in req:
        own_seen_from = any(q.startswith(f"Does the {n} fit this description: ") for n in seen_from)
        if q not in wanted_questions and not own_seen_from:
            report.fail.append(f"{f}: unexpected required check: {q}")


def negations(question):
    """The negative words of a text, outside its quotes. Checks, judge questions and image prompts are all
    worded in the positive."""
    return NEGATION.findall(re.sub(r'"[^"]*"', "", question))


def check_image(im, want, drawn, seen_from, report, inherited=frozenset(), names=None):
    """`drawn`: the spec's drawn exits; `inherited`: an edit's first picture's rule marks; `names`: its labels."""
    f = want["file"]
    for key in ("kind", "size", "edit_of"):
        if im.get(key) != want[key]:
            report.fail.append(f"{f}: {key} is {im.get(key)!r}, expected {want[key]!r}")
    checks = im.get("checks", [])
    marks = marked(im) | set(inherited)
    required = [(relabel(q, names or {}), x) for q, x in want["required"] if not waived(q, marks)]
    if want["kind"] == "item" and not im.get("label"):
        report.fail.append(f"{f}: an item picture needs a `label`: the object as this picture shows it")
    check_required(f, checks, required, seen_from if want["kind"] == "scene" else [], report)
    for c in checks:
        if negations(c["question"]):
            report.fail.append(f"{f}: check worded in the negative ({', '.join(negations(c['question']))}): {c['question']}")

    if want["kind"] == "scene":
        exit_checks = [c for c in checks if c.get("origin") == "exit"]
        trues = sum(1 for c in exit_checks if c["expected"])
        # one per drawn exit (visible), a second per object exit (inside the picture), and one count per kind of
        # object exit ("Is exactly one doorway visible?"): an image model adds a second, unwanted doorway
        kinds = {(e.get("kind") or "").lower() for e in drawn if e.get("shape") == "object"}
        want_trues = sum(2 if e.get("shape") == "object" else 1 for e in drawn) + len(kinds)
        if trues != want_trues or len(exit_checks) != trues:
            report.fail.append(f"{f}: exit checks should be {want_trues}, all expecting true")
        for c in exit_checks:
            if "fully inside" in c["question"].lower():
                report.fail.append(f"{f}: a drawn exit is asked 'inside the picture', never 'fully inside': {c['question']}")
        for e in drawn:
            label = (e.get("label") or "").lower()
            if e.get("shape") == "path" and any(label and label in c["question"].lower()
                                                and "inside the picture" in c["question"] for c in exit_checks):
                report.fail.append(f"{f}: {e.get('label')!r} is a path: asked whether visible, not inside the picture")
        for e in drawn:  # the label, word for word, in its exit checks
            label = (e.get("label") or "").lower()
            if label and not any(label in c["question"].lower() for c in exit_checks):
                report.fail.append(f"{f}: no exit check names the label {e.get('label')!r} word for word")
        for k in sorted(kinds):
            if k and not any(c["expected"] and "exactly" in c["question"].lower() and k in c["question"].lower()
                             for c in exit_checks):
                report.fail.append(f"{f}: no count check for the object exits of kind {k!r} "
                                   f"(\"Is exactly one {k} visible in the image?\")")

    decisions = im.get("picture_decisions", [])
    for n, d in enumerate(decisions, 1):
        cs = d.get("checks") if isinstance(d, dict) else None
        if not (isinstance(d, dict) and d.get("draw") and d.get("why") and cs and all(
                isinstance(c, dict) and c.get("question") and isinstance(c.get("expected"), bool) for c in cs)):
            report.fail.append(f"{f}: picture decision {n} needs draw, why, and checks with question and expected")
    carried = sum(len(d.get("checks") or []) for d in decisions if isinstance(d, dict))
    own = sum(1 for c in checks if c.get("origin") == "decision")
    if own != carried:
        report.fail.append(f"{f}: {own} decision checks, but the picture decisions carry {carried}")

    prompt = im.get("generation_prompt", "")
    words = len(prompt.split())
    if words > PROMPT_WORDS[1]:
        report.fail.append(f"{f}: generation_prompt has {words} words, more than {PROMPT_WORDS[1]}: the image model cuts it")
    elif words < PROMPT_WORDS[0] and not im.get("edit_of"):  # an edit names only what its state changes
        report.warn.append(f"{f}: generation_prompt has {words} words, aim for {PROMPT_WORDS[0]} to {PROMPT_WORDS[1]}")
    if negations(prompt):
        report.fail.append(f"{f}: generation_prompt names what is not there ({', '.join(negations(prompt))})")
    # one check per fact: a check the writer wrote never asks again what a fixed or exit check asks
    fixed = {plain(c["question"]) for c in checks if c.get("origin") in ("required", "exit")}
    for c in checks:
        if c.get("origin") == "decision" and plain(c["question"]) in fixed:
            report.fail.append(f"{f}: decision check repeats a fixed or exit check: {c['question']}")
    if not im.get("picture_decisions"):
        report.fail.append(f"{f}: no picture_decisions")


def check(src, spec_path):
    loc, text, exits, images, seen_from = expected(src)
    spec = json.loads(Path(spec_path).read_text())
    report = Report()
    if spec.get("room") != loc:
        report.fail.append(f"room is {spec.get('room')!r}, expected {loc!r}")
    if spec.get("text") != text:
        report.fail.append("text differs from description.long on one line")

    check_exits(spec.get("exits", []), exits, text, report)
    check_places(spec, report)
    check_edits(spec, report)
    check_sentences(src, spec, report)
    drawn = [e for e in spec.get("exits", []) if e.get("drawn")]
    names = labels(src, spec)

    # same names, order does not matter (edit_of already says which image is made first)
    got = {i.get("file"): i for i in spec.get("images", [])}
    want = {i["file"] for i in images}
    report.fail += [f"image missing: {f}" for f in sorted(want - got.keys())]
    report.fail += [f"image not expected: {f}" for f in sorted(got.keys() - want)]
    for i in images:
        if i["file"] in got:
            first = got.get(got[i["file"]].get("edit_of")) if got[i["file"]].get("edit_of") else None
            check_image(got[i["file"]], i, drawn, seen_from, report, marked(first) if first else frozenset(),
                        names.get(i["file"], {}))
    return report


if __name__ == "__main__":
    r = check(sys.argv[1], sys.argv[2])
    for line in r.fail:
        print("FAIL", line)
    for line in r.warn:
        print("WARN", line)
    for line in r.judge:
        print("JUDGE", line)
    print("PASS" if not r.fail else f"{len(r.fail)} failure(s)")
    sys.exit(1 if r.fail else 0)
