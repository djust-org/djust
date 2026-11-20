"""#4244: a ``readonly_fields`` entry naming a ModelAdmin method or a callable is shown.

Django's admin displays such an entry on the change form (label from
``short_description`` / the name, value from calling it with the object).
The djust change form showed only readonly *model* fields: a method or
callable name was dropped, and a method name in ``ModelAdmin.fields`` made
the generated form raise.
"""

from __future__ import annotations

import re
from decimal import Decimal

import pytest
from django.contrib import admin
from django.core.exceptions import ObjectDoesNotExist
from django.test import Client
from django.utils.html import format_html
from django.urls import path

from djust.admin_ext import DjustAdminSite, DjustModelAdmin
from djust_rentals.models import Property

pytestmark = pytest.mark.admin

ROW = {
    "name": "Seed house",
    "address": "1 Seed St",
    "city": "Austin",
    "state": "TX",
    "zip_code": "78701",
    "property_type": "house",
    "bedrooms": 2,
    "bathrooms": Decimal("1.5"),
    "square_feet": 900,
    "monthly_rent": Decimal("1000.00"),
    "security_deposit": Decimal("500.00"),
}


def rent_per_sqft(obj):
    return "%.2f per sq ft" % (obj.monthly_rent / obj.square_feet)


rent_per_sqft.short_description = "Rent density"


class MethodAdmin(DjustModelAdmin):
    """Django's usual shape: the method name is in ``fields`` and in ``readonly_fields``."""

    fields = ["name", "location", "city"]
    readonly_fields = ["location", "city"]

    def location(self, obj):
        return "<b>%s</b>, %s" % (obj.city, obj.state)

    location.short_description = "Where"


class CallableAdmin(DjustModelAdmin):
    """A module-level callable, placed by ``fieldsets``."""

    fieldsets = [(None, {"fields": ["name", rent_per_sqft]})]
    readonly_fields = [rent_per_sqft]


CALLS: list = []


class SafeAndBooleanAdmin(DjustModelAdmin):
    """A method returning author-marked-safe HTML, a boolean display, an unsafe label."""

    fields = ["name", "badge", "is_big", "evil"]
    readonly_fields = ["badge", "is_big", "evil"]

    def badge(self, obj):
        CALLS.append(obj)
        return format_html("<em>{}</em>", obj.city)

    @admin.display(boolean=True, description="Big?")
    def is_big(self, obj):
        return obj.square_feet > 500

    @admin.display(description="<script>label</script>")
    def evil(self, obj):
        return '<script>alert("x")</script>'


class MissingRelatedAdmin(DjustModelAdmin):
    """A readonly method whose lookup raises ObjectDoesNotExist (e.g. a missing related row)."""

    fields = ["name", "gone"]
    readonly_fields = ["gone"]

    def gone(self, obj):
        raise ObjectDoesNotExist("related row is gone")


METHOD_SITE = DjustAdminSite(name="readonly_callable_4244_method")
METHOD_SITE.register(Property, MethodAdmin)
CALLABLE_SITE = DjustAdminSite(name="readonly_callable_4244_callable")
CALLABLE_SITE.register(Property, CallableAdmin)
SAFE_SITE = DjustAdminSite(name="readonly_callable_4244_safe")
SAFE_SITE.register(Property, SafeAndBooleanAdmin)

MISSING_SITE = DjustAdminSite(name="readonly_callable_4244_missing")
MISSING_SITE.register(Property, MissingRelatedAdmin)

urlpatterns = [
    path("x4244-missing/", MISSING_SITE.urls),
    path("x4244-method/", METHOD_SITE.urls),
    path("x4244-callable/", CALLABLE_SITE.urls),
    path("x4244-safe/", SAFE_SITE.urls),
]


@pytest.fixture(autouse=True)
def _urls(settings):
    settings.ROOT_URLCONF = __name__
    settings.ALLOWED_HOSTS = ["testserver"]
    settings.LIVEVIEW_ALLOWED_MODULES = ["djust", __name__]


@pytest.fixture
def staff(db, django_user_model):
    user = django_user_model.objects.create_superuser("root", password="pw")
    client = Client()
    client.force_login(user)
    return client


@pytest.mark.django_db
def test_readonly_admin_method_is_shown_escaped(staff):
    row = Property.objects.create(**ROW)
    response = staff.get("/x4244-method/djust_rentals/property/%d/change/" % row.pk)
    assert response.status_code == 200
    html = response.content.decode()
    assert re.search(
        r"Where</span>\s*<div[^>]*>\s*<p[^>]*>&lt;b&gt;Austin&lt;/b&gt;, TX</p>", html
    ), "readonly ModelAdmin method not shown (or not escaped)"
    assert "<b>Austin</b>" not in html
    # The readonly model field is still shown, and neither is an input.
    assert re.search(r"City</span>\s*<div[^>]*>\s*<p[^>]*>Austin</p>", html)
    assert 'name="location"' not in html and 'name="city"' not in html


@pytest.mark.django_db
def test_readonly_callable_is_shown(staff):
    row = Property.objects.create(**ROW)
    response = staff.get("/x4244-callable/djust_rentals/property/%d/change/" % row.pk)
    assert response.status_code == 200
    html = response.content.decode()
    assert re.search(r"Rent density</span>\s*<div[^>]*>\s*<p[^>]*>1\.11 per sq ft</p>", html), (
        "readonly callable not shown"
    )


@pytest.mark.django_db
def test_unsafe_string_from_readonly_method_and_label_are_escaped(staff):
    row = Property.objects.create(**ROW)
    response = staff.get("/x4244-safe/djust_rentals/property/%d/change/" % row.pk)
    assert response.status_code == 200
    html = response.content.decode()
    assert '<script>alert("x")</script>' not in html
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in html
    assert "<script>label</script>" not in html
    assert "&lt;script&gt;label&lt;/script&gt;" in html


@pytest.mark.django_db
def test_author_marked_safe_value_renders_and_boolean_is_yes(staff):
    row = Property.objects.create(**ROW)
    response = staff.get("/x4244-safe/djust_rentals/property/%d/change/" % row.pk)
    assert response.status_code == 200
    html = response.content.decode()
    # format_html output is the author's own markup: not double-escaped (Django parity).
    assert re.search(r"Badge</span>\s*<div[^>]*>\s*<p[^>]*><em>Austin</em></p>", html)
    assert re.search(r"Big\?</span>\s*<div[^>]*>\s*<p[^>]*>Yes</p>", html)


@pytest.mark.django_db
def test_add_page_shows_dash_and_does_not_call_method(staff):
    CALLS.clear()
    response = staff.get("/x4244-method/djust_rentals/property/add/")
    assert response.status_code == 200
    html = response.content.decode()
    assert re.search(r"Where</span>\s*<div[^>]*>\s*<p[^>]*>-</p>", html)
    response = staff.get("/x4244-safe/djust_rentals/property/add/")
    assert response.status_code == 200
    assert CALLS == []


@pytest.mark.django_db
def test_readonly_method_raising_does_not_exist_shows_empty_value(staff):
    row = Property.objects.create(**ROW)
    response = staff.get("/x4244-missing/djust_rentals/property/%d/change/" % row.pk)
    assert response.status_code == 200
    html = response.content.decode()
    assert re.search(r"Gone</span>\s*<div[^>]*>\s*<p[^>]*>-</p>", html)
