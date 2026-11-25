"""The render path with a model on public state.

``test_state_snapshot_orm_early_validation`` was about the ``strict=`` capture
of the legacy signed snapshot, removed in 2.0. The cases that pin code that
stays are kept here (the time-travel capture case went with time-travel
debugging, #4184): the render path is untouched: ``_is_serializable`` accepts
a model, and the context an explicit view supplies renders the fields of that live instance the template reads.
"""

from __future__ import annotations

from django.test import override_settings

from djust import LiveView
from djust.testing import rendered_state


def _make_user(*, username="alice", pk=7):
    from django.contrib.auth.models import User

    user = User(username=username, email=f"{username}@example.com")
    user.pk = pk
    user.id = pk
    return user


class _PublicOrmStateView(LiveView):
    """An explicit view supplying a Model instance to its template."""

    exposure_policy = "explicit"
    template = "<div dj-root>{{ user.username }} {{ label }}</div>"

    def mount(self, request, **kwargs):
        self._user = _make_user()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["user"] = self._user
        context["label"] = "safe-scalar"
        return context


class TestRenderingJitPipelineUnaffected:
    @override_settings(DEBUG=True)
    def test_is_serializable_still_true_for_model(self):
        assert LiveView._is_serializable(_make_user()) is True

    @override_settings(DEBUG=True)
    def test_rendered_state_renders_the_models_fields_the_template_reads(self):
        view = _PublicOrmStateView()
        view.mount(None)
        view.render_with_diff()

        state = rendered_state(view)
        # The template reads one attribute of the model: that is what renders.
        assert state["user"] == {"username": "alice"}
        assert state["label"] == "safe-scalar"
