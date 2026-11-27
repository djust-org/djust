"""V030 also reports a plain value stored under a LiveView method or read-only property (#4360).

A view that assigned ``self._pending = []`` hid ``CommandsMixin._pending()``:
``assert_push_event`` reported nothing queued and the 1.x
``_pending_push_events`` property raised ``TypeError``, with no error at the
assignment. ``manage.py check`` now names the assignment.
"""

from djust import LiveView
from djust.checks.components import _framework_view_members, check_shadowed_view_methods
from djust.decorators import event_handler


class PendingListView(LiveView):
    template = "<div dj-root></div>"

    def mount(self, request, **kwargs):
        self._pending = []  # the app's own list: hides CommandsMixin._pending()


class ClassBodyView(LiveView):
    template = "<div dj-root></div>"

    _has_pending = False  # a plain class attribute over a framework method


class SettablePropertyView(LiveView):
    template = "<div dj-root></div>"

    def mount(self, request, **kwargs):
        # A property with a setter is assignable: not reported.
        self._pending_push_events = []
        self._waiting = []


class OverridingView(LiveView):
    template = "<div dj-root></div>"

    def get_context_data(self, **kwargs):  # an override, not a shadow
        return super().get_context_data(**kwargs)

    @event_handler()
    def bump(self, **kwargs):
        self.push_event("bumped", {})


def _v030(cls):
    label = "%s.%s" % (cls.__module__, cls.__qualname__)
    return [m for m in check_shadowed_view_methods(None) if m.msg.startswith(label + " ")]


def test_framework_members_include_the_effect_queue_and_skip_settable_properties():
    members = _framework_view_members()
    assert members["_pending"] == "method"
    assert members["_has_pending"] == "method"
    assert "_pending_push_events" not in members  # has a setter


def test_v030_reports_a_mount_assignment_over_a_framework_method():
    (message,) = _v030(PendingListView)
    assert message.id == "djust.V030"
    assert "mount() assigns self._pending" in message.msg
    assert "djust's method _pending()" in message.msg
    assert message.line_number is not None
    assert "#4360" in message.hint


def test_v030_reports_a_plain_class_attribute_over_a_framework_method():
    (message,) = _v030(ClassBodyView)
    assert "sets _has_pending in its class body" in message.msg


def test_v030_is_silent_for_overrides_settable_properties_and_new_names():
    assert _v030(SettablePropertyView) == []
    assert _v030(OverridingView) == []
