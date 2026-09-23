"""A custom filter's ``is_safe=True`` keeps a safe input safe; it does not
make the filter's output safe.

Django's rule, ``django/template/base.py`` ``FilterExpression.resolve``::

    new_obj = func(obj, *arg_vals)
    if getattr(func, "is_safe", False) and isinstance(obj, SafeData):
        obj = mark_safe(new_obj)

where ``obj`` is the filter's INPUT. So::

    @register.filter(is_safe=True)
    def shout(v):
        return v.upper()

    {{ value|shout }}    ->  &lt;B&gt;X&lt;/B&gt;   when ``value`` is plain
    {{ value|safe|shout }}  ->  <B>X</B>

The Rust renderer applies the same rule in ``renderer::filter_output_is_safe``,
used by the ``Node::Variable`` and ``Node::InlineIf`` arms and by
``get_value_safe`` (``{% firstof %}`` / ``{% cycle %}`` operands).

Render assertions compare against Django rendered in-process on the three
djust entry points a project reaches (the raw ``_rust`` call, the template
backend and ``RustLiveView``), and check separately through ``capabilities()``
that plain input produces no live element or event-handler attribute.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote

import pytest

pytest.importorskip("django")

from django import template  # noqa: E402
from django.template import Context as DjangoContext  # noqa: E402
from django.template import Engine  # noqa: E402
from django.template.base import Template as DjangoTemplate  # noqa: E402
from django.template.defaultfilters import stringfilter  # noqa: E402
from django.utils.html import conditional_escape  # noqa: E402
from django.utils.safestring import mark_safe  # noqa: E402

from djust import _rust  # noqa: E402
from djust.mixins.rust_bridge import _collect_safe_keys  # noqa: E402
from djust.serialization import normalize_django_value  # noqa: E402
from djust.template.backend import DjustTemplateBackend  # noqa: E402

# Distinctive names: the Rust filter registry is process-global, so a name
# shared with another module's filter would collide silently between tests.
SHOUT = "_djcfs_shout"
SHOUT_SF = "_djcfs_shout_sf"
WRAP_B = "_djcfs_wrap_b"
UNQ = "_djcfs_unq"
PLAIN_UPPER = "_djcfs_plain_upper"
AE_CANON = "_djcfs_ae_canon"
RET_SAFE = "_djcfs_ret_safe"

_library = template.Library()


@_library.filter(name=SHOUT, is_safe=True)
def _shout(value):
    """``is_safe=True``, plain ``str`` return."""
    return value.upper()


@_library.filter(name=SHOUT_SF, is_safe=True)
@stringfilter
def _shout_sf(value):
    """The same through ``@stringfilter``."""
    return value.upper()


@_library.filter(name=WRAP_B, is_safe=True)
def _wrap_b(value):
    """``is_safe=True`` and interpolates the input into markup, plain return."""
    return "<b>%s</b>" % value


@_library.filter(name=UNQ, is_safe=True)
def _unq(value):
    """``is_safe=True`` on a decoding filter: ``%3Cb%3E`` carries no markup
    until this filter decodes it."""
    return unquote(value)


@_library.filter(name=PLAIN_UPPER)
def _plain_upper(value):
    """``is_safe=False`` control, same body as ``shout``."""
    return value.upper()


@_library.filter(name=AE_CANON, is_safe=True, needs_autoescape=True)
def _ae_canon(value, autoescape=True):
    """Django's ``needs_autoescape`` shape: escapes internally and
    ``mark_safe``s its own output."""
    esc = conditional_escape if autoescape else (lambda x: x)
    return mark_safe("<p>%s</p>" % esc(value))


@_library.filter(name=RET_SAFE)
def _ret_safe(value):
    """``is_safe=False`` but returns a runtime ``SafeString`` (#1660)."""
    return mark_safe("<em>%s</em>" % value)


@pytest.fixture(scope="module", autouse=True)
def _registered():
    """Register with Rust as the #1121 bridge does (flags read off the
    callable), and unregister afterwards. The Django side is a private
    ``Engine`` with the library as a builtin, so nothing touches the
    project-wide registry."""
    for name, fn in _library.filters.items():
        _rust.register_custom_filter(
            name,
            fn,
            bool(getattr(fn, "is_safe", False)),
            bool(getattr(fn, "needs_autoescape", False)),
        )
    yield
    for name in _library.filters:
        _rust.unregister_custom_filter(name)


_engine = Engine(libraries={}, builtins=[])
_engine.template_builtins.append(_library)

_backend = DjustTemplateBackend(
    {"NAME": "djust_custom_filter_is_safe", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}}
)


# ---------------------------------------------------------------------------
# What the output would do in a browser
# ---------------------------------------------------------------------------


class _Capabilities(HTMLParser):
    """Elements and event-handler attributes the markup actually creates.

    A character reference (``&lt;b&gt;``) never creates an element, so the
    output is parsed rather than substring-matched.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.caps: set[str] = set()

    def handle_starttag(self, tag, attrs):
        self.caps.add("tag:" + tag)
        for name, value in attrs:
            if name.startswith("on"):
                self.caps.add("evt:" + name)
            if value and html.unescape(value).strip().lower().startswith("javascript:"):
                self.caps.add("url:" + name)

    handle_startendtag = handle_starttag


def capabilities(out: str) -> set[str]:
    parser = _Capabilities()
    try:
        parser.feed(out)
        parser.close()
    except Exception:  # noqa: BLE001 - markup that does not parse creates nothing
        pass
    return parser.caps


def test_capabilities_helper_distinguishes_markup_from_text():
    assert capabilities("<img src=x onerror=f()>") == {"tag:img", "evt:onerror"}
    assert capabilities("&lt;img src=x onerror=f()&gt;") == set()
    assert capabilities('<a title="x&quot; onclick=&quot;f()">') == {"tag:a"}


# ---------------------------------------------------------------------------
# The render entry points: Django, and the three djust paths
# ---------------------------------------------------------------------------


def django_render(source: str, ctx: dict) -> str:
    return DjangoTemplate(source, engine=_engine).render(DjangoContext(dict(ctx)))


def _normalized_with_safe_keys(ctx: dict) -> tuple[dict, list[str]]:
    normalized = normalize_django_value(dict(ctx))
    safe_keys: list[str] = []
    for key, sub in normalized.items():
        safe_keys.extend(_collect_safe_keys(sub, key))
    return normalized, safe_keys


def djust_raw(source: str, ctx: dict) -> str:
    """The ``_rust`` entry, with the ``safe_keys`` channel that carries a
    view's ``mark_safe`` values."""
    normalized, safe_keys = _normalized_with_safe_keys(ctx)
    return _rust.render_template_with_dirs(source, normalized, [], safe_keys)


def djust_backend(source: str, ctx: dict) -> str:
    """``DjustTemplateBackend``: what a Django ``TEMPLATES`` entry renders."""
    return str(_backend.from_string(source).render(dict(ctx)))


def djust_live(source: str, ctx: dict) -> str:
    """``RustLiveView``: the LiveView path, via ``mark_safe_keys``."""
    view = _rust.RustLiveView(source)
    normalized, safe_keys = _normalized_with_safe_keys(ctx)
    view.update_state(normalized)
    if safe_keys:
        view.mark_safe_keys(safe_keys)
    return view.render()


PATHS = [
    ("raw", djust_raw),
    ("backend", djust_backend),
    ("live", djust_live),
]
PATH_IDS = [p[0] for p in PATHS]

H = "<b>x</b>"


def assert_all_paths_agree_with_django(source: str, ctx: dict) -> str:
    """Every djust path renders Django's bytes. Returns Django's output."""
    expected = django_render(source, ctx)
    for label, fn in PATHS:
        actual = fn(source, ctx)
        assert actual == expected, (
            f"{label}: {source} on {ctx!r}: django={expected!r} djust={actual!r}"
        )
    return expected


# ---------------------------------------------------------------------------
# 1. Plain input through an is_safe=True custom filter is escaped
# ---------------------------------------------------------------------------


PLAIN_INPUT = [
    ("shout", "{{ h|%s }}" % SHOUT, {"h": H}),
    ("shout_sf", "{{ h|%s }}" % SHOUT_SF, {"h": H}),
    ("wrap_b", "{{ h|%s }}" % WRAP_B, {"h": H}),
    ("unq", "{{ h|%s }}" % UNQ, {"h": "%3Cb%3Ex%3C/b%3E"}),
]
PLAIN_INPUT_IDS = [r[0] for r in PLAIN_INPUT]


class TestPlainInputIsEscaped:
    @pytest.mark.parametrize(("label", "source", "ctx"), PLAIN_INPUT, ids=PLAIN_INPUT_IDS)
    @pytest.mark.parametrize(("path", "render"), PATHS, ids=PATH_IDS)
    def test_plain_input_through_an_is_safe_filter_is_escaped(
        self, label, source, ctx, path, render
    ) -> None:
        expected = django_render(source, ctx)
        actual = render(source, ctx)
        assert actual == expected, f"{path}/{label}: django={expected!r} djust={actual!r}"
        assert "tag:b" not in capabilities(actual), f"{path}/{label}: {actual!r}"

    @pytest.mark.parametrize(("label", "source", "ctx"), PLAIN_INPUT, ids=PLAIN_INPUT_IDS)
    def test_the_django_reference_is_escaped(self, label, source, ctx) -> None:
        """The comparison only means something if Django's side escapes."""
        expected = django_render(source, ctx)
        assert "&lt;" in expected, expected
        assert "tag:b" not in capabilities(expected), expected


# ---------------------------------------------------------------------------
# 2. A safe input stays safe through the filter
# ---------------------------------------------------------------------------


class TestSafeInputStaysSafe:
    """What ``is_safe=True`` does: a ``SafeData`` input stays safe through
    the filter, so its markup is emitted unchanged."""

    @pytest.mark.parametrize(("path", "render"), PATHS, ids=PATH_IDS)
    def test_safe_then_is_safe_filter(self, path, render) -> None:
        source = "{{ h|safe|%s }}" % SHOUT
        expected = django_render(source, {"h": H})
        assert expected == "<B>X</B>"
        assert render(source, {"h": H}) == expected, path

    @pytest.mark.parametrize(("path", "render"), PATHS, ids=PATH_IDS)
    def test_mark_safe_context_value_then_is_safe_filter(self, path, render) -> None:
        source = "{{ s|%s }}" % SHOUT
        ctx = {"s": mark_safe(H)}
        expected = django_render(source, ctx)
        assert expected == "<B>X</B>"
        assert render(source, ctx) == expected, path

    @pytest.mark.parametrize(("path", "render"), PATHS, ids=PATH_IDS)
    def test_runtime_safe_output_feeds_an_is_safe_filter(self, path, render) -> None:
        """A filter that returns ``mark_safe`` makes the NEXT filter's input
        safe, so a following ``is_safe=True`` filter keeps it safe."""
        source = "{{ h|%s|%s }}" % (RET_SAFE, SHOUT)
        expected = django_render(source, {"h": "x"})
        assert expected == "<EM>X</EM>"
        assert render(source, {"h": "x"}) == expected, path

    def test_safe_and_plain_input_render_differently(self) -> None:
        assert djust_raw("{{ h|safe|%s }}" % SHOUT, {"h": H}) != djust_raw(
            "{{ h|%s }}" % SHOUT, {"h": H}
        )


# ---------------------------------------------------------------------------
# 3. Chain position
# ---------------------------------------------------------------------------


class TestChainPosition:
    @pytest.mark.parametrize(
        "source",
        [
            "{{ h|%s|lower }}" % SHOUT,
            "{{ h|lower|%s }}" % SHOUT,
            "{{ h|%s|%s }}" % (SHOUT, SHOUT_SF),
        ],
        ids=["shout-then-lower", "lower-then-shout", "shout-then-shout_sf"],
    )
    def test_no_filter_in_the_chain_receives_safe_input(self, source) -> None:
        out = assert_all_paths_agree_with_django(source, {"h": H})
        assert "tag:b" not in capabilities(out), out
        assert "&lt;" in out, out

    def test_a_trailing_safe_is_still_honoured(self) -> None:
        out = assert_all_paths_agree_with_django("{{ h|%s|safe }}" % SHOUT, {"h": H})
        assert out == "<B>X</B>"


# ---------------------------------------------------------------------------
# 4. Neighbouring rules are unchanged
# ---------------------------------------------------------------------------


class TestNeighbouringRules:
    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("{{ h|%s }}" % RET_SAFE, "<em><b>x</b></em>"),
            ("{{ h|%s }}" % AE_CANON, "<p>&lt;b&gt;x&lt;/b&gt;</p>"),
            ("{{ h|%s }}" % PLAIN_UPPER, "&lt;B&gt;X&lt;/B&gt;"),
        ],
        ids=["runtime-safe-return", "needs-autoescape", "is-safe-false-control"],
    )
    def test_rules_unchanged(self, source, expected) -> None:
        """A runtime ``SafeString`` return (#1660) is safe with or without
        the flag; ``needs_autoescape`` filters escape internally; an
        ``is_safe=False`` filter escapes."""
        out = assert_all_paths_agree_with_django(source, {"h": H})
        assert out == expected


# ---------------------------------------------------------------------------
# 5. Every renderer arm
# ---------------------------------------------------------------------------


class TestRendererArms:
    """``Node::Variable`` (above), ``get_value_safe`` (tag operands), a loop
    item, and ``Node::InlineIf``. (1.1's ``{% with %}`` does not apply
    filters to its bindings, so it has no row here.)"""

    @pytest.mark.parametrize(
        ("source", "ctx"),
        [
            ("{%% firstof h|%s %%}" % SHOUT, {"h": H}),
            ("{%% for i in items %%}{{ i|%s }}{%% endfor %%}" % SHOUT, {"items": [H]}),
        ],
        ids=["firstof", "for-loop-item"],
    )
    def test_tag_operand_and_binding_arms_escape(self, source, ctx) -> None:
        out = assert_all_paths_agree_with_django(source, ctx)
        assert out == "&lt;B&gt;X&lt;/B&gt;"

    @pytest.mark.parametrize(("path", "render"), PATHS, ids=PATH_IDS)
    def test_firstof_keeps_a_safe_operand_safe(self, path, render) -> None:
        source = "{%% firstof s|%s %%}" % SHOUT
        ctx = {"s": mark_safe(H)}
        expected = django_render(source, ctx)
        assert expected == "<B>X</B>"
        assert render(source, ctx) == expected, path

    @pytest.mark.parametrize(("path", "render"), PATHS, ids=PATH_IDS)
    def test_inline_if_arm_escapes(self, path, render) -> None:
        """``{{ x if c else y|filter }}`` is djust-only syntax (the filters
        apply to the chosen branch); it renders the same bytes as the plain
        ``{{ h|shout }}`` Django reference."""
        expected = django_render("{{ h|%s }}" % SHOUT, {"h": H})
        out = render('{{ h if f else ""|%s }}' % SHOUT, {"h": H, "f": True})
        assert out == expected, f"{path}: {out!r}"


# ---------------------------------------------------------------------------
# 6. Other representations of the same input
# ---------------------------------------------------------------------------


ENCODED = [
    ("already-escaped", "{{ h|%s }}" % SHOUT, {"h": "&lt;b&gt;x&lt;/b&gt;"}),
    ("fullwidth-brackets", "{{ h|%s }}" % SHOUT, {"h": "<ｂ>x</ｂ>"}),
    ("self-closing-img", "{{ h|%s }}" % SHOUT, {"h": "<img/src=x onerror=f()>"}),
    ("comment-closer", "<!-- {{ h|%s }} -->" % SHOUT, {"h": "--><b>1</b><!--"}),
    ("attr-double-quote", '<a title="{{ h|%s }}">' % SHOUT, {"h": 'x" onclick="f()'}),
    ("attr-single-quote", "<a title='{{ h|%s }}'>" % SHOUT, {"h": "x' onclick='f()"}),
]
ENCODED_IDS = [e[0] for e in ENCODED]


class TestOtherRepresentations:
    @pytest.mark.parametrize(("label", "source", "ctx"), ENCODED, ids=ENCODED_IDS)
    @pytest.mark.parametrize(("path", "render"), PATHS, ids=PATH_IDS)
    def test_django_equal_and_no_new_elements(self, label, source, ctx, path, render) -> None:
        expected = django_render(source, ctx)
        actual = render(source, ctx)
        assert actual == expected, f"{path}/{label}: django={expected!r} djust={actual!r}"
        caps = capabilities(actual)
        assert not any(c.startswith("evt:") for c in caps), f"{path}/{label}: {actual!r}"
        assert not caps - {"tag:a"}, f"{path}/{label}: {actual!r}"


# ---------------------------------------------------------------------------
# 7. Source pin: the flag is only read together with the input's safeness
# ---------------------------------------------------------------------------


_CRATES = Path(__file__).resolve().parents[2] / "crates"


def test_is_custom_filter_safe_is_read_only_in_filter_output_is_safe() -> None:
    """``is_custom_filter_safe`` is ``pub``; every call must go through
    ``renderer::filter_output_is_safe``, which gates it on the input."""
    hits = []
    for path in sorted(_CRATES.glob("*/src/**/*.rs")):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("//") or "fn is_custom_filter_safe(" in stripped:
                continue
            if "is_custom_filter_safe(" in stripped:
                hits.append((path.name, lineno, stripped))
    assert len(hits) == 1 and hits[0][0] == "renderer.rs", hits
    assert re.search(
        r"input_was_safe\s*&&\s*crate::filter_registry::is_custom_filter_safe\(", hits[0][2]
    ), hits
