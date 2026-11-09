"""Stage 2c: repair the flagged parts of one room's spec, at most two rounds.

Usage: uv run repair_spec.py "../01 - yaml/llm_input/<LOC>.yaml" specs/<name>/<LOC>.json

1. Check the spec: check_spec.py, then the Jev spec judge. Every failed answer flags its part (an unsure does not:
   it stays in the verdict, for the page): the room's exits, or one image. A script failure flags the part it names.
2. Round 1: for each flagged part, one repair call (prompts/repair.md + the spec writer's rules) with the initial
   input (room YAML, whole spec), the part, its errors and each error's objective; "fix it". Only that part is
   replaced. A fixed check the repair rewrote is kept, marked repaired, with the template sentence beside it.
   The call also returns a justification: for each error, what it changed and why (logged, shown in the viewer,
   and given to the second round with the first attempt).
3. Check the whole spec again. A repaired part with a question that passed before the round and is flagged now,
   or with a new script failure, is a regression: that repair is undone and the part goes to a person. A part
   nobody touched that is newly flagged is Jev varying near the line: it is flagged as usual.
4. Round 2: the parts still flagged get a second call that also sees both earlier attempts and their errors.
5. Check again. What is still flagged goes to a person.

Writes the repaired spec over the original (git keeps the original), its <spec>.jev.json, each verdict on its own
(<spec>.jev.round0.json the first, .round<n>.json after round n), and <spec>.repair.json:
the system prompt once, then each round's calls (part, errors, the user message sent, the answer, usage). Calls run on otari.ai, like make_spec.py.
"""
import copy
from concurrent.futures import ThreadPoolExecutor as Pool
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))  # env.py

from check_spec import add_checks, check, join_edits
from jev_judge import judge
from make_spec import EXTRA, MODEL, PROMPT, api_key, call
from prompt_text import prompt_text
from paths import PROMPTS
from spec_questions import OBJECTIVE, kind_of

REPAIR = PROMPTS / "repair.md"
ROUNDS = 2


def part_of_line(line):
    """The part a script failure is about: the image file it names, else the room."""
    m = re.search(r"\b(?:img|items)/[^\s:']+", line)
    return m.group(0) if m else "room"


def flagged(src, spec_path):
    """{part: [errors]} for one spec, {part: questions that passed}, and Jev's verdict (None when the script failed)."""
    report = check(src, spec_path)
    if report.fail:
        errs = {}
        for line in report.fail:
            errs.setdefault(part_of_line(line), []).append({"script": line, "objective": "follow the rules the script checks"})
        return errs, {}, None
    v = judge(src, spec_path)
    errs, passed = {}, {}
    for r in v["results"]:
        part = "room" if r["level"] == "room" else r["target"]
        if r["result"] == "pass":
            passed.setdefault(part, set()).add(r["question"])
        elif r["result"] == "fail":  # an unsure flags nothing: shown on the page, not repaired, not held back;
            # the picture judge finds the real faults
            errs.setdefault(part, []).append(
                {"question": r["question"], "good_answer": "yes" if r["expected"] else "no",
                 "p_yes": r["p_yes"], "result": r["result"], "objective": OBJECTIVE[kind_of(r)]})
    return errs, passed, v


def regressions(part, new_errs, passed_before, script_before):
    """The errors of a repaired part that are new: a question that passed before, or a new script failure."""
    return [e for e in new_errs.get(part, []) if e.get("question") in passed_before.get(part, set())
            or e.get("script") and e["script"] not in script_before]


def get_part(spec, part):
    if part == "room":
        return {"exits": spec.get("exits", [])}
    return next(im for im in spec["images"] if im["file"] == part)


def put_part(spec, part, new):
    if part == "room":
        spec["exits"] = new.get("exits", spec.get("exits", []))
        return
    for c in new.get("checks", []):  # a rewritten fixed check stands in for its template
        if c.get("origin") == "required" and c.get("replaces"):
            c["repaired"], c["template"] = True, c.pop("replaces")
    spec["images"] = [new if im["file"] == part else im for im in spec["images"]]


def parse(answer):
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", answer.strip())
    return json.loads(text)


def user_message(src, spec, part, errors, history):
    return json.dumps({"room_yaml": Path(src).read_text(), "spec": spec, "part": part,
                       "current": get_part(spec, part), "errors": errors,
                       **({"earlier_attempts": history} if history else {}),
                       "task": "Fix it. Return only the repaired part as JSON."}, ensure_ascii=False, indent=1)


def repair(src, spec, part, errors, history, key, model, extra, system):
    user = user_message(src, spec, part, errors, history)
    _, trace = call(spec["room"], system, key, model, extra, user=user, label="room-spec-repair")
    try:
        reply = parse(trace["answer"])
        # the model sometimes sends the repaired part on its own, without the {"part", "justification"} wrapper:
        # taken as the part, with no justification
        bare = isinstance(reply, dict) and "part" not in reply and ("file" in reply or "exits" in reply)
        new, why = (reply, []) if bare else (reply["part"], reply.get("justification", []))
        problem = "the part without its justification" if bare else None
    except (ValueError, KeyError, TypeError):
        new, why, problem = None, [], "not JSON, or not the shape asked for"
    return {"part": part, "errors": errors, "user": user, "answer": new, "justification": why, "usage": trace["usage"],
            "latency_s": trace["latency_s"], "error": trace["error"] or (problem if not new else None),
            "note": problem if new else None,
            "raw": trace["answer"] if not new else None}  # kept when unusable, to read why


def keep_verdict(spec_path, verdict, n):
    """Each Jev verdict of the loop kept on its own, <spec>.jev.round<n>.json (0: the first, n: after round n), so the
    page can show every question at each step; <spec>.jev.json stays the last one."""
    if verdict:
        Path(spec_path).with_suffix(f".jev.round{n}.json").write_text(json.dumps(verdict, indent=2, ensure_ascii=False) + "\n")


def save(src, spec, spec_path):
    spec = join_edits(add_checks(src, spec))
    Path(spec_path).write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n")
    return spec


def run(src, spec_path, repair_fn=repair):
    model, extra, key = MODEL, EXTRA, api_key()
    system = REPAIR.read_text() + "\n\n---\n\n# The spec writer's rules\n\n" + prompt_text(PROMPT)
    spec = json.loads(Path(spec_path).read_text())
    errs, passed, verdict = flagged(src, spec_path)
    keep_verdict(spec_path, verdict, 0)
    print(f"before: {sum(map(len, errs.values()))} flagged answers in {len(errs)} parts: {', '.join(errs)}")
    log, history, person = [], {}, {}
    for rnd in range(1, ROUNDS + 1):
        if not errs:
            break
        before = copy.deepcopy(spec)
        script_before = {e["script"] for es in errs.values() for e in es if e.get("script")}
        with Pool(len(errs)) as ex:
            done = list(ex.map(lambda p: repair_fn(src, before, p, errs[p], history.get(p), key, model, extra, system),
                               errs))
        for d in done:
            history.setdefault(d["part"], []).append({"part": get_part(before, d["part"]), "errors": d["errors"],
                                                      "justification": d["justification"]})
            if d["answer"]:
                put_part(spec, d["part"], copy.deepcopy(d["answer"]))  # the log keeps the answer as returned
            print(f"round {rnd}: {d['part']:34} {d['latency_s']:5.0f}s cost=${(d['usage'] or {}).get('cost_usd')} "
                  f"{d['error'] or 'repaired'}")
        spec = save(src, spec, spec_path)
        new_errs, new_passed, verdict = flagged(src, spec_path)
        undone = {d["part"]: r for d in done if (r := regressions(d["part"], new_errs, passed, script_before))}
        if undone:  # a repair that broke what passed is undone, and its part goes to a person
            for part in undone:
                put_part(spec, part, copy.deepcopy(get_part(before, part)))
                person[part] = {"errors": errs[part], "regression": undone[part]}
            spec = save(src, spec, spec_path)
            new_errs, new_passed, verdict = flagged(src, spec_path)
        keep_verdict(spec_path, verdict, rnd)
        unchanged = {d["part"] for d in done if not d["answer"]}  # nothing came back: a pass now is the judge varying
        fixed = [p for p in errs if p not in new_errs and p not in undone and p not in unchanged]
        varied = [p for p in errs if p not in new_errs and p in unchanged]
        print(f"after round {rnd}: {sum(map(len, new_errs.values()))} flagged answers in {len(new_errs)} parts; "
              f"fixed: {', '.join(fixed) or '-'}; judge varied, nothing changed: {', '.join(varied) or '-'}; "
              f"undone: {', '.join(undone) or '-'}")
        log.append({"round": rnd, "calls": done, "fixed": fixed, "judge_varied": varied, "undone": undone,
                    "flagged_after": new_errs})
        for d in done:  # the second round sees both earlier attempts: as it was, and as the first repair left it
            if d["part"] in new_errs and d["part"] not in person:
                history[d["part"]].append({"part": get_part(spec, d["part"]), "errors": new_errs[d["part"]]})
        errs = {p: e for p, e in new_errs.items() if p not in person}
        passed = new_passed
    if verdict:
        Path(spec_path).with_suffix(".jev.json").write_text(json.dumps(verdict, indent=2, ensure_ascii=False) + "\n")
    for part, e in errs.items():
        person[part] = {"errors": e}
    Path(spec_path).with_suffix(".repair.json").write_text(
        json.dumps({"system": system, "rounds": log, "to_a_person": person}, indent=1, ensure_ascii=False) + "\n")
    if person:
        print("to a person: " + "; ".join(f"{p} ({len(v['errors'])})" for p, v in person.items()))
    return person


if __name__ == "__main__":
    run(sys.argv[1], sys.argv[2])
