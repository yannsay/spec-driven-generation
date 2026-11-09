"""Stage 05a, the router: for each flagged check of a picture, which step is at fault, and what to do with the picture.

Usage: uv run route_image.py <run> [--only LOC_START.jpg,...]

For each picture of the run whose combined judge verdict (<image>.judge.<TAG>.both.json, stage 04) has a failed or
flipped check, one call to Gemma 4 on otari.ai (prompts/image_router.md, then the rules in short from
02 - spec/prompts/spec_judge.md, so the router can tell a fault from a departure the rules make on purpose) with the
picture and, as JSON, the game's sentences, the spec's numbered decisions, the exact prompt sent (the run's
log.jsonl), and the flagged checks with the judge's answers and reasons. Back, per flagged check: cause
spec | image | both | judge | rule, an action for the picture new | edit | keep, why, and a fix.
Writes <image>.route.<model with / as _>.json; prints each route.
"""
import json
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor as Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))
from paths import INPUT, SPEC_RULES, stages
stages()

from check_spec import game_sentences
from judge_steps import MODEL, TAG
from llm_image_judge import image_url, pictures
from make_spec import URL, api_key

PROMPT = Path(__file__).parent / "prompts" / "image_router.md"
JUDGE = f"{TAG}.both"  # the two judge runs combined
NAME = MODEL.replace("/", "_")  # in the verdict's file name


def sent_prompt(path):
    """The prompt the image model received, from the run's log (the last entry for this file)."""
    room_dir = next(p for p in path.parents if (p / "log.jsonl").exists())
    rel = str(path.relative_to(room_dir))
    sent = ""
    for line in (room_dir / "log.jsonl").read_text().splitlines():
        e = json.loads(line)
        if e.get("file") == rel:
            sent = e.get("prompt", sent)
    return sent


def system():
    return PROMPT.read_text() + "\n\n---\n\n" + SPEC_RULES.read_text()  # the shared rules, whole


def ask(path, payload):
    body = {"model": MODEL, "max_tokens": 4000, "temperature": 0,
            "messages": [{"role": "system", "content": system()},
                         {"role": "user", "content": [
                             {"type": "image_url", "image_url": {"url": image_url(path)}},
                             {"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=1)}]}]}
    req = urllib.request.Request(URL, json.dumps(body).encode(), {
        "Content-Type": "application/json", "Authorization": "Bearer " + api_key()})
    with urllib.request.urlopen(req, timeout=300) as r:
        out = json.loads(r.read())
    return out["choices"][0]["message"].get("content") or "", out.get("usage") or {}


def route(spec_path, file, path, first_verdict=None):
    """The router's verdict for one picture, or None when its judge verdict is missing or all passed.
    `first_verdict`: for a state picture (an edit of its first picture), the first picture's final verdict; a check
    that passed there and fails here is marked, the edit broke it."""
    verdict = Path(f"{path}.judge.{JUDGE}.json")
    if not verdict.exists():
        return None
    flagged = [r for r in json.loads(verdict.read_text()).get("results", []) if r["result"] != "pass"]
    if not flagged:
        return None
    spec = json.loads(Path(spec_path).read_text())
    im = next(i for i in spec["images"] if i["file"] == file)
    passed_first = set()
    if first_verdict and Path(first_verdict).exists():
        passed_first = {r["question"] for r in json.loads(Path(first_verdict).read_text()).get("results", [])
                        if r["result"] == "pass"}
    payload = {"game_text": game_sentences(INPUT / f"{spec['room']}.yaml", im),
               "decisions": [{"n": n, **{k: d.get(k) for k in ("draw", "why", "checks")}}
                             for n, d in enumerate(im.get("picture_decisions", []), 1) if isinstance(d, dict)],
               "prompt": sent_prompt(Path(path)),
               "flagged": [{"id": r["id"], "question": r["question"], "good_answer": "yes" if r["expected"] else "no",
                            "answer": r["answer"], "why": r.get("why", ""),
                            **({"passed_on_first_picture": True} if r["question"] in passed_first else {})}
                           for r in flagged]}
    text, usage = ask(str(path), payload)
    try:
        routes = json.loads(text[text.find("{"):text.rfind("}") + 1])["routes"]
    except (ValueError, KeyError):
        return {"image": str(path), "error": "not JSON", "answer_text": text, "usage": usage}
    return {"image": str(path), "model": MODEL, "judge": JUDGE, "payload": payload, "routes": routes, "usage": usage}


def save(path, v):
    Path(f"{path}.route.{NAME}.json").write_text(json.dumps(v, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    args = sys.argv[1:]
    only = args.pop(args.index("--only") + 1).split(",") if "--only" in args else []
    run = [a for a in args if not a.startswith("--")][0]
    cost = 0.0
    with Pool(4) as ex:  # four pictures at a time
        done = list(ex.map(lambda t: (t[2], route(*t)), list(pictures(run, only))))
    for path, v in done:
        if v is None:
            continue
        save(path, v)
        cost += float((v.get("usage") or {}).get("cost_usd") or 0)
        print(f"== {path.name}")
        if "error" in v:
            print("   ERROR", v["error"])
            continue
        for r in v["routes"]:
            print(f"   {r.get('id'):5} {r.get('cause'):6} {str(r.get('action')):5} | {r.get('why', '')[:110]}\n"
                  f"{'':20}fix: {r.get('fix', '')[:110]}")
    print(f"cost ${cost:.4f}")
