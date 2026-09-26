"""``dj-track-static`` detects a deploy on reconnect (#2966).

The client recorded its tracked URLs once and compared them with themselves,
so a deploy that changed asset URLs was never noticed. A reconnecting client
now sends the URLs its page loaded (``track_static`` on the mount frame); the
server answers with ``stale_static`` — the ones whose asset the CURRENT static
manifest maps to a different hashed name.

The storage is a real ``ManifestStaticFilesStorage`` reading a real
``staticfiles.json``, and the end-to-end case goes over a real WebSocket mount.
"""

from __future__ import annotations

import json

import pytest
from django.test import override_settings

from djust import LiveView
from djust._track_static import MAX_TRACKED_URLS, stale_static_urls

_MOD = "djust.tests.test_track_static_stale_2966"
OLD = "/static/js/app.0123456789ab.js"
NEW = "/static/js/app.ba9876543210.js"
CSS_NOW = "/static/css/site.aaaaaaaaaaaa.css"


def _manifest_settings(tmp_path, paths):
    (tmp_path / "staticfiles.json").write_text(
        json.dumps({"paths": paths, "version": "1.1", "hash": "x"})
    )
    return override_settings(
        STATIC_URL="/static/",
        STATIC_ROOT=str(tmp_path),
        STORAGES={
            "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {
                "BACKEND": "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"
            },
        },
    )


_PATHS = {
    "js/app.js": "js/app.ba9876543210.js",
    "css/site.css": "css/site.aaaaaaaaaaaa.css",
}


class TestStaleStaticUrls:
    def test_an_older_hash_of_a_current_asset_is_stale(self, tmp_path):
        with _manifest_settings(tmp_path, _PATHS):
            assert stale_static_urls([OLD, CSS_NOW]) == [OLD]

    def test_current_urls_are_not_stale(self, tmp_path):
        with _manifest_settings(tmp_path, _PATHS):
            assert stale_static_urls([NEW, CSS_NOW]) == []

    @pytest.mark.parametrize(
        "url",
        [
            "/static/js/vendor.0123456789ab.js",  # asset not in the manifest
            "/static/js/app.js",  # unhashed name: cannot judge
            "/other/js/app.0123456789ab.js",  # outside STATIC_URL
            "https://cdn.example.com/static/js/app.0123456789ab.js",  # another origin
            "/static/js/app.0123.js",  # a different hash shape
            "x" * 3000,
            42,
            None,
        ],
    )
    def test_what_it_cannot_judge_is_never_reported(self, tmp_path, url):
        with _manifest_settings(tmp_path, _PATHS):
            assert stale_static_urls([url]) == []

    def test_without_a_manifest_nothing_is_reported(self):
        with override_settings(
            STORAGES={
                "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
                "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
            }
        ):
            assert stale_static_urls([OLD]) == []

    def test_the_list_is_bounded(self, tmp_path):
        with _manifest_settings(tmp_path, _PATHS):
            urls = [NEW] * MAX_TRACKED_URLS + [OLD]
            assert stale_static_urls(urls) == []

    def test_a_different_hash_length_is_not_judged_stale(self, tmp_path):
        # Review of #3163 (M1): a name whose "hash" has a different length from
        # the current build's is not an older build of that asset (it could be
        # a version number in a file name), so it is never reported.
        with _manifest_settings(tmp_path, {"js/app.js": "js/app.ba98765432.js"}):
            assert stale_static_urls([OLD]) == []
            assert stale_static_urls(["/static/js/app.0123456789.js"]) == [
                "/static/js/app.0123456789.js"
            ]

    def test_the_current_name_set_is_built_once_per_manifest(self, tmp_path):
        # Review of #3163 (M2): not rebuilt on every reconnecting mount.
        from djust import _track_static

        with _manifest_settings(tmp_path, _PATHS):
            manifest = _track_static._manifest()
            first = _track_static._current_names(manifest)
            assert _track_static._current_names(manifest) is first
            manifest["js/new.js"] = "js/new.cccccccccccc.js"
            try:
                rebuilt = _track_static._current_names(manifest)
                assert rebuilt is not first
                assert "js/new.cccccccccccc.js" in rebuilt
            finally:
                manifest.pop("js/new.js")

    def test_not_a_list(self, tmp_path):
        with _manifest_settings(tmp_path, _PATHS):
            assert stale_static_urls(OLD) == []
            assert stale_static_urls({"u": OLD}) == []


class Page2966(LiveView):
    template = f'<div dj-root dj-view="{_MOD}.Page2966" dj-id="0">page</div>'

    #: The mount kwargs of the last mount (the SSE case checks the tracking
    #: param never reaches ``mount()``).
    last_mount_kwargs: dict = {}

    def mount(self, request, **kwargs):
        type(self).last_mount_kwargs = dict(kwargs)

    def get_context_data(self, **kwargs):
        return {}


async def _mount_frame(extra):
    pytest.importorskip("channels")
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    connected, _ = await communicator.connect()
    assert connected
    try:
        await communicator.receive_json_from(timeout=2)
        with override_settings(LIVEVIEW_ALLOWED_MODULES=[_MOD]):
            await communicator.send_json_to(
                {"type": "mount", "view": f"{_MOD}.Page2966", "url": "/p2966/", **extra}
            )
            for _ in range(8):
                msg = await communicator.receive_json_from(timeout=3)
                if msg.get("type") == "mount":
                    return msg
        raise AssertionError("no mount frame")
    finally:
        await communicator.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
class TestMountReportsStaleStatic:
    async def test_reconnect_mount_reports_the_stale_url(self, tmp_path):
        with _manifest_settings(tmp_path, _PATHS):
            frame = await _mount_frame({"track_static": [OLD, CSS_NOW]})
        assert frame.get("stale_static") == [OLD]

    async def test_current_assets_add_nothing_to_the_frame(self, tmp_path):
        with _manifest_settings(tmp_path, _PATHS):
            frame = await _mount_frame({"track_static": [NEW]})
        assert "stale_static" not in frame

    async def test_a_page_that_tracks_nothing_is_unchanged(self, tmp_path):
        with _manifest_settings(tmp_path, _PATHS):
            frame = await _mount_frame({})
        assert "stale_static" not in frame

    async def test_a_malformed_list_never_breaks_the_mount(self, tmp_path):
        with _manifest_settings(tmp_path, _PATHS):
            frame = await _mount_frame({"track_static": [{"x": 1}, 7, OLD]})
        assert frame.get("stale_static") == [OLD]


def test_manifest_storage_is_reset_between_settings(tmp_path):
    # Guards the harness: the lazy storage picks up each override.
    with _manifest_settings(tmp_path, _PATHS):
        assert stale_static_urls([OLD]) == [OLD]
    other = tmp_path / "other"
    other.mkdir()
    with _manifest_settings(other, {"js/app.js": "js/app.0123456789ab.js"}):
        assert stale_static_urls([OLD]) == []


# --------------------------------------------------------------------------- #
# SSE (review of #3163, I1). The stream GET mounts the view, and a POSTed
# mount frame after that is a no-op (the runtime's already-mounted early
# return); an EventSource auto-reconnect re-requests the stream URL and posts
# nothing. So the tracked URLs ride the stream GET (``_djust_track_static``,
# repeated) and are evaluated at that mount.
# --------------------------------------------------------------------------- #


async def _sse_stream_mount(query):
    from urllib.parse import urlencode

    from django.contrib.auth.models import AnonymousUser
    from django.test import RequestFactory

    from djust.sse import DjustSSEStreamView, _sse_sessions

    sid = "22966000-0000-0000-0000-000000002966"
    _sse_sessions.pop(sid, None)
    params = [("view", f"{_MOD}.Page2966"), ("_djust_url", "/p2966/")] + query
    request = RequestFactory().get(
        f"/djust/sse/{sid}/?{urlencode(params)}", HTTP_ORIGIN="https://example.com"
    )
    request.user = AnonymousUser()
    from django.contrib.sessions.backends.db import SessionStore

    request.session = SessionStore()
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[_MOD], ALLOWED_HOSTS=["example.com"]):
        await DjustSSEStreamView().get(request, session_id=sid)
    session = _sse_sessions.pop(sid, None)
    assert session is not None, "the stream mount must succeed"
    frames = []
    while not session.queue.empty():
        msg = session.queue.get_nowait()
        if msg is not None:
            frames.append(msg)
    session.shutdown()
    return [m for m in frames if m.get("type") == "mount"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
class TestSSEStreamReportsStaleStatic:
    async def test_the_stream_mount_reports_a_stale_tracked_url(self, tmp_path):
        with _manifest_settings(tmp_path, _PATHS):
            (frame,) = await _sse_stream_mount(
                [("_djust_track_static", OLD), ("_djust_track_static", CSS_NOW)]
            )
        assert frame.get("stale_static") == [OLD]

    async def test_current_urls_report_nothing(self, tmp_path):
        with _manifest_settings(tmp_path, _PATHS):
            (frame,) = await _sse_stream_mount([("_djust_track_static", NEW)])
        assert "stale_static" not in frame

    async def test_the_tracking_param_is_not_a_mount_kwarg(self, tmp_path):
        with _manifest_settings(tmp_path, _PATHS):
            (frame,) = await _sse_stream_mount([("_djust_track_static", NEW), ("tab", "b")])
        assert frame.get("type") == "mount"
        assert Page2966.last_mount_kwargs.get("tab") == "b"
        assert "_djust_track_static" not in Page2966.last_mount_kwargs

    async def test_a_stream_without_tracked_urls_is_unchanged(self, tmp_path):
        with _manifest_settings(tmp_path, _PATHS):
            (frame,) = await _sse_stream_mount([])
        assert "stale_static" not in frame
