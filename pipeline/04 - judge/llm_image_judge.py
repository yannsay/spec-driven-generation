"""The image judge's one call: a picture and its checks as numbered questions, sent to a vision LLM on otari.ai
(Gemma 4 here, judge_steps.py), JSON back.

The questions are the picture's checks from its spec. System prompt prompts/image_judge.md. A picture is sent as
JPEG, at most 1024 px a side. The answer is yes or no per question (the judge decides: no "unsure"); a check passes
when the answer matches its expected value. Any other answer is recorded as "unsure", a judge error to see.
"""
import base64
import hashlib
import io
import json
import re
import time
import urllib.request
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))
from paths import stages
stages()

from PIL import Image

from make_spec import URL, api_key
from paths import PICTURES, PILOT, SPECS

PROMPT = Path(__file__).parent / "prompts" / "image_judge.md"
MAX_SIDE = 1024


def image_url(path):
    im = Image.open(path).convert("RGB")
    im.thumbnail((MAX_SIDE, MAX_SIDE))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def ask(model, path, qs):
    listing = "\n".join(f"{q['id']}. {q['question']}" for q in qs)
    # No temperature: the provider's default. At temperature 0 two runs give the same answer on almost every check,
    # so judging twice could not show an unstable answer.
    body = {"model": model, "max_tokens": 4000,
            "messages": [{"role": "system", "content": PROMPT.read_text()},
                         {"role": "user", "content": [
                             {"type": "image_url", "image_url": {"url": image_url(path)}},
                             {"type": "text", "text": f"Questions:\n{listing}"}]}]}
    req = urllib.request.Request(URL, json.dumps(body).encode(), {
        "Content-Type": "application/json", "Authorization": "Bearer " + api_key()})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=300) as r:
        out = json.loads(r.read())
    text = out["choices"][0]["message"].get("content") or ""
    return text, out.get("usage") or {}, round(time.time() - t0, 1)


def parse(text):
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start:end + 1])["answers"]


def judge(model, spec_path, file, path):
    checks = next(i for i in json.loads(Path(spec_path).read_text())["images"] if i["file"] == file)["checks"]
    qs = [{"id": f"q{n}", **c} for n, c in enumerate(checks, 1)]
    try:
        text, usage, seconds = ask(model, str(path), qs)
    except Exception as e:  # one picture's error is reported, the run goes on
        return {"model": model, "image": str(path), "error": repr(e)}
    try:
        got = {a["id"]: a for a in parse(text)}
    except (ValueError, KeyError, TypeError) as e:
        return {"model": model, "image": str(path), "error": f"not JSON: {e!r}", "answer_text": text,
                "usage": usage, "seconds": seconds}
    results = []
    for q in qs:
        a = got.get(q["id"], {})
        ans = str(a.get("answer", "")).lower()
        result = "unsure" if ans not in ("yes", "no") else "pass" if (ans == "yes") == q["expected"] else "fail"
        results.append({**q, **({"parts": a["parts"]} if a.get("parts") else {}),  # the quoted text's parts
                        "answer": ans or "missing", "why": a.get("why", ""), "result": result})
    return {"model": model, "image": str(path), "spec": spec_path, "file": file,
            "prompt_sha1": hashlib.sha1(PROMPT.read_bytes()).hexdigest()[:10], "usage": usage,
            "seconds": seconds, "results": results}


def pictures(run, only):
    """(spec, file, picture path) for each picture of a run that exists, named in `only` (file names) if given."""
    for room in PILOT:
        spec = SPECS / run / f"{room}.json"
        if not spec.exists():  # a run of some rooms only
            continue
        for im in json.loads(spec.read_text())["images"]:
            path = PICTURES / run / room / im["file"]
            if path.exists() and (not only or path.name in only):
                yield str(spec), im["file"], path
