"""Stage 2b: judge a room spec with Jev, after the script check passes.

Usage: uv run jev_judge.py "../01 - yaml/llm_input/<LOC>.yaml" <spec>.json

Runs check_spec.py first and stops if it fails. Otherwise asks Jev (typesafe/jev-1.13, through OpenRouter's
decisions endpoint) in one request per subject, sent in parallel, so each question sees only what it is about
(accuracy falls with unrelated state):
- the room: the rules in short, the input YAML, the exits and the exits the script could not decide;
- each image: the rules, the input YAML, that image's part of the spec, and for an edit the original's prompt.
Every question from spec_questions.py is a Noul. Jev answers with
the probability that the answer is yes: above YES it is yes, below NO it is no, in between it is unsure
and a person looks at it. A question passes when Jev's answer is the one it expects.

Writes <spec>.jev.json next to the spec, prints the fails and unsures, and exits 1 when anything failed.
Needs OPENROUTER_API_KEY, from the environment or the repo's .env.
"""
import json
from concurrent.futures import ThreadPoolExecutor as Pool
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))  # env.py

from check_spec import check
from env import key
from prompt_text import prompt_text
from paths import PROMPTS
from spec_questions import questions

URL = "https://openrouter.ai/api/alpha/decisions"
MODEL = "typesafe/jev-1.13"
NO, YES = 0.3, 0.7
JUDGE_PROMPT = PROMPTS / "spec_judge.md"


def api_key():
    return key("OPENROUTER_API_KEY")


def rules():
    """spec_judge.md, whole, with the shared rules.md: how a spec is used and its rules (Jev's background)."""
    return prompt_text(JUDGE_PROMPT).strip()


def sort(p, expected):
    if NO <= p <= YES:
        return "unsure"
    return "pass" if (p > YES) == expected else "fail"


def states(src, data, judge_notes):
    """One state per subject: "room", then each image's file."""
    base = {"rules": rules(), "input_yaml": Path(src).read_text()}
    out = {"room": {**base, "room": {"text": data.get("text"), "exits": data.get("exits", [])},
                    "exits_the_script_could_not_decide": judge_notes}}
    files = {im.get("file"): im for im in data.get("images", [])}
    for f, im in files.items():
        out[f] = {**base, "image": im}
        if im.get("edit_of") in files:  # the first picture, and the decisions this edit replaces
            first = files[im["edit_of"]]
            ds = [d for d in first.get("picture_decisions", []) if isinstance(d, dict)]
            out[f]["original"] = {"file": im["edit_of"], "generation_prompt": first.get("generation_prompt", ""),
                                  "replaced_decisions": [{"n": n, "draw": d.get("draw", "")} for n, d in enumerate(ds, 1)
                                                         if n in (im.get("replaces") or [])]}
    return out


def ask(state, qs):
    body = {"model": MODEL, "state": state,
            "questions": {q["id"]: {"type": "noul", "instructions": q["question"]} for q in qs}}
    r = requests.post(URL, json=body, headers={"Authorization": f"Bearer {api_key()}"}, timeout=300)
    r.raise_for_status()
    return r.json()


def judge(src, spec):
    report = check(src, spec)
    if report.fail:
        sys.exit("script check failed, fix the spec first:\n" + "\n".join(report.fail))
    qs = questions(spec)
    subjects = states(src, json.loads(Path(spec).read_text()), report.judge)
    groups = {}
    for q in qs:
        groups.setdefault("room" if q["level"] == "room" else q["target"], []).append(q)
    with Pool(len(groups)) as ex:
        outs = dict(zip(groups, ex.map(lambda k: ask(subjects[k], groups[k]), groups)))
    answers = {i: a for out in outs.values() for i, a in out["answers"].items()}
    results = [{**q, "p_yes": answers[q["id"]]["noul"], "result": sort(answers[q["id"]]["noul"], q["expected"])}
               for q in qs]
    usage = {"cost": sum((o.get("usage") or {}).get("cost", 0) for o in outs.values()), "requests": len(outs)}
    return {"spec": spec, "model": next(iter(outs.values())).get("model"), "usage": usage,
            "ids": {k: o.get("id") for k, o in outs.items()},
            "thresholds": {"no_below": NO, "yes_above": YES}, "results": results}


if __name__ == "__main__":
    src, spec = sys.argv[1], sys.argv[2]
    v = judge(src, spec)
    Path(spec).with_suffix(".jev.json").write_text(json.dumps(v, indent=2, ensure_ascii=False) + "\n")
    for res in v["results"]:
        if res["result"] != "pass":
            print(f"{res['result'].upper():6} {res['p_yes']:.2f} {res['id']}\n       {res['question']}")
    n = {k: sum(x["result"] == k for x in v["results"]) for k in ("pass", "unsure", "fail")}
    print(f"{n['pass']} pass, {n['unsure']} unsure, {n['fail']} fail of {len(v['results'])}"
          f" (cost ${v['usage'].get('cost', 0):.4f}): {'FAIL' if n['fail'] else 'PASS'}")
    sys.exit(1 if n["fail"] else 0)
