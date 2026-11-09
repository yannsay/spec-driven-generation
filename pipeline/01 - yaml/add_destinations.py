"""Stage 1b: add to a room's input where each of its exits leads: each destination's description, from the game.

Usage: uv run add_destinations.py <adventure.yaml> LOC_GRATE [...]

Appends a section to llm_input/<ROOM>.yaml, after the objects:

    # where each exit leads (the destination's description in the game)
    # LOC_FOREST7: You are wandering aimlessly through the forest.

as YAML comments: the scripts' YAML reader skips them, and every model that reads the input (the spec writer, the
spec judge, the repair) sees them. Without it the writer knows an exit only by its verbs ("EAST") and invents what
the way out looks like (dirt tracks where the game has forest). A section already
there is replaced, so running it again is safe.
"""
import re
import sys
from pathlib import Path

import yaml

INPUT = Path(__file__).parent / "llm_input"
HEADER = "# where each exit leads (the destination's description in the game)"


def destinations(locations, room):
    """{destination: its long description on one line}, for the room's goto exits, in travel order."""
    out = {}
    for t in locations[room].get("travel", []):
        if t["action"][0] == "goto":
            to = t["action"][1]
            text = (locations.get(to, {}).get("description") or {}).get("long") or ""
            out.setdefault(to, " ".join(text.split()))
    return out


def add(path, dests):
    text = path.read_text()
    text = text.split("\n" + HEADER)[0].rstrip("\n")  # an earlier section is replaced
    lines = [HEADER] + [f"# {to}: {desc}" if desc else f"# {to}: (no description in the game)" for to, desc in dests.items()]
    path.write_text(text + "\n\n" + "\n".join(lines) + "\n")


if __name__ == "__main__":
    source, rooms = sys.argv[1], sys.argv[2:]
    locations = yaml.safe_load(Path(source).read_text())["locations"]
    locations = dict(e if isinstance(e, tuple) else next(iter(e.items())) for e in locations)  # a list of (id, entry)
    for room in rooms:
        path = INPUT / f"{room}.yaml"
        add(path, destinations(locations, room))
        print(path.read_text().split(HEADER)[1].strip())
