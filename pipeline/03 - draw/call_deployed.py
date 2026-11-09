"""The deployed picture models on Modal, called from this machine: klein draws the first pictures (klein.py), and
Qwen-Image-Edit draws the edits (qwen_edit.py). A container still warm from the previous step is reused.

Deploy both once (again after a change to klein.py or qwen_edit.py): uv run init.py
Then, from 06 - loop/image_loop.py:
  uv run "03 - draw/call_deployed.py" draw --spec a.json,b.json --style styles/comic70s.txt --run <run>
      [--only img/LOC_X.jpg,items/KEYS.png] [--n 2]
  uv run "03 - draw/call_deployed.py" edits --plan plan.json

draw: the first pictures (those without `edit_of`) of one or more room specs. The style file is appended to every
prompt (the item style, <style>_item.txt, to an item's): the spec prompts carry none. 4 steps, a fresh random seed
every draw, logged. `--n` draws that many candidates per picture, named <stem>.c1, <stem>.c2, … (the loop keeps the
best). Writes outputs/pictures/<run>/<ROOM>/<file>, plus the room's log.jsonl (file, seed, seconds, the full prompt
sent) and the run's style.txt and style_item.txt.

edits: an edit plan is a JSON list of {room, file, ref, out, prompt}. All edits are sent at once (starmap), each with
a fresh random seed. Each picture gets a line in its room's log.jsonl (two folders above the file).
"""
import json
import random
import sys
import time
from pathlib import Path

import modal

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))
from paths import PICTURES

KLEIN_STEPS = 4


def candidate(file, k, n):
    """With several candidates per picture, img/X.jpg is drawn as img/X.c1.jpg, img/X.c2.jpg, …"""
    if n == 1:
        return file
    p = Path(file)
    return str(p.with_name(f"{p.stem}.c{k + 1}{p.suffix}"))


def draw(spec, style, run, only="", n=1):
    style_line = Path(style).read_text().strip()
    # items have a style of their own when <style>_item.txt exists: no frame, no paper, a plain background
    item_style = Path(style).with_name(Path(style).stem + "_item.txt")
    item_line = item_style.read_text().strip() if item_style.exists() else style_line
    model = modal.Cls.from_name("klein-imagegen", "Klein")()
    for spec_path in spec.split(","):
        s = json.loads(Path(spec_path).read_text())
        room_dir = PICTURES / run / s["room"]
        room_dir.parent.mkdir(parents=True, exist_ok=True)
        (room_dir.parent / "style.txt").write_text(style_line + "\n")  # the styles this run was drawn with
        (room_dir.parent / "style_item.txt").write_text(item_line + "\n")
        for im in s["images"]:
            if im.get("edit_of") or (only and im["file"] not in only.split(",")):
                continue
            width, height = map(int, im["size"].split("x"))
            fmt = "PNG" if im["file"].endswith(".png") else "JPEG"
            tail = item_line if im["kind"] == "item" else style_line
            prompt = f"{im['generation_prompt']} {tail}"  # room_spec.md: the pipeline adds the closing lines
            for k in range(n):  # each draw takes a fresh random seed, logged
                file, seed = candidate(im["file"], k, n), random.randrange(2**31)
                t0 = time.time()
                data = model.generate.remote(prompt, width, height, None, fmt, seed, KLEIN_STEPS)
                seconds = round(time.time() - t0, 1)
                path = room_dir / file
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                with open(room_dir / "log.jsonl", "a") as log:
                    log.write(json.dumps({"file": file, "seed": seed, "steps": KLEIN_STEPS, "seconds": seconds,
                                          "prompt": prompt}) + "\n")
                print(f"{file}: {seconds}s", flush=True)


def edits(plan):
    entries = json.loads(Path(plan).read_text())
    seeds = [random.randrange(2**31) for _ in entries]
    edit = modal.Cls.from_name("qwen-edit", "QwenEdit")()
    t0 = time.time()
    results = edit.edit.starmap([(e["prompt"], Path(e["ref"]).read_bytes(), "PNG" if e["out"].endswith(".png") else "JPEG", sd)
                                 for e, sd in zip(entries, seeds)])
    for e, sd, (data, gpu, seconds) in zip(entries, seeds, results):
        out = Path(e["out"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        with open(out.parents[1] / "log.jsonl", "a") as log:
            log.write(json.dumps({"file": e["file"], "model": "Qwen/Qwen-Image-Edit-2511", "seed": sd, "gpu": gpu, "seconds": seconds,
                                  "edit_of": e["ref"], "prompt": e["prompt"]}) + "\n")
        print(f"{e['room']}/{e['file']}: {seconds}s on {gpu}")
    print(f"{len(entries)} edits in {round(time.time() - t0, 1)}s")


if __name__ == "__main__":
    args = sys.argv[1:]
    command = args.pop(0) if args else ""

    def opt(name, default=""):
        if name not in args:
            return default
        i = args.index(name)
        value = args[i + 1]
        del args[i:i + 2]
        return value

    if command == "draw":
        draw(opt("--spec"), opt("--style"), opt("--run"), opt("--only"), int(opt("--n", "1")))
    elif command == "edits":
        edits(opt("--plan"))
    else:
        sys.exit(__doc__)
