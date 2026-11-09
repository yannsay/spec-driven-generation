"""Where the pipeline's inputs and outputs are. Every stage folder puts the others on sys.path with `stages()`."""
import sys
from pathlib import Path

PIPE = Path(__file__).resolve().parents[1]           # <repo>/pipeline
INPUT = PIPE / "01 - yaml" / "llm_input"             # one input per room (stage 1)
PROMPTS = PIPE / "02 - spec" / "prompts"
SPEC_RULES = PROMPTS / "rules.md"  # the shared rules, read by the router and corrector too
STYLES = PIPE / "03 - draw" / "styles"
RUNS = PIPE.parent / "outputs"                       # everything a run writes, at the repo root
WRITTEN = RUNS / "specs" / "written"                 # written/<run>/<ROOM>.json: the specs as the writer wrote them
SPECS = RUNS / "specs" / "checked"                   # checked/<run>/<ROOM>.json, the spec the loop draws from, one folder per round
PICTURES = RUNS / "pictures"                         # pictures/<run>/<ROOM>/<file>, same names as SPECS
PILOT = ["LOC_START", "LOC_BUILDING", "LOC_GRATE", "LOC_BELOWGRATE", "LOC_COBBLE",
         "LOC_DEBRIS", "LOC_AWKWARD", "LOC_BIRDCHAMBER", "LOC_PITTOP", "LOC_MISTHALL"]
STAGES = ["02 - spec", "03 - draw", "04 - judge", "05 - route", "06 - loop"]


def stages():
    """Put every stage folder on sys.path, so a script imports another stage's module by its name."""
    for s in STAGES:
        if str(PIPE / s) not in sys.path:
            sys.path.append(str(PIPE / s))
