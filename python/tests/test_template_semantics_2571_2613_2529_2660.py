"""Django-vs-djust parity for #2571, #2613, #2529 and #2660.

Every parity case renders the SAME template + context through Django's own
engine and through ``DjustTemplateBackend`` and asserts the two agree byte for
byte, so a row here is a claim about Django measured rather than remembered.

* #2571 — a LITERAL numeric fallback (``default_if_none:0``, ``default:0.0``)
  crosses the filter dispatch as a typed value, so ``{% if %}`` sees Django's
  falsy ``0`` rather than the truthy text ``"0"``.
* #2613 — ``{% for %}`` over a one-shot iterator (a generator, ``iter(...)``,
  ``MultiValueDict.lists()``) consumes it once, as ``ForNode.render``'s
  ``list(values)`` does, instead of walking the repr character by character.
* #2529 — the dj-if marker's loop-path suffix is ``Context`` state the user
  namespace cannot reach; a context key of the old name is inert.
* #2660 — a template name whose ``..`` transiently leaves the search directory
  and lands back inside it loads (Django's ``safe_join`` normalises first);
  ``{% static var %}`` on the native node resolves the variable.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

pytest.importorskip("django")

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "python"))

TEMPLATE_DIR = Path(__file__).resolve().parent / "_semantics_2660_templates"
LIBRARIES = {"static": "django.templatetags.static"}


class _DjangoEngine:
    """`django.template.Engine` behind the backend-shaped `from_string`."""

    def __init__(self, dirs):
        from django.template import Engine

        self._engine = Engine(dirs=dirs, libraries=LIBRARIES)

    def from_string(self, source):
        from django.template import Context

        template = self._engine.from_string(source)

        class _T:
            @staticmethod
            def render(ctx):
                return template.render(Context(ctx))

        return _T


@pytest.fixture(scope="module")
def engines():
    from djust.template import DjustTemplateBackend

    return {
        "django": _DjangoEngine([str(TEMPLATE_DIR)]),
        "djust": DjustTemplateBackend(
            {
                "NAME": "djust",
                "DIRS": [str(TEMPLATE_DIR)],
                "APP_DIRS": False,
                "OPTIONS": {"libraries": LIBRARIES},
            }
        ),
    }


@pytest.fixture(scope="module", autouse=True)
def template_dir():
    TEMPLATE_DIR.mkdir(exist_ok=True)
    (TEMPLATE_DIR / "sub").mkdir(exist_ok=True)
    (TEMPLATE_DIR / "ok.html").write_text("OK")
    (TEMPLATE_DIR / "base.html").write_text("{% block c %}three{% endblock %}")
    yield TEMPLATE_DIR
    for p in (TEMPLATE_DIR / "ok.html", TEMPLATE_DIR / "base.html"):
        p.unlink(missing_ok=True)
    (TEMPLATE_DIR / "sub").rmdir()
    TEMPLATE_DIR.rmdir()


def render_both(engines, source: str, context_factory) -> tuple[str, str]:
    """Render on both engines with a FRESH context each — a one-shot iterator
    consumed by the first engine must not be handed, spent, to the second."""
    out = []
    for name in ("django", "djust"):
        out.append(engines[name].from_string(source).render(context_factory()))
    return out[0], out[1]


# --------------------------------------------------------------------- #2571


class TestLiteralFallbackIsTyped2571:
    @pytest.mark.parametrize(
        "literal",
        ["0", "0.0", "1", "1.5", "''", "None", "False", "True", "'0'", "-0", "1e0"],
    )
    def test_if_default_if_none_literal_agrees(self, engines, literal):
        src = "{% if x|default_if_none:" + literal + " %}yes{% else %}no{% endif %}"
        dj, dr = render_both(engines, src, dict)
        assert dj == dr, (literal, dj, dr)

    @pytest.mark.parametrize("literal", ["0", "0.0", "1", "''", "None", "False"])
    def test_if_default_literal_agrees(self, engines, literal):
        src = "{% if x|default:" + literal + " %}yes{% else %}no{% endif %}"
        dj, dr = render_both(engines, src, lambda: {"x": ""})
        assert dj == dr, (literal, dj, dr)

    def test_the_cited_cell_is_djangos_no(self, engines):
        dj, dr = render_both(
            engines, "{% if x|default_if_none:0 %}yes{% else %}no{% endif %}", dict
        )
        assert (dj, dr) == ("no", "no")

    @pytest.mark.parametrize(
        ("src", "ctx"),
        [
            ("{{ x|default_if_none:0 }}", lambda: {"x": None}),
            ("{{ x|default_if_none:0.0 }}", lambda: {"x": None}),
            ("{{ x|default:0 }}", lambda: {"x": ""}),
            ("{{ x|default_if_none:0 }}", dict),
            ("{{ x|default_if_none:0|add:1 }}", lambda: {"x": None}),
            ("{% firstof x|default_if_none:0 'z' %}", lambda: {"x": None}),
        ],
    )
    def test_emitted_literal_fallback_agrees(self, engines, src, ctx):
        dj, dr = render_both(engines, src, ctx)
        assert dj == dr, (src, dj, dr)


# --------------------------------------------------------------------- #2613


class _Obj:
    def gen(self):
        return iter(["a", "b"])

    def genexpr(self):
        return (x for x in [1, 2, 3])

    def pairs(self):
        return zip(["k1", "k2"], [1, 2])


def _qd():
    from django.http import QueryDict

    return QueryDict("a=1&a=2&page=3")


class TestForConsumesAOneShotIterator2613:
    @pytest.mark.parametrize(
        ("src", "ctx"),
        [
            ("{% for k in obj.gen %}{{ k }};{% endfor %}", lambda: {"obj": _Obj()}),
            ("{% for k in obj.genexpr %}{{ k }};{% endfor %}", lambda: {"obj": _Obj()}),
            ("{% for k, v in obj.pairs %}{{ k }}={{ v }};{% endfor %}", lambda: {"obj": _Obj()}),
            ("{% for k in it %}{{ k }};{% endfor %}", lambda: {"it": iter([1, 2, 3])}),
            ("{% for k in g %}{{ k }};{% endfor %}", lambda: {"g": (c for c in "xy")}),
            ("{% for k, v in qd.lists %}{{ k }}={{ v }};{% endfor %}", lambda: {"qd": _qd()}),
            # Non-regression siblings the issue names.
            ("{% for k, v in qd.items %}{{ k }}={{ v }};{% endfor %}", lambda: {"qd": _qd()}),
            ("{% for c in s %}{{ c }};{% endfor %}", lambda: {"s": "abc"}),
            ("{% for k in d %}{{ k }};{% endfor %}", lambda: {"d": {"p": 1, "q": 2}}),
            ("{% for k in it %}{{ k }};{% empty %}E{% endfor %}", lambda: {"it": iter([])}),
            # Consumed ONCE: the second loop is empty on both engines, and the
            # object stays truthy.
            (
                "{% for k in it %}{{ k }};{% endfor %}|{% for k in it %}{{ k }};{% endfor %}"
                "|{% if it %}T{% endif %}",
                lambda: {"it": iter([1, 2, 3])},
            ),
            # `len(iterator)` raises -> Django's `length` is 0, not consumed.
            ("{{ it|length }}|{% for k in it %}{{ k }}{% endfor %}", lambda: {"it": iter([7])}),
        ],
    )
    def test_agrees_with_django(self, engines, src, ctx):
        dj, dr = render_both(engines, src, ctx)
        assert dj == dr, (src, dj, dr)

    def test_the_repr_is_still_what_prints(self, engines):
        it = iter([1, 2, 3])
        dj, dr = render_both(engines, "{{ it }}", lambda: {"it": it})
        assert dj == dr
        assert "list_iterator" in dr

    def test_the_cited_garbage_is_gone(self, engines):
        _, dr = render_both(
            engines, "{% for k in obj.gen %}{{ k }};{% endfor %}", lambda: {"obj": _Obj()}
        )
        assert dr == "a;b;"
        assert "list_iterator" not in dr

    def test_an_unbounded_one_shot_raises_rather_than_hanging(self, engines):
        import itertools

        with pytest.raises(Exception, match="more than"):
            engines["djust"].from_string("{% for k in it %}{{ k }}{% endfor %}").render(
                {"it": itertools.count()}
            )


# --------------------------------------------------------------------- #2529

PAYLOADS = {
    "raw": '"--><script>alert(1)</script><!--',
    "percent": "%22--%3E%3Cscript%3Ealert(1)%3C/script%3E%3C!--",
    "double-percent": "%2522--%253E%253Cscript%253E",
    "fullwidth": "＂--＞＜script＞alert(1)＜/script＞",
    "nested-quotes": '"\'--><b onmouseover="alert(1)">\'"',
    "bare-terminator": "-->",
    "digits-and-dash": "-9-9",
    "newline": "\n--><script>x</script>",
}

MARKER = re.compile(r'<!--dj-if id="([^"]*)"-->')


def _liveview(source: str, state: dict) -> str:
    from djust._rust import RustLiveView

    view = RustLiveView(source)
    view.update_state(state)
    return view.render()


class TestLoopPathIsNotAContextKey2529:
    @pytest.mark.parametrize("name", sorted(PAYLOADS))
    def test_a_context_key_cannot_forge_the_marker(self, name):
        payload = PAYLOADS[name]
        html = _liveview(
            "<div>{% if foo %}<b>x</b>{% endif %}</div>",
            {"foo": 1, "__djust_if_loop_path": payload},
        )
        # The marker id is exactly the template-derived form, whatever the key
        # carried — the sink no longer reads the context at all.
        ids = MARKER.findall(html)
        assert len(ids) == 1, html
        assert re.fullmatch(r"if-[0-9a-f]{8}-0", ids[0]), ids
        assert "<script" not in html
        if payload != "-->":
            assert payload not in html
        assert html == '<div><!--dj-if id="if-4369cbd0-0"--><b>x</b><!--/dj-if--></div>'

    def test_a_context_key_is_an_ordinary_inert_variable(self):
        # It is just a name: asking for it is Django's own parse-time refusal
        # (`Variables and attributes may not begin with underscores`), and a
        # template that does not ask for it renders as if it were absent.
        with pytest.raises(RuntimeError, match="may not begin with underscores"):
            _liveview("{{ __djust_if_loop_path }}", {"__djust_if_loop_path": PAYLOADS["raw"]})
        html = _liveview("<i>{{ foo }}</i>", {"foo": "ok", "__djust_if_loop_path": PAYLOADS["raw"]})
        assert html == "<i>ok</i>"

    def test_a_legitimate_loop_path_still_round_trips(self):
        html = _liveview(
            "<div>{% for i in items %}{% if i %}<b>{{ i }}</b>{% endif %}{% endfor %}</div>",
            {"items": [1, 2], "__djust_if_loop_path": PAYLOADS["raw"]},
        )
        assert MARKER.findall(html) == ["if-bcde5f9a-0-0", "if-bcde5f9a-0-1"]
        assert "<script" not in html

    def test_nested_loops_compose_the_path(self):
        html = _liveview(
            "{% for i in a %}{% for j in b %}{% if j %}<b>x</b>{% endif %}{% endfor %}{% endfor %}",
            {"a": [0, 1], "b": [0, 1]},
        )
        ids = MARKER.findall(html)
        assert [i.split("-", 2)[2] for i in ids] == ["0-0-0", "0-0-1", "0-1-0", "0-1-1"]

    def test_the_path_is_reset_after_the_loop(self):
        html = _liveview(
            "{% for i in a %}{% if i %}<b>x</b>{% endif %}{% endfor %}{% if t %}<b>y</b>{% endif %}",
            {"a": [1], "t": 1},
        )
        ids = MARKER.findall(html)
        assert ids[0].endswith("-0-0")
        assert re.fullmatch(r"if-[0-9a-f]{8}-1", ids[1]), ids

    def test_included_body_inherits_the_iteration_path(self, tmp_path):
        from djust._rust import RustLiveView

        (tmp_path / "inc.html").write_text("{% if i %}<b>x</b>{% endif %}")
        view = RustLiveView(
            "{% for i in a %}{% include 'inc.html' only %}{% endfor %}", [str(tmp_path)]
        )
        view.update_state({"a": [1, 2]})
        ids = MARKER.findall(view.render())
        assert len(ids) == 2 and ids[0] != ids[1], ids
        assert ids[0].endswith("-0") and ids[1].endswith("-1"), ids

    def test_the_renderer_never_reads_the_old_key(self):
        renderer = (REPO / "crates/djust_templates/src/renderer.rs").read_text()
        assert 'get("__djust_if_loop_path")' not in renderer
        assert '"__djust_if_loop_path".to_string()' not in renderer
        assert "context.dj_if_loop_path()" in renderer
        assert "set_dj_if_loop_path(" in renderer

    def test_the_field_refuses_non_path_grammar(self):
        """`Context::set_dj_if_loop_path` accepts only `(-<digits>)*`."""
        ctx = (REPO / "crates/djust_core/src/context.rs").read_text()
        assert "c == '-' || c.is_ascii_digit()" in ctx


# --------------------------------------------------------------------- #2660


class TestParityGaps2660:
    def test_nested_block_super_is_empty_when_inner_has_no_parent(self, engines):
        src = (
            "{% extends 'base.html' %}{% block c %}"
            "{% block inner %}[{{ block.super }}]{% endblock %}{% endblock %}"
        )
        dj, dr = render_both(engines, src, dict)
        assert dj == dr == "[]"

    def test_outer_block_super_still_reaches_the_parent(self, engines):
        src = (
            "{% extends 'base.html' %}{% block c %}<{{ block.super }}>"
            "{% block inner %}[{{ block.super }}]{% endblock %}{% endblock %}"
        )
        dj, dr = render_both(engines, src, dict)
        assert dj == dr == "<three>[]"

    @pytest.mark.parametrize(
        ("src", "ctx"),
        [
            ("{% load static %}{% static p %}", lambda: {"p": "a.css"}),
            ("{% load static %}{% static p %}", dict),
            ("{% load static %}{% static 'a.css' %}", dict),
            ("{% load static %}{% static p|upper %}", lambda: {"p": "a.css"}),
            ("{% load static %}{% static p as u %}{{ u }}", lambda: {"p": "a.css"}),
        ],
    )
    def test_static_operand_agrees(self, engines, src, ctx):
        dj, dr = render_both(engines, src, ctx)
        assert dj == dr, (src, dj, dr)

    def test_raw_entry_static_agrees_with_the_backend(self):
        """The raw `render_template` entry answers the same bytes.

        In a Django-configured process the registered `static` library serves
        the tag on every entry, so the NATIVE Rust node is not reachable from
        here; its operand rule is pinned at the Rust level in
        `renderer.rs::static_operand_tests_2660` (gate-off: 3 of 4 red).
        """
        from djust import _rust

        assert _rust.render_template("{% static p %}", {"p": "a.css"}) == "/static/a.css"
        assert _rust.render_template("{% static 'a.css' %}", {"p": "x"}) == "/static/a.css"
        assert _rust.render_template("{% static p %}", {}) == "/static/"

    def test_round_tripping_dotdot_loads_like_safe_join(self, engines):
        name = f"sub/../../{TEMPLATE_DIR.name}/ok.html"
        src = "{% include '" + name + "' %}"
        dj, dr = render_both(engines, src, dict)
        assert dj == dr == "OK"

    def test_a_dotdot_that_stays_inside_still_loads(self, engines):
        dj, dr = render_both(engines, "{% include 'sub/../ok.html' %}", dict)
        assert dj == dr == "OK"

    @pytest.mark.parametrize(
        "name",
        [
            "../ok.html",
            "sub/../../ok.html",
            "sub/../../../etc/passwd",
            "/etc/passwd",
            "",
            "sub/../..",
        ],
    )
    def test_escaping_names_are_still_refused(self, engines, name):
        from django.template import TemplateDoesNotExist, TemplateSyntaxError

        src = "{% include '" + name + "' %}"
        # Django's own refusal for a string-origin template with a relative
        # literal is an AttributeError out of `construct_relative_path`; the
        # claim here is only that NEITHER engine loads the file.
        with pytest.raises(Exception):  # noqa: B017 — see above
            engines["django"].from_string(src).render({})
        with pytest.raises((TemplateDoesNotExist, TemplateSyntaxError, RuntimeError)):
            engines["djust"].from_string(src).render({})

    def test_a_context_supplied_escaping_name_is_refused(self, engines):
        secret = TEMPLATE_DIR.parent / "_secret_2660.txt"
        secret.write_text("SECRET")
        try:
            outside = f"sub/../../../{secret.name}"
            from django.template import TemplateDoesNotExist

            with pytest.raises((TemplateDoesNotExist, RuntimeError)):
                engines["djust"].from_string("{% include t %}").render({"t": outside})
        finally:
            secret.unlink()

    def test_the_guard_is_lexical(self):
        """No filesystem call precedes the containment decision."""
        src = (REPO / "crates/djust_templates/src/inheritance.rs").read_text()
        body = src.split("fn template_name_is_contained(")[1].split("\n}\n")[0]
        assert "is_file" not in body and "canonicalize" not in body and "metadata" not in body
        assert "starts_with(&base)" in body


def test_repo_root_is_the_worktree_under_test():
    import djust

    assert Path(djust.__file__).resolve().is_relative_to(REPO), djust.__file__
    assert os.environ.get("PYTEST_CURRENT_TEST")
