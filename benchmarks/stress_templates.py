#!/usr/bin/env python
"""Template stress test: Django's engine vs djust's Rust engine.

Broader and harsher than `benchmark.py` — 14 shapes chosen to stress
different parts of a template engine (variable resolution, attribute walks,
filter chains, escaping, branch density, nesting, tag dispatch, includes,
inheritance) at two sizes, plus a separate parse/compile measurement.

Three rules this harness follows, each because getting it wrong produces a
confident wrong answer:

1. **Byte-identical output is a precondition, not a result.** Two engines
   that render different bytes are doing different work, and timing them
   against each other is meaningless. Every shape is rendered once through
   both engines and compared BEFORE it is timed; a mismatch is reported and
   the shape is excluded from the timings.

2. **Median, never mean.** A single GC pause or scheduler preemption drags a
   mean past any threshold while the median is unmoved. `min` is reported
   too — it is the cleanest estimate of achievable cost, since noise can
   only ever add time.

3. **A debug build inverts the result.** An unoptimized `.so` measures
   Django as faster. The guard below refuses to run rather than mislead.

Usage:
    python benchmarks/stress_templates.py            # both sizes
    python benchmarks/stress_templates.py --quick    # small size only
"""

from __future__ import annotations

import html
import os
import re
import statistics
import sys
import tempfile
import time
from pathlib import Path

import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        DEBUG=False,
        USE_TZ=True,
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [],
                "APP_DIRS": False,
                "OPTIONS": {},
            }
        ],
    )
    django.setup()

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402

from djust import _rust  # noqa: E402


# --------------------------------------------------------------------------- #
# guards
# --------------------------------------------------------------------------- #


def _refuse_a_debug_build() -> str:
    so = _rust.__file__
    size = os.path.getsize(so)
    if size > 20_000_000:
        sys.exit(
            f"REFUSING TO RUN: {os.path.basename(so)} is {size / 1e6:.0f} MB — a DEBUG build.\n"
            f"A debug build is unoptimized and REVERSES this comparison.\n"
            f"Run `make build` first."
        )
    return f"{size / 1e6:.1f} MB (release)"


# --------------------------------------------------------------------------- #
# fixtures — plain objects, because attribute access is what real templates walk
# --------------------------------------------------------------------------- #


class Author:
    __slots__ = ("name", "email")

    def __init__(self, i: int) -> None:
        self.name = f"Author {i}"
        self.email = f"a{i}@example.com"


class Row:
    __slots__ = ("id", "name", "value", "price", "active", "tags", "author", "body", "group")

    def __init__(self, i: int) -> None:
        self.id = i
        self.name = f"Row {i}"
        self.value = i * 3
        self.price = i * 1.5
        self.active = (i % 3) != 0
        self.tags = [f"t{i % 5}", f"t{i % 7}"]
        self.author = Author(i % 20)
        # Unsafe content: exercises the escaping path, which is a real cost.
        self.body = f"<b>item {i}</b> & 'quoted' \"text\" <script>x</script>"
        self.group = f"g{i % 8}"


def make_context(n: int) -> dict:
    rows = [Row(i) for i in range(n)]
    return {
        "rows": rows,
        "matrix": [[c for c in range(8)] for _ in range(max(1, n // 8))],
        "mapping": {f"k{i}": f"v{i}" for i in range(50)},
        "title": "Stress <Test> & Co",
        "n": n,
    }


# --------------------------------------------------------------------------- #
# shapes
# --------------------------------------------------------------------------- #

SHAPES: dict[str, str] = {
    "01 static markup (floor)": "<ul>{% for r in rows %}<li>item</li>{% endfor %}</ul>",
    "02 simple list (2 vars)": (
        "<ul>{% for r in rows %}<li>{{ r.name }}: {{ r.value }}</li>{% endfor %}</ul>"
    ),
    "03 wide table (10 cols)": (
        "<table>{% for r in rows %}<tr>"
        "<td>{{ r.id }}</td><td>{{ r.name }}</td><td>{{ r.value }}</td>"
        "<td>{{ r.price }}</td><td>{{ r.active }}</td><td>{{ r.group }}</td>"
        "<td>{{ r.author.name }}</td><td>{{ r.author.email }}</td>"
        "<td>{{ r.tags.0 }}</td><td>{{ r.tags.1 }}</td>"
        "</tr>{% endfor %}</table>"
    ),
    "04 deep attribute walk": (
        "{% for r in rows %}{{ r.author.name }}{{ r.author.email }}{% endfor %}"
    ),
    "05 filter chain (5 deep)": (
        "{% for r in rows %}{{ r.name|lower|upper|truncatechars:20|ljust:24|slice:':10' }}"
        "{% endfor %}"
    ),
    "06 numeric filters": (
        "{% for r in rows %}{{ r.price|floatformat:2 }}{{ r.value|add:7 }}"
        "{{ r.value|stringformat:'05d' }}{% endfor %}"
    ),
    "07 escaping-heavy": "{% for r in rows %}<p>{{ r.body }}</p>{% endfor %}",
    "08 escaping bypassed (|safe)": "{% for r in rows %}<p>{{ r.body|safe }}</p>{% endfor %}",
    "09 branch-dense (if/elif)": (
        "{% for r in rows %}{% if r.value > 1000 %}H{% elif r.value > 500 %}M"
        "{% elif r.value > 100 %}L{% elif r.active %}A{% else %}Z{% endif %}{% endfor %}"
    ),
    "10 nested loops": (
        "{% for row in matrix %}<tr>{% for c in row %}<td>{{ c }}</td>{% endfor %}</tr>{% endfor %}"
    ),
    "11 tag mix (with/cycle/firstof)": (
        "{% for r in rows %}{% with v=r.value %}<li class='{% cycle 'a' 'b' 'c' %}'>"
        "{% firstof r.name v 'none' %}:{{ v }}</li>{% endwith %}{% endfor %}"
    ),
    "12 regroup": (
        "{% regroup rows by group as gs %}{% for g in gs %}<h3>{{ g.grouper }}</h3>"
        "{% for r in g.list %}{{ r.id }}{% endfor %}{% endfor %}"
    ),
    "13 dict lookups": (
        "{% for r in rows %}{{ mapping.k1 }}{{ mapping.k2 }}{{ mapping.k3 }}{% endfor %}"
    ),
    "14 realistic page": (
        "<h1>{{ title }}</h1><div class='wrap'>"
        "{% for r in rows %}<article class='{% cycle 'odd' 'even' %}'>"
        "<h2>{{ r.name|title|truncatechars:30 }}</h2>"
        "<p class='meta'>{{ r.author.name }} &middot; {{ r.price|floatformat:2 }}</p>"
        "{% if r.active %}<span class='badge'>active</span>{% endif %}"
        "<div class='body'>{{ r.body }}</div>"
        "</article>{% endfor %}</div>"
    ),
}


# --------------------------------------------------------------------------- #
# equality gate
# --------------------------------------------------------------------------- #


DJ_IF_MARKER = re.compile(r"<!--/?dj-if(?: [^>]*)?-->")


def strip_markers(html_out: str) -> str:
    """Remove dj-if markers.

    The stateful path emits `<!--dj-if id="..."-->` around conditionals so the
    client can patch them — a deliberate LiveView feature, not a rendering
    difference. Comparing raw output would report it as a mismatch and exclude
    the shape; comparing stripped output compares the rendering. The markers
    are still WRITTEN, so their cost stays in the timing.
    """
    return DJ_IF_MARKER.sub("", html_out)


def render_django(source: str, ctx: dict) -> str:
    return DjangoTemplate(source).render(DjangoContext(ctx))


def render_djust(source: str, ctx: dict) -> str:
    return _rust.render_template(source, ctx)


def check_parity(ctx: dict) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """Render every shape through both engines; keep only the ones that agree."""
    ok: dict[str, str] = {}
    mismatched: list[tuple[str, str]] = []
    for name, src in SHAPES.items():
        try:
            d = render_django(src, ctx)
        except Exception as exc:  # noqa: BLE001 — a shape Django refuses is a harness bug
            mismatched.append((name, f"django raised {type(exc).__name__}: {exc}"))
            continue
        try:
            j = render_djust(src, ctx)
        except Exception as exc:  # noqa: BLE001
            mismatched.append((name, f"djust raised {type(exc).__name__}: {exc}"))
            continue
        if d != j:
            at = next((k for k in range(min(len(d), len(j))) if d[k] != j[k]), min(len(d), len(j)))
            mismatched.append(
                (
                    name,
                    f"output differs at char {at} "
                    f"(django {len(d)}B, djust {len(j)}B): "
                    f"django={d[at : at + 40]!r} djust={j[at : at + 40]!r}",
                )
            )
            continue
        ok[name] = src
    return ok, mismatched


# --------------------------------------------------------------------------- #
# timing
# --------------------------------------------------------------------------- #


def bench(fn, reps: int, batches: int = 7) -> tuple[float, float]:
    """(median, min) milliseconds per call."""
    fn()  # warm caches on both sides
    times = []
    for _ in range(batches):
        start = time.perf_counter()
        for _ in range(reps):
            fn()
        times.append((time.perf_counter() - start) / reps)
    return statistics.median(times) * 1e3, min(times) * 1e3


def bench_render(size: int, reps: int) -> None:
    """Three contenders, because two of them answer different questions.

    * django       — `Template.render(Context)`; resolves variables lazily, so
                     an unused context costs nothing.
    * djust call   — `render_template(source, dict)`; marshals the WHOLE dict
                     across the PyO3 boundary on every call, used or not.
    * djust view   — `RustLiveView.set_state(...)` once, then `render()`; the
                     path a real LiveView takes. State is mutated between
                     renders so no cache can flatter the number.
    """
    ctx = make_context(size)
    shapes, mismatched = check_parity(ctx)

    print(f"\n{'=' * 100}")
    print(f"RENDER — {size:,} rows   ({len(shapes)}/{len(SHAPES)} shapes byte-identical)")
    print("=" * 100)
    if mismatched:
        print("  EXCLUDED (engines disagree — timing them would compare different work):")
        for name, why in mismatched:
            print(f"    {name}: {why}")
        print()
    print(
        f"  {'shape':<34}{'django':>10}{'djust call':>12}{'djust view':>12}"
        f"{'call/dj':>10}{'view/dj':>10}"
    )
    print(f"  {'-' * 34}{'-' * 10}{'-' * 12}{'-' * 12}{'-' * 10}{'-' * 10}")

    call_sp, view_sp = [], []
    for name, src in shapes.items():
        dt = DjangoTemplate(src)
        d_med, _ = bench(lambda t=dt: t.render(DjangoContext(ctx)), reps)
        c_med, _ = bench(lambda s=src: _rust.render_template(s, ctx), reps)

        view = _rust.RustLiveView(src)
        for k, v in ctx.items():
            view.set_state(k, v)
        if strip_markers(view.render()) != dt.render(DjangoContext(ctx)):
            print(f"  {name:<34}{'stateful output differs — excluded':>54}")
            continue

        tick = {"i": 0}

        def stateful(v=view, t=tick):
            t["i"] += 1
            v.set_state("n", t["i"])  # mutate, so a cached render cannot be measured
            return v.render()

        v_med, _ = bench(stateful, reps)

        cs = d_med / c_med if c_med else float("inf")
        vs = d_med / v_med if v_med else float("inf")
        call_sp.append(cs)
        view_sp.append(vs)
        print(f"  {name:<34}{d_med:>10.3f}{c_med:>12.3f}{v_med:>12.3f}{cs:>9.2f}x{vs:>9.2f}x")

    if view_sp:
        print(f"  {'-' * 88}")
        print(
            f"  {'median across shapes':<34}{'':>10}{'':>12}{'':>12}"
            f"{statistics.median(call_sp):>9.2f}x{statistics.median(view_sp):>9.2f}x"
        )
        print(
            f"  {'range':<34}{'':>10}{'':>12}{'':>12}"
            f"{min(call_sp):>6.2f}-{max(call_sp):.2f}x{min(view_sp):>6.2f}-{max(view_sp):.2f}x"
        )


def bench_unused_state(reps: int = 20) -> None:
    """Why the floor shape looks slow: cost tracks TOTAL state, not what is read.

    Every per-shape number above is partly this. Django resolves lazily, so an
    unused context is free; djust converts the whole context, so unrelated
    state is charged to every render. Tracked at #2732.
    """
    src = "{% for row in matrix %}{% for c in row %}<td>{{ c }}</td>{% endfor %}{% endfor %}"
    matrix = [list(range(8)) for _ in range(250)]

    print(f"\n{'=' * 100}")
    print("UNUSED STATE — the template below reads ONLY `matrix` (fixed 250x8).")
    print("`unused` is never referenced. Django is flat; djust is linear. (#2732)")
    print("=" * 100)
    print(f"  {'unused rows in state':<24}{'django ms':>11}{'djust ms':>11}")
    print(f"  {'-' * 24}{'-' * 11}{'-' * 11}")
    for n in (0, 100, 500, 2_000):
        ctx = {"matrix": matrix, "unused": [Row(i) for i in range(n)]}
        dt = DjangoTemplate(src)
        d_med, _ = bench(lambda t=dt: t.render(DjangoContext(ctx)), reps)
        view = _rust.RustLiveView(src)
        for k, v in ctx.items():
            view.set_state(k, v)
        j_med, _ = bench(lambda vv=view: vv.render(), reps)
        print(f"  {n:<24,}{d_med:>11.2f}{j_med:>11.2f}")


def bench_parse(reps: int = 200) -> None:
    """Parse/compile cost. Both engines cache, so defeat the cache with unique sources."""
    print(f"\n{'=' * 86}")
    print("PARSE / COMPILE — cold, cache defeated with a unique comment per iteration")
    print("=" * 86)
    print(f"  {'shape':<34}{'django ms':>11}{'djust ms':>11}{'speedup':>10}")
    print(f"  {'-' * 34}{'-' * 11}{'-' * 11}{'-' * 10}")

    for name in ("02 simple list (2 vars)", "03 wide table (10 cols)", "14 realistic page"):
        src = SHAPES[name]
        counter = {"d": 0, "j": 0}

        def d_parse(s=src, c=counter):
            c["d"] += 1
            DjangoTemplate(f"{{# {c['d']} #}}{s}")

        def j_parse(s=src, c=counter):
            c["j"] += 1
            _rust.compile_template(f"{{# {c['j']} #}}{s}", None)

        d_med, _ = bench(d_parse, reps)
        j_med, _ = bench(j_parse, reps)
        sp = d_med / j_med if j_med else float("inf")
        print(f"  {name:<34}{d_med:>11.4f}{j_med:>11.4f}{sp:>9.2f}x")


def bench_include_extends(size: int, reps: int) -> None:
    """{% include %} and {% extends %} need a loader, so they get their own pass."""
    ctx = make_context(size)
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "base.html").write_text(
            "<html><body>{% block content %}base{% endblock %}</body></html>", encoding="utf-8"
        )
        (d / "card.html").write_text("<li>{{ r.name }}: {{ r.value }}</li>", encoding="utf-8")

        cases = {
            "15 include per row": (
                "<ul>{% for r in rows %}{% include 'card.html' %}{% endfor %}</ul>"
            ),
            "16 extends + block": (
                "{% extends 'base.html' %}{% block content %}"
                "{% for r in rows %}{{ r.name }}{% endfor %}{% endblock %}"
            ),
        }

        from django.template import Engine

        eng = Engine(dirs=[str(d)], app_dirs=False)

        print(f"\n{'=' * 86}")
        print(f"INCLUDE / EXTENDS — {size:,} rows (needs a loader, so measured separately)")
        print("=" * 86)
        print(f"  {'shape':<34}{'django ms':>11}{'djust ms':>11}{'speedup':>10}")
        print(f"  {'-' * 34}{'-' * 11}{'-' * 11}{'-' * 10}")

        for name, src in cases.items():
            try:
                dj_out = eng.from_string(src).render(DjangoContext(ctx))
                ju_out = _rust.render_template_with_dirs(src, ctx, [str(d)])
            except Exception as exc:  # noqa: BLE001
                print(f"  {name:<34}  SKIPPED — {type(exc).__name__}: {str(exc)[:40]}")
                continue
            if dj_out != ju_out:
                print(f"  {name:<34}  EXCLUDED — output differs ({len(dj_out)}B vs {len(ju_out)}B)")
                continue
            dt = eng.from_string(src)
            d_med, _ = bench(lambda t=dt: t.render(DjangoContext(ctx)), reps)
            j_med, _ = bench(
                lambda s=src, p=str(d): _rust.render_template_with_dirs(s, ctx, [p]), reps
            )
            sp = d_med / j_med if j_med else float("inf")
            print(f"  {name:<34}{d_med:>11.3f}{j_med:>11.3f}{sp:>9.2f}x")


def main() -> None:
    quick = "--quick" in sys.argv
    build = _refuse_a_debug_build()

    print("=" * 86)
    print("DJANGO TEMPLATE STRESS TEST — Django engine vs djust Rust engine")
    print("=" * 86)
    print(f"  extension : {os.path.basename(_rust.__file__)}  {build}")
    print(f"  python    : {sys.version.split()[0]}")
    print(f"  django    : {django.get_version()}")
    print("  timing    : median of 7 batches. Never the mean —")
    print("              one GC pause moves a mean and leaves a median alone.")
    print("  parity    : every shape is rendered through BOTH engines and compared")
    print("              byte-for-byte before it is timed. Mismatches are excluded,")
    print("              because timing engines that produce different bytes")
    print("              compares different work.")
    print(f"  escaping  : on (Django default). Sample: {html.escape('<b>&</b>')!r}")

    sizes = ((100, 400),) if quick else ((100, 400), (2_000, 25))
    for size, reps in sizes:
        bench_render(size, reps)

    bench_parse()
    bench_include_extends(100, 300)
    bench_unused_state()

    print(f"\n{'=' * 86}")
    print("Speedup is django_ms / djust_ms — higher favours djust, 1.0x is parity.")
    print("A shape with nothing to accelerate (static markup) SHOULD sit near 1.0x;")
    print("if it does not, the harness is measuring something other than rendering.")
    print("=" * 86)


if __name__ == "__main__":
    main()
