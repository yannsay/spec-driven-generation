"""Stage 06, the loop: spec stage → draw → judge → route → correct → judge again, at most two rounds, then a person.

Usage: uv run "06 - loop/image_loop.py" <run> --specs <folder in outputs/specs/written> --rooms LOC_START,LOC_GRATE [--rounds 2]
       [--openrouter]

1. The spec stage: each room's spec is copied to outputs/specs/checked/<run>/ and goes through check_spec.py, the Jev spec judge
   and up to two repairs (02 - spec/repair_spec.py). A part still flagged goes to a person and is not drawn: the
   room's exits hold back the whole room.
2. Waves: a picture is drawn once the picture it depends on is done. Only a state picture depends on another, its
   first picture (`edit_of`: the grate open on the grate closed), and is drawn from it by Qwen-Image-Edit with the
   edit's own prompt (its decisions' draw sentences). Wave 1 in <run>, the next in <run>_w2, … A picture whose first picture ended not done waits,
   listed for the person.
3. Each round of a wave:
   a. Gemma 4 judges each new picture twice (04 - judge); a check passes only when both runs pass it.
   b. Every picture with a failed or flipped check is routed (05 - route/route_image.py).
   c. A picture whose routes are all `keep` is done. The others, while rounds are left, are corrected in a new run
      <run>_r<n>: `new` because of the picture → klein draws CANDIDATES pictures with the same prompt and the best is
      kept; `new` because of the spec → the corrector rewrites the decisions (05 - route/correct_images.py) and klein
      redraws; `edit` → up to two changes with the short prompt (05 - route/edit_images.py), drawn by
      Qwen-Image-Edit; more changes than that → drawn again with the same prompt.
4. The best version is kept: across rounds, the version with the most checks passed (as a share); on a tie, a done
   version (every route keep) wins, then the earliest. Every version stays on disk; a picture sent to a person comes with all of them.
After the last round, every picture that still has a route other than `keep` goes to a person.
Writes outputs/pictures/<run>/loop.json: per round, per picture, the counts, the routes' actions, what was done, and
the seconds of each step. The page: uv run common/pipeline_viewer.py <run>.
Drawing and edits: the two deployed apps on Modal, klein and Qwen-Image-Edit, through 03 - draw/call_deployed.py;
deploy them once with init.py. With --openrouter: Seedream 5.0 Flash on OpenRouter draws and edits, no Modal.
"""
import json
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor as Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))
from paths import INPUT, PICTURES, PIPE, SPECS, STYLES, WRITTEN, stages
stages()

from judge_steps import TAG, combine, judge_once
from llm_image_judge import pictures
from route_image import route, save, sent_prompt

DRAW, ROUTE = PIPE / "03 - draw", PIPE / "05 - route"
STYLE = STYLES / "comic70s.txt"
CANDIDATES = 2  # a redraw whose prompt was right is drawn this many times
OPENROUTER = "--openrouter" in sys.argv  # Seedream on OpenRouter draws and edits, instead of klein and Qwen on Modal
BACKEND = ["--openrouter"] if OPENROUTER else []
DRAWER, EDITOR = ("Seedream 5.0 Flash (OpenRouter)",) * 2 if OPENROUTER else ("FLUX.2 klein (Modal)", "Qwen-Image-Edit (Modal)")


def say(what):
    """One line on the screen when a step starts: the time, then what runs and where."""
    print(f"[{time.strftime('%H:%M:%S')}] {what}", flush=True)


def sh(cmd):
    """Run a step's own script; its output is printed as it comes, and returned."""
    proc = subprocess.Popen(cmd, cwd=PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    lines = []
    for line in proc.stdout:
        print("    " + line, end="", flush=True)
        lines.append(line)
    if proc.wait():
        raise SystemExit(f"step failed: {' '.join(cmd)}")
    return "".join(lines)


def specs_of(run, rooms):
    return {room: json.loads((SPECS / run / f"{room}.json").read_text()) for room in rooms}


def qwen_edits(run, entries):
    """Draw an edit plan with the deployed Qwen-Image-Edit app, all edits at once."""
    plan = PICTURES / run / "qwen_edits.json"
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text(json.dumps(entries, indent=1, ensure_ascii=False) + "\n")
    say(f"editing {len(entries)} picture(s) with {EDITOR}")
    sh([sys.executable, str(DRAW / "call_deployed.py"), "edits", "--plan", str(plan), *BACKEND])


def draw(run, rooms, only, candidates=0):
    """klein draws the first pictures; Qwen-Image-Edit draws each state picture from its first, with the edit's own
    prompt (its decisions' draw sentences) in the short prompt. `only`: the spec's file names to draw. `candidates`: first pictures drawn that many
    times, as <stem>.c1, <stem>.c2. Every draw takes a fresh random seed."""
    from edit_images import short, sentence
    firsts, edits = [], []
    for room, spec in specs_of(run, rooms).items():
        for im in spec["images"]:
            if im["file"] not in only:
                continue
            if not im.get("edit_of"):
                firsts.append(im["file"])
            else:
                room_dir = PICTURES / run / room
                edits.append({"room": room, "file": im["file"], "ref": str(room_dir / im["edit_of"]),
                              "out": str(room_dir / im["file"]),
                              "prompt": short(im["file"], [sentence(im.get("generation_prompt") or "")])})
    if firsts:
        specs = ",".join(str(SPECS / run / f"{r}.json") for r in rooms)
        say(f"drawing {len(firsts)} picture(s) with {DRAWER}"
            + (f", {candidates} candidates each" if candidates else ""))
        sh([sys.executable, str(DRAW / "call_deployed.py"), "draw", "--spec", specs, "--style", str(STYLE),
            "--run", run, "--only", ",".join(firsts), *(["--n", str(candidates)] if candidates else []), *BACKEND])
    if edits:
        qwen_edits(run, edits)


def first_of(spec_path, file, state):
    """For a state picture: its first picture's final verdict file, so the router knows which checks the edit broke."""
    im = next(i for i in json.loads(Path(spec_path).read_text())["images"] if i["file"] == file)
    s = state.get(Path(im["edit_of"]).name) if im.get("edit_of") else None
    if not s:
        return None
    return PICTURES / s["run"] / Path(spec_path).stem / f"{im['edit_of']}.judge.{TAG}.both.json"


def judge_and_route(run, rooms, names, state):
    """Judge twice and route the pictures of `run` named in `names` (file names, e.g. LOC_START.jpg). A verdict
    already on disk is not asked again (a stopped run picks up where it was)."""
    todo = [t for t in pictures(run, names) if Path(t[0]).stem in rooms]
    jobs = [(*t, r) for t in todo for r in ("r1", "r2")]
    say(f"judging {len(todo)} picture(s), twice each, with Gemma 4 (otari.ai)")
    with Pool(4) as ex:
        verdicts = list(ex.map(judge_once, jobs))
    got = {}
    for (spec, file, path, r), v in zip(jobs, verdicts):
        got.setdefault(path, {})[r] = v
    results, flagged = {}, []
    for spec, file, path in todo:
        both = combine(path, got[path]["r1"], got[path]["r2"])
        Path(f"{path}.judge.{TAG}.both.json").write_text(json.dumps(both, indent=2, ensure_ascii=False) + "\n")
        n = Counter(r["result"] for r in both.get("results", []))
        results[path] = {"room": Path(spec).stem, "file": file, "pass": n["pass"], "flip": n["flip"],
                         "fail": n["fail"], "checks": sum(n.values()), "error": both.get("error")}
        if n["flip"] or n["fail"]:
            flagged.append((spec, file, path))

    def routed(t):
        spec, file, path = t
        done = next(Path(path).parent.glob(f"{Path(path).name}.route.*.json"), None)
        if done:
            return path, json.loads(done.read_text())
        v = route(spec, file, path, first_verdict=first_of(spec, file, state))
        if v is not None:
            save(path, v)
        return path, v

    if flagged:
        say(f"routing {len(flagged)} picture(s) with a failed check, with Gemma 4 (otari.ai): spec, picture or judge?")
    with Pool(4) as ex:
        for path, v in ex.map(routed, flagged):
            if v is None:
                continue
            results[path]["actions"] = dict(Counter(r.get("action") for r in v.get("routes", [])))
            results[path]["routes"] = [{k: r.get(k) for k in ("id", "cause", "action", "fix")} for r in v.get("routes", [])]
    return results


def to_fix(results):
    """File names of the pictures with a route other than keep."""
    return [Path(p).name for p, r in results.items() if any(a != "keep" for a in r.get("actions", {}))]


def candidate_files(run, rooms, results):
    """The first pictures whose redraw has cause `image` only (the prompt was right): spec file names. A state
    picture is redrawn as one edit (not built: candidates for edits)."""
    specs, out = specs_of(run, rooms), []
    for r in results.values():
        new = [x for x in r.get("routes", []) if x.get("action") == "new"]
        if new and all(x.get("cause") == "image" for x in new):
            im = next(i for i in specs[r["room"]]["images"] if i["file"] == r["file"])
            if not im.get("edit_of"):
                out.append(r["file"])
    return out


def pick_best(new_run, rooms, files):
    """Judge each candidate twice and copy the one with the most checks passed to the picture's own file (on a tie,
    the later candidate); the others stay as they are. Returns {file: {"candidates": [(path, passed, checks)], "best"}}."""
    specs, jobs = specs_of(new_run, rooms), []
    for f in files:
        room = next(r for r in rooms if any(i["file"] == f for i in specs[r]["images"]))
        for k in range(1, CANDIDATES + 1):
            path = PICTURES / new_run / room / Path(f).with_name(f"{Path(f).stem}.c{k}{Path(f).suffix}")
            jobs += [(str(SPECS / new_run / f"{room}.json"), f, path, r) for r in ("r1", "r2")]
    with Pool(4) as ex:
        got = list(ex.map(judge_once, jobs))
    by, chosen = {}, {}
    for (spec, f, path, r), v in zip(jobs, got):
        by.setdefault((f, path), {})[r] = v
    for (f, path), v in by.items():
        both = combine(path, v["r1"], v["r2"])
        Path(f"{path}.judge.{TAG}.both.json").write_text(json.dumps(both, indent=2, ensure_ascii=False) + "\n")
        n = Counter(x["result"] for x in both.get("results", []))
        chosen.setdefault(f, []).append((str(path), n["pass"], sum(n.values())))
    for f, cands in chosen.items():
        best = max(cands, key=lambda c: (c[1] / max(c[2], 1), cands.index(c)))
        target = Path(best[0]).with_name(Path(f).name)
        shutil.copy(best[0], target)
        for v in Path(best[0]).parent.glob(f"{Path(best[0]).name}.judge.{TAG}.*.json"):  # its verdicts with it:
            shutil.copy(v, target.parent / v.name.replace(Path(best[0]).name, target.name, 1))  # not judged again
        room_dir = target.parents[1]
        with open(room_dir / "log.jsonl", "a") as log:  # the kept candidate's prompt, under the picture's own name
            log.write(json.dumps({"file": f, "prompt": sent_prompt(Path(best[0])), "candidate_of": Path(best[0]).name}) + "\n")
        chosen[f] = {"candidates": cands, "best": Path(best[0]).name}
        print(f"candidates {Path(f).name}: " + ", ".join(f"{Path(c[0]).name} {c[2] - c[1]} fail" for c in cands)
              + f" -> {Path(best[0]).name}")
    return chosen


def correct(run, new_run, names, rooms, allowed, results):
    """`new` then `edit` for the named pictures, into new_run; returns the file names redrawn or edited.
    `allowed`: the file names of this pass. correct_images.py also lists the state pictures of a redrawn first
    picture; they are left for their own wave."""
    done = []
    cands = candidate_files(run, rooms, results)
    others = [n for n in names if n not in {Path(c).name for c in cands}]
    out = ""
    if others:
        say(f"correcting {len(others)} picture(s) with Gemma 4 (otari.ai): the spec rewritten, or the picture redrawn")
        out = sh([sys.executable, str(ROUTE / "correct_images.py"), run, new_run, "--only", ",".join(others)])
    else:  # nothing for the corrector: the new run still starts as a copy of this one
        (SPECS / new_run).mkdir(parents=True, exist_ok=True)
        for room in rooms:
            shutil.copy(SPECS / run / f"{room}.json", SPECS / new_run / f"{room}.json")
        shutil.copytree(PICTURES / run, PICTURES / new_run, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("*.json", "log.jsonl"))
    if cands:
        draw(new_run, rooms, cands, candidates=CANDIDATES)
        chosen = pick_best(new_run, rooms, cands)
        (PICTURES / new_run / "candidates.json").write_text(json.dumps(chosen, indent=1) + "\n")
        done += [Path(f).name for f in cands]
    redraw = re.search(r"^redraw: (.*)$", out, re.M)
    redraw = [f for f in (redraw.group(1).split(",") if redraw else []) if f and Path(f).name in allowed]
    if redraw:
        draw(new_run, rooms, redraw)
        done += [Path(f).name for f in redraw]
    say("writing the edit plan from the router's fixes")
    sh([sys.executable, str(ROUTE / "edit_images.py"), run, new_run, "--only", ",".join(names)])
    plan = PICTURES / new_run / "edits.json"
    entries = json.loads(plan.read_text()) if plan.exists() else []
    too_many = PICTURES / new_run / "too_many.json"  # more than 2 changes: drawn again with the same prompt
    again = [t["file"] for t in (json.loads(too_many.read_text()) if too_many.exists() else [])
             if Path(t["file"]).name in allowed and Path(t["file"]).name not in done]
    if again:
        draw(new_run, rooms, again)
        done += [Path(f).name for f in again]
    entries = [e for e in entries if Path(e["file"]).name not in done]  # a redraw wins over an edit
    if entries:
        qwen_edits(new_run, entries)
        done += [Path(e["file"]).name for e in entries]
    return done


def done(s):
    """A picture is done when it was judged without error and every route it got is `keep`."""
    return bool(s) and not s.get("error") and all(a == "keep" for a in s.get("actions", {}))


def run_pass(n_pass, start, files, rooms, rounds, log, state):
    """The rounds of one wave, from the run `start` where its pictures are drawn; returns the last run."""
    names = sorted(Path(f).name for f in files)
    allowed, current = set(names), start
    for n in range(rounds + 1):
        t0 = time.time()
        results = judge_and_route(current, rooms, names, state)
        seconds = {"judge_route": round(time.time() - t0, 1)}
        for path, r in results.items():  # every version kept; the best is the picture's state
            name = Path(path).name
            versions = state.get(name, {}).get("versions", []) + [{**r, "run": current}]
            best = max(range(len(versions)),  # the most checks passed; on a tie a done version, then the earliest
                       key=lambda i: (versions[i]["pass"] / max(versions[i]["checks"], 1), done(versions[i]), -i))
            state[name] = {**versions[best], "versions": versions}
        fix = to_fix(results)
        entry = {"pass": n_pass, "round": n, "run": current, "judged": names,
                 "results": {Path(p).name: r for p, r in results.items()}, "to_fix": fix}
        for p, r in sorted(results.items()):
            acts = ", ".join(f"{a} {k}" for a, k in r.get("actions", {}).items()) or "-"
            print(f"wave {n_pass} round {n}  {Path(p).name:34} {r['checks']} checks · {r['flip'] + r['fail']} fail   "
                  f"router: {acts}")
        if not fix or n == rounds:
            entry["seconds"] = seconds
            log["rounds"].append(entry)
            return current
        new_run = f"{start}_r{n + 1}"
        t0 = time.time()
        names = correct(current, new_run, fix, rooms, allowed, results)
        seconds["correct_draw"] = round(time.time() - t0, 1)
        entry.update(corrected=names, next_run=new_run, seconds=seconds)
        log["rounds"].append(entry)
        if not names:
            print("nothing could be corrected (a rejected correction)")
            return current
        current = new_run
    return current


def depends_on(run, rooms):
    """{file: (the file it waits for, or None; its room)}. Only a state picture waits, for its first picture."""
    return {im["file"]: (im.get("edit_of"), room) for room, spec in specs_of(run, rooms).items() for im in spec["images"]}


def new_wave(prev, run_w, rooms, state):
    """The folder of the next wave: the specs and pictures of the last run, then each kept version where an earlier
    round was better."""
    (SPECS / run_w).mkdir(parents=True, exist_ok=True)
    for room in rooms:
        shutil.copy(SPECS / prev / f"{room}.json", SPECS / run_w / f"{room}.json")
    shutil.copytree(PICTURES / prev, PICTURES / run_w, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("*.json", "log.jsonl"))
    for name, st in state.items():
        src = next((PICTURES / st["run"]).glob(f"*/*/{name}"), None)
        if src and st["run"] != prev:
            shutil.copy(src, PICTURES / run_w / src.relative_to(PICTURES / st["run"]))


def spec_stage(run, spec_folder, rooms):
    """Each room's spec copied into the run, then check_spec, Jev and up to two repairs. Returns {file: why} for the
    pictures held back: a part still flagged goes to a person; the room's exits hold back the whole room."""
    import repair_spec
    (SPECS / run).mkdir(parents=True, exist_ok=True)
    blocked = {}
    for room in rooms:
        if not (WRITTEN / spec_folder / f"{room}.json").exists():  # the writer gave no valid spec: the room goes
            blocked[room] = "no spec: the writer's answer was not a valid spec"  # to a person, the run goes on
            say(f"skipping {room}: no spec written")
            continue
        shutil.copy(WRITTEN / spec_folder / f"{room}.json", SPECS / run / f"{room}.json")
        trace = WRITTEN / spec_folder / f"{room}.trace.json"  # the writer's first answer, for the page
        if trace.exists():
            shutil.copy(trace, SPECS / run / trace.name)
        say(f"verifying the spec of {room}: the rules in code, then Jev (OpenRouter); a flagged part is repaired "
            "by DeepSeek (otari.ai), at most twice")
        person = repair_spec.run(str(INPUT / f"{room}.yaml"), str(SPECS / run / f"{room}.json"))
        for im in json.loads((SPECS / run / f"{room}.json").read_text())["images"]:
            if "room" in person or im["file"] in person:
                blocked[im["file"]] = "spec flagged after the repairs"
    return blocked


def main(run, spec_folder, rooms, rounds):
    log = {"run": run, "specs": spec_folder, "rooms": rooms, "rounds": []}
    blocked = spec_stage(run, spec_folder, rooms)
    log["spec_stage"] = blocked
    rooms = [r for r in rooms if (SPECS / run / f"{r}.json").exists()]  # the rooms that have a spec
    state = {}
    left = {f: d for f, d in depends_on(run, rooms).items() if f not in blocked}
    prev, n = None, 0
    while True:
        go = [f for f, (dep, _) in left.items() if dep is None or Path(dep).name in state and done(state[Path(dep).name])]
        if not go:
            break
        n += 1
        run_w = run if n == 1 else f"{run}_w{n}"
        if prev:
            new_wave(prev, run_w, rooms, state)
        draw(run_w, rooms, go)
        prev = run_pass(n, run_w, go, rooms, rounds, log, state)
        for f in go:
            left.pop(f)
    finish(run, log, state, {**{f: dep for f, (dep, _) in left.items()}, **blocked})


def finish(run, log, state, waiting):
    person = {f: s for f, s in state.items() if not done(s)}
    log.update(waiting=waiting, to_a_person=person,  # with every version: the kept one is `run`
               kept={f: {"run": s["run"], "pass": s.get("pass"), "checks": s.get("checks"), "done": done(s)}
                     for f, s in state.items()})
    (PICTURES / run).mkdir(parents=True, exist_ok=True)
    (PICTURES / run / "loop.json").write_text(json.dumps(log, indent=1, ensure_ascii=False) + "\n")
    print("\nto a person:" if person or waiting else "\nnothing left for a person")
    for f, s in person.items():
        fails = lambda v: f"{(v.get('checks') or 0) - (v.get('pass') or 0)} fail"
        tries = ", ".join(f"{v['run']} {fails(v)}" for v in s.get("versions", []))
        print(f"  {f:34} kept {s['run']} {fails(s)}; versions: {tries}")
    for f, why in waiting.items():
        print(f"  {Path(f).name:34} not drawn: {'waits for ' + Path(why).name if why.endswith(('.jpg', '.png')) else why}")


if __name__ == "__main__":
    args = sys.argv[1:]
    opt = lambda name, default: args.pop(args.index(name) + 1) if name in args else default
    specs, rooms, rounds = opt("--specs", ""), opt("--rooms", ""), int(opt("--rounds", "2"))
    run = [a for a in args if not a.startswith("--")][0]
    main(run, specs, rooms.split(","), rounds)
