"""``{% render_slot %}`` emits the parent's rendered slot LIVE, and a bare
context string ESCAPED (#2421).

Two findings on one method, pointing in opposite directions
-----------------------------------------------------------
``RenderSlotTagHandler._render_value`` has two returns and #2379 treated them
alike, which is right for one of them and wrong for the other:

* ``value["content"]`` — a slot entry's body, **already rendered and already
  escaped by the parent**. #2379 escaped it a second time, so every function
  component and named slot began rendering its own markup as visible text and
  any context data inside it double-escaped to ``&amp;lt;``. That is the
  regression this file closes, and it blocked the 1.2 RC.
* the trailing ``str(value)`` — a bare value straight out of the render
  context, e.g. ``{% render_slot p %}`` with ``p = "<img src=x onerror=…>"``.
  Nothing has escaped it. It is the one handler of the 221 #2379 enumerated
  that echoes a *context* value, which is what makes the custom-tag XSS
  framework-reachable on 1.0.0 / 1.0.8 / 1.1.0 with no ``|safe``, no
  ``mark_safe`` and no app-written handler — using slots is enough.

So a blanket ``mark_safe`` restores a shipped vulnerability and leaving it
alone keeps today's regression. The mark goes at the ONE exit that is
genuinely already-escaped (#1104), and this file asserts BOTH directions so
that widening the mark and removing it each turn a test red.

Why the regression shipped
--------------------------
``tests/unit/test_named_slots.py`` (14) and
``tests/unit/test_function_components.py`` (18) are green both before and
after — 32 between them, not 32 each as #2421 reports. Every slot body they
render is plain text — ``"first-col-content"``, ``"H"``, ``"cellA"``, ``"B"``
— which escapes to itself, so a double escape is invisible to all 32. Nothing
asserted that a slot's *markup* reaches the page live, which is the assertion
:class:`TestASlotsMarkupRendersLive` adds.

The premise is verified here, not quoted
----------------------------------------
The handler's docstring calls the content "already-escaped HTML from the
parent". :class:`TestThePremiseTheMarkRestsOn` proves it instead: a ``{{ var }}``
written into a ``{% slot %}`` body arrives in the slot entry as
``&lt;img …&gt;`` while literal markup written beside it survives raw — i.e.
exactly the trust status Django gives ``{% include %}``'s output.
"""

from __future__ import annotations

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
    RenderSlotTagHandler,
    _extract_slots,
    clear_components,
    component,
)

#: The payload every hostile row uses.
XSS = "<img src=x onerror=alert(1)>"


#: A slot entry exactly as ``_extract_slots`` builds one.
def _entry(content: str) -> dict:
    return {"name": "col", "attrs": {}, "content": content}


@pytest.fixture(autouse=True)
def _handlers_registered():
    """``{% call %}`` / ``{% slot %}`` / ``{% render_slot %}`` on the Rust engine.

    Importing the module is NOT enough — the tags reach the engine only
    through ``register_with_rust_engine``. The component registry is saved and
    restored so this file cannot leak a component into another test's process.
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
# The premise the mark rests on
# ---------------------------------------------------------------------------


class TestThePremiseTheMarkRestsOn:
    """Marking a value safe is only correct if something already escaped it.

    Asserted rather than taken from the docstring, because the whole fix is
    wrong if this is false.
    """

    def _slot_entry_for(self, body: str, ctx: dict) -> dict:
        seen: dict = {}

        @component
        def probe(assigns):
            seen.update(assigns["slots"])
            return "<i>x</i>"

        render('{% call "probe" %}{% slot col %}' + body + "{% endslot %}{% endcall %}", ctx)
        assert "col" in seen, "the slot never reached the component — the probe proves nothing"
        return seen["col"][0]

    def test_context_data_in_a_slot_body_arrives_ESCAPED(self) -> None:
        entry = self._slot_entry_for("{{ evil }}", {"evil": XSS})
        assert entry["content"] == "&lt;img src=x onerror=alert(1)&gt;"

    def test_literal_markup_in_a_slot_body_arrives_RAW(self) -> None:
        """The other half: the parent's own markup is not escaped on the way in.

        Both halves together are what "already-escaped HTML from the parent"
        means, and they are why this content has ``{% include %}``'s status
        rather than a ``simple_tag`` return's.
        """
        entry = self._slot_entry_for("<strong>lit</strong>{{ evil }}", {"evil": "<b>c</b>"})
        assert entry["content"] == "<strong>lit</strong>&lt;b&gt;c&lt;/b&gt;"

    def test_the_sentinel_round_trip_is_not_what_escapes_it(self) -> None:
        """``_emit_slot_sentinel`` html-escapes its JSON and ``_extract_slots``
        un-escapes it, so the transport is a no-op on the content. The escape
        seen above came from the ENGINE rendering the body, which is the only
        reason it is trustworthy."""
        from djust.components.function_component import _emit_slot_sentinel

        slots, _ = _extract_slots(_emit_slot_sentinel({"name": "col", "attrs": {}, "content": XSS}))
        assert slots["col"][0]["content"] == XSS, "the sentinel must round-trip byte-for-byte"


# ---------------------------------------------------------------------------
# Direction 1 — the regression
# ---------------------------------------------------------------------------


class TestASlotsMarkupRendersLive:
    """The assertion nothing made, which is why #2421 shipped.

    Every row renders MARKUP through a slot and asserts it reaches the output
    live. Gate the ``safe_html`` off at ``_render_value``'s dict branch and
    every one of these goes red.
    """

    def test_a_component_slots_markup_renders_live_end_to_end(self) -> None:
        """The whole user-visible path: ``{% call %}`` → ``{% slot %}`` →
        the component's own template → ``{% render_slot %}`` → the page."""

        @component
        def card(assigns):
            return render(
                "<div class=card>{% render_slot slots.body.0 %}</div>",
                {"slots": assigns["slots"]},
            )

        out = render(
            '{% call "card" %}{% slot body %}<strong>hello</strong>{% endslot %}{% endcall %}'
        )
        assert out == "<div class=card><strong>hello</strong></div>"

    def test_the_issues_own_example(self) -> None:
        assert render("{% render_slot p %}", {"p": {"content": "<strong>rendered</strong>"}}) == (
            "<strong>rendered</strong>"
        )

    def test_a_slot_entry_reached_by_dotted_path_renders_live(self) -> None:
        """``{% render_slot slots.col.0 %}`` — the spelling
        ``docs/website/guides/components.md`` documents."""
        ctx = {"slots": {"col": [_entry("<em>cell</em>")]}}
        assert render("{% render_slot slots.col.0 %}", ctx) == "<em>cell</em>"

    def test_a_list_of_slot_entries_renders_the_first_live(self) -> None:
        """``{% for col in slots.col %}{% render_slot col %}{% endfor %}`` —
        the ROADMAP's table spelling — resolves each item to a dict, and a
        bare ``{% render_slot slots.col %}`` resolves to the list."""
        ctx = {"slots": {"col": [_entry("<em>first</em>"), _entry("<em>second</em>")]}}
        assert render("{% render_slot slots.col %}", ctx) == "<em>first</em>"
        assert (
            render("{% for c in slots.col %}[{% render_slot c %}]{% endfor %}", ctx)
            == "[<em>first</em>][<em>second</em>]"
        )

    def test_context_data_inside_a_live_slot_is_still_escaped_exactly_once(self) -> None:
        """The compounding half of the regression.

        On ``main`` this rendered ``&amp;lt;img …&amp;gt;`` — the engine
        escaped the ``{{ var }}`` on the way in and the bridge escaped the
        result again on the way out. One escape is correct; two is a visible
        ``&amp;lt;`` on the page.
        """

        @component
        def card(assigns):
            return render("<p>{% render_slot slots.body.0 %}</p>", {"slots": assigns["slots"]})

        out = render(
            '{% call "card" %}{% slot body %}<b>hi</b> {{ evil }}{% endslot %}{% endcall %}',
            {"evil": XSS},
        )
        assert out == "<p><b>hi</b> &lt;img src=x onerror=alert(1)&gt;</p>"
        assert "&amp;lt;" not in out, "double-escaped — the #2421 compounding"


# ---------------------------------------------------------------------------
# Direction 2 — the XSS that must stay closed
# ---------------------------------------------------------------------------


class TestABareContextStringStaysEscaped:
    """Widen the mark to the whole return and every row here goes red.

    This is the framework-reachable half of #2379: ``render_slot`` is the one
    handler of 221 that echoes a value straight out of the render context, so
    on 1.0.0 / 1.0.8 / 1.1.0 the payload rendered LIVE with no ``|safe``, no
    ``mark_safe`` and no app-written handler.
    """

    @pytest.mark.parametrize(
        "payload",
        [
            XSS,
            "<script>alert(1)</script>",
            "<svg onload=alert(1)>",
            '"><img src=x onerror=alert(1)>',
        ],
    )
    def test_a_hostile_bare_string_is_escaped(self, payload: str) -> None:
        """The issue's shape. Under the Rust engine this reaches ``render()``'s
        Shape-3 passthrough — NOT ``_render_value`` — so it is guarded by that
        exit staying unmarked, a separate mechanism from the one below."""
        out = render("{% render_slot p %}", {"p": payload})
        assert "<" not in out, f"live markup reached the page: {out!r}"
        assert "&lt;" in out

    @pytest.mark.parametrize(
        "payload",
        [XSS, "<script>alert(1)</script>", "<svg onload=alert(1)>"],
    )
    def test_a_hostile_string_inside_a_LIST_is_escaped(self, payload: str) -> None:
        """The row that reaches ``_render_value``'s trailing ``str(value)``.

        Traced, not assumed: ``{% render_slot p %}`` with ``p = ["<img …>"]``
        JSON-decodes to a list, takes ``value[0]``, finds a ``str`` rather than
        a slot entry and falls to the trailing return. Widen the mark to that
        return and this row goes red — which is the only reason the trailing
        exit is guarded at all, since the bare-string row above never gets
        there.
        """
        out = render("{% render_slot p %}", {"p": [payload]})
        assert "<" not in out, f"live markup reached the page: {out!r}"
        assert "&lt;" in out

    def test_a_hostile_string_inside_a_slot_ENTRY_is_escaped_by_the_parent(self) -> None:
        """The entry path stays safe for a different reason than the bare path:
        the ENGINE escaped it, so the mark is applied to bytes that are already
        inert. Both routes end at the same rendered output."""

        @component
        def card(assigns):
            return render("{% render_slot slots.body.0 %}", {"slots": assigns["slots"]})

        out = render(
            '{% call "card" %}{% slot body %}{{ evil }}{% endslot %}{% endcall %}', {"evil": XSS}
        )
        assert out == "&lt;img src=x onerror=alert(1)&gt;"

    def test_the_scalar_spelling_is_now_LIVE_and_the_bare_string_is_not(self) -> None:
        """The limit this class pinned, CLOSED in #2423 — and the row it had to
        be argued with, which is why it is rewritten here rather than deleted.

        ``{% render_slot slots.col.0.content %}`` used to resolve to a bare
        string before the handler ran, so it was indistinguishable there from a
        hostile context value and took the escape. #2423 gives the handler the
        LITERAL path (``RESOLVE_ARG_POSITIONS = frozenset()``), and the two are
        structurally distinct again: one terminates at the ``content`` key of a
        slot entry, the other at a bare context value.

        Both directions are asserted together, because the whole argument for
        the change is that it moves ONLY the first.
        """
        ctx = {"slots": {"col": [_entry("<em>cell</em>")]}}
        assert render("{% render_slot slots.col.0.content %}", ctx) == "<em>cell</em>"
        assert render("{% render_slot p %}", {"p": XSS}) == "&lt;img src=x onerror=alert(1)&gt;"


# ---------------------------------------------------------------------------
# What the mark trusts, stated rather than implied
# ---------------------------------------------------------------------------


class TestTheTrustContractOfTheDictExit:
    """A dict reaching ``render_slot`` is trusted AS A SLOT ENTRY.

    Normally it is one: ``_extract_slots`` built it from the parent's rendered
    block body, which :class:`TestThePremiseTheMarkRestsOn` shows is already
    escaped. An app that instead hands ``{% render_slot %}`` a dict of its own
    with attacker data under ``"content"`` is outside that contract, and the
    content goes out raw.

    That is not a widening: on 1.1.0 the bridge escaped NOTHING
    (``escape_handler_return`` does not exist in ``registry.rs`` at ``v1.1.0``),
    so this dict rendered raw there too — and so did the bare string, which is
    the vulnerability #2379 closed and this fix keeps closed. The restored
    surface is strictly narrower than what shipped.
    """

    def test_a_hand_built_dict_is_taken_at_its_word(self) -> None:
        assert render("{% render_slot p %}", {"p": {"content": XSS}}) == XSS

    def test_a_dict_without_content_renders_empty_not_its_other_keys(self) -> None:
        """Only ``content`` is emitted, so an unrelated dict cannot leak its
        values through this tag."""
        assert render("{% render_slot p %}", {"p": {"name": "col", "attrs": {"x": XSS}}}) == ""


# ---------------------------------------------------------------------------
# The mark is at ONE exit
# ---------------------------------------------------------------------------


class TestTheMarkIsAtExactlyOneExit:
    """``__html__`` on the handler's return is the whole mechanism — the Rust
    bridge escapes iff it is absent — so it can be asserted directly rather
    than only through rendered output."""

    def test_the_slot_entry_exit_carries_the_marker(self) -> None:
        out = RenderSlotTagHandler._render_value(_entry("<em>cell</em>"))
        assert hasattr(out, "__html__"), "the already-escaped exit lost its mark"

    def test_the_list_exit_carries_the_marker_through_its_first_entry(self) -> None:
        out = RenderSlotTagHandler._render_value([_entry("<em>cell</em>")])
        assert hasattr(out, "__html__")

    def test_the_bare_value_exit_does_NOT_carry_the_marker(self) -> None:
        out = RenderSlotTagHandler._render_value(XSS)
        assert not hasattr(out, "__html__"), (
            "the untrusted exit was marked safe — this is the #2379 XSS restored"
        )

    def test_the_scalar_passthrough_exit_does_NOT_carry_the_marker(self) -> None:
        """``render()``'s Shape-3 return, the fourth exit."""
        out = RenderSlotTagHandler().render([XSS], {})
        assert out == XSS
        assert not hasattr(out, "__html__")

    def test_an_empty_or_missing_slot_is_still_empty(self) -> None:
        """Marking must not turn a miss into output."""
        assert RenderSlotTagHandler._render_value([]) == ""
        assert render("{% render_slot nope %}", {"slots": {}}) == ""


# ---------------------------------------------------------------------------
# The two sibling handlers from the same #2379 sweep
# ---------------------------------------------------------------------------


class TestTheSiblingHandlersInTheSameSweep:
    """``SlotTagHandler`` and ``CallTagHandler`` share the content-passing
    contract and were marked by #2379. Each is decided here explicitly rather
    than assumed correct, since a drifted parallel path is this repo's most
    recurring bug class (#1646).

    Both are RIGHT as they stand, for reasons that differ from each other and
    from ``render_slot``:

    * ``SlotTagHandler`` returns a ``<!--DJUST_SLOT_V1:…-->`` SENTINEL whose
      payload ``_emit_slot_sentinel`` has already ``html.escape``-d, so there
      is nothing left in it to escape — and escaping the comment itself would
      turn it into visible text and break slot collection outright.
    * ``CallTagHandler`` returns a COMPONENT's rendered markup, which is
      markup by contract (``{% include %}``'s status, not a ``simple_tag``
      return's). A component that interpolates user data into its own markup
      escapes it itself, exactly as a template author does.
    """

    def test_the_slot_sentinel_is_not_escaped_so_collection_still_works(self) -> None:
        seen: dict = {}

        @component
        def probe(assigns):
            seen.update(assigns["slots"])
            return "<i>x</i>"

        render('{% call "probe" %}{% slot col %}body{% endslot %}{% endcall %}')
        assert seen.get("col"), (
            "the sentinel was escaped into visible text — slot collection is broken"
        )

    def test_a_components_own_markup_renders_live(self) -> None:
        @component
        def badge(assigns):
            return "<span class=badge>ok</span>"

        assert render('{% call "badge" %}{% endcall %}') == "<span class=badge>ok</span>"

    def test_a_component_is_responsible_for_escaping_what_it_interpolates(self) -> None:
        """The documented limit of ``CallTagHandler``'s mark, unchanged by this
        PR and pinned so it stays a stated contract."""
        from django.utils.html import escape

        @component
        def unsafe(assigns):
            return f"<p>{assigns['v']}</p>"

        @component
        def careful(assigns):
            return f"<p>{escape(assigns['v'])}</p>"

        assert render('{% call "unsafe" v=evil %}{% endcall %}', {"evil": XSS}) == f"<p>{XSS}</p>"
        out = render('{% call "careful" v=evil %}{% endcall %}', {"evil": XSS})
        assert out == "<p>&lt;img src=x onerror=alert(1)&gt;</p>"
