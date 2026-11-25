"""A page that extends a base answers its first fallback event with patches (#4283).

Under Django 6.0 a template that extends another goes through the authenticated
page-root path: the page GET and the VDOM baseline come from one evaluation. Two
things on that path cost the first fallback answer a whole page, which Django
5.2 (the old path) never did:

* the page GET carried no ``djust-render-digest``, so the client had nothing to
  echo, and
* the pre-handler render marked the view as holding embedded children because
  the path sets ``_explicit_child_vdom_tracking`` on every diff render, with or
  without a child.

The demo's Markdown stream page (``dj-update="ignore"`` region in a template
extending ``base.html``) was the case that failed in CI on 6.0.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from django.test import Client, override_settings
from django.urls import path

from djust import LiveView
from djust.decorators import event_handler, state
from djust._http_fallback_baseline import _holds_other_views
from djust.tests.http_fallback_matrix import meta_digest, post_event

pytestmark = [pytest.mark.django_db(transaction=True)]

BASE = (
    "<!DOCTYPE html><html><head><title>t</title></head><body>"
    "{% block content %}{% endblock %}</body></html>"
)
PAGE = (
    '{% extends "base4283.html" %}{% block content %}<div dj-root>'
    '<button dj-click="toggle">Toggle</button>{% if open %}<p>Details</p>{% endif %}'
    '<article dj-stream="s" dj-update="ignore"><p>kept</p></article>'
    "</div>{% endblock %}"
)


class Extending(LiveView):
    exposure_policy = "explicit"
    template_name = "page4283.html"
    open = state(False, persist="server")

    @event_handler
    def toggle(self, **kwargs):
        self.open = not self.open


urlpatterns = [path("f/extends/", Extending.as_view())]


@pytest.fixture(autouse=True)
def _site(tmp_path):
    (tmp_path / "base4283.html").write_text(BASE)
    (tmp_path / "page4283.html").write_text(PAGE)
    templates = [
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "DIRS": [str(tmp_path)],
            "APP_DIRS": True,
            "OPTIONS": {"context_processors": []},
        }
    ]
    with override_settings(
        ROOT_URLCONF=__name__,
        ALLOWED_HOSTS=["*"],
        DEBUG=False,
        TEMPLATES=templates,
        LIVEVIEW_ALLOWED_MODULES=["djust"],
    ):
        yield


def test_the_first_fallback_event_of_an_extending_page_answers_with_patches():
    client = Client()
    response = client.get("/f/extends/")
    assert response.status_code == 200
    digest = meta_digest(response.content.decode())
    assert digest, "the page GET carries a render digest"
    answer = post_event(client, "/f/extends/", "toggle", {}, digest)
    assert answer.status_code == 200, answer.content[:300]
    body = json.loads(answer.content)
    assert "html" not in body, sorted(body)
    assert body["patches"], body


def test_the_tracking_flag_alone_is_not_an_embedded_child():
    flag = {"_explicit_child_vdom_tracking": True}
    bare = SimpleNamespace(**flag, _explicit_child_vdom_bindings={}, __dict__={})
    assert _holds_other_views(bare) is False
    holding = SimpleNamespace(**flag, _explicit_child_vdom_bindings={"n": object()}, __dict__={})
    assert _holds_other_views(holding) is True
