"""#2556 PRs B–E: `filter`, `cycle`/`resetcycle`, `lorem`, `debug`, `querystring`.

Django-parity, in-process, for the five Django built-in tags the Rust parser
did not know, on BOTH Rust entries the plain backend and the LiveView path use:

* ``backend_render`` — ``DjustTemplateBackend.from_string(...).render(...)``,
  the path the Django-suite scoreboard (#2517) scores and the one that carries
  a ``RequestContext``'s request since #2556;
* ``liveview_render`` — ``LiveView.render_full_template`` over a real
  ``RequestFactory`` request, the HTTP-GET LiveView entry.

Every class pairs the suite's own cells (by id, verbatim) with a seeded
randomized differential against Django (the v1.1.1-2 rule: a curated table
samples one axis; the sweep finds the next one).

The reference is Django itself: ``django.template.Template`` rendered
in-process with the same context, so "what does Django do" is measured, never
recalled.
"""

from __future__ import annotations

import random
import re
from typing import Any, Dict, Optional

import django
import pytest
from django.contrib.auth.models import User
from django.db import models
from django.http import QueryDict
from django.template import Context as DjangoContext
from django.template import RequestContext
from django.template import Template as DjangoTemplate
from django.template import TemplateSyntaxError
from django.test import RequestFactory
from django.utils.datastructures import MultiValueDict
from django.utils.lorem_ipsum import COMMON_P, WORDS

from djust.template_backend import DjustTemplateBackend

# `render_full_template` saves the request session (the LiveView HTTP-GET entry).
pytestmark = pytest.mark.django_db

# --------------------------------------------------------------------------- #
# Entries
# --------------------------------------------------------------------------- #

_BACKEND = DjustTemplateBackend(
    params={"NAME": "djust", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}}
)
_FACTORY = RequestFactory()
_DJ_IF_MARKER_RE = re.compile(r"<!--/?dj-if[^>]*-->")


def django_render(src: str, ctx: Optional[Dict[str, Any]] = None, request=None) -> str:
    """Django's engine, in-process. With ``request``, a ``RequestContext``."""
    context = (
        RequestContext(request, ctx or {}) if request is not None else DjangoContext(ctx or {})
    )
    return DjangoTemplate(src).render(context)


def backend_render(
    src: str,
    ctx: Optional[Dict[str, Any]] = None,
    request=None,
    *,
    request_context: bool = False,
) -> str:
    """The plain ``DjustTemplateBackend`` entry.

    ``request_context=True`` passes a ``RequestContext(request, ctx)`` as the
    context and NO ``request=`` kwarg — the shape Django's ``Engine`` adapter
    (and the #2517 scoreboard) uses, and the one whose request the backend
    dropped before #2556.
    """
    template = _BACKEND.from_string(src)
    if request_context:
        assert request is not None
        return template.render(context=RequestContext(request, ctx or {}))
    return template.render(context=ctx or {}, request=request)


def liveview_render(src: str, ctx: Optional[Dict[str, Any]] = None, path: str = "/") -> str:
    """The LiveView HTTP-GET entry (``render_full_template``), markers stripped."""
    from django.contrib.sessions.middleware import SessionMiddleware

    from djust.live_view import LiveView

    values = dict(ctx or {})

    class _V(LiveView):
        def mount(self, request, **kwargs):
            for key, value in values.items():
                setattr(self, key, value)

        def get_context_data(self, **kwargs):
            base = super().get_context_data(**kwargs)
            base.update(values)
            return base

    _V.template = "<div dj-root>inner</div>"
    _V._full_template = f"<html><body><div dj-root>inner</div><nav>{src}</nav></body></html>"

    request = _FACTORY.get(path)
    SessionMiddleware(lambda r: r).process_request(request)
    request.session.save()
    view = _V()
    view.setup(request)
    view.mount(request)
    view._full_template = _V._full_template
    html = view.render_full_template(request, serialized_context=view.get_context_data())
    match = re.search(r"<nav>(.*?)</nav>", html, re.S)
    assert match is not None, html
    return _DJ_IF_MARKER_RE.sub("", match.group(1))


def assert_both_entries_agree(src: str, ctx: Optional[Dict[str, Any]] = None) -> str:
    """Django == backend == LiveView, byte for byte. Returns Django's bytes."""
    expected = django_render(src, ctx)
    assert backend_render(src, ctx) == expected, src
    assert liveview_render(src, ctx) == expected, src
    return expected


def assert_refused(src: str, ctx: Optional[Dict[str, Any]], message: str) -> None:
    """Django raises ``TemplateSyntaxError``; djust surfaces the same text.

    The djust side is typed ``Exception`` until #2549 types the parse-time
    channel; the MESSAGE is what is pinned here so the cell flips that day.
    """
    with pytest.raises(TemplateSyntaxError, match=re.escape(message)):
        django_render(src, ctx)
    with pytest.raises(Exception, match=re.escape(message)):
        backend_render(src, ctx)


# =========================================================================== #
# B — {% filter %}
# =========================================================================== #

XSS = "<script>alert(1)</script> & \"q\" 'r'"


class TestFilterTag:
    """`filter01`–`filter04` verbatim, then chains, args, escaping, nesting."""

    @pytest.mark.parametrize(
        "src, ctx",
        [
            ("{% filter upper %}{% endfilter %}", {}),
            ("{% filter upper %}django{% endfilter %}", {}),
            ("{% filter upper|lower %}django{% endfilter %}", {}),
            ("{% filter cut:remove %}djangospam{% endfilter %}", {"remove": "spam"}),
        ],
        ids=["filter01", "filter02", "filter03", "filter04"],
    )
    def test_the_suite_cells(self, src, ctx):
        assert_both_entries_agree(src, ctx)

    @pytest.mark.parametrize(
        "src",
        [
            "{% filter force_escape %}{{ p }}{% endfilter %}",
            "{% filter upper %}{{ p }}{% endfilter %}",
            "{% filter lower|force_escape %}<b>{{ p }}</b>{% endfilter %}",
            "{% filter striptags %}<b>{{ p }}</b>{% endfilter %}",
            "{% filter linebreaksbr %}a\n{{ p }}{% endfilter %}",
            "{% filter truncatechars:9 %}{{ p }}{% endfilter %}",
            '{% filter cut:"<" %}{{ p }}{% endfilter %}',
            "{% filter title|urlencode %}{{ p }}{% endfilter %}",
        ],
    )
    def test_the_body_enters_the_chain_safe_and_the_output_is_the_chains(self, src):
        """`NodeList.render` is a `SafeString` and a `FilterNode`'s return is
        joined into the output AS-IS (only `VariableNode` escapes): `force_escape`
        re-escapes the already-escaped `{{ p }}` (double-escaped, as Django);
        `upper` upper-cases the body's own `&lt;` to `&LT;` and that is the
        output — no second escape."""
        assert_both_entries_agree(src, {"p": XSS})

    def test_cycle21_the_filter_plus_named_cycle_cell(self):
        src = "{% filter force_escape %}{% cycle one two as foo %} & {% cycle foo %}{% endfilter %}"
        out = assert_both_entries_agree(src, {"two": "C & D", "one": "A & B"})
        assert out == "A &amp;amp; B &amp; C &amp;amp; D"

    @pytest.mark.parametrize(
        "src",
        [
            "{% for x in xs %}{% filter upper %}{{ x }}-{% endfilter %}{% endfor %}",
            "{% if p %}{% filter lower %}{{ p }}{% endfilter %}{% endif %}",
            "{% filter upper %}{% for x in xs %}{{ x }}{% endfor %}{% endfilter %}",
            "{% filter upper %}{% if p %}yes{{ p }}{% else %}no{% endif %}{% endfilter %}",
            "{% with q=p %}{% filter upper %}{{ q }}{% endfilter %}{% endwith %}",
            "{% filter upper %}{% filter lower %}MiXed{% endfilter %}!{% endfilter %}",
        ],
    )
    def test_inside_and_around_the_other_block_tags(self, src):
        assert_both_entries_agree(src, {"p": "a<b", "xs": ["x", "y<z"]})

    @pytest.mark.parametrize(
        "src, name",
        [
            ("{% filter safe %}fail{% endfilter %}", "safe"),
            ("{% filter upper|safe %}fail{% endfilter %}", "safe"),
            ("{% filter escape %}fail{% endfilter %}", "escape"),
            ("{% filter upper|escape %}fail{% endfilter %}", "escape"),
        ],
        ids=["filter05", "filter05bis", "filter06", "filter06bis"],
    )
    def test_safe_and_escape_are_refused_with_djangos_message(self, src, name):
        assert_refused(
            src, {}, f'"filter {name}" is not permitted.  Use the "autoescape" tag instead.'
        )

    def test_markers_are_off_inside_the_body_on_the_liveview_entry(self):
        """A `{% if %}` inside `{% filter %}` is text to the chain, not a VDOM
        boundary: the marker bytes would otherwise go through `upper`."""
        from djust import _rust

        view = _rust.RustLiveView(
            "{% filter upper %}{% if x %}<b>a</b>{% endif %}{% endfilter %}|{% if x %}<b>b</b>{% endif %}"
        )
        view.update_state({"x": True})
        html = view.render()
        before, after = html.split("|")
        assert before == "<B>A</B>", before
        # The control: the same `{% if %}` outside the block keeps its marker.
        assert '<!--dj-if id="if-' in after, after

    def test_randomized_chains_agree_with_django(self):
        """Random chains × bodies with markup, `{{ }}`, `{% cycle %}`, `{% if %}`."""
        rng = random.Random(2556)
        filters = [
            "upper",
            "lower",
            "title",
            "capfirst",
            "force_escape",
            "striptags",
            "linebreaksbr",
            "truncatechars:7",
            'cut:"a"',
            "cut:remove",
            # `wordcount` / `length` return an int, which Django's own
            # `NodeList.render` cannot join (`TypeError`) — not a chain a
            # template can use on either engine.
            "slugify",
            'default:"d"',
            "urlencode",
            "addslashes",
        ]
        bodies = [
            "{{ p }}",
            "<b>{{ p }}</b>",
            "{% cycle 'a' 'b' %}{% cycle 'a' 'b' %}",
            "{% if p %}{{ p }}{% else %}none{% endif %}",
            "plain text & more",
            "{{ n }} things",
            "",
            "{% for x in xs %}{{ x }},{% endfor %}",
        ]
        ctx = {"p": XSS, "remove": "a", "n": 1234567, "xs": ["<i>", "j"]}
        for _ in range(200):
            chain = "|".join(rng.sample(filters, rng.randint(1, 3)))
            body = rng.choice(bodies)
            src = f"{{% filter {chain} %}}{body}{{% endfilter %}}"
            assert backend_render(src, ctx) == django_render(src, ctx), src


# =========================================================================== #
# C — {% cycle %} / {% resetcycle %}
# =========================================================================== #


class TestCycleStateModel:
    """The 13 previously-FAILing `test_cycle` shapes, the `resetcycle` cells,
    and the invariants of Django's per-node per-render state."""

    @pytest.mark.parametrize(
        "src, ctx, expected",
        [
            ("{% cycle 'a' 'b' 'c' as abc %}{% cycle abc %}", {}, "ab"),
            ("{% cycle 'a' 'b' 'c' as abc %}{% cycle abc %}{% cycle abc %}", {}, "abc"),
            (
                "{% cycle 'a' 'b' 'c' as abc %}{% cycle abc %}{% cycle abc %}{% cycle abc %}",
                {},
                "abca",
            ),
            (
                "{% for i in test %}{% cycle 'a' 'b' %}{{ i }},{% endfor %}",
                {"test": list(range(5))},
                "a0,b1,a2,b3,a4,",
            ),
            ("{% cycle one two as foo %}{% cycle foo %}", {"one": "1", "two": "2"}, "12"),
            ("{% cycle one|lower two as foo %}{% cycle foo %}", {"one": "A", "two": "2"}, "a2"),
            (
                "{% cycle 'a' 'b' 'c' as abc silent %}{% cycle abc %}{% cycle abc %}{% cycle abc %}{% cycle abc %}",
                {},
                "",
            ),
            ("{% cycle 'a' 'b' as silent %}{% cycle silent %}", {}, "ab"),
            (
                "{% cycle one two as foo %} &amp; {% cycle foo %}",
                {"two": "C & D", "one": "A & B"},
                "A &amp; B &amp; C &amp; D",
            ),
            (
                "{% for x in values %}{% cycle 'a' 'b' 'c' as abc silent %}{{ x }}{% endfor %}",
                {"values": [1, 2, 3, 4]},
                "1234",
            ),
            (
                "{% for x in values %}{% cycle 'a' 'b' 'c' as abc silent %}{{ abc }}{{ x }}{% endfor %}",
                {"values": [1, 2, 3, 4]},
                "a1b2c3a4",
            ),
            ("{% cycle a as abc %}", {"a": "<"}, "&lt;"),
            ("{% cycle a b as ab %}{% cycle ab %}", {"a": "<", "b": ">"}, "&lt;&gt;"),
            ("{% cycle a|safe b as ab %}{% cycle ab %}", {"a": "<", "b": ">"}, "<&gt;"),
        ],
        ids=[
            "cycle10",
            "cycle11",
            "cycle12",
            "cycle13",
            "cycle14",
            "cycle16",
            "cycle17",
            "cycle19",
            "cycle20",
            "cycle22",
            "cycle23",
            "cycle25",
            "cycle26",
            "cycle28",
        ],
    )
    def test_the_test_cycle_cells(self, src, ctx, expected):
        assert assert_both_entries_agree(src, ctx) == expected

    def test_cycle24_a_non_only_include_sees_the_bound_name(self, tmp_path):
        (tmp_path / "included-cycle.html").write_text("{{ abc }}", encoding="utf-8")
        src = "{% for x in values %}{% cycle 'a' 'b' 'c' as abc silent %}{% include 'included-cycle.html' %}{% endfor %}"
        backend = DjustTemplateBackend(
            params={"NAME": "djust", "DIRS": [str(tmp_path)], "APP_DIRS": False, "OPTIONS": {}}
        )
        out = backend.from_string(src).render(context={"values": [1, 2, 3, 4]})
        assert out == "abca"

    def test_an_only_include_shares_the_render_state(self, tmp_path):
        """Django's `context.new()` keeps `render_context`: the cycle inside an
        `only` include is ONE node advancing across the loop, not a fresh
        iterator per include."""
        (tmp_path / "inc.html").write_text("{% cycle 'x' 'y' 'z' %}", encoding="utf-8")
        src = "{% for i in xs %}{% include 'inc.html' only %}{% endfor %}"
        backend = DjustTemplateBackend(
            params={"NAME": "djust", "DIRS": [str(tmp_path)], "APP_DIRS": False, "OPTIONS": {}}
        )
        out = backend.from_string(src).render(context={"xs": [1, 2, 3, 4]})
        assert out == "xyzx"

    @pytest.mark.parametrize(
        "src, ctx, expected",
        [
            (
                "{% for i in test %}{% cycle 'a' 'b' %}{% resetcycle %}{% endfor %}",
                {"test": list(range(5))},
                "aaaaa",
            ),
            (
                "{% cycle 'a' 'b' 'c' as abc %}{% for i in test %}{% cycle abc %}{% cycle '-' '+' %}{% resetcycle %}{% endfor %}",
                {"test": list(range(5))},
                "ab-c-a-b-c-",
            ),
            (
                "{% cycle 'a' 'b' 'c' as abc %}{% for i in test %}{% resetcycle abc %}{% cycle abc %}{% cycle '-' '+' %}{% endfor %}",
                {"test": list(range(5))},
                "aa-a+a-a+a-",
            ),
            (
                "{% for i in outer %}{% for j in inner %}{% cycle 'a' 'b' %}{% endfor %}{% resetcycle %}{% endfor %}",
                {"outer": list(range(2)), "inner": list(range(3))},
                "abaaba",
            ),
            (
                "{% for i in outer %}{% cycle 'a' 'b' %}{% for j in inner %}{% cycle 'X' 'Y' %}{% endfor %}{% resetcycle %}{% endfor %}",
                {"outer": list(range(2)), "inner": list(range(3))},
                "aXYXbXYX",
            ),
            (
                "{% for i in test %}{% cycle 'X' 'Y' 'Z' as XYZ %}{% cycle 'a' 'b' 'c' as abc %}{% if i == 1 %}{% resetcycle abc %}{% endif %}{% endfor %}",
                {"test": list(range(5))},
                "XaYbZaXbYc",
            ),
            (
                "{% for i in test %}{% cycle 'X' 'Y' 'Z' as XYZ %}{% cycle 'a' 'b' 'c' as abc %}{% if i == 1 %}{% resetcycle XYZ %}{% endif %}{% endfor %}",
                {"test": list(range(5))},
                "XaYbXcYaZb",
            ),
        ],
        ids=[
            "resetcycle05",
            "resetcycle06",
            "resetcycle07",
            "resetcycle08",
            "resetcycle09",
            "resetcycle10",
            "resetcycle11",
        ],
    )
    def test_the_resetcycle_cells(self, src, ctx, expected):
        assert assert_both_entries_agree(src, ctx) == expected

    def test_two_cycles_outside_a_loop_are_two_nodes(self):
        """The pre-#2556 per-iteration counter rendered `aa`."""
        assert assert_both_entries_agree("{% cycle 'a' 'b' %}{% cycle 'a' 'b' %}", {}) == "aa"
        assert assert_both_entries_agree("{% cycle 'a' 'b' as x %}{% cycle x %}", {}) == "ab"

    def test_as_name_advances_once_and_binds_the_emitted_value(self):
        """One advance per render of the node: the emitted value IS the bound
        value (`aa`, never `ab`)."""
        assert assert_both_entries_agree("{% cycle 'a' 'b' as x %}{{ x }}", {}) == "aa"
        assert (
            assert_both_entries_agree("{% cycle 'a' 'b' as x %}{{ x }}{% cycle x %}{{ x }}", {})
            == "aabb"
        )

    def test_a_reference_shares_the_definitions_silent_flag_and_iterator(self):
        assert (
            assert_both_entries_agree("{% cycle 'a' 'b' as x silent %}{% cycle x %}{{ x }}", {})
            == "b"
        )

    def test_nested_loops_keep_one_iterator_per_node(self):
        src = "{% for x in outer %}{% cycle 'A' 'B' %}{% for y in inner %}{% cycle '1' '2' '3' %}{% endfor %}{% endfor %}"
        assert assert_both_entries_agree(src, {"outer": [1, 2], "inner": [1, 2]}) == "A12B31"

    def test_each_render_starts_fresh(self):
        """Per RENDER: the second render of the same template starts at the
        first value again, on both entries."""
        src = "{% cycle 'a' 'b' 'c' %}"
        template = _BACKEND.from_string(src)
        assert template.render(context={}) == "a"
        assert template.render(context={}) == "a"
        assert liveview_render(src) == "a"
        assert liveview_render(src) == "a"

    @pytest.mark.parametrize(
        "src, message",
        [
            ("{% cycle a %}", "No named cycles in template. 'a' is not defined"),
            ("{% cycle %}", "'cycle' tag requires at least two arguments"),
            (
                "{% cycle 'a' 'b' 'c' as foo invalid_flag %}",
                "Only 'silent' flag is allowed after cycle's name, not 'invalid_flag'.",
            ),
            (
                "{% cycle 'a' 'b' as x %}{% cycle undefined %}",
                "Named cycle 'undefined' does not exist",
            ),
            ("{% resetcycle %}", "No cycles in template."),
            ("{% resetcycle undefinedcycle %}", "Named cycle 'undefinedcycle' does not exist."),
            (
                "{% cycle 'a' 'b' %}{% resetcycle undefinedcycle %}",
                "Named cycle 'undefinedcycle' does not exist.",
            ),
            (
                "{% cycle 'a' 'b' as ab %}{% resetcycle undefinedcycle %}",
                "Named cycle 'undefinedcycle' does not exist.",
            ),
        ],
        ids=[
            "cycle01",
            "cycle05",
            "cycle18",
            "cycle_undefined",
            "resetcycle01",
            "resetcycle02",
            "resetcycle03",
            "resetcycle04",
        ],
    )
    def test_the_parse_errors_carry_djangos_text(self, src, message):
        assert_refused(src, {}, message)

    def test_a_reference_does_not_become_the_resetcycle_target(self):
        """Django's `cycle()` returns a reference BEFORE `_last_cycle_node =
        node`, so a bare `{% resetcycle %}` after `{% cycle abc %}` resets the
        last DEFINITION."""
        src = "{% cycle 'x' 'y' as abc %}{% cycle '1' '2' %}{% cycle abc %}{% resetcycle %}{% cycle '1' '2' %}{% cycle abc %}"
        assert assert_both_entries_agree(src, {}) == "x1y1x"

    def test_randomized_cycle_programs_agree_with_django(self):
        """Sequences of cycle / cycle-as / cycle-as-silent / reference /
        resetcycle inside 0–2 nested loops with `{% if %}` gates."""
        rng = random.Random(2556)
        ctx = {"outer": [0, 1, 2], "inner": [0, 1], "a": "<", "b": "y"}

        def program() -> str:
            names: list[str] = []
            ops: list[str] = []
            for _ in range(rng.randint(2, 7)):
                kind = rng.choice(
                    ["plain", "as", "as_silent", "ref", "reset", "reset_named", "text"]
                )
                if kind == "plain":
                    ops.append("{% cycle 'a' 'b' 'c' %}")
                elif kind in ("as", "as_silent"):
                    name = f"n{len(names)}"
                    names.append(name)
                    tail = " silent" if kind == "as_silent" else ""
                    ops.append(f"{{% cycle a b '1' as {name}{tail} %}}{{{{ {name} }}}}")
                elif kind == "ref" and names:
                    ops.append(f"{{% cycle {rng.choice(names)} %}}")
                elif kind == "reset" and ops:
                    ops.append("{% resetcycle %}")
                elif kind == "reset_named" and names:
                    ops.append(f"{{% resetcycle {rng.choice(names)} %}}")
                else:
                    ops.append(".")
            body = "".join(ops)
            depth = rng.randint(0, 2)
            if depth >= 1:
                gate = (
                    rng.choice(["", "{% if i == 1 %}{% resetcycle %}{% endif %}"])
                    if "cycle" in body
                    else ""
                )
                body = f"{{% for i in outer %}}{body}{gate}{{% endfor %}}"
            if depth == 2:
                body = f"{{% for j in inner %}}{body}{{% endfor %}}"
            return body

        checked = 0
        for _ in range(300):
            src = program()
            try:
                expected = django_render(src, ctx)
            except TemplateSyntaxError as exc:
                # Both engines refuse — with the same message.
                with pytest.raises(Exception, match=re.escape(str(exc))):
                    backend_render(src, ctx)
                continue
            assert backend_render(src, ctx) == expected, src
            checked += 1
        assert checked >= 150


# =========================================================================== #
# D — {% lorem %} and {% debug %}
# =========================================================================== #


class TestLoremTag:
    @pytest.mark.parametrize(
        "src, check",
        [
            ("{% lorem 3 w %}", lambda out: out == "lorem ipsum dolor"),
            ("{% lorem %}", lambda out: out == COMMON_P),
            (
                "{% lorem 3 w random %}",
                lambda out: len(out.split(" ")) == 3 and all(w in WORDS for w in out.split(" ")),
            ),
            ("{% lorem 2 p %}", lambda out: out.count("<p>") == 2),
            ("{% lorem two p %}", lambda out: out.count("<p>") == 1),
        ],
        ids=[
            "lorem1",
            "lorem_default",
            "lorem_random",
            "lorem_multiple_paragraphs",
            "lorem_incorrect_count",
        ],
    )
    def test_the_suite_cells(self, src, check):
        assert check(backend_render(src, {}))
        assert check(liveview_render(src, {}))

    def test_lorem_syntax_carries_djangos_message(self):
        assert_refused("{% lorem 1 2 3 4 %}", {}, "Incorrect format for 'lorem' tag")

    def test_seeded_differential_is_byte_exact(self):
        """Both engines consume the same global `random` stream: seed it the
        same way on each side and the bytes agree, `random` or not."""
        cases = 0
        for seed in range(6):
            for count in ("0", "1", "2", "19", "20", "25", "n"):
                for method in ("", "w", "p", "b"):
                    for flag in ("", "random"):
                        src = "{% lorem " + " ".join(t for t in (count, method, flag) if t) + " %}"
                        ctx = {"n": 3}
                        random.seed(seed)
                        expected = django_render(src, ctx)
                        random.seed(seed)
                        assert backend_render(src, ctx) == expected, (seed, src)
                        cases += 1
        assert cases >= 100

    def test_the_count_resolves_through_djangos_compiler(self):
        random.seed(1)
        expected = django_render("{% lorem n|add:1 w %}", {"n": 2})
        random.seed(1)
        assert backend_render("{% lorem n|add:1 w %}", {"n": 2}) == expected == "lorem ipsum dolor"


class TestDebugTag:
    def test_debug_false_renders_nothing(self, settings):
        settings.DEBUG = False
        assert backend_render("{% debug %}", {"a": 1}) == ""
        assert liveview_render("{% debug %}", {"a": 1}) == ""

    def test_plain_and_script_cells(self, settings):
        settings.DEBUG = True
        out = backend_render("{% debug %}", {"a": 1})
        assert out.startswith(
            "{&#x27;a&#x27;: 1}{&#x27;False&#x27;: False, &#x27;None&#x27;: None, &#x27;True&#x27;: True}\n\n{"
        )
        out = backend_render("{% debug %}", {"frag": "<script>"})
        assert out.startswith("{&#x27;frag&#x27;: &#x27;&lt;script&gt;&#x27;}")
        assert "&#x27;django&#x27;: &lt;module &#x27;django&#x27; " in out

    def test_the_serialization_floor_sits_in_front_of_the_dump(self, settings):
        """#1867 falsification: a `User` with a password hash in context,
        DEBUG=True — the hash is NOT in the dump, on either entry. Django's
        own `{% debug %}` prints the model repr; djust's prints what the
        floor lets through."""
        settings.DEBUG = True
        secret = "pbkdf2_sha256$TOPSECRETHASH"
        user = User(username="alice", password=secret, is_superuser=True)
        for out in (
            backend_render("{% debug %}", {"user": user}),
            liveview_render("{% debug %}", {"user": user}),
        ):
            assert "alice" in out
            assert secret not in out
            assert "TOPSECRET" not in out

    def test_private_view_attrs_are_absent_on_the_liveview_entry(self, settings):
        settings.DEBUG = True
        from django.contrib.sessions.middleware import SessionMiddleware

        from djust.live_view import LiveView

        class _V(LiveView):
            def mount(self, request, **kwargs):
                self.public_thing = "PUBLIC_MARK"
                self._secret_thing = "PRIVATE_MARK"

        _V.template = "<div dj-root>inner</div>"
        _V._full_template = (
            "<html><body><div dj-root>inner</div><nav>{% debug %}</nav></body></html>"
        )
        request = _FACTORY.get("/")
        SessionMiddleware(lambda r: r).process_request(request)
        request.session.save()
        view = _V()
        view.setup(request)
        view.mount(request)
        html = view.render_full_template(request, serialized_context=view.get_context_data())
        assert "PUBLIC_MARK" in html
        assert "PRIVATE_MARK" not in html


# =========================================================================== #
# E — {% querystring %}
# =========================================================================== #

_QS = pytest.mark.skipif(django.VERSION < (5, 1), reason="{% querystring %} is Django 5.1+")


def _assert_querystring_agrees(src: str, query: str, ctx: Optional[Dict[str, Any]] = None) -> str:
    request = _FACTORY.get("/?" + query if query else "/")
    expected = django_render(src, ctx, request=request)
    assert backend_render(src, ctx, request=request) == expected, (src, query)
    assert backend_render(src, ctx, request=request, request_context=True) == expected, (src, query)
    return expected


@_QS
class TestQuerystringTag:
    @pytest.mark.parametrize(
        "src, query, ctx, expected",
        [
            ("{% querystring %}", "", {}, ""),
            ("{% querystring a=None %}", "a=b", {}, "?"),
            ("{% querystring a=None %}", "", {}, ""),
            ("{% querystring %}", "a=b", {}, "?a=b"),
            ("{% querystring %}", "x=y&a=b", {}, "?x=y&amp;a=b"),
            ("{% querystring qd %}", "", {"qd": None}, ""),
            ("{% querystring qd %}", "", {"qd": {}}, ""),
            ("{% querystring qd %}", "", {"qd": QueryDict()}, ""),
            ("{% querystring a=1 %}", "x=y&a=b", {}, "?x=y&amp;a=1"),
            ("{% querystring test_new='something' %}", "a=b", {}, "?a=b&amp;test_new=something"),
            ("{% querystring test=None a=1 %}", "test=value&a=1", {}, "?a=1"),
            ("{% querystring nonexistent=None a=1 %}", "x=y&a=1", {}, "?x=y&amp;a=1"),
            ("{% querystring a=my_list %}", "", {"my_list": [2, 3]}, "?a=2&amp;a=3"),
            (
                "{% querystring a=my_dict %}",
                "",
                {"my_dict": {i: i * 2 for i in range(3)}},
                "?a=0&amp;a=1&amp;a=2",
            ),
        ],
        ids=[
            "empty_get_params",
            "remove_all_params",
            "remove_all_params_empty",
            "non_empty_get_params",
            "multiple",
            "empty_params_None",
            "empty_params_dict",
            "empty_params_QueryDict",
            "replace",
            "add",
            "remove",
            "remove_nonexistent",
            "add_list",
            "add_dict",
        ],
    )
    def test_the_request_context_cells(self, src, query, ctx, expected):
        assert _assert_querystring_agrees(src, query, ctx) == expected

    def test_the_explicit_query_dict_cells(self):
        request = _FACTORY.get("/", {"a": 1})
        src = "{% querystring request.GET a=2 %}"
        assert (
            backend_render(src, {"request": request})
            == django_render(src, {"request": request})
            == "?a=2"
        )
        src = "{% querystring my_query_dict a=2 %}"
        ctx = {"my_query_dict": QueryDict("a=1&b=2")}
        assert backend_render(src, ctx) == django_render(src, ctx) == "?a=2&amp;b=2"

    def test_without_a_request_djangos_own_attribute_error_surfaces(self):
        with pytest.raises(AttributeError, match="'Context' object has no attribute 'request'"):
            django_render("{% querystring %}", {})
        with pytest.raises(Exception, match="'Context' object has no attribute 'request'"):
            backend_render("{% querystring %}", {})

    def test_the_liveview_ws_path_reads_the_mount_time_get(self):
        """The WS/render path (`_sync_state_to_rust`) carries `request` in the
        raw-value sidecar (#1145), so `{% querystring %}` reads the mount-time
        GET — Django's own semantics within one request."""
        from djust import LiveView
        from djust.testing import LiveViewTestClient

        class _V(LiveView):
            def mount(self, request, **kwargs):
                self.x = 1

        _V.template = "<div dj-root>{% querystring a=2 %}</div>"
        client = LiveViewTestClient(_V)
        client.mount()
        client.view_instance.request = _FACTORY.get("/?a=1&b=x")
        html, _, _ = client.render_with_patches()
        assert "?a=2&amp;b=x" in html, html

    def test_as_var_is_refused_rather_than_mis_rendered(self):
        request = _FACTORY.get("/?a=1")
        assert (
            django_render("{% querystring a=2 as qs %}[{{ qs }}]", {}, request=request) == "[?a=2]"
        )
        with pytest.raises(Exception, match="#2547"):
            backend_render("{% querystring a=2 as qs %}[{{ qs }}]", {}, request=request)

    def test_kwargs_resolve_through_djangos_compiler(self):
        src = "{% querystring page=page_obj.number|add:1 %}"
        assert (
            _assert_querystring_agrees(src, "q=x", {"page_obj": {"number": 3}}) == "?q=x&amp;page=4"
        )

    def test_randomized_query_shapes_and_encoded_variants(self):
        """≥200 random `QueryDict`s × add / replace / remove / list kwargs,
        including the #1825 encoded forms fed END-TO-END: `%2e`, `%2f`, `+`,
        `%26`, `<`, `"`, unicode, repeated keys, empty values."""
        rng = random.Random(2556)
        keys = [
            "a",
            "b",
            "q",
            "page",
            "x y",
            "k%2e",
            "k%2f",
            "%26",
            "ü",
            "e",
            "lt<",
            'qu"ot',
            "plus+",
        ]
        values = [
            "",
            "1",
            "a b",
            "a+b",
            "%2e%2e",
            "%2f",
            "x&y",
            "<b>",
            '"',
            "ü",
            "1;2",
            "%26",
            "%3C",
        ]
        for _ in range(220):
            pairs = [f"{rng.choice(keys)}={rng.choice(values)}" for _ in range(rng.randint(0, 6))]
            query = "&".join(pairs)
            kwargs = []
            ctx: Dict[str, Any] = {"lst": ["p", "q r", "<"], "num": 7, "text": "a&b"}
            for _ in range(rng.randint(0, 3)):
                kind = rng.choice(["remove", "set_literal", "set_var", "set_list", "set_num"])
                key = rng.choice(["a", "b", "q", "page", "new", "e"])
                if kind == "remove":
                    kwargs.append(f"{key}=None")
                elif kind == "set_literal":
                    kwargs.append(f"{key}='{rng.choice(['v', 'a b', 'x&y', '<i>', 'ü'])}'")
                elif kind == "set_var":
                    kwargs.append(f"{key}=text")
                elif kind == "set_list":
                    kwargs.append(f"{key}=lst")
                else:
                    kwargs.append(f"{key}=num")
            src = "{% querystring " + " ".join(kwargs) + " %}"
            try:
                _assert_querystring_agrees(src, query, ctx)
            except TemplateSyntaxError as exc:
                # A repeated kwarg: Django's `parse_bits` refuses, and so does
                # the handler — it IS `parse_bits`. Same message.
                assert "received multiple values for keyword argument" in str(exc)
                request = _FACTORY.get("/?" + query if query else "/")
                with pytest.raises(Exception, match=re.escape(str(exc))):
                    backend_render(src, ctx, request=request)


class TestQueryDictLookupsKeepDjangosLastValue:
    """A `QueryDict` context VARIABLE resolves like Django's on both entries.

    The multi-value storage rides only the sidecar (for `{% querystring %}`);
    the `Value` the extractor sees is `dict(qd.items())` — last value per
    key, `QueryDict.__getitem__`'s rule. Stage 11 of PR #2596 measured the
    raw pass-through rendering `['3']` / `['1', '2']` / `''` / a false
    `{% if %}` for the four cells below.
    """

    _CELLS = (
        ("{{ qd.page }}", "3"),
        ("{{ qd.a }}", "2"),
        ("{{ qd.page|add:1 }}", "4"),
        ("{% if qd.page == '3' %}yes{% else %}no{% endif %}", "yes"),
        ("{% for k in qd %}{{ k }};{% endfor %}", "a;page;"),
        ("{{ qd.missing }}", ""),
    )

    @pytest.mark.parametrize("src, expected", _CELLS, ids=[c[0] for c in _CELLS])
    def test_the_lookup_cells_on_both_entries(self, src, expected):
        ctx = {"qd": _FACTORY.get("/?a=1&a=2&page=3").GET}
        assert assert_both_entries_agree(src, ctx) == expected

    @pytest.mark.parametrize(
        "src, expected",
        [
            ("{{ request.GET.page }}", "3"),
            ("{{ request.GET.a }}", "2"),
            ("{{ request.GET.page|add:1 }}", "4"),
        ],
    )
    def test_request_get_lookups_reach_the_query_dict_through_the_request(self, src, expected):
        """`request.GET` is reached through the sidecar's getattr walk, so
        the raw `QueryDict` meets the converter directly — the same
        last-value rule. Was `['3']` on the backend before the converter
        arm. The page-shell LiveView entry carries no `request` (#2589), so
        the LiveView side pinned here is the WS/render path."""
        from djust import LiveView
        from djust.testing import LiveViewTestClient

        request = _FACTORY.get("/?a=1&a=2&page=3")
        assert django_render(src, {}, request=request) == expected
        assert backend_render(src, {}, request=request) == expected
        assert backend_render(src, {}, request=request, request_context=True) == expected

        class _V(LiveView):
            def mount(self, request, **kwargs):
                self.x = 1

        _V.template = f"<div dj-root>[{src}]</div>"
        client = LiveViewTestClient(_V)
        client.mount()
        client.view_instance.request = _FACTORY.get("/?a=1&a=2&page=3")
        html, _, _ = client.render_with_patches()
        assert f"[{expected}]" in html, html

    def test_normalize_django_value_yields_a_json_native_last_value_dict(self):
        """The LiveView state boundary (session / signed snapshot) takes
        `normalize_django_value`'s output through `json.dumps`, which walks a
        `dict` subclass's STORAGE — a raw `QueryDict` would persist as lists
        and restore as `{{ qd.a }}` = `['1', '2']`. So the normalized value
        is a plain, JSON-native, last-value dict; the converter arm that
        reads a raw object the same way is for the paths that carry one."""
        import json

        from djust.serialization import normalize_django_value

        out = normalize_django_value(QueryDict("a=1&a=2&page=3"))
        assert type(out) is dict
        assert out == {"a": "2", "page": "3"}
        assert json.loads(json.dumps(out)) == {"a": "2", "page": "3"}

    def test_a_multi_value_dict_that_is_not_a_query_dict(self):
        from django.utils.datastructures import MultiValueDict

        ctx = {"mvd": MultiValueDict({"x": ["p", "q"], "y": ["r"]})}
        assert assert_both_entries_agree("{{ mvd.x }}-{{ mvd.y }}", ctx) == "q-r"

    @_QS
    def test_the_same_variable_still_feeds_querystring_with_every_value(self):
        """One context, both sinks: the lookup sees the last value, the tag
        sees the whole list — on the plain backend AND the LiveView WS/render
        path (`_sync_state_to_rust`, whose sidecar now carries the raw
        `QueryDict`). The full-template shell entry (`render_full_template`'s
        `temp_rust`) wires no sidecar at all — pre-existing, noted in
        `mixins/template.py` — so it is not the LiveView entry pinned here."""
        from djust import LiveView
        from djust.testing import LiveViewTestClient

        ctx = {"qd": QueryDict("a=1&a=2&page=3")}
        src = "{{ qd.a }}|{% querystring qd page=4 %}"
        expected = django_render(src, ctx)
        assert expected == "2|?a=1&amp;a=2&amp;page=4"
        assert backend_render(src, ctx) == expected

        class _V(LiveView):
            def mount(self, request, **kwargs):
                self.qd = QueryDict("a=1&a=2&page=3")

        _V.template = f"<div dj-root>{src}</div>"
        client = LiveViewTestClient(_V)
        client.mount()
        html, _, _ = client.render_with_patches()
        assert expected in html, html


class TestQuerystringVersionGate:
    def test_registration_follows_django_version(self):
        from djust import _rust

        registered = "querystring" in set(_rust.get_registered_tags())
        assert registered == (django.VERSION >= (5, 1))


# =========================================================================== #
# The children-walker count pin (#1125 / plan §6.3)
# =========================================================================== #


class TestEveryChildrenWalkerKnowsTheNewBlockNode:
    def test_filter_joins_every_walker_spaceless_is_in(self):
        """`Node::Spaceless` is the simplest existing block node; every match
        arm that recurses into its children must recurse into `Node::Filter`
        too, or a `{% block %}` / `{% if %}` / dep inside a `{% filter %}` is
        silently missed."""
        import pathlib

        src_dir = pathlib.Path(__file__).resolve().parents[2] / "crates" / "djust_templates" / "src"
        per_file = {}
        for path in src_dir.glob("*.rs"):
            text = path.read_text(encoding="utf-8")
            spaceless = len(re.findall(r"Node::Spaceless \{", text))
            filter_ = len(re.findall(r"Node::Filter \{", text))
            if spaceless or filter_:
                per_file[path.name] = (spaceless, filter_)
        # The four files that walk children today; a fifth walker would show
        # up here as a file with Spaceless and no Filter.
        assert set(per_file) == {"parser.rs", "renderer.rs", "inheritance.rs", "loop_cache.rs"}, (
            per_file
        )
        for name, (spaceless, filter_) in per_file.items():
            assert spaceless >= 1, (name, per_file)
            # `>=`, not `==`: `Filter` also joins two inheritance walkers
            # (`apply_block_overrides`, `extract_blocks_recursive`) and the
            # cycle-resolution pass, which `Spaceless` predates.
            assert filter_ >= spaceless, (name, per_file)
        assert sum(f for _, f in per_file.values()) >= 12, per_file


# =========================================================================== #
# The serialization floor inside a MultiValueDict (#2596 re-review)
# =========================================================================== #


class SecretiveUser(User):
    """A model carrying all three shapes the serialization floor governs.

    * ``password`` — a floor field (``_ALWAYS_EXCLUDED_FIELDS``);
    * ``_secret`` — an underscore name the sidecar proxy refuses outright
      (Django-parity: template resolution never touches ``_``-names);
    * ``session_digest`` — a ``@property`` returning a hash, denied by the
      model's own ``djust_exclude_fields``. A property, not a method, on
      purpose: the floor has to hold for a descriptor too, and it must hold on
      BOTH channels — the eager serializer's property pass and the sidecar
      proxy's ``__getattr__`` consult the same denylist.

    A proxy model, so it needs no table and no migration.
    """

    _secret = "instance-only-marker"
    djust_exclude_fields = ("session_digest",)

    class Meta:
        proxy = True
        app_label = "auth"

    @property
    def session_digest(self) -> str:
        return "hmac$" + self.password


#: What the probe tag handler was handed on the last render. A REAL registered
#: handler is the only faithful harness here: the tag bridge gives a handler
#: the sidecar's raw Python objects WHOLESALE, which is the sink the floor has
#: to hold at — the ``{{ x.y }}`` walk re-protects per segment and would report
#: safe either way (#1650 reproduction fidelity).
_FLOOR_PROBE_SEEN: Dict[str, Any] = {}


def _register_floor_probe() -> None:
    from djust.template_tags import TagHandler, register

    @register("djust_floor_probe_2556")
    class _FloorProbeTag(TagHandler):
        def render(self, args, context):
            _FLOOR_PROBE_SEEN.clear()
            _FLOOR_PROBE_SEEN.update(context)
            return ""


_register_floor_probe()


def _secretive_user() -> "SecretiveUser":
    user = SecretiveUser(pk=1, username="alice")
    user.set_password("hunter2")
    assert user.password.startswith("pbkdf2"), user.password
    return user


def _probe_backend(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """The plain ``DjustTemplateBackend`` entry."""
    _FLOOR_PROBE_SEEN.clear()
    backend_render("{% djust_floor_probe_2556 %}", ctx)
    return dict(_FLOOR_PROBE_SEEN)


def _probe_liveview(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """The LiveView entry that carries a sidecar — ``_sync_state_to_rust``.

    NOT ``liveview_render`` above: that is ``render_full_template``'s page
    shell, which wires no sidecar at all (#2513), so it could never show the
    difference this test is about.
    """
    from djust import LiveView
    from djust.testing import LiveViewTestClient

    values = dict(ctx)

    class _V(LiveView):
        def mount(self, request, **kwargs):
            for key, value in values.items():
                setattr(self, key, value)

    _V.template = "<div dj-root>{% djust_floor_probe_2556 %}</div>"
    client = LiveViewTestClient(_V)
    client.mount()
    _FLOOR_PROBE_SEEN.clear()
    client.render_with_patches()
    return dict(_FLOOR_PROBE_SEEN)


#: ``box`` holds the user in a container the handler reaches by hand. The
#: plain-dict row is the CONTROL — the shape the floor already protected — and
#: every ``MultiValueDict`` row must answer identically.
_FLOOR_SHAPES = {
    "plain-dict": (lambda u: {"u": u}, lambda box: box["u"]),
    "mvd-top": (lambda u: MultiValueDict({"u": [u]}), lambda box: box["u"]),
    "mvd-in-dict": (lambda u: {"q": MultiValueDict({"u": [u]})}, lambda box: box["q"]["u"]),
    "mvd-in-list": (lambda u: [MultiValueDict({"u": [u]})], lambda box: box[0]["u"]),
}

#: The three names the floor governs on :class:`SecretiveUser`.
_FLOOR_NAMES = ("password", "_secret", "session_digest")


def _floor_verdict(reached: Any) -> Dict[str, str]:
    """How each governed name answers, whatever channel produced ``reached``.

    A handler can be handed a model through the sidecar (then the answer is a
    ``_SidecarModelProxy`` and a governed name RAISES) or through the eager
    serializer's floor-filtered dict (then the name is simply ABSENT). Both are
    the floor holding; a live ``Model`` whose attribute returns the value is
    not. Recording the verdict per name rather than asserting one channel keeps
    the row honest on an entry that answers through the other one.
    """
    verdict = {}
    for name in _FLOOR_NAMES:
        if isinstance(reached, dict):
            verdict[name] = "absent" if name not in reached else "LEAK:%r" % (reached[name],)
            continue
        try:
            value = getattr(reached, name)
        except AttributeError:
            verdict[name] = "refused"
        else:
            verdict[name] = "LEAK:%r" % (value,)
    return verdict


@pytest.mark.parametrize("probe", [_probe_backend, _probe_liveview], ids=["backend", "liveview"])
class TestTheFloorHoldsInsideAMultiValueDict:
    """#2596 re-review 🔴: ``_protect_sidecar_tree`` handed a ``MultiValueDict``
    to a tag handler RAW, so a model inside one arrived live.

    Measured before the fix, through the handler below:
    ``{"box": {"u": user}}`` gave a ``_SidecarModelProxy`` and ``.password``
    raised; ``{"box": MultiValueDict({"u": [user]})}`` gave the ``User`` itself
    and ``.password`` was the hash. Introduced and fixed inside this PR.

    The two entries reach the handler through different channels for different
    shapes, which is why the security row below asserts the VERDICT rather than
    one channel's shape: the LiveView entry's eager serializer already answers a
    JSON-friendly plain ``dict`` of models with a floor-filtered dict, while a
    ``MultiValueDict`` is not JSON-friendly and rides the sidecar. Both are the
    floor holding. On the plain backend the sidecar answers every shape, so the
    stricter "identical to the plain-dict control" assertion lives there.
    """

    @pytest.mark.parametrize("shape", list(_FLOOR_SHAPES), ids=list(_FLOOR_SHAPES))
    def test_no_governed_name_is_reachable_on_any_shape(self, probe, shape):
        """The security invariant, on every entry × shape.

        ``LEAK:`` is what the pre-fix ``MultiValueDict`` rows reported, and it
        is the only verdict that fails here — refused (a proxy raised) and
        absent (the eager serializer's floor dropped it) are both the floor.
        """
        build, reach = _FLOOR_SHAPES[shape]
        reached = reach(probe({"box": build(_secretive_user())})["box"])
        assert not isinstance(reached, models.Model), "a live model reached the handler: %r" % (
            reached,
        )
        verdict = _floor_verdict(reached)
        assert all(v in ("refused", "absent") for v in verdict.values()), (shape, verdict)

    @pytest.mark.parametrize("shape", list(_FLOOR_SHAPES), ids=list(_FLOOR_SHAPES))
    def test_the_answer_matches_the_plain_dict_control_where_one_channel_answers(
        self, probe, shape
    ):
        """The stricter row: same TYPE and same per-name verdict as a plain dict.

        Skipped on an entry/shape pair whose two containers legitimately take
        different channels (see the class docstring) — asserting equality there
        would pin the eager/sidecar split rather than the floor. The skip is
        keyed on the observed channel, not on a hard-coded entry name, so a
        future change that routes a shape differently re-arms the assertion
        rather than silently keeping it skipped.
        """
        control = _FLOOR_SHAPES["plain-dict"][1](
            probe({"box": _FLOOR_SHAPES["plain-dict"][0](_secretive_user())})["box"]
        )
        reached = _FLOOR_SHAPES[shape][1](
            probe({"box": _FLOOR_SHAPES[shape][0](_secretive_user())})["box"]
        )
        if isinstance(control, dict) != isinstance(reached, dict):
            pytest.skip(
                "different channels: control=%s, %s=%s"
                % (type(control).__name__, shape, type(reached).__name__)
            )
        assert type(reached) is type(control), (type(reached), type(control))
        assert _floor_verdict(reached) == _floor_verdict(control)


class TestTheBackendEntryHandsOverAProxy:
    """Non-vacuity for the rows above: on the plain backend the sidecar IS the
    channel for all four shapes, so ``_SidecarModelProxy`` — not merely "not a
    live model", and not the eager serializer's dict — is what every one of
    them must produce. Without this, a change that stopped attaching the
    sidecar entirely would leave the verdict rows green."""

    @pytest.mark.parametrize("shape", list(_FLOOR_SHAPES), ids=list(_FLOOR_SHAPES))
    def test_every_shape_arrives_as_a_sidecar_proxy(self, shape):
        from djust.serialization import _SidecarModelProxy

        build, reach = _FLOOR_SHAPES[shape]
        reached = reach(_probe_backend({"box": build(_secretive_user())})["box"])
        assert isinstance(reached, _SidecarModelProxy), (shape, type(reached))


class TestProtectingAMultiValueDictKeepsItsType:
    """The second mechanism: the floor rebuilds THROUGH the container's type.

    Rebuilding as a plain ``dict`` would keep the floor and break
    ``{% querystring %}`` — which calls ``.copy()`` / ``.setlist()`` /
    ``.urlencode()`` — and would collapse every repeated key. These rows go red
    for that mistake alone, and stay green for the floor mistake above.
    """

    def test_a_query_dict_with_nothing_to_protect_is_returned_untouched(self):
        """Every real request ``QueryDict`` is this row: strings, no model.

        Identity, not just equality — a rebuild would cost a copy per render on
        the hot path, and would silently make an immutable ``request.GET``
        writable.
        """
        from djust.serialization import build_render_sidecar

        qd = _FACTORY.get("/?a=1&a=2&page=3").GET
        assert build_render_sidecar({"qd": qd})["qd"] is qd

    def test_a_query_dict_that_carried_a_model_is_still_a_query_dict(self):
        from djust.serialization import _SidecarModelProxy, build_render_sidecar

        qd = QueryDict("a=1&a=2&page=3", mutable=True)
        qd.setlist("u", [_secretive_user()])
        out = build_render_sidecar({"qd": qd})["qd"]

        assert isinstance(out, QueryDict), type(out)
        assert out is not qd, "the protected copy must not be the caller's own object"
        assert isinstance(out["u"], _SidecarModelProxy), type(out["u"])
        with pytest.raises(AttributeError):
            out["u"].password
        # Repeated keys survive: the plain-`dict` rebuild would keep only "2".
        assert out.getlist("a") == ["1", "2"]
        assert out.urlencode().startswith("a=1&a=2&page=3&u=")

    def test_the_rebuilt_copy_keeps_the_originals_mutability(self):
        """``QueryDict.copy()`` always returns a MUTABLE copy, so a rebuilt
        ``request.GET`` would start accepting writes Django refuses."""
        from djust.serialization import build_render_sidecar

        frozen = QueryDict("a=1", mutable=True)
        frozen.setlist("u", [_secretive_user()])
        frozen._mutable = False
        out = build_render_sidecar({"qd": frozen})["qd"]
        with pytest.raises(AttributeError):
            out["page"] = "9"

    @_QS
    def test_querystring_still_renders_from_a_protected_query_dict(self):
        """The tag runs on the object the floor handed over, on both entries."""
        from djust import LiveView
        from djust.testing import LiveViewTestClient

        def _qd():
            qd = QueryDict("a=1&a=2&page=3", mutable=True)
            qd.setlist("u", [_secretive_user()])
            return qd

        src = "{% querystring qd page=4 %}"
        expected = django_render(src, {"qd": _qd()})
        assert "page=4" in expected and "a=1&amp;a=2" in expected, expected
        assert backend_render(src, {"qd": _qd()}) == expected

        class _V(LiveView):
            def mount(self, request, **kwargs):
                self.qd = _qd()

        _V.template = "<div dj-root>%s</div>" % src
        client = LiveViewTestClient(_V)
        client.mount()
        html, _, _ = client.render_with_patches()
        assert expected in html, html

    def test_a_multi_value_dict_whose_lists_cannot_be_read_is_not_downgraded(self):
        """A subclass with a hostile ``lists()`` keeps its type rather than
        being rebuilt as a plain ``dict`` — the floor declines, it does not
        silently destroy the container it cannot walk."""
        from djust.serialization import build_render_sidecar

        class _Hostile(MultiValueDict):
            def lists(self):
                raise RuntimeError("no")

        hostile = _Hostile({"a": ["1"]})
        assert build_render_sidecar({"h": hostile})["h"] is hostile
