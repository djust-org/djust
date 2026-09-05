"""#2519 — ``<!--dj-if-->`` VDOM markers must not leak on the plain-backend path.

The markers are the VDOM differ's keyed boundaries. On the LiveView path
(``RustLiveView.render`` / ``render_with_diff``) they are load-bearing; on the
plain ``DjustTemplateBackend`` path — and the two ``_rust`` entries it and
``SimpleLiveView`` call — there is no VDOM, and Django emits nothing.

Root cause was parallel-path drift (#1646): ``_rust.render_template`` stripped
the markers with a post-render regex, ``_rust.render_template_with_dirs`` (the
one the backend binds) did not. The fix removes the strip and has BOTH plain
entries switch marker emission off on their ``Context``
(``set_emit_dj_if_markers(false)``); the LiveView path keeps the default.
The same rule holds inside ``crates/djust_templates`` (#2537): its own
``Template::py_render`` / ``render_template`` are Python-facing and switch
the markers off too — pinned by ``TestSourcePins`` below.

Every backend row here is a real differential: the same source rendered
through Django's ``DjangoTemplates`` backend and through ``DjustTemplateBackend``
must be byte-equal. A parity pin proves the flag is path-specific (the LiveView
path still emits the marker for identical source), and a source-level count pin
guards against a third plain entry appearing unflagged or the strip creeping
back.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.template.backends.django import DjangoTemplates

from djust import _rust
from djust.simple_live_view import SimpleLiveView
from djust.template.backend import DjustTemplateBackend

REPO_ROOT = Path(__file__).resolve().parents[2]

ISSUE_SHAPE = "{% if foo %}foo{% elif bar %}bar{% endif %}"

# (label, source, context) — the reproducer table from the plan, every shape
# the renderer's `Node::If` arm can take on a plain page: text/element bodies,
# else branches, loop nesting (#1832 loop-path ids), attribute context (#380),
# `{% include %}` (non-`only` clones the parent Context; `only` builds a FRESH
# one, the landmine that must copy the flag), and `{% extends %}`.
CASES = [
    ("issue_shape_if_elif_both_false", ISSUE_SHAPE, {}),
    ("text_if_false", "{% if foo %}x{% endif %}", {}),
    ("text_if_true", "{% if foo %}x{% endif %}", {"foo": True}),
    ("text_if_else_false", "{% if foo %}x{% else %}y{% endif %}", {}),
    ("element_if_false", "{% if foo %}<b>x</b>{% endif %}", {}),
    ("element_if_true", "{% if foo %}<b>x</b>{% endif %}", {"foo": True}),
    ("element_if_else_false", "{% if foo %}<b>x</b>{% else %}<i>y</i>{% endif %}", {}),
    (
        "for_text_if_nested_false",
        "{% for i in items %}{% if foo %}x{% endif %}{% endfor %}",
        {"items": [1, 2]},
    ),
    (
        "for_element_if_nested_false",
        "{% for i in items %}{% if foo %}<b>x</b>{% endif %}{% endfor %}",
        {"items": [1, 2]},
    ),
    (
        "for_element_if_nested_true",
        "{% for i in items %}{% if i %}<b>{{ i }}</b>{% endif %}{% endfor %}",
        {"items": [1, 2]},
    ),
    ("if_in_attribute_false", '<div class="a {% if foo %}b{% endif %}"></div>', {}),
    ("include_with_false_text_if", "{% include 'inc_false_if_text.html' %}", {}),
    ("include_with_false_element_if", "{% include 'inc_false_if.html' %}", {}),
    ("include_only_with_false_text_if", "{% include 'inc_false_if_text.html' only %}", {}),
    ("include_only_with_false_element_if", "{% include 'inc_false_if.html' only %}", {}),
    (
        "include_only_with_true_branch",
        "{% include 'inc_false_if.html' with foo=1 only %}",
        {},
    ),
    (
        "include_inside_true_if",
        "{% if outer %}{% include 'inc_false_if.html' %}{% endif %}",
        {"outer": True, "foo": True},
    ),
    ("wrapped_element_if_true", "<div>{% if foo %}<b>x</b>{% endif %}</div>", {"foo": 1}),
    (
        "extends_parent_and_child_ifs",
        '{% extends "parent.html" %}'
        "{% block content %}{% if show_a %}<div>A</div>{% endif %}{% endblock %}",
        {"show_header": True},
    ),
]
CASE_IDS = [c[0] for c in CASES]


@pytest.fixture(scope="module")
def template_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    d = tmp_path_factory.mktemp("tpl2519")
    (d / "inc_false_if.html").write_text("{% if foo %}<b>foo</b>{% endif %}", encoding="utf-8")
    (d / "inc_false_if_text.html").write_text("{% if foo %}foo{% endif %}", encoding="utf-8")
    (d / "parent.html").write_text(
        "<html>{% if show_header %}<header>H</header>{% endif %}"
        "{% block content %}{% endblock %}</html>",
        encoding="utf-8",
    )
    return d


def _params(name: str, template_dir: Path) -> dict:
    return {"NAME": name, "DIRS": [str(template_dir)], "APP_DIRS": False, "OPTIONS": {}}


@pytest.fixture(scope="module")
def django_engine(template_dir: Path) -> DjangoTemplates:
    return DjangoTemplates(_params("django", template_dir))


@pytest.fixture(scope="module")
def djust_engine(template_dir: Path) -> DjustTemplateBackend:
    return DjustTemplateBackend(_params("djust", template_dir))


# --------------------------------------------------------------------------- #
# the issue's exact reproducer
# --------------------------------------------------------------------------- #


class TestIssueReproducer:
    def test_backend_from_string_renders_empty_like_django(self, djust_engine, django_engine):
        assert django_engine.from_string(ISSUE_SHAPE).render({}) == ""
        assert djust_engine.from_string(ISSUE_SHAPE).render({}) == ""


# --------------------------------------------------------------------------- #
# the differential: DjustTemplateBackend == DjangoTemplates, byte for byte
# --------------------------------------------------------------------------- #


class TestPlainBackendMatchesDjango:
    @pytest.mark.parametrize(("label", "source", "ctx"), CASES, ids=CASE_IDS)
    def test_backend_output_is_byte_equal_to_django(
        self, label, source, ctx, djust_engine, django_engine
    ):
        expected = django_engine.from_string(source).render(dict(ctx))
        actual = djust_engine.from_string(source).render(dict(ctx))
        assert "dj-if" not in expected, "the reference must not know the marker"
        assert actual == expected, f"{label}: {actual!r} != Django {expected!r}"

    def test_django_reference_is_not_vacuous(self, django_engine):
        # The differential only means something if the reference produces
        # DIFFERENT bytes for different shapes.
        outs = {django_engine.from_string(src).render(dict(ctx)) for _, src, ctx in CASES}
        assert len(outs) > 1


# --------------------------------------------------------------------------- #
# the three plain entries, one test each (#1104)
# --------------------------------------------------------------------------- #


class TestEachPlainEntry:
    def test_rust_render_template_emits_no_markers(self):
        assert _rust.render_template(ISSUE_SHAPE, {}) == ""
        assert (
            _rust.render_template("<div>{% if foo %}<b>x</b>{% endif %}</div>", {"foo": 1})
            == "<div><b>x</b></div>"
        )

    def test_rust_render_template_with_dirs_emits_no_markers(self, template_dir):
        dirs = [str(template_dir)]
        assert _rust.render_template_with_dirs(ISSUE_SHAPE, {}, dirs) == ""
        assert (
            _rust.render_template_with_dirs(
                "<div>{% if foo %}<b>x</b>{% endif %}</div>", {"foo": 1}, dirs
            )
            == "<div><b>x</b></div>"
        )
        assert (
            _rust.render_template_with_dirs("{% include 'inc_false_if.html' only %}", {}, dirs)
            == ""
        )

    def test_simple_live_view_render_template_emits_no_markers(self):
        class _Page(SimpleLiveView):
            template = "<section>" + ISSUE_SHAPE + "{% if on %}<b>x</b>{% endif %}</section>"
            on = True

        assert _Page().render_template() == "<section><b>x</b></section>"


# --------------------------------------------------------------------------- #
# the parity pin: the flag is PATH-specific — LiveView still emits markers
# --------------------------------------------------------------------------- #

_OPEN_PAIR = '<!--dj-if id="if-'


_LIVE_CASES = [
    ("<div>{% if foo %}<b>x</b>{% endif %}</div>", {"foo": 1}, _OPEN_PAIR),
    ("{% if foo %}x{% endif %}", {}, "<!--dj-if-->"),
]
_LIVE_IDS = ["element_pair", "legacy_placeholder"]


def _live_render(source: str, ctx: dict) -> str:
    view = _rust.RustLiveView(source)
    view.update_state(dict(ctx))
    return view.render()


class TestLiveViewPathStillEmitsMarkers:
    """The LiveView CONTROL: unaffected by the plain-entry flag."""

    @pytest.mark.parametrize(("source", "ctx", "expect"), _LIVE_CASES, ids=_LIVE_IDS)
    def test_live_view_render_emits_the_marker(self, source, ctx, expect):
        live = _live_render(source, ctx)
        assert expect in live, f"LiveView path lost its marker: {live!r}"

    @pytest.mark.parametrize(("source", "ctx", "expect"), _LIVE_CASES, ids=_LIVE_IDS)
    def test_backend_is_the_live_view_bytes_minus_markers(self, source, ctx, expect, djust_engine):
        live = _live_render(source, ctx)
        plain = djust_engine.from_string(source).render(dict(ctx))
        assert "dj-if" not in plain
        assert plain == re.sub(r"<!--/?dj-if(?:\s[^>]*)?-->", "", live)


# --------------------------------------------------------------------------- #
# the count pin (#1125): every plain entry sets the flag; the strip is gone
# --------------------------------------------------------------------------- #


class TestSourcePins:
    def test_both_djust_live_plain_entries_set_the_flag(self):
        src = (REPO_ROOT / "crates" / "djust_live" / "src" / "lib.rs").read_text(encoding="utf-8")
        assert src.count("set_emit_dj_if_markers(false)") == 2, (
            "exactly the two plain entries (`render_template`, "
            "`render_template_with_dirs`) switch marker emission off; a third "
            "plain entry must set it too, and a LiveView entry must never"
        )

    def test_every_python_facing_entry_in_djust_templates_sets_the_flag(self):
        # #2537: the templates crate has two `#[pymethods]`/`#[pyfunction]`
        # entries of its own (`Template::py_render`, `render_template`). Neither
        # is registered on `djust._rust` today, but if either is ever exposed it
        # must not be the one Python-shaped entry that leaks VDOM markers.
        src = (REPO_ROOT / "crates" / "djust_templates" / "src" / "lib.rs").read_text(
            encoding="utf-8"
        )
        entries = src.count("fn py_render(") + src.count("fn render_template(")
        assert entries == 2, "the set of Python-facing entries in djust_templates changed"
        assert src.count("set_emit_dj_if_markers(false)") == entries, (
            "every Python-facing entry in crates/djust_templates/src/lib.rs "
            "switches marker emission off (#2537)"
        )

    def test_the_regex_strip_is_deleted_everywhere(self):
        hits = [
            p
            for p in (REPO_ROOT / "crates").rglob("*.rs")
            if "strip_dj_if_markers" in p.read_text(encoding="utf-8")
        ]
        assert hits == [], (
            "the post-render strip was the second mechanism that let this bug "
            f"ship (#2233 shadowing); it must stay deleted: {hits}"
        )
