"""
Unit tests for JIT short-circuit behavior in ContextMixin.get_context_data().

When a view's context contains only plain Python values (int, str, list of
primitives, etc.) the JIT serialization pipeline should be skipped entirely,
avoiding the cost of _get_template_content() and downstream codegen.
"""

import pytest
from unittest.mock import patch

from djust.live_view import LiveView
import djust.mixins.context as context_module


class TestJITShortCircuitNonDB:
    """Non-DB views should skip the JIT pipeline completely."""

    def test_non_db_context_skips_jit(self):
        """Context with only int/str values: _jit_serialized_keys is empty
        and _get_template_content is NOT called."""

        class PlainView(LiveView):
            template = "<div>{{ greeting }} {{ count }}</div>"
            greeting = "hello"
            count = 42

        view = PlainView()

        with patch.object(context_module, "JIT_AVAILABLE", True), patch.object(
            view, "_get_template_content", wraps=view._get_template_content
        ) as mock_gtc:
            ctx = view.get_context_data()

        mock_gtc.assert_not_called()
        assert view._jit_serialized_keys == set()
        assert ctx["greeting"] == "hello"
        assert ctx["count"] == 42

    def test_non_db_context_with_list_of_primitives(self):
        """A list of plain dicts (not Models) should also skip JIT."""

        class DictListView(LiveView):
            template = "<ul>{% for item in items %}<li>{{ item }}</li>{% endfor %}</ul>"
            items = [{"name": "a"}, {"name": "b"}]

        view = DictListView()

        with patch.object(context_module, "JIT_AVAILABLE", True), patch.object(
            view, "_get_template_content", wraps=view._get_template_content
        ) as mock_gtc:
            ctx = view.get_context_data()

        mock_gtc.assert_not_called()
        assert view._jit_serialized_keys == set()
        assert ctx["items"] == [{"name": "a"}, {"name": "b"}]


class TestJITShortCircuitWithDB:
    """Views with DB objects in context should still run JIT."""

    @pytest.mark.django_db
    def test_queryset_triggers_jit(self):
        """A QuerySet in context should cause _get_template_content to be called."""
        from django.contrib.auth.models import User

        qs = User.objects.none()

        class DBView(LiveView):
            template = "<div>{% for u in users %}{{ u.username }}{% endfor %}</div>"
            users = qs

        view = DBView()

        with patch.object(context_module, "JIT_AVAILABLE", True), patch.object(
            view, "_get_template_content", return_value=view.template
        ) as mock_gtc:
            view.get_context_data()

        mock_gtc.assert_called_once()

    @pytest.mark.django_db
    def test_model_instance_triggers_jit(self):
        """A Model instance in context should cause _get_template_content to be called."""
        from django.contrib.auth.models import User

        user = User(username="testuser", pk=1)

        class ModelView(LiveView):
            template = "<div>{{ profile.username }}</div>"

        view = ModelView()
        view.profile = user

        with patch.object(context_module, "JIT_AVAILABLE", True), patch.object(
            view, "_get_template_content", return_value=view.template
        ) as mock_gtc:
            view.get_context_data()

        mock_gtc.assert_called_once()

    @pytest.mark.django_db
    def test_list_of_models_triggers_jit(self):
        """A list whose first element is a Model should trigger JIT."""
        from django.contrib.auth.models import User

        users_list = [User(username="a", pk=1), User(username="b", pk=2)]

        class ListModelView(LiveView):
            template = "<div>{% for u in users %}{{ u.username }}{% endfor %}</div>"

        view = ListModelView()
        view.users = users_list

        with patch.object(context_module, "JIT_AVAILABLE", True), patch.object(
            view, "_get_template_content", return_value=view.template
        ) as mock_gtc:
            view.get_context_data()

        mock_gtc.assert_called_once()


class TestJITShortCircuitEdgeCases:
    """Edge cases for the short-circuit logic."""

    def test_empty_context(self):
        """A view with no public attributes should short-circuit cleanly."""

        class EmptyView(LiveView):
            template = "<div>empty</div>"

        view = EmptyView()

        with patch.object(context_module, "JIT_AVAILABLE", True), patch.object(
            view, "_get_template_content", wraps=view._get_template_content
        ) as mock_gtc:
            view.get_context_data()

        mock_gtc.assert_not_called()
        assert view._jit_serialized_keys == set()

    def test_none_and_bool_values_skip_jit(self):
        """None, bool, float values should not trigger JIT."""

        class MixedView(LiveView):
            template = "<div>{{ flag }} {{ rate }}</div>"
            flag = True
            rate = 3.14
            label = None

        view = MixedView()

        with patch.object(context_module, "JIT_AVAILABLE", True), patch.object(
            view, "_get_template_content", wraps=view._get_template_content
        ) as mock_gtc:
            view.get_context_data()

        mock_gtc.assert_not_called()
        assert view._jit_serialized_keys == set()

    def test_empty_list_skips_jit(self):
        """An empty list should not trigger JIT (no first element to check)."""

        class EmptyListView(LiveView):
            template = "<div>{% for x in items %}{{ x }}{% endfor %}</div>"
            items = []

        view = EmptyListView()

        with patch.object(context_module, "JIT_AVAILABLE", True), patch.object(
            view, "_get_template_content", wraps=view._get_template_content
        ) as mock_gtc:
            view.get_context_data()

        mock_gtc.assert_not_called()
        assert view._jit_serialized_keys == set()
