#!/usr/bin/env python3
"""Print which covered documentation examples run (ADR-037 D2).

python scripts/doc-examples-report.py          # table
python scripts/doc-examples-report.py --json   # machine-readable
"""

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from djust.tests import _doc_examples as H  # noqa: E402


def main(argv):
    data = H.report(H.COVERED)
    if "--json" in argv:
        sys.stdout.write(json.dumps(data, sort_keys=True) + "\n")
        return 0
    sys.stdout.write("%-50s %8s %8s %8s\n" % ("covered file", "executed", "skipped", "unmarked"))
    for path, c in sorted(data["covered"].items()):
        sys.stdout.write("%-50s %8d %8d %8d\n" % (path, c["executed"], c["skipped"], c["unmarked"]))
    sys.stdout.write(
        "docs/website: %d Python blocks, %d not executed (parse/import-checked only)\n"
        % (data["docs_website_python_blocks"], data["docs_website_unexecuted"])
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
