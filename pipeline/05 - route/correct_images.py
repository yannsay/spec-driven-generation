"""Stage 05b, the corrector, for pictures the router sends back as `new` because of the spec: rewrite their
decisions so the image model gets it right, in a new run that klein redraws.

Usage: uv run correct_images.py <run> <new run> [--only LOC_START.jpg,...]

For each picture of <run> whose router verdict asks for `new` (any route's action is new), one call
(prompts/image_corrector.md + the rules in short, Gemma 4 on otari.ai, with the picture) with the game's sentences, the picture's numbered decisions with their checks by id (k1, k2, …),
its sentences list, the exact prompt sent, and the router's image and both routes. The corrector cites every
existing check by its id; the code puts the check back word for word and rejects an answer that drops one, cites
one twice, or invents an id (the checks stay the same, so a fix shows as a fix). A new check only for a new fact. The answer replaces the picture's decisions and sentences in a copy of the specs, specs/<new run>/; the
code adds the fixed checks, builds the prompt, and runs check_spec.py: a correction that breaks a rule is rejected
and the picture keeps its decisions. Pictures of <run> are copied to <new run> so edits can start from their
originals. Writes specs/<new run>/correct.json (each call: picture, routes, answer, justification, accepted) and
prints the list of files for klein to redraw: the corrected pictures, and the edits of a corrected first scene.
"""
import json
import shutil
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor as Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))
from paths import INPUT, PICTURES, PILOT, SPEC_RULES, SPECS, stages
stages()

from check_spec import add_checks, check, join_edits
from judge_steps import MODEL  # Gemma 4, with vision
from llm_image_judge import image_url
from make_spec import URL, api_key
from route_image import NAME, sent_prompt

PROMPT = Path(__file__).parent / "prompts" / "image_corrector.md"


def system():
    return PROMPT.read_text() + "\n\n---\n\n" + SPEC_RULES.read_text()  # the shared rules, whole


def numbered(im):
    """The picture's decisions with their checks by id: k1, k2, … in the order of the decisions."""
    out, k = [], 0
    for n, d in enumerate(im.get("picture_decisions", []), 1):
        cs = []
        for c in d.get("checks") or []:
            k += 1
            cs.append({"id": f"k{k}", "question": c["question"], "expected": c["expected"]})
        out.append({"n": n, "draw": d.get("draw"), "why": d.get("why"), "checks": cs})
    return out


def ask(path, payload):
    body = {"model": MODEL, "max_tokens": 6000,
            "messages": [{"role": "system", "content": system()},
                         {"role": "user", "content": [
                             {"type": "image_url", "image_url": {"url": image_url(path)}},
                             {"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=1)}]}]}
    req = urllib.request.Request(URL, json.dumps(body).encode(), {
        "Content-Type": "application/json", "Authorization": "Bearer " + api_key()})
    with urllib.request.urlopen(req, timeout=300) as r:
        out = json.loads(r.read())
    return out["choices"][0]["message"].get("content") or "", out.get("usage") or {}


def correct(room, spec, file, path, routes, rejected=None):
    """One correction call; returns the parsed answer, or None. `rejected`: on the one retry, the earlier answer and
    why it was rejected."""
    im = next(i for i in spec["images"] if i["file"] == file)
    payload = {"game_text": [e["sentence"] for e in im.get("sentences") or []],
               "decisions": numbered(im),
               "sentences": im.get("sentences") or [],
               "prompt": sent_prompt(path),
               "routes": routes,
               **({"rejected": rejected} if rejected else {})}
    try:
        text, usage = ask(str(path), payload)
        error = None
    except Exception as e:  # one picture's error is reported, the run goes on
        text, usage, error = "", {}, repr(e)
    try:
        answer = json.loads(text[text.find("{"):text.rfind("}") + 1])
    except ValueError:
        answer = None
    return {"payload": payload, "answer": answer, "answer_text": text if answer is None else None,
            "usage": usage, "error": error}


def restore_checks(old_decisions, new_decisions):
    """The corrector's decisions with each cited check id replaced by the spec's check, word for word. Returns
    (decisions, problem): problem names an id dropped, cited twice or unknown, or a decision left without a check."""
    by_id = {c["id"]: {"question": c["question"], "expected": c["expected"]}
             for d in old_decisions for c in d["checks"]}
    seen, out = [], []
    for n, d in enumerate(new_decisions, 1):
        cs = []
        for c in d.get("checks") or []:
            if isinstance(c, dict) and c.get("id"):  # an existing check written out with its id: cited by the id
                c = c["id"]
            if isinstance(c, str):
                if c not in by_id:
                    return None, f"decision {n} cites an unknown check id {c!r}"
                seen.append(c)
                cs.append(dict(by_id[c]))
            elif isinstance(c, dict) and c.get("question") and isinstance(c.get("expected"), bool):
                cs.append({"question": c["question"], "expected": c["expected"], "new": True})
            else:
                return None, f"decision {n} has a check that is neither an id nor a question with expected"
        if not cs:
            return None, f"decision {n} has no check"
        out.append({"draw": d.get("draw"), "why": d.get("why"), "checks": cs})
    twice = sorted({k for k in seen if seen.count(k) > 1})
    dropped = sorted(set(by_id) - set(seen), key=lambda k: int(k[1:]))
    if twice or dropped:
        return None, "every existing check comes back exactly once, by its id: " + "; ".join(
            ([f"cited twice: {', '.join(twice)}"] if twice else []) + ([f"dropped: {', '.join(dropped)}"] if dropped else []))
    return out, ""


def to_correct(run, only):
    """(room, file, path, routes) for each picture whose router asks for a new picture."""
    for room in PILOT:
        if not (SPECS / run / f"{room}.json").exists():  # a run of some rooms only
            continue
        spec = json.loads((SPECS / run / f"{room}.json").read_text())
        for im in spec["images"]:
            path = PICTURES / run / room / im["file"]
            verdict = Path(f"{path}.route.{NAME}.json")
            if only and path.name not in only or not verdict.exists():
                continue
            v = json.loads(verdict.read_text())
            flagged = {f["id"]: f["question"] for f in v.get("payload", {}).get("flagged", [])}
            routes = [{"question": flagged.get(r["id"], r["id"]), **{k: r.get(k) for k in ("cause", "why", "fix")}}
                      for r in v.get("routes", []) if r.get("cause") in ("image", "both", "spec")]
            if routes and any(r.get("action") == "new" for r in v.get("routes", [])):
                yield room, im["file"], path, routes


def apply(src, spec_path, file, c):
    """Put a correction into the spec and check it; a correction that breaks a rule is undone.
    Returns (accepted, the reason it was rejected)."""
    before = json.loads(spec_path.read_text())
    spec = json.loads(spec_path.read_text())
    ans = c["answer"] or {}
    if not (ans.get("decisions") and ans.get("sentences")):
        return False, c["error"] or ("not JSON" if c["answer"] is None else "no decisions or sentences")
    im = next(i for i in spec["images"] if i["file"] == file)
    decisions, problem = restore_checks(c["payload"]["decisions"], ans["decisions"])
    if problem:
        return False, problem
    im["picture_decisions"], im["sentences"] = decisions, ans["sentences"]
    old = {f for f in check(src, spec_path).fail if f.startswith(file) or file in f}  # what the spec broke already
    spec = join_edits(add_checks(src, spec))
    spec_path.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n")
    fails = [f for f in check(src, spec_path).fail if (f.startswith(file) or file in f) and f not in old]
    if fails:  # only what the correction broke counts
        spec_path.write_text(json.dumps(before, indent=2, ensure_ascii=False) + "\n")  # rejected: rules broken
        return False, "; ".join(fails[:3])
    return True, ""


if __name__ == "__main__":
    args = sys.argv[1:]
    only = args.pop(args.index("--only") + 1).split(",") if "--only" in args else []
    run, new_run = [a for a in args if not a.startswith("--")]
    new_specs = SPECS / new_run
    new_specs.mkdir(parents=True, exist_ok=True)
    for spec in (SPECS / run).glob("LOC_*.json"):  # the specs only, not their verdicts and logs
        if spec.name.count(".") == 1:
            shutil.copy(spec, new_specs / spec.name)
    # the earlier pictures, so edits start from their originals
    shutil.copytree(PICTURES / run, PICTURES / new_run, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("*.json", "log.jsonl"))
    todo = list(to_correct(run, only))
    specs = {room: json.loads((new_specs / f"{room}.json").read_text()) for room in {t[0] for t in todo}}
    with Pool(4) as ex:
        done = list(ex.map(lambda t: (t, correct(t[0], specs[t[0]], t[1], t[2], t[3])), todo))
    log, redraw = [], []
    for (room, file, path, routes), c in done:
        src, spec_path = INPUT / f"{room}.yaml", new_specs / f"{room}.json"
        tries = []
        for attempt in (1, 2):  # one retry, with the reason, when the script rejects the correction
            accepted, reason = apply(src, spec_path, file, c)
            tries.append({**c, "accepted": accepted, "rejected_because": reason})
            if accepted or attempt == 2 or c["answer"] is None:
                break
            print(f"{room:15} {file:38} rejected, retrying: {reason[:90]}")
            c = correct(room, json.loads(spec_path.read_text()), file, path, routes,
                        rejected={"answer": c["answer"], "because": reason})
        if accepted:
            redraw.append(file)
            spec = json.loads(spec_path.read_text())
            redraw += [i["file"] for i in spec["images"] if i.get("edit_of") == file]  # its edits with it
        log.append({"room": room, "file": file, "routes": routes, "tries": tries, "accepted": accepted})
        print(f"{room:15} {file:38} {'accepted' if accepted else 'REJECTED: ' + reason[:90]}"
              + (" (second try)" if len(tries) == 2 else ""))
    (new_specs / "correct.json").write_text(json.dumps(log, indent=1, ensure_ascii=False) + "\n")
    print("redraw:", ",".join(dict.fromkeys(redraw)))
