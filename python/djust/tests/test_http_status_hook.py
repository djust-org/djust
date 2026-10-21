"""``LiveView.get_http_status()``: the HTTP status of the page's first render.

A page that renders a "not found" or "forbidden" state answered 200, because a
LiveView had no status hook and an override of ``get()`` never runs on the
WebSocket mount (``djust.V013``). The hook is read after the render, so it can
depend on what ``mount()`` found; the socket path never consults it.
"""

import sys

import pytest
from django.test import Client
from django.urls import path

from djust import LiveView


class _MaybeMissing(LiveView):
    template = "<div dj-root>{% if found %}found{% else %}No document at this ID{% endif %}</div>"

    def mount(self, request, doc_id: str = "", **kwargs):
        self.found = doc_id == "1"

    def get_http_status(self):
        return None if self.found else 404


class _Plain(LiveView):
    template = "<div dj-root>plain</div>"


setattr(sys.modules[__name__], "_MaybeMissing", _MaybeMissing)
setattr(sys.modules[__name__], "_Plain", _Plain)

urlpatterns = [
    path("docs/<str:doc_id>", _MaybeMissing.as_view()),
    path("plain", _Plain.as_view()),
]


@pytest.fixture(autouse=True)
def _urls(settings):
    settings.ROOT_URLCONF = __name__
    settings.ALLOWED_HOSTS = ["testserver"]
    settings.LIVEVIEW_ALLOWED_MODULES = [__name__]


@pytest.mark.django_db
def test_the_hook_sets_the_status_and_keeps_the_page():
    response = Client().get("/docs/2")
    assert response.status_code == 404
    assert b"No document at this ID" in response.content


@pytest.mark.django_db
def test_none_keeps_200():
    response = Client().get("/docs/1")
    assert response.status_code == 200
    assert b"found" in response.content


@pytest.mark.django_db
def test_the_default_hook_is_200():
    assert Client().get("/plain").status_code == 200


def test_the_default_hook_returns_none():
    assert _Plain().get_http_status() is None
