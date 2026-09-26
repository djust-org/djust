"""#3103: get_debug_info() must survive properties that raise, and must not run them.

The legacy (non-explicit) debug panel walked ``dir(self)`` and called
``getattr`` on every name, so a ``@property`` raising anything other than
``AttributeError`` crashed the whole build, and a property querying the
database ran that query on every debug render.
"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from djust import LiveView


class _Boom(LiveView):
    template = "<div dj-root></div>"

    def mount(self, request, **kwargs):
        self.count = 3

    @property
    def raises_value_error(self):
        raise ValueError("not ready")

    @property
    def user_count(self):
        from django.contrib.auth import get_user_model

        return get_user_model().objects.count()


def _view():
    view = _Boom()
    view.mount(None)
    return view


def test_raising_property_does_not_crash_debug_info():
    info = _view().get_debug_info()
    assert info["variables"]["count"]["value"] == "3"
    entry = info["variables"]["raises_value_error"]
    assert entry["type"] == "property"
    assert "not evaluated" in entry["value"]


@pytest.mark.django_db
def test_database_property_is_not_evaluated():
    view = _view()
    with CaptureQueriesContext(connection) as queries:
        info = view.get_debug_info()
    assert len(queries) == 0
    assert info["variables"]["user_count"]["type"] == "property"
    assert info["variables"]["count"]["value"] == "3"


def test_non_property_descriptor_raising_is_reported_unavailable():
    class _Raising:
        def __get__(self, obj, objtype=None):
            if obj is None:
                return self
            raise LookupError("backend down")

    class _DescriptorView(_Boom):
        flaky = _Raising()

    view = _DescriptorView()
    view.mount(None)
    info = view.get_debug_info()
    assert info["variables"]["flaky"]["type"] == "unavailable"
    assert "LookupError" in info["variables"]["flaky"]["value"]
    assert info["variables"]["count"]["value"] == "3"


@pytest.mark.django_db
def test_debug_update_parallel_path_also_skips_properties():
    view = _view()
    with CaptureQueriesContext(connection) as queries:
        update = view.get_debug_update()
    assert len(queries) == 0
    assert update["variables"]["raises_value_error"]["type"] == "property"
    assert update["variables"]["user_count"]["type"] == "property"
    assert update["variables"]["count"]["value"] == "3"
