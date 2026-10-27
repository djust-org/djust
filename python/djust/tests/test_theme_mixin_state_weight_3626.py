"""``ThemeMixin`` must not put its rendered HTML in the view's state (#3626).

``ThemeMixin._setup_theme_context()`` used to set four PUBLIC attributes on the
view: ``theme_head``, ``theme_css``, ``theme_switcher`` and ``theme_presets``
(about 80 KB together). A public attribute is view state, so every one of them
was serialised into the Rust snapshot the state backend stores, into the legacy
session save, and (via the private ``_theme_css_raw``) into the private-state
save, even on a page whose template only prints the ``{% theme_head %}`` tag.

These tests drive the real paths: an HTTP GET of a mixed-in view, then the
bytes ``RustLiveView.serialize_msgpack()`` produces (what
``InMemoryStateBackend.set`` stores and measures) and what the session holds.
Sizes are DERIVED from the rendered chunks, never typed in.
"""

from __future__ import annotations

import msgpack
import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory

from djust import LiveView
from djust.theming import ThemeMixin

pytestmark = [pytest.mark.theming, pytest.mark.django_db]

# The four names the mixin used to assign. ``theme_preset`` / ``theme_mode`` /
# ``theme_resolved_mode`` are a few bytes each and stay ordinary attributes.
HEAVY_NAMES = ("theme_head", "theme_css", "theme_switcher", "theme_presets")


class TagOnlyView(ThemeMixin, LiveView):
    """The page from the issue: it prints the tag and reads none of the four."""

    template = (
        "{% load theme_tags %}<html><head>{% theme_head %}</head>"
        "<body><div dj-root>C{{ count }}</div></body></html>"
    )

    def mount(self, request, **kwargs):
        super().mount(request, **kwargs)
        self.count = 0


class VariableView(ThemeMixin, LiveView):
    """A page that prints the variable forms the mixin documents."""

    template = (
        "<html><head>[{{ theme_head }}]</head><body><div dj-root>"
        "C{{ count }}|{{ theme_switcher }}|{{ theme_mode }}"
        "|{{ theme_presets|length }}|{{ theme_css|length }}</div></body></html>"
    )

    def mount(self, request, **kwargs):
        super().mount(request, **kwargs)
        self.count = 0


def _request():
    request = RequestFactory().get("/")
    request.user = AnonymousUser()
    request.tenant = None
    request.session = SessionStore()
    request.session.create()
    return request


def _get(view_cls):
    request = _request()
    view = view_cls()
    view.setup(request)  # Django's View.setup: sets self.request, as dispatch() does
    response = view.get(request)
    return view, request, response


def _snapshot(view):
    """The stored snapshot, split the way ``serialize_msgpack`` lays it out."""
    blob = view._rust_view.serialize_msgpack()
    _template, state, _vdom, _version, _ts = msgpack.unpackb(blob, raw=False, strict_map_key=False)
    return blob, state


def _chunk_sizes(view):
    """Bytes of the four values, as the mixin renders them on request."""
    return {name: len(str(getattr(view, name))) for name in HEAVY_NAMES}


class TestTheViewStateOmitsTheHeavyChunks:
    def test_rust_snapshot_state_has_none_of_the_four(self):
        view, _request_, _response = _get(TagOnlyView)
        _blob, state = _snapshot(view)
        present = [name for name in HEAVY_NAMES if name in state]
        assert present == [], f"ThemeMixin leaked {present} into the stored Rust state"

    def test_the_stored_snapshot_is_smaller_than_one_rendered_chunk(self):
        """A trivial ``count`` view stores a few hundred bytes, not ~80 KB."""
        view, _request_, _response = _get(TagOnlyView)
        blob, _state = _snapshot(view)
        smallest_chunk = min(_chunk_sizes(view).values())
        assert len(blob) < smallest_chunk, (
            f"stored snapshot is {len(blob)} bytes, larger than the smallest of "
            f"the four chunks ({smallest_chunk}): they are in the state again"
        )

    def test_the_session_save_has_none_of_the_four(self):
        from djust._tenant_state import session_view_key

        view, request, _response = _get(TagOnlyView)
        saved = request.session[session_view_key(view, request.path)]
        present = [name for name in HEAVY_NAMES if name in saved]
        assert present == [], f"ThemeMixin leaked {present} into the session save"

    def test_the_private_state_save_does_not_carry_the_raw_css(self):
        """``_theme_css_raw`` (the push_event CSS) was a stored private attr."""
        from djust._tenant_state import session_view_key

        view, request, _response = _get(TagOnlyView)
        private = request.session.get(f"{session_view_key(view, request.path)}__private", {})
        assert "_theme_css_raw" not in private
        assert "_theme_css_raw" not in view.__dict__

    def test_the_instance_dict_does_not_hold_them(self):
        view, _request_, _response = _get(TagOnlyView)
        assert [n for n in HEAVY_NAMES if n in view.__dict__] == []


class TestTheNamesStillWork:
    """Dropping the attributes must not drop the feature."""

    def test_python_reads_still_return_the_rendered_chunks(self):
        view, _request_, _response = _get(TagOnlyView)
        assert "<style" in str(view.theme_head)
        assert "<style" in str(view.theme_css)
        assert "data-theme-switcher" in str(view.theme_switcher)
        assert len(view.theme_presets) > 20

    def test_a_template_reading_the_variables_renders_them(self):
        _view, _request_, response = _get(VariableView)
        html = response.content.decode()
        assert "<style" in html and "&lt;script" not in html
        assert "data-theme-switcher" in html

    def test_the_variable_forms_equal_the_python_reads(self):
        view, _request_, response = _get(VariableView)
        html = response.content.decode()
        assert str(view.theme_head) in html
        assert str(view.theme_switcher) in html

    def test_the_variables_work_without_the_context_processor(self, settings):
        """The mixin never needed the processor; it must not start to."""
        import copy

        templates = copy.deepcopy(settings.TEMPLATES)
        for config in templates:
            options = config.get("OPTIONS", {})
            options["context_processors"] = [
                p for p in options.get("context_processors", []) if "theme_context" not in p
            ]
        settings.TEMPLATES = templates
        from djust.mixins.context import _context_processors_cache

        _context_processors_cache.clear()
        _view, _request_, response = _get(VariableView)
        html = response.content.decode()
        assert "<style" in html and "data-theme-switcher" in html

    def test_with_the_processor_installed_the_mixins_values_win(self):
        """Documented: when both are present the mixin's values win, as the old
        attributes did. The two differ, so the page shows which one it got."""
        from djust.theming.context_processors import theme_context

        view, request, response = _get(VariableView)
        html = response.content.decode()
        processor_switcher = str(theme_context(request)["theme_switcher"])
        assert processor_switcher != str(view.theme_switcher), "the test cannot tell them apart"
        assert str(view.theme_switcher) in html
        assert processor_switcher not in html

    def test_an_assignment_in_a_subclass_still_wins(self):
        class Custom(VariableView):
            def mount(self, request, **kwargs):
                super().mount(request, **kwargs)
                self.theme_head = "CUSTOM-HEAD"

        _view, _request_, response = _get(Custom)
        assert "[CUSTOM-HEAD]" in response.content.decode()


# --------------------------------------------------------------------------
# Every other path that renders or saves the view
# --------------------------------------------------------------------------


class BumpView(VariableView):
    """``VariableView`` with an event, so a POST/WS turn re-renders it."""

    from djust.decorators import event_handler as _event_handler

    @_event_handler()
    def bump(self, **kwargs):
        self.count += 1


class TestTheHttpPostFallback:
    def test_a_post_turn_patches_only_the_counter(self):
        """End to end: the fallback POST of a mixed-in view re-renders and patches
        only what moved. (The injection mechanism itself is pinned by
        ``test_processor_context_injects_a_derived_name_and_removes_it_again``:
        the Rust state the POST restores keeps the switcher, so this turn alone
        cannot tell an injected value from a retained one.)"""
        import json

        request = _request()
        view = BumpView()
        view.setup(request)
        view.get(request)
        post = RequestFactory().post(
            request.path,
            data=json.dumps({}),
            content_type="application/json",
            HTTP_X_DJUST_EVENT="bump",
        )
        post.user, post.session, post.tenant = request.user, request.session, None
        view = BumpView()
        view.setup(post)
        response = view.post(post)
        assert response.status_code == 200
        body = json.loads(response.content)
        patches = json.dumps(body.get("patches") or [])
        assert "C1" in patches
        assert "data-theme-switcher" not in patches
        assert len(body["patches"]) == 1, patches

    def test_processor_context_injects_a_derived_name_and_removes_it_again(self):
        request = _request()
        view = BumpView()
        view.setup(request)
        view.get(request)
        with view._processor_context(request) as out:
            assert "theme_head" in out
            assert "theme_head" in view.__dict__, "the derived name was not injected"
        assert "theme_head" not in view.__dict__
        assert "<style" in str(view.theme_head)  # the computed read is back


class TestExplicitExposure:
    def test_an_explicit_view_still_gets_the_variables(self):
        class Explicit(ThemeMixin, LiveView):
            exposure_policy = "explicit"
            template = (
                "<html><head>[{{ theme_head }}]</head><body><div dj-root>"
                "{{ theme_switcher }}</div></body></html>"
            )

            def mount(self, request, **kwargs):
                super().mount(request, **kwargs)

        _view, _request_, response = _get(Explicit)
        html = response.content.decode()
        assert "<style" in html and "data-theme-switcher" in html


class TestTheHandlers:
    def test_a_preset_change_pushes_the_css_for_the_new_preset(self):
        from djust.theming.manager import generate_css_for_state, get_css_prefix

        view, _request_, _response = _get(TagOnlyView)
        pushed = []
        view.push_event = lambda name, payload: pushed.append((name, payload))
        view.set_theme_preset(preset="blue")
        assert pushed and pushed[0][0] == "theme_update"
        payload = pushed[0][1]
        assert payload["preset"] == "blue"
        assert payload["css"] == generate_css_for_state(
            view._theme_state, css_prefix=get_css_prefix()
        )
        assert view.theme_preset == "blue"
        assert [n for n in HEAVY_NAMES if n in view.__dict__] == []
        assert "_theme_css_raw" not in view.__dict__

    def test_a_mode_change_updates_the_small_state_values(self):
        view, _request_, _response = _get(TagOnlyView)
        view.push_event = lambda name, payload: None
        view.set_theme_mode(mode="dark")
        assert view.theme_mode == "dark"
        assert [n for n in HEAVY_NAMES if n in view.__dict__] == []
        # the computed switcher follows the new mode, with nothing stored
        assert 'data-theme-mode="dark"' in str(view.theme_switcher) or "dark" in str(
            view.theme_switcher
        )


class TestAViewRestoredFromAnOlderSession:
    def test_a_mount_sheds_the_stale_strings_an_old_session_restored(self):
        view = TagOnlyView()
        view.setup(_request())
        # What a pre-#3626 session restore sets back on the view.
        for name in ("theme_head", "theme_css", "theme_switcher", "theme_presets"):
            view.__dict__[name] = "STALE"
        view.mount(_request())
        assert [n for n in HEAVY_NAMES if n in view.__dict__] == []
        assert "STALE" not in str(view.theme_head)

    def test_a_view_that_never_ran_mount_still_reads_the_values(self):
        view = TagOnlyView()
        view.setup(_request())
        assert "<style" in str(view.theme_head)
        assert len(view.theme_presets) > 20


class TestLazyThemeList:
    def _list(self):
        from djust.theming._lazy_html import LazyThemeList

        calls = []

        def factory():
            calls.append(1)
            return [{"name": "a"}, {"name": "b"}]

        return LazyThemeList(factory), calls

    def test_building_waits_for_the_first_read(self):
        lazy, calls = self._list()
        repr(lazy)
        assert calls == [] and not lazy.evaluated
        assert len(lazy) == 2 and calls == [1] and lazy.evaluated
        assert [p["name"] for p in lazy] == ["a", "b"]
        assert calls == [1]

    def test_it_behaves_as_a_list_to_a_template_and_the_renderer(self):
        lazy, _calls = self._list()
        assert bool(lazy) and lazy[1]["name"] == "b" and {"name": "a"} in lazy
        assert lazy == [{"name": "a"}, {"name": "b"}]
        assert lazy.__djust_serialize__() == [{"name": "a"}, {"name": "b"}]

    def test_the_renderer_copy_cannot_mutate_the_memo(self):
        lazy, _calls = self._list()
        lazy.__djust_serialize__().append("x")
        assert len(lazy) == 2

    def test_it_is_not_json_data_so_it_stays_out_of_state(self):
        import json

        lazy, _calls = self._list()
        with pytest.raises(TypeError):
            json.dumps(lazy)

    def test_copies_and_pickles_do_not_carry_the_factory(self):
        import copy
        import pickle

        lazy, _calls = self._list()
        assert copy.deepcopy(lazy) is lazy
        assert pickle.loads(pickle.dumps(lazy)) == [{"name": "a"}, {"name": "b"}]


# --------------------------------------------------------------------------
# The WebSocket path: mount, then events. The processors run on every sync
# here (``self.request`` is long-lived), which is where the processor's own
# values reach the stored state, so this is the path the issue measured.
# --------------------------------------------------------------------------


class WsThemed(ThemeMixin, LiveView):
    template = (
        '<div dj-view="djust.tests.test_theme_mixin_state_weight_3626.WsThemed" dj-id="0">'
        "[{{ theme_head }}]|[{{ theme_switcher }}]|{{ theme_presets|length }}"
        "|{{ theme_css|length }}|{{ count }}</div>"
    )

    def mount(self, request, **kwargs):
        super().mount(request, **kwargs)
        self.count = 0

    from djust.decorators import event_handler as _event_handler

    @_event_handler()
    def bump(self, **kwargs):
        self.count += 1


class WsTagOnly(WsThemed):
    template = (
        '<div dj-view="djust.tests.test_theme_mixin_state_weight_3626.WsTagOnly" dj-id="0">'
        "{{ count }}</div>"
    )


async def _drive_ws(view_name):
    from asgiref.sync import sync_to_async
    from channels.testing import WebsocketCommunicator
    from unittest.mock import patch

    from djust.state_backends.memory import InMemoryStateBackend
    from djust.websocket import LiveViewConsumer

    from .test_ws_send_version_1788 import _receive_until

    def make_key():
        session = SessionStore()
        session.create()
        return session.session_key

    key = await sync_to_async(make_key)()

    class Session:
        session_key = key

    stored = []
    real_set = InMemoryStateBackend.set

    def recording_set(self, cache_key, view, *args, **kwargs):
        blob = view.serialize_msgpack()
        _t, state, _v, _ver, _ts = msgpack.unpackb(blob, raw=False, strict_map_key=False)
        stored.append((len(blob), sorted(state)))
        return real_set(self, cache_key, view, *args, **kwargs)

    communicator = WebsocketCommunicator(
        LiveViewConsumer.as_asgi(),
        "/ws/",
        headers=[(b"host", b"testserver"), (b"origin", b"http://testserver")],
    )
    communicator.scope["session"] = Session()
    with patch.object(InMemoryStateBackend, "set", recording_set):
        connected, _ = await communicator.connect()
        assert connected
        await communicator.receive_json_from(timeout=2)
        await communicator.send_json_to(
            {"type": "mount", "view": f"{__name__}.{view_name}", "url": "/"}
        )
        frames = [await _receive_until(communicator, "mount")]
        for ref in (1, 2):
            await communicator.send_json_to(
                {"type": "event", "event": "bump", "params": {}, "ref": ref}
            )
            frames.append(await _receive_until(communicator, "patch"))
        await communicator.disconnect()
    return frames, stored


@pytest.mark.asyncio
class TestTheWebSocketPath:
    async def test_mount_renders_the_variables_and_events_patch_only_the_count(self):
        from django.test import override_settings

        with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
            frames, _stored = await _drive_ws("WsThemed")
        html = frames[0].get("html") or ""
        assert "<style" in html and "&lt;script" not in html
        assert "data-theme-switcher" in html
        # presets and css lengths are non-zero: the lazy list and chunk reached the renderer
        assert "|0|0|" not in html
        import json

        for frame in frames[1:]:
            patches = json.dumps(frame.get("patches"))
            assert "data-theme-switcher" not in patches and "<style" not in patches

    async def test_no_stored_snapshot_holds_the_heavy_values(self):
        from django.test import override_settings

        from asgiref.sync import sync_to_async

        def smallest_chunk():
            view, _request_, _response = _get(TagOnlyView)
            return min(_chunk_sizes(view).values())

        floor = await sync_to_async(smallest_chunk)()
        with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
            _frames, stored = await _drive_ws("WsTagOnly")
        assert stored, "the state backend was never written"
        for size, keys in stored:
            assert [n for n in HEAVY_NAMES if n in keys] == [], keys
            assert size < floor, f"a one-counter view stores {size} bytes (a chunk is {floor})"
