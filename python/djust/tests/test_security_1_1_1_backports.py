"""Regression tests for the five XSS vulnerabilities fixed in 1.1.1.

Every one of these was measured LIVE on unmodified ``v1.1.0`` before the fix,
against live Django as the reference. The five, in severity order:

======  ====================================  ==================================
 id      reproducer                            precondition
======  ====================================  ==================================
 V1      ``{{ bio|unordered_list }}``           none at all
         ``{{ bio|safeseq }}``
 V2      ``{% render_slot p %}``                uses component slots -- djust's
                                                OWN tag, no app code
 V3      bare ``{{ p }}`` after an earlier      one prior ``mark_safe`` on the
         ``mark_safe`` on the same key          same key
 V4      ``{{ p|escape|safe }}``                ``|safe`` in the chain
 V5      ``{{ p|linenumbers|safe }}``           ``|safe`` in the chain
======  ====================================  ==================================

**What "LIVE" means here.** A rendered fragment is live when the browser will
construct a real element from it -- not when the payload survives as a
substring. The two are different: the VDOM parses an element and reorders its
attributes, so a substring scan reports a genuine XSS as inert (that exact
instrument error hid one of these during measurement). Every assertion below
therefore checks the ELEMENT shape, and pairs it with a positive assertion
that the payload arrived escaped -- so a test cannot pass by the payload
never reaching the output at all.

The 1.1.1 fixes are re-implementations against 1.1.0's own code, not
cherry-picks: ``main``'s versions are built on an ``InputSafety`` /
per-key-grant model this release does not have. Where a faithful port would
need that machinery, these fixes over-escape instead. Over-escaping is a
rendering bug; under-escaping is the vulnerability.
"""

from __future__ import annotations

import re

import pytest

pytest.importorskip("django")

from django.utils.safestring import mark_safe  # noqa: E402

from djust import LiveView, _rust  # noqa: E402
from djust._rust import render_template  # noqa: E402
from djust.decorators import event_handler  # noqa: E402
from djust.mixins.rust_bridge import _collect_safe_keys  # noqa: E402
from djust.serialization import normalize_django_value  # noqa: E402

HOSTILE = "<img src=x onerror=alert(1)>"
ESCAPED = "&lt;img src=x onerror=alert(1)&gt;"

# An `<img ...>` OPENING TAG carrying an onerror handler, tolerant of attribute
# order and of the `dj-id` the VDOM injects. This is the shape a browser
# actually executes.
_LIVE_ELEMENT = re.compile(r"<\s*img\b[^>]*\bonerror\s*=", re.I)


class _StaleGrantView(LiveView):
    """Mount renders a ``mark_safe``d ``p``; the event replaces it with a
    hostile one. Module-level so ``LIVEVIEW_ALLOWED_MODULES=[__name__]`` can
    reach it over a real WebSocket mount."""

    template = (
        '<div dj-view="djust.tests.test_security_1_1_1_backports._StaleGrantView" '
        'dj-id="0">{{ p }}</div>'
    )

    def mount(self, request, **kwargs):
        self.p = mark_safe("<b>trusted</b>")

    @event_handler()
    def go_hostile(self, **kwargs):
        self.p = HOSTILE


def _walk_nodes(obj):
    """Yield every dict in a decoded frame tree that looks like a VDOM node."""
    if isinstance(obj, dict):
        if "tag" in obj:
            yield obj
        for v in obj.values():
            yield from _walk_nodes(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_nodes(v)


def _element_nodes_carrying_a_handler(frames):
    """Element (non-``#``-prefixed) nodes carrying an ``on*`` attribute."""
    out = []
    for node in _walk_nodes(frames):
        tag = node.get("tag") or ""
        if tag.startswith("#"):
            continue  # `#text` / `#comment` -- written with textContent
        attrs = node.get("attrs") or {}
        if any(k.lower().startswith("on") for k in attrs):
            out.append(node)
    return out


def _text_nodes_carrying_the_payload(frames):
    return [
        n
        for n in _walk_nodes(frames)
        if (n.get("tag") or "").startswith("#") and "onerror" in (n.get("text") or "")
    ]


def _html_fields(frames):
    """Every raw-html string field in the frames (mount / html_update)."""
    out = []

    def walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == "html" and isinstance(v, str):
                    out.append(v)
                else:
                    walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)

    walk(frames)
    return out


def assert_inert(out: str, *, cell: str) -> None:
    """The payload reached the output, and reached it escaped."""
    assert not _LIVE_ELEMENT.search(out), f"{cell}: rendered a LIVE element: {out!r}"
    assert "onerror" in out, (
        f"{cell}: the payload never reached the output at all, so this "
        f"assertion proves nothing: {out!r}"
    )


class TestV1UnorderedListAndSafeseqOnAString:
    """V1 -- the only two cells needing no precondition whatsoever.

    Both filter names sit in the renderer's name-based ``SAFE_OUTPUT_FILTERS``
    list, which suppresses auto-escaping on the filter NAME alone. Both
    returned a non-sequence value unchanged, so the suppression applied to a
    value nothing had escaped.
    """

    def test_unordered_list_on_a_hostile_string_is_inert(self):
        out = render_template("{{ bio|unordered_list }}", {"bio": HOSTILE})
        assert_inert(out, cell="{{ bio|unordered_list }}")

    def test_safeseq_on_a_hostile_string_is_inert(self):
        out = render_template("{{ bio|safeseq }}", {"bio": HOSTILE})
        assert_inert(out, cell="{{ bio|safeseq }}")

    def test_unordered_list_still_renders_a_real_list(self):
        """The other direction: the list arm is what the filter is FOR."""
        out = render_template("{{ bio|unordered_list }}", {"bio": ["a", "b"]})
        assert out == "\t<li>a</li>\n\t<li>b</li>", out

    def test_unordered_list_still_escapes_a_hostile_ITEM(self):
        out = render_template("{{ bio|unordered_list }}", {"bio": [HOSTILE]})
        assert out == f"\t<li>{ESCAPED}</li>", out

    def test_a_non_string_scalar_is_unharmed(self):
        """Escaping the fallback arm must not mangle an ordinary value."""
        assert render_template("{{ n|unordered_list }}", {"n": 42}) == "42"


class TestV2RenderSlot:
    """V2 -- djust's OWN tag, reachable with no app-written handler.

    The Rust engine inserts a tag handler's return into the page verbatim.
    ``RenderSlotTagHandler`` has two exits that merely echo a value out of the
    render context, and those were emitting attacker-controlled strings raw.

    Handler registration is ``register_with_rust_engine()`` -- importing the
    module is not enough, so a test that only imports would exercise nothing.
    """

    @staticmethod
    def _register():
        from djust.components.rust_handlers import register_with_rust_engine

        register_with_rust_engine()

    def test_a_bare_hostile_context_string_is_inert(self):
        self._register()
        out = render_template("{% render_slot p %}", {"p": HOSTILE})
        assert_inert(out, cell="{% render_slot p %}")

    def test_a_slots_own_markup_still_renders_live(self):
        """The opposite direction, and the reason a blanket escape is wrong.

        A slot entry's ``content`` is the pre-rendered block body the parent
        already escaped -- the trust status Django gives ``{% include %}``'s
        output. Escaping it again renders every function component's markup
        as visible text.
        """
        self._register()
        out = render_template(
            "{% render_slot p %}", {"p": {"content": "<strong>rendered</strong>"}}
        )
        assert out == "<strong>rendered</strong>", out

    def test_the_direct_python_caller_path_is_also_covered(self):
        """The path-resolution shape reaches a different exit than the engine's.

        `_LOOKS_LIKE_PATH` matches a bare identifier, so a direct caller
        resolves ``p`` itself and lands in ``_render_value``'s trailing
        ``str(value)`` -- a second exit, needing its own case (#1104).
        """
        from djust.components.function_component import RenderSlotTagHandler

        out = RenderSlotTagHandler().render(["p"], {"p": HOSTILE})
        assert_inert(out, cell="RenderSlotTagHandler().render(['p'], ...)")


def _sync(view, value, key: str = "p") -> None:
    """The production sequence ``_sync_state_to_rust`` performs.

    Mirrors the bridge rather than calling ``mark_safe_keys`` directly,
    including its ``if safe_keys:`` guard -- the bug lived in the
    *interaction* between what the bridge sends and what Rust does with it,
    and the guard is precisely why a caller-discipline fix would not have
    closed it. A proxy that always calls ``mark_safe_keys`` is a different
    program.
    """
    normalized = normalize_django_value({key: value})
    keys: list[str] = []
    for k, v in normalized.items():
        keys.extend(_collect_safe_keys(v, k))
    view.update_state(normalized)
    if keys:  # the bridge's own guard -- rust_bridge.py `if safe_keys:`
        view.mark_safe_keys(keys)


class TestV3StaleSafeGrant:
    """V3 -- a grant that outlives the value it was granted for.

    ``mark_safe_keys`` accumulated into a set nothing ever cleared, so a key
    marked safe once stayed safe for the lifetime of the view -- which spans
    every event on a WebSocket connection. The fix ties the grant to the
    value: replacing a key's value in ``update_state`` revokes it.
    """

    def test_a_grant_does_not_survive_into_the_next_render(self):
        view = _rust.RustLiveView("{{ p }}")
        _sync(view, mark_safe("<b>trusted</b>"))
        assert view.render() == "<b>trusted</b>", "the legitimate grant must work"

        _sync(view, HOSTILE)
        assert_inert(view.render(), cell="stale grant, second render")

    def test_the_grant_must_be_re_earned_every_render(self):
        """One clear is not enough."""
        view = _rust.RustLiveView("{{ p }}")
        for _ in range(3):
            _sync(view, mark_safe("<b>ok</b>"))
            assert view.render() == "<b>ok</b>"
            _sync(view, HOSTILE)
            assert_inert(view.render(), cell="stale grant, repeated")

        # safe -> hostile -> safe: the grant comes back when it is re-sent.
        _sync(view, mark_safe("<i>y</i>"))
        assert view.render() == "<i>y</i>"

    def test_revocation_is_scoped_to_the_key_being_replaced(self):
        """The over-escape direction. ``update_state`` is a partial merge.

        Updating ``p`` must not revoke an untouched ``q``'s still-valid grant.
        """
        view = _rust.RustLiveView("{{ p }}|{{ q }}")
        view.update_state(normalize_django_value({"p": "x", "q": "<b>q</b>"}))
        view.mark_safe_keys(["q"])
        assert view.render() == "x|<b>q</b>"

        _sync(view, HOSTILE)  # touches p only
        out = view.render()
        assert out.endswith("|<b>q</b>"), f"q's untouched grant was revoked: {out!r}"
        assert_inert(out, cell="scoped revocation")

    def test_descendants_of_a_replaced_key_are_revoked_too(self):
        """A grant on ``p.0`` cannot outlive a replacement of ``p``."""
        view = _rust.RustLiveView("{{ p.0 }}")
        view.update_state(normalize_django_value({"p": [mark_safe("<b>ok</b>")]}))
        view.mark_safe_keys(["p.0"])
        assert view.render() == "<b>ok</b>"

        view.update_state(normalize_django_value({"p": [HOSTILE]}))
        assert_inert(view.render(), cell="descendant grant")


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_v3_over_a_real_websocket_connection():
    """V3 is DEFINED over a connection's lifetime, so it is measured there.

    A renderer-level test drives ``RustLiveView`` directly; the vulnerability
    is that the grant persists across the events of one ``LiveViewConsumer``
    session. Only a real ``WebsocketCommunicator`` round-trip -- mount with a
    ``mark_safe``d value, then an event replacing it with a hostile one --
    exercises the path the advisory describes.
    """
    pytest.importorskip("channels")
    from asgiref.sync import sync_to_async
    from channels.testing import WebsocketCommunicator
    from django.contrib.sessions.backends.db import SessionStore
    from django.test import override_settings

    from djust.websocket import LiveViewConsumer

    def _create_session():
        s = SessionStore()
        s.create()
        return s.session_key

    session_key = await sync_to_async(_create_session)()

    class _ScopeSession:
        def __init__(self, key):
            self.session_key = key

    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        communicator.scope["session"] = _ScopeSession(session_key)
        connected, _ = await communicator.connect()
        assert connected
        await communicator.receive_json_from(timeout=2)  # connect frame

        await communicator.send_json_to(
            {"type": "mount", "view": f"{__name__}._StaleGrantView", "url": "/grant/"}
        )
        mount = None
        for _ in range(6):
            mount = await communicator.receive_json_from(timeout=3)
            if mount.get("type") == "mount":
                break
        assert mount.get("type") == "mount", mount
        # The VDOM injects `dj-id`, so match the ELEMENT rather than the
        # literal source string.
        mount_html = mount.get("html", "")
        assert re.search(r"<b\b[^>]*>trusted</b>", mount_html), (
            f"the legitimate mark_safe must render live at mount: {mount!r}"
        )

        await communicator.send_json_to(
            {"type": "event", "event": "go_hostile", "params": {}, "ref": 1}
        )
        frames = []
        for _ in range(6):
            f = await communicator.receive_json_from(timeout=3)
            frames.append(f)
            if f.get("type") in ("patch", "html_update"):
                break
        await communicator.disconnect()

        import json

        # Non-vacuity: the payload must have reached the wire at all.
        assert "onerror" in json.dumps(frames), f"the payload never reached any frame: {frames!r}"

        # Liveness on the wire is STRUCTURAL, not textual. A `#text` node is
        # written with `textContent` and is inert no matter what characters it
        # carries; an ELEMENT node with an `onerror` attribute is executed.
        # Regex-scanning the serialized frame conflates the two -- it reports
        # a correct fix as a failure, and (in the other, worse direction) an
        # attribute-reordered element as inert.
        assert _element_nodes_carrying_a_handler(frames) == [], (
            f"a stale grant produced a live ELEMENT node on the wire: {frames!r}"
        )
        for html in _html_fields(frames):
            assert not _LIVE_ELEMENT.search(html), (
                f"a stale grant produced live html on the wire: {html!r}"
            )
        # The positive half, and the discriminator against the vulnerable
        # build: the payload arrives as a text node. Pre-fix it arrived as
        # `{"tag": "img", "attrs": {"onerror": "alert(1)"}}`.
        assert _text_nodes_carrying_the_payload(frames), (
            f"expected the payload to arrive as a #text node: {frames!r}"
        )


class TestV4EscapeThenSafe:
    """V4 -- ``escape`` was a no-op, and ``|safe`` suppresses what it deferred to.

    The filter returned its input unchanged on the theory that render-time
    auto-escaping handles it. A trailing ``|safe`` suppresses exactly that,
    so the chain performed no escaping anywhere.
    """

    def test_escape_then_safe_is_inert(self):
        out = render_template("{{ p|escape|safe }}", {"p": HOSTILE})
        assert_inert(out, cell="{{ p|escape|safe }}")
        assert out == ESCAPED, out

    def test_escape_alone_is_not_escaped_twice(self):
        """The regression the fix must not introduce."""
        out = render_template("{{ p|escape }}", {"p": HOSTILE})
        assert out == ESCAPED, out

    def test_escape_in_an_attribute_still_escapes_quotes(self):
        """`escape` now suppresses the attribute-context escape, so it must
        itself cover `"` and `'` -- as Django's `escape()` does."""
        out = render_template('<a href="{{ p|escape }}">x</a>', {"p": '"><script>'})
        assert out == '<a href="&quot;&gt;&lt;script&gt;">x</a>', out

    def test_a_later_plain_filter_re_taints(self):
        """Safeness is reported at the LAST-filter position, not by NAME.

        This is why the fix does not add `escape` to the renderer's
        name-based `SAFE_OUTPUT_FILTERS` list: that list matches ANY position
        in the chain, so `{{ p|escape|<anything> }}` would emit the last
        filter's output raw -- closing two holes and opening a third.

        `upper` reports not-safe, so the already-escaped text is escaped a
        second time. The `&amp;` is the witness that the grant did NOT
        survive past `escape`'s own position.
        """
        out = render_template("{{ p|escape|upper }}", {"p": HOSTILE})
        assert "&amp;LT;IMG" in out, f"the grant leaked past its own position: {out!r}"
        assert not _LIVE_ELEMENT.search(out), out


class TestV5LinenumbersThenSafe:
    """V5 -- ``linenumbers`` emitted raw lines and leaned on auto-escaping."""

    def test_linenumbers_then_safe_is_inert(self):
        out = render_template("{{ p|linenumbers|safe }}", {"p": HOSTILE})
        assert_inert(out, cell="{{ p|linenumbers|safe }}")
        assert out == f"1. {ESCAPED}", out

    def test_linenumbers_alone_is_not_escaped_twice(self):
        out = render_template("{{ p|linenumbers }}", {"p": HOSTILE})
        assert out == f"1. {ESCAPED}", out

    def test_numbering_and_multiline_shape_are_unchanged(self):
        assert render_template("{{ p|linenumbers }}", {"p": "a\nb"}) == "1. a\n2. b"

    def test_a_later_plain_filter_re_taints(self):
        """Last-filter scope, as for `escape` -- see the V4 sibling."""
        out = render_template("{{ p|linenumbers|upper }}", {"p": HOSTILE})
        assert "&amp;LT;IMG" in out, f"the grant leaked past its own position: {out!r}"
        assert not _LIVE_ELEMENT.search(out), out


class TestV6LinebreaksThenSafe:
    """V6 -- ``linebreaks`` / ``linebreaksbr``, the same class as V5.

    NOT one of the five the advisory classification named. Found by sweeping
    every built-in filter against live Django and asking the only question
    that means anything -- *where is djust live and Django not* -- rather than
    "where is the output live", which flags every `|safe` cell and is
    therefore no measurement at all.

    It is the most reachable of the ``|safe``-preconditioned cells, because on
    1.1.0 ``|safe`` was **mandatory** for the filter to work: both filters
    emit ``<p>``/``<br>`` and were not reported safe, so the bare spelling
    escaped the filter's OWN tags and rendered literal ``<p>`` text on the
    page. A developer who wanted paragraph breaks had to write
    ``{{ bio|linebreaks|safe }}`` -- and that spelling emitted the content
    live. The only working spelling was the vulnerable one.
    """

    def test_linebreaks_then_safe_is_inert(self):
        out = render_template("{{ p|linebreaks|safe }}", {"p": HOSTILE})
        assert_inert(out, cell="{{ p|linebreaks|safe }}")
        assert out == f"<p>{ESCAPED}</p>", out

    def test_linebreaksbr_then_safe_is_inert(self):
        out = render_template("{{ p|linebreaksbr|safe }}", {"p": HOSTILE})
        assert_inert(out, cell="{{ p|linebreaksbr|safe }}")
        assert out == ESCAPED, out

    def test_the_bare_spelling_now_emits_its_own_tags(self):
        """The regression half, and why this is not merely an over-escape fix.

        Pre-fix, `{{ p|linebreaks }}` produced `&lt;p&gt;a&lt;/p&gt;` -- the
        filter's own markup escaped, i.e. visibly broken output. It now emits
        real tags around escaped content, which is what Django does.
        """
        out = render_template("{{ p|linebreaks }}", {"p": "a\n\nb"})
        assert out.startswith("<p>a</p>"), out
        assert "&lt;p&gt;" not in out, f"the filter escaped its own tags: {out!r}"

    def test_the_bare_spelling_still_escapes_the_content(self):
        out = render_template("{{ p|linebreaks }}", {"p": HOSTILE})
        assert_inert(out, cell="{{ p|linebreaks }}")
        assert out == f"<p>{ESCAPED}</p>", out

    def test_linebreaksbr_bare_emits_its_own_tags(self):
        out = render_template("{{ p|linebreaksbr }}", {"p": "a\nb"})
        assert out == "a<br>b", out

    def test_a_later_plain_filter_re_taints(self):
        out = render_template("{{ p|linebreaks|upper }}", {"p": HOSTILE})
        assert "&amp;LT;IMG" in out, f"the grant leaked past its own position: {out!r}"
        assert not _LIVE_ELEMENT.search(out), out
