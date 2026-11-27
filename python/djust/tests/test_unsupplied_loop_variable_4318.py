"""A ``{% for %}`` loop variable is never an unsupplied name (#4318).

``{% for head in items %}{{ head }}`` failed a real request under
``DJUST_UNSUPPLIED_NAMES = "error"``: ``template_paths()`` lists the loop variable
``head`` as a root (it now folds it onto its iterable), and Django's ``View.setup()`` sets ``self.head = self.get`` on
every instance, so the check took ``head`` for a name the view holds. The test
client never calls ``setup()``, so only a request through ``as_view()`` showed it.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.signed_cookies import SessionStore
from django.test import RequestFactory, override_settings

from djust import LiveView, state
from djust import _exposure_unsupplied
from djust._exposure_unsupplied import UnsuppliedTemplateNameError

pytestmark = pytest.mark.django_db


class LoopNamedHead(LiveView):
    template = "<div dj-root>{% for head in items %}<p>{{ head }}</p>{% endfor %}</div>"
    items = state(["a", "b"])


class LoopNamedLikeHeldAttr(LiveView):
    """The loop variable shares its name with an attribute the app sets."""

    template = "<div dj-root>{% for k, row in pairs %}<p>{{ k }}{{ row }}</p>{% endfor %}</div>"
    pairs = state([["a", 1]])

    def mount(self, request, **kwargs):
        self.row = "server-only"


class StillHeld(LiveView):
    """Control: a name the view holds and never supplies is still an error."""

    template = "<div dj-root>{{ held }}</div>"

    def mount(self, request, **kwargs):
        self.held = "x"


@pytest.fixture(autouse=True)
def fresh_reports():
    saved = dict(_exposure_unsupplied._REPORTED)
    _exposure_unsupplied._REPORTED.clear()
    yield
    _exposure_unsupplied._REPORTED.clear()
    _exposure_unsupplied._REPORTED.update(saved)


def _get(view_class):
    request = RequestFactory().get("/")
    request.user, request.session = AnonymousUser(), SessionStore()
    return view_class.as_view()(request)


@override_settings(DJUST_UNSUPPLIED_NAMES="error")
@pytest.mark.parametrize("view_class", [LoopNamedHead, LoopNamedLikeHeldAttr])
def test_a_loop_variable_renders_under_the_error_mode(view_class):
    response = _get(view_class)
    assert response.status_code == 200


@override_settings(DJUST_UNSUPPLIED_NAMES="error")
def test_a_held_name_outside_a_loop_still_raises():
    with pytest.raises(UnsuppliedTemplateNameError, match="held"):
        _get(StillHeld)


def test_view_setup_attributes_are_not_held_by_the_view():
    view = LoopNamedHead()
    view.setup(RequestFactory().get("/"))
    assert "head" in vars(view)
    for name in ("head", "request", "args", "kwargs"):
        assert not _exposure_unsupplied._held_by_view(view, name), name


def test_template_paths_folds_a_loop_variable_onto_its_iterable():
    """As its docstring says: the loop variable is not a root of its own."""
    view = LoopNamedLikeHeldAttr()
    paths = view.template_paths()
    assert "pairs" in paths
    assert "k" not in paths and "row" not in paths, paths
