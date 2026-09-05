"""#2556 PR A — the ``{% autoescape on|off %}`` tag on the Rust engine.

Django's ``AutoEscapeControlNode`` saves ``context.autoescape``, sets it for
the body and restores it. djust mirrors that with a ``Context::autoescape``
flag (mirroring ``emit_dj_if_markers``: render-time, per-context, never on the
cached ``Template``) that a ``Node::AutoEscape`` render arm flips on a
lexical body and restores on exit.

The flag is an EMIT-time term and the ``needs_autoescape`` argument — it is
**not** a safety grant. It reaches the sink at exactly the sites in the plan's
§2.3 table (rows below, one cell each); it never enters
``filter_output_is_safe`` / ``filter_output_items_are_safe`` /
``SAFE_OUTPUT_FILTERS`` / ``input_was_safe`` (``TestSecurityPins``).

Every cell is rendered on Django in-process AND on each Rust entry a project
reaches — the plain ``render_template_with_dirs`` call, ``DjustTemplateBackend``
and the LiveView ``RustLiveView`` path — and compared byte-for-byte
(``PATHS``). ``TestRandomizedDifferential`` is the v1.1.1-2 rule: a curated
table samples one axis, so a small template grammar × {on, off} × {plain,
``mark_safe``, ``format_html``} inputs is swept against Django as well.

Gate-off (#1468, #2129/#2135 harness rules), each mechanism alone — run by hand
before commit, results in the PR body:

* remove the render arm's ``set_autoescape(*on)`` → the differential and every
  ``off`` row go red;
* remove the ``{% include … only %}`` copy → only the ``include only`` row and
  its differential cases go red;
* pass literal ``true`` to the custom-filter bridge again → only the
  ``needs_autoescape custom filter`` row goes red.
"""

from __future__ import annotations

import random
import re
import sys
from pathlib import Path

import pytest

pytest.importorskip("django")

from django.http import HttpResponse  # noqa: E402
from django.template import Context as DjangoContext  # noqa: E402
from django.template import Engine, Library  # noqa: E402
from django.test import override_settings  # noqa: E402
from django.urls import path  # noqa: E402
from django.utils.html import format_html  # noqa: E402
from django.utils.safestring import mark_safe  # noqa: E402

from djust import _rust, template_libraries  # noqa: E402
from djust.mixins.rust_bridge import _collect_safe_keys  # noqa: E402
from djust.serialization import normalize_django_value  # noqa: E402
from djust.template.backend import DjustTemplateBackend  # noqa: E402

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
REPO = HERE.parents[1]
# A `{% load %}`-bridged library (#2547): its `simple_tag` / `simple_block_tag`
# render through Django's OWN node, so the block policy must reach the Django
# `Context` the bridge builds — a #1646 twin of the `register_tag_handler` row.
LIBRARIES = {"ae2556_lib": "lib2556.templatetags.ae2556_lib"}
CRATES = REPO / "crates"
RENDERER_RS = CRATES / "djust_templates" / "src" / "renderer.rs"
CONTEXT_RS = CRATES / "djust_core" / "src" / "context.rs"

# ---------------------------------------------------------------------------
# The two custom-registry rows (11 and 12 of the sink table) need the same
# filter / tag on BOTH engines.
# ---------------------------------------------------------------------------

_LIB = Library()


@_LIB.filter(name="ae2556_needs", needs_autoescape=True)
def _needs_autoescape_filter(value, autoescape=True):
    # Django's own `linebreaksbr` shape: the flag is the filter's to read.
    return f"{value}|ae={autoescape}"


@_LIB.filter(name="ae2556_shout", is_safe=True)
def _is_safe_filter(value):
    # #2548's `shout`: `is_safe=True` keeps a safe input safe, never grants.
    return str(value).upper()


@_LIB.simple_tag(name="ae2556_tag")
def _simple_tag(value):
    return "[" + str(value) + "]"


class _Probe:
    """The ``.render(args, context)`` shape ``registry.rs`` calls a handler with."""

    def __init__(self, fn):
        self.fn = fn

    def render(self, args, *rest):
        return self.fn(*args)


@pytest.fixture(scope="module", autouse=True)
def _registrations():
    _rust.register_custom_filter("ae2556_needs", _needs_autoescape_filter, False, True)
    _rust.register_custom_filter("ae2556_shout", _is_safe_filter, True, False)
    _rust.register_tag_handler("ae2556_tag", _Probe(_simple_tag))
    yield
    _rust.unregister_custom_filter("ae2556_needs")
    _rust.unregister_custom_filter("ae2556_shout")


@pytest.fixture(scope="module")
def tpl_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    d = tmp_path_factory.mktemp("ae2556")
    (d / "inc.html").write_text("{{ first }} --- {{ missing }}", encoding="utf-8")
    (d / "base.html").write_text(
        "{% autoescape off %}{% block b %}P{{ x }}{% endblock %}{% endautoescape %}|{{ x }}",
        encoding="utf-8",
    )
    (d / "base_on.html").write_text(
        "{% block b %}P{{ x }}{% endblock %}|{{ x }}",
        encoding="utf-8",
    )
    return d


@pytest.fixture(scope="module")
def engines(tpl_dir: Path):
    django_engine = Engine(dirs=[str(tpl_dir)], libraries=LIBRARIES)
    django_engine.template_builtins.append(_LIB)
    # Keep this fixture's library names on each engine, without registering
    # them globally and changing other modules' unknown-library diagnostics.
    backend = DjustTemplateBackend(
        {"NAME": "djust_2556", "DIRS": [str(tpl_dir)], "APP_DIRS": False, "OPTIONS": {}}
    )
    backend.template_libraries.update(LIBRARIES)
    return django_engine, backend, tpl_dir


def _normalized_with_safe_keys(ctx: dict) -> tuple[dict, list[str]]:
    normalized = normalize_django_value(dict(ctx))
    safe_keys: list[str] = []
    for key, sub in normalized.items():
        safe_keys.extend(_collect_safe_keys(sub, key))
    return normalized, safe_keys


def django_render(engines, source: str, ctx: dict) -> str:
    django_engine, _, _ = engines
    return django_engine.from_string(source).render(DjangoContext(dict(ctx)))


def _bridged(engines):
    """``{% load %}`` resolves against the Django ``Engine``'s ``LIBRARIES``
    for this render — parse-time, where the loader hook runs (#2547)."""
    django_engine, _, _ = engines
    return template_libraries.rendering_with_backend(django_engine)


def djust_raw(engines, source: str, ctx: dict) -> str:
    """The ``_rust`` entry the plain backend calls, with the ``safe_keys`` channel."""
    _, _, tpl_dir = engines
    normalized, safe_keys = _normalized_with_safe_keys(ctx)
    with _bridged(engines):
        return _rust.render_template_with_dirs(source, normalized, [str(tpl_dir)], safe_keys)


def djust_backend(engines, source: str, ctx: dict) -> str:
    """``DjustTemplateBackend`` — what a Django ``TEMPLATES`` entry renders."""
    _, backend, _ = engines
    return str(backend.from_string(source).render(dict(ctx)))


def djust_live(engines, source: str, ctx: dict) -> str:
    """``RustLiveView`` — the LiveView path, via ``mark_safe_keys`` (#2287)."""
    _, _, tpl_dir = engines
    with _bridged(engines):
        view = _rust.RustLiveView(source, [str(tpl_dir)])
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

HOSTILE = "<b>&\"'x"
CTX = {
    "x": HOSTILE,
    "s": mark_safe("<i>&</i>"),
    # Uniformly marked or unmarked: a MIXED list is escaped whole (#2287),
    # which is a pre-existing per-element limitation, not this PR's.
    "l": ["<i>", "&"],
    "ls": [mark_safe("<u>"), mark_safe("<v>&")],
    "xs": [1, 2, 3],
    "first": "&",
    "var1": "&",
    "url": "http://example.com/?a=<x>&b=1",
    "csrf_token": "<t>",
    "nl": "<a>\nb\n\nc",
    "none": None,
}


def _off(body: str) -> str:
    return "{% autoescape off %}" + body + "{% endautoescape %}"


def _on(body: str) -> str:
    return "{% autoescape on %}" + body + "{% endautoescape %}"


# ---------------------------------------------------------------------------
# §2.3 — the sink table, one Django-parity cell per row, under `off` AND `on`
# ---------------------------------------------------------------------------

SINK_ROWS = {
    # 1 Node::Variable emit
    "1-variable": "{{ x }}|{{ s }}|{{ x|lower }}|{{ s|lower }}",
    "1-variable-in-attr": '<a title="{{ x }}">{{ x }}</a>',
    # 3 Node::Cycle emit
    "3-cycle": "{% for i in xs %}{% cycle x s %}{% endfor %}",
    # 4 firstof
    "4-firstof": "{% firstof none x %}|{% firstof s %}",
    # 5 csrf_token — UNCHANGED (Django's CsrfTokenNode is format_html)
    "5-csrf_token": "{% csrf_token %}",
    # 6 apply_filter_full_safe gains the flag — chains that end unsafe
    "6-chain": "{{ x|upper|lower }}|{{ s|upper }}|{{ x|ae2556_shout }}|{{ s|ae2556_shout }}",
    # 7 the five `needs_autoescape` built-ins: autoescape && !SafeData
    "7-linebreaks": "{{ nl|linebreaks }}|{{ s|linebreaks }}",
    "7-linebreaksbr": "{{ nl|linebreaksbr }}|{{ s|linebreaksbr }}",
    "7-linenumbers": "{{ nl|linenumbers }}|{{ s|linenumbers }}",
    "7-urlize": "{{ x|urlize }}",
    "7-urlizetrunc": '{{ x|urlizetrunc:"10" }}',
    # 8 join
    "8-join": '{{ l|join:"<br>" }}|{{ l|join:x }}|{{ l|safeseq|join:", " }}|{{ ls|join:"," }}',
    # 9 unordered_list
    "9-unordered_list": "{{ l|unordered_list }}|{{ l|safeseq|unordered_list }}|{{ ls|unordered_list }}",
    # 10 escapeseq — UNCHANGED (unconditional)
    "10-escapeseq": '{{ l|escapeseq|join:"," }}|{{ ls|escapeseq|join:"," }}',
    # 11 custom `needs_autoescape` filter — the bridge passes the flag
    "11-custom-needs_autoescape": "{{ x|ae2556_needs }}|{{ s|ae2556_needs }}",
    # 11 `{% load %}`-bridged `needs_autoescape` filter — same bridge, via
    # `register_django_filter` (#2547)
    "11-library-needs_autoescape": "{% load ae2556_lib %}{{ x|lib2556_needs }}|{{ s|lib2556_needs }}",
    # 12 custom tag handler — escape_handler_return honours the flag
    "12-custom-tag": "{% ae2556_tag x %}",
    # 12 `{% load %}`-bridged library tags — Django's own SimpleNode /
    # SimpleBlockNode read the flag off the Context the bridge hands them
    "12-library-simple_tag": "{% load ae2556_lib %}{% lib2556_tag x %}|{% lib2556_safe_tag x %}",
    "12-library-block_tag": (
        "{% load ae2556_lib %}{% lib2556_block x %}{{ x }}{% endlib2556_block %}"
    ),
    # 13 include … only — the fresh Context copies the flag
    "13-include-only": '{% include "inc.html" with first=var1 only %}',
    # 14 clone-carrying blocks
    "14-include": '{% include "inc.html" %}',
    "14-for": "{% for i in xs %}{{ x }}{% endfor %}",
    "14-with": "{% with y=x %}{{ y }}{% endwith %}",
    "14-spaceless": "{% spaceless %}<b> <i>{{ x }}</i> </b>{% endspaceless %}",
    # 16 text / widthratio are not escaped either way
    "16-text-widthratio": "<b>&</b>{% widthratio 1 2 100 %}",
    # the `safe` / `escape` / `force_escape` trio keep their own semantics
    "escape-trio": "{{ x|safe }}|{{ x|escape }}|{{ x|force_escape }}|{{ s|escape }}|{{ s|force_escape }}",
    # quoted literal filter argument (`autoescape-tag08`)
    "literal-arg": r'{{ none|default_if_none:" endquote\" hah" }}',
}


class TestSinkTable:
    @pytest.mark.parametrize("row", sorted(SINK_ROWS), ids=sorted(SINK_ROWS))
    @pytest.mark.parametrize("wrap", [_off, _on], ids=["off", "on"])
    @pytest.mark.parametrize(("path_name", "render"), PATHS, ids=[p[0] for p in PATHS])
    def test_every_row_agrees_with_django(self, engines, row, wrap, path_name, render):
        source = wrap(SINK_ROWS[row])
        expected = django_render(engines, source, CTX)
        assert render(engines, source, CTX) == expected, (row, path_name, source)

    def test_off_and_on_differ_where_they_must(self, engines):
        """The `on` twin is not decorative: rows the flag reaches render
        DIFFERENT bytes under `off`. Rows 5 and 10 are pinned unchanged."""
        changed = {
            row
            for row, body in SINK_ROWS.items()
            if djust_raw(engines, _off(body), CTX) != djust_raw(engines, _on(body), CTX)
        }
        assert "5-csrf_token" not in changed
        assert "10-escapeseq" not in changed
        assert "16-text-widthratio" not in changed
        assert "literal-arg" not in changed
        for row in (
            "1-variable",
            "3-cycle",
            "4-firstof",
            "6-chain",
            "7-linebreaks",
            "8-join",
            "9-unordered_list",
            "11-custom-needs_autoescape",
            "11-library-needs_autoescape",
            "12-custom-tag",
            "12-library-simple_tag",
            "12-library-block_tag",
            "13-include-only",
            "14-include",
        ):
            assert row in changed, row

    def test_csrf_token_is_escaped_under_off(self, engines):
        out = djust_raw(engines, _off("{% csrf_token %}"), CTX)
        assert "<t>" not in out
        assert "&lt;t&gt;" in out

    def test_escapeseq_still_escapes_under_off(self, engines):
        assert djust_raw(engines, _off('{{ l|escapeseq|join:"," }}'), CTX) == "&lt;i&gt;,&amp;"
        assert djust_raw(engines, _off('{{ ls|escapeseq|join:"," }}'), CTX) == "<u>,<v>&"


# ---------------------------------------------------------------------------
# Nesting, `include only`, `extends`, and the parser's messages
# ---------------------------------------------------------------------------


class TestBlockStructure:
    @pytest.mark.parametrize(("path_name", "render"), PATHS, ids=[p[0] for p in PATHS])
    def test_nesting_restores_the_outer_setting(self, engines, path_name, render):
        # `autoescape-tag04`, plus one more level.
        src = _off("{{ x }} " + _on("{{ x }} " + _off("{{ x }}")) + " {{ x }}") + "|{{ x }}"
        expected = django_render(engines, src, CTX)
        assert render(engines, src, CTX) == expected
        assert expected.count(HOSTILE) == 3

    @pytest.mark.parametrize(("path_name", "render"), PATHS, ids=[p[0] for p in PATHS])
    def test_include_only_inherits_the_policy(self, engines, path_name, render):
        # `include14`, modulo `string_if_invalid` (#2518/#2550).
        src = _off('{% include "inc.html" with first=var1 only %}')
        assert render(engines, src, CTX) == django_render(engines, src, CTX) == "& --- "

    @pytest.mark.parametrize(("path_name", "render"), PATHS, ids=[p[0] for p in PATHS])
    def test_parent_off_governs_the_child_block(self, engines, path_name, render):
        src = '{% extends "base.html" %}{% block b %}C{{ x }}{% endblock %}'
        expected = django_render(engines, src, CTX)
        assert expected == f"C{HOSTILE}|&lt;b&gt;&amp;&quot;&#x27;x"
        assert render(engines, src, CTX) == expected

    @pytest.mark.parametrize(("path_name", "render"), PATHS, ids=[p[0] for p in PATHS])
    def test_child_off_inside_its_own_block(self, engines, path_name, render):
        src = '{% extends "base_on.html" %}{% block b %}' + _off("{{ x }}") + "{% endblock %}"
        expected = django_render(engines, src, CTX)
        assert expected == f"{HOSTILE}|&lt;b&gt;&amp;&quot;&#x27;x"
        assert render(engines, src, CTX) == expected

    def test_live_path_renders_the_same_bytes_as_the_plain_path(self, engines):
        for row, body in SINK_ROWS.items():
            for wrap in (_off, _on):
                src = wrap(body)
                assert djust_live(engines, src, CTX) == djust_raw(engines, src, CTX), row

    @pytest.mark.parametrize(
        ("src", "message"),
        [
            (
                "{% autoescape %}x{% endautoescape %}",
                "'autoescape' tag requires exactly one argument.",
            ),
            (
                "{% autoescape on off %}x{% endautoescape %}",
                "'autoescape' tag requires exactly one argument.",
            ),
            (
                "{% autoescape maybe %}x{% endautoescape %}",
                "'autoescape' argument should be 'on' or 'off'",
            ),
        ],
    )
    def test_parser_messages_are_djangos_verbatim(self, engines, src, message):
        # Typed as `TemplateSyntaxError` only after #2549; the TEXT is pinned now.
        with pytest.raises(Exception, match=re.escape(message)):
            djust_raw(engines, src, CTX)
        with pytest.raises(Exception, match=re.escape(message)):
            django_render(engines, src, CTX)

    def test_unclosed_block_is_refused(self, engines):
        with pytest.raises(Exception):
            djust_raw(engines, "{% autoescape off %}{{ x }}", CTX)


class TestInlineIfIsDjustOnly:
    """Row 2: ``{{ a if c else b }}`` is a djust extension Django cannot parse,
    so it is pinned against the Variable arm rather than against Django."""

    def test_off_emits_raw_and_on_escapes(self, engines):
        assert djust_raw(engines, _off("{{ x if x else s }}"), CTX) == HOSTILE
        assert djust_raw(engines, _on("{{ x if x else s }}"), CTX) == djust_raw(
            engines, _on("{{ x }}"), CTX
        )
        assert djust_raw(engines, _off("{{ s if x else x }}"), CTX) == "<i>&</i>"


# ---------------------------------------------------------------------------
# The randomized differential (v1.1.1-2 rule)
# ---------------------------------------------------------------------------

_FILTER_NAMES = [
    "",
    "|lower",
    "|upper",
    "|safe",
    "|escape",
    "|force_escape",
    "|linebreaks",
    "|linebreaksbr",
    "|linenumbers",
    "|urlize",
    '|urlizetrunc:"12"',
    "|ae2556_needs",
    "|ae2556_shout",
    "|safe|lower",
    "|lower|safe",
    "|escape|lower",
    "|safe|linebreaksbr",
    "|safe|ae2556_needs",
    '|default_if_none:"<d>"',
]
_LIST_FILTERS = [
    '|join:"<br>"',
    "|join:x",
    "|unordered_list",
    '|escapeseq|join:","',
    '|safeseq|join:","',
    "|safeseq|unordered_list",
]
_VALUE_KINDS = {
    "plain": lambda: HOSTILE,
    "mark_safe": lambda: mark_safe("<i>&</i>"),
    "format_html": lambda: format_html("<em>{}</em>", "<x>&"),
    "newlines": lambda: "<a>\n&\n\nc",
    "url": lambda: "see http://example.com/?a=<x>&b=1 now",
}


def _random_case(rng: random.Random) -> tuple[str, dict]:
    kind = rng.choice(sorted(_VALUE_KINDS))
    ctx = {
        "x": _VALUE_KINDS[kind](),
        "s": mark_safe("<i>&</i>"),
        "l": rng.choice([[HOSTILE, "&"], [mark_safe("<u>"), mark_safe("<v>&")]]),
        "xs": [1, 2],
        "first": "&",
        "var1": "&",
    }
    shape = rng.choice(
        ["var", "var", "list", "cycle", "firstof", "tag", "include_only", "spaceless"]
    )
    if shape == "var":
        body = "{{ x" + rng.choice(_FILTER_NAMES) + " }}"
    elif shape == "list":
        body = "{{ l" + rng.choice(_LIST_FILTERS) + " }}"
    elif shape == "cycle":
        body = "{% for i in xs %}{% cycle x s %}{% endfor %}"
    elif shape == "firstof":
        body = "{% firstof x s %}"
    elif shape == "tag":
        body = "{% ae2556_tag x %}"
    elif shape == "include_only":
        body = '{% include "inc.html" with first=x only %}'
    else:
        body = "{% spaceless %}<b> <i>{{ x }}</i> </b>{% endspaceless %}"
    # 0–2 levels of wrapping, each on or off, plus text either side.
    depth = rng.randint(0, 2)
    src = body
    for _ in range(depth):
        src = rng.choice([_off, _on])("<" + src + ">")
    if rng.random() < 0.5:
        src = "{{ x }}" + src + "{{ s }}"
    return src, ctx


class TestRandomizedDifferential:
    @pytest.mark.parametrize("seed", range(4))
    def test_grammar_sweep_agrees_with_django(self, engines, seed):
        rng = random.Random(2556 + seed)
        cases = [_random_case(rng) for _ in range(100)]
        assert len(cases) == 100
        for src, ctx in cases:
            expected = django_render(engines, src, ctx)
            for path_name, render in PATHS:
                assert render(engines, src, ctx) == expected, (path_name, src, ctx["x"])

    def test_the_sweep_is_not_vacuous(self, engines):
        """Gate-off of the harness itself: the cases must exercise `off` AND
        must produce raw hostile bytes somewhere, or the equality proves nothing."""
        rng = random.Random(1)
        cases = [_random_case(rng) for _ in range(300)]
        offs = [c for c in cases if "autoescape off" in c[0]]
        assert len(offs) > 60
        raw_hits = sum(HOSTILE in django_render(engines, src, ctx) for src, ctx in offs)
        assert raw_hits > 10


# ---------------------------------------------------------------------------
# §2.4 — security pins
# ---------------------------------------------------------------------------


def _production_lines(src: str) -> list[str]:
    """Source lines with comments dropped and `#[cfg(test)]` modules cut off."""
    body = src.split("#[cfg(test)]", 1)[0]
    return [
        line
        for line in body.splitlines()
        if line.strip() and not line.strip().startswith(("//", "///", "//!"))
    ]


class TestSecurityPins:
    def test_only_explicit_render_policy_and_lexical_scopes_write_autoescape(self):
        """Only explicit API policy, lexical scopes, and include-only copies.
        Context dictionary keys must never configure the render policy."""
        writers: list[tuple[str, str]] = []
        for rs in sorted(CRATES.glob("*/src/**/*.rs")):
            for line in _production_lines(rs.read_text(encoding="utf-8")):
                if "set_autoescape(" in line and "pub fn set_autoescape" not in line:
                    writers.append((rs.relative_to(CRATES).as_posix(), line.strip()))
        assert sorted(writers) == [
            ("djust_live/src/lib.rs", "ctx.set_autoescape(autoescape);"),
            ("djust_live/src/lib.rs", "ctx.set_autoescape(autoescape);"),
            ("djust_templates/src/renderer.rs", "context.set_autoescape(*on);"),
            ("djust_templates/src/renderer.rs", "context.set_autoescape(previous);"),
            ("djust_templates/src/renderer.rs", "fresh.set_autoescape(context.autoescape());"),
        ], writers

    def test_the_flag_is_not_a_term_of_the_safety_grant(self):
        src = RENDERER_RS.read_text(encoding="utf-8")
        for fn in ("filter_output_is_safe", "filter_output_items_are_safe"):
            body = src.split(f"fn {fn}(", 1)[1].split("\n}\n", 1)[0]
            assert "autoescape" not in body, fn
        # The static grant tables do not mention it either.
        for table in ("SAFE_OUTPUT_FILTERS", "ITEM_SAFE_OUTPUT_FILTERS", "IS_SAFE_FILTERS"):
            decl = src.split(f"{table}:", 1)[1].split("];", 1)[0]
            assert "autoescape" not in decl, table
        # `input_was_safe` is captured from `runtime_safe` alone, at every site.
        assert re.findall(r"let input_was_safe = (\w+);", src) == ["runtime_safe"] * 3

    def test_only_plain_render_pyfunctions_expose_explicit_policy(self):
        src = (CRATES / "djust_live" / "src" / "lib.rs").read_text(encoding="utf-8")
        exposed = re.findall(r"fn (\w+)\([^)]*\bautoescape\b[^)]*\)", src)
        assert sorted(exposed) == ["render_template", "render_template_with_dirs"]
        signatures = re.findall(r"signature\s*=\s*\(([^)]*autoescape[^)]*)\)", src)
        assert len(signatures) == 2
        assert all("*, autoescape=true" in signature for signature in signatures)
        assert not hasattr(_rust.RustLiveView, "set_autoescape")

    def test_a_context_key_named_autoescape_has_no_effect(self, engines):
        ctx = {"autoescape": False, "x": HOSTILE}
        for path_name, render in PATHS:
            assert render(engines, "{{ x }}", ctx) == "&lt;b&gt;&amp;&quot;&#x27;x", path_name
            assert render(engines, "{{ x|linebreaksbr }}", ctx) == "&lt;b&gt;&amp;&quot;&#x27;x"
        # Django agrees — the key is just data there too.
        assert django_render(engines, "{{ x }}", ctx) == "&lt;b&gt;&amp;&quot;&#x27;x"

    def test_off_never_upgrades_the_safety_a_later_filter_reads(self, engines):
        """`{{ x|safe|add:first }}` is UNSAFE after `add` (Django's `add` is
        `is_safe=False` and a plain-`str` argument yields a plain `str`, so
        the chain re-taints — #2274). Under `off` it emits raw because the
        flag is off; under `on` it is escaped — the policy is per emit, not
        per value, and `off` never marks anything safe."""
        chain = "{{ x|safe|add:first }}"
        assert djust_raw(engines, _off(chain), CTX) == django_render(engines, _off(chain), CTX)
        assert djust_raw(engines, _off(chain), CTX) == HOSTILE + "&"
        assert djust_raw(engines, _on(chain), CTX) == django_render(engines, _on(chain), CTX)
        assert djust_raw(engines, _on(chain), CTX) == "&lt;b&gt;&amp;&quot;&#x27;x&amp;"
        # An `off` block cannot grant safety to a sibling emit outside it.
        assert djust_raw(engines, _off("{{ x }}") + "{{ x }}", CTX) == (
            HOSTILE + "&lt;b&gt;&amp;&quot;&#x27;x"
        )

    def test_default_policy_is_byte_identical_to_the_2548_contract(self, engines):
        """The #2548 rule outside any block: a custom `is_safe=True` filter on
        hostile input is escaped, on a safe input it is not."""
        assert djust_raw(engines, "{{ x|ae2556_shout }}", CTX) == "&lt;B&gt;&amp;&quot;&#x27;X"
        assert djust_raw(engines, "{{ s|ae2556_shout }}", CTX) == "<I>&</I>"
        assert (
            django_render(engines, "{{ x|ae2556_shout }}|{{ s|ae2556_shout }}", CTX)
            == "&lt;B&gt;&amp;&quot;&#x27;X|<I>&</I>"
        )

    def test_context_default_is_on(self):
        src = CONTEXT_RS.read_text(encoding="utf-8")
        assert src.count("autoescape: true,") == 2, "new() and from_dict() must default to escaping"


# ---------------------------------------------------------------------------
# Every children-walker a new block node must join (#1104 / #1646 count pin)
# ---------------------------------------------------------------------------


class TestEveryWalkerKnowsTheNewNode:
    def test_autoescape_appears_wherever_spaceless_does(self):
        """`Node::Spaceless` is the simplest existing block node; every match
        site that recurses into its children must recurse into `AutoEscape`
        too, or a `{% block %}`/`{% if %}`/loop-var inside the block is
        silently missed. Counted per file, production code only."""
        for rs in sorted((CRATES / "djust_templates" / "src").glob("*.rs")):
            lines = _production_lines(rs.read_text(encoding="utf-8"))
            spaceless = sum("Node::Spaceless" in line for line in lines)
            autoescape = sum("Node::AutoEscape" in line for line in lines)
            # The shared inheritance child-list inventory now covers both.
            assert autoescape == spaceless, (rs.name, spaceless, autoescape)

    def test_the_generated_backend_list_names_the_tag(self):
        doc = (REPO / "docs" / "TEMPLATE_BACKEND.md").read_text(encoding="utf-8")
        block = doc.split("<!-- generated:template-backend-lists -->", 1)[1].split(
            "<!-- /generated:template-backend-lists -->", 1
        )[0]
        native = next(line for line in block.splitlines() if line.startswith("- native Rust"))
        assert "`autoescape`" in native
        assert "endautoescape" not in block
        unsupported = next(
            line for line in block.splitlines() if "unsupported (" in line and "tags" in line
        )
        assert "`autoescape`" not in unsupported


# ---------------------------------------------------------------------------
# The `autoescape=` kwarg is OPT-IN (#2556 x #2563, PR #2595)
# ---------------------------------------------------------------------------
#
# `{% url %}`'s `UrlTagHandler` declares `RETURNS_BINDINGS` (#2563) and its
# `render` signature is `(args, context)`. The first version of the bridge fix
# handed the `{% autoescape %}` policy to EVERY bindings handler as an
# `autoescape=` keyword, which is a `TypeError` for any handler that does not
# name the parameter — so `{% url %}` did not render *at all*, in every mode,
# not merely with the wrong escaping under `off`. The kwarg is therefore a
# declared opt-in, `WANTS_AUTOESCAPE`, read at registration beside
# `RETURNS_BINDINGS` / `ACCEPTS_AS_VAR` / `RESOLVE_ARG_POSITIONS`.


def _view(_request, **_kwargs):
    return HttpResponse("")


urlpatterns = [path("u/<str:v>/", _view, name="ae2556_u")]


class _RecordingBindingsHandler:
    """Accepts the kwarg but does not declare it — so it must NOT arrive."""

    RETURNS_BINDINGS = True

    def __init__(self):
        self.seen = []

    def render(self, args, context, **kwargs):
        self.seen.append(dict(kwargs))
        return ("[" + str(args[0]) + "]", {})


class _DeclaringBindingsHandler(_RecordingBindingsHandler):
    """Declares it — so it must arrive, carrying the block's policy."""

    WANTS_AUTOESCAPE = True


class TestTheKwargIsOptIn:
    """Both arms of the opt-in are independently reachable (#2129/#2135): a
    handler that declares the flag is called WITH the keyword, one that does
    not is called WITHOUT it. Neither test can pass for the other's reason —
    they assert on opposite contents of the same recorded call."""

    @pytest.fixture
    def declaring(self):
        h = _DeclaringBindingsHandler()
        _rust.register_tag_handler("ae2556_wants", h)
        yield h
        _rust.unregister_tag_handler("ae2556_wants")

    @pytest.fixture
    def silent(self):
        h = _RecordingBindingsHandler()
        _rust.register_tag_handler("ae2556_silent", h)
        yield h
        _rust.unregister_tag_handler("ae2556_silent")

    @pytest.mark.parametrize(
        "body,on", [("{% autoescape on %}", True), ("{% autoescape off %}", False)]
    )
    def test_a_declaring_handler_receives_the_block_policy(self, declaring, body, on):
        _rust.render_template(body + "{% ae2556_wants 'x' %}{% endautoescape %}", {})
        assert declaring.seen == [{"autoescape": on}]

    def test_a_handler_that_did_not_declare_it_is_called_without_the_keyword(self, silent):
        """The `{% url %}` shape. Were the kwarg unconditional this would not
        merely record it — a handler whose signature does not absorb `**kwargs`
        raises `TypeError` on EVERY render, which is what the merge of #2607
        into this branch did to `{% url %}`."""
        _rust.render_template("{% autoescape off %}{% ae2556_silent 'x' %}{% endautoescape %}", {})
        assert silent.seen == [{}]


class TestUrlIsDjangoEqualUnderEveryMode:
    """The regression the opt-in exists for, on the real tag rather than a
    probe. A value that survives `reverse()` AND carries an escapable
    character is what the divergence needs — `<script>` raises
    `NoReverseMatch` and `%3Cscript%3E` has nothing to escape."""

    @pytest.mark.parametrize(
        "wrap,expected",
        [
            ("%s", "/u/a&amp;b/"),
            ("{%% autoescape on %%}%s{%% endautoescape %%}", "/u/a&amp;b/"),
            ("{%% autoescape off %%}%s{%% endautoescape %%}", "/u/a&b/"),
        ],
    )
    def test_matches_django(self, engines, wrap, expected):
        src = wrap % "{% url 'ae2556_u' v %}"
        ctx = {"v": "a&b"}
        with override_settings(ROOT_URLCONF=__name__):
            assert django_render(engines, src, ctx) == expected
            assert djust_raw(engines, src, ctx) == expected
            assert djust_live(engines, src, ctx) == expected


# ---------------------------------------------------------------------------
# `{% cycle %}` state inside an `{% autoescape %}` body (PR #2595)
# ---------------------------------------------------------------------------


class TestCycleStateSurvivesTheBlock:
    """`resolve_cycle_nodes` assigns each `{% cycle %}` node its per-render
    state id by walking the tree. It listed every other block node but not
    `Node::AutoEscape`, so two cycles inside an `{% autoescape %}` body kept
    NO id and collided on one shared counter — a merge artefact of #2596's
    cycle rewrite meeting this branch's node, invisible to the compiler."""

    @pytest.mark.parametrize("mode", ["on", "off"])
    def test_two_cycles_in_one_block_advance_independently(self, engines, mode):
        body = "{% for i in xs %}{% cycle 'a' 'b' %}{% cycle '1' '2' '3' %}{% endfor %}"
        src = "{%% autoescape %s %%}%s{%% endautoescape %%}" % (mode, body)
        ctx = {"xs": [0, 1, 2, 3]}
        expected = django_render(engines, src, ctx)
        assert expected == "a1b2a3b1"
        for name, render in PATHS:
            assert render(engines, src, ctx) == expected, name

    def test_a_cycle_inside_the_block_does_not_share_one_outside_it(self, engines):
        src = (
            "{% for i in xs %}{% cycle 'a' 'b' %}"
            "{% autoescape off %}{% cycle '1' '2' '3' %}{% endautoescape %}"
            "{% endfor %}"
        )
        ctx = {"xs": [0, 1, 2, 3]}
        expected = django_render(engines, src, ctx)
        assert expected == "a1b2a3b1"
        for name, render in PATHS:
            assert render(engines, src, ctx) == expected, name
