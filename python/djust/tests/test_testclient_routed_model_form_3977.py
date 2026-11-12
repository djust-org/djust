"""``LiveViewTestClient.mount()`` mounts a routed ``ModelFormMixin`` view (#3977).

The client never bound the route's kwargs (the only source a ``ModelFormMixin``
looks its object up from) and never ran the post-mount object check, so a routed
edit view "mounted" with ``object = None``, empty ``kwargs`` and no form, and
nothing said so. ``mount(route_kwargs=...)`` binds them before ``mount()`` as the
transports do, and a denial raises ``PermissionDenied`` as the transports answer
403 / ``permission_denied``.
"""

import uuid

import pytest
from django import forms
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.test import override_settings
from django.urls import path

from djust import LiveView
from djust.forms import ModelFormMixin
from djust.testing import LiveViewTestClient

pytestmark = pytest.mark.django_db

REVOKED: set = set()


class GroupForm(forms.ModelForm):
    class Meta:
        model = Group
        fields = ["name"]


class EditGroupView(ModelFormMixin[Group], LiveView):
    exposure_policy = "legacy"
    template = '<div dj-root><h1>{{ object.name }}</h1><input name="name"></div>'
    model = Group
    form_class = GroupForm
    login_required = True

    def get_queryset(self):
        return super().get_queryset().filter(user=self.request.user)

    def has_object_permission(self, request, obj):
        return obj.pk not in REVOKED


urlpatterns = [path("groups/<int:pk>/edit/", EditGroupView.as_view())]


@pytest.fixture(autouse=True)
def _routes():
    REVOKED.clear()
    with override_settings(ROOT_URLCONF=__name__, DJUST_TENANTS=None):
        yield
    REVOKED.clear()


def _world():
    from django.contrib.auth import get_user_model

    tag = uuid.uuid4().hex[:8]
    alice = get_user_model().objects.create_user(username="alice-" + tag)
    mine = Group.objects.create(name="Mine " + tag)
    theirs = Group.objects.create(name="Theirs " + tag)
    alice.groups.add(mine)
    return alice, mine, theirs


def test_route_kwargs_mount_the_routed_record():
    alice, mine, _ = _world()
    client = LiveViewTestClient(EditGroupView, user=alice).mount(route_kwargs={"pk": mine.pk})
    view = client.view_instance
    assert view.object == mine
    assert view.kwargs == {"pk": mine.pk}
    assert view.form_data["name"] == mine.name
    assert client._mounted
    # Driven by events as usual.
    result = client.send_event("validate_field", field="name", value="Renamed")
    assert result["success"], result


def test_a_mount_kwarg_is_not_the_route():
    """``mount(pk=...)`` is a mount kwarg, which the view deliberately ignores: denied, loudly."""
    alice, mine, _ = _world()
    client = LiveViewTestClient(EditGroupView, user=alice)
    with pytest.raises(PermissionDenied):
        client.mount(pk=mine.pk)
    assert not client._mounted


def test_a_record_the_user_may_not_see_is_denied():
    alice, _, theirs = _world()
    with pytest.raises(PermissionDenied):
        LiveViewTestClient(EditGroupView, user=alice).mount(route_kwargs={"pk": theirs.pk})


def test_a_revoked_object_permission_is_denied():
    alice, mine, _ = _world()
    REVOKED.add(mine.pk)
    with pytest.raises(PermissionDenied):
        LiveViewTestClient(EditGroupView, user=alice).mount(route_kwargs={"pk": mine.pk})


def test_a_view_without_an_object_is_unaffected():
    class Plain(LiveView):
        exposure_policy = "legacy"
        template = "<div dj-root></div>"

        def mount(self, request, **kwargs):
            self.seen = kwargs

    client = LiveViewTestClient(Plain).mount(route_kwargs={"pk": 1}, x=2)
    assert client.view_instance.seen == {"x": 2}


def test_the_smoke_test_passes_route_kwargs_from_view_config():
    from djust.testing import LiveViewSmokeTest

    alice, mine, _ = _world()
    smoke = LiveViewSmokeTest()
    smoke.view_config = {EditGroupView: {"route_kwargs": {"pk": mine.pk}, "user": alice}}
    assert smoke._make_client(EditGroupView).view_instance.object == mine
