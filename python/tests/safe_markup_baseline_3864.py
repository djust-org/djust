"""The findings of ``main``'s safe-markup guard, as an oracle for the two passes (#3864).

``find_unsafe_markup`` runs the guard twice over a file: pass A is the guard as it was (no call is ever
bound), pass B adds the judgement of a sink handed a call. ``test_safe_markup_guard_3864`` checks that
pass A alone finds EXACTLY what ``main``'s guard found, and the union at least that, over three
corpora: the call matrix (``safe_markup_call_fuzz_3864.matrix()``), the stress programs
(``safe_markup_stress_3864.program``) and the real tree. Nothing of ``main`` is vendored: the
findings are recorded in ``safe_markup_main_baseline_3864.json`` by running this module with the
guard of ``main``::

    python python/tests/safe_markup_baseline_3864.py <main's scripts/lib/safe_markup_taint.py> <sha>

A tree file is compared only while its contents are what they were when the baseline was recorded
(``crc``): the tree moves, and a file that changed is not ``main``'s file any more.
"""

from __future__ import annotations

import json
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASELINE = Path(__file__).with_name("safe_markup_main_baseline_3864.json")
STRESS_PROGRAMS = 1200
TREE_DIRS = ("python/djust", "scripts", "examples")


def key(f) -> list:
    return [f.lineno, f.function, f.sink]


def matrix_key(case) -> str:
    source, helpers, placement = case
    return "|".join([source, ",".join(helpers), placement])


def tree_files(root: Path = ROOT) -> list[tuple[str, str]]:
    """``(path, source)`` of every Python file of the tree the guard reads that compiles."""
    out = []
    for top in TREE_DIRS:
        for path in sorted((root / top).rglob("*.py")):
            text = path.read_text()
            try:
                compile(text, str(path), "exec")
            except SyntaxError:
                continue
            out.append((str(path.relative_to(root)), text))
    return out


def crc(text: str) -> int:
    return zlib.crc32(text.encode())


def record(find, dynamic_class_names) -> dict:
    """What ``find(source, **kw)`` (a guard's ``find_unsafe_markup``) reports over the three corpora."""
    import safe_markup_call_fuzz_3864 as fuzz
    import safe_markup_stress_3864 as stress

    matrix = {}
    for case in fuzz.matrix():
        found = sorted(key(f) for f in find(fuzz.build(*case)))
        if found:
            matrix[matrix_key(case)] = found
    programs = {}
    for seed in range(STRESS_PROGRAMS):
        found = sorted(key(f) for f in find(stress.program(seed)))
        if found:
            programs[str(seed)] = found
    files = tree_files()
    dynamic = dynamic_class_names(text for _, text in files)
    tree = {}
    for path, text in files:
        found = sorted(key(f) for f in find(text, dynamic_bases=dynamic))
        tree[path] = {"crc": crc(text), "findings": found}
    return {"matrix": matrix, "stress": programs, "tree": tree}


if __name__ == "__main__":  # pragma: no cover - records the baseline from another guard
    import importlib.util
    import sys

    sys.path[:0] = [str(ROOT), str(ROOT / "python/tests"), str(ROOT / "python")]
    import django
    from django.conf import settings

    settings.configure()
    django.setup()
    spec = importlib.util.spec_from_file_location("main_taint", sys.argv[1])
    guard = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["main_taint"] = guard
    spec.loader.exec_module(guard)  # type: ignore[union-attr]
    data = record(guard.find_unsafe_markup, guard.dynamic_class_names)
    data = {
        "_comment": "Findings of main's safe-markup guard (see safe_markup_baseline_3864.py).",
        "main_commit": sys.argv[2] if len(sys.argv) > 2 else "",
        "stress_programs": STRESS_PROGRAMS,
        **data,
    }
    BASELINE.write_text(json.dumps(data, indent=0, sort_keys=True) + "\n")
    print(len(data["matrix"]), "matrix", len(data["stress"]), "stress", len(data["tree"]), "files")
