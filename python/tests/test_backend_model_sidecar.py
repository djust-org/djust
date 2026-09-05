"""Backend JIT snapshots must not replace protected runtime model objects."""

import pytest
from django.contrib.auth.models import Group, User
from django.db import models
from django.template import Context, Engine
from django.test import override_settings
from django.utils.safestring import mark_safe

from djust.template import DjustTemplateBackend


class GreetingModel(models.Model):
    label = models.CharField(max_length=40)

    class Meta:
        app_label = "tests"

    def greet(self):
        self._calls = getattr(self, "_calls", 0) + 1
        return "hello " + self.label

    @property
    def safe_label(self):
        return mark_safe("<b>" + self.label + "</b>")


def backend():
    return DjustTemplateBackend({"NAME": "djust", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}})


@pytest.mark.parametrize(
    "source",
    [
        "{{ obj.greet }}",
        "{{ obj.greet|upper }}",
        "{% with result=obj.greet %}{{ result }}{% endwith %}",
        "{% if obj.greet %}yes{% endif %}",
        "{{ obj.safe_label }}",
        "{{ obj.safe_label|lower }}",
    ],
)
def test_model_lookup_matches_django(source):
    reference = GreetingModel(label="A&B")
    actual = GreetingModel(label="A&B")
    expected = Engine().from_string(source).render(Context({"obj": reference}))
    result = backend().from_string(source).render({"obj": actual})
    assert result == expected
    assert getattr(actual, "_calls", 0) == getattr(reference, "_calls", 0)


@override_settings(DEBUG=True)
def test_debug_keeps_the_model_representation():
    group = Group(name="清風")
    result = backend().from_string("{% debug %}").render({"group": group})
    expected = Engine().from_string("{% debug %}").render(Context({"group": group}))
    assert result.split("\n\n", 1)[0] == expected.split("\n\n", 1)[0]


@pytest.mark.parametrize("field", ["password", "is_superuser", "get_session_auth_hash"])
def test_model_sidecar_retains_field_protection(field):
    user = User(username="alice", password="do-not-expose", is_superuser=True)
    assert backend().from_string("{{ user." + field + " }}").render({"user": user}) == ""


@pytest.mark.django_db
def test_queryset_lookup_and_iteration_match_django():
    Group.objects.create(name="A&B")
    Group.objects.create(name="清風")
    source = "{{ groups.count }}|{% for group in groups %}{{ group.name }};{% endfor %}"
    expected = (
        Engine().from_string(source).render(Context({"groups": Group.objects.order_by("pk")}))
    )
    result = backend().from_string(source).render({"groups": Group.objects.order_by("pk")})
    assert result == expected
