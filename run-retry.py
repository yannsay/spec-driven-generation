"""Show the writer's retry: the first call is capped at a few tokens, so its answer comes back cut, and the writer
calls again through the otari.ai routing policy. Writes the spec only (no drawing).

Usage: uv run run-retry.py [LOC_START]

Writes outputs/specs/written/retry_test/<ROOM>.json and its trace; the trace's `first_try` holds the cut call,
`headers` the retry's (Otari-Model: the model that answered; Otari-Fallback: whether the policy fell back).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "pipeline" / "common"))
from paths import INPUT, WRITTEN, stages

stages()
from check_spec import add_checks, join_edits
from make_spec import POLICY, PROMPT, api_key, prompt_text, write

FIRST_TRY = {"max_tokens": 64}  # the reasoning alone uses more: the first answer is cut before any spec

if __name__ == "__main__":
    room = (sys.argv[1:] or ["LOC_START"])[0]
    print(f"writing the spec of {room} through the otari.ai policy {POLICY}; first try capped at "
          f"{FIRST_TRY['max_tokens']} tokens", flush=True)
    room, trace = write(room, prompt_text(PROMPT), api_key(), first_extra=FIRST_TRY)
    out = WRITTEN / "retry_test"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{room}.trace.json").write_text(json.dumps(trace, indent=1, ensure_ascii=False) + "\n")
    first = trace.get("first_try") or {}
    h = {k.lower(): v for k, v in (trace.get("headers") or {}).items()}
    print(f"first try: {first.get('latency_s')} s, finish_reason {first.get('finish_reason')}")
    print(f"retry: {trace['latency_s']} s, finish_reason {trace['finish_reason']}, answered by {h.get('otari-model')}, "
          f"fallback {h.get('otari-fallback')}, cost ${(trace.get('usage') or {}).get('cost_usd')}")
    try:
        spec = join_edits(add_checks(INPUT / f"{room}.yaml", json.loads(trace["answer"])))
        (out / f"{room}.json").write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n")
        print(f"spec written: {out / (room + '.json')}")
    except ValueError as e:
        print(f"the retry's answer is not a valid spec either ({e.__class__.__name__})")
