"""A custom tag handler's ARGUMENT keeps its ``SafeData`` marker, and a quoted
literal loses its quotes (#2416).

The two divergences
-------------------
Django's ``SimpleNode.render`` compiles each operand with
``parser.compile_filter(bit)`` and resolves it with
``FilterExpression.resolve(context)``, handing the handler the resolved
**object**. djust flattened every operand to a ``String`` through
``value_to_arg_string``, which lost two things Django keeps:

1. **The ``SafeData`` marker.** ``{% ct_cond p %}`` over
   ``p = mark_safe("<img …>")`` — a handler whose body is the ordinary
   defensive ``conditional_escape(value)`` — is a no-op in Django and the
   markup renders. djust handed it a bare ``str``, so the handler's own escape
   fired. #2290's finding on the ARGUMENT side of the tag registry rather than
   the filter registry.
2. **A quoted literal's quotes.** ``Variable('"<b>"')`` ends with
   ``self.literal = mark_safe(unescape_string_literal(var))``, so the literal
   loses its surrounding quotes AND arrives as ``SafeData``. djust passed the
   token verbatim, so ``{% ct_ident "<b>" %}`` handed the handler the five
   characters ``"<b>"``. Not only a markup problem: ``{% t "post" %}`` handed
   the handler ``"post"`` WITH the quotes, where Django hands it ``post``.

Both were MASKED before #2379: the marker was lost on the way in, the bridge
emitted the return raw on the way out, and the two wrongs cancelled — djust
matched Django's live output for the wrong reason. Neither is a regression from
#2379; #2379 is what made them visible.

The direction, and why the new grants are safe
----------------------------------------------
Every change here moves in the LESS-escaping direction, on the path where a
live XSS lived (#2379), so :class:`TestTheGrantDoesNotWiden` states exactly
which values become live and asserts the boundary:

* a resolved operand is marked **only** when the resolver reports the value
  ``SafeData`` **and** the value is a ``Value::String``. That first bool is the
  same one that decides whether ``{{ p }}`` escapes, so the set of newly-live
  values is a SUBSET of what the primary output channel already renders live —
  if it contains attacker data, ``{{ p }}`` is already an XSS and this changes
  nothing about that;
* a quoted literal is the TEMPLATE AUTHOR's own source bytes, never context
  data — the same argument #2376 made for ``{{ "<b>" }}``, and Django's own;
* nothing else is marked: not an unmarked context string, not an ``int`` /
  ``float`` / ``bool`` / ``None``, not a list or dict (whose JSON encoding is
  structure the renderer synthesized rather than bytes anyone vouched for), not
  a ``key=value`` composite, and not an operand that failed to resolve.

Every expectation here is LIVE Django, never a transcription.
"""

from __future__ import annotations

import re

import pytest

pytest.importorskip("django")

import django  # noqa: E402
from django.conf import settings  # noqa: E402

if not settings.configured:  # pragma: no cover — import-time bootstrap
    settings.configure(
        DEBUG=False,
        USE_I18N=False,
        USE_TZ=False,
        TIME_ZONE="UTC",
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [],
                "APP_DIRS": False,
                "OPTIONS": {"context_processors": []},
            }
        ],
        INSTALLED_APPS=[],
    )
    django.setup()

from django import template as dj_template  # noqa: E402
from django.template import Context as DjangoContext  # noqa: E402
from django.template.base import Template as DjangoTemplate  # noqa: E402
from django.template.engine import Engine  # noqa: E402
from django.utils.html import conditional_escape  # noqa: E402
from django.utils.safestring import mark_safe  # noqa: E402

from djust import _rust  # noqa: E402
from djust.mixins.rust_bridge import _collect_safe_keys  # noqa: E402

XSS = "<img src=x onerror=alert(1)>"

#: An unescaped tag OPENER. Substring-matching the payload would also match
#: inside fully-escaped text, so "the payload appears" is not a leak test.
UNESCAPED_TAG = re.compile(r"<[a-zA-Z/!]")

_LIB = dj_template.Library()


@_LIB.simple_tag(name="a2416_ident")
def _ident(value):
    """Returns its argument untouched — the sharpest probe there is."""
    return value


@_LIB.simple_tag(name="a2416_cond")
def _cond(value):
    """``conditional_escape`` — reads the marker on the way IN.

    The ordinary defensive shape a tag handler is written with, and the one
    that is a NO-OP exactly when the argument carries ``SafeData``.
    """
    return conditional_escape(value)


@_LIB.simple_tag(name="a2416_probe")
def _probe(value):
    """Reports the argument's TYPE and marker rather than its content.

    Measured at the handler rather than inferred from the page: the output
    alone cannot tell "the bridge marked it" from "the handler marked it".
    """
    return f"[{type(value).__name__}|html={hasattr(value, '__html__')}|{value!r}]"


class _Adapter:
    """The ``.render(args, context)`` shape ``registry.rs`` calls a handler with."""

    def __init__(self, fn):
        self.fn = fn

    def render(self, args, *_rest):
        return self.fn(*args)


_TAGS = {"a2416_ident": _ident, "a2416_cond": _cond, "a2416_probe": _probe}


@pytest.fixture(scope="module", autouse=True)
def _probes():
    """Register on BOTH engines from one function body, and clean up after."""
    Engine.get_default().template_builtins.append(_LIB)
    for name, fn in _TAGS.items():
        _rust.register_tag_handler(name, _Adapter(fn))
    yield
    for name in _TAGS:
        _rust.unregister_tag_handler(name)
    Engine.get_default().template_builtins.remove(_LIB)


def both(source: str, ctx: dict | None = None) -> tuple[str, str]:
    """Render through Django and djust, with the context's safety grants.

    ``render_template`` has no ``safe_keys`` parameter, so the CONTEXT-safety
    channel has to go through ``render_template_with_dirs`` — the only Python
    entry point that carries it (#2287). The grants are DERIVED from the
    context with djust's own ``_collect_safe_keys`` rather than hand-listed, so
    a test cannot claim a grant the bridge would not have produced.
    """
    ctx = dict(ctx or {})
    try:
        dj = DjangoTemplate(source, engine=Engine.get_default()).render(DjangoContext(dict(ctx)))
    except Exception as exc:  # noqa: BLE001 — a raise is a comparable outcome
        dj = f"<<EXC {type(exc).__name__}>>"
    safe_keys: list[str] = []
    for key, value in ctx.items():
        safe_keys.extend(_collect_safe_keys(value, key))
    try:
        if safe_keys:
            du = _rust.render_template_with_dirs(source, dict(ctx), [], safe_keys)
        else:
            du = _rust.render_template(source, dict(ctx))
    except Exception as exc:  # noqa: BLE001
        du = f"<<EXC {type(exc).__name__}>>"
    return dj, du


# ---------------------------------------------------------------------------
# 1. The marker
# ---------------------------------------------------------------------------


class TestAMarkedContextValueArrivesAsSafeData:
    def test_the_issues_own_example(self) -> None:
        dj, du = both("{% a2416_cond p %}", {"p": mark_safe(XSS)})  # noqa: S308
        assert dj == XSS
        assert du == dj

    def test_the_identity_probe_agrees_too(self) -> None:
        """A handler that returns its argument untouched.

        Since #2379 the bridge escapes a plain-`str` return, so this cell needs
        the marker to survive the hop in BOTH directions: in, so the value is a
        `SafeString`; out, so `escape_handler_return` leaves it alone.
        """
        dj, du = both("{% a2416_ident p %}", {"p": mark_safe(XSS)})  # noqa: S308
        assert du == dj == XSS

    def test_the_handler_actually_receives_SafeData(self) -> None:
        """Measured at the handler, not inferred from the page."""
        dj, du = both("{% a2416_probe p %}", {"p": mark_safe("<b>")})  # noqa: S308
        assert "SafeString|html=True" in dj, dj
        assert du == dj

    @pytest.mark.parametrize(
        "source",
        [
            "{% a2416_cond p %}",  # a bare name
            "{% a2416_cond d.k %}",  # a dotted path
            "{% a2416_cond li.0 %}",  # a list index
            "{% a2416_cond p|lower %}",  # through an is_safe=True filter
        ],
    )
    def test_every_spelling_the_grant_travels(self, source: str) -> None:
        """The grant is a property of the RESOLVED value, so it must survive
        each way the resolver can reach one — `_collect_safe_keys` spells a
        nested mark positionally (`li.0`), and a filter chain re-derives it."""
        marked = mark_safe("<b>x</b>")  # noqa: S308
        dj, du = both(source, {"p": marked, "d": {"k": marked}, "li": [marked]})
        assert du == dj
        assert UNESCAPED_TAG.search(du), f"nothing live here, so the row proves nothing: {du!r}"

    def test_a_retainting_filter_still_escapes(self) -> None:
        """The other direction, or "the grant travels" would be satisfied by
        granting everything: `upper` is registered `is_safe=False` in Django
        precisely because upper-casing `&lt;` yields `&LT;`."""
        dj, du = both("{% a2416_cond p|upper %}", {"p": mark_safe("<b>x</b>")})  # noqa: S308
        assert du == dj
        assert not UNESCAPED_TAG.search(du), du


# ---------------------------------------------------------------------------
# 2. The quoted literal
# ---------------------------------------------------------------------------


class TestAQuotedLiteralLosesItsQuotesAndIsSafeData:
    @pytest.mark.parametrize("source", ['{% a2416_ident "<b>" %}', "{% a2416_ident '<b>' %}"])
    def test_both_quote_spellings_render_the_markup(self, source: str) -> None:
        dj, du = both(source)
        assert dj == "<b>"
        assert du == dj

    def test_a_literal_with_no_markup_loses_its_quotes(self) -> None:
        """The half nothing could see while every literal cell carried markup:
        `{% t "post" %}` handed the handler `"post"` WITH the quotes, and the
        page said `&quot;post&quot;`. Django hands it `post`."""
        dj, du = both('{% a2416_ident "post" %}')
        assert dj == "post"
        assert du == dj

    def test_the_handler_receives_the_unquoted_SafeString(self) -> None:
        dj, du = both('{% a2416_probe "ab" %}')
        assert "SafeString|html=True" in dj and "&#x27;ab&#x27;" in dj, dj
        assert du == dj

    def test_a_literal_containing_an_equals_is_not_torn_into_a_kwarg(self) -> None:
        """The literal test runs BEFORE the `key=value` split, as it did
        before this change. `{% t "a=b" %}` is one quoted literal."""
        dj, du = both('{% a2416_probe "a=b" %}')
        assert "&#x27;a=b&#x27;" in dj, dj
        assert du == dj

    def test_a_literal_containing_a_pipe_is_not_torn_into_a_filter(self) -> None:
        """`split_pipes` is quote-aware (#2409), so the literal survives."""
        dj, du = both('{% a2416_probe "a|b" %}')
        assert "&#x27;a|b&#x27;" in dj, dj
        assert du == dj

    def test_a_NUMBER_literal_is_not_marked(self) -> None:
        """Django `mark_safe`s only the QUOTED branch of `Variable.__init__`."""
        dj, _du = both("{% a2416_probe 5 %}")
        assert "html=False" in dj, dj

    def test_a_literal_through_a_filter_keeps_the_grant(self) -> None:
        """The cross of the literal axis and the filter axis, which is its own
        axis: `get_value_safe` seeded the chain from `context.is_safe(name)`,
        and a literal is not a name — so `{% t "<B>"|lower %}` came out
        ESCAPED where the `{{ }}` arm (seeded from `django_literal`'s own bool)
        was already right. `lower` is `is_safe=True`, so a safe input stays
        safe; `upper` is not, which is why an `upper` cell could not tell the
        two seeds apart."""
        dj, du = both('{% a2416_ident "<B>"|lower %}')
        assert dj == "<b>"
        assert du == dj

    def test_the_same_seed_fix_reaches_the_builtin_tag_operands(self) -> None:
        """`get_value_safe` is also the `{% firstof %}` / `{% cycle %}` operand
        resolver, so the seed fix lands there too — and it must, or this is
        #1646 again with the literal grant living in two places."""
        dj, du = both('{% firstof "<B>"|lower %}')
        assert dj == "<b>"
        assert du == dj


# ---------------------------------------------------------------------------
# 3. The security boundary — what does NOT become live
# ---------------------------------------------------------------------------


class TestTheGrantDoesNotWiden:
    """Every row here is a value that must stay ESCAPED.

    The change moves in the less-escaping direction on the path where #2379's
    XSS lived, so the set of newly-live values is stated and asserted rather
    than argued.
    """

    def test_an_UNMARKED_context_string_is_still_escaped(self) -> None:
        """The framework-reachable half of #2379. Nothing marked this."""
        dj, du = both("{% a2416_ident p %}", {"p": XSS})
        assert du == dj
        assert not UNESCAPED_TAG.search(du), du

    def test_an_unmarked_value_reaches_the_handler_as_a_plain_str(self) -> None:
        dj, du = both("{% a2416_probe p %}", {"p": "<b>"})
        assert "html=False" in dj, dj
        assert du == dj

    @pytest.mark.parametrize(
        "ctx",
        [
            {"p": 5},
            {"p": 5.5},
            {"p": True},
            {"p": None},
            {"p": [1, 2]},
            {"p": {"a": 1}},
        ],
    )
    def test_a_non_string_value_is_never_marked(self, ctx: dict) -> None:
        """Django's `SafeData` is a `str` subclass, so only a string can carry
        it — and a container's JSON encoding is structure the renderer
        synthesized rather than bytes anyone vouched for. `tag_arg`'s
        `matches!(value, Value::String(_))` narrowing is what enforces this."""
        _dj, du = both("{% a2416_probe p %}", ctx)
        assert "html=False" in du, du

    def test_a_marked_LIST_does_not_hand_the_handler_live_JSON(self) -> None:
        """The sharpest row of the narrowing: `Context::is_safe` can legitimately
        answer `true` for a marked container, and without the `Value::String`
        test the handler would receive that container's JSON encoding WITH a
        grant."""
        payload = mark_safe(f'["{XSS}"]')  # noqa: S308
        _dj, du = both("{% a2416_probe li %}", {"li": [payload]})
        assert "html=False" in du, du

    def test_a_kwarg_composite_is_not_marked(self) -> None:
        """The transported text is `key=<value>`, not the value; marking it
        would mark the `key=` bytes too. Left over-escaping, and unchanged."""

        class _Kw:
            def render(self, args, _context):
                return f"[{args[0]}|html={hasattr(args[0], '__html__')}]"

        _rust.register_tag_handler("a2416_kw", _Kw())
        try:
            out = _rust.render_template_with_dirs(
                "{% a2416_kw k=p %}",
                {"p": mark_safe(XSS)},
                [],
                ["p"],  # noqa: S308
            )
        finally:
            _rust.unregister_tag_handler("a2416_kw")
        assert "html=False" in out, out
        assert not UNESCAPED_TAG.search(out), out

    def test_an_unresolved_name_is_not_marked(self) -> None:
        _dj, du = both("{% a2416_probe nope %}")
        assert "html=False" in du, du

    def test_no_shape_emits_a_live_payload_django_does_not(self) -> None:
        """The permissiveness question on its own, over the whole probe grid
        crossed with every grant shape — the assertion #2379 made for the
        return channel, re-run for the argument channel."""
        shapes = [
            "{% a2416_ident p %}",
            "{% a2416_cond p %}",
            "{% a2416_probe p %}",
            "{% a2416_ident p|upper %}",
            "{% a2416_ident p|lower %}",
            "{% a2416_cond p|safe %}",
            '{% a2416_ident "<b>" %}',
            "{% a2416_ident k=p %}",
        ]
        contexts = [
            {"p": XSS},
            {"p": f"</script><script>alert(1)</script>{XSS}"},
            {"p": '" onmouseover="x'},
            {"p": [XSS]},
            {"p": {"k": XSS}},
        ]
        leaked = []
        for source in shapes:
            for ctx in contexts:
                dj, du = both(source, ctx)
                if UNESCAPED_TAG.search(du) and not UNESCAPED_TAG.search(dj):
                    leaked.append((source, ctx, dj, du))
        assert not leaked, leaked


# ---------------------------------------------------------------------------
# 4. The premise the issue got wrong
# ---------------------------------------------------------------------------


class TestTheArgumentTYPEIsStillAString:
    """#2416 says fixing the marker "would also close the third bullet of
    #2379's step 4" — the divergence where a handler that type-checks its
    argument sees ``"5"`` where Django hands it ``5``. Run, it does not.

    Marking a string ``SafeData`` does not make it an ``int``; the two are
    different halves of the same flattening, and only the safety half changed.
    The type half needs the argument channel to transport real Python objects,
    which would rework the ``value_to_arg_string`` contract every handler
    decodes against (`RenderSlotTagHandler`'s JSON round-trip among them).
    Pinned so the remaining divergence is a named limit rather than a silence.
    """

    @pytest.mark.parametrize(
        ("source", "ctx", "django_type"),
        [
            ("{% a2416_probe n %}", {"n": 5}, "int"),
            ("{% a2416_probe f %}", {"f": 5.5}, "float"),
            ("{% a2416_probe b %}", {"b": True}, "bool"),
            ("{% a2416_probe z %}", {"z": None}, "NoneType"),
            ("{% a2416_probe li %}", {"li": [1, 2]}, "list"),
            ("{% a2416_probe 5 %}", {}, "int"),
        ],
    )
    def test_django_hands_the_object_and_djust_hands_a_string(
        self, source: str, ctx: dict, django_type: str
    ) -> None:
        dj, du = both(source, ctx)
        assert f"[{django_type}|" in dj, dj
        assert "[str|" in du, du
