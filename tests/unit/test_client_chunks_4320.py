"""Preload hints and precache for the client chunks (#4320, #4007 slice 3).

The LiveView's first render knows which chunks the page uses: the head carries
``<link rel="preload" as="script">`` for each, with the CSP nonce and the same
URL the core's loader requests (``data-djust-chunks``), so a page that uses a
chunk does not wait an extra round trip before it binds. ``generate_sw``
precaches the bundle the LiveView injects and every chunk, at those URLs.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any, Dict, List

import pytest
from django.test import RequestFactory, override_settings

import djust
from djust._client_chunks import CLIENT_CHUNKS, chunks_for_first_render, chunks_in_markup
from djust.config import config
from djust.mixins.post_processing import PostProcessingMixin, _mask_document_text

ROOT = Path(__file__).resolve().parents[2]
V = f"?v={djust.__version__}"


class _View(PostProcessingMixin):
    def get_debug_info(self) -> dict:
        return {}


class _Uploads:
    def get_upload_state(self) -> Dict[str, Any]:
        return {"avatar": {"config": {}}}


def _render(body: str, *, request: Any = None, view: Any = None) -> str:
    view = view or _View()
    if request is not None:
        view.request = request
    return view._inject_client_script(
        f"<!DOCTYPE html><html><head><title>t</title></head><body>{body}</body></html>"
    )


def _preloads(out: str) -> List[str]:
    return re.findall(r'<link rel="preload" as="script" href="([^"]*)"[^>]*>', out)


def _chunk_map(out: str) -> Dict[str, str]:
    m = re.search(r'data-djust-chunks="([^"]*)"', out)
    assert m, out
    return json.loads(html.unescape(m.group(1)))


@pytest.fixture
def full_bundle():
    previous = config.get("client_bundle")
    config.set("client_bundle", "full")
    try:
        yield
    finally:
        config.set("client_bundle", previous)


@override_settings(DEBUG=False)
def test_first_render_preloads_its_chunks_in_the_head_with_the_nonce():
    request = RequestFactory().get("/")
    request.csp_nonce = "n0nce"  # type: ignore[attr-defined]
    out = _render(
        '<div dj-view="a.V"><dialog dj-dialog="open"></dialog><ul dj-flip></ul></div>',
        request=request,
    )
    head = out[: out.index("</head>")]
    links = re.findall(r'<link rel="preload" as="script"[^>]*>', head)
    assert links == [
        f'<link rel="preload" as="script" href="/static/djust/chunks/flip.min.js{V}" nonce="n0nce">',
        f'<link rel="preload" as="script" href="/static/djust/chunks/dialog.min.js{V}" nonce="n0nce">',
    ]
    # The loader requests exactly these URLs, so it reuses the preloaded responses.
    chunks = _chunk_map(out)
    assert _preloads(out) == [chunks["flip"], chunks["dialog"]]


@override_settings(DEBUG=False)
def test_a_page_using_no_chunk_preloads_nothing():
    out = _render('<div dj-view="a.V"><button dj-click="go">x</button></div>')
    assert _preloads(out) == []


@override_settings(DEBUG=False)
def test_the_full_bundle_preloads_nothing(full_bundle):
    out = _render('<div dj-view="a.V"><ul dj-flip></ul></div>')
    assert _preloads(out) == []
    assert "data-djust-chunks" not in out


@override_settings(DEBUG=True)
def test_debug_preloads_the_error_overlay():
    out = _render('<div dj-view="a.V"></div>')
    assert _preloads(out) == [f"/static/djust/chunks/error-overlay.js{V}"]


@override_settings(DEBUG=False)
def test_a_view_declaring_uploads_preloads_the_uploads_chunk():
    view = _View()
    view._upload_manager = _Uploads()  # type: ignore[attr-defined]
    out = _render('<div dj-view="a.V"></div>', view=view)
    assert _preloads(out) == [f"/static/djust/chunks/uploads.min.js{V}"]


@override_settings(DEBUG=False)
def test_markers_in_scripts_comments_and_text_do_not_preload():
    out = _render(
        '<div dj-view="a.V"><script>const s = "<ul dj-flip>";</script>'
        "<!-- <dialog dj-dialog> --><pre>&lt;div dj-virtual&gt;</pre>"
        "<textarea><a dj-prefetch href=/x>x</a></textarea></div>"
    )
    assert _preloads(out) == []


def test_no_head_no_preload():
    out = _View()._inject_client_script('<div dj-view="a.V"><ul dj-flip></ul></div>')
    assert _preloads(out) == []


@pytest.mark.parametrize(
    ("markup", "chunk"),
    [
        ('<input type="file" dj-upload="avatar">', "uploads"),
        ("<div dj-upload-drop='avatar'></div>", "uploads"),
        ('<div dj-virtual="items"></div>', "virtual-list"),
        ('<div dj-hook="CursorOverlay"></div>', "cursor-overlay"),
        ('<div id="dj-tutorial-bubble"></div>', "tutorial"),
        ('<div class="x combobox"></div>', "component-behaviors"),
        ("<div class=kanban></div>", "component-behaviors"),
        ('<div role="tablist"></div>', "keyboard-nav"),
        ('<div class="dj-modal"></div>', "keyboard-nav"),
        ("<ul dj-flip></ul>", "flip"),
        ('<div dj-transition="a b c"></div>', "transitions"),
        ('<li dj-remove="fade"></li>', "transitions"),
        ('<ul dj-transition-group="a | b"></ul>', "transitions"),
        ('<ul dj-viewport-bottom="more"></ul>', "infinite-scroll"),
        ('<a href="/x" dj-prefetch>x</a>', "prefetch"),
        ('<link dj-track-static rel="stylesheet" href="/a.css">', "track-static"),
        ('<dialog dj-dialog="open"></dialog>', "dialog"),
    ],
)
def test_each_element_marker_names_its_chunk(markup, chunk):
    assert chunks_in_markup(_mask_document_text(markup)) == {chunk}


@pytest.mark.parametrize(
    "markup",
    [
        '<div class="combobox-option"></div>',  # a longer class name
        '<div dj-virtual-item-height="20"></div>',  # a longer attribute
        '<div dj-dialog="open"></div>',  # dj-dialog serves <dialog> only
        '<button dj-prefetch="x">x</button>',  # dj-prefetch serves <a> only
        '<div title="dj-flip"></div>',  # an attribute value
        '<div role="button"></div>',
    ],
)
def test_near_misses_are_not_markers(markup):
    assert chunks_in_markup(_mask_document_text(markup)) == frozenset()


def test_page_triggers_and_order():
    assert chunks_for_first_render(
        "<dialog dj-dialog></dialog><ul dj-flip></ul>",
        declares_uploads=True,
        debug=True,
        strict_styles=True,
    ) == ["uploads", "error-overlay", "dj-style", "flip", "dialog"]


def test_python_markers_cover_every_loader_trigger():
    """Every chunk the loader can need from the page has a server-side rule, or is browser-only."""
    loader = (ROOT / "python/djust/static/djust/src/13c-chunk-loader.js").read_text()
    sel = loader[loader.index("const _DJUST_CHUNK_SELECTORS = {") :]
    sel = sel[: sel.index("\n};")]
    page = loader[loader.index("const _DJUST_CHUNK_PAGE_TRIGGERS = {") :]
    page = page[: page.index("\n};")]
    names = set(re.findall(r"^    '([\w-]+)':", sel + "\n" + page, re.MULTILINE))
    assert names == set(CLIENT_CHUNKS)
    from djust._client_chunks import _MARKERS

    browser_only = {"service-worker"}  # a worker's control of the page
    page_level = {"error-overlay", "dj-style"}  # settings, not markup
    assert set(_MARKERS) | browser_only | page_level == set(CLIENT_CHUNKS)


# ---------------------------------------------------------------------------
# generate_sw precache
# ---------------------------------------------------------------------------


def _core_assets() -> List[str]:
    from djust.management.commands.generate_sw import Command

    return Command()._get_djust_core_assets()


@override_settings(DEBUG=False)
def test_generate_sw_precaches_the_injected_core_and_every_chunk():
    assets = _core_assets()
    assert assets[0] == f"/static/djust/client-core.min.js{V}"
    chunk_assets = [f"/static/djust/chunks/{n}.min.js{V}" for n in CLIENT_CHUNKS]
    assert assets[1 : 1 + len(CLIENT_CHUNKS)] == chunk_assets
    # The same URLs the page requests.
    out = _render('<div dj-view="a.V"></div>')
    assert f'src="{assets[0]}"' in out.replace("&amp;", "&")
    assert sorted(_chunk_map(out).values()) == sorted(chunk_assets)


@override_settings(DEBUG=False)
def test_generate_sw_precaches_the_full_bundle_when_injected(full_bundle):
    assets = _core_assets()
    assert assets[0] == f"/static/djust/client.min.js{V}"
    assert not any("/chunks/" in a for a in assets)
