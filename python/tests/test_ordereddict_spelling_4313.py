"""#4313 — ``{{ v }}`` of an ``OrderedDict`` renders what Django renders, on both paths."""

from __future__ import annotations

import collections
from typing import Any

import django
import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
            "django.contrib.sessions",
        ],
        SECRET_KEY="test-4313",
        SESSION_ENGINE="django.contrib.sessions.backends.cache",
        USE_TZ=True,
    )
    django.setup()

from django.contrib.sessions.backends.cache import SessionStore  # noqa: E402
from django.template.backends.django import DjangoTemplates  # noqa: E402
from django.test import RequestFactory  # noqa: E402

from djust import LiveView, state  # noqa: E402
from djust.template.backend import DjustTemplateBackend  # noqa: E402

OD = collections.OrderedDict

CASES = {
    "whole": "{{ v }}",
    "nested": "{{ outer }}|{{ outer.inner }}",
    "key": "{{ v.a }}|{{ v.b }}",
    "loop": "{% for k, x in v.items %}{{ k }}={{ x }};{% endfor %}",
    "dictsort": "{% for r in v|dictsort:'0' %}{{ r }};{% endfor %}",
}


def _ctx() -> dict[str, Any]:
    return {"v": OD(a=1, b=2), "outer": {"inner": OD(k="alpha")}}


def _engines():
    params = {"DIRS": [], "APP_DIRS": False, "OPTIONS": {}}
    return (
        DjangoTemplates({**params, "NAME": "dj4313"}),
        DjustTemplateBackend({**params, "NAME": "du4313"}),
    )


@pytest.mark.parametrize("name", sorted(CASES))
def test_backend_matches_django(name):
    django_engine, djust_engine = _engines()
    source = CASES[name]
    expected = django_engine.from_string(source).render(_ctx())
    assert djust_engine.from_string(source).render(_ctx()) == expected
    if name in ("whole", "nested"):
        assert "OrderedDict(" in expected


def test_backend_plain_dict_is_unchanged():
    django_engine, djust_engine = _engines()
    ctx = {"v": {"a": 1}}
    assert djust_engine.from_string("{{ v }}").render(ctx) == django_engine.from_string(
        "{{ v }}"
    ).render(ctx)


class Page(LiveView):
    v = state()
    outer = state()
    template = (
        '<div dj-root dj-id="0">[{{ v }}][{{ outer }}][{{ outer.inner }}][{{ v.a }}]'
        "[{% for k, x in v.items %}{{ k }}={{ x }};{% endfor %}]</div>"
    )

    def mount(self, request: Any, **kwargs: Any) -> None:
        self.v = OD(a=1, b=2)
        self.outer = {"inner": OD(k="alpha")}


def test_liveview_render_matches_django():
    request = RequestFactory().get("/4313/")
    request.session = SessionStore()
    body = Page.as_view()(request).content.decode()
    django_engine, _ = _engines()
    expected = django_engine.from_string(
        "[{{ v }}][{{ outer }}][{{ outer.inner }}][{{ v.a }}]"
        "[{% for k, x in v.items %}{{ k }}={{ x }};{% endfor %}]"
    ).render(_ctx())
    assert expected in body, body
