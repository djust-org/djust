"""PresenceMixin's documented base order works, and the wrong one fails at
class definition with the reason (#3109).

``PresenceMixin.__init__`` sets ``_presence_tracked`` and friends. Django's
``View.__init__`` does not call ``super().__init__()``, so a mixin listed
AFTER ``LiveView`` never initialises, and the first ``track_presence()``
raised ``AttributeError: ... '_presence_tracked'`` from inside
``presence.py``. The module's own docstring examples used that order.
"""

from __future__ import annotations

import re
import textwrap

import pytest

import djust.presence as presence
from djust import LiveView
from djust.presence import LiveCursorMixin, PresenceMixin


def _docstring_examples() -> list[str]:
    """Every ``class …(…Mixin…):`` example in the module's docstrings."""
    docs = [presence.__doc__, PresenceMixin.__doc__, LiveCursorMixin.__doc__]
    found = []
    for doc in docs:
        found += re.findall(r"class \w+\(([^)]*(?:Presence|Cursor)Mixin[^)]*)\):", doc or "")
    return found


def test_every_docstring_example_lists_the_mixin_before_liveview():
    examples = _docstring_examples()
    assert len(examples) == 3, examples
    for bases in examples:
        names = [b.strip() for b in bases.split(",")]
        assert names.index("LiveView") > max(
            i for i, n in enumerate(names) if n.endswith("Mixin")
        ), bases


@pytest.mark.parametrize("mixin", [PresenceMixin, LiveCursorMixin])
def test_the_documented_order_tracks_presence(mixin):
    """The corrected module example, executed."""
    src = textwrap.dedent(
        """
        class DocumentView(Mixin, LiveView):
            presence_key = "document:{doc_id}"
            template = "<div></div>"
        """
    )
    namespace = {"Mixin": mixin, "LiveView": LiveView}
    exec(src, namespace)  # noqa: S102 — a fixed literal
    view = namespace["DocumentView"]()
    view.doc_id = 7
    # What the WebSocket mount sets; track_presence skips without it (#1612).
    view._websocket_session_id = "ws-3109"
    view.presence_unique_per_connection = True
    from django.contrib.auth.models import AnonymousUser
    from django.test import RequestFactory

    view.request = RequestFactory().get("/doc/7/")
    view.request.user = AnonymousUser()
    view.track_presence(meta={"name": "ada"})
    assert view._presence_tracked is True
    view.untrack_presence()


@pytest.mark.parametrize("mixin", [PresenceMixin, LiveCursorMixin])
def test_the_reversed_order_fails_at_class_definition_with_the_reason(mixin):
    with pytest.raises(TypeError) as excinfo:
        type("DocumentView", (LiveView, mixin), {"presence_key": "doc:1"})
    message = str(excinfo.value)
    assert "DocumentView" in message
    assert f"class DocumentView({mixin.__name__}, LiveView)" in message


def test_a_subclass_of_a_correctly_ordered_view_is_accepted():
    class Base(PresenceMixin, LiveView):
        presence_key = "doc:1"

    class Child(Base):
        pass

    assert Child()._presence_tracked is False


def test_a_mixin_used_without_a_django_view_is_not_refused():
    """Plain-object hosts (the test doubles, custom views) keep working."""

    class Host:
        def __init__(self, **kwargs):
            super().__init__(**kwargs)

    class V(Host, PresenceMixin):
        pass

    assert V()._presence_tracked is False
