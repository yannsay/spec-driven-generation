"""Run the pipeline on some rooms: the writer writes their specs, the loop gates, draws, judges and corrects them, and
the page shows the result.

Usage: uv run run.py <run> --rooms LOC_BUILDING,LOC_GRATE [--rounds 2]

<run> names everything this run writes, all under outputs/: the specs as written in outputs/specs/written/<run>/, the run's specs
and pictures in outputs/specs/checked/<run>*/ and outputs/pictures/<run>*/, and the page outputs/pipeline_<run>.html.
The rooms are the files of pipeline/01 - yaml/llm_input/. Run init.py once before the first run.
"""
import subprocess
import sys
from pathlib import Path

PIPE = Path(__file__).resolve().parent / "pipeline"


def step(title, *cmd):
    """Run one stage's script with this Python; stop the run if it fails."""
    print(f"\n== {title}", flush=True)
    if subprocess.run([sys.executable, *cmd], cwd=PIPE).returncode:
        sys.exit(f"step failed: {cmd[0]}")


if __name__ == "__main__":
    args = sys.argv[1:]

    def opt(name, default):
        """The value after `name`, both taken out of args."""
        if name not in args:
            return default
        i = args.index(name)
        value = args[i + 1]
        del args[i:i + 2]
        return value

    rooms, rounds = opt("--rooms", ""), opt("--rounds", "2")
    if len(args) != 1 or not rooms:
        sys.exit(__doc__)
    run = args[0]
    step("1/3 writing the specs", "02 - spec/make_spec.py", "--out", run, *rooms.split(","))
    step("2/3 the loop: verify the specs, draw, judge, route, correct",
         "06 - loop/image_loop.py", run, "--specs", run, "--rooms", rooms, "--rounds", rounds)
    step("3/3 building the page", "common/pipeline_viewer.py", run)
