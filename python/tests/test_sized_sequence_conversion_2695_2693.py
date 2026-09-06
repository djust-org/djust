"""A sized sequence is no longer materialised at conversion (#2695), and
``dictsort`` consumes a live handle (#2693).

#2678 stopped the conversion from enumerating an object whose stated
``__len__`` its ``__getitem__`` could never reach. It scoped the decline to
that ONE shape — an object with no ``__iter__`` — because its first version
had capped on the stated length alone and regressed every honest collection
past the cap. So ``range(10**9)``, which terminates, kept being materialised
into a billion ``Value``s the moment it entered a context, and
``{{ v.0 }}`` / ``{{ v|length }}`` / ``{{ v }}`` / ``{% for %}`` all failed to
return — measured identical on ``main`` and on #2691's head.

Django never enumerates at binding time. ``{{ v.0 }}`` is one
``__getitem__``, ``{{ v|length }}`` is ``len(v)``, ``{{ v }}`` is ``str(v)``,
``|first`` is ``value[0]``, ``|last`` is ``value[-1]``, ``|slice`` is
``value[slice(*bits)]`` and ``in`` is Python's own ``in``. So the fix is not a
cap, it is the same lazy carrier ADR-027 already has: the conversion declines
to enumerate past ``OPAQUE_ITEM_CAP`` on EITHER axis, and each sink reads what
it needs off the live handle.

The bound then moves to where it belongs. The conversion asks "is the stated
length too large to spend HERE"; the SINKS ask "can this walk end at all"
(``Encoded::live_walk_terminates``) — which is why ``list(range(100_001))``
still renders every item at ``{% for %}`` while #2678's liar still raises.

Every claim above is checked against REAL Django 5.2, in a subprocess, on both
settings of ``template_resolve_lazy``: #2691 was reverted once for verifying
only the four sinks that never need the items.

Any cell where djust does not answer Django's string is either in
``LAZY_PARITY`` / ``EAGER_PARITY`` (it agrees) or it is not — and the two sets
are pinned, so adding a divergence and removing one are equally loud.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import threading
import time

import pytest

# --------------------------------------------------------------------------
# The differential child.
#
# One process per (engine, template_resolve_lazy); it renders the cells named
# on argv and writes one JSON line per cell, FLUSHED — so a parent that has to
# kill it still knows every answer it did produce, and which cell it died on.
# --------------------------------------------------------------------------
_CHILD = textwrap.dedent(
    '''
    import array, collections, json, re, sys
    import django
    from django.conf import settings

    ENGINE, LAZY, CELLS = sys.argv[1], sys.argv[2] == "lazy", json.loads(sys.argv[3])

    settings.configure(
        SECRET_KEY="x", DEBUG=False, USE_TZ=False,
        TEMPLATES=[{
            "BACKEND": (
                "django.template.backends.django.DjangoTemplates"
                if ENGINE == "django"
                else "djust.template_backend.DjustTemplateBackend"
            ),
            "NAME": "e", "DIRS": [], "APP_DIRS": False, "OPTIONS": {},
        }],
        LIVEVIEW_CONFIG={"template_resolve_lazy": LAZY},
    )
    django.setup()
    from django.template import engines
    ENG = engines["e"]

    BIG = 10**9


    class Liar:
        """#2678: no ``__iter__``, and a stated ``__len__`` its
        ``__getitem__`` can never reach."""

        def __len__(self):
            return BIG

        def __getitem__(self, k):
            return "x"

        def __str__(self):
            return "liar"


    class QuerySetShape:
        """Sized, re-iterable, subscriptable, with a result cache."""

        def __init__(self, rows):
            self._result_cache = list(rows)

        def __iter__(self):
            return iter(self._result_cache)

        def __len__(self):
            return len(self._result_cache)

        def __getitem__(self, k):
            return self._result_cache[k]


    class LyingLength:
        """A stated `__len__` its own `__iter__` never honours — the shape
        `Encoded::live_walk_terminates` cannot see through. NOT in the matrix;
        used only by `TestALyingLengthIsUnfixedRatherThanNewlyBroken`."""

        def __init__(self, stated):
            self._stated = stated

        def __len__(self):
            return self._stated

        def __iter__(self):
            import itertools

            return itertools.count()

        def __str__(self):
            return "lying"


    D = [{"k": 2}, {"k": 1}]

    SHAPES = {
        "lying_len_small": lambda: LyingLength(3),
        "lying_len_big": lambda: LyingLength(BIG),
        "range_big": lambda: range(BIG),
        "range_small": lambda: range(3),
        "list": lambda: [3, 1, 2],
        "list_big": lambda: list(range(100_001)),
        "set": lambda: {3, 1, 2},
        "frozenset": lambda: frozenset({3, 1, 2}),
        "dict_keys": lambda: {3: "a", 1: "b", 2: "c"}.keys(),
        "deque": lambda: collections.deque([3, 1, 2]),
        "array": lambda: array.array("i", [3, 1, 2]),
        "bytes": lambda: b"\\x03\\x01\\x02",
        "tuple": lambda: (3, 1, 2),
        "generator": lambda: (x for x in [3, 1, 2]),
        "map": lambda: map(int, ["3", "1", "2"]),
        "zip": lambda: zip([3, 1], [2, 0]),
        "queryset": lambda: QuerySetShape([3, 1, 2]),
        "liar": lambda: Liar(),
        "dict_list": lambda: list(D),
        "dict_gen": lambda: (d for d in D),
        "dict_iter": lambda: iter(list(D)),
    }

    SINKS = {
        "for": "{% for x in v %}{{ x }},{% endfor %}",
        "join": '{{ v|join:"," }}',
        "length": "{{ v|length }}",
        "index0": "{{ v.0 }}",
        "first": "{{ v|first }}",
        "last": "{{ v|last }}",
        "slice": '{{ v|slice:":3" }}',
        "in_hit": "{% if 1 in v %}Y{% else %}N{% endif %}",
        "in_miss": "{% if 987654321 in v %}Y{% else %}N{% endif %}",
        "dictsort": '{{ v|dictsort:"k" }}',
        "dictsortreversed": '{{ v|dictsortreversed:"k" }}',
        "str": "{{ v }}",
        # NOT part of the matrix — the grid hands every cell a FRESH object
        # by design, and this one asks what happens when it does not.
        "consume_once": '{{ v|dictsort:"k" }}|{% for x in v %}{{ x }}{% endfor %}',
    }


    def scrub(text):
        # A repr carrying a heap address differs between two processes for
        # reasons that have nothing to do with the engines.
        return re.sub(r"0x[0-9a-f]+", "0xADDR", text)


    for shape, sink in CELLS:
        try:
            # A FRESH object per cell: a one-shot iterator consumed by one
            # sink would otherwise decide the next one's answer.
            out = ENG.from_string(SINKS[sink]).render({"v": SHAPES[shape]()})
            if len(out) > 400:
                out = "<%d chars>%s" % (len(out), out[:80])
            rec = {"cell": [shape, sink], "ok": True, "out": scrub(out)}
        except Exception as exc:  # noqa: BLE001 - the differential records it
            rec = {
                "cell": [shape, sink],
                "ok": False,
                "out": scrub("%s: %s" % (type(exc).__name__, exc))[:300],
            }
        sys.stdout.write(json.dumps(rec) + "\\n")
        sys.stdout.flush()
    '''
)

SHAPES = (
    "range_big",
    "range_small",
    "list",
    "list_big",
    "set",
    "frozenset",
    "dict_keys",
    "deque",
    "array",
    "bytes",
    "tuple",
    "generator",
    "map",
    "zip",
    "queryset",
    "liar",
    "dict_list",
    "dict_gen",
    "dict_iter",
)

SINKS = (
    "for",
    "join",
    "length",
    "index0",
    "first",
    "last",
    "slice",
    "in_hit",
    "in_miss",
    "dictsort",
    "dictsortreversed",
    "str",
)

#: The sinks that need EVERY item. Over a stated billion neither engine comes
#: back from these — Django because ``sorted()`` / ``list()`` / the joined
#: output is a billion long, djust because ``consume_live_items`` walks the
#: same billion. Measured with a 60 s deadline and a 4 GiB ceiling on both:
#:
#: ===============  ======================  ======================
#: cell             Django 5.2              djust (lazy)
#: ===============  ======================  ======================
#: ``for``          60 s, no output         >4 GiB after 33 s
#: ``join``         >4 GiB after 32 s       >4 GiB after 12 s
#: ``dictsort``     >4 GiB after 1 s        >4 GiB after 12 s
#: ``dictsortrev``  >4 GiB after 1 s        >4 GiB after 12 s
#: ===============  ======================  ======================
#:
#: So they are excluded from the batch grid (a child that never exits answers
#: nothing) and asserted separately by
#: :class:`TestNeitherEngineReturnsFromAWalkOfAStatedBillion`.
WALKING_SINKS = ("for", "join", "dictsort", "dictsortreversed")

#: Django walks the liar's legacy sequence protocol forever; djust raises at
#: the cap (#2678) for the four walking sinks, and its ``{% if %}`` fails soft
#: for the two ``in`` cells. Excluded from Django's batch for the same reason.
LIAR_NON_TERMINATING_FOR_DJANGO = WALKING_SINKS + ("in_hit", "in_miss")

_DEADLINE = float(os.environ.get("DJUST_2695_DEADLINE", "45"))
_RSS_CEILING_KB = 1_500_000


def _rss_kb(pid: int) -> int:
    try:
        out = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        return int(out) if out else 0
    except Exception:  # noqa: BLE001 - a dead pid is just "no memory"
        return 0


def _drain(stream, sink: list[str]) -> None:
    for line in stream:
        sink.append(line)


def _render_in_child(
    engine: str,
    lazy: bool,
    cells: list[tuple[str, str]],
    deadline: float = _DEADLINE,
) -> tuple[dict[tuple[str, str], str], str | None]:
    """Render ``cells`` in a subprocess; answer what it produced and why it
    stopped.

    The repo's ``_render_in_child`` pattern
    (``test_value_conversion_crashes_2555_2624_2572.py``) with a second axis:
    a cell that fails to return here does so by ALLOCATING — a billion
    ``Value``s, or a billion-element joined string — so the child is watched
    on memory as well as on the clock. macOS refuses
    ``setrlimit(RLIMIT_AS)``, hence the poll.

    Returns ``(answers, stop_reason)``; ``stop_reason`` is ``None`` on a clean
    exit, else ``"HANG"`` / ``"RUNAWAY-MEMORY"`` / a crash description, and
    the cell it died on is the first of ``cells`` missing from ``answers``.
    """
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            _CHILD,
            engine,
            "lazy" if lazy else "eager",
            json.dumps([list(c) for c in cells]),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    lines: list[str] = []
    errs: list[str] = []
    threads = [
        threading.Thread(target=_drain, args=(proc.stdout, lines), daemon=True),
        threading.Thread(target=_drain, args=(proc.stderr, errs), daemon=True),
    ]
    for t in threads:
        t.start()
    began = time.monotonic()
    reason: str | None = None
    while proc.poll() is None:
        if time.monotonic() - began > deadline:
            reason = "HANG"
            break
        if _rss_kb(proc.pid) > _RSS_CEILING_KB:
            reason = "RUNAWAY-MEMORY"
            break
        time.sleep(0.1)
    if reason is not None:
        proc.kill()
    proc.wait()
    for t in threads:
        t.join(timeout=5)
    answers: dict[tuple[str, str], str] = {}
    for line in lines:
        if line.strip():
            rec = json.loads(line)
            answers[tuple(rec["cell"])] = rec["out"]
    if reason is None and proc.returncode != 0:
        reason = f"CRASH rc={proc.returncode} {''.join(errs).strip()[-200:]}"
    return answers, reason


def _batch_cells(engine: str, lazy: bool) -> list[tuple[str, str]]:
    """Every cell that TERMINATES on this column, so one child can answer all
    of them."""
    cells = []
    for shape in SHAPES:
        for sink in SINKS:
            if shape == "range_big" and sink in WALKING_SINKS:
                continue
            if engine == "django" and shape == "liar":
                if sink in LIAR_NON_TERMINATING_FOR_DJANGO:
                    continue
            if engine == "djust" and not lazy and shape in ("range_big", "liar"):
                # The eager escape hatch has no live handle to read an object
                # through, so it keeps enumerating in full and both #2678's
                # and #2695's hangs stay unfixed there — deliberately, since
                # a decline would land on ``str(o)`` and answer ``{{ v.0 }}``
                # with a bracket. Asserted by
                # ``TestTheEagerHatchStillEnumerates``.
                continue
            cells.append((shape, sink))
    return cells


@pytest.fixture(scope="module")
def grids() -> dict[str, dict[tuple[str, str], str]]:
    out = {}
    for key, engine, lazy in (
        ("django", "django", True),
        ("lazy", "djust", True),
        ("eager", "djust", False),
    ):
        answers, reason = _render_in_child(engine, lazy, _batch_cells(engine, lazy))
        assert reason is None, (
            f"the {key} grid did not finish: {reason}. It stopped at "
            f"{[c for c in _batch_cells(engine, lazy) if c not in answers][:1]}"
        )
        out[key] = answers
    return out


# --------------------------------------------------------------------------
# The parity pins.
#
# A cell is in the set when djust's rendered string EQUALS live Django's for
# the same input. Pinned as a set rather than as expected strings so a Django
# patch release that changes a repr does not churn the file, while a djust
# change that gains or loses parity anywhere is loud in both directions.
# --------------------------------------------------------------------------


def _cells(shape: str, *sinks: str) -> set[str]:
    return {f"{shape}|{sink}" for sink in sinks}


_ALL_SINKS_AGREE = (
    "list",
    "list_big",
    "tuple",
    "dict_list",
)

#: Cells where djust (``template_resolve_lazy`` ON, the shipped default)
#: renders exactly what Django 5.2 renders.
LAZY_PARITY: frozenset[str] = frozenset(
    {f"{shape}|{sink}" for shape in _ALL_SINKS_AGREE for sink in SINKS}
    # #2695: every sink a stated billion can answer without a walk.
    | _cells(
        "range_big",
        "length",
        "index0",
        "first",
        "last",
        "in_hit",
        "in_miss",
        "str",
    )
    | _cells("liar", "length", "index0", "first", "last", "slice", "str")
    | _cells(
        "range_small",
        *(s for s in SINKS if s not in ("slice", "str")),
    )
    | _cells("set", *(s for s in SINKS if s not in ("first", "last")))
    | _cells("frozenset", *(s for s in SINKS if s not in ("first", "last")))
    | _cells("dict_keys", *(s for s in SINKS if s not in ("first", "last")))
    | _cells("deque", *(s for s in SINKS if s not in ("slice", "str")))
    | _cells("array", *(s for s in SINKS if s not in ("slice", "str")))
    | _cells("bytes", *(s for s in SINKS if s not in ("slice", "str")))
    | _cells("queryset", *(s for s in SINKS if s != "str"))
    # #2693: a one-shot iterator now answers ``dictsort`` too.
    | _cells("generator", *(s for s in SINKS if s not in ("first", "last")))
    | _cells("map", *(s for s in SINKS if s not in ("first", "last")))
    | _cells("zip", *(s for s in SINKS if s not in ("first", "last")))
    | _cells("dict_gen", *(s for s in SINKS if s not in ("first", "last")))
    | _cells("dict_iter", *(s for s in SINKS if s not in ("first", "last")))
)

#: The divergences that remain, each with the reason it is out of #2695's and
#: #2693's scope. Pinned as prose next to the set above rather than as a
#: second data structure, but grouped here so a reader does not have to
#: subtract two frozensets by hand:
#:
#: * ``{first,last}`` over a ``set`` / ``frozenset`` / ``dict_keys`` /
#:   generator / ``map`` / ``zip`` / one-shot iterator — BOTH engines raise,
#:   and djust's message quotes Django's sentence verbatim
#:   (``TypeError: 'set' object is not subscriptable``); only the exception
#:   CLASS wrapping it differs. Pre-existing.
#: * ``{slice,str}`` over ``range``/``deque``/``array``/``bytes``, and ``str``
#:   over the QuerySet shape — those cross as a ``Value::List``, so
#:   ``{{ v }}`` is ``[3, 1, 2]`` where Django renders ``deque([3, 1, 2])``.
#:   A container-spelling divergence that predates both issues and is
#:   independent of the cap: ``range(3)`` has it too.
#: * ``range_big|slice`` is that same cell: the live slice answers Python's
#:   ``range(0, 3)``, which then crosses as a list exactly as ``range(3)``
#:   does. It is the class above, not a new one.


#: Cells where djust on the EAGER escape hatch renders exactly Django's
#: string. The hatch has no live handle, so every carrier-answered cell above
#: is missing here: a one-shot iterator falls to ``str(o)`` and ``{% for %}``
#: walks the repr CHARACTER by character. That is #2613/#2674's documented
#: non-fix, restated by measurement rather than assumed.
EAGER_PARITY: frozenset[str] = frozenset(
    {f"{shape}|{sink}" for shape in _ALL_SINKS_AGREE for sink in SINKS}
    | _cells("range_small", *(s for s in SINKS if s not in ("slice", "str")))
    | _cells("set", *(s for s in SINKS if s not in ("first", "last")))
    | _cells("frozenset", *(s for s in SINKS if s not in ("first", "last")))
    | _cells("dict_keys", *(s for s in SINKS if s not in ("first", "last")))
    | _cells("deque", *(s for s in SINKS if s not in ("slice", "str")))
    | _cells("array", *(s for s in SINKS if s not in ("slice", "str")))
    | _cells("bytes", *(s for s in SINKS if s not in ("slice", "str")))
    | _cells("queryset", *(s for s in SINKS if s != "str"))
    # A one-shot iterator has no handle here, so it keeps the terminal
    # ``str(o)`` and `{% for %}` walks the REPR character by character. The
    # cells that still agree do so because BOTH engines answer nothing:
    # ``dictsort`` over ints is a `TypeError` on Django too, and ``in`` fails
    # soft. `dict_gen` / `dict_iter` lose even those two, because Django
    # genuinely sorts them and the hatch cannot.
    | _cells("generator", "in_miss", "dictsort", "dictsortreversed", "str")
    | _cells("map", "in_miss", "dictsort", "dictsortreversed", "str")
    | _cells("zip", "in_hit", "in_miss", "dictsort", "dictsortreversed", "str")
    | _cells("dict_gen", "in_hit", "in_miss", "str")
    | _cells("dict_iter", "in_hit", "in_miss", "str")
)


class TestTheDjangoDifferential:
    """Every sink x every shape, against real Django 5.2, on both settings."""

    def test_lazy_parity_set_is_exactly_what_is_pinned(self, grids) -> None:
        django, lazy = grids["django"], grids["lazy"]
        agree = {
            f"{shape}|{sink}"
            for (shape, sink), answer in lazy.items()
            if (shape, sink) in django and django[(shape, sink)] == answer
        }
        gained = agree - LAZY_PARITY
        lost = LAZY_PARITY - agree
        assert not lost, (
            "these cells USED to answer Django's string and no longer do: "
            + json.dumps(
                {
                    c: {
                        "django": django[tuple(c.split("|"))],
                        "djust": lazy.get(tuple(c.split("|")), "<not rendered>"),
                    }
                    for c in sorted(lost)
                },
                indent=1,
            )
        )
        assert not gained, (
            "these cells now agree with Django and are not in LAZY_PARITY — "
            "add them, so the next regression is loud: " + ", ".join(sorted(gained))
        )

    def test_eager_parity_set_is_exactly_what_is_pinned(self, grids) -> None:
        django, eager = grids["django"], grids["eager"]
        agree = {
            f"{shape}|{sink}"
            for (shape, sink), answer in eager.items()
            if (shape, sink) in django and django[(shape, sink)] == answer
        }
        assert agree == EAGER_PARITY, {
            "lost": sorted(EAGER_PARITY - agree),
            "gained": sorted(agree - EAGER_PARITY),
        }

    def test_the_grid_really_covered_every_sink_and_shape(self, grids) -> None:
        """The differential is only evidence if it ran. #2691 was reverted for
        verifying four sinks; a silently shrunken matrix would repeat that."""
        seen_shapes = {shape for shape, _ in grids["lazy"]}
        seen_sinks = {sink for _, sink in grids["lazy"]}
        assert seen_shapes == set(SHAPES)
        assert seen_sinks == set(SINKS)
        assert len(grids["lazy"]) == len(SHAPES) * len(SINKS) - len(WALKING_SINKS)


class TestASizedSequenceIsNotMaterialisedAtConversion:
    """#2695 — the reproducer, cell by cell, with Django's answer inline."""

    @pytest.mark.parametrize(
        ("sink", "expected"),
        [
            ("length", "1000000000"),
            ("index0", "0"),
            ("first", "0"),
            ("last", "999999999"),
            ("in_hit", "Y"),
            ("in_miss", "Y"),
            ("str", "range(0, 1000000000)"),
        ],
    )
    def test_range_of_a_billion_answers_django_s_value(self, sink: str, expected: str) -> None:
        """Each of these ran away in memory on ``main`` and on #2691's head.

        In a child with a deadline because the failure mode is a non-return:
        an in-process assertion cannot report "did not hang".
        """
        answers, reason = _render_in_child("djust", True, [("range_big", sink)], deadline=20)
        assert reason is None, f"{sink} over range(10**9) did not return: {reason}"
        assert answers[("range_big", sink)] == expected

    def test_a_list_past_the_cap_still_renders_every_item(self) -> None:
        """The regression #2678's second version was written to avoid, and the
        reason the sink keeps its own termination rule rather than inheriting
        the conversion's cap."""
        answers, reason = _render_in_child(
            "djust",
            True,
            [("list_big", "for"), ("list_big", "join"), ("list_big", "length")],
            deadline=30,
        )
        assert reason is None, reason
        assert answers[("list_big", "length")] == "100001"
        # Every one of the 100 001 items, in order, with the separator each
        # sink uses — `{% for %}` appends a comma per item, `|join` puts one
        # BETWEEN them, hence the 100 001-character difference.
        # 488 896 digits for 0..100000, plus one comma per item for
        # `{% for %}` and one BETWEEN items for `|join`.
        assert answers[("list_big", "for")].startswith("<588897 chars>0,1,2,3,")
        assert answers[("list_big", "join")].startswith("<588896 chars>0,1,2,3,")

    def test_a_list_past_the_cap_slices_to_three_items(self) -> None:
        """``|slice`` had to grow a live arm with the conversion change: the
        fallback returns the value UNCHANGED, so a carried 100 001-item list
        rendered all of them where Django renders three."""
        answers, reason = _render_in_child("djust", True, [("list_big", "slice")], deadline=20)
        assert reason is None, reason
        assert answers[("list_big", "slice")] == "[0, 1, 2]"

    def test_the_liar_still_raises_rather_than_walking_forever(self) -> None:
        """#2678 must survive #2695. The liar states a billion and has no
        ``__iter__``, so its walk cannot end and the sink keeps the cap."""
        answers, reason = _render_in_child("djust", True, [("liar", "for")], deadline=20)
        assert reason is None, f"the liar walked instead of raising: {reason}"
        assert "yielded more than 100000 items" in answers[("liar", "for")]


class TestNeitherEngineReturnsFromAWalkOfAStatedBillion:
    """The cells this fix does NOT close, with the proof they are Django's
    behaviour rather than djust's limitation."""

    @pytest.mark.parametrize("sink", WALKING_SINKS)
    def test_django_does_not_return_either(self, sink: str) -> None:
        for engine in ("django", "djust"):
            answers, reason = _render_in_child(engine, True, [("range_big", sink)], deadline=8)
            assert reason in ("HANG", "RUNAWAY-MEMORY"), (
                f"{engine} ANSWERED {sink} over range(10**9) with "
                f"{answers.get(('range_big', sink))!r} — if it now terminates, "
                f"djust's non-return is no longer parity and this is a real gap"
            )


class TestALyingLengthIsUnfixedRatherThanNewlyBroken:
    """`Encoded::live_walk_terminates` asks the two questions CPython lets it
    ask — "did `len(o)` answer" and "does the type have `__iter__`" — and a
    class that states a length its own iterator never honours passes both.

    That is a limit of the claim, so it is measured rather than asserted. It
    is not a regression: `opaque_value`'s enumeration has no cap either, so
    the same object failed to return at the CONVERSION before #2695 — which
    is why the SMALL variant, whose length is under the cap and which
    therefore still takes the conversion path, is unchanged here. Moving the
    walk to the sink makes the big variant strictly better: only a template
    that asks for every item pays it.
    """

    def test_a_small_lying_length_still_does_not_return(self) -> None:
        """Under the cap, so the conversion still enumerates — the pre-#2695
        behaviour, unchanged."""
        _, reason = _render_in_child("djust", True, [("lying_len_small", "length")], deadline=8)
        assert reason in ("HANG", "RUNAWAY-MEMORY"), reason

    def test_a_big_lying_length_now_answers_the_cheap_sinks(self) -> None:
        """Past the cap the conversion declines, so `{{ v|length }}` is
        answered from the object instead of being paid for."""
        answers, reason = _render_in_child(
            "djust", True, [("lying_len_big", "length")], deadline=20
        )
        assert reason is None, reason
        assert answers[("lying_len_big", "length")] == "1000000000"

    def test_a_big_lying_length_still_does_not_return_from_a_full_walk(self) -> None:
        """And the walk it cannot bound is still unbounded — said out loud so
        the next reader does not take `live_walk_terminates` for a proof."""
        _, reason = _render_in_child("djust", True, [("lying_len_big", "for")], deadline=8)
        assert reason in ("HANG", "RUNAWAY-MEMORY"), reason


class TestTheEagerHatchStillEnumerates:
    """``template_resolve_lazy=False`` has no live handle to read an object
    through, so a decline would land on ``str(o)`` and answer ``{{ v.0 }}``
    with a bracket. #2678 chose the unfixed cell over the wrong one and #2695
    keeps that choice; this pins it as a decision rather than an oversight."""

    @pytest.mark.parametrize("shape", ["range_big", "liar"])
    def test_the_hatch_does_not_return_for_a_stated_billion(self, shape: str) -> None:
        answers, reason = _render_in_child("djust", False, [(shape, "length")], deadline=8)
        assert reason in ("HANG", "RUNAWAY-MEMORY"), (
            f"the eager hatch answered {shape}|length with "
            f"{answers.get((shape, 'length'))!r} — if it is fixed, say so here"
        )


class TestTheSerializationFloorHoldsOnTheNewHandle:
    """A `list` past the cap now reaches `Encoded::live`, which it never did
    before — so the floor has to be re-checked rather than assumed.

    Under the cap a list is a `Value::List` whose `Model` elements went
    through `normalize_django_value`, and the denylist was applied at the
    CONVERSION. Past it the list crosses as a carrier and `{{ v.0.password }}`
    is answered by `Context::walk_live` over the RAW list instead — a
    different mechanism, so "unchanged in kind" would be a guess.

    It is protected by `Context::protect_sidecar_strict`, which re-wraps after
    EVERY segment; this is the measurement that says so. `Encoded::live`'s own
    doc claimed no list could reach the field, and #2695 made that claim
    false — the claim is corrected there and pinned here.
    """

    @staticmethod
    def _rows(padding: int):
        from django.contrib.auth.models import User

        rows = []
        for i in range(3):
            user = User(username="alice", password="pbkdf2_sha256$SECRET", email="a@b.c")
            user.pk = i
            rows.append(user)
        # `None` padding puts the list past the cap without building 100 000
        # models; only element 0 is ever read.
        return rows + [None] * padding

    @pytest.mark.parametrize(
        ("label", "padding"), [("under the cap", 0), ("past the cap", 100_001 - 3)]
    )
    @pytest.mark.parametrize(
        ("src", "expected"),
        [
            ("{{ v.0.password }}", ""),
            ("{{ v.0.username }}", "alice"),
            ("{{ v.0 }}", "alice"),
        ],
    )
    def test_a_denylisted_field_is_empty_on_both_sides_of_the_cap(
        self, label: str, padding: int, src: str, expected: str
    ) -> None:
        from djust import _rust

        out = _rust.render_template(src, {"v": self._rows(padding)})
        assert out == expected, f"{label}: {src} rendered {out!r}"
        assert "SECRET" not in out

    def test_the_carrier_really_is_the_path_being_tested(self) -> None:
        """Non-vacuity: if the padded list did NOT cross as a carrier, the
        three cells above would be testing the old `Value::List` path twice
        and the class would prove nothing (#1200)."""
        from djust import _rust

        assert _rust.crosses_as_encoded(self._rows(100_001 - 3)) is True
        assert _rust.crosses_as_encoded(self._rows(0)) is False


class TestEverySequenceArmDecidesTheCarrier:
    """The omission #2693 is, pinned as a SET rather than as a floor (#1125).

    ``dictsort`` answered ``''`` for a carried collection because it had
    iteration of ITS OWN — a hand-written ``Value::List(items) |
    Value::Tuple(items) | …`` arm — instead of going through
    ``filters::iter_values``, the sink #2674 fixed. That is the "grep for the
    sink, not for the callers you expect" shape: the next filter written the
    same way inherits the same bug silently.

    So the rule is mechanical: any function in ``filters.rs`` that matches a
    sequence by hand must also NAME ``Value::Encoded``, i.e. must have made a
    decision about the carrier — routing it to ``iter_values``, reading it off
    the live handle, or refusing it deliberately. Four functions do today.
    """

    #: Every function allowed to hand-match a sequence, and what it decided.
    SEQUENCE_ARM_OWNERS = {
        # The sink itself.
        "iter_values": "consumes the live handle once (#2613/#2674)",
        # `dictsort` / `dictsortreversed` live here.
        "apply_builtin_filter": "routes a carrier through `iter_values` (#2693)",
        "apply_slice": "slices the live object, Django's `value[slice]` (#2695)",
        "python_getitem": "subscripts the live object (#2695)",
        "value_to_json": "has its own `Value::Encoded` arm above (#2448)",
    }

    def test_the_owner_set_is_exactly_what_is_pinned(self) -> None:
        source = (
            __import__("pathlib")
            .Path(__file__)
            .resolve()
            .parents[2]
            .joinpath("crates/djust_templates/src/filters.rs")
            .read_text()
        )
        # `filters.rs` interleaves SIX `#[cfg(test)] mod` blocks with its
        # production code, so splitting at the first one would hide two
        # thirds of the file — including `python_getitem`. Drop each block
        # from its attribute to the `}` that closes the module at column 0.
        production_lines: list[str] = []
        in_tests = False
        for line in source.splitlines():
            if not in_tests and line == "#[cfg(test)]":
                in_tests = True
                continue
            if in_tests:
                if line == "}":
                    in_tests = False
                continue
            production_lines.append(line)
        assert len(production_lines) > 4000, (
            "the test-module stripper dropped the file — the scan below would "
            f"then be vacuous (kept {len(production_lines)} lines)"
        )
        owners: dict[str, list[str]] = {}
        current = None
        for line in production_lines:
            # Top-level `fn` only: a nested helper (`python_getitem`'s `nth`)
            # is part of its parent, not an owner of its own.
            if line.startswith(("fn ", "pub fn ", "pub(crate) fn ")):
                current = line.split("fn ", 1)[1].split("(")[0].split("<")[0]
                owners.setdefault(current, [])
            if current is not None:
                owners[current].append(line)
        # The signature of a hand-written sequence arm is destructuring the
        # ITEMS out of all three sequence variants. `rebuild_like` matches
        # `Value::NamedTuple { .. }` without them — it BUILDS a sequence
        # rather than reading one — so it is correctly not an owner.
        hand_matchers = {
            name
            for name, body in owners.items()
            if any("Value::NamedTuple { items, .. }" in line for line in body)
        }
        assert hand_matchers == set(self.SEQUENCE_ARM_OWNERS), {
            "new hand-matching functions — route the carrier through "
            "`iter_values` (or decide it explicitly) and add them here": sorted(
                hand_matchers - set(self.SEQUENCE_ARM_OWNERS)
            ),
            "gone": sorted(set(self.SEQUENCE_ARM_OWNERS) - hand_matchers),
        }
        for name in hand_matchers:
            assert any("Value::Encoded" in line for line in owners[name]), (
                f"{name} matches a sequence by hand and never names "
                f"Value::Encoded — a carried collection falls to its "
                f"catch-all, which is exactly #2693"
            )


class TestDictsortConsumesTheLiveHandle:
    """#2693 — link N+1 of #2674."""

    @pytest.mark.parametrize("shape", ["dict_gen", "dict_iter"])
    @pytest.mark.parametrize(
        ("sink", "expected"),
        [
            ("dictsort", "[{&#x27;k&#x27;: 1}, {&#x27;k&#x27;: 2}]"),
            ("dictsortreversed", "[{&#x27;k&#x27;: 2}, {&#x27;k&#x27;: 1}]"),
        ],
    )
    def test_a_one_shot_iterator_sorts_where_it_used_to_answer_empty(
        self, shape: str, sink: str, expected: str
    ) -> None:
        answers, reason = _render_in_child("djust", True, [(shape, sink)], deadline=20)
        assert reason is None, reason
        assert answers[(shape, sink)] == expected

    def test_a_one_shot_iterator_is_spent_by_the_sort_exactly_once(self) -> None:
        """Django's ``sorted()`` consumes the generator, so the ``{% for %}``
        AFTER it is empty. The whole point of routing through the one sink is
        that the handle is read once, wherever it is first read — a second arm
        with iteration of its own would read it twice, or not at all.

        Asserted against live Django rather than a transcribed expectation,
        because "how much of the generator is left" is exactly the claim a
        transcription would get wrong.
        """
        cell = [("dict_gen", "consume_once")]
        mine, reason = _render_in_child("djust", True, cell, deadline=20)
        assert reason is None, reason
        theirs, reason = _render_in_child("django", True, cell, deadline=20)
        assert reason is None, reason
        assert mine[("dict_gen", "consume_once")] == theirs[("dict_gen", "consume_once")]
        # And it really is spent: the loop after the sort renders nothing.
        assert mine[("dict_gen", "consume_once")].endswith("|")
