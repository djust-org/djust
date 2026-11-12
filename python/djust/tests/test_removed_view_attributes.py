"""2.0 removed the actor system; a view that still asks for it is refused.

``use_actors = True`` used to move a view's state and rendering into a Tokio
actor. Ignoring the attribute would leave such a view running on a different
path than the one it asked for, without a word, so ``LiveView`` refuses the
class when it is created and names the replacement.
"""

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from djust import LiveView
from djust._removed_settings import REMOVED_VIEW_ATTRIBUTES


def test_a_view_that_sets_use_actors_is_refused_at_class_creation():
    with pytest.raises(ImproperlyConfigured) as refused:

        class ActorView(LiveView):
            exposure_policy = "explicit"
            template = "<div dj-root></div>"
            use_actors = True

    message = str(refused.value)
    assert "ActorView" in message
    assert "use_actors" in message
    assert "removed in djust 2.0" in message
    # The replacement, not just the refusal.
    assert "delete the attribute" in message
    assert "start_async()" in message


def test_a_mixin_that_sets_use_actors_is_refused_where_it_meets_liveview():
    class ActorsMixin:
        use_actors = True

    with pytest.raises(ImproperlyConfigured, match="use_actors"):

        class MixedView(ActorsMixin, LiveView):
            exposure_policy = "explicit"
            template = "<div dj-root></div>"


def test_a_subclass_of_a_plain_view_can_still_turn_it_on_and_is_refused():
    class Base(LiveView):
        exposure_policy = "explicit"
        template = "<div dj-root></div>"

    with pytest.raises(ImproperlyConfigured, match="Child"):

        class Child(Base):
            use_actors = True


def test_use_actors_false_and_an_ordinary_view_are_accepted():
    class Off(LiveView):
        exposure_policy = "explicit"
        template = "<div dj-root></div>"
        use_actors = False

    class Plain(LiveView):
        exposure_policy = "explicit"
        template = "<div dj-root></div>"

    assert Off() is not None and Plain() is not None


def test_the_attribute_and_the_extension_surface_are_gone():
    from djust import _rust

    assert not hasattr(LiveView, "use_actors")
    for name in ("create_session_actor", "get_actor_stats", "SessionActorHandle"):
        assert not hasattr(_rust, name), name
    assert "use_actors" in REMOVED_VIEW_ATTRIBUTES


# --------------------------------------------------------------------------
# The mount path. Three shapes the class-creation check cannot see, each over
# the HTTP GET and the shared runtime mount (WebSocket and SSE).
# --------------------------------------------------------------------------


class InstanceAttributeView(LiveView):
    """Sets it on the instance, in ``mount()``."""

    exposure_policy = "explicit"

    template = "<div dj-root>instance</div>"

    def mount(self, request, **kwargs):
        self.use_actors = True


class LateClassAttributeView(LiveView):
    """Plain when created; a test assigns the class attribute afterwards."""

    exposure_policy = "explicit"

    template = "<div dj-root>late</div>"


class _SkipsSuper:
    def __init_subclass__(cls, **kwargs):
        pass  # does not call super(): LiveView.__init_subclass__ never runs


class HiddenFromInitSubclassView(_SkipsSuper, LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root>hidden</div>"
    use_actors = True


class PlainMountedView(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root>plain</div>"


SHAPES = ["InstanceAttributeView", "LateClassAttributeView", "HiddenFromInitSubclassView"]


@pytest.fixture
def shape(request, monkeypatch):
    if request.param == "LateClassAttributeView":
        monkeypatch.setattr(LateClassAttributeView, "use_actors", True, raising=False)
    return globals()[request.param]


def _http_request(rf):
    from django.contrib.auth.models import AnonymousUser
    from django.contrib.sessions.backends.db import SessionStore

    request = rf.get("/removed/")
    request.user = AnonymousUser()
    request.session = SessionStore()
    request.session.create()
    return request


# The subject is the failure's message, which explicit views show only under DEBUG.
@override_settings(DEBUG=True)
@pytest.mark.django_db
@pytest.mark.parametrize("shape", SHAPES, indirect=True)
def test_the_http_get_refuses_each_shape(shape, rf):
    with pytest.raises(ImproperlyConfigured) as refused:
        shape.as_view()(_http_request(rf))
    message = str(refused.value)
    assert shape.__name__ in message
    assert "use_actors" in message and "removed in djust 2.0" in message


@pytest.mark.django_db
@override_settings(DEBUG=False)
@pytest.mark.parametrize("shape", SHAPES, indirect=True)
def test_without_debug_the_http_get_still_refuses_each_shape_without_the_detail(shape, rf):
    response = shape.as_view()(_http_request(rf))
    assert response.status_code == 500
    assert b"use_actors" not in response.content


@pytest.mark.django_db
def test_the_http_get_still_serves_a_plain_view(rf):
    response = PlainMountedView.as_view()(_http_request(rf))
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
            {"type": "mount", "view": "%s.%s" % (__name__, view_class.__name__), "url": "/removed/"}
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
    assert "use_actors" in caplog.text and "removed in djust 2.0" in caplog.text


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_the_runtime_mount_still_mounts_a_plain_view():
    transport = await _runtime_mount(PlainMountedView)
    assert [frame["type"] for frame in transport.sent][-1] == "mount"


def test_v017_and_v020_are_retired_and_nothing_emits_them():
    import pathlib

    import djust.checks

    package = pathlib.Path(djust.checks.__file__).parent
    source = "".join(path.read_text() for path in sorted(package.glob("*.py")))
    for retired in ("djust.V017", "djust.V020"):
        assert retired not in source, retired
