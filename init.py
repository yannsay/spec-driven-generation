"""Once, before the first run: check the keys, log in to Modal, deploy the two picture models.

Usage: uv run init.py

1. The keys: otari_ai_key (the spec writer, the picture judge, the router and the corrector, on otari.ai) and
   OPENROUTER_API_KEY (the spec judge, Jev), from the environment or .env (copy .env.example); then the otari.ai
   routing policy room-spec, which the spec writer is called through, with one one-token call.
2. Modal: `modal setup` opens the browser to log in, if this machine is not logged in yet; then the Modal secret
   hf-secret, which holds the Hugging Face token the picture models download with.
3. The two picture models, deployed as Modal apps: FLUX.2 klein (the first pictures) and Qwen-Image-Edit (the
   edits). Run this again after a change to pipeline/03 - draw/klein.py or qwen_edit.py.
"""
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
KEYS = ["otari_ai_key", "OPENROUTER_API_KEY"]


def has_key(name):
    env = ROOT / ".env"
    in_file = env.exists() and any(line.startswith(name + "=") and line.split("=", 1)[1].strip()
                                   for line in env.read_text().splitlines())
    return bool(os.environ.get(name)) or in_file


def value(name):
    if os.environ.get(name):
        return os.environ[name]
    env = ROOT / ".env"
    for line in env.read_text().splitlines() if env.exists() else []:
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip().strip('"')
    return ""


def policy_answers(name):
    """One one-token call to the otari.ai routing policy: "" when it answers, else otari's error."""
    body = json.dumps({"model": name, "max_tokens": 1, "messages": [{"role": "user", "content": "hi"}]}).encode()
    req = urllib.request.Request("https://eu.api.otari.ai/api/v1/chat/completions", body,
                                 {"Content-Type": "application/json", "Authorization": "Bearer " + value("otari_ai_key")})
    try:
        urllib.request.urlopen(req, timeout=60).read()
        return ""
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}: {e.read().decode()[:300]}"


def modal(*args):
    return subprocess.run([sys.executable, "-m", "modal", *args], cwd=ROOT).returncode


if __name__ == "__main__":
    missing = [k for k in KEYS if not has_key(k)]
    if missing:
        sys.exit(f"missing in .env: {', '.join(missing)} (copy .env.example to .env and fill it in)")
    print("keys: ok")
    error = policy_answers("room-spec")
    if error:
        sys.exit(f"the otari.ai routing policy room-spec does not answer ({error}).\n"
                 "  Create it in the otari.ai dashboard: Routing → Create policy → Fallback, named room-spec,\n"
                 "  main model deepseek/deepseek-v4.1-flash, fallback alibaba/qwen3.8-max (needs org owner or admin).")
    print("otari.ai policy room-spec: ok")
    logged_in = os.environ.get("MODAL_TOKEN_ID") or (Path.home() / ".modal.toml").exists()
    if not logged_in and modal("setup"):
        sys.exit("modal setup failed")
    print("modal: logged in")
    listed = subprocess.run([sys.executable, "-m", "modal", "secret", "list", "--json"], cwd=ROOT,
                            capture_output=True, text=True)
    if listed.returncode or "hf-secret" not in {s["name"] for s in json.loads(listed.stdout or "[]")}:
        sys.exit("missing the Modal secret hf-secret (the Hugging Face token the picture models download with):\n"
                 "  uv run modal secret create hf-secret HF_TOKEN=<your Hugging Face token>")
    print("modal secret hf-secret: ok")
    for name, file in [("FLUX.2 klein", "pipeline/03 - draw/klein.py"), ("Qwen-Image-Edit", "pipeline/03 - draw/qwen_edit.py")]:
        if modal("deploy", file):
            sys.exit(f"deploying {name} failed")
    print("ready: uv run run.py <run> --rooms LOC_BUILDING,LOC_GRATE")
