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

    def test_not_a_list(self, tmp_path):
        with _manifest_settings(tmp_path, _PATHS):
            assert stale_static_urls(OLD) == []
            assert stale_static_urls({"u": OLD}) == []


class Page2966(LiveView):
    template = f'<div dj-root dj-view="{_MOD}.Page2966" dj-id="0">page</div>'

    def mount(self, request, **kwargs):
        pass

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
