"""The image judge: Gemma 4 judges a picture twice against its spec's checks, and the two runs are combined.

1. Two judge runs per picture, same model and prompt (llm_image_judge.judge), at VERSION:
   <image>.judge.<judge>.<VERSION>.r1.json and .r2.json. A run already on disk is not asked again.
2. Combined: a check passes only when both runs pass it. Both fail -> "fail"; one pass, one fail
   -> "flip". Written by the loop to
   <image>.judge.<judge>.<VERSION>.both.json, each result with both answers and both reasons.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))
from paths import stages
stages()

from llm_image_judge import judge

MODEL = "google/gemma-4-31b-it"
# the judge prompt's version, in the output file names: the reason before the answer, a quoted text split first
VERSION = "v5"
TAG = MODEL.replace("/", "_") + "." + VERSION


def judge_once(job):
    spec, file, path, run = job
    out = Path(f"{path}.judge.{TAG}.{run}.json")
    if not out.exists():
        out.write_text(json.dumps(judge(MODEL, spec, file, path), indent=2, ensure_ascii=False) + "\n")
    return json.loads(out.read_text())


def combine(path, r1, r2):
    if "error" in r1 or "error" in r2:
        return {"image": str(path), "error": r1.get("error") or r2.get("error")}
    results = []
    for a, b in zip(r1["results"], r2["results"], strict=True):
        result = "pass" if a["result"] == b["result"] == "pass" else "fail" if a["result"] == b["result"] else "flip"
        answer = a["answer"] if a["answer"] == b["answer"] else f"{a['answer']} once, {b['answer']} once"
        why = a["why"] if a["answer"] == b["answer"] else f"run 1 ({a['answer']}): {a['why']} | run 2 ({b['answer']}): {b['why']}"
        results.append({**{k: a[k] for k in ("id", "question", "expected", "origin") if k in a},
                        "answer": answer, "why": why, "result": result,
                        "runs": [{**({"parts": a["parts"]} if "parts" in a else {}), "answer": a["answer"], "why": a["why"]},
                                 {**({"parts": b["parts"]} if "parts" in b else {}), "answer": b["answer"], "why": b["why"]}]})
    return {"model": MODEL, "image": str(path), "spec": r1["spec"], "file": r1["file"], "runs": [f"{VERSION}.r1", f"{VERSION}.r2"],
            "rule": "pass only when both runs pass; both fail = fail; otherwise flip", "results": results,
            "cost_usd": sum(float((r.get("usage") or {}).get("cost_usd") or 0) for r in (r1, r2))}

