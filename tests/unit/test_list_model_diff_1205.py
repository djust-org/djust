"""Regression: ``list[Model]`` in user-overridden ``get_context_data`` must
produce VDOM patches when model fields mutate (#1205).

The bug
-------

When a view's ``get_context_data`` override sets ``ctx["tasks"] = list(qs)``
*after* calling ``super().get_context_data()``, the JIT auto-serialization
pipeline runs inside ``super()`` and never sees the user-added ``tasks``.
The list of raw ``Model`` instances flows through ``_sync_state_to_rust``,
where change-detection compares ``list[Model]`` via Python ``==``. ``==`` on
lists delegates to per-element ``Model.__eq__``, which only compares by
``pk``. In-place field mutations (``is_active`` toggling, ``completed``
flipping, etc.) don't change ``pk``, so the list compares as "unchanged"
and Rust never receives the new state. Result: zero VDOM patches, template
renders identically forever.

The fix (in ``rust_bridge.py``) is a defensive normalize pass: any
``list[Model]`` / ``Model`` / ``QuerySet`` value that snuck past the JIT
pipeline gets serialized to ``list[dict]`` / ``dict`` via
``normalize_django_value`` before change-detection runs.

These tests lock down the bug so it can't silently regress.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User

from djust.decorators import event_handler
from djust.live_view import LiveView
from djust.testing import LiveViewTestClient


# ---------------------------------------------------------------------------
# Primary regression: list(qs) in user override propagates field mutations
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestListModelDiff1205:
    def test_user_override_with_list_qs_propagates_field_mutations(self):
        """``ctx['tasks'] = list(qs)`` after ``super()`` must trigger a real
        re-render when a model field mutates."""

        class TaskView(LiveView):
            template = (
                "<div>"
                "{% for task in tasks %}"
                "<li data-pk='{{ task.id }}'>"
                "{% if task.is_active %}A{% else %}I{% endif %}{{ task.username }}"
                "</li>"
                "{% endfor %}"
                "</div>"
            )

            def mount(self, request, **kwargs):
                self._users = [
                    User(username="alice", pk=1, is_active=True),
                    User(username="bob", pk=2, is_active=False),
                ]

            @event_handler
            def toggle(self, pk: int = 1, **kwargs):
                for u in self._users:
                    if u.pk == pk:
                        u.is_active = not u.is_active

            def get_context_data(self, **kwargs):
                ctx = super().get_context_data(**kwargs)
                ctx["tasks"] = list(self._users)  # the bug pattern
                return ctx

        client = LiveViewTestClient(TaskView).mount()
        html_before = client.render()
        assert "Aalice" in html_before, html_before
        assert "Ibob" in html_before, html_before

        client.send_event("toggle", pk=1)
        html_after = client.render()

        # The actual bug: html_after used to be byte-identical to html_before
        # because change-detection missed the in-place field mutation.
        assert html_before != html_after, (
            "Re-render after field mutation produced identical HTML — change "
            "detection missed the list[Model] in-place mutation. See #1205."
        )
        assert "Ialice" in html_after, html_after
        assert "Ibob" in html_after, html_after

    def test_raw_queryset_in_user_override_still_works(self):
        """Sanity: ``ctx['users'] = qs`` (raw, unmaterialized QuerySet) added in
        the override must also flow through the normalize pass and propagate
        DB-level field changes. Locks the QuerySet branch of the fix in
        (``isinstance(_val, QuerySet)`` arm at rust_bridge.py)."""
        # Create real users in the test DB so the QuerySet is non-empty.
        User.objects.create(username="alice", is_active=True)
        User.objects.create(username="bob", is_active=False)

        class UserListView(LiveView):
            template = (
                "<div>"
                "{% for u in users %}"
                "<li>{% if u.is_active %}A{% else %}I{% endif %}{{ u.username }}</li>"
                "{% endfor %}"
                "</div>"
            )

            @event_handler
            def deactivate(self, username: str = "", **kwargs):
                User.objects.filter(username=username).update(is_active=False)

            def get_context_data(self, **kwargs):
                ctx = super().get_context_data(**kwargs)
                # Raw QuerySet (re-evaluated each call). The fix must not break
                # this path — the normalize pass converts it to list[dict].
                ctx["users"] = User.objects.order_by("username")
                return ctx

        client = LiveViewTestClient(UserListView).mount()
        html_before = client.render()
        assert "Aalice" in html_before, html_before

        client.send_event("deactivate", username="alice")
        html_after = client.render()
        assert html_before != html_after
        assert "Ialice" in html_after, html_after

    def test_single_model_in_user_override(self):
        """``ctx['profile'] = single_model`` (Model, not list) added in override
        must also propagate field mutations."""

        class ProfileView(LiveView):
            template = (
                "<div>{% if profile.is_active %}A{% else %}I{% endif %}{{ profile.username }}</div>"
            )

            def mount(self, request, **kwargs):
                self._user = User(username="alice", pk=1, is_active=True)

            @event_handler
            def toggle(self, **kwargs):
                self._user.is_active = not self._user.is_active

            def get_context_data(self, **kwargs):
                ctx = super().get_context_data(**kwargs)
                ctx["profile"] = self._user
                return ctx

        client = LiveViewTestClient(ProfileView).mount()
        html_before = client.render()
        assert "Aalice" in html_before, html_before

        client.send_event("toggle")
        html_after = client.render()
        assert html_before != html_after
        assert "Ialice" in html_after, html_after

    def test_normalize_idempotent_on_already_serialized(self):
        """If JIT already serialized a value to ``list[dict]``, the normalize
        pass must NOT double-process. ``is_model_list`` returns False for
        ``list[dict]`` (first item isn't a Model), and ``isinstance(dict,
        Model)`` is False — so the pass is a no-op for already-serialized
        values."""

        class JITView(LiveView):
            template = "<div>{% for t in tasks %}{{ t.username }}{% endfor %}</div>"
            tasks = [
                User(username="alice", pk=1, is_active=True),
                User(username="bob", pk=2, is_active=False),
            ]  # class-level list[Model] — JIT WILL process this

            @event_handler
            def noop(self, **kwargs):
                pass

        client = LiveViewTestClient(JITView).mount()
        # First render establishes baseline. Should not throw on the
        # normalize pass (idempotent on serialized dicts).
        html = client.render()
        assert "alice" in html and "bob" in html

        # Second render — context unchanged, no field mutation.
        client.send_event("noop")
        html2 = client.render()
        # No change, but render must succeed (idempotency check).
        assert "alice" in html2

    def test_empty_list_normalize_safe(self):
        """An empty list (not list[Model]) must not trip the normalize pass."""

        class EmptyView(LiveView):
            template = "<div>{% for t in tasks %}{{ t }}{% endfor %}empty</div>"

            def mount(self, request, **kwargs):
                self._tasks = []

            def get_context_data(self, **kwargs):
                ctx = super().get_context_data(**kwargs)
                ctx["tasks"] = list(self._tasks)
                return ctx

        client = LiveViewTestClient(EmptyView).mount()
        html = client.render()
        assert "empty" in html

    def test_mixed_list_with_model_first(self):
        """``[Model, dict, Model]`` — ``is_model_list`` triggers if first is a
        Model. The normalize loop must handle each item independently via
        ``normalize_django_value(item)`` (which is a no-op on plain dicts)."""

        class MixedView(LiveView):
            template = "<div>{% for t in items %}|{{ t }}{% endfor %}</div>"

            def mount(self, request, **kwargs):
                self._items = [
                    User(username="alice", pk=1, is_active=True),
                    {"name": "plain dict"},
                ]

            def get_context_data(self, **kwargs):
                ctx = super().get_context_data(**kwargs)
                ctx["items"] = list(self._items)
                return ctx

        client = LiveViewTestClient(MixedView).mount()
        # Should not crash; rendering the dict via {{ t }} produces its repr/str.
        html = client.render()
        assert "alice" in html or "plain dict" in html


# ---------------------------------------------------------------------------
# Dead code: _lazy_serialize_context must be gone
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestLazySerializeContextRemoved:
    """``_lazy_serialize_context`` was dead code (zero call sites) that the
    issue reporter mistook for the bug location. It contained a
    ``str(model_instance)`` fallback that, if ever wired up, would produce
    exactly the symptom reported. Removing it prevents future confusion."""

    def test_lazy_serialize_context_does_not_exist(self):
        """The dead method is removed from JITMixin."""
        from djust.mixins.jit import JITMixin

        assert not hasattr(JITMixin, "_lazy_serialize_context"), (
            "_lazy_serialize_context should be removed (#1205 retro)"
        )
