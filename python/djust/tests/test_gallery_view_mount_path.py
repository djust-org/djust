"""Every gallery page must name a view that can actually mount.

`_base.html` declared its LiveView as

    <div dj-view="djust_components.gallery.live_views.{{ view_class }}">

`djust_components` is the **static** namespace — the directory
`static/djust_components/` — and has never been a Python package. The real
module is `djust.components.gallery.live_views`. `security/mount.py:276`
resolves that string with `importlib.import_module(module_path)`, so every
components-gallery page failed to mount:

    View djust_components.gallery.live_views.GalleryIndexView is not allowed

The failure is close to invisible from the outside. The HTTP GET renders the
whole page server-side, so the gallery returned 200, showed its components and
styled them correctly — and no control responded, because nothing was ever
listening. A `<div>` naming a module that does not exist looks exactly like a
`<div>` naming one that does.

These tests read the `dj-view` attribute **out of the rendered page** and mount
that exact string, so they pin what the browser is actually told rather than
what the template source appears to say.
"""

import importlib
import re

import pytest
from django.test import Client, override_settings

# LIVEVIEW_ALLOWED_MODULES — #2889: setting it REPLACES the framework's
#   `{"djust"}` fallback, so djust's own views cannot mount until the project
#   allowlists them. Unrelated to this suite; overridden to keep them separate.
# ROOT_URLCONF — the gallery templates reverse `djust_theming:*`.
_BASE = override_settings(LIVEVIEW_ALLOWED_MODULES=None, ROOT_URLCONF="djust.tests.urls_theming")

_CATEGORY_SLUGS = [
    "layout",
    "form",
    "data",
    "overlay",
    "feedback",
    "navigation",
    "indicator",
    "typography",
    "misc",
]

#: Pages that declare a `dj-view`, paired with the page URL. The `/lv/` routes
#: are the LiveViews; `<slug>/` is still the static shim (see the module
#: docstring of `urls.py`).
_LIVE_PAGES = [("/theme/components/", "GalleryIndexView")] + [
    (f"/theme/components/lv/{slug}/", None) for slug in _CATEGORY_SLUGS
]

_DJ_VIEW_RE = re.compile(r'dj-view="([^"]+)"')


def _rendered_view_path(page_url: str) -> str:
    """The `dj-view` value the browser is handed for `page_url`."""
    response = Client().get(page_url)

    assert response.status_code == 200, f"{page_url} returned {response.status_code}"
    match = _DJ_VIEW_RE.search(response.content.decode())
    assert match, f"{page_url} declares no dj-view at all — it cannot be interactive"

    return match.group(1)


def _split(view_path: str) -> tuple[str, str]:
    module_path, _, class_name = view_path.rpartition(".")
    return module_path, class_name


# ---------------------------------------------------------------------------
# The rendered path must resolve
# ---------------------------------------------------------------------------


# `django_db` is required, not incidental: the gallery's context processor
# reads the project's theme from the database, so an unmarked test gets a
# RuntimeError that the test client renders as a 500 — which reads as "the
# page is broken" rather than "the test needs the database".
# `@_BASE` carries ROOT_URLCONF: without it these hit the demo project's
# urlconf, which does not route the gallery, and every page reads as a 404 —
# which looks like a broken gallery rather than a mis-decorated test.
@_BASE
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("page_url,_expected", _LIVE_PAGES)
def test_every_gallery_page_names_a_module_that_exists(page_url, _expected):
    """The regression. This fails on the pre-fix `djust_components.…` string."""
    module_path, class_name = _split(_rendered_view_path(page_url))

    try:
        module = importlib.import_module(module_path)
    except ModuleNotFoundError as exc:
        raise AssertionError(
            f"{page_url} mounts {module_path!r}, which is not importable ({exc}). "
            f"`djust_components` is the static namespace, not a Python package — "
            f"the module is `djust.components.gallery.live_views`."
        ) from exc

    assert hasattr(module, class_name), (
        f"{page_url} mounts {class_name!r}, which {module_path} does not define"
    )


@_BASE
@pytest.mark.django_db(transaction=True)
def test_the_index_names_the_index_view_and_a_category_names_its_own():
    """Each page must name its OWN view, not a shared default.

    The template no longer names a view at all — it emits `<div dj-root>` and
    `request.py:349-350` stamps `dj-view` from the class doing the render. That
    is exactly what this pins: the stamp is per-page, so a category page must
    not come back carrying the index's class (resolvable, and wrong).
    """
    assert _rendered_view_path("/theme/components/").endswith(".GalleryIndexView")
    assert _rendered_view_path("/theme/components/lv/form/").endswith(".FormGalleryView")
    assert _rendered_view_path("/theme/components/lv/data/").endswith(".DataGalleryView")


# ---------------------------------------------------------------------------
# Gate-off: the pre-fix string must not resolve
# ---------------------------------------------------------------------------


def test_the_static_namespace_is_not_a_python_module():
    """Gate-off for the fix.

    If `djust_components` were importable the bug would have been harmless and
    these tests would prove nothing. `djust_components` is the template/static
    namespace only.
    """
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("djust_components")


def test_no_gallery_template_names_the_static_namespace_as_a_package():
    """The source pin — cheap, and catches a reintroduction at the template."""
    from pathlib import Path

    templates = Path(__file__).resolve().parent.parent / "components" / "templates"

    offenders = {}
    for path in templates.rglob("*.html"):
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if "djust_components.gallery" in line:
                offenders[f"{path.name}:{lineno}"] = line.strip()

    assert not offenders, (
        f"a template imports the static namespace as a Python package: {offenders}"
    )


# ---------------------------------------------------------------------------
# And the mount must actually succeed over the socket
# ---------------------------------------------------------------------------


async def _mount(view_path: str, page_url: str):
    pytest.importorskip("channels")
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    connected, _ = await communicator.connect()
    assert connected, "the WebSocket did not accept the connection"

    try:
        await communicator.receive_json_from(timeout=3)  # connect ack
    except Exception:  # noqa: BLE001 — absent on some transports
        pass

    await communicator.send_json_to(
        {"type": "mount", "view": view_path, "url": page_url, "params": {}}
    )
    return communicator, await communicator.receive_json_from(timeout=5)


@_BASE
@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("page_url,_expected", _LIVE_PAGES)
async def test_every_live_gallery_page_mounts(page_url, _expected):
    """A 200 proves nothing about the mount; this asserts the mount itself.

    This is the assertion that fails when `dj-view` names the wrong module —
    the page renders perfectly and the socket reports an error frame.
    """
    view_path = await _sync_to_async(_rendered_view_path, page_url)
    communicator, mounted = await _mount(view_path, page_url)
    try:
        assert mounted.get("type") != "error", (
            f"{page_url} mounted {view_path!r} and the server refused: {mounted!r}"
        )
        assert mounted.get("type") != "permission_denied", mounted
    finally:
        await communicator.disconnect()


async def _sync_to_async(fn, *args):
    """The test client is synchronous and these tests are not."""
    from asgiref.sync import sync_to_async

    return await sync_to_async(fn)(*args)
