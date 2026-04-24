"""Unit tests for ``{% dj_activity %}`` + ActivityMixin (v0.7.0).

React 19.2 ``<Activity>`` parity. Pre-renders hidden regions of a LiveView
and preserves their local state across show/hide cycles. Covers:

1.  ``test_activity_tag_renders_visible_by_default`` — no-kwarg render.
2.  ``test_activity_tag_renders_hidden_when_visible_false`` — hidden attr
    + aria-hidden + body content still present in the DOM.
3.  ``test_activity_tag_eager_sets_data_attr`` — ``eager=True``.
4.  ``test_set_activity_visible_toggles_state`` — server-side API.
5.  ``test_deferred_events_queue_and_flush`` — queue 3, flip, drain.
6.  ``test_deferred_queue_caps_at_100`` — FIFO eviction.
7.  ``test_eager_activity_bypasses_defer_path``.
8.  ``test_a070_missing_name_warns`` — system check A070.
9.  ``test_a071_duplicate_name_errors`` — system check A071.
10. ``test_form_value_preserved_across_toggle``.
11. ``test_activity_mixin_methods_exist`` — signature contract.
12. ``test_activities_internal_state_excluded_from_get_state``.
"""

from __future__ import annotations

import asyncio
from html.parser import HTMLParser
from typing import Dict, List, Tuple
from unittest.mock import AsyncMock, MagicMock

from django.template import Context, Template

from djust.live_view import LiveView
from djust.mixins.activity import ActivityMixin, _DEFAULT_ACTIVITY_EVENT_QUEUE_CAP


# ---------------------------------------------------------------------------
# HTML parse helpers — same discipline as test_live_render_tag.py:
# assertions walk the attribute dict, never a substring.
# ---------------------------------------------------------------------------


class _AttrCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.elements: List[Tuple[str, Dict[str, str]]] = []
        self.text: List[str] = []

    def handle_starttag(self, tag, attrs):
        d = {name: (v if v is not None else "") for name, v in attrs}
        self.elements.append((tag, d))

    def handle_startendtag(self, tag, attrs):
        d = {name: (v if v is not None else "") for name, v in attrs}
        self.elements.append((tag, d))

    def handle_data(self, data):
        self.text.append(data)


def _parse(html: str) -> _AttrCollector:
    p = _AttrCollector()
    p.feed(html)
    p.close()
    return p


def _find_activity(html: str):
    """Return (attrs_dict, text) for the first ``data-djust-activity`` element."""
    tree = _parse(html)
    for tag, attrs in tree.elements:
        if "data-djust-activity" in attrs:
            return attrs, "".join(tree.text)
    return None, "".join(tree.text)


# ---------------------------------------------------------------------------
# Module-scope LiveView for mount-then-render tests
# ---------------------------------------------------------------------------


class _PanelView(LiveView):
    """LiveView with two panels wrapped in {% dj_activity %} blocks."""

    template = (
        "{% load live_tags %}"
        "<div dj-root>"
        '{% dj_activity "tab-a" visible=tab_a_visible %}<input value="{{ a_value }}"/>{% enddj_activity %}'
        '{% dj_activity "tab-b" visible=tab_b_visible eager=True %}<span>{{ b_count }}</span>{% enddj_activity %}'
        "</div>"
    )

    def mount(self, request, **kwargs):
        self.tab_a_visible = True
        self.tab_b_visible = False
        self.a_value = "hello"
        self.b_count = 0


# ---------------------------------------------------------------------------
# 1-3. Template-tag rendering
# ---------------------------------------------------------------------------


def test_activity_tag_renders_visible_by_default():
    """Without visible=False, the wrapper has no hidden attribute."""
    tpl = Template('{% load live_tags %}{% dj_activity "panel" %}<p>body</p>{% enddj_activity %}')
    out = tpl.render(Context({}))
    attrs, text = _find_activity(out)
    assert attrs is not None
    assert attrs["data-djust-activity"] == "panel"
    assert "hidden" not in attrs
    assert "aria-hidden" not in attrs
    assert attrs["data-djust-visible"] == "true"
    # Body preserved as PCDATA regardless of visibility.
    assert "body" in text


def test_activity_tag_renders_hidden_when_visible_false():
    """visible=False sets hidden + aria-hidden but keeps the body."""
    tpl = Template(
        '{% load live_tags %}{% dj_activity "panel" visible=flag %}<p>body</p>{% enddj_activity %}'
    )
    out = tpl.render(Context({"flag": False}))
    attrs, text = _find_activity(out)
    assert attrs is not None
    # hidden boolean attribute is canonically serialized as hidden="hidden"
    # by build_tag (True-valued). Browsers accept either bare or named form.
    assert "hidden" in attrs
    assert attrs["aria-hidden"] == "true"
    assert attrs["data-djust-visible"] == "false"
    # Body content still present — hidden only suppresses rendering, not DOM.
    assert "body" in text


def test_activity_tag_eager_sets_data_attr():
    """eager=True adds data-djust-eager="true" regardless of visibility."""
    tpl = Template(
        '{% load live_tags %}{% dj_activity "p" visible=False eager=True %}x{% enddj_activity %}'
    )
    out = tpl.render(Context({}))
    attrs, _ = _find_activity(out)
    assert attrs is not None
    assert attrs.get("data-djust-eager") == "true"
    # Eager + hidden is the "continues to run while hidden" mode.
    assert "hidden" in attrs


# ---------------------------------------------------------------------------
# 4. ActivityMixin public API
# ---------------------------------------------------------------------------


def test_set_activity_visible_toggles_state():
    """set_activity_visible flips declared state; is_activity_visible reads it."""

    class _V(ActivityMixin):
        pass

    v = _V()
    v._init_activity()
    # Unknown activity defaults to visible (so typos don't suppress events).
    assert v.is_activity_visible("nope") is True
    v._register_activity("panel", visible=True, eager=False)
    assert v.is_activity_visible("panel") is True
    v.set_activity_visible("panel", False)
    assert v.is_activity_visible("panel") is False
    v.set_activity_visible("panel", True)
    assert v.is_activity_visible("panel") is True


# ---------------------------------------------------------------------------
# 5-7. Deferred-event queue
# ---------------------------------------------------------------------------


class _QueueView(ActivityMixin):
    """Minimal ActivityMixin-only stub for queue/flush tests."""

    pass


def _make_consumer():
    """Build a mock consumer whose _dispatch_single_event is a coroutine (AsyncMock)."""
    consumer = MagicMock()
    # Post-refactor: flush awaits consumer._dispatch_single_event(view, event, params)
    # instead of firing consumer.handle_event via create_task.
    consumer._dispatch_single_event = AsyncMock(return_value=None)
    return consumer


def test_deferred_events_queue_and_flush():
    """Queue 3 events while hidden, flip visible, drain in FIFO order."""
    v = _QueueView()
    v._init_activity()
    v._register_activity("panel", visible=False, eager=False)
    v._queue_deferred_activity_event("panel", "bump", {"value": 1})
    v._queue_deferred_activity_event("panel", "bump", {"value": 2})
    v._queue_deferred_activity_event("panel", "bump", {"value": 3})
    assert len(v._deferred_activity_events["panel"]) == 3

    consumer = _make_consumer()
    # Still hidden — drain is a no-op.
    asyncio.run(_drain(v, consumer))
    assert consumer._dispatch_single_event.await_count == 0
    assert len(v._deferred_activity_events["panel"]) == 3

    # Flip to visible — drain dispatches all 3 in order.
    v.set_activity_visible("panel", True)
    asyncio.run(_drain(v, consumer))
    # Queue is popped clean on drain.
    assert "panel" not in v._deferred_activity_events
    assert consumer._dispatch_single_event.await_count == 3
    # Call args arrive in FIFO order. _dispatch_single_event signature:
    # (target_view, event_name, params).
    call_values = [
        call.args[2]["value"] for call in consumer._dispatch_single_event.await_args_list
    ]
    assert call_values == [1, 2, 3]


async def _drain(view, consumer):
    """Helper that awaits the async flush inside an event loop."""
    # flush is now async — await it directly so we see the same
    # round-trip semantics as the real consumer path.
    await view._flush_deferred_activity_events(consumer)


def test_deferred_queue_caps_at_100():
    """Queue cap of 100 evicts oldest via FIFO when flooded."""
    v = _QueueView()
    v._init_activity()
    v._register_activity("flood", visible=False, eager=False)
    for i in range(120):
        v._queue_deferred_activity_event("flood", "evt", {"n": i})
    q = v._deferred_activity_events["flood"]
    assert len(q) == _DEFAULT_ACTIVITY_EVENT_QUEUE_CAP == 100
    # First 20 were dropped by FIFO eviction.
    assert q[0][1]["n"] == 20
    assert q[-1][1]["n"] == 119


def test_deferred_events_flush_in_same_round_trip():
    """Each deferred event is awaited inline — no fire-and-forget create_task.

    Regression lock for Stage 7 Finding 1. Prior implementation used
    ``loop.create_task(coro)`` per event, which returned before the
    dispatch actually ran — the drained events fired AFTER the response
    had already been sent. The fix awaits each dispatch sequentially so
    every drained event completes inside the current handler's
    round-trip. We prove the new contract by tracking completion order:
    if the flush returns before the last dispatch finishes, the
    completion flag stays False.
    """
    v = _QueueView()
    v._init_activity()
    v._register_activity("panel", visible=False, eager=False)
    for i in range(3):
        v._queue_deferred_activity_event("panel", "evt", {"n": i})
    v.set_activity_visible("panel", True)

    # Build a consumer whose _dispatch_single_event yields once per call
    # and then records its own completion. If the flush await'd properly
    # the completion list will be [0, 1, 2] when flush returns.
    completions: list = []

    async def _fake_dispatch(target_view, event_name, params):
        # Yield once so a create_task-based (broken) implementation would
        # return from the flush before this line executes.
        await asyncio.sleep(0)
        completions.append(params["n"])

    consumer = MagicMock()
    consumer._dispatch_single_event = AsyncMock(side_effect=_fake_dispatch)

    asyncio.run(v._flush_deferred_activity_events(consumer))

    # Every dispatch completed before flush returned. Order is FIFO.
    assert completions == [0, 1, 2]
    # _dispatch_single_event was called exactly 3 times.
    assert consumer._dispatch_single_event.await_count == 3
    # Queue is drained clean.
    assert "panel" not in v._deferred_activity_events


def test_deferred_flush_strips_activity_param_before_dispatch():
    """Flush strips ``_activity`` from params so the gate cannot re-queue.

    Regression lock: a deferred event's params may still carry the
    ``_activity`` marker (it was present when the client first sent the
    frame). The flush MUST remove it before calling dispatch; otherwise
    a handler that flipped the activity back to hidden mid-drain would
    cause the re-dispatched event to hit the activity gate and re-queue
    itself.
    """
    v = _QueueView()
    v._init_activity()
    v._register_activity("panel", visible=True, eager=False)
    # Queue with a stale _activity marker (simulates a client race).
    v._queue_deferred_activity_event("panel", "bump", {"value": 1, "_activity": "panel"})
    consumer = _make_consumer()
    asyncio.run(v._flush_deferred_activity_events(consumer))
    assert consumer._dispatch_single_event.await_count == 1
    # The dispatched params do NOT carry _activity anymore.
    dispatched_params = consumer._dispatch_single_event.await_args_list[0].args[2]
    assert "_activity" not in dispatched_params
    assert dispatched_params["value"] == 1


def test_eager_activity_bypasses_defer_path():
    """Eager activities always drain — even when visible=False."""
    v = _QueueView()
    v._init_activity()
    v._register_activity("always-on", visible=False, eager=True)
    v._queue_deferred_activity_event("always-on", "tick", {})
    v._queue_deferred_activity_event("always-on", "tick", {})
    consumer = _make_consumer()
    asyncio.run(_drain(v, consumer))
    assert consumer._dispatch_single_event.await_count == 2
    assert "always-on" not in v._deferred_activity_events


# ---------------------------------------------------------------------------
# 8-9. System checks A070 / A071
# ---------------------------------------------------------------------------


def test_a070_missing_name_warns(tmp_path, settings):
    """A070: {% dj_activity %} with no name argument emits a DjustWarning."""
    tpl_dir = tmp_path / "templates"
    tpl_dir.mkdir()
    (tpl_dir / "bad.html").write_text(
        "{% load live_tags %}{% dj_activity %}body{% enddj_activity %}"
    )
    settings.TEMPLATES = [
        {
            "DIRS": [str(tpl_dir)],
            "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
        }
    ]
    from djust.checks import check_templates

    errors = check_templates(None)
    a070 = [e for e in errors if getattr(e, "id", "") == "djust.A070"]
    assert len(a070) == 1
    # Line number is preserved so IDE quick-fix can jump to the tag.
    assert a070[0].line_number == 1


def test_a070_not_emitted_for_variable_name(tmp_path, settings):
    """A070 must NOT fire for ``{% dj_activity panel_name %}`` (bare identifier).

    The tag body resolves the name at render time. Treating bare
    identifiers as "missing name" was a false positive that flagged
    valid user code — the inline comment at checks.py already acknowledged
    that variable expressions resolve at runtime but the regex only
    matched string literals. This test locks the fix.
    """
    tpl_dir = tmp_path / "templates"
    tpl_dir.mkdir()
    (tpl_dir / "var.html").write_text(
        "{% load live_tags %}{% dj_activity panel_name visible=x %}body{% enddj_activity %}"
    )
    settings.TEMPLATES = [
        {
            "DIRS": [str(tpl_dir)],
            "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
        }
    ]
    from djust.checks import check_templates

    errors = check_templates(None)
    a070 = [e for e in errors if getattr(e, "id", "") == "djust.A070"]
    assert a070 == [], f"A070 should not fire for variable-name tags, got: {a070}"


def test_a071_variable_names_skip_duplicate_check(tmp_path, settings):
    """A071 must skip tags whose names are bare identifiers.

    We cannot statically prove two variable-name tags will resolve to
    the same string, so A071 cannot fire for them. String-literal
    duplicate detection still works (covered by the existing
    ``test_a071_duplicate_name_errors`` test).
    """
    tpl_dir = tmp_path / "templates"
    tpl_dir.mkdir()
    (tpl_dir / "var_dup.html").write_text(
        "{% load live_tags %}"
        "{% dj_activity x %}a{% enddj_activity %}"
        "{% dj_activity x %}b{% enddj_activity %}"
    )
    settings.TEMPLATES = [
        {
            "DIRS": [str(tpl_dir)],
            "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
        }
    ]
    from djust.checks import check_templates

    errors = check_templates(None)
    a071 = [e for e in errors if getattr(e, "id", "") == "djust.A071"]
    # No A071 because neither tag has a string-literal name we can compare.
    assert a071 == [], f"A071 should not fire for variable-name tags, got: {a071}"


def test_activity_name_escapes_html():
    """Name attribute must be HTML-escaped to prevent XSS via tag attribute.

    Regression lock for Stage 8 LOW #1. ``build_tag`` handles escaping,
    but this test pins the behavior for defense-in-depth: a template
    variable holding ``'"><script>alert(1)</script>'`` must NOT produce
    an unescaped ``<script>`` tag in the rendered output. The properly
    escaped form belongs in the ``data-djust-activity`` attribute value.
    """
    malicious = '"><script>alert(1)</script>'
    tpl = Template("{% load live_tags %}{% dj_activity name_var %}body{% enddj_activity %}")
    out = tpl.render(Context({"name_var": malicious}))
    # The unescaped script tag MUST NOT appear.
    assert '"><script>' not in out
    assert "<script>alert" not in out
    # The escaped form MUST appear inside the attribute value.
    # build_tag uses escape() which produces &quot; &lt; &gt; (not &#34; / &#60;).
    assert "&quot;&gt;&lt;script&gt;" in out


def test_a071_duplicate_name_errors(tmp_path, settings):
    """A071: two {% dj_activity %} blocks sharing a name emit a DjustError."""
    tpl_dir = tmp_path / "templates"
    tpl_dir.mkdir()
    (tpl_dir / "dup.html").write_text(
        "{% load live_tags %}"
        '{% dj_activity "panel" %}a{% enddj_activity %}'
        '{% dj_activity "panel" %}b{% enddj_activity %}'
    )
    settings.TEMPLATES = [
        {
            "DIRS": [str(tpl_dir)],
            "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
        }
    ]
    from djust.checks import check_templates

    errors = check_templates(None)
    a071 = [e for e in errors if getattr(e, "id", "") == "djust.A071"]
    assert len(a071) == 1
    # Includes both line numbers in the message for debuggability.
    assert "duplicate" in a071[0].msg.lower()


# ---------------------------------------------------------------------------
# 10. Form-value preservation semantics
# ---------------------------------------------------------------------------


def test_form_value_preserved_across_toggle(rf):
    """Body (incl. form inputs) is rendered identically in visible + hidden branches.

    Regression lock: the ``hidden`` attribute is a presentation-layer toggle;
    the child DOM is emitted unchanged in both states so local form state is
    preserved. A future refactor that drops the body when hidden would break
    state preservation — this test catches that.
    """
    # We render the same panel twice, once visible + once hidden, and
    # compare the body spans. The body INPUT element must be identical.
    tpl = Template(
        '{% load live_tags %}{% dj_activity "panel" visible=flag %}'
        '<input name="q" value="preserved"/>{% enddj_activity %}'
    )
    visible_out = tpl.render(Context({"flag": True}))
    hidden_out = tpl.render(Context({"flag": False}))

    v_attrs_list = [a for tag, a in _parse(visible_out).elements if tag == "input"]
    h_attrs_list = [a for tag, a in _parse(hidden_out).elements if tag == "input"]
    assert len(v_attrs_list) == 1 and len(h_attrs_list) == 1
    # Identical input attributes in both branches.
    assert v_attrs_list[0] == h_attrs_list[0]
    # Input value is the one we declared — not stripped by the wrapper.
    assert v_attrs_list[0]["value"] == "preserved"


# ---------------------------------------------------------------------------
# 11. Signature contract
# ---------------------------------------------------------------------------


def test_activity_mixin_methods_exist():
    """Lock in the public + internal method surface of ActivityMixin.

    Add-a-method refactors must not silently remove one of these — the
    WebSocket consumer and the template tag both rely on the exact names.
    """

    class _V(ActivityMixin):
        pass

    v = _V()
    assert callable(v._init_activity)
    assert callable(v._register_activity)
    assert callable(v.set_activity_visible)
    assert callable(v.is_activity_visible)
    assert callable(v._queue_deferred_activity_event)
    assert callable(v._flush_deferred_activity_events)
    assert callable(v._is_activity_eager)
    # eager_activities is a class attr that subclasses override.
    assert isinstance(_V.eager_activities, frozenset)


# ---------------------------------------------------------------------------
# 12. Internal state is NOT surfaced to clients via get_state()
# ---------------------------------------------------------------------------


def test_activities_internal_state_excluded_from_get_state(rf):
    """Underscore-prefixed activity attrs must not leak to the client payload.

    ``_djust_activities`` and ``_deferred_activity_events`` store
    server-only bookkeeping. The underscore-prefix filter in
    :func:`LiveView.get_state` / ``_capture_snapshot_state`` MUST exclude
    them — otherwise a malicious client could see queue lengths + event
    parameters they never triggered.
    """
    view = _PanelView()
    view.request = rf.get("/")
    view.mount(view.request)
    # Trigger some internal state so the dicts are non-empty.
    view._register_activity("tab-a", visible=True, eager=False)
    view._queue_deferred_activity_event("tab-a", "ping", {"x": 1})

    state = view.get_state()
    # get_state exposes only non-underscore user attributes.
    assert "_djust_activities" not in state
    assert "_deferred_activity_events" not in state
    # Public attrs (assigned in mount) are surfaced — sanity check that
    # the filter isn't too aggressive.
    assert state.get("tab_a_visible") is True
    assert state.get("a_value") == "hello"
