"""``{% render_slot slots.col.0.content %}`` renders LIVE, and a bare context
string still does not (#2423).

The exit #2421 could not reach
-----------------------------
#2421 restored live rendering at ``RenderSlotTagHandler._render_value``'s slot
entry exit. The one spelling it deliberately left over-escaping was the scalar
passthrough: ``{% render_slot slots.col.0.content %}`` and a hostile
``{% render_slot p %}`` arrived there as the SAME opaque string, because the
Rust engine resolved both before the handler ran. With no information left to
separate them the exit took the escape — over-escaping, never a leak.

The discriminator has to come from BEFORE resolution
----------------------------------------------------
So it does. ``RenderSlotTagHandler`` now declares
``RESOLVE_ARG_POSITIONS = frozenset()`` and the engine hands it the LITERAL
token — the inline-tag twin of the policy ``{% regroup %}`` uses to keep its
keyword operands literal (#2041), which until now existed only on the assign
registry. With the path in hand the two spellings are structurally distinct:
``slots.col.0.content`` terminates at the ``content`` key of a
``{"name", "attrs", "content"}`` slot entry, and ``p`` terminates at a bare
context value.

What that grants, exactly
-------------------------
Only a string reached through a path whose last segment is ``content`` and
whose parent is a dict with EXACTLY the key set ``_extract_slots`` builds. That
is not a new trust: ``{% render_slot d %}`` over the same dict already emits
``d["content"]`` live at ``_render_value``'s dict exit (#2421), so this lets
the ``.content`` SPELLING reach a grant that spelling-by-entry already had —
it does not widen the grant itself. :class:`TestTheGrantIsNotWidened` asserts
that equivalence directly, plus every near-miss shape that must stay escaped.

It also retires the #861 dual-caller split rather than patching it: the engine
now hands this handler exactly what a direct Python caller does.
"""

from __future__ import annotations

import html
import json

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
        INSTALLED_APPS=[],
    )
    django.setup()

from djust import _rust  # noqa: E402
from djust.components.function_component import (  # noqa: E402
    _COMPONENT_REGISTRY,
    _SLOT_ENTRY_KEYS,
    RenderSlotTagHandler,
    _emit_slot_sentinel,
    _extract_slots,
    _terminates_in_slot_content,
    clear_components,
    component,
)

#: The payload every hostile row uses.
XSS = "<img src=x onerror=alert(1)>"

#: A slot body that is MARKUP — the value #2421 and #2423 exist to keep live.
MARKUP = "<em>cell</em>"


def _entry(content: str = MARKUP) -> dict:
    """A slot entry exactly as ``_extract_slots`` builds one."""
    return {"name": "col", "attrs": {}, "content": content}


def _slots(content: str = MARKUP) -> dict:
    return {"col": [_entry(content)]}


@pytest.fixture(autouse=True)
def _handlers_registered():
    """``{% call %}`` / ``{% slot %}`` / ``{% render_slot %}`` on the Rust engine.

    Importing the module is NOT enough — the tags reach the engine only through
    ``register_with_rust_engine``. The component registry is saved and restored
    so this file cannot leak a component into another test's process.
    """
    from djust.components.rust_handlers import register_with_rust_engine

    register_with_rust_engine()
    saved = dict(_COMPONENT_REGISTRY)
    clear_components()
    yield
    clear_components()
    _COMPONENT_REGISTRY.update(saved)


def render(source: str, context: dict | None = None) -> str:
    return _rust.render_template(source, context or {})


# ---------------------------------------------------------------------------
# The mechanism: the engine hands over the LITERAL token
# ---------------------------------------------------------------------------


class TestTheEngineHandsOverTheLiteralToken:
    """Measured at the handler, not inferred from the page.

    The output alone cannot tell "the engine passed the path" from "the engine
    resolved it to something that happened to render the same".
    """

    def test_render_slot_receives_the_path_it_was_written_with(self) -> None:
        seen: list[list[str]] = []
        original = RenderSlotTagHandler.render

        def spy(self, args, context):  # type: ignore[no-untyped-def]
            seen.append(list(args))
            return original(self, args, context)

        RenderSlotTagHandler.render = spy  # type: ignore[assignment]
        try:
            render("{% render_slot slots.col.0.content %}", {"slots": _slots()})
        finally:
            RenderSlotTagHandler.render = original  # type: ignore[assignment]
        assert seen == [["slots.col.0.content"]], seen

    def test_a_handler_that_declares_NO_policy_still_gets_resolved_args(self) -> None:
        """The default is untouched — the policy is opt-in, per handler."""
        seen: list[list[str]] = []

        class _Probe:
            def render(self, args, context):  # noqa: ARG002
                seen.append(list(args))
                return ""

        _rust.register_tag_handler("rs2423_default", _Probe())
        try:
            render("{% rs2423_default p %}", {"p": "resolved"})
        finally:
            _rust.unregister_tag_handler("rs2423_default")
        assert seen == [["resolved"]], seen

    def test_the_policy_is_honoured_PER_POSITION_on_the_inline_path(self) -> None:
        """Not all-or-nothing: a handler declaring `{0}` gets position 0
        resolved and every other position literal, which is the contract the
        assign registry has had since #2041."""
        seen: list[list[str]] = []

        class _Probe:
            RESOLVE_ARG_POSITIONS = {0}

            def render(self, args, context):  # noqa: ARG002
                seen.append(list(args))
                return ""

        _rust.register_tag_handler("rs2423_partial", _Probe())
        try:
            render("{% rs2423_partial p q %}", {"p": "first", "q": "second"})
        finally:
            _rust.unregister_tag_handler("rs2423_partial")
        assert seen == [["first", "q"]], seen

    def test_an_explicit_None_policy_means_resolve_everything(self) -> None:
        """The two halves of the reader — absent and explicitly `None` — are
        the pair a hand-copy gets wrong, which is why both registries now share
        one `read_resolve_positions`."""
        seen: list[list[str]] = []

        class _Probe:
            RESOLVE_ARG_POSITIONS = None

            def render(self, args, context):  # noqa: ARG002
                seen.append(list(args))
                return ""

        _rust.register_tag_handler("rs2423_none", _Probe())
        try:
            render("{% rs2423_none p %}", {"p": "resolved"})
        finally:
            _rust.unregister_tag_handler("rs2423_none")
        assert seen == [["resolved"]], seen


# ---------------------------------------------------------------------------
# The fix
# ---------------------------------------------------------------------------


class TestTheScalarSpellingRendersLive:
    def test_the_issues_own_spelling(self) -> None:
        assert render("{% render_slot slots.col.0.content %}", {"slots": _slots()}) == MARKUP

    def test_a_slot_body_carrying_escaped_context_data_is_emitted_ONCE(self) -> None:
        """The compounding half of #2421, one spelling over: a `{{ evil }}`
        inside a slot body is already `&lt;img …&gt;` in the entry, and a
        second escape spells it `&amp;lt;`."""
        body = html.escape(XSS)
        out = render("{% render_slot slots.col.0.content %}", {"slots": _slots(body)})
        assert out == body
        assert "&amp;lt;" not in out

    @pytest.mark.parametrize(
        "source",
        [
            "{% render_slot slots.col.0 %}",
            "{% render_slot col %}",
            "{% render_slot col.0 %}",
        ],
    )
    def test_the_slot_ENTRY_spellings_still_render_live(self, source: str) -> None:
        """#2421's exit, unchanged by re-routing the operand — the engine used
        to JSON-encode the dict and now hands over the path, and both reach
        `_render_value`."""
        assert render(source, {"slots": _slots(), "col": [_entry()]}) == MARKUP

    def test_end_to_end_through_a_real_component(self) -> None:
        """The user-visible story: a `{% slot %}` body written by the caller
        reaches the component's own `{% render_slot %}` as live markup."""

        @component(name="rs2423_card")
        def _card(assigns):
            return _rust.render_template(
                "<div>{% render_slot slots.col.0.content %}</div>",
                {"slots": assigns["slots"]},
            )

        out = render("{% call rs2423_card %}{% slot col %}<em>cell</em>{% endslot %}{% endcall %}")
        assert out == "<div><em>cell</em></div>", out

    def test_a_missing_path_is_still_the_empty_string(self) -> None:
        assert render("{% render_slot slots.missing.0 %}", {"slots": _slots()}) == ""
        assert render("{% render_slot nope.content %}", {}) == ""


# ---------------------------------------------------------------------------
# The boundary
# ---------------------------------------------------------------------------


class TestTheGrantIsNotWidened:
    """Every row here is a value that must stay ESCAPED, or a claim about the
    grant's edge measured rather than asserted."""

    def test_a_bare_hostile_context_string_is_still_escaped(self) -> None:
        """The framework-reachable half of the #2379 XSS. Nothing escaped this,
        so the bridge must."""
        out = render("{% render_slot p %}", {"p": XSS})
        assert out == html.escape(XSS)
        assert "<img" not in out

    def test_a_hostile_string_at_a_content_key_of_a_NON_slot_dict_is_escaped(self) -> None:
        """The near-miss that decides the shape test: an extra key means it is
        not a slot entry, and the mark does not apply."""
        ctx = {"d": {"name": "x", "attrs": {}, "content": XSS, "extra": 1}}
        assert render("{% render_slot d.content %}", ctx) == html.escape(XSS)

    def test_a_MISSING_key_is_not_a_slot_entry_either(self) -> None:
        ctx = {"d": {"attrs": {}, "content": XSS}}
        assert render("{% render_slot d.content %}", ctx) == html.escape(XSS)

    def test_a_content_key_whose_parent_is_not_a_dict_resolves_to_NOTHING(self) -> None:
        """Measured rather than predicted: the answer here is the empty string,
        not the escaped payload, because `_resolve_context_path` cannot walk a
        `content` segment off a list at all — so the value never reaches an
        exit. The predicate is asserted directly in the table below; this row
        is about what the page gets, and the page gets nothing.
        """
        assert render("{% render_slot li.content %}", {"li": [XSS]}) == ""
        assert render("{% render_slot s.content %}", {"s": XSS}) == ""

    def test_a_top_level_content_variable_is_escaped(self) -> None:
        """No parent segment at all, so no slot entry to vouch for it."""
        assert render("{% render_slot content %}", {"content": XSS}) == html.escape(XSS)

    def test_the_slot_shaped_dict_grant_is_the_one_2421_ALREADY_gives(self) -> None:
        """A context dict shaped exactly like a slot entry IS marked through
        the `.content` spelling — and this row is why that is not a widening:
        `{% render_slot d %}` over the SAME dict already emits its `content`
        live at `_render_value`'s dict exit (#2421). The two spellings agree,
        which is the property, rather than one being more permissive.
        """
        ctx = {"d": _entry(XSS)}
        by_entry = render("{% render_slot d %}", ctx)
        by_content = render("{% render_slot d.content %}", ctx)
        assert by_entry == by_content == XSS

    def test_the_ENTRY_exit_still_refuses_a_bare_string(self) -> None:
        """#2421's own guard, re-asserted here because #2423 re-routes the
        operand and a re-route is exactly where a guard gets dropped."""
        assert render("{% render_slot li %}", {"li": [XSS]}) == html.escape(XSS)


# ---------------------------------------------------------------------------
# The discriminator's own premises
# ---------------------------------------------------------------------------


class TestTheDiscriminatorsPremises:
    def test_the_slot_entry_shape_is_the_BUILDERS_own(self) -> None:
        """`_SLOT_ENTRY_KEYS` is the whole security argument, so it is derived
        from `_extract_slots` rather than transcribed: a builder that grows a
        fourth key must fail here rather than silently stop matching and turn
        every slot body back into visible text."""
        payload = json.dumps({"name": "col", "attrs": {}, "content": MARKUP})
        built, _remainder = _extract_slots(_emit_slot_sentinel(json.loads(payload)))
        assert frozenset(built["col"][0]) == _SLOT_ENTRY_KEYS

    @pytest.mark.parametrize(
        ("path", "ctx", "expected"),
        [
            ("slots.col.0.content", {"slots": _slots()}, True),
            ("d.content", {"d": _entry()}, True),
            ("d.content", {"d": {"content": MARKUP}}, False),
            ("d.name", {"d": _entry()}, False),
            ("content", {"content": MARKUP}, False),
            ("d.content", {}, False),
        ],
    )
    def test_the_predicate_table(self, path: str, ctx: dict, expected: bool) -> None:
        assert _terminates_in_slot_content(path, ctx) is expected

    def test_the_direct_python_caller_contract_survives(self) -> None:
        """The #861 shape a direct caller uses — a literal path — is now the
        ONLY shape, which is the simplification the re-route buys."""
        handler = RenderSlotTagHandler()
        ctx = {"slots": _slots()}
        assert handler.render(["slots.col.0"], ctx) == MARKUP
        assert handler.render(["slots.col.0.content"], ctx) == MARKUP
        assert handler.render([], ctx) == ""
        assert handler.render(["slots.missing.0"], ctx) == ""

    def test_a_quoted_operand_is_unquoted_before_resolution(self) -> None:
        """The engine no longer strips the quotes for this handler, so the
        handler must — or `{% render_slot "slots.col.0" %}` resolves nothing."""
        assert render('{% render_slot "slots.col.0" %}', {"slots": _slots()}) == MARKUP
        assert render("{% render_slot 'slots.col.0' %}", {"slots": _slots()}) == MARKUP
