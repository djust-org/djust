#!/usr/bin/env python3
"""Fail if a built wheel or sdist ships files nobody loads.

``pyproject.toml``'s ``[tool.maturin] exclude`` keeps djust's own tests
(``djust/tests/``), the client's source modules (``djust/static/djust/src/``,
concatenated into ``client.js`` by ``scripts/build-client.sh``) and editor
backups out of both artifacts. A glob that matches nothing fails silently, so
CI builds the artifacts and lists them here. The bundles themselves must still
be present, or the excludes went too far.

Usage::

    python scripts/check-package-contents.py dist/djust-*.whl dist/djust-*.tar.gz
"""

from __future__ import annotations

import re
import sys
import tarfile
import zipfile

FORBIDDEN = [
    (re.compile(r"(^|/)djust/tests/"), "djust's own test suite"),
    (re.compile(r"(^|/)djust/static/djust/src/"), "client source modules (client.js ships them)"),
    (re.compile(r"\.backup$"), "an editor/dev backup (#3295)"),
]
REQUIRED = ["djust/static/djust/client.js", "djust/static/djust/client.min.js"]


def names(path: str) -> list[str]:
    if path.endswith(".whl"):
        with zipfile.ZipFile(path) as zf:
            return zf.namelist()
    with tarfile.open(path) as tf:
        return tf.getnames()


def check(path: str) -> list[str]:
    listing = names(path)
    errors = [
        "%s: ships %s (%s)" % (path, name, why)
        for name in listing
        for pattern, why in FORBIDDEN
        if pattern.search(name)
    ]
    for required in REQUIRED:
        if not any(name == required or name.endswith("/" + required) for name in listing):
            errors.append("%s: missing %s" % (path, required))
    return errors


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    errors = [error for path in argv for error in check(path)]
    for error in errors[:50]:
        print(error, file=sys.stderr)
    if len(errors) > 50:
        print("... and %d more" % (len(errors) - 50), file=sys.stderr)
    if errors:
        return 1
    for path in argv:
        print("%s: %d files, none excluded by pyproject shipped" % (path, len(names(path))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
