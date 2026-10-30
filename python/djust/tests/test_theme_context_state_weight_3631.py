"""``theme_context``'s ``theme_presets`` must not be copied into view state (#3631).

The processor returned ``theme_presets`` as a plain ``list`` of dicts (about
11.7 KB). ``_sync_state_to_rust`` copies every JSON-serialisable processor value
into the Rust state, so every view in an app that installs the processor stored
it, with no ``ThemeMixin`` and no template that reads it. A one-counter view
stored 12,182 bytes after a GET; 11,684 of them were this list.

The fix keeps the value a real ``list`` (``RequestScopedList``) and tells the
bridge not to store it: it rides the raw-value sidecar, as the lazy HTML chunks
do. So ``isinstance(..., list)``, ``json.dumps`` and Django's
``{{ theme_presets|json_script }}`` keep working, which a lazy non-list object
cannot offer (``DjangoJSONEncoder`` has no hook for one).

These tests drive the real paths: an HTTP GET of a one-counter view, the bytes
``RustLiveView.serialize_msgpack()`` produces (what ``InMemoryStateBackend``
stores and measures), the real consumer over a WebSocket, and both template
engines. Sizes are DERIVED from the processor's own value, never typed in.
"""

from __future__ import annotations

import json

import msgpack
import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.template import engines
from django.test import RequestFactory

from djust import LiveView
from djust.decorators import event_handler
from djust.theming import context_processors as cp
from djust.theming.manager import ThemeManager

pytestmark = [pytest.mark.theming, pytest.mark.django_db]

PROCESSOR = "djust.theming.context_processors.theme_context"


class Counter(LiveView):
    """The issue's reproduction: no theme mixin, one counter, no theme variable."""

    template = "<html><head></head><body><div dj-root>C{{ count }}</div></body></html>"

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler()
    def bump(self, **kwargs):
        self.count += 1


class ReadsPresets(LiveView):
    """A page that reads the list in the dj-root and in the page shell."""

    template = (
        "<html><head>[S{{ theme_presets|length }}]</head><body><div dj-root>"
        "C{{ count }}|n{{ theme_presets|length }}|first={{ theme_presets.0.name }}"
        "|{% for p in theme_presets %}{{ p.name }},{% endfor %}"
        "|{% if theme_presets %}yes{% endif %}"
        "</div></body></html>"
    )

    def mount(self, request, **kwargs):
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
    view.setup(request)
    response = view.get(request)
    return view, request, response


def _snapshot(view):
    blob = view._rust_view.serialize_msgpack()
    _template, state, _vdom, _version, _ts = msgpack.unpackb(blob, raw=False, strict_map_key=False)
    return blob, state


def _presets_bytes(request):
    """The processor's own value as the snapshot would carry it (msgpack): the
    least a copy into the stored state could cost."""
    return msgpack.packb(list(cp.theme_context(request)["theme_presets"]))


@pytest.fixture(autouse=True)
def _processor_is_installed(settings):
    assert PROCESSOR in settings.TEMPLATES[0]["OPTIONS"]["context_processors"], (
        "the test settings must install theme_context, or none of this measures anything"
    )
    cp.clear_theme_context_cache()
    yield
    cp.clear_theme_context_cache()


class TestTheStoredSnapshotOmitsThePresets:
    """The measurement in the issue: a one-counter view with the processor installed."""

    def test_the_rust_state_does_not_hold_theme_presets(self):
        view, _request_, _response = _get(Counter)
        _blob, state = _snapshot(view)
        assert "theme_presets" not in state, sorted(state)

    def test_the_stored_snapshot_is_smaller_than_the_list_it_used_to_hold(self):
        view, request, _response = _get(Counter)
        blob, _state = _snapshot(view)
        presets = _presets_bytes(request)
        assert len(presets) > 5000, "the list is the heavy value this test is about"
        assert len(blob) < len(presets), (
            f"a one-counter view stores {len(blob)} bytes, more than the presets list "
            f"alone ({len(presets)}): it is in the state again"
        )

    def test_the_presets_text_is_not_inside_the_stored_snapshot(self):
        view, request, _response = _get(Counter)
        blob, _state = _snapshot(view)
        presets = list(cp.theme_context(request)["theme_presets"])
        assert len(presets) > 20
        # A preset NAME can legitimately appear (the active preset is state);
        # the descriptions are long and unique to the list.
        for preset in presets[:8]:
            assert len(preset["description"]) > 20, preset
            assert preset["description"].encode() not in blob, (
                f"preset {preset['name']!r} is inside the stored snapshot"
            )

    def test_the_session_save_does_not_hold_it(self):
        from djust._tenant_state import session_view_key

        view, request, _response = _get(Counter)
        saved = request.session[session_view_key(view, request.path)]
        assert "theme_presets" not in saved, sorted(saved)

    def test_the_instance_dict_does_not_hold_it(self):
        view, _request_, _response = _get(Counter)
        assert "theme_presets" not in view.__dict__


_SHELL_BASE = (
    "<!DOCTYPE html><html><head><title>T</title></head><body>"
    "<p id='shell'>shell={{ theme_presets|length }}|{{ theme_presets.0.name }}</p>"
    '{{ theme_presets|json_script:"shell-presets" }}'
    "<div dj-root>{% block content %}{% endblock %}</div></body></html>"
)
_SHELL_CHILD = (
    '{% extends "base_3631.html" %}{% block content %}'
    "<span>root={{ theme_presets|length }}|n={{ count }}</span>{% endblock %}"
)


@pytest.fixture
def shell_templates(settings):
    """A page-shell view: ``template_name`` extends a base, so the GET renders the
    shell through ``render_full_template`` as well as the dj-root."""
    import shutil
    import tempfile
    from pathlib import Path

    from django.test import override_settings

    from djust.mixins.context import _context_processors_cache, _resolved_processors_cache
    from djust.utils import clear_template_dirs_cache

    tmp = Path(tempfile.mkdtemp())
    (tmp / "base_3631.html").write_text(_SHELL_BASE)
    (tmp / "child_3631.html").write_text(_SHELL_CHILD)
    templates = [
        {
            "BACKEND": "djust.template.backend.DjustTemplateBackend",
            "DIRS": [str(tmp)],
            "APP_DIRS": False,
            "OPTIONS": {
                "context_processors": [
                    "django.template.context_processors.request",
                    "django.contrib.auth.context_processors.auth",
                    PROCESSOR,
                ]
            },
        }
    ]
    with override_settings(TEMPLATES=templates):
        clear_template_dirs_cache()
        _resolved_processors_cache.clear()
        _context_processors_cache.clear()
        yield tmp
    clear_template_dirs_cache()
    _resolved_processors_cache.clear()
    _context_processors_cache.clear()
    shutil.rmtree(tmp, ignore_errors=True)


class ShellView(LiveView):
    template_name = "child_3631.html"

    def mount(self, request, **kwargs):
        self.count = 3


class TestThePageShell:
    """``render_full_template`` is a parallel path with its own split of the processor
    output: it must read the list (shell and dj-root) and must not normalize it."""

    def test_the_shell_and_the_root_both_read_the_list(self, shell_templates):
        view, request, response = _get(ShellView)
        html = response.content.decode()
        presets = ThemeManager(request=request).get_available_presets()
        assert f"shell={len(presets)}|{presets[0]['name']}" in html, html[:600]
        assert f"root={len(presets)}|n=3" in html
        body = html.split('id="shell-presets" type="application/json">', 1)[1].split("</script>")[0]
        assert json.loads(body) == presets

    def test_the_shells_view_stores_no_presets(self, shell_templates):
        view, _request_, _response = _get(ShellView)
        _blob, state = _snapshot(view)
        assert "theme_presets" not in state

    def test_no_normalizer_call_receives_the_list(self, shell_templates):
        from unittest.mock import patch

        import djust.serialization as serialization
        from djust.request_scoped import RequestScopedList

        seen = []
        real = serialization.normalize_django_value

        def spy(value, *args, **kwargs):
            seen.append(value)
            return real(value, *args, **kwargs)

        with patch.object(serialization, "normalize_django_value", spy):
            _get(ShellView)
        assert seen, "the spy never ran, so it measured nothing"
        assert not any(isinstance(v, RequestScopedList) for v in seen)
        assert not any(isinstance(v, dict) and "theme_presets" in v for v in seen)


class TestACustomProcessorsPlainListIsStillStored:
    """The exemption is opt-in (a ``RequestScopedList``), not a change to what every
    processor value means. A processor that returns a plain list keeps today's
    behaviour, so an app's own processor is not silently re-routed."""

    def test_a_plain_list_from_another_processor_is_stored(self, settings):
        import copy

        from djust.mixins.context import _context_processors_cache

        templates = copy.deepcopy(settings.TEMPLATES)
        templates[0]["OPTIONS"]["context_processors"].append(
            "djust.tests.test_theme_context_state_weight_3631.plain_list_processor"
        )
        settings.TEMPLATES = templates
        _context_processors_cache.clear()
        try:
            view, _request_, _response = _get(Counter)
            _blob, state = _snapshot(view)
        finally:
            _context_processors_cache.clear()
        assert state.get("plain_rows") == list(range(50)), sorted(state)


def plain_list_processor(request):
    return {"plain_rows": list(range(50))}


class TestNothingBreaksForReaders:
    """The compat decision: it stays a ``list``."""

    def test_it_is_a_list_and_json_dumps_gives_the_presets(self):
        request = _request()
        value = cp.theme_context(request)["theme_presets"]
        assert isinstance(value, list)
        eager = ThemeManager(request=request).get_available_presets()
        assert json.loads(json.dumps(value)) == eager
        assert value == eager

    def test_it_supports_the_list_protocol(self):
        value = cp.theme_context(_request())["theme_presets"]
        assert len(value) > 20 and bool(value)
        assert value[0]["name"] == next(iter(value))["name"]
        assert value[0] in value
        assert value[-1] == list(value)[-1]
        assert [p["name"] for p in value[:2]] == [value[0]["name"], value[1]["name"]]

    def test_a_copy_is_independent_of_the_processors_next_value(self):
        request = _request()
        first = cp.theme_context(request)["theme_presets"]
        first.append({"name": "mutated"})
        second = cp.theme_context(request)["theme_presets"]
        assert all(p["name"] != "mutated" for p in second)

    def test_django_json_script_in_the_django_engine(self):
        request = _request()
        html = (
            engines["django"]
            .from_string('{{ theme_presets|json_script:"presets" }}')
            .render({}, request)
        )
        body = html.split(">", 1)[1].rsplit("</script>", 1)[0]
        assert json.loads(body) == ThemeManager(request=request).get_available_presets()

    def test_django_for_length_index_and_in_in_the_django_engine(self):
        request = _request()
        out = (
            engines["django"]
            .from_string(
                "{{ theme_presets|length }}|{{ theme_presets.0.name }}"
                "|{% for p in theme_presets %}{{ p.name }},{% endfor %}"
                "|{% if theme_presets %}yes{% endif %}"
            )
            .render({}, request)
        )
        presets = ThemeManager(request=request).get_available_presets()
        names = ",".join(p["name"] for p in presets)
        assert out == f"{len(presets)}|{presets[0]['name']}|{names},|yes"

    def test_rust_engine_dj_root_and_page_shell_read_it(self):
        view, request, response = _get(ReadsPresets)
        html = response.content.decode()
        presets = ThemeManager(request=request).get_available_presets()
        assert f"[S{len(presets)}]" in html, "the page shell lost the list"
        names = ",".join(p["name"] for p in presets)
        assert f"n{len(presets)}|first={presets[0]['name']}|{names},|yes" in html

    def test_rust_engine_json_script(self):
        class JsonScript(ReadsPresets):
            template = (
                "<html><head></head><body><div dj-root>"
                '{{ theme_presets|json_script:"presets" }}</div></body></html>'
            )

        _view, request, response = _get(JsonScript)
        html = response.content.decode()
        body = html.split('id="presets" type="application/json">', 1)[1].split("</script>", 1)[0]
        assert json.loads(body) == ThemeManager(request=request).get_available_presets()


class Bumps(ReadsPresets):
    @event_handler()
    def bump(self, **kwargs):
        self.count += 1


class TestTheHttpPostFallback:
    def test_a_post_turn_renders_the_list_and_patches_only_the_counter(self):
        request = _request()
        view = Bumps()
        view.setup(request)
        view.get(request)
        post = RequestFactory().post(
            request.path,
            data=json.dumps({}),
            content_type="application/json",
            HTTP_X_DJUST_EVENT="bump",
        )
        post.user, post.session, post.tenant = request.user, request.session, None
        view = Bumps()
        view.setup(post)
        response = view.post(post)
        assert response.status_code == 200
        body = json.loads(response.content)
        presets = ThemeManager(request=post).get_available_presets()
        patches = json.dumps(body.get("patches") or [])
        # The fallback render read the list (the text node carries its length and
        # first name), and only that one text node moved.
        assert f"C1|n{len(presets)}|first={presets[0]['name']}" in patches, patches
        assert len(body["patches"]) == 1, patches

    def test_processor_context_injects_the_list_and_removes_it_again(self):
        request = _request()
        view = Counter()
        view.setup(request)
        view.get(request)
        with view._processor_context(request) as out:
            assert isinstance(out["theme_presets"], list)
            assert "theme_presets" in view.__dict__
        assert "theme_presets" not in view.__dict__


class TestAStaleStateEntryIsShed:
    """Rolling deploy: a snapshot stored by the previous build holds the list."""

    def test_a_stored_presets_entry_does_not_survive_the_next_sync(self):
        view, request, _response = _get(Counter)
        view._rust_view.update_state({"theme_presets": [{"name": "STALE"}]})
        assert "theme_presets" in _snapshot(view)[1], "the setup cannot reproduce the old state"
        view._context_processor_keys = set()
        view._sync_state_to_rust()
        _blob, state = _snapshot(view)
        assert "theme_presets" not in state, state.get("theme_presets")


class TestRequestScopedList:
    def test_it_is_a_list_that_compares_and_encodes_as_one(self):
        from djust.request_scoped import RequestScopedList

        value = RequestScopedList([{"a": 1}, {"a": 2}])
        assert isinstance(value, list) and value == [{"a": 1}, {"a": 2}]
        assert json.dumps(value) == json.dumps([{"a": 1}, {"a": 2}])
        assert type(value) is not list

    def test_only_the_marked_type_is_request_scoped_data(self):
        from djust.request_scoped import RequestScopedList, is_request_scoped_data

        assert is_request_scoped_data(RequestScopedList())
        assert not is_request_scoped_data([])
        assert not is_request_scoped_data({})
        assert not is_request_scoped_data(None)

    def test_copies_and_pickles_round_trip(self):
        import copy
        import pickle

        from djust.request_scoped import RequestScopedList

        value = RequestScopedList([{"a": 1}])
        assert copy.deepcopy(value) == value
        assert pickle.loads(pickle.dumps(value)) == value

    def test_a_view_that_assigns_one_itself_stores_it_like_any_list(self):
        """The marker only matters for a key the framework recorded as a processor's."""
        from djust.request_scoped import RequestScopedList

        class Assigns(Counter):
            def mount(self, request, **kwargs):
                self.count = 0
                self.mine = RequestScopedList([{"k": "v" * 40}])

        view, _request_, _response = _get(Assigns)
        _blob, state = _snapshot(view)
        assert state.get("mine") == [{"k": "v" * 40}]


class TestTheThemeMixinSuppliesTheSameType:
    def test_a_mixin_view_stores_no_presets_and_keeps_json_script(self):
        from djust.theming import ThemeMixin

        class Mixed(ThemeMixin, LiveView):
            template = (
                "<html><head></head><body><div dj-root>C{{ count }}"
                '{{ theme_presets|json_script:"p" }}</div></body></html>'
            )

            def mount(self, request, **kwargs):
                super().mount(request, **kwargs)
                self.count = 0

        view, request, response = _get(Mixed)
        _blob, state = _snapshot(view)
        assert "theme_presets" not in state
        html = response.content.decode()
        body = html.split('id="p" type="application/json">', 1)[1].split("</script>", 1)[0]
        assert json.loads(body) == view.theme_presets
        # Python readers of the context get a list too (#3626 documented this as
        # a break while the value was a lazy non-list).
        supplied = view._apply_context_processors({}, request)["theme_presets"]
        assert isinstance(supplied, list)
        assert json.loads(json.dumps(supplied)) == view.theme_presets


# --------------------------------------------------------------------------
# The real consumer: mount + two events
# --------------------------------------------------------------------------


class WsCounter(Counter):
    template = (
        '<div dj-view="djust.tests.test_theme_context_state_weight_3631.WsCounter" dj-id="0">'
        "C{{ count }}</div>"
    )


async def _drive_ws(view_name):
    from unittest.mock import patch

    from asgiref.sync import sync_to_async
    from channels.testing import WebsocketCommunicator

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
    real_refresh = InMemoryStateBackend.refresh

    def _record(view, kind):
        blob = view.serialize_msgpack()
        _t, state, _v, _ver, _ts = msgpack.unpackb(blob, raw=False, strict_map_key=False)
        stored.append((len(blob), sorted(state), kind, blob))

    def recording_set(self, cache_key, view, *args, **kwargs):
        _record(view, "set")
        return real_set(self, cache_key, view, *args, **kwargs)

    def recording_refresh(self, cache_key, view):
        _record(view, "refresh")
        return real_refresh(self, cache_key, view)

    communicator = WebsocketCommunicator(
        LiveViewConsumer.as_asgi(),
        "/ws/",
        headers=[(b"host", b"testserver"), (b"origin", b"http://testserver")],
    )
    communicator.scope["session"] = Session()
    with (
        patch.object(InMemoryStateBackend, "set", recording_set),
        patch.object(InMemoryStateBackend, "refresh", recording_refresh),
    ):
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
    async def test_every_store_after_the_mount_and_each_event_omits_the_presets(self):
        from asgiref.sync import sync_to_async
        from django.test import override_settings

        presets_bytes = await sync_to_async(lambda: len(_presets_bytes(_request())))()
        with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
            _frames, stored = await _drive_ws("WsCounter")
        refreshed = [entry for entry in stored if entry[2] == "refresh"]
        assert len(refreshed) >= 3, f"the post-render store never ran: {stored}"
        for size, keys, _kind, blob in refreshed:
            assert "theme_presets" not in keys, keys
            assert size < presets_bytes, f"{size} bytes stored; the list alone is {presets_bytes}"
            assert b"primary_hsl" not in blob

    async def test_the_events_still_patch_the_counter(self):
        from django.test import override_settings

        with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
            frames, _stored = await _drive_ws("WsCounter")
        assert "C0" in (frames[0].get("html") or "")
        for ref, frame in enumerate(frames[1:], start=1):
            assert f"C{ref}" in json.dumps(frame.get("patches")), frame
