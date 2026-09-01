"""Rewrite tests/golden/ after an intended change in the output.

    python -m tests.regen_golden

Look at the diff before committing it.
"""
import os

from sqltriage.render import FORMATS
from tests.golden_support import GOLDEN, incident_report

EXTENSIONS = {"text": "txt", "json": "json", "markdown": "md", "oneline": "line"}


def main():
    os.makedirs(GOLDEN, exist_ok=True)
    report = incident_report()
    for name, render in FORMATS.items():
        path = os.path.join(GOLDEN, "incident1." + EXTENSIONS[name])
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(render(report))
        print("wrote", path)


if __name__ == "__main__":
    main()
