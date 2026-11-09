"""Stage 2a: write the art spec of one or more rooms with the room-spec LLM, through otari.ai.

Usage: uv run make_spec.py [--out <name>] LOC_START [...]

Sends prompts/room_spec.md as the system message and ../01 - yaml/llm_input/<ROOM>.yaml as the user message,
streamed, through the otari.ai routing policy POLICY (DeepSeek V4.1 Flash, reasoning on medium, falling back to
Qwen 3.8 Max), with one retry when the answer comes back empty. Rooms run in parallel. For each room it writes, in
outputs/specs/written/<name>/ (default outputs/specs/written/deepseek-v4.1-flash/):
- <ROOM>.json        the spec, when the answer is valid JSON, with the pipeline's fixed checks and the decisions'
                     checks added by check_spec.add_checks, and each picture's prompt joined from its decisions'
                     draw sentences, an edit's too (join_edits);
- <ROOM>.trace.json  the messages sent, the prompt's git hash, the answer, the reasoning, usage, headers.
Needs otari_ai_key, from the environment or the repo's .env.
"""
import concurrent.futures as cf
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))  # env.py

from check_spec import add_checks, join_edits
from env import key
from paths import INPUT, PROMPTS, WRITTEN
from prompt_text import prompt_text

R = Path(__file__).parent
URL = "https://eu.api.otari.ai/api/v1/chat/completions"
# An otari.ai routing policy, sent as the model: DeepSeek V4.1 Flash, falling back to Qwen 3.8 Max when DeepSeek fails
# to start (an outage, a rate limit, a refusal). Created once in the otari dashboard (Routing → Create policy → Fallback).
POLICY = "room-spec"
TAG, MODEL, EXTRA = "deepseek-v4.1-flash", POLICY, {"reasoning_effort": "medium"}
PROMPT = PROMPTS / "room_spec.md"


def api_key():
    return key("otari_ai_key")


def prompt_version(prompt=PROMPT):
    """The prompt's last commit, and whether the working copy differs from it."""
    git = lambda *a: subprocess.run(["git", *a], cwd=R, capture_output=True, text=True).stdout.strip()
    rules = prompt.parent / "rules.md"  # the shared rules, part of the prompt sent
    return {"commit": git("log", "-1", "--format=%h", "--", str(prompt), str(rules)),
            "blob": git("hash-object", str(prompt)), "rules_blob": git("hash-object", str(rules)), "folder": prompt.parent.name,
            "uncommitted_changes": bool(git("status", "--porcelain", "--", str(prompt), str(rules)))}


def call(room, system, key, model, extra, user=None, label="room-spec"):
    """One streamed call. The user message is the room's input, unless `user` is given (repair_spec.py)."""
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user or (INPUT / f"{room}.yaml").read_text()}]
    body = {"model": model, "temperature": 0.2, "stream": True, "stream_options": {"include_usage": True},
            "session_label": label, "metadata": {"run": label, "room": room},
            "messages": messages, **extra}
    req = urllib.request.Request(URL, json.dumps(body).encode(), {
        "Content-Type": "application/json", "Authorization": "Bearer " + key})
    t = time.time(); info = {"request_extra": extra, "headers": {}, "ttft_s": None, "ttfr_s": None,
                             "id": None, "model": None, "usage": None, "finish_reason": None, "n_chunks": 0, "error": None}
    content, reasoning = [], []
    shown = t
    try:
        with urllib.request.urlopen(req, timeout=900) as resp:
            info["headers"] = dict(resp.headers)
            for line in resp:
                line = line.decode().strip()
                if not line.startswith("data:"): continue
                d = line[5:].strip()
                if d == "[DONE]": break
                c = json.loads(d); info["n_chunks"] += 1
                if time.time() - shown >= 30:  # a line every 30 s, so a long answer is not a blank screen
                    shown = time.time()
                    print(f"    {room}: {label}, {int(shown - t)} s, about {info['n_chunks']} tokens so far", flush=True)
                info["id"] = info["id"] or c.get("id"); info["model"] = c.get("model") or info["model"]
                if c.get("usage"): info["usage"] = c["usage"]
                for ch in c.get("choices", []):
                    dl = ch.get("delta") or {}
                    rc = dl.get("reasoning_content") or dl.get("reasoning")
                    if rc:
                        if info["ttfr_s"] is None: info["ttfr_s"] = round(time.time()-t, 2)
                        reasoning.append(rc)
                    if dl.get("content"):
                        if info["ttft_s"] is None: info["ttft_s"] = round(time.time()-t, 2)
                        content.append(dl["content"])
                    if ch.get("finish_reason"): info["finish_reason"] = ch["finish_reason"]
    except urllib.error.HTTPError as e:
        info["headers"] = dict(e.headers); info["error"] = f"HTTP {e.code}: {e.read().decode()[:2000]}"
    except Exception as e:
        info["error"] = repr(e)
    info["latency_s"] = round(time.time()-t, 2)
    return room, {"room": room, "model": model, "temperature": 0.2, "messages": messages,
                  "answer": "".join(content), "reasoning": "".join(reasoning), **info}


def write(room, system, key, first_extra=None):
    """One call through the policy, and one retry when the answer did not finish: a stream cut late (after the
    policy's fallback could act, which is only before the first chunk) or an answer cut short. `first_extra` adds
    request fields to the first try only (run-retry.py caps its tokens to force the retry)."""
    room, trace = call(room, system, key, MODEL, {**EXTRA, **(first_extra or {})})
    if trace["answer"].strip() and trace["finish_reason"] == "stop":
        return room, trace
    print(f"    {room}: no finished answer after {trace['latency_s']:.0f} s (finish_reason {trace['finish_reason']}, "
          f"error {trace['error']}), retrying", flush=True)
    room, again = call(room, system, key, MODEL, EXTRA)
    again["first_try"] = {k: trace.get(k) for k in ("latency_s", "error", "finish_reason", "n_chunks", "usage",
                                                   "headers", "request_extra")}
    return room, again


if __name__ == "__main__":
    args = sys.argv[1:]
    out_name = args.pop(args.index("--out") + 1) if "--out" in args else TAG
    rooms = [a for a in args if a != "--out"]
    model, extra, prompt = MODEL, EXTRA, PROMPT
    key, system, version = api_key(), prompt_text(prompt), prompt_version(prompt)
    print(f"[{time.strftime('%H:%M:%S')}] writing the spec of {', '.join(rooms)} through the otari.ai policy {POLICY} "
          "(DeepSeek V4.1 Flash, falling back to Qwen 3.8 Max)", flush=True)

    OUT = WRITTEN / out_name
    OUT.mkdir(parents=True, exist_ok=True)
    with cf.ThreadPoolExecutor(len(rooms)) as ex:
        for room, trace in ex.map(lambda r: write(r, system, key), rooms):
            trace["prompt"] = {"file": str(prompt.relative_to(R)), **version}
            (OUT / f"{room}.trace.json").write_text(json.dumps(trace, indent=1, ensure_ascii=False) + "\n")
            try:
                spec = json.loads(trace["answer"]); ok = "valid JSON"
                spec = join_edits(add_checks(INPUT / f"{room}.yaml", spec))
                (OUT / f"{room}.json").write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n")
            except ValueError as e:
                ok = f"NOT JSON ({e.__class__.__name__})"
            u = trace["usage"] or {}
            print(f"{room:13} {trace['latency_s']:6.0f}s {ok} out={u.get('completion_tokens')} "
                  f"cost=${u.get('cost_usd')} err={trace['error']}")
