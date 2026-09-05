"""#2631 — ``dj-view="{{ dj_view_id }}"`` renders EMPTY; bare ``dj-root`` is stamped.

The docs taught ``dj-view="{{ dj_view_id }}"`` for months; djust never injects
that variable (``git log -S dj_view_id -- python/`` is empty), so Django
resolves it to ``string_if_invalid`` and the client refuses to mount. The
docs half was corrected in PRs #2646 / #2666 to teach ``dj-root`` alone.
This file pins the CODE facts those docs now rely on, through the real HTTP
GET page-shell path (``LiveView.as_view()`` → ``render_full_template``):

* a template with bare ``dj-root`` comes back with ``dj-view`` stamped
  server-side with the rendering view's dotted path;
* the phantom ``{{ dj_view_id }}`` spelling renders ``dj-view=""`` — the
  reporter's symptom — which is exactly what the new T013 sub-case
  (``python/djust/checks/templates.py``) refuses at ``manage.py check`` time.
"""

from __future__ import annotations

import re

import pytest
from django.test import RequestFactory

from djust import LiveView


class _BareRootView(LiveView):
    template = "<div dj-root>n={{ n }}</div>"

    def mount(self, request, **kwargs):
        self.n = 1


class _PhantomView(LiveView):
    template = '<div dj-root dj-view="{{ dj_view_id }}">n={{ n }}</div>'

    def mount(self, request, **kwargs):
        self.n = 1


def _get(view_cls):
    from django.contrib.sessions.backends.db import SessionStore

    request = RequestFactory().get("/x/")
    request.session = SessionStore()
    request.session.create()
    response = view_cls.as_view()(request)
    if hasattr(response, "render"):
        response.render()
    return response.content.decode()


@pytest.mark.django_db
def test_bare_dj_root_is_stamped_with_the_views_dotted_path():
    html = _get(_BareRootView)
    m = re.search(r'<div[^>]*\bdj-view="([^"]*)"', html)
    assert m, f"dj-root alone must be stamped with dj-view on the GET path; got {html!r}"
    assert m.group(1) == f"{__name__}._BareRootView", m.group(1)


@pytest.mark.django_db
def test_phantom_dj_view_id_renders_empty_attribute():
    """The reporter's symptom, pinned so the docs choice stays honest: there is
    no framework-provided ``dj_view_id``; teaching it would be wrong."""
    html = _get(_PhantomView)
    assert 'dj-view=""' in html, html
