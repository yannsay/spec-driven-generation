"""Stage 05c, the `edit` action: an edit plan for the pictures that are right except for one or two things, built by
the code from the router's verdict. No LLM call; Qwen-Image-Edit draws the plan (03 - draw/qwen_edit.py).

Usage: uv run edit_images.py <run> <new run> [--only LOC_START.jpg,...] [--dry-run]

A picture is edited when its router verdict asks for `edit` on at least one check and for `new` on none: a redraw
wins over an edit (the corrector, or new candidates, own those). The changes are the router's `fix` sentences for
the edit routes, in order, deduplicated, already written as instructions to the
image model. The prompt is the short one: "Using image 1 as the base, <changes>
Keep everything else exactly as it is.", no keep list and no style line; an item's keeps its flat magenta background (as its style line asks).
A picture needing more than CHANGES changes is not nearly right: it is listed in too_many.json, and the loop draws it
again. The reference is always the picture of <run>.

Writes the plan to outputs/pictures/<new run>/edits.json and too_many.json, and prints every prompt; `--dry-run` stops
before writing.
"""
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))
from paths import PICTURES, SPECS, stages
stages()

from llm_image_judge import pictures
from route_image import NAME

CHANGES = 2  # up to two changes for Qwen-Image-Edit
SHORT = "Using image 1 as the base, {change} Keep everything else exactly as it is."
SHORT_ITEM = ("Using image 1 as the base, {change} Keep everything else exactly as it is, on the same flat magenta "
              "(#FF00FF) background.")


def sentence(text):
    text = text.strip()
    return text if text.endswith((".", "!", "?")) else text + "."


def short(file, changes):
    """The short edit prompt; the change's first letter lower-cased, as it follows "…as the base,"."""
    change = " ".join(changes).strip()
    word = change.split(" ")[0] if change else ""
    if not (len(word) > 1 and word.isupper()):  # "The" → "the", "A" → "a"; a word in capitals ("XYZZY") stays
        change = change[:1].lower() + change[1:]
    return (SHORT_ITEM if file.startswith("items/") else SHORT).format(change=change)


def edit_routes(path):
    """The routes asking for an edit, each with its question, or None when the picture is not to be edited."""
    route_file = Path(f"{path}.route.{NAME}.json")
    if not route_file.exists():
        return None
    v = json.loads(route_file.read_text())
    routes = v.get("routes", [])
    if any(r.get("action") == "new" for r in routes):  # a redraw wins over an edit
        return None
    asked = {f["id"]: f["question"] for f in v.get("payload", {}).get("flagged", [])}  # a route names an id only
    edits = [{**r, "question": asked.get(r.get("id"), "")} for r in routes if r.get("action") == "edit"]
    return edits or None


def plan(run, new_run, only):
    """(the pictures to edit, the pictures the router asked to edit that need more than CHANGES changes)."""
    entries, too_many = [], []
    for spec_path, file, path in pictures(run, only):
        edits = edit_routes(path)
        if not edits:
            continue
        change = list(dict.fromkeys(sentence(r["fix"]) for r in edits if r.get("fix")))
        spec = json.loads(Path(spec_path).read_text())
        if len(change) > CHANGES:
            too_many.append({"room": spec["room"], "file": file, "changes": change})
            continue
        im = next(i for i in spec["images"] if i["file"] == file)
        entries.append({"room": spec["room"], "file": file, "size": im["size"], "ref": str(path),
                        "out": str(PICTURES / new_run / spec["room"] / file),
                        "fmt": "PNG" if file.endswith(".png") else "JPEG", "prompt": short(file, change),
                        "change": change,
                        "routes": [{k: r.get(k) for k in ("id", "cause", "question", "why", "fix")} for r in edits]})
    return entries, too_many


if __name__ == "__main__":
    args = sys.argv[1:]
    only = args.pop(args.index("--only") + 1).split(",") if "--only" in args else []
    dry = "--dry-run" in args
    run, new_run = [a for a in args if not a.startswith("--")]
    entries, too_many = plan(run, new_run, only)
    for e in entries:
        print(f"== {e['room']}  {e['file']}\n   {e['prompt']}\n")
    for t in too_many:
        print(f"-- {t['room']}  {t['file']}: {len(t['changes'])} changes, more than {CHANGES}: drawn again")
    if dry:
        print(f"{len(entries)} pictures to edit, {len(too_many)} to draw again (dry run, nothing written)")
        sys.exit()
    if not (PICTURES / new_run).exists():  # the earlier pictures, so an edit's run holds the whole set
        shutil.copytree(PICTURES / run, PICTURES / new_run, ignore=shutil.ignore_patterns("*.json", "log.jsonl"))
    (SPECS / new_run).mkdir(parents=True, exist_ok=True)
    for spec in (SPECS / run).glob("LOC_*.json"):  # an edit changes no spec; the new run keeps the same ones
        if spec.name.count(".") == 1 and not (SPECS / new_run / spec.name).exists():
            shutil.copy(spec, SPECS / new_run / spec.name)
    out = PICTURES / new_run / "edits.json"
    out.write_text(json.dumps(entries, indent=1, ensure_ascii=False) + "\n")
    (out.parent / "too_many.json").write_text(json.dumps(too_many, indent=1, ensure_ascii=False) + "\n")
    print(f"{len(entries)} pictures to edit, {len(too_many)} to draw again; plan: {out}")
