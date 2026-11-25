#!/usr/bin/env python3
"""Benchmark: the ``djust.components.ui`` simple components' render cost -- issue #4002.

#4002 removed the Rust ``djust_components`` crate (``RustAlert`` ... ``RustTooltip``) that
fifteen ``ui/*_simple.py`` classes rendered through, keeping one renderer per class: a Python
``_render_custom``. This script measures, per class, ``render()`` on a built instance and a
construct-and-render, the shape a view uses. The pre-#4002 numbers (crate path and template
path) are recorded in ``docs/components/RUST_COMPONENTS.md``.

Run from the checkout::

    PYTHONPATH=python python scripts/bench_ui_simple_components.py [iterations]

Exit code is always 0 -- this is a measurement tool, not a gate.
"""

from __future__ import annotations

import importlib
import os
import sys
import timeit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "examples", "demo_project")]
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "demo_project.settings")

#: (module, class, constructor kwargs): the same shapes the #4002 decision was measured with.
CASES = [
    ("alert_simple", "Alert", {"text": "Saved"}),
    ("avatar_simple", "Avatar", {"src": "/a.png", "alt": "Ann"}),
    ("badge_simple", "Badge", {"text": "New"}),
    ("button_simple", "Button", {"text": "Go"}),
    ("card_simple", "Card", {"body": "Body"}),
    ("divider_simple", "Divider", {}),
    ("icon_simple", "Icon", {"name": "star"}),
    ("modal_simple", "Modal", {"body": "Body", "id": "m1"}),
    ("progress_simple", "Progress", {"value": 40}),
    ("range_simple", "Range", {"name": "vol"}),
    ("spinner_simple", "Spinner", {}),
    ("switch_simple", "Switch", {"name": "n", "label": "L"}),
    ("textarea_simple", "TextArea", {"name": "bio"}),
    ("toast_simple", "Toast", {"title": "T", "message": "M"}),
    ("tooltip_simple", "Tooltip", {"content": "Hover", "text": "Tip"}),
]


def _best_us(fn, number: int) -> float:
    return min(timeit.repeat(fn, number=number, repeat=5)) / number * 1e6


def main() -> int:
    import django

    django.setup()
    number = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
    print("%-9s %10s %18s" % ("class", "render us", "build+render us"))
    for module_name, class_name, kwargs in CASES:
        cls = getattr(importlib.import_module("djust.components.ui." + module_name), class_name)
        instance = cls(**kwargs)
        render = _best_us(instance.render, number)
        build = _best_us(lambda: cls(**kwargs).render(), number)
        sys.stdout.write("%-9s %10.2f %18.2f\n" % (class_name, render, build))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
