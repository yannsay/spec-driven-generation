"""Where the repo is, and the API keys: from the environment, else from the repo's .env (not in git)."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # the repo: <repo>/pipeline/common/env.py
ENV = ROOT / ".env"


def key(name):
    if os.environ.get(name):
        return os.environ[name]
    if ENV.exists():
        for line in ENV.read_text().splitlines():
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip().strip('"')
    sys.exit(f"{name} is not set")
