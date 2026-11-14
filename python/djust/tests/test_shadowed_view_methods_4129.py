"""V030: a view attribute that replaces one of djust's #4129 view methods.

A view that defines ``skip_render`` (say, as an event handler) replaces the
framework method: the client can call it, and the view's own
``self.skip_render()`` runs the handler. ``manage.py check`` reports it.
"""

from djust import LiveView
from djust.checks.components import _PUBLIC_VIEW_METHODS, check_shadowed_view_methods
from djust.decorators import event_handler


class _Flags:
    def tidy(self):
        self.reset_submitted_form = True  # an instance attribute of that name


class ShadowingView(_Flags, LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root></div>"

    @event_handler()
    def skip_render(self, **kwargs):
        pass

    clear_push_events = None


class CleanView(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root></div>"

    @event_handler()
    def save(self, **kwargs):
        self.reset_submitted_form()
        self.skip_render()


def _v030(*classes):
    labels = ["%s.%s" % (c.__module__, c.__qualname__) for c in classes]
    return [
        m
        for m in check_shadowed_view_methods(None)
        if any(m.msg.startswith(label) for label in labels)
    ]


def test_v030_reports_each_shadowing_name():
    messages = _v030(ShadowingView)
    assert {m.id for m in messages} == {"djust.V030"}
    named = sorted(m.msg.split("replaces djust's ")[1].split("()")[0] for m in messages)
    assert named == ["clear_push_events", "reset_submitted_form", "skip_render"]
    mixin = [m for m in messages if "reset_submitted_form" in m.msg][0]
    assert "(in _Flags)" in mixin.msg and "tidy() assigns self.reset_submitted_form" in mixin.msg


def test_v030_is_silent_for_a_view_that_calls_the_methods():
    assert _v030(CleanView) == []


def test_v030_covers_every_new_public_name():
    from djust.mixins.push_events import PushEventMixin
    from djust.mixins.rust_bridge import RustBridgeMixin
    from djust.theming import ThemeMixin

    for name in _PUBLIC_VIEW_METHODS:
        assert any(name in vars(c) for c in (RustBridgeMixin, PushEventMixin, ThemeMixin)), name


def test_v030_can_be_suppressed(settings):
    settings.DJUST_CONFIG = {**getattr(settings, "DJUST_CONFIG", {}), "suppress_checks": ["V030"]}
    from djust.config import config

    config.reset()
    try:
        assert _v030(ShadowingView) == []
    finally:
        config.reset()
