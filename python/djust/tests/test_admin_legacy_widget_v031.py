"""#4185: an admin widget that states the legacy exposure policy.

The admin change list and change form are explicit, and an explicit page
cannot embed a legacy view: the page fails to render. There is no runtime
legacy branch. ``djust.V031`` (an Error) names the widget and the admin slot
that lists it, and the runtime error names the embedded view's class.
"""

from __future__ import annotations

import sys

import pytest
from django.contrib.auth.models import Group
from django.test import Client
from django.urls import path

from djust import LiveView
from djust._exposure import ExposureError
from djust.admin_ext import DjustAdminSite, DjustModelAdmin
from djust.checks.components import check_admin_legacy_widgets

pytestmark = pytest.mark.admin

MODULE = __name__


class LegacyWidget(LiveView):
    exposure_policy = "legacy"  # legacy subject: V031 reports an admin widget that states it
    template = "<p>legacy {{ object_id }}</p>"
    label = "Legacy widget"

    def mount(self, request, object_id=None, **kwargs):
        self.object_id = object_id


class ExplicitWidget(LiveView):
    exposure_policy = "explicit"
    template = "<p>explicit</p>"
    label = "Explicit widget"


setattr(sys.modules[__name__], "LegacyWidget", LegacyWidget)
setattr(sys.modules[__name__], "ExplicitWidget", ExplicitWidget)


class LegacyWidgetAdmin(DjustModelAdmin):
    change_list_widgets = [ExplicitWidget, LegacyWidget]
    change_form_widgets = [LegacyWidget]


class ExplicitWidgetAdmin(DjustModelAdmin):
    change_list_widgets = [ExplicitWidget]
    change_form_widgets = [ExplicitWidget]


SITE = DjustAdminSite(name="v031_admin")
SITE.register(Group, LegacyWidgetAdmin)

urlpatterns = [path("v031-admin/", SITE.urls)]


def _v031(admin: type) -> list:
    label = "%s.%s." % (MODULE, admin.__qualname__)
    return [m for m in check_admin_legacy_widgets(None) if str(m.msg).startswith(label)]


def test_v031_names_each_slot_that_lists_a_legacy_widget():
    messages = _v031(LegacyWidgetAdmin)
    widget = "%s.LegacyWidget" % MODULE
    admin = "%s.LegacyWidgetAdmin" % MODULE
    assert sorted(str(m.msg).split(" lists ")[0] for m in messages) == [
        admin + ".change_form_widgets",
        admin + ".change_list_widgets",
    ]
    for message in messages:
        assert message.id == "djust.V031"
        assert message.is_serious()  # an Error: the page is broken now
        assert widget in str(message.msg) and "fails to render" in str(message.msg)
        assert 'exposure_policy = "explicit"' in message.hint and widget in message.hint


def test_v031_is_silent_for_explicit_widgets():
    assert _v031(ExplicitWidgetAdmin) == []


@pytest.fixture
def site_urls(settings):
    from djust.routing import _reset_route_map_cache

    settings.ROOT_URLCONF = MODULE
    settings.ALLOWED_HOSTS = ["testserver"]
    settings.LIVEVIEW_ALLOWED_MODULES = ["djust", MODULE]
    _reset_route_map_cache()
    yield
    _reset_route_map_cache()


@pytest.mark.django_db
@pytest.mark.parametrize("page", ["", "%d/change/", "add/"])
def test_the_admin_page_error_names_the_legacy_widget(site_urls, settings, django_user_model, page):
    """What V031 predicts, and the runtime error names the class.

    Under ``DEBUG``: an explicit page's failure is value-free otherwise
    (ADR-038 D-a, the generic 500), which is why V031 exists.
    """
    settings.DEBUG = True
    group = Group.objects.create(name="g")
    user = django_user_model.objects.create_superuser("root", password="pw")
    browser = Client()
    browser.force_login(user)
    url = "/v031-admin/auth/group/" + (page % group.pk if "%d" in page else page)
    with pytest.raises(ExposureError, match=r"embedded view %s\.LegacyWidget$" % MODULE):
        browser.get(url)
