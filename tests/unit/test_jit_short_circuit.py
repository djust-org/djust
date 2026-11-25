"""
Unit tests for the JIT short-circuit in ContextMixin.get_context_data().

The attribute-walking context (the legacy exposure policy, now removed) ran the
JIT serialization pipeline for a Model, QuerySet or list of Models, and skipped
it for plain values. An explicit view's context is its declared ``state()``
fields plus ``get_context_data()``: it never reaches the JIT pipeline, whatever
it holds. Models and QuerySets reach the template through the engine's
floor-enforcing handles instead.
"""

from unittest.mock import patch

import pytest

from djust import state
from djust.live_view import LiveView


def _context_without_jit(view):
    with patch.object(view, "_get_template_content", wraps=view._get_template_content) as gtc:
        ctx = view.get_context_data()
    gtc.assert_not_called()
    assert view._jit_serialized_keys == set()
    return ctx


class TestPlainValues:
    def test_non_db_context_skips_jit(self):
        class PlainView(LiveView):
            exposure_policy = "explicit"
            template = "<div>{{ greeting }} {{ count }}</div>"
            greeting = state("hello")
            count = state(42)

        ctx = _context_without_jit(PlainView())
        assert ctx["greeting"] == "hello"
        assert ctx["count"] == 42

    def test_non_db_context_with_list_of_dicts(self):
        class DictListView(LiveView):
            exposure_policy = "explicit"
            template = "<ul>{% for item in items %}<li>{{ item }}</li>{% endfor %}</ul>"
            items = state(default_factory=lambda: [{"name": "a"}, {"name": "b"}])

        ctx = _context_without_jit(DictListView())
        assert ctx["items"] == [{"name": "a"}, {"name": "b"}]

    def test_empty_context(self):
        class EmptyView(LiveView):
            exposure_policy = "explicit"
            template = "<div>empty</div>"

        _context_without_jit(EmptyView())

    def test_none_bool_and_float_values(self):
        class MixedView(LiveView):
            exposure_policy = "explicit"
            template = "<div>{{ flag }} {{ rate }}</div>"
            flag = state(True)
            rate = state(3.14)
            label = state(None)

        ctx = _context_without_jit(MixedView())
        assert (ctx["flag"], ctx["rate"], ctx["label"]) == (True, 3.14, None)

    def test_empty_list(self):
        class EmptyListView(LiveView):
            exposure_policy = "explicit"
            template = "<div>{% for x in items %}{{ x }}{% endfor %}</div>"
            items = state(default_factory=list)

        ctx = _context_without_jit(EmptyListView())
        assert ctx["items"] == []


@pytest.mark.django_db
class TestDBObjectsSuppliedByGetContextData:
    """A Model, QuerySet or list of Models in the context is not JIT-serialized either."""

    def _view(self, template, **supplied):
        class DBView(LiveView):
            exposure_policy = "explicit"

            def get_context_data(self, **kwargs):
                ctx = super().get_context_data(**kwargs)
                ctx.update({name: make() for name, make in supplied.items()})
                return ctx

        DBView.template = template
        return DBView()

    def test_queryset(self):
        from django.contrib.auth.models import User

        view = self._view(
            "<div>{% for u in users %}{{ u.username }}{% endfor %}</div>",
            users=lambda: User.objects.none(),
        )
        ctx = _context_without_jit(view)
        assert list(ctx["users"]) == []

    def test_model_instance(self):
        from django.contrib.auth.models import User

        view = self._view(
            "<div>{{ profile.username }}</div>",
            profile=lambda: User(username="testuser", pk=1),
        )
        ctx = _context_without_jit(view)
        assert ctx["profile"].username == "testuser"

    def test_list_of_models(self):
        from django.contrib.auth.models import User

        view = self._view(
            "<div>{% for u in users %}{{ u.username }}{% endfor %}</div>",
            users=lambda: [User(username="a", pk=1), User(username="b", pk=2)],
        )
        ctx = _context_without_jit(view)
        assert [u.username for u in ctx["users"]] == ["a", "b"]
