"""#3955 -- ``djust.S015``: ``djust_exclude_fields`` hides the field, not a
``get_*()`` / ``@property`` computed from it.

The eager serializer collects explicit ``get_*`` methods and ``@property``
values, and the floor proxy answers a zero-argument method, so a custom
``User`` excluding ``last_name`` still renders it through ``get_full_name``.
The check reads the members' source; nothing is called.
"""

from __future__ import annotations

import pytest
from django.apps import apps
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.test import override_settings
from django.test.utils import isolate_apps

from djust.checks.security import check_excluded_field_derived_reads


def _run(monkeypatch, *model_classes) -> list:
    monkeypatch.setattr(apps, "get_models", lambda *a, **k: list(model_classes))
    return [m for m in check_excluded_field_derived_reads(None) if m.id == "djust.S015"]


@pytest.fixture
def isolated():
    with isolate_apps("django.contrib.auth"):
        yield


def _model(name: str, body: dict, bases=(models.Model,)):
    attrs = {
        "__module__": __name__,
        "Meta": type("Meta", (), {"app_label": "auth"}),
        "secret": models.CharField(max_length=10),
        "title": models.CharField(max_length=10),
        **body,
    }
    return type(name, bases, attrs)


def test_warns_for_get_method_reading_excluded_field(isolated, monkeypatch):
    def get_display(self):
        return "%s (%s)" % (self.title, self.secret)

    model = _model("S015Get", {"djust_exclude_fields": ["secret"], "get_display": get_display})
    messages = _run(monkeypatch, model)
    assert len(messages) == 1
    assert "S015Get.get_display reads secret" in messages[0].msg
    assert "djust_exclude_fields" in messages[0].hint


def test_warns_for_property(isolated, monkeypatch):
    def masked(self):
        return getattr(self, "secret")[:2]

    model = _model("S015Prop", {"djust_exclude_fields": ("secret",), "masked": property(masked)})
    messages = _run(monkeypatch, model)
    assert [m.msg.split(" reads ")[0].rsplit(".", 1)[-1] for m in messages] == ["masked"]


def test_warns_for_inherited_django_method(isolated, monkeypatch):
    model = type(
        "S015User",
        (AbstractUser,),
        {
            "__module__": __name__,
            "Meta": type("Meta", (), {"app_label": "auth"}),
            "djust_exclude_fields": ["last_name"],
        },
    )
    messages = _run(monkeypatch, model)
    assert any("S015User.get_full_name reads last_name" in m.msg for m in messages), [
        m.msg for m in messages
    ]


def test_one_hop_through_helper(isolated, monkeypatch):
    def _fmt(self):
        return self.secret.upper()

    def get_label(self):
        return self._fmt()

    model = _model(
        "S015Hop",
        {"djust_exclude_fields": ["secret"], "_fmt": _fmt, "get_label": get_label},
    )
    messages = _run(monkeypatch, model)
    assert len(messages) == 1 and "get_label reads secret" in messages[0].msg


def test_no_warning_without_reads(isolated, monkeypatch):
    def get_display(self):
        return self.title

    model = _model("S015Clean", {"djust_exclude_fields": ["secret"], "get_display": get_display})
    assert _run(monkeypatch, model) == []


def test_no_warning_without_exclude_fields(isolated, monkeypatch):
    def get_display(self):
        return self.secret

    model = _model("S015NoExclude", {"get_display": get_display})
    assert _run(monkeypatch, model) == []


def test_unreadable_source_is_skipped(isolated, monkeypatch):
    namespace: dict = {}
    exec("def get_display(self):\n    return self.secret\n", namespace)
    model = _model(
        "S015Exec", {"djust_exclude_fields": ["secret"], "get_display": namespace["get_display"]}
    )
    assert _run(monkeypatch, model) == []


def test_suppressed(isolated, monkeypatch):
    def get_display(self):
        return self.secret

    model = _model("S015Quiet", {"djust_exclude_fields": ["secret"], "get_display": get_display})
    with override_settings(DJUST_CONFIG={"suppress_checks": ["S015"]}):
        assert _run(monkeypatch, model) == []


def test_warns_for_plain_method(isolated, monkeypatch):
    """The floor proxy answers any public zero-argument method as a call (``{{ obj.label }}``), not
    only ``get_*`` (train/2.0-ah review S6). A method that needs an argument, a private one and an
    ``alters_data`` one are not template-callable and stay quiet."""

    def label(self):
        return "L:%s" % self.secret

    def needs_arg(self, prefix):
        return prefix + self.secret

    def _private(self):
        return self.secret

    def wipe(self):
        return self.secret

    wipe.alters_data = True
    model = _model(
        "S015Plain",
        {
            "djust_exclude_fields": ["secret"],
            "label": label,
            "needs_arg": needs_arg,
            "_private": _private,
            "wipe": wipe,
        },
    )
    messages = _run(monkeypatch, model)
    assert [m.msg.split(" reads ")[0].rsplit(".", 1)[-1] for m in messages] == ["label"]
