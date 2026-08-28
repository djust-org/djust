#!/usr/bin/env python3
"""Reproduce the README's performance table.

    python benchmarks/benchmark.py

The README used to cite this script and it did not exist, so its figures could
not be checked by anyone — including us. They were also wrong: measured against
this harness, the published 16.7x and 37.5x are 1.0x-11.3x depending entirely on
what the template does.

WHAT THIS MEASURES
------------------
Full render throughput of the same template on both engines, template parsed
once on each side. That is the fair comparison and it is narrow on purpose:

  * It is NOT djust's whole value proposition. djust re-renders and then sends a
    VDOM patch, where Django can only re-render and ship the whole page. The
    wire-size difference is not captured here at all.
  * It says nothing about the WebSocket round trip, diffing, or client apply.

Numbers are per render, median of 7 timed batches. Speedup is Django/djust, so
higher is better for djust and 1.0x means "no difference".

TWO WAYS TO GET A WRONG ANSWER, BOTH GUARDED BELOW
--------------------------------------------------
1. Benchmarking a DEBUG build. `make dev-build` produces an unoptimized
   extension; `make build` produces the release one. The difference is ~7.6x and
   it INVERTS the result -- a debug build measures Django as ~2x faster than
   djust. Since `make dev-build` is what most test and mutation workflows run,
   a debug .so is usually what is installed. This script refuses to run on one.
2. Leaving `DEBUG=True` in Django settings, which inflates BOTH engines and
   flatters neither consistently. Forced off below.
"""

from __future__ import annotations

import os
import statistics
import sys
import time

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "demo_project.settings")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (REPO_ROOT, os.path.join(REPO_ROOT, "examples", "demo_project")):
    if path not in sys.path:
        sys.path.insert(0, path)

import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402

settings.DEBUG = False  # see the module docstring

from django.template import Context  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402

from djust import _rust  # noqa: E402


def _refuse_a_debug_build() -> str:
    """A debug extension inverts the result. Refuse rather than mislead."""
    so = _rust.__file__
    size = os.path.getsize(so)
    # Release is ~6 MB; a debug build carries symbols and runs ~30 MB+.
    if size > 20_000_000:
        sys.exit(
            f"REFUSING TO RUN: {os.path.basename(so)} is {size / 1e6:.0f} MB, "
            f"which is a DEBUG build.\n"
            f"A debug build is unoptimized and reverses this comparison "
            f"(it measures Django as faster).\n"
            f"Run `make build` first, then re-run this script."
        )
    return f"{size / 1e6:.1f} MB (release)"


class Row:
    """A plain object, because attribute access is what real templates walk."""

    __slots__ = ("name", "value")

    def __init__(self, name: str, value: int) -> None:
        self.name = name
        self.value = value


SHAPES: dict[str, str] = {
    # The honest floor: nothing to accelerate, so nothing is accelerated.
    "Static markup (no variables)": "<ul>{% for r in rows %}<li>item</li>{% endfor %}</ul>",
    "Simple list (2 vars/row)": (
        "<ul>{% for r in rows %}<li>{{ r.name }}: {{ r.value }}</li>{% endfor %}</ul>"
    ),
    # Closest to a real page: filters, a tag, and a conditional per row.
    "Filtered list (typical page)": (
        "<ul>{% for r in rows %}<li class='{% cycle 'a' 'b' %}'>"
        "{{ r.name|upper|truncatechars:12 }}: {{ r.value|floatformat:2 }}"
        "{% if r.value %} <em>{{ r.value|add:1 }}</em>{% endif %}</li>{% endfor %}</ul>"
    ),
}

SIZES: tuple[tuple[int, int], ...] = ((100, 300), (10_000, 10))


def bench(fn, repetitions: int) -> float:
    """Milliseconds per call, median of 7 batches."""
    fn()  # warm
    batches = []
    for _ in range(7):
        start = time.perf_counter()
        for _ in range(repetitions):
            fn()
        batches.append((time.perf_counter() - start) / repetitions)
    return statistics.median(batches) * 1e3


def main() -> None:
    build = _refuse_a_debug_build()
    print(f"djust extension : {build}")
    print(f"Django          : {django.get_version()}")
    print(f"Python          : {sys.version.split()[0]}")
    print(f"settings.DEBUG  : {settings.DEBUG}")
    print()
    print(f"{'Template shape':32} {'Rows':>6} {'Django':>10} {'djust':>10} {'Speedup':>9}")
    print("-" * 72)

    for label, source in SHAPES.items():
        for rows_n, repetitions in SIZES:
            rows = [Row(f"name{i}", i) for i in range(rows_n)]
            context = {"rows": rows}

            # Both engines parse once, then render repeatedly.
            django_template = DjangoTemplate(source)
            django_context = Context(context)
            django_ms = bench(lambda: django_template.render(django_context), repetitions)

            view = _rust.RustLiveView(source)
            view.update_state(context)
            view.render()  # establish the baseline render
            djust_ms = bench(view.render, repetitions)

            print(
                f"{label:32} {rows_n:6} {django_ms:9.2f}m {djust_ms:9.2f}m "
                f"{django_ms / djust_ms:8.1f}x"
            )

    print()
    print("Speedup is Django/djust. 1.0x means no difference — expected for")
    print("static markup, where there is nothing to accelerate. The advantage")
    print("grows with variable and filter density, which is what real pages do.")


if __name__ == "__main__":
    main()
