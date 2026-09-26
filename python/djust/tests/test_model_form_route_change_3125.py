"""#3125: a URL change to another record remounts; it never keeps editing the old one.

``url_change`` (a ``dj-patch`` link or back/forward within the same path)
used to run ``handle_params`` on the mounted view without rebinding
``self.kwargs``, so ``/edit/<A>/ -> /edit/<B>/`` showed B's URL over A's
form. A ``ModelFormMixin`` view now answers a change of its route kwargs with
a ``live_redirect`` to the new URL, so the object is resolved and authorized
for the URL the user sees, and a server-side ``live_patch`` to another record
becomes the same redirect.
"""

import json

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth.models import Group

from djust.tests import test_model_form_acceptance_adr035 as acc


async def _url_change(socket, uri):
    await socket.send_json_to({"type": "url_change", "params": {}, "uri": uri})
    frames = []
    for _ in range(6):
        try:
            frame = await socket.receive_json_from(timeout=3)
        except Exception:  # noqa: BLE001 -- no further frame
            break
        frames.append(frame)
        if frame.get("type") in {"patch", "html_update", "error", "navigation"}:
            break
    return frames


def _two_of_mine():
    user, mine, theirs = acc._world()
    other = Group.objects.create(name="Other " + mine.name)
    user.groups.add(other)
    return user, mine, other, theirs


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_url_change_to_another_record_redirects_instead_of_rendering_the_old_one():
    user, mine, other, _ = await sync_to_async(_two_of_mine)()
    session = await sync_to_async(acc._session)()
    with acc._allow():
        socket = await acc._connect(user, session)
        try:
            frame = await acc._mount(socket, "ScopedEdit", "/edit/%s/" % mine.pk)
            assert frame["type"] == "mount", frame
            frames = await _url_change(socket, "/edit/%s/" % other.pk)
            assert [f["type"] for f in frames] == ["navigation"], frames
            assert frames[0]["action"] == "live_redirect"
            assert frames[0]["path"] == "/edit/%s/" % other.pk
            assert frames[0]["replace"] is True
            # The old record is not re-rendered under the new URL.
            assert mine.name not in json.dumps(frames)
            # The redirect's mount edits the record the URL names.
            frame = await acc._mount(socket, "ScopedEdit", "/edit/%s/" % other.pk)
            assert frame["type"] == "mount", frame
            assert other.name in json.dumps(frame)
            frames = await acc._event(socket, 1, "submit_form", {"name": "Renamed"})
            assert frames[-1]["type"] != "error", frames
        finally:
            await socket.disconnect()
    names = await sync_to_async(acc._names)(mine, other)
    assert names == {mine.pk: mine.name, other.pk: "Renamed"}


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_url_change_to_a_forbidden_record_is_denied_by_the_remount():
    """Authorization follows the URL: the redirect's mount checks the new record."""
    user, mine, _, theirs = await sync_to_async(_two_of_mine)()
    session = await sync_to_async(acc._session)()
    with acc._allow():
        socket = await acc._connect(user, session)
        try:
            assert (await acc._mount(socket, "ScopedEdit", "/edit/%s/" % mine.pk))[
                "type"
            ] == "mount"
            frames = await _url_change(socket, "/edit/%s/" % theirs.pk)
            assert [(f["type"], f.get("action")) for f in frames] == [
                ("navigation", "live_redirect")
            ], frames
            frame = await acc._mount(socket, "ScopedEdit", "/edit/%s/" % theirs.pk)
            assert (frame["type"], frame.get("error")) == ("error", acc.DENIED), frame
        finally:
            await socket.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_url_change_on_the_same_record_still_patches():
    user, mine, _, _ = await sync_to_async(_two_of_mine)()
    session = await sync_to_async(acc._session)()
    with acc._allow():
        socket = await acc._connect(user, session)
        try:
            assert (await acc._mount(socket, "ScopedEdit", "/edit/%s/" % mine.pk))[
                "type"
            ] == "mount"
            frames = await _url_change(socket, "/edit/%s/?tab=2" % mine.pk)
            assert frames and frames[-1]["type"] in {"patch", "html_update"}, frames
            assert not any(f["type"] == "navigation" for f in frames)
        finally:
            await socket.disconnect()


@pytest.mark.django_db
def test_server_live_patch_to_another_record_becomes_a_redirect():
    user, mine, other, _ = _two_of_mine()
    with acc._allow():
        view = acc.ScopedEdit()
        view._djust_bind_route_kwargs({"pk": mine.pk})
        view.live_patch(path="/edit/%s/" % other.pk)
        view.live_patch(path="/edit/%s/" % mine.pk, params={"tab": "2"})
        view.live_patch(params={"tab": "3"})
    assert view._drain_navigation() == [
        {"type": "live_redirect", "path": "/edit/%s/" % other.pk, "replace": False},
        {
            "type": "live_patch",
            "replace": False,
            "params": {"tab": "2"},
            "path": "/edit/%s/" % mine.pk,
        },
        {"type": "live_patch", "replace": False, "params": {"tab": "3"}},
    ]


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "uri",
    [
        "javascript:alert(1)",
        "//evil.example/edit/{t}/",
        "http://evil.example/edit/{t}/?x=1",
        "/\\evil.example/edit/{t}/",
        "\\\\evil.example/x",
    ],
)
async def test_the_redirect_never_echoes_a_scheme_or_host(uri):
    """PR #3159 review: the redirect target is the URI's path and query only,
    with no leading ``//`` or ``/\\`` a browser would read as another host."""
    from urllib.parse import urlsplit

    user, mine, other, _ = await sync_to_async(_two_of_mine)()
    session = await sync_to_async(acc._session)()
    with acc._allow():
        socket = await acc._connect(user, session)
        try:
            assert (await acc._mount(socket, "ScopedEdit", "/edit/%s/" % mine.pk))[
                "type"
            ] == "mount"
            frames = await _url_change(socket, uri.replace("{t}", str(other.pk)))
        finally:
            await socket.disconnect()
    navigations = [f for f in frames if f.get("type") == "navigation"]
    assert navigations, frames
    path = navigations[0]["path"]
    parts = urlsplit(path)
    assert (parts.scheme, parts.netloc) == ("", ""), path
    assert path.startswith("/") and path[1:2] not in ("/", "\\"), path
    assert "javascript" not in path.lower()
