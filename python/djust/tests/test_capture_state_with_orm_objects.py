"""``_capture_snapshot_state`` and the render path with a model on public state.

``test_state_snapshot_orm_early_validation`` was about the ``strict=`` capture
of the legacy signed snapshot, removed in 2.0. Three of its cases pin code
that stays, and are kept here unchanged (the view no longer sets
``enable_state_snapshot``, which none of them read):

* the time-travel capture (``_capture_snapshot_state()``, no arguments) does
  not raise in DEBUG and keeps a model as a plain dict;
* the render path is untouched: ``_is_serializable`` accepts a model, and
  ``get_state()`` hands back the live instance.
"""

from __future__ import annotations

from django.test import override_settings

from djust import LiveView


def _make_user(*, username="alice", pk=7):
    from django.contrib.auth.models import User

    user = User(username=username, email=f"{username}@example.com")
    user.pk = pk
    user.id = pk
    return user


class _PublicOrmStateView(LiveView):
    """A legacy view storing a Model instance on public state."""

    exposure_policy = "legacy"
    template_name = "test.html"

    def mount(self, request, **kwargs):
        self.user = _make_user()
        self.label = "safe-scalar"


class TestTimeTravelCaptureUnaffected:
    @override_settings(DEBUG=True)
    def test_default_call_does_not_raise_even_in_debug(self):
        view = _PublicOrmStateView()
        view.mount(None)

        state = view._capture_snapshot_state()

        assert "user" in state
        assert isinstance(state["user"], dict)


class TestRenderingJitPipelineUnaffected:
    @override_settings(DEBUG=True)
    def test_is_serializable_still_true_for_model(self):
        assert LiveView._is_serializable(_make_user()) is True

    @override_settings(DEBUG=True)
    def test_get_state_still_returns_raw_model_for_rendering(self):
        view = _PublicOrmStateView()
        view.mount(None)

        state = view.get_state()
        assert state["user"] is view.user
