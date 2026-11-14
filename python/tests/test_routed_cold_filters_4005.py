"""Eight cold built-in filters run Django's own functions (#4005); four stay native.

``cut``, ``last``, ``random``, ``dictsort``, ``dictsortreversed``, ``ljust``,
``rjust`` and ``center`` call ``django.template.defaultfilters.<name>`` through
the bridge #3549 and #3551 built, instead of a Rust port of each. ``escape``,
``escapejs``, ``default_if_none`` and ``force_escape`` stay native because they
are not cold: a Python call per value cost 1.8-2.9x on a 500-row LiveView
render for the first three, and every hybrid-template attribute slot ends in
``force_escape``.

What this file proves, per filter in ``CASES`` (routed AND native):

* **Byte-identical to Django** over a seeded randomized sweep of values and
  arguments — quoted, unquoted and variable arguments, ``SafeData`` input, and
  the chains that tell ``escape`` from ``force_escape`` or show whether a result
  is safe — rendered through ``_rust.render_template`` AND a ``RustLiveView``,
  the two engine entry points. An exception is compared as "both raise", since
  a template that raises in Django must not render in djust either.
* **The caps stay.** ``ljust`` / ``rjust`` / ``center`` refuse a width past
  ``MAX_PAD_WIDTH`` with the same message as before, without asking Python to
  allocate it; ``dictsort`` over a carried collection walks it through the
  ``OPAQUE_ITEM_CAP`` bound first and raises past it.
* **What djust carries survives.** The routed ``last`` / ``random`` /
  ``dictsort`` hand back djust's own values by identity, so a row picked out of
  a list keeps its attributes for the next lookup.
"""

from __future__ import annotations

import random
import string
from decimal import Decimal
from typing import Any, Callable

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402
from django.utils.safestring import mark_safe  # noqa: E402

from djust import _rust  # noqa: E402
from djust.serialization import normalize_django_value  # noqa: E402

RAISES = "<<RAISES>>"

#: Cases per (filter, template form). Seeded, so a failure reproduces.
SWEEP = 120

_TEXT_ALPHABET = (
    string.ascii_letters + string.digits + " <>&'\"\\/;:=-_\n\t\r" + "  \x00\x1f`" + "é中\U0001f44d́"
)


def _django(source: str, ctx: dict[str, Any], seed: int | None) -> str:
    if seed is not None:
        random.seed(seed)
    try:
        return DjangoTemplate(source).render(DjangoContext(ctx))
    except Exception:  # noqa: BLE001 - the differential records "raises"
        return RAISES


def _djust(source: str, ctx: dict[str, Any], seed: int | None) -> str:
    if seed is not None:
        random.seed(seed)
    try:
        return _rust.render_template(source, normalize_django_value(ctx))
    except Exception:  # noqa: BLE001 - the differential records "raises"
        return RAISES


def _djust_live_view(source: str, ctx: dict[str, Any], seed: int | None) -> str:
    view = _rust.RustLiveView(source)
    view.update_state(normalize_django_value(ctx))
    if seed is not None:
        random.seed(seed)
    try:
        return view.render()
    except Exception:  # noqa: BLE001 - the differential records "raises"
        return RAISES


def _text(rng: random.Random, max_len: int = 12) -> str:
    return "".join(rng.choice(_TEXT_ALPHABET) for _ in range(rng.randint(0, max_len)))


def _scalar(rng: random.Random) -> Any:
    kind = rng.randrange(9)
    if kind == 0:
        return None
    if kind == 1:
        return rng.randint(-1000, 1000)
    if kind == 2:
        return rng.choice([0.0, -1.5, 3.25, 1e20, 1e-7])
    if kind == 3:
        return Decimal(rng.choice(["0", "1.10", "-3.5", "1E-9"]))
    if kind == 4:
        return rng.choice([True, False])
    if kind == 5:
        return mark_safe(_text(rng))
    return _text(rng)


def _sequence(rng: random.Random) -> Any:
    # A list, not a tuple: `normalize_django_value` turns a tuple into a list
    # before it crosses (so `{{ p }}` alone already prints `[…]`), which is the
    # serializer's contract and not a filter's.
    return [_scalar(rng) for _ in range(rng.randint(0, 5))]


def _any_value(rng: random.Random) -> Any:
    kind = rng.randrange(5)
    if kind == 0:
        return _sequence(rng)
    if kind == 1:
        return {rng.choice(["a", "b", 0, 1, 2]): _scalar(rng) for _ in range(rng.randint(0, 3))}
    return _scalar(rng)


def _row(rng: random.Random) -> Any:
    kind = rng.randrange(6)
    if kind == 0:
        # A sequence row: `itemgetter(n)` indexes it.
        return [rng.randint(0, 3), _text(rng, 2)][: rng.randint(0, 2)]
    if kind == 1:
        return _text(rng, 3)
    row: dict[str, Any] = {}
    for key in ("k", "name"):
        if rng.random() < 0.9:
            row[key] = rng.choice(
                [rng.randint(0, 4), rng.randint(0, 4), _text(rng, 2), None, Decimal("1.5")]
            )
    if rng.random() < 0.5:
        row["sub"] = {"x": rng.randint(0, 3)}
    return row


def _rows(rng: random.Random) -> Any:
    kind = rng.randrange(6)
    if kind == 0:
        # Homogeneous int keys: the shape that SORTS, with ties for stability.
        return [{"k": rng.randint(0, 3), "name": _text(rng, 2)} for _ in range(rng.randint(0, 6))]
    if kind == 1:
        return [[rng.randint(0, 3), _text(rng, 2)] for _ in range(rng.randint(0, 5))]
    if kind == 2:
        return [_text(rng, 3) for _ in range(rng.randint(0, 5))]
    if kind == 3:
        return _scalar(rng)
    return [_row(rng) for _ in range(rng.randint(0, 5))]


def _width(rng: random.Random) -> Any:
    return rng.choice([0, 1, 3, 5, 8, 13, 20, -2, rng.randint(0, 40)])


#: (template, context builder, reseeds Python's `random` before each render).
Case = tuple[str, Callable[[random.Random], dict[str, Any]], bool]

CASES: dict[str, list[Case]] = {
    "cut": [
        (
            '{{ p|cut:"%s" }}' % ch,
            lambda r: {"p": _scalar(r)},
            False,
        )
        for ch in ("a", " ", ";", "&", "<", "1")
    ]
    + [
        ("{{ p|cut:q }}", lambda r: {"p": _scalar(r), "q": r.choice(_TEXT_ALPHABET)}, False),
        ("{{ p|safe|cut:q }}", lambda r: {"p": _text(r), "q": r.choice("&;<a")}, False),
        ('{{ p|safe|cut:";" }}', lambda r: {"p": "&lt;b&gt;" + _text(r)}, False),
    ],
    "last": [
        ("{{ p|last }}", lambda r: {"p": _any_value(r)}, False),
        ("{{ p|safe|last }}", lambda r: {"p": _text(r)}, False),
        ("{% with x=p|last %}{{ x.name }}{% endwith %}", lambda r: {"p": _rows(r)}, False),
    ],
    "random": [
        ("{{ p|random }}", lambda r: {"p": _any_value(r)}, True),
        (
            "{{ p|random }}",
            lambda r: {"p": {i: _text(r) for i in r.sample(range(4), r.randint(0, 4))}},
            True,
        ),
        ("{{ p|safe|random }}", lambda r: {"p": _text(r)}, True),
    ],
    "default_if_none": [
        ('{{ p|default_if_none:"<b>fb</b>" }}', lambda r: {"p": _any_value(r)}, False),
        ("{{ p|default_if_none:0 }}", lambda r: {"p": _scalar(r)}, False),
        ("{{ p|default_if_none:q }}", lambda r: {"p": _scalar(r), "q": _scalar(r)}, False),
        ('{{ p|default_if_none:"x"|upper }}', lambda r: {"p": _scalar(r)}, False),
        ('{{ p|default_if_none:"<i>"|force_escape }}', lambda r: {"p": _scalar(r)}, False),
        ("{{ p|safe|default_if_none:q }}", lambda r: {"p": _scalar(r), "q": _scalar(r)}, False),
        (
            "{% if p|default_if_none:0 %}T{% else %}F{% endif %}",
            lambda r: {"p": _scalar(r)},
            False,
        ),
    ],
    "escape": [
        ("{{ p|escape }}", lambda r: {"p": _any_value(r)}, False),
        ("{{ p|escape|upper }}", lambda r: {"p": _text(r)}, False),
        ("{{ p|safe|escape }}", lambda r: {"p": _text(r)}, False),
        ("{{ p|escape|safe }}", lambda r: {"p": _text(r)}, False),
        ("{{ p|escape|escape }}", lambda r: {"p": _text(r)}, False),
        ("{{ p|escape|force_escape }}", lambda r: {"p": _any_value(r)}, False),
        ("{{ p|safe|escape|upper }}", lambda r: {"p": _scalar(r)}, False),
        (
            "{% autoescape off %}{{ p|escape }}{% endautoescape %}",
            lambda r: {"p": _text(r)},
            False,
        ),
    ],
    "escapejs": [
        ("{{ p|escapejs }}", lambda r: {"p": _scalar(r)}, False),
        ("{{ p|escapejs|safe }}", lambda r: {"p": _text(r) + "`"}, False),
        ("{{ p|escapejs|upper }}", lambda r: {"p": _scalar(r)}, False),
        ("{{ p|safe|escapejs }}", lambda r: {"p": _text(r)}, False),
        (
            "{% autoescape off %}{{ p|escapejs }}{% endautoescape %}",
            lambda r: {"p": _any_value(r)},
            False,
        ),
    ],
    "dictsort": [
        ('{{ p|dictsort:"k" }}', lambda r: {"p": _rows(r)}, False),
        ('{{ p|dictsort:"name" }}', lambda r: {"p": _rows(r)}, False),
        ('{{ p|dictsort:"sub.x" }}', lambda r: {"p": _rows(r)}, False),
        ('{{ p|dictsort:"_k" }}', lambda r: {"p": _rows(r)}, False),
        ("{{ p|dictsort:0 }}", lambda r: {"p": _rows(r)}, False),
        ('{{ p|dictsort:"0" }}', lambda r: {"p": _rows(r)}, False),
        (
            "{{ p|dictsort:q }}",
            lambda r: {"p": _rows(r), "q": r.choice(["k", "name", 0, 1, "1", 1.5])},
            False,
        ),
        (
            '{% for x in p|dictsort:"k" %}{{ x.name }},{% endfor %}',
            lambda r: {"p": _rows(r)},
            False,
        ),
    ],
    "dictsortreversed": [
        ('{{ p|dictsortreversed:"k" }}', lambda r: {"p": _rows(r)}, False),
        ("{{ p|dictsortreversed:0 }}", lambda r: {"p": _rows(r)}, False),
        (
            "{{ p|dictsortreversed:q }}",
            lambda r: {"p": _rows(r), "q": r.choice(["k", "name", 0, "1"])},
            False,
        ),
    ],
    **{
        name: [
            ('{{ p|%s:"%%s" }}' % name, lambda r: {"p": _scalar(r)}, False),
            ("{{ p|%s:w }}" % name, lambda r: {"p": _scalar(r), "w": _width(r)}, False),
            ("{{ p|safe|%s:w }}" % name, lambda r: {"p": _text(r), "w": _width(r)}, False),
            ('{{ p|%s:"abc" }}' % name, lambda r: {"p": _text(r)}, False),
        ]
        for name in ("ljust", "rjust", "center")
    },
}

ROUTED = sorted(CASES)  # routed and native alike: every filter #4005 names


def _materialize(source: str, rng: random.Random) -> str:
    # A pad template carries a `%s` slot for a quoted width literal.
    if '"%s"' in source:
        return source % _width(rng)
    return source


@pytest.mark.parametrize("filter_name", ROUTED)
def test_routed_filter_is_byte_identical_to_django(filter_name: str) -> None:
    """A seeded randomized differential per filter, on both engine entry points."""
    rng = random.Random(4005 + ROUTED.index(filter_name))
    agreed_non_empty = 0
    mismatches: list[str] = []
    for form, (template, build, reseed) in enumerate(CASES[filter_name]):
        for case in range(SWEEP):
            source = _materialize(template, rng)
            ctx = build(rng)
            seed = (form * 10_000 + case) if reseed else None
            expected = _django(source, ctx, seed)
            got = _djust(source, ctx, seed)
            live = _djust_live_view(source, ctx, seed)
            if got != expected:
                mismatches.append(f"{source!r} on {ctx!r}: django={expected!r} djust={got!r}")
            elif live != expected:
                mismatches.append(
                    f"RustLiveView {source!r} on {ctx!r}: django={expected!r} djust={live!r}"
                )
            elif expected not in ("", RAISES):
                agreed_non_empty += 1
    assert not mismatches, "\n".join(mismatches[:15]) + f"\n({len(mismatches)} in all)"
    # Non-vacuity: the sweep must reach the filter's real output, not only
    # empty strings and raises.
    assert agreed_non_empty > SWEEP, (filter_name, agreed_non_empty)


@pytest.mark.parametrize("filter_name", ["ljust", "rjust", "center"])
def test_pad_width_cap_and_its_message_are_kept(filter_name: str) -> None:
    """Past `MAX_PAD_WIDTH` the width is refused before Python allocates it."""
    source = '{{ p|%s:"1000001" }}' % filter_name
    with pytest.raises(Exception) as caught:
        _rust.render_template(source, {"p": "ab"})
    message = str(caught.value)
    assert (
        f"filter '{filter_name}' asks for a width of 1000001, past djust's 1000000 cap" in message
    ), message
    # Exactly at the cap is still a real pad, answered by Django.
    out = _rust.render_template('{{ p|%s:"1000000" }}' % filter_name, {"p": "ab"})
    assert len(out) == 1_000_000
    assert out == DjangoTemplate('{{ p|%s:"1000000" }}' % filter_name).render(
        DjangoContext({"p": "ab"})
    )


@pytest.mark.parametrize("filter_name", ["ljust", "rjust", "center"])
def test_a_bad_width_still_raises_through_the_chokepoint(filter_name: str) -> None:
    """`int(arg)` raises in Django, and the routed arm keeps djust's raise."""
    with pytest.raises(Exception):
        DjangoTemplate('{{ p|%s:"x" }}' % filter_name).render(DjangoContext({"p": "ab"}))
    with pytest.raises(Exception):
        _rust.render_template('{{ p|%s:"x" }}' % filter_name, {"p": "ab"})


def _bounded_rows(n: int):
    # FINITE by construction: a gate-off that drops the bound sorts these
    # rows and renders them, which is what the assertion below refuses, and
    # it can never walk forever.
    for i in range(n):
        yield {"k": -i}


@pytest.mark.parametrize("filter_name", ["dictsort", "dictsortreversed"])
def test_dictsort_walks_a_carried_collection_through_the_item_cap(filter_name: str) -> None:
    """One past `OPAQUE_ITEM_CAP` raises instead of being handed to Django whole."""
    source = '{{ v|%s:"k" }}' % filter_name
    small = _rust.render_template(source, normalize_django_value({"v": _bounded_rows(3)}))
    assert small == DjangoTemplate(source).render(DjangoContext({"v": _bounded_rows(3)}))
    with pytest.raises(Exception, match="yielded more than 100000 items"):
        _rust.render_template(source, normalize_django_value({"v": _bounded_rows(100_001)}))


class _Row:
    def __init__(self, name: str, k: int) -> None:
        self.name = name
        self.k = k

    def __str__(self) -> str:
        return "row"


@pytest.mark.parametrize(
    "source",
    [
        "{% with x=p|last %}{{ x.name }}{% endwith %}",
        '{% for x in p|dictsort:"k" %}{{ x.name }},{% endfor %}',
        '{% for x in p|dictsortreversed:"k" %}{{ x.name }},{% endfor %}',
        "{% with x=p|random %}{{ x.name }}{% endwith %}",
    ],
)
def test_a_picked_row_keeps_its_attributes(source: str) -> None:
    """The element comes back as djust's own value, so `x.name` still resolves."""
    rows = [_Row("b", 2), _Row("a", 1), _Row("c", 3)]
    random.seed(7)
    expected = DjangoTemplate(source).render(DjangoContext({"p": rows}))
    random.seed(7)
    got = _rust.render_template(source, normalize_django_value({"p": rows}))
    assert got == expected
    assert got.strip(",")


def test_escape_is_conditional_and_force_escape_is_not() -> None:
    """The two cells that tell `escape` from `force_escape` (#2281)."""
    ctx = {"p": "<b>"}
    assert _djust("{{ p|safe|escape }}", ctx, None) == "<b>"
    assert _djust("{{ p|safe|force_escape }}", ctx, None) == "&lt;b&gt;"
    assert _djust("{{ p|escape|safe }}", ctx, None) == "&lt;b&gt;"


@pytest.mark.parametrize("filter_name", ["ljust", "rjust", "center"])
@pytest.mark.parametrize("width", ['" 5 "', '"+5"', '"1_0"', '"65535"', '"65536"', '"70000"'])
def test_python_int_spellings_and_the_old_formatter_boundary(filter_name: str, width: str) -> None:
    """Moved from the Rust suite when the pad filters became Django's own: `int()`'s
    spellings pass the chokepoint, and the width that once panicked Rust's
    formatter (65536) renders as Python pads it."""
    source = "{{ p|%s:%s }}" % (filter_name, width)
    expected = DjangoTemplate(source).render(DjangoContext({"p": "ab"}))
    assert _rust.render_template(source, {"p": "ab"}) == expected


#: Moved from `crates/djust_templates/tests/test_xss_prevention.rs` when
#: `escapejs` became Django's own function: (input, escapes that must appear,
#: raw text that must not).
ESCAPEJS_CASES = [
    ("</script><script>alert(1)</script>", ["\\u003C", "\\u003E"], ["</script>"]),
    ("'; alert('xss'); //", ["\\u0027"], ["'"]),
    ('"; alert("xss"); //', ["\\u0022"], ['alert("xss")']),
    ("\\'; alert(1); //", ["\\u005C", "\\u0027"], []),
    ("line1\nline2", ["\\u000A"], ["\n"]),
    ("line1\rline2", ["\\u000D"], ["\r"]),
    ("col1\tcol2", ["\\u0009"], ["\t"]),
    ("null\x00byte", ["\\u0000"], []),
    ("a b c", ["\\u2028", "\\u2029"], [" ", " "]),
    ("<>&", ["\\u003C", "\\u003E", "\\u0026"], []),
    ("a=b", ["\\u003D"], []),
    ("a;b", ["\\u003B"], []),
    ("a-b", ["\\u002D"], []),
]


@pytest.mark.parametrize("value,present,absent", ESCAPEJS_CASES)
def test_escapejs_neutralizes_every_js_breakout(value, present, absent) -> None:
    source = "{% autoescape off %}{{ p|escapejs }}{% endautoescape %}"
    out = _rust.render_template(source, {"p": value})
    assert out == DjangoTemplate(source).render(DjangoContext({"p": value}))
    for text in present:
        assert text in out, (value, text, out)
    for text in absent:
        assert text not in out, (value, text, out)
    # Under autoescape the escapes are HTML-escaped once more, never undone.
    escaped = _rust.render_template("{{ p|escapejs }}", {"p": value})
    assert "<" not in escaped and "\n" not in escaped


def test_escape_then_safe_never_uncovers_live_markup() -> None:
    """Moved from the Rust XSS suite (#2281): `escape` is eager, so `|safe` after
    it marks the ESCAPED text."""
    out = _rust.render_template("{{ html|escape|safe }}", {"html": "<img src=x onerror=alert(1)>"})
    assert "<img" not in out
    assert out == "&lt;img src=x onerror=alert(1)&gt;"


@pytest.mark.django_db
@pytest.mark.parametrize("filter_name", ["dictsort", "dictsortreversed"])
def test_a_raw_model_list_is_not_ordered_by_a_floor_field(filter_name: str) -> None:
    """Raw `User` objects straight into the engine, with no `normalize_django_value`
    in front: the sort must not order rows by `password` (a ranking of hashes is a
    disclosure). Django sorts by it; djust renders the empty loop."""
    from django.contrib.auth.models import User

    # Usernames in the OPPOSITE order to the password hashes, so an order that
    # followed the password is visible.
    User.objects.create(username="a", password="pbkdf2$z")
    User.objects.create(username="b", password="pbkdf2$y")
    User.objects.create(username="c", password="pbkdf2$x")
    rows = list(User.objects.order_by("username"))
    source = "{% for u in rows|" + filter_name + ":'password' %}[{{ u.username }}]{% endfor %}"
    by_password = DjangoTemplate(source).render(DjangoContext({"rows": rows}))
    assert by_password in ("[c][b][a]", "[a][b][c]"), by_password
    got = _rust.render_template(source, {"rows": rows})
    assert got == "", got
    # Non-vacuity: a readable column sorts.
    readable = "{% for u in rows|" + filter_name + ":'username' %}[{{ u.username }}]{% endfor %}"
    assert _rust.render_template(readable, {"rows": rows}) == DjangoTemplate(readable).render(
        DjangoContext({"rows": rows})
    )
