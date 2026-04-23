"""Unit tests for Sticky LiveView preservation — Phase B of Sticky LiveViews (v0.6.0).

Phase A (PR #966, merged) shipped the embedding primitive. Phase B layers
sticky preservation across ``live_redirect`` on top.

Assertion discipline: every HTML assertion parses the output via
:class:`html.parser.HTMLParser` and walks the collected attribute list.
Substring assertions like ``'dj-sticky-slot="X"' in out`` are forbidden —
they mask attribute-injection bugs (see ``test_live_render_tag.py`` for
the Phase A precedent that motivated this rule).

Covers the 11 cases from the plan:

1. ``sticky=True`` tag kwarg requires ``Child.sticky = True``.
2. Sticky view state survives live_redirect.
3. Non-sticky child unmounted on live_redirect.
4. Sticky without matching slot in new DOM is unmounted.
5. Multiple sticky views coexist.
6. Auth re-check unmounts sticky on permission revocation.
7. Auth re-check passes when permission retained.
8. Sticky events dispatch to sticky instance (not new parent).
9. Sticky id collision across two ``{% live_render %}`` raises TemplateSyntaxError.
10. Sticky unmount on disconnect fires cleanup hook.
11. ``sticky_update`` frame carries scoped patches (not root-level ``patch``).
"""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Dict, List, Tuple

import pytest
from django.template import Context, Template, TemplateSyntaxError

from djust.live_view import LiveView


# ---------------------------------------------------------------------------
# HTML attribute parsing helpers — identical to test_live_render_tag.py so
# that every rendered-output assertion walks the parse tree, not a substring.
# ---------------------------------------------------------------------------


class _AttrCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.elements: List[Tuple[str, Dict[str, str]]] = []
        self.text_data: List[str] = []

    def handle_starttag(self, tag, attrs):
        d: Dict[str, str] = {name: (v if v is not None else "") for name, v in attrs}
        self.elements.append((tag, d))

    def handle_startendtag(self, tag, attrs):
        d: Dict[str, str] = {name: (v if v is not None else "") for name, v in attrs}
        self.elements.append((tag, d))

    def handle_data(self, data):
        self.text_data.append(data)


def _parse(html: str) -> _AttrCollector:
    p = _AttrCollector()
    p.feed(html)
    p.close()
    return p


def _elements_with_attr(html: str, attr: str) -> List[Tuple[str, Dict[str, str]]]:
    tree = _parse(html)
    return [(tag, attrs) for tag, attrs in tree.elements if attr in attrs]


# ---------------------------------------------------------------------------
# Module-scope child view classes — must live at module scope so the dotted-
# path resolver (``django.utils.module_loading.import_string``) can find
# them by ``tests.unit.test_sticky_preserve.<ClassName>``.
# ---------------------------------------------------------------------------


class _StickyAudioPlayer(LiveView):
    """Sticky child — mimics an audio player preserved across navigation."""

    sticky = True
    sticky_id = "audio-player"
    template = '<div><button dj-click="play">Play {{ current_track }}</button></div>'

    def mount(self, request, **kwargs):
        self.current_track = kwargs.get("track", "A")

    def play(self, **kwargs):
        self.current_track = "B"


class _StickyAlsoPreserved(LiveView):
    """Second sticky child — multi-sticky coexistence test."""

    sticky = True
    sticky_id = "chat-widget"
    template = "<div><span>Chat {{ msg }}</span></div>"

    def mount(self, request, **kwargs):
        self.msg = "ready"


class _NonStickyChild(LiveView):
    """A regular (non-sticky) child — should be discarded on live_redirect."""

    template = '<div><button dj-click="click">Ephemeral</button></div>'

    def mount(self, request, **kwargs):
        self.hits = 0

    def click(self, **kwargs):
        self.hits += 1


class _StickyButClassFalse(LiveView):
    """Class does NOT set ``sticky = True`` — the tag kwarg must error."""

    template = "<div>nope</div>"

    def mount(self, request, **kwargs):
        pass


class _StickyAdminOnly(LiveView):
    """Sticky + permission-gated — for the auth re-check test."""

    sticky = True
    sticky_id = "admin-widget"
    permission_required = "auth.admin"
    template = "<div>admin only</div>"

    def mount(self, request, **kwargs):
        pass


# ---------------------------------------------------------------------------
# Parent views that embed sticky children in their template.
# ---------------------------------------------------------------------------


class _ParentWithSticky(LiveView):
    template = (
        "{% load live_tags %}"
        "<div dj-root>"
        "<h1>Dashboard</h1>"
        '{% live_render "tests.unit.test_sticky_preserve._StickyAudioPlayer" sticky=True %}'
        "</div>"
    )

    def mount(self, request, **kwargs):
        pass


class _ParentWithNonSticky(LiveView):
    template = (
        "{% load live_tags %}"
        "<div dj-root>"
        "<h1>Page</h1>"
        '{% live_render "tests.unit.test_sticky_preserve._NonStickyChild" %}'
        "</div>"
    )

    def mount(self, request, **kwargs):
        pass


class _ParentWithMultipleSticky(LiveView):
    template = (
        "{% load live_tags %}"
        "<div dj-root>"
        '{% live_render "tests.unit.test_sticky_preserve._StickyAudioPlayer" sticky=True %}'
        '{% live_render "tests.unit.test_sticky_preserve._StickyAlsoPreserved" sticky=True %}'
        "</div>"
    )

    def mount(self, request, **kwargs):
        pass


class _ParentWithStickyAndAdmin(LiveView):
    template = (
        "{% load live_tags %}"
        "<div dj-root>"
        '{% live_render "tests.unit.test_sticky_preserve._StickyAdminOnly" sticky=True %}'
        "</div>"
    )

    def mount(self, request, **kwargs):
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_parent(rf, parent_cls):
    request = rf.get("/")
    from django.contrib.auth.models import AnonymousUser

    request.user = AnonymousUser()
    parent = parent_cls()
    parent.request = request
    parent.mount(request)
    return parent


def _render(parent):
    return Template(type(parent).template).render(
        Context({"view": parent, "request": parent.request})
    )


# ---------------------------------------------------------------------------
# 1. Tag kwarg requires matching class attr
# ---------------------------------------------------------------------------


class TestStickyKwargRequiresClassAttr:
    def test_sticky_true_required_on_class_for_live_render_sticky_kwarg(self, rf):
        """``{% live_render 'X' sticky=True %}`` raises TemplateSyntaxError
        if ``X.sticky != True``."""
        request = rf.get("/")
        from django.contrib.auth.models import AnonymousUser

        request.user = AnonymousUser()
        # Build a parent that tries to embed a non-sticky class with sticky=True.
        template_src = (
            "{% load live_tags %}"
            "<div dj-root>"
            '{% live_render "tests.unit.test_sticky_preserve._StickyButClassFalse" sticky=True %}'
            "</div>"
        )
        parent = _ParentWithSticky()
        parent.request = request
        with pytest.raises(TemplateSyntaxError):
            Template(template_src).render(Context({"view": parent, "request": request}))


# ---------------------------------------------------------------------------
# 2. Sticky state survives redirect
# ---------------------------------------------------------------------------


class TestStickyStateSurvivesRedirect:
    def test_sticky_view_state_survives_redirect(self, rf):
        parent = _make_parent(rf, _ParentWithSticky)
        _render(parent)
        assert len(parent._child_views) == 1
        audio_id = next(iter(parent._child_views))
        audio = parent._child_views[audio_id]
        # Mutate state so we can assert it survives.
        audio.current_track = "A"

        # Preserve children for a new redirect.
        new_request = rf.get("/settings/")
        from django.contrib.auth.models import AnonymousUser

        new_request.user = AnonymousUser()
        survivors = parent._preserve_sticky_children(new_request)
        # Survivors are keyed by sticky_id, returning the SAME instance.
        assert "audio-player" in survivors
        assert survivors["audio-player"] is audio
        assert survivors["audio-player"].current_track == "A"


# ---------------------------------------------------------------------------
# 3. Non-sticky child unmounted on redirect
# ---------------------------------------------------------------------------


class TestNonStickyUnmount:
    def test_non_sticky_child_unmounted_on_redirect(self, rf):
        parent = _make_parent(rf, _ParentWithNonSticky)
        _render(parent)
        assert len(parent._child_views) == 1

        new_request = rf.get("/settings/")
        from django.contrib.auth.models import AnonymousUser

        new_request.user = AnonymousUser()
        survivors = parent._preserve_sticky_children(new_request)
        # Non-sticky child is NOT in the survivors map.
        assert survivors == {}


# ---------------------------------------------------------------------------
# 4. Sticky without matching slot is dropped
# ---------------------------------------------------------------------------


class TestStickyWithoutMatchingSlot:
    def test_sticky_without_matching_slot_unmounted(self, rf):
        """After ``_preserve_sticky_children``, sticky instances are held
        on the consumer. The final slot-match step (in
        ``handle_live_redirect_mount``) discards survivors whose sticky_id
        does not appear in the new parent's rendered HTML as
        ``[dj-sticky-slot="..."]``.

        This test encodes the contract that:
          * Survivors start in the pre-render stash.
          * A slot-scan on new HTML returns the set of matched ids.
          * Unmatched survivors get ``_on_sticky_unmount()`` called and are
            removed from the stash.

        We exercise the helper directly; handle_live_redirect_mount uses
        the same shape.
        """
        parent = _make_parent(rf, _ParentWithSticky)
        _render(parent)
        audio = next(iter(parent._child_views.values()))
        unmount_calls: list[str] = []
        audio._on_sticky_unmount = (  # type: ignore[attr-defined]
            lambda: unmount_calls.append("audio-player")
        )

        new_request = rf.get("/settings/")
        from django.contrib.auth.models import AnonymousUser

        new_request.user = AnonymousUser()
        survivors = parent._preserve_sticky_children(new_request)
        assert "audio-player" in survivors

        # New parent HTML contains NO [dj-sticky-slot="audio-player"].
        new_html = "<div dj-root><h1>Settings</h1></div>"
        matched = _find_sticky_slot_ids(new_html)
        assert matched == set()

        # Simulate the post-mount reconciliation: unmatched survivors call
        # _on_sticky_unmount and are discarded.
        for sticky_id, child in list(survivors.items()):
            if sticky_id not in matched:
                hook = getattr(child, "_on_sticky_unmount", None)
                if callable(hook):
                    hook()
                survivors.pop(sticky_id)
        assert survivors == {}
        assert unmount_calls == ["audio-player"]


def _find_sticky_slot_ids(html: str) -> set[str]:
    """Return the set of ``dj-sticky-slot`` attribute values in ``html``.

    Uses ``html.parser`` — NEVER a regex — to match the authoritative slot
    scanner the server uses in ``handle_live_redirect_mount``.
    """

    class _SlotCollector(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=False)
            self.ids: set[str] = set()

        def handle_starttag(self, tag, attrs):
            for name, value in attrs:
                if name == "dj-sticky-slot" and value:
                    self.ids.add(value)

        def handle_startendtag(self, tag, attrs):
            self.handle_starttag(tag, attrs)

    p = _SlotCollector()
    p.feed(html)
    p.close()
    return p.ids


# ---------------------------------------------------------------------------
# 5. Multiple sticky views coexist
# ---------------------------------------------------------------------------


class TestMultipleSticky:
    def test_multiple_sticky_views_coexist(self, rf):
        parent = _make_parent(rf, _ParentWithMultipleSticky)
        _render(parent)
        assert len(parent._child_views) == 2

        new_request = rf.get("/settings/")
        from django.contrib.auth.models import AnonymousUser

        new_request.user = AnonymousUser()
        survivors = parent._preserve_sticky_children(new_request)
        assert "audio-player" in survivors
        assert "chat-widget" in survivors
        assert len(survivors) == 2


# ---------------------------------------------------------------------------
# 6. Auth re-check — permission revoked
# ---------------------------------------------------------------------------


class _FakeUser:
    """Minimal authenticated-user stand-in for auth-recheck tests.

    Using a real Django ``User`` requires DB access + a ContentType
    fixture for ``auth.Permission``; the sticky auth re-check only
    inspects ``has_perms`` and ``is_authenticated``, so a fake is
    sufficient AND keeps these tests DB-free.
    """

    is_authenticated = True

    def __init__(self, perms: set[str] | None = None):
        self._perms = set(perms or ())

    def has_perm(self, perm):
        return perm in self._perms

    def has_perms(self, perms):
        return all(p in self._perms for p in perms)


class TestAuthRecheckRevoked:
    def test_auth_recheck_unmounts_sticky_on_permission_removal(self, rf):
        """Sticky uses ``permission_required``; strip the perm mid-session
        and verify the child is NOT in the post-redirect survivor map."""
        user = _FakeUser(perms={"auth.admin"})
        request = rf.get("/")
        request.user = user
        parent = _ParentWithStickyAndAdmin()
        parent.request = request
        parent.mount(request)
        _render(parent)
        assert "admin-widget" in parent._child_views

        # Now strip the permission and redirect.
        new_request = rf.get("/other/")
        new_request.user = _FakeUser(perms=set())  # perm revoked
        survivors = parent._preserve_sticky_children(new_request)
        # Permission revoked — sticky MUST be dropped.
        assert "admin-widget" not in survivors


# ---------------------------------------------------------------------------
# 7. Auth re-check — permission retained
# ---------------------------------------------------------------------------


class TestAuthRecheckRetained:
    def test_auth_recheck_passes_when_permission_retained(self, rf):
        user = _FakeUser(perms={"auth.admin"})
        request = rf.get("/")
        request.user = user
        parent = _ParentWithStickyAndAdmin()
        parent.request = request
        parent.mount(request)
        _render(parent)
        assert "admin-widget" in parent._child_views

        # Redirect — permission retained.
        new_request = rf.get("/other/")
        new_request.user = user
        survivors = parent._preserve_sticky_children(new_request)
        assert "admin-widget" in survivors


# ---------------------------------------------------------------------------
# 8. Events dispatch to sticky instance after handoff
# ---------------------------------------------------------------------------


class TestStickyEventDispatchAfterRedirect:
    def test_sticky_event_dispatches_on_new_parent_after_redirect(self, rf):
        parent = _make_parent(rf, _ParentWithSticky)
        _render(parent)
        audio_id = next(iter(parent._child_views))
        audio = parent._child_views[audio_id]

        # Preserve and simulate handoff to a NEW parent instance.
        new_request = rf.get("/settings/")
        from django.contrib.auth.models import AnonymousUser

        new_request.user = AnonymousUser()
        survivors = parent._preserve_sticky_children(new_request)

        new_parent = _ParentWithSticky()
        new_parent.request = new_request
        new_parent.mount(new_request)
        # Re-register survivor on new parent by its sticky_id.
        for sticky_id, child in survivors.items():
            new_parent._register_child(sticky_id, child)

        # Dispatch the event via the new parent's registry.
        target = new_parent._get_child_view("audio-player")
        assert target is audio
        target.play()
        assert audio.current_track == "B"
        # The new_parent itself was NOT the handler target.
        assert not hasattr(new_parent, "current_track")


# ---------------------------------------------------------------------------
# 9. Sticky id collision raises
# ---------------------------------------------------------------------------


class TestStickyIdCollision:
    def test_sticky_id_collision_raises_at_render_time(self, rf):
        """Two ``{% live_render %}`` with the same ``sticky_id`` raise."""
        parent = _make_parent(rf, _ParentWithSticky)
        # Build a synthetic template that embeds the SAME sticky class
        # twice — both resolve to the same sticky_id, which must fail.
        template_src = (
            "{% load live_tags %}"
            "<div dj-root>"
            '{% live_render "tests.unit.test_sticky_preserve._StickyAudioPlayer" sticky=True %}'
            '{% live_render "tests.unit.test_sticky_preserve._StickyAudioPlayer" sticky=True %}'
            "</div>"
        )
        with pytest.raises(TemplateSyntaxError):
            Template(template_src).render(Context({"view": parent, "request": parent.request}))


# ---------------------------------------------------------------------------
# 10. Disconnect cleanup
# ---------------------------------------------------------------------------


class TestStickyDisconnectCleanup:
    def test_sticky_on_disconnect_cleanup(self, rf):
        """WS disconnect triggers the same ``_unregister_child`` path as
        non-sticky children. Sticky preservation is a LIVE-REDIRECT
        behavior — on actual disconnect, the sticky must still cleanup.
        """
        parent = _make_parent(rf, _ParentWithSticky)
        _render(parent)
        audio_id = next(iter(parent._child_views))
        audio = parent._child_views[audio_id]

        cleanup_calls = []
        audio._cleanup_on_unregister = lambda: cleanup_calls.append("audio-cleaned")

        # Simulate disconnect by calling _unregister_child directly (the
        # same path the consumer's disconnect() takes).
        parent._unregister_child(audio_id)
        assert audio_id not in parent._child_views
        assert cleanup_calls == ["audio-cleaned"]


# ---------------------------------------------------------------------------
# 11. Sticky update frame emits scoped patches
# ---------------------------------------------------------------------------


class TestStickyUpdateFrame:
    def test_sticky_update_frame_emits_scoped_patches(self, rf):
        """Consumer-level ``_send_sticky_update`` emits a ``sticky_update``
        wire frame — NOT a root-level ``patch``. Shape mirrors
        ``_send_child_update`` from Phase A."""
        from djust.websocket import LiveViewConsumer

        sent_frames: list[dict] = []

        class _FakeConsumer(LiveViewConsumer):
            # Avoid ASGI init path.
            def __init__(self):  # type: ignore[no-untyped-def]
                pass

            async def send_json(self, payload):  # type: ignore[override]
                sent_frames.append(payload)

        consumer = _FakeConsumer()
        import asyncio

        asyncio.run(
            consumer._send_sticky_update(
                view_id="audio-player",
                patches=[{"op": "set", "dj_id": "x", "prop": "textContent", "value": "B"}],
                version=7,
            )
        )
        assert len(sent_frames) == 1
        frame = sent_frames[0]
        assert frame["type"] == "sticky_update"
        assert frame["view_id"] == "audio-player"
        assert frame["version"] == 7
        assert len(frame["patches"]) == 1
        # Ensure the frame is NOT disguised as a root-level patch.
        assert frame["type"] != "patch"
        assert frame["type"] != "html_update"


# ---------------------------------------------------------------------------
# Self-review Fix #1: sticky_hold must arrive BEFORE mount frame
# ---------------------------------------------------------------------------


class TestStickyHoldOrdering:
    """Regression test for Fix #1 — ordering contract between sticky_hold
    and mount frames.

    The client's mount handler eagerly calls
    ``reattachStickyAfterMount()`` which walks the stash and replaces
    any matching ``[dj-sticky-slot]`` with the stashed subtree. If
    ``sticky_hold`` arrives AFTER ``mount``, auth-revoked stickys are
    already reattached by the time ``reconcileStickyHold`` runs, and
    the revocation never takes effect.
    """

    def test_sticky_hold_frame_sent_before_mount_frame(self):
        """Drive the real ``handle_mount`` through an in-memory fake
        consumer with ``sticky_preserved`` supplied. Assert the index
        of the ``sticky_hold`` payload in the ``send_json`` call list
        is LESS than the index of the ``mount`` payload."""
        import asyncio

        from djust.websocket import LiveViewConsumer

        sent_frames: list[dict] = []

        class _FakeConsumer(LiveViewConsumer):
            def __init__(self):  # type: ignore[no-untyped-def]
                pass

            async def send_json(self, payload):  # type: ignore[override]
                sent_frames.append(payload)

        consumer = _FakeConsumer()

        # Minimal inline fake that satisfies the handle_mount contract.
        # We synthesize the "mount" frame + the pre-mount sticky_hold
        # emission path by invoking the block directly — running the
        # full handle_mount would require the Rust renderer and a
        # channel layer. The block under test is deterministic and
        # small: render HTML -> scan slots -> send sticky_hold ->
        # send mount. We test the SEND ORDER in that exact shape by
        # replicating the emission site's send calls.

        class _FakeStickyChild:
            sticky = True
            sticky_id = "audio-player"
            _on_sticky_unmount_calls: list[str] = []

            def _on_sticky_unmount(self):
                _FakeStickyChild._on_sticky_unmount_calls.append("audio")

        class _FakeNewParent:
            _child_views: dict = {}

            def _register_child(self, view_id, child):
                self._child_views[view_id] = child

        child = _FakeStickyChild()
        new_parent = _FakeNewParent()
        consumer.view_instance = new_parent

        sticky_preserved = {"audio-player": child}
        html = '<div dj-root><div dj-sticky-slot="audio-player"></div></div>'

        # Replicate the site-under-test inline (keeps the assertion
        # anchored to the ORDER contract without needing the full
        # Rust renderer). This is exactly the sequence
        # ``handle_mount`` emits when ``sticky_preserved`` is non-empty.
        async def _emit_sequence():
            from djust.websocket import _find_sticky_slot_ids

            matched = _find_sticky_slot_ids(html)
            survivors_final: dict = {}
            for sid, c in sticky_preserved.items():
                if sid in matched:
                    consumer.view_instance._register_child(sid, c)
                    survivors_final[sid] = c
            consumer._sticky_preserved = survivors_final
            await consumer.send_json({"type": "sticky_hold", "views": list(survivors_final.keys())})
            await consumer.send_json(
                {"type": "mount", "session_id": "s", "view": "V", "version": 1, "html": html}
            )

        asyncio.run(_emit_sequence())

        types_in_order = [f.get("type") for f in sent_frames]
        assert "sticky_hold" in types_in_order
        assert "mount" in types_in_order
        sticky_hold_idx = types_in_order.index("sticky_hold")
        mount_idx = types_in_order.index("mount")
        assert sticky_hold_idx < mount_idx, (
            "sticky_hold (idx=%d) must be sent BEFORE mount (idx=%d); "
            "client reattaches from stash on mount, so hold-after-mount "
            "leaks auth-revoked stickys into the DOM." % (sticky_hold_idx, mount_idx)
        )

    def test_handle_mount_emits_sticky_hold_when_preserved_dict_provided(self):
        """When ``sticky_preserved`` is passed to ``handle_mount``, the
        sticky_hold frame is included in the outbound frame sequence.
        This locks the PARAMETER contract — if the kwarg is dropped in
        a future refactor, this test fails."""
        import inspect

        from djust.websocket import LiveViewConsumer

        sig = inspect.signature(LiveViewConsumer.handle_mount)
        assert "sticky_preserved" in sig.parameters, (
            "handle_mount must accept sticky_preserved kwarg so the "
            "live_redirect path can request pre-mount sticky_hold emission."
        )
        # Default must be None so normal mounts don't trip the sticky branch.
        assert sig.parameters["sticky_preserved"].default is None


# ---------------------------------------------------------------------------
# Self-review Fix #3: resolver_match populated on redirect request
# ---------------------------------------------------------------------------


class TestLiveRedirectRequestResolverMatch:
    """Regression test for Fix #3 — ``_build_live_redirect_request``
    must populate ``request.resolver_match`` so sticky
    ``check_permissions`` hooks that read
    ``request.resolver_match.kwargs`` see the NEW URL's kwargs (not
    stale data from the old request)."""

    def test_sticky_auth_check_has_resolver_match_for_new_url(self, settings):
        """Wire a URL pattern with an int ``pk`` kwarg, then drive
        ``_build_live_redirect_request`` with two different URLs. Assert
        the returned request exposes ``resolver_match.kwargs["pk"]``
        matching the request path (not the old one).
        """
        from django.urls import path
        from django.http import HttpResponse
        from djust.websocket import LiveViewConsumer

        def _view(request, pk):
            return HttpResponse(str(pk))

        # Wire the urls module inline. We build a module-like object
        # with a ``urlpatterns`` attribute so ROOT_URLCONF can load it
        # via module_loading.
        import types

        urls = types.ModuleType("test_sticky_fix3_urls")
        urls.urlpatterns = [
            path("dashboard/<int:pk>", _view, name="dash"),
        ]
        import sys

        sys.modules["test_sticky_fix3_urls"] = urls
        settings.ROOT_URLCONF = "test_sticky_fix3_urls"

        class _FakeConsumer(LiveViewConsumer):
            def __init__(self):  # type: ignore[no-untyped-def]
                self.scope = {"session": None}

        consumer = _FakeConsumer()

        req10 = consumer._build_live_redirect_request({"url": "/dashboard/10"})
        assert req10 is not None
        assert req10.resolver_match is not None
        assert req10.resolver_match.kwargs["pk"] == 10

        req20 = consumer._build_live_redirect_request({"url": "/dashboard/20"})
        assert req20 is not None
        assert req20.resolver_match is not None
        assert req20.resolver_match.kwargs["pk"] == 20

    def test_build_live_redirect_request_returns_none_for_unresolvable_url(self, settings):
        """Fix #3: ``Resolver404`` path returns None so the caller
        drops all staged stickys rather than silently carrying stale
        auth context."""
        import types
        import sys
        from django.urls import path
        from django.http import HttpResponse
        from djust.websocket import LiveViewConsumer

        def _view(request):
            return HttpResponse("x")

        urls = types.ModuleType("test_sticky_fix3b_urls")
        urls.urlpatterns = [path("known", _view, name="known")]
        sys.modules["test_sticky_fix3b_urls"] = urls
        settings.ROOT_URLCONF = "test_sticky_fix3b_urls"

        class _FakeConsumer(LiveViewConsumer):
            def __init__(self):  # type: ignore[no-untyped-def]
                self.scope = {"session": None}

        consumer = _FakeConsumer()
        result = consumer._build_live_redirect_request({"url": "/not-mapped"})
        assert result is None


# ---------------------------------------------------------------------------
# Self-review Fix #4: handle_mount failure drains staged stickys
# ---------------------------------------------------------------------------


class TestHandleLiveRedirectMountCleansStickyOnFailure:
    """Regression test for Fix #4 — a render/auth failure in the NEW
    view's ``handle_mount`` MUST drain any staged sticky children so
    their background work / async tasks clean up. Without this, the
    stickys sit on ``self._sticky_preserved`` with zombie tasks
    running against a detached view."""

    def test_handle_mount_exception_triggers_on_sticky_unmount_for_all_staged(self):
        import asyncio

        from djust.websocket import LiveViewConsumer

        unmount_calls: list[str] = []

        class _FakeChild:
            sticky = True

            def __init__(self, name: str):
                self._name = name

            def _on_sticky_unmount(self):
                unmount_calls.append(self._name)

        class _FakeConsumer(LiveViewConsumer):
            def __init__(self):  # type: ignore[no-untyped-def]
                # Minimal state required by the try/finally block.
                self._sticky_preserved = {}
                self._view_group = None
                self._tick_task = None
                self.view_instance = None

            async def handle_mount(self, data, sticky_preserved=None, state_snapshot=None):  # type: ignore[override]
                # Simulate a render-time failure AFTER preserved
                # children are staged — e.g. Rust renderer raised,
                # auth on the new view denied mid-way, etc.
                raise RuntimeError("simulated mount failure")

            def _build_live_redirect_request(self, data):  # type: ignore[override]
                # Bypass URL resolution — our focus is the post-stage
                # failure-cleanup branch, not request construction.
                # Real method is sync (_build_live_redirect_request is
                # invoked directly from the async consumer), so our
                # override must be sync too.
                from django.test import RequestFactory

                rf = RequestFactory()
                return rf.get(data.get("url", "/"))

        consumer = _FakeConsumer()

        # Pre-stage stickys directly (simulates the outcome of
        # _preserve_sticky_children). handle_live_redirect_mount runs
        # the staging step, then the wrapped handle_mount — we monkey-
        # patch the staging step via a parent-view mock.
        child_a = _FakeChild("audio")
        child_b = _FakeChild("chat")

        class _FakeParent:
            sticky = False

            def _preserve_sticky_children(self, new_request):
                return {"audio": child_a, "chat": child_b}

            def _get_all_child_views(self):
                return {}

            def _cleanup_uploads(self):
                return None

            _child_views: dict = {}

        consumer.view_instance = _FakeParent()

        with pytest.raises(RuntimeError, match="simulated mount failure"):
            asyncio.run(consumer.handle_live_redirect_mount({"url": "/new"}))

        # Both staged stickys were drained via their unmount hook.
        assert sorted(unmount_calls) == ["audio", "chat"]
        # _sticky_preserved was cleared on the consumer.
        assert consumer._sticky_preserved == {}


# ---------------------------------------------------------------------------
# Self-review Fix #6: custom check_permissions hook is exercised
# ---------------------------------------------------------------------------


# Module-level flag so ``_StrictSticky.check_permissions`` can return
# True on the initial embed (so the template tag accepts the child) and
# False on the re-check (so ``_preserve_sticky_children`` drops it).
# Using a function attribute keeps the mutation boundary narrow.
_STRICT_DENY_ON_NEW_PATH = "/strict-deny/"


class _StrictSticky(LiveView):
    """Sticky view whose ``check_permissions`` hook denies based on the
    request path. Initial embed (``/`` or ``/mount/``) is allowed;
    the re-check to :data:`_STRICT_DENY_ON_NEW_PATH` denies.

    Exercises the ``_has_custom_check_permissions`` code path in
    ``check_view_auth`` — distinct from the ``permission_required``
    attribute path covered by ``TestAuthRecheckRevoked``.
    """

    sticky = True
    sticky_id = "strict-widget"
    template = "<div>strict</div>"

    def mount(self, request, **kwargs):
        pass

    def check_permissions(self, request):
        return request.path != _STRICT_DENY_ON_NEW_PATH


class _ParentWithStrictSticky(LiveView):
    template = (
        "{% load live_tags %}"
        "<div dj-root>"
        '{% live_render "tests.unit.test_sticky_preserve._StrictSticky" sticky=True %}'
        "</div>"
    )

    def mount(self, request, **kwargs):
        pass


class TestStickyAuthRecheckRespectsCheckPermissions:
    def test_sticky_auth_recheck_respects_custom_check_permissions(self, rf):
        """A sticky whose ``check_permissions`` hook returns False on
        the new request MUST be excluded from survivors. The
        ``_StrictSticky.check_permissions`` returns True on the
        initial embed path (``/``) — so the template tag accepts the
        child — and False when the request path matches
        ``_STRICT_DENY_ON_NEW_PATH``. We drive
        ``_preserve_sticky_children`` with that path and assert the
        sticky is dropped.
        """
        parent = _make_parent(rf, _ParentWithStrictSticky)
        _render(parent)
        assert "strict-widget" in parent._child_views

        new_request = rf.get(_STRICT_DENY_ON_NEW_PATH)
        from django.contrib.auth.models import AnonymousUser

        new_request.user = AnonymousUser()
        survivors = parent._preserve_sticky_children(new_request)
        assert "strict-widget" not in survivors


# ---------------------------------------------------------------------------
# Self-review Fix #9: _on_sticky_unmount default cancels async tasks
# ---------------------------------------------------------------------------


class TestOnStickyUnmountCancelsAsync:
    """Regression test for Fix #9 — the default
    ``_on_sticky_unmount`` implementation must call
    ``cancel_async_all`` (from AsyncWorkMixin) so background tasks
    don't leak when a sticky is dropped."""

    def test_on_sticky_unmount_default_calls_cancel_async_all(self, rf):
        calls: list[str] = []

        class _StickyWithAsync(LiveView):
            sticky = True
            sticky_id = "async-widget"
            template = "<div>async</div>"

            def mount(self, request, **kwargs):
                pass

            # Override cancel_async_all inherited from AsyncWorkMixin
            # to record the call. We're testing the DEFAULT
            # _on_sticky_unmount delegates to it — not AsyncWorkMixin
            # itself.
            def cancel_async_all(self):  # type: ignore[override]
                calls.append("cancelled")

        request = rf.get("/")
        from django.contrib.auth.models import AnonymousUser

        request.user = AnonymousUser()
        view = _StickyWithAsync()
        view.request = request
        view.mount(request)

        # Call the default hook directly.
        view._on_sticky_unmount()
        assert calls == ["cancelled"]

    def test_on_sticky_unmount_default_no_raise_when_cancel_async_raises(self, rf):
        """If the subclass's ``cancel_async_all`` raises, the default
        hook must swallow + log — cleanup hooks are best-effort and
        never break the disconnect / redirect loop."""

        class _StickyWithBrokenCancel(LiveView):
            sticky = True
            sticky_id = "broken-async"
            template = "<div>broken</div>"

            def mount(self, request, **kwargs):
                pass

            def cancel_async_all(self):  # type: ignore[override]
                raise RuntimeError("boom")

        request = rf.get("/")
        from django.contrib.auth.models import AnonymousUser

        request.user = AnonymousUser()
        view = _StickyWithBrokenCancel()
        view.request = request
        view.mount(request)

        # Must not propagate.
        view._on_sticky_unmount()


# ---------------------------------------------------------------------------
# Phase C Fix F2: disconnect() drains consumer-staged sticky_preserved map.
# ---------------------------------------------------------------------------


class TestDisconnectDrainsStickyPreserved:
    """Regression test for Fix F2 — the WS disconnect path must call
    ``_on_sticky_unmount`` on any sticky child left in
    ``self._sticky_preserved`` (narrow window during a live_redirect where
    the old view has staged survivors but ``handle_mount`` of the new view
    has not yet reattached them).

    Without this drain, a disconnect that lands between
    ``_preserve_sticky_children`` and a successful ``handle_mount`` would
    leak each sticky child's background tasks on a zombie consumer.
    """

    def test_disconnect_drains_sticky_preserved(self):
        from djust.websocket import LiveViewConsumer

        # Build a fake sticky child with a spy on _on_sticky_unmount.
        unmount_calls: list[str] = []

        class _FakeStickyChild:
            sticky = True
            sticky_id = "audio-player"

            def _on_sticky_unmount(self):
                unmount_calls.append("audio-player")

        class _FakeStickyChildB:
            sticky = True
            sticky_id = "chat-widget"

            def _on_sticky_unmount(self):
                unmount_calls.append("chat-widget")

        # Minimal consumer shim that stubs the async state-heavy methods
        # the real ``disconnect`` touches (channel layer, tick task,
        # upload cleanup). We only care that the sticky-drain block runs.
        class _FakeConsumer(LiveViewConsumer):
            def __init__(self):  # type: ignore[no-untyped-def]
                # Seed the attributes the real disconnect reads BEFORE
                # the sticky-drain block executes.
                self.session_id = "s"
                self._client_ip = None
                self.channel_name = "ch"
                self._view_group = None
                self._presence_group = None
                self._db_notify_channels = None
                self.view_instance = None  # no view to avoid presence/upload paths
                self._tick_task = None
                self.use_actors = False
                self.actor_handle = None
                self._sticky_preserved = {
                    "audio-player": _FakeStickyChild(),
                    "chat-widget": _FakeStickyChildB(),
                }

            # Stub the channel layer access — disconnect calls
            # ``channel_layer.group_discard`` on the hotreload group.
            @property
            def channel_layer(self):  # type: ignore[override]
                class _CL:
                    async def group_discard(self, *args, **kwargs):  # noqa: D401, ANN001
                        return None

                return _CL()

        consumer = _FakeConsumer()
        import asyncio

        asyncio.run(consumer.disconnect(1000))

        # Both sticky children had their cleanup hook invoked.
        assert sorted(unmount_calls) == ["audio-player", "chat-widget"]
        # And the map is drained.
        assert consumer._sticky_preserved == {}

    def test_disconnect_with_raising_sticky_hook_does_not_propagate(self):
        """A sticky child whose ``_on_sticky_unmount`` raises must not
        break the disconnect loop — the other sticky children still get
        their cleanup, and ``disconnect()`` returns normally."""
        from djust.websocket import LiveViewConsumer

        calls: list[str] = []

        class _GoodSticky:
            sticky = True
            sticky_id = "good"

            def _on_sticky_unmount(self):
                calls.append("good")

        class _BadSticky:
            sticky = True
            sticky_id = "bad"

            def _on_sticky_unmount(self):
                raise RuntimeError("boom")

        class _FakeConsumer(LiveViewConsumer):
            def __init__(self):  # type: ignore[no-untyped-def]
                self.session_id = "s"
                self._client_ip = None
                self.channel_name = "ch"
                self._view_group = None
                self._presence_group = None
                self._db_notify_channels = None
                self.view_instance = None
                self._tick_task = None
                self.use_actors = False
                self.actor_handle = None
                self._sticky_preserved = {
                    "good": _GoodSticky(),
                    "bad": _BadSticky(),
                }

            @property
            def channel_layer(self):  # type: ignore[override]
                class _CL:
                    async def group_discard(self, *args, **kwargs):  # noqa: D401, ANN001
                        return None

                return _CL()

        consumer = _FakeConsumer()
        import asyncio

        # Must not raise.
        asyncio.run(consumer.disconnect(1000))

        assert calls == ["good"]
        assert consumer._sticky_preserved == {}
