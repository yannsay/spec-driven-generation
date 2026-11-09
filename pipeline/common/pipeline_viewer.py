"""The pipeline viewer: each picture of a run through its chain, from the game's text to done or a person.

  uv run common/pipeline_viewer.py <run> [<run> ...]     # writes outputs/pipeline_<first run>.html

It imports no stage: it reads only what the stages wrote (specs, verdicts, pictures, logs), so it works on a run at
any point, from a single stage to the whole loop.

Per picture: the game's text (the room's llm_input YAML) → the spec (decisions with their checks, the prompt) → the
spec judge (Jev) and its repair → one card per round: the picture, the prompt the model received (the room's
log.jsonl), the judge ×2 and the router, then what the loop did before the next round (the corrector's rewritten
decisions, or the edit prompt). The last card says whether the picture is done or goes to a person.
A run without loop.json (still going, or a single stage) is read from its folders on disk.
"""
import html
import json
import re
import sys
from pathlib import Path

from paths import INPUT, PICTURES, RUNS, SPECS

esc = html.escape


ORDER = {"fail": 0, "flip": 0, "unsure": 1, "pass": 2}  # in every list, the fails first


def fails(results):
    """The checks that failed: a flip (the judge's two runs disagree) counts as a fail; a Jev unsure does not."""
    return sum(r["result"] in ("fail", "flip") for r in results)


def counts(results):
    return f"{len(results)} checks · {fails(results)} fail"


def rel(path):
    """A path as the page links it, relative to outputs/; a stored path is absolute or relative to the repo."""
    path = Path(path)
    if not path.is_absolute():
        path = RUNS.parent / path
    return esc(str(path.relative_to(RUNS)))


def log_of(room_dir):
    log = {}
    if (room_dir / "log.jsonl").exists():
        for line in (room_dir / "log.jsonl").read_text().splitlines():
            e = json.loads(line)
            log[e["file"]] = e
    return log


def verdict_of(path, kind):
    """The latest verdict file of a picture: kind "judge" (<image>.judge.*.both.json) or "route"."""
    pattern = f"{Path(path).name}.judge.*.both.json" if kind == "judge" else f"{Path(path).name}.route.*.json"
    found = sorted(Path(path).parent.glob(pattern))
    return found[-1] if found else None


def judge_card(path):
    """The picture judge, Gemma 4 twice and combined: the counts, every check that is not a clear pass (question,
    the two answers, the reasons), then every check folded by its origin."""
    both = verdict_of(path, "judge")
    if not both:
        return ""
    v = json.loads(both.read_text())
    if "error" in v:
        return '<div class="card front"><h4>Picture judge (Gemma ×2)</h4><p>error</p></div>'
    items, every = [], {}
    for r in sorted(v["results"], key=lambda r: ORDER.get(r["result"], 1)):
        good = "yes" if r.get("expected", True) else "no"
        if r["result"] != "pass":
            runs = " / ".join(f"{x['answer']}: {x['why']}" for x in r.get("runs", [])) if r["result"] == "flip" else r["why"]
            items.append(f'<li class="{r["result"]}"><b>{r["result"]}</b> {esc(r["id"])} {esc(r["question"])}'
                         f' <span>(good answer: {good}, got: {esc(r["answer"])})</span><br>{esc(runs)}</li>')
        runs = " / ".join(f"{x['answer']}: {x['why']}" for x in r.get("runs", []))
        every.setdefault(r.get("origin", "?"), []).append(
            f'<li class="{r["result"]}"><b>{r["result"]}</b> {esc(r["id"])} {esc(r["question"])}'
            f' <span>(good answer: {good})</span><br>{esc(runs)}</li>')
    all_html = "".join(f"<details><summary>{esc(o)} ({len(li)} checks)</summary><ul>{''.join(li)}</ul></details>"
                       for o, li in every.items())
    return (f'<div class="card front"><h4>Picture judge (Gemma ×2)</h4>'
            f'<p>{counts(v["results"])}</p>'
            f'<ul class="j">{"".join(items)}</ul>'
            f'<details class="j"><summary>all {len(v["results"])} checks</summary>{all_html}</details></div>')


def router_card(path):
    """The router, Gemma 4 with the picture: for each flagged check, the cause, the action, why, and the fix."""
    route = verdict_of(path, "route")
    if not route:
        return ""
    v = json.loads(route.read_text())
    if "error" in v:
        return f'<div class="card front"><h4>Router (Gemma)</h4><p>error: {esc(v["error"])}</p></div>'
    asked = {f["id"]: f["question"] for f in v.get("payload", {}).get("flagged", [])}
    acts = [r.get("action") for r in v.get("routes", [])]
    items = "".join(f'<li><b>{esc(str(r.get("cause")))} → {esc(str(r.get("action")))}</b> {esc(r.get("id", ""))} '
                    f'{esc(asked.get(r.get("id"), ""))}<br><span class="meta">{esc(r.get("why", ""))}</span>'
                    f'<br><b>fix:</b> {esc(r.get("fix", ""))}</li>' for r in v.get("routes", []))
    cost = (v.get("usage") or {}).get("cost_usd")
    return (f'<div class="card front"><h4>Router (Gemma)</h4>'
            f'<p>{", ".join(f"{a} {acts.count(a)}" for a in ("new", "edit", "keep") if a in acts)} · ${cost}</p>'
            f'<ul class="j">{items}</ul></div>')


def corrections(run):
    """What the loop did going into `run`: {file: html}."""
    out = {}
    c = SPECS / run / "correct.json"
    for e in json.loads(c.read_text()) if c.exists() else []:
        parts = []
        for n, t in enumerate(e.get("tries") or [e], 1):
            ds = "".join(f"<li>{esc(d.get('draw', ''))}</li>" for d in (t.get("answer") or {}).get("decisions") or [])
            verdict = "accepted" if t.get("accepted") else f"rejected: {esc(t.get('rejected_because') or '')}"
            parts.append(f"<p><b>Corrector, try {n}</b> ({verdict})</p><ol>{ds}</ol>")
        out[e["file"]] = ("<details open><summary>new: decisions rewritten, picture redrawn</summary>"
                          + "".join(parts) + "</details>")
    q = PICTURES / run / "qwen_edits.json"
    for e in json.loads(q.read_text()) if q.exists() else []:
        if e.get("change"):  # an edit of the loop, not a state picture of round 0
            out[e["file"]] = f"<details open><summary>edit</summary><p>{esc(e['prompt'])}</p></details>"
    return out


def candidates_html(run, file):
    """The candidates drawn for this picture in this round, as large as the picture: the kept one first (the picture
    above is its copy), then the others, each with its score."""
    c = PICTURES / run / "candidates.json"
    got = (json.loads(c.read_text()) if c.exists() else {}).get(file)
    if not got:
        return ""
    cands = sorted(got["candidates"], key=lambda x: Path(x[0]).name != got["best"])
    cells = "".join(f'<div class="cand{" kept" if Path(p).name == got["best"] else ""}"><a href="{rel(p)}"><img src="{rel(p)}"></a>'
                    f'<span>{esc(Path(p).name)} · {t - n} fail · '
                    f'{"kept: the picture above" if Path(p).name == got["best"] else "not kept"}</span></div>'
                    for p, n, t in cands)
    return f'<p class="meta"><b>{len(cands)} candidates, same prompt</b></p><div class="cands">{cells}</div>'


def card(run, room, file, n, result, n_pass=None, kept=False):
    path = PICTURES / run / room / file
    if not path.exists():
        return ['<div class="card empty">–</div>']
    entry = log_of(path.parents[1]).get(file, {})
    m = str(entry.get("model", ""))
    model = "Seedream 5.0 Flash" if "seedream" in m else "Qwen-Image-Edit" if "Qwen-Image-Edit" in m else "klein 4B"
    if not result.get("checks") and verdict_of(path, "judge"):  # a run still going: the score from the verdict on disk
        v = json.loads(verdict_of(path, "judge").read_text()).get("results", [])
        result = {"pass": sum(r["result"] == "pass" for r in v), "checks": len(v)}
    score = f' · {result["checks"] - result["pass"]} fail' if result.get("checks") else ""
    picture = (f'<div class="card{" kept" if kept else ""}"><h4>{"Pass " + str(n_pass) + ", r" if n_pass else "R"}ound {n} · {esc(run)}{score}'
            f'{" · <b>kept</b>" if kept else ""}</h4><a href="{rel(path)}"><img src="{rel(path)}" loading="lazy"></a>'
            f'<div class="meta">{model} · {entry.get("seconds", "?")}s</div>'
            f'<details><summary>prompt sent</summary><p class="prompt">{esc(entry.get("prompt", ""))}</p></details>'
            + candidates_html(run, file) + "</div>")
    arrow = '<div class="arrow">→</div>'
    return [picture] + [x for c in (judge_card(path), router_card(path)) if c for x in (arrow, c)]


def from_disk(run):
    """A run with no loop.json (still going, or a single stage): its rounds from the folders on disk, oldest first,
    with the pictures each drew (its room's log.jsonl names them)."""
    rounds = []
    dirs = sorted((d for d in PICTURES.iterdir() if d.is_dir() and (d.name == run or d.name.startswith(run + "_"))),
                  key=lambda d: (bool(re.search(r"_w\d", d.name)), d.stat().st_mtime))
    for d in dirs:
        results = {}
        for f in sorted(d.glob("LOC_*/*/*")):
            candidate = re.search(r"\.c\d+$", f.stem)  # shown inside its picture's card, not as a picture
            if f.suffix in (".jpg", ".png") and not candidate and f"{f.parts[-2]}/{f.name}" in log_of(f.parents[1]):
                results[f.name] = {"room": f.parts[-3], "file": f"{f.parts[-2]}/{f.name}"}
        n_pass = 2 if re.search(r"_w\d", d.name) else 1  # a wave-2 folder: the state pictures
        rounds.append({"round": sum(r["pass"] == n_pass for r in rounds), "pass": n_pass, "run": d.name,
                       "results": results})
    rooms = {r["room"] for x in rounds for r in x["results"].values()}
    rooms |= {f.stem for f in (SPECS / run).glob("LOC_*.json") if f.name.count(".") == 1}  # specs not drawn yet
    return {"run": run, "rooms": sorted(rooms),
            "specs": "(read from disk)", "rounds": rounds, "to_a_person": {}}


def front_cards(run, room, file):
    """The start of a picture's chain: the game's text → the spec → the spec judge (Jev) and the repair."""
    spec_path = SPECS / run / f"{room}.json"
    if not spec_path.exists():
        return []
    spec = json.loads(spec_path.read_text())
    im = next((i for i in spec["images"] if i["file"] == file), None)
    if not im:
        return []
    src = INPUT / f"{room}.yaml"
    yaml_card = (f'<div class="card front"><h4>Game text (YAML)</h4><p>{"<br>".join(esc(e.get("sentence", "")) for e in im.get("sentences") or [])}</p>'
                 f'<details><summary>whole input</summary><pre class="prompt">{esc(src.read_text())}</pre></details></div>')
    spec_cards = spec_split(spec_path, spec, im)
    jev = spec_path.with_suffix(".jev.json")
    if jev.exists():
        v = json.loads(jev.read_text())
        mine = [r for r in v.get("results", []) if r.get("target") == file or r.get("level") == "room"]
        bad = "".join(f"<li class='fail'>{esc(r['question'][:120])} ({r.get('p_yes')})</li>" for r in mine if r["result"] == "fail")
        repaired = bool(repair_rounds(spec_path, file))
        judge_card = (f'<div class="card front"><h4>Spec judge (Jev){", after the repair" if repaired else ""}</h4>'
                      f'<p>{counts(mine)}</p><ul>{bad}</ul>{all_questions(mine)}</div>')
    else:
        judge_card = '<div class="card front empty"><h4>Spec judge (Jev)</h4><p class="meta">not run on this spec</p></div>'
    arrow = '<div class="arrow">→</div>'
    return [yaml_card, arrow, *spec_cards, *repair_cards(spec_path, file), judge_card, arrow]


def all_questions(results):
    """Every question Jev was asked, folded, grouped by level and kind (the kind from the id: room/exit/X → exit,
    …/q3/drawn → drawn, …/decision2 → decision), each with its result, p(yes) and the good answer."""
    groups = {}
    for r in results:
        parts = r["id"].split("/")
        kind = parts[1] if r["level"] == "room" else parts[-1].rstrip("0123456789")
        groups.setdefault(f"{r['level']} · {kind}", []).append(r)
    groups = dict(sorted(groups.items(), key=lambda g: -fails(g[1])))  # the groups with fails first
    inner = "".join(
        f"<details><summary>{esc(g)} ({counts(rs)})</summary><ul>"
        + "".join(f"<li class='{r['result']}'><b>{r['result']}</b> {esc(r['question'])} "
                  f"<span>(p(yes) {r['p_yes']:.2f}, good answer {'yes' if r['expected'] else 'no'})</span></li>"
                  for r in sorted(rs, key=lambda r: ORDER.get(r["result"], 1)))
        + "</ul></details>" for g, rs in groups.items())
    return f'<details class="j"><summary>all {len(results)} questions</summary>{inner}</details>'


def written(spec_path, im):
    """This picture as the spec writer first answered it (the call's own record, <ROOM>.trace.json), or None."""
    trace = spec_path.with_suffix(".trace.json")
    try:
        raw = json.loads(json.loads(trace.read_text())["answer"])
    except (OSError, ValueError, KeyError):
        return None
    return next((i for i in raw.get("images", []) if i.get("file") == im["file"]), None), raw


def spec_split(spec_path, spec, im):
    """The spec as first written, in two cards, by who wrote what: the spec writer (DeepSeek:
    exits, picture decisions with their reasons and checks, which decisions draw each game sentence) in blue; the
    scripts (check_spec.py: the prompt joined from the draw sentences, the fixed checks, the check list put together)
    in grey. Both before any repair: the writer's first answer comes from the call's own record (<ROOM>.trace.json);
    what the repair changed is in the repair cards that follow."""
    first, raw = written(spec_path, im) or (None, None)
    w = first or im  # without the record, the spec as it is now
    exits_of = (raw or spec).get("exits", [])
    arrow = '<div class="arrow">→</div>'
    items = []
    for d in w.get("picture_decisions") or []:
        if isinstance(d, dict):
            checks = "".join(f"<li class='chk'>{esc(c['question'])}</li>" for c in d.get("checks") or [])
            items.append(f"<li>{esc(d.get('draw', ''))}<br><span class='meta'>why: {esc(d.get('why', ''))}</span><ul>{checks}</ul></li>")
    exits = "".join(f"<li>{'<b>drawn</b>: ' + esc(e.get('label', '')) if e.get('drawn') else 'not drawn'} "
                    f"<span class='meta'>({esc(e['to'])}; {esc(e.get('why', ''))})</span></li>" for e in exits_of)
    sents = "".join(f"<li>{esc(e.get('sentence', ''))} → decisions {', '.join(map(str, e.get('decisions') or []))}</li>"
                    for e in w.get("sentences") or [])
    exit_checks = [c for c in w.get("checks", []) if c.get("origin") == "exit"]
    exit_html = "".join(f"<li>{esc(c['question'])}</li>" for c in exit_checks)
    replaces = (f"<p><b>replaces</b> decisions {', '.join(map(str, w.get('replaces') or [])) or 'none'} of "
                f"{esc(im['edit_of'])}</p>" if im.get("edit_of") else "")
    rest = (f"<details><summary>the other picture decisions ({len(items) - 1})</summary>"
            f"<ol start='2'>{''.join(items[1:])}</ol></details>" if len(items) > 1 else "")
    body = (f"<p><b>Picture decisions</b> (draw, why, its checks): the first of {len(items)}</p>{replaces}"
            f"<ol>{''.join(items[:1])}</ol>{rest}")
    writer = (f'<div class="card front by-llm"><h4>Spec writer (DeepSeek){" · edit of " + esc(im["edit_of"]) if im.get("edit_of") else ""}</h4>'
              + ("" if first else '<p class="meta">the first answer is not on disk: shown as the spec is now</p>')
              + body
              + f"<details><summary>game sentences → decisions</summary><ul>{sents}</ul></details>"
              + f"<details><summary>exits ({len(exits_of)})</summary><ul>{exits}</ul></details>"
              + f"<details><summary>exit checks ({len(exit_checks)})</summary><ul>{exit_html}</ul></details></div>")
    # what the scripts made of that first answer: check_spec.join_edits and add_checks, as they ran in make_spec.py
    how = ("the edit's draw sentences, joined in order (sent to Qwen-Image-Edit in the short edit prompt)"
           if im.get("edit_of") else "the decisions' draw sentences, joined in order")
    prompt = " ".join(d["draw"].strip() for d in w.get("picture_decisions") or [] if isinstance(d, dict) and d.get("draw"))
    required = [c.get("template") or c["question"] if c.get("repaired") else c["question"]
                for c in im.get("checks", []) if c.get("origin") == "required"]
    n_dec = sum(len(d.get("checks") or []) for d in w.get("picture_decisions") or [] if isinstance(d, dict))
    total = len(required) + len(exit_checks) + n_dec
    req_html = "".join(f"<li>{esc(q)}</li>" for q in required)
    script = (f'<div class="card front by-code"><h4>Scripts (check_spec.py)</h4>'
              f'<p>Check list put together: {total} = {len(required)} fixed + {len(exit_checks)} exit + '
              f'{n_dec} from the decisions</p>'
              f'<details><summary>the prompt: {how}</summary><p class="prompt">{esc(prompt)}</p></details>'
              f'<details><summary>fixed checks added ({len(required)})</summary><ul>{req_html}</ul></details></div>')
    return [writer, arrow, script, arrow]


def repair_rounds(spec_path, file):
    """The repair rounds that touched this picture's part of the spec (its image, or the room's exits for a scene)."""
    path = spec_path.with_suffix(".repair.json")
    log = json.loads(path.read_text()) if path.exists() else {}
    parts = {file, "room"}
    return [(r, c) for r in log.get("rounds", []) for c in r.get("calls", []) if c["part"] in parts]


def repair_cards(spec_path, file):
    """Two cards per repair round on this picture's part: what Jev (or the script) flagged, then what the repair
    changed and why, and what the round ended with."""
    arrow, cards = '<div class="arrow">→</div>', []
    for r, c in repair_rounds(spec_path, file):
        flagged = "".join(f"<li class='{e.get('result', 'fail')}'>{esc(e.get('question') or e.get('script', ''))}"
                          + (f" <span>({e['result']}, p(yes) {e['p_yes']}, good answer {e['good_answer']})</span>" if "p_yes" in e else "")
                          + "</li>" for e in c["errors"])
        why = "".join(f"<li><b>changed:</b> {esc(j.get('change', ''))}<br><b>why:</b> {esc(j.get('why', ''))}</li>"
                      for j in c.get("justification") or [])
        end = ("fixed" if c["part"] in r.get("fixed", []) else "undone, to a person" if c["part"] in r.get("undone", {})
               else "judge varied, nothing changed" if c["part"] in r.get("judge_varied", []) else "still flagged")
        cost = (c.get("usage") or {}).get("cost_usd")
        label = "first verdict" if r["round"] == 1 else f"after round {r['round'] - 1}"
        kept = spec_path.with_suffix(f".jev.round{r['round'] - 1}.json")  # that verdict whole, when the run kept it
        if kept.exists():
            mine = [x for x in json.loads(kept.read_text()).get("results", [])
                    if x.get("target") == file or x.get("level") == "room"]
            every = (f'<p>{counts(mine)}</p>' + all_questions(mine))
        else:
            every = '<p class="meta">the whole verdict was not kept by this run: only what it flagged</p>'
        cards += [f'<div class="card front"><h4>Spec judge (Jev), {label}</h4>'
                  f'<p>{len(c["errors"])} flagged on {esc(c["part"])}</p><ul>{flagged}</ul>{every}</div>', arrow,
                  f'<div class="card front"><h4>Spec repair, round {r["round"]} · {esc(c["part"])}</h4>'
                  f'<p>DeepSeek, {c.get("latency_s", "?")}s, ${cost}'
                  f'{": " + esc(c["error"]) if c.get("error") else ""}</p><ul>{why}</ul>'
                  f'<p class="meta">after the round: {end}</p></div>', arrow]
    return cards


def section(run):
    """One run's pictures, each with its chain; a picture not drawn (waiting, or its spec held back) says why."""
    path = PICTURES / run / "loop.json"
    loop = json.loads(path.read_text()) if path.exists() else from_disk(run)
    rounds, person, files = loop["rounds"], loop.get("to_a_person", {}), []
    for r in rounds:
        for res in r["results"].values():
            if (res["room"], res["file"]) not in files:
                files.append((res["room"], res["file"]))
    rows = []
    for room, file in files:
        name, cards = Path(file).name, []
        for n, r in enumerate(rounds):
            if name not in r["results"]:
                continue
            kept = loop.get("kept", {}).get(name, {}).get("run") == r["run"]
            cards += card(r["run"], room, file, r.get("round", n), r["results"][name], r.get("pass"), kept)
            nxt = r.get("next_run") or (rounds[n + 1]["run"] if n + 1 < len(rounds) and r.get("corrected") else None)
            if nxt and name in (r.get("corrected") or []):
                cards.append(f'<div class="step">→{corrections(nxt).get(file, "")}</div>')
        end = ("" if not path.exists() else '<div class="end person">to a person</div>'
               if name in person else '<div class="end done">done</div>')
        rows.append(f"<h3>{esc(room)} · {esc(file)}</h3><div class='row'>{''.join(front_cards(run, room, file) + cards)}{end}</div>")
    waiting = dict(loop.get("waiting", {}))
    for room in loop["rooms"]:  # every picture of the specs not drawn: held back at the spec stage, or not reached yet
        spec_path = SPECS / run / f"{room}.json"
        if not spec_path.exists():
            continue
        repair = spec_path.with_suffix(".repair.json")
        person = json.loads(repair.read_text()).get("to_a_person", {}) if repair.exists() else {}
        for im in json.loads(spec_path.read_text())["images"]:
            if (room, im["file"]) in files or im["file"] in waiting:
                continue
            waiting[im["file"]] = ("spec flagged after the repairs: to a person" if "room" in person or im["file"] in person
                                   else "not drawn yet" if not path.exists() else "not drawn")
    for file, why in waiting.items():
        room = next((r for r in loop["rooms"] if (SPECS / run / f"{r}.json").exists()
                     and any(i["file"] == file for i in json.loads((SPECS / run / f"{r}.json").read_text())["images"])), "")
        why = f"waits for {Path(why).name}" if str(why).endswith((".jpg", ".png")) else why
        rows.append(f"<h3>{esc(room)} · {esc(file)}</h3><div class='row'>{''.join(front_cards(run, room, file))}"
                    f'<div class="end person">not drawn: {esc(why)}</div></div>')
    return (f'<h2>Run {esc(run)}</h2><p class="sub">Rooms {esc(", ".join(loop["rooms"]))}, specs {esc(loop["specs"])}.</p>'
            + "".join(rows))


def page(runs):
    return PAGE.format(run=esc(", ".join(runs)), body="".join(section(r) for r in runs))


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Pipeline viewer</title>
<style>
  :root {{ color-scheme: light dark; --bg: #f6f5f1; --fg: #222; --muted: #666; --card: #fff; --line: #ddd; }}
  @media (prefers-color-scheme: dark) {{ :root {{ --bg: #1b1b1b; --fg: #eee; --muted: #aaa; --card: #262626; --line: #444; }} }}
  body {{ margin: 0; padding: 16px; background: var(--bg); color: var(--fg); font: 14px/1.4 system-ui, sans-serif; }}
  h1 {{ font-size: 20px; margin: 0 0 4px; }} h2 {{ font-size: 17px; margin: 30px 0 2px; border-top: 1px solid var(--line); padding-top: 12px; }} h3 {{ margin: 26px 0 8px; font-size: 15px; }} h4 {{ margin: 0 0 6px; font-size: 13px; }}
  p {{ margin: 4px 0; }} .sub {{ color: var(--muted); }}
  .row {{ display: flex; gap: 10px; overflow-x: auto; align-items: flex-start; padding-bottom: 6px; }}
  .card {{ flex: 0 0 360px; background: var(--card); border: 1px solid var(--line); border-radius: 6px; padding: 8px; }}
  .card img {{ width: 100%; display: block; }}
  .meta {{ color: var(--muted); font-size: 12px; margin: 4px 0; }}
  .step {{ flex: 0 0 300px; font-size: 12px; }} .step ol {{ padding-left: 18px; margin: 4px 0; }}
  .prompt {{ font-size: 12px; color: var(--muted); }}
  details.j {{ font-size: 12px; }} details.j ul {{ padding-left: 16px; margin: 4px 0; }} details.j li {{ margin-bottom: 6px; }}
  li.flip b {{ color: #b07a00; }} li.fail b {{ color: #c0392b; }} li span {{ color: var(--muted); }}
  .end {{ flex: 0 0 110px; font-weight: 600; align-self: center; text-align: center; padding: 8px; border-radius: 6px; }}
  .person {{ color: #c0392b; border: 1px solid #c0392b; }} .done {{ color: #2e7d32; border: 1px solid #2e7d32; }}
  .card.front {{ flex: 0 0 300px; font-size: 12px; }}
  .arrow {{ align-self: center; font-size: 22px; color: var(--muted); }}
  li.chk {{ color: var(--muted); }} .card.empty {{ opacity: .6; }}
  li.fail {{ color: #c0392b; }} li.flip {{ color: #b07a00; }}
  .by-llm {{ border-left: 4px solid #1976d2; }} .by-code {{ border-left: 4px solid #888; }}
  .card.kept {{ border: 2px solid #2e7d32; }} .cands {{ display: flex; flex-direction: column; gap: 8px; }} .cand {{ font-size: 12px; }}
  .cand img {{ width: 100%; }} .cand.kept img {{ outline: 2px solid #2e7d32; }}
</style></head><body>
<h1>Pipeline · {run}</h1>
<p class="sub">Drawing: klein 4B, 4 steps; edits: Qwen-Image-Edit, 8 steps (short prompt); with --openrouter both
are Seedream 5.0 Flash (the model is named under each picture);
Gemma 4 judges twice, a check passes only when both runs pass it; the router sorts what did not. The kept version
(green) is the one with the most checks passed across rounds; on a tie a done one, then the earliest; every version is shown.</p>
{body}
</body></html>"""

if __name__ == "__main__":
    runs = sys.argv[1:]  # one page for several runs, named after the first
    out = RUNS / f"pipeline_{runs[0]}.html"
    out.write_text(page(runs))
    print(out)
