"""A ``_restore_snapshot()`` override is refused: 2.0 never calls it.

The legacy signed snapshot called ``LiveView._restore_snapshot()`` on Back,
and a view could override it. 2.0 removed the snapshot and the base method,
so an override would silently never run. ``LiveView`` refuses it like a
removed attribute (``test_removed_view_attributes``): when the class is
created, and on the mount path for the shapes the class check cannot see.
"""

import pytest
from django.core.exceptions import ImproperlyConfigured

from djust import LiveView
from djust._removed_settings import MIGRATION_GUIDE_URL, REMOVED_VIEW_ATTRIBUTES


def _assert_pointed(message: str) -> None:
    assert "_restore_snapshot" in message
    assert "removed in djust 2.0" in message
    assert 'persist="client", client=True' in message
    assert "handle_restore()" in message
    assert MIGRATION_GUIDE_URL in message


def _override(self, state):
    self.restored = state


def test_an_override_is_refused_at_class_creation():
    with pytest.raises(ImproperlyConfigured) as refused:

        class OverridingView(LiveView):
            exposure_policy = "legacy"
            template = "<div dj-root></div>"

            def _restore_snapshot(self, state):
                pass

    assert "OverridingView" in str(refused.value)
    _assert_pointed(str(refused.value))


def test_a_mixin_override_is_refused_where_it_meets_liveview():
    class RestoreMixin:
        def _restore_snapshot(self, state):
            pass

    with pytest.raises(ImproperlyConfigured, match="_restore_snapshot"):

        class MixedView(RestoreMixin, LiveView):
            exposure_policy = "legacy"
            template = "<div dj-root></div>"


def test_a_subclass_of_a_plain_view_that_overrides_it_is_refused():
    class Base(LiveView):
        exposure_policy = "legacy"
        template = "<div dj-root></div>"

    with pytest.raises(ImproperlyConfigured, match="Child"):

        class Child(Base):
            _restore_snapshot = _override


def test_the_base_class_has_no_such_method_and_a_plain_view_is_accepted():
    class Plain(LiveView):
        exposure_policy = "legacy"
        template = "<div dj-root></div>"

    assert Plain() is not None
    assert not hasattr(LiveView, "_restore_snapshot")
    assert "_restore_snapshot" in REMOVED_VIEW_ATTRIBUTES


# --------------------------------------------------------------------------
# The mount path: the shapes the class-creation check cannot see.
# --------------------------------------------------------------------------


class InstanceOverrideView(LiveView):
    """Sets it on the instance, in ``mount()``."""

    exposure_policy = "legacy"
    template = "<div dj-root>instance</div>"

    def mount(self, request, **kwargs):
        self._restore_snapshot = lambda state: None


class LateOverrideView(LiveView):
    """Plain when created; a test assigns the method afterwards."""

    exposure_policy = "legacy"
    template = "<div dj-root>late</div>"


class _SkipsSuper:
    def __init_subclass__(cls, **kwargs):
        pass  # does not call super(): LiveView.__init_subclass__ never runs


class HiddenOverrideView(_SkipsSuper, LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root>hidden</div>"

    def _restore_snapshot(self, state):
        pass


class PlainRestorelessView(LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root>plain</div>"


SHAPES = ["InstanceOverrideView", "LateOverrideView", "HiddenOverrideView"]


@pytest.fixture
def shape(request, monkeypatch):
    if request.param == "LateOverrideView":
        monkeypatch.setattr(LateOverrideView, "_restore_snapshot", _override, raising=False)
    return globals()[request.param]


def _http_request(rf):
    from django.contrib.auth.models import AnonymousUser
    from django.contrib.sessions.backends.db import SessionStore

    request = rf.get("/removed-restore/")
    request.user = AnonymousUser()
    request.session = SessionStore()
    request.session.create()
    return request


@pytest.mark.django_db
@pytest.mark.parametrize("shape", SHAPES, indirect=True)
def test_the_http_get_refuses_each_shape(shape, rf):
    with pytest.raises(ImproperlyConfigured) as refused:
        shape.as_view()(_http_request(rf))
    assert shape.__name__ in str(refused.value)
    _assert_pointed(str(refused.value))


@pytest.mark.django_db
def test_the_http_get_still_serves_a_plain_view(rf):
    response = PlainRestorelessView.as_view()(_http_request(rf))
    assert response.status_code == 200 and b"plain" in response.content


async def _runtime_mount(view_class):
    from asgiref.sync import sync_to_async
    from django.test import override_settings

    from djust.runtime import ViewRuntime

    from .test_exposure_runtime import make_request
    from .test_runtime_state_save_tt_1894 import MockTransport

    request = await sync_to_async(make_request)()
    transport = MockTransport()
    transport.build_request = lambda: request
    runtime = ViewRuntime(transport)
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], DEBUG=True):
        await runtime.dispatch_mount(
            {
                "type": "mount",
                "view": "%s.%s" % (__name__, view_class.__name__),
                "url": "/removed-restore/",
            }
        )
    return transport


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("shape", SHAPES, indirect=True)
async def test_the_runtime_mount_refuses_each_shape(shape, caplog):
    transport = await _runtime_mount(shape)
    frames = [frame["type"] for frame in transport.sent]
    assert "mount" not in frames, frames
    assert frames[-1] == "error", frames
    assert "_restore_snapshot" in caplog.text and "removed in djust 2.0" in caplog.text


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_the_runtime_mount_still_mounts_a_plain_view():
    transport = await _runtime_mount(PlainRestorelessView)
    assert [frame["type"] for frame in transport.sent][-1] == "mount"
