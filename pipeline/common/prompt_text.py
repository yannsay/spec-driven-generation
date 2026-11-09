"""A prompt file's text, with each {{<file>}} line replaced by that file (same folder): the shared rules file
02 - spec/prompts/rules.md is one text read by the spec writer, the spec judge, the repair, the router and the
corrector: one copy of each rule, no drift between prompts."""
import re
from pathlib import Path


def prompt_text(path):
    path = Path(path)
    return re.sub(r"^\{\{([^}]+)\}\}$", lambda m: (path.parent / m.group(1)).read_text().strip(),
                  path.read_text(), flags=re.M)
