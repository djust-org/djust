"""Regression: a custom filter's ``is_safe=True`` must not mark UNSAFE input safe (#2548).

Django's rule, ``django/template/base.py`` ``FilterExpression.resolve``::

    new_obj = func(obj, *arg_vals)
    if getattr(func, "is_safe", False) and isinstance(obj, SafeData):
        obj = mark_safe(new_obj)

where ``obj`` is the filter's INPUT. ``is_safe=True`` means "a safe input stays
safe through this filter" — it never makes the filter's own output safe. The
Rust renderer applied that two-term rule to the BUILT-IN ``is_safe`` list
(#2274) but granted a registered custom filter's flag unconditionally, so::

    @register.filter(is_safe=True)
    def shout(v):
        return v.upper()

    {{ hostile|shout }}    Django  &lt;B&gt;X&lt;/B&gt;    djust  <B>X</B>   <-- #2548

for every plain-return ``is_safe=True`` project filter, and for
``django.contrib.humanize``'s ``intcomma``/``apnumber`` (both return non-numeric
input unchanged) without the project writing a filter at all. The bridge (#1121)
walks every ``DjangoTemplates`` engine's libraries at ``DjustConfig.ready()``, so
a filter is reachable whether or not a template ``{% load %}``s it.

The fix folds the custom-filter term into the same ``input_was_safe &&``
conjunction the built-in term already lives in — ONE sink,
``renderer::filter_output_is_safe``, consulted by all three render arms.

Every assertion below compares against **live Django** rendered in-process, and
the direction that matters is asserted separately through ``capabilities()``:
the output must grant no live tag or event handler. The "grant still flows"
group is the non-tautology proof — the fix is Django's rule, not "escape
everything a custom filter touches".
"""

from __future__ import annotations

import re
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

from test_safe_survives_is_safe_filter_2274 import capabilities  # noqa: E402

# Un-guessable names: the Rust filter registry is process-global and is NOT
# cleared by ``reset_djust_globals``, so a collision with another module's
# filter would be a silent cross-test leak rather than an error.
SHOUT = "_dj2548_shout"
SHOUT_SF = "_dj2548_shout_sf"
WRAP_B = "_dj2548_wrap_b"
UNQ = "_dj2548_unq"
PLAIN_UPPER = "_dj2548_plain_upper"
AE_PROBE = "_dj2548_ae_probe"
AE_CANON = "_dj2548_ae_canon"
RET_SAFE = "_dj2548_ret_safe"

_library = template.Library()


@_library.filter(name=SHOUT, is_safe=True)
def _shout(value):
    """The issue's filter: ``is_safe=True``, plain ``str`` return."""
    return value.upper()


@_library.filter(name=SHOUT_SF, is_safe=True)
@stringfilter
def _shout_sf(value):
    """The same through ``@stringfilter`` — Django's second copy of the rule
    (``defaultfilters.py`` ``isinstance(first, SafeData) and is_safe``)."""
    return value.upper()


@_library.filter(name=WRAP_B, is_safe=True)
def _wrap_b(value):
    """``is_safe=True`` and interpolates the input into markup, plain return —
    the shape Django's own docs use for the flag (``add_xx``)."""
    return "<b>%s</b>" % value


@_library.filter(name=UNQ, is_safe=True)
def _unq(value):
    """``is_safe=True`` on a DECODING filter: the encoded-bypass canary.
    ``%3Cscript%3E`` carries no markup until this filter decodes it."""
    return unquote(value)


@_library.filter(name=PLAIN_UPPER)
def _plain_upper(value):
    """``is_safe=False`` control — same body as ``shout``."""
    return value.upper()


@_library.filter(name=AE_PROBE, needs_autoescape=True)
def _ae_probe(value, autoescape=True):
    """``needs_autoescape``, ``is_safe=False``, plain return (#2290 neighbour)."""
    return "[%s|%s]%s" % (autoescape, type(value).__name__, value)


@_library.filter(name=AE_CANON, is_safe=True, needs_autoescape=True)
def _ae_canon(value, autoescape=True):
    """Django's canonical ``needs_autoescape`` shape (``linebreaks``-like):
    escapes internally and ``mark_safe``s its own output."""
    esc = conditional_escape if autoescape else (lambda x: x)
    return mark_safe("<p>%s</p>" % esc(value))


@_library.filter(name=RET_SAFE)
def _ret_safe(value):
    """``is_safe=False`` but returns a runtime ``SafeString`` (#1660)."""
    return mark_safe("<em>%s</em>" % value)


@pytest.fixture(scope="module", autouse=True)
def _registered():
    """Register with Rust exactly as the #1121 bridge does (flags read off the
    callable), and tear down. The Django side is a private ``Engine`` with the
    library in ``template_builtins`` so no ``{% load %}`` is needed and nothing
    touches the project-wide registry."""
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
    {"NAME": "djust_2548", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}}
)


# ---------------------------------------------------------------------------
# The four render entries: Django, and the three djust paths a project reaches
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
    """The ``_rust`` entry the plain backend calls, with the ``safe_keys``
    channel a view's ``mark_safe`` values travel through."""
    normalized, safe_keys = _normalized_with_safe_keys(ctx)
    return _rust.render_template_with_dirs(source, normalized, [], safe_keys)


def djust_backend(source: str, ctx: dict) -> str:
    """``DjustTemplateBackend`` — what a Django ``TEMPLATES`` entry renders."""
    return str(_backend.from_string(source).render(dict(ctx)))


def djust_live(source: str, ctx: dict) -> str:
    """``RustLiveView`` — the LiveView path, via ``mark_safe_keys`` (#2287)."""
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
# 1. The reported row — three djust paths vs Django
# ---------------------------------------------------------------------------


REPORTED = [
    ("shout", "{{ h|%s }}" % SHOUT, {"h": H}),
    ("shout_sf", "{{ h|%s }}" % SHOUT_SF, {"h": H}),
    ("wrap_b", "{{ h|%s }}" % WRAP_B, {"h": H}),
    ("unq", "{{ h|%s }}" % UNQ, {"h": "%3Cscript%3Ealert(1)%3C/script%3E"}),
]
REPORTED_IDS = [r[0] for r in REPORTED]


class TestTheReportedRow:
    """``{{ hostile|is_safe_filter }}`` is escaped — on every path, like Django."""

    @pytest.mark.parametrize(("label", "source", "ctx"), REPORTED, ids=REPORTED_IDS)
    @pytest.mark.parametrize(("path", "render"), PATHS, ids=PATH_IDS)
    def test_unsafe_input_through_an_is_safe_filter_is_escaped(
        self, label, source, ctx, path, render
    ) -> None:
        expected = django_render(source, ctx)
        actual = render(source, ctx)
        assert actual == expected, f"{path}/{label}: django={expected!r} djust={actual!r}"
        assert capabilities(actual) == set(), f"{path}/{label} LIVE: {actual!r}"

    @pytest.mark.parametrize(("label", "source", "ctx"), REPORTED, ids=REPORTED_IDS)
    def test_the_django_reference_actually_escaped(self, label, source, ctx) -> None:
        """The differential is only worth something if Django's side is the
        escaped one. It is — ``&lt;`` in, no live tag out."""
        expected = django_render(source, ctx)
        assert "&lt;" in expected, expected
        assert capabilities(expected) == set(), expected

    def test_the_decoder_row_was_a_live_script_element(self) -> None:
        """The v1.0.6 encoded-canary shape, stated in the affirmative: the
        input carries no markup at all, the ``is_safe=True`` decoder creates
        it, and the flag must not launder that into a live ``<script>``."""
        out = djust_live("{{ h|%s }}" % UNQ, {"h": "%3Cscript%3Ealert(1)%3C/script%3E"})
        assert "tag:script" not in capabilities(out), out
        assert "&lt;script&gt;" in out, out


# ---------------------------------------------------------------------------
# 2. The grant still flows when it is EARNED — the non-tautology proof
# ---------------------------------------------------------------------------


class TestTheGrantStillFlowsForSafeInput:
    """What ``is_safe=True`` DOES do: a ``SafeData`` input stays safe through
    the filter, so the markup comes out raw. Each case must be raw on every
    djust path AND equal to Django — a fix that escaped everything a custom
    filter touched would fail all three."""

    @pytest.mark.parametrize(("path", "render"), PATHS, ids=PATH_IDS)
    def test_safe_then_is_safe_filter(self, path, render) -> None:
        source = "{{ h|safe|%s }}" % SHOUT
        expected = django_render(source, {"h": H})
        assert expected == "<B>X</B>"
        assert render(source, {"h": H}) == expected, path

    @pytest.mark.parametrize(("path", "render"), PATHS, ids=PATH_IDS)
    def test_mark_safe_context_value_then_is_safe_filter(self, path, render) -> None:
        """``mark_safe`` in the context — ``safe_keys`` on the raw and backend
        paths, ``mark_safe_keys`` on the live path — is the input term."""
        source = "{{ s|%s }}" % SHOUT
        ctx = {"s": mark_safe(H)}
        expected = django_render(source, ctx)
        assert expected == "<B>X</B>"
        assert render(source, ctx) == expected, path

    @pytest.mark.parametrize(("path", "render"), PATHS, ids=PATH_IDS)
    def test_safe_flows_through_a_custom_then_a_built_in_is_safe_filter(self, path, render) -> None:
        """``|safe|shout|lower``: the grant survives BOTH ``is_safe`` kinds in a
        row — the folded arm answers for each."""
        source = "{{ h|safe|%s|lower }}" % SHOUT
        expected = django_render(source, {"h": H})
        assert expected == "<b>x</b>"
        assert render(source, {"h": H}) == expected, path

    def test_the_control_is_not_the_same_bytes_as_the_reported_row(self) -> None:
        """``|safe|shout`` and ``|shout`` must DIFFER, or group 1 proves nothing."""
        assert djust_raw("{{ h|safe|%s }}" % SHOUT, {"h": H}) != djust_raw(
            "{{ h|%s }}" % SHOUT, {"h": H}
        )


# ---------------------------------------------------------------------------
# 3. Chain position — the check runs per filter, and the INPUT feeds forward
# ---------------------------------------------------------------------------


class TestChainPosition:
    @pytest.mark.parametrize(
        "source",
        [
            "{{ h|%s|lower }}" % SHOUT,
            "{{ h|lower|%s }}" % SHOUT,
            "{{ h|%s|escape }}" % SHOUT,
        ],
        ids=["shout-then-lower", "lower-then-shout", "shout-then-escape"],
    )
    def test_an_unearned_grant_does_not_flow_to_the_next_filter(self, source) -> None:
        """Before the fix ``lower`` and ``escape`` (``conditional_escape``,
        #2281) inherited ``shout``'s unearned grant. Neither link ever receives
        ``SafeData``, so Django escapes; so must djust."""
        out = assert_all_paths_agree_with_django(source, {"h": H})
        assert capabilities(out) == set(), out
        assert "&lt;" in out, out

    def test_a_trailing_safe_is_still_honoured(self) -> None:
        out = assert_all_paths_agree_with_django("{{ h|%s|safe }}" % SHOUT, {"h": H})
        assert out == "<B>X</B>"


# ---------------------------------------------------------------------------
# 4. Untouched neighbours — the other grants, before and after
# ---------------------------------------------------------------------------


class TestUntouchedNeighbours:
    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("{{ h|%s }}" % RET_SAFE, "<em><b>x</b></em>"),
            ("{{ h|%s }}" % AE_CANON, "<p>&lt;b&gt;x&lt;/b&gt;</p>"),
            ("{{ h|%s }}" % PLAIN_UPPER, "&lt;B&gt;X&lt;/B&gt;"),
            ("{{ h|%s }}" % AE_PROBE, "[True|str]&lt;b&gt;x&lt;/b&gt;"),
            ("{{ h|safe|%s }}" % AE_PROBE, "[True|SafeString]&lt;b&gt;x&lt;/b&gt;"),
        ],
        ids=[
            "ret_safe-1660",
            "ae_canon",
            "plain_upper-control",
            "ae_probe-2290",
            "safe-ae_probe-2290",
        ],
    )
    def test_neighbouring_grants_are_unchanged(self, source, expected) -> None:
        """A runtime ``SafeString`` return (#1660) still earns the grant with
        or without the flag; ``needs_autoescape`` (#2284/#2290) is its own
        channel; the ``is_safe=False`` control escapes. ``expected`` is a
        transcription of Django's bytes, pinned so the reference cannot drift
        silently either."""
        out = assert_all_paths_agree_with_django(source, {"h": H})
        assert out == expected


# ---------------------------------------------------------------------------
# 5. All three renderer arms consult the same helper
# ---------------------------------------------------------------------------


class TestAllThreeRendererArms:
    """``Node::Variable`` (group 1), ``get_value_safe`` (tag operands) and the
    ``{% with %}`` binding, plus ``Node::InlineIf``."""

    @pytest.mark.parametrize(
        ("source", "ctx"),
        [
            ("{%% firstof h|%s %%}" % SHOUT, {"h": H}),
            ("{%% with q=h|%s %%}{{ q }}{%% endwith %%}" % SHOUT, {"h": H}),
            ("{%% for i in items %%}{{ i|%s }}{%% endfor %%}" % SHOUT, {"items": [H]}),
        ],
        ids=["firstof", "with-binding", "for-loop-item"],
    )
    def test_tag_operand_and_binding_arms_escape(self, source, ctx) -> None:
        out = assert_all_paths_agree_with_django(source, ctx)
        assert out == "&lt;B&gt;X&lt;/B&gt;"
        assert capabilities(out) == set(), out

    @pytest.mark.parametrize(("path", "render"), PATHS, ids=PATH_IDS)
    def test_inline_if_arm_escapes(self, path, render) -> None:
        """``{{ x if c else y|filter }}`` is Rust-only syntax (the filters
        apply to the chosen branch), so there is no Django row; it must render
        the same bytes as the plain ``{{ h|shout }}`` Django reference and
        grant nothing."""
        expected = django_render("{{ h|%s }}" % SHOUT, {"h": H})
        out = render('{{ h if f else ""|%s }}' % SHOUT, {"h": H, "f": True})
        assert out == expected, f"{path}: {out!r}"
        assert capabilities(out) == set(), out


# ---------------------------------------------------------------------------
# 6. Encoded / alternate-representation rows (the v1.0.6 review rule)
# ---------------------------------------------------------------------------


ENCODED = [
    ("already-escaped", "{{ h|%s }}" % SHOUT, {"h": "&lt;b&gt;x&lt;/b&gt;"}),
    ("fullwidth-brackets", "{{ h|%s }}" % SHOUT, {"h": "<ｂ>x</ｂ>"}),
    ("self-closing-img-onerror", "{{ h|%s }}" % SHOUT, {"h": "<img/src=x onerror=alert(1)>"}),
    ("comment-closer", "<!-- {{ h|%s }} -->" % SHOUT, {"h": "--><script>1</script><!--"}),
    (
        "attr-double-quote-breakout",
        '<a title="{{ h|%s }}">' % SHOUT,
        {"h": 'x" onmouseover="alert(1)'},
    ),
    (
        "attr-single-quote-breakout",
        "<a title='{{ h|%s }}'>" % SHOUT,
        {"h": "x' onmouseover='alert(1)"},
    ),
]
ENCODED_IDS = [e[0] for e in ENCODED]


class TestEncodedAndAlternateRepresentations:
    @pytest.mark.parametrize(("label", "source", "ctx"), ENCODED, ids=ENCODED_IDS)
    @pytest.mark.parametrize(("path", "render"), PATHS, ids=PATH_IDS)
    def test_django_equal_and_nothing_live(self, label, source, ctx, path, render) -> None:
        expected = django_render(source, ctx)
        actual = render(source, ctx)
        assert actual == expected, f"{path}/{label}: django={expected!r} djust={actual!r}"
        caps = capabilities(actual)
        assert not any(c.startswith("evt:") for c in caps), f"{path}/{label} LIVE: {actual!r}"
        assert "tag:script" not in caps, f"{path}/{label} LIVE: {actual!r}"

    @pytest.mark.parametrize(("path", "render"), PATHS, ids=PATH_IDS)
    def test_javascript_url_scheme_is_django_equal_and_out_of_scope(self, path, render) -> None:
        """``href="{{ h|shout }}"`` with ``javascript:alert(1)``: Django renders
        ``JAVASCRIPT:ALERT(1)`` too — neither engine escapes ``:``. This fix is
        about the escaping grant, not URL schemes (the GHSA-4mf4 class); the row
        pins Django-equality so a future change in either direction is seen."""
        source = '<a href="{{ h|%s }}">x</a>' % SHOUT
        ctx = {"h": "javascript:alert(1)"}
        expected = django_render(source, ctx)
        assert expected == '<a href="JAVASCRIPT:ALERT(1)">x</a>'
        assert render(source, ctx) == expected, path


# ---------------------------------------------------------------------------
# 7. Source pins — the invariant, mechanically
# ---------------------------------------------------------------------------


_TEMPLATES_SRC = Path(__file__).resolve().parents[2] / "crates" / "djust_templates" / "src"


def _helper_body_without_comments() -> str:
    src = (_TEMPLATES_SRC / "renderer.rs").read_text()
    fn = src.split("fn filter_output_is_safe(", 1)[1].split("\n}\n", 1)[0]
    return "\n".join(ln for ln in fn.splitlines() if not ln.strip().startswith("//"))


def test_the_custom_filter_term_is_gated_on_the_input_being_safe() -> None:
    """``is_custom_filter_safe(`` may appear in ``filter_output_is_safe`` ONLY
    inside a parenthesised group that opens with ``input_was_safe &&``.

    Walks back from the call over balanced parentheses to the enclosing
    groups and requires one of them to be the ``input_was_safe`` conjunction
    — a bare ``|| is_custom_filter_safe(name)`` arm (the #2548 shape) has no
    such enclosing group and fails here before any template renders.
    """
    code = re.sub(r"\s+", " ", _helper_body_without_comments())
    calls = [m.start() for m in re.finditer(r"is_custom_filter_safe\(", code)]
    assert len(calls) == 1, code
    pos = calls[0]
    depth = 0
    gated = False
    for i in range(pos - 1, -1, -1):
        ch = code[i]
        if ch == ")":
            depth += 1
        elif ch == "(":
            if depth == 0:
                if code[i + 1 :].lstrip().startswith("input_was_safe &&"):
                    gated = True
                    group_open = i
                    break
            else:
                depth -= 1
    assert gated, f"is_custom_filter_safe is not inside an `input_was_safe &&` group:\n{code}"
    # `&&` binds tighter than `||`, so `(input_was_safe && A || custom(...))`
    # opens with the conjunction yet leaves the custom term ungated. Require
    # that no `||` sits at depth 0 between the group's opening and the call.
    inner = code[group_open + 1 : pos]
    depth = 0
    for j, ch in enumerate(inner):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0 and inner.startswith("||", j):
            raise AssertionError(
                f"a depth-0 `||` inside the `input_was_safe &&` group leaves the custom term ungated:\n{code}"
            )


def test_the_helper_has_exactly_two_unconditional_terms() -> None:
    """After #2548 the only terms outside ``input_was_safe &&`` are the two that
    EARN the grant: a runtime ``SafeString`` and an internally-escaping
    built-in. A third unconditional term is the bug class coming back."""
    code = re.sub(r"\s+", " ", _helper_body_without_comments())
    body = code.split("{", 1)[1]
    # Top-level `||` arms: split on `||` at paren depth 0.
    arms, depth, cur = [], 0, ""
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if depth == 0 and body.startswith("||", i):
            arms.append(cur.strip())
            cur = ""
            i += 2
            continue
        cur += ch
        i += 1
    arms.append(cur.strip().rstrip("}").strip())
    unconditional = [a for a in arms if not a.startswith("(input_was_safe &&")]
    assert unconditional == ["produced_safe", "SAFE_OUTPUT_FILTERS.contains(&filter_name)"], arms


def test_is_custom_filter_safe_has_exactly_one_production_call_site() -> None:
    """The pin is only as good as the sink being the only reader."""
    hits = []
    for path in sorted(_TEMPLATES_SRC.rglob("*.rs")):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("//"):
                continue
            if "fn is_custom_filter_safe(" in stripped:
                continue
            if "is_custom_filter_safe(" in stripped:
                hits.append(f"{path.name}:{lineno}")
    assert len(hits) == 1 and hits[0].startswith("renderer.rs:"), hits
