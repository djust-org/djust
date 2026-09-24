"""v1.2.1-7 — LiveView state: dirty tracking, snapshots, private session, forms.

- #2956: ``is_dirty`` / ``changed_fields`` must see ``state()`` fields, which
  live in per-instance ``_state_<name>`` slots.
- #2912: they must also see a class-level component's State, which lives in a
  ``_component_<name>`` slot.
- #2896: the signed back-navigation snapshot captures ``__components__``;
  ``_restore_snapshot`` must apply it, to registered components only.
- #2959: a legacy view must not save ``state()`` backing slots or
  ``_reactive_state`` in the private session; the public-state restore already
  carries ``state()`` values through the descriptor.
- #2974: ``FormMixin.reset_form`` is an ``@event_handler``, and a
  ``reset_form()`` inside ``form_valid`` is not undone by ``_sync_form_data``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from django import forms
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory

from djust.components.descriptors.base import LiveComponent, TypedState
from djust.decorators import event_handler, is_event_handler, state
from djust.forms import FormMixin
from djust.live_view import LiveView


class Counter(LiveComponent):
    class State(TypedState):
        n: int = 0


class StateFieldView(LiveView):
    template = "<div dj-root>{{ count }} {{ label }}</div>"

    count = state(1)

    def mount(self, request: Any, **kwargs: Any) -> None:
        self.label = "x"


class ComponentView(LiveView):
    template = "<div dj-root>{{ counter }}</div>"

    counter = Counter()

    def mount(self, request: Any, **kwargs: Any) -> None:
        self.label = "x"


# ─────────────────────────────────────────────────────────────────────────────
# #2956 — dirty tracking sees state() fields
# ─────────────────────────────────────────────────────────────────────────────


class TestDirtyStateFields2956:
    def test_state_field_change_is_dirty(self):
        v = StateFieldView()
        v.mount(None)
        v._capture_dirty_baseline()
        assert v.is_dirty is False
        v.count = 2
        assert v.changed_fields == {"count"}
        assert v.is_dirty is True

    def test_state_field_default_then_first_assignment(self):
        """The baseline was taken while the slot was still unset (default);
        the first assignment of a different value is a change."""
        v = StateFieldView()
        v._capture_dirty_baseline()
        v.count = 5
        assert v.changed_fields == {"count"}

    def test_state_field_same_value_write_is_clean(self):
        v = StateFieldView()
        v.mount(None)
        v.count = 3
        v._capture_dirty_baseline()
        v.count = 3
        assert v.is_dirty is False

    def test_mark_clean_resets_state_field(self):
        v = StateFieldView()
        v.mount(None)
        v._capture_dirty_baseline()
        v.count = 9
        v.mark_clean()
        assert v.is_dirty is False

    def test_private_state_field_stays_invisible(self):
        class V(LiveView):
            template = "<div dj-root></div>"
            _hidden = state(0)

        v = V()
        v._capture_dirty_baseline()
        v._hidden = 4
        assert v.changed_fields == set()

    def test_static_assign_state_field_is_skipped(self):
        class V(LiveView):
            template = "<div dj-root></div>"
            static_assigns = ["count"]
            count = state(0)

        v = V()
        v._capture_dirty_baseline()
        v.count = 4
        assert v.is_dirty is False


# ─────────────────────────────────────────────────────────────────────────────
# #2912 — dirty tracking sees a class-level component's State
# ─────────────────────────────────────────────────────────────────────────────


class TestDirtyClassLevelComponent2912:
    def test_state_mutation_flips_is_dirty(self):
        v = ComponentView()
        v.mount(None)
        v._capture_dirty_baseline()
        assert v.is_dirty is False
        v.counter.n = 1
        assert v.changed_fields == {"counter"}
        assert v.is_dirty is True

    def test_mutation_before_first_access_is_seen(self):
        """The baseline materialises the component, so a mutation made after it
        (the first access) still differs from the default State."""
        v = ComponentView()
        v._capture_dirty_baseline()
        v.counter.n = 7
        assert v.is_dirty is True

    def test_same_value_write_is_clean(self):
        v = ComponentView()
        v.mount(None)
        v.counter.n = 2
        v._capture_dirty_baseline()
        v.counter.n = 2
        assert v.is_dirty is False

    def test_untouched_component_is_clean_across_render(self):
        v = ComponentView()
        v.mount(None)
        v._capture_dirty_baseline()
        v.get_context_data()  # renders read the component; no state change
        assert v.is_dirty is False


# ─────────────────────────────────────────────────────────────────────────────
# #2896 — _restore_snapshot applies __components__
# ─────────────────────────────────────────────────────────────────────────────


class TestSnapshotRestoresComponents2896:
    def test_class_level_component_round_trips(self):
        src = ComponentView()
        src.mount(None)
        src.counter.n = 5
        snap = json.loads(json.dumps(src._capture_snapshot_state(strict=True)))
        assert snap["__components__"]["counter"]["n"] == 5

        # A fresh view restored from the snapshot, mount() skipped (the
        # runtime's restore path): the component has not been accessed yet.
        dst = ComponentView()
        dst._restore_snapshot(snap)
        assert dst.counter.n == 5
        assert dst.label == "x"

    def test_unknown_component_id_is_ignored(self):
        dst = ComponentView()
        dst._restore_snapshot({"__components__": {"nope": {"n": 1}, "counter": {"n": 2}}})
        assert not hasattr(dst, "nope")
        assert "nope" not in dst._components
        assert dst.counter.n == 2

    def test_non_component_attribute_is_not_a_dispatch_target(self):
        """Only registered components / declared component descriptors are
        targets — never an arbitrary attribute named like a component id."""
        dst = ComponentView()
        dst.label = "keep"
        dst._restore_snapshot({"__components__": {"label": {"upper": "x"}}})
        assert dst.label == "keep"

    def test_private_and_dunder_component_keys_are_blocked(self):
        dst = ComponentView()
        dst._restore_snapshot({"__components__": {"counter": {"_secret": 1, "__class__": 2}}})
        assert "_secret" not in dst.counter.state
        assert dst.counter.n == 0

    def test_malformed_components_value_is_ignored(self):
        dst = ComponentView()
        dst._restore_snapshot({"__components__": ["counter"], "label": "y"})
        dst._restore_snapshot({"__components__": {"counter": "n=3"}})
        assert dst.label == "y"
        assert dst.counter.n == 0

    def test_instance_component_registered_in_registry_is_restored(self):
        from djust.components.base import LiveComponent as InstanceComponent

        class Widget(InstanceComponent):
            template = "<i>{{ n }}</i>"

            def mount(self, **kwargs: Any) -> None:
                self.n = 0

        class V(LiveView):
            template = "<div dj-root>{{ w }}</div>"

        dst = V()
        w = Widget(component_id="w1")
        dst._components["w1"] = w
        dst._restore_snapshot({"__components__": {"w1": {"n": 4, "component_id": "evil"}}})
        assert w.n == 4
        assert w.component_id == "w1"


# ─────────────────────────────────────────────────────────────────────────────
# #2959 — private session holds no state() backing slots / _reactive_state
# ─────────────────────────────────────────────────────────────────────────────


def _session_request(method: str = "get", body: Any = None, session: Any = None):
    rf = RequestFactory()
    if method == "get":
        request = rf.get("/state-2959/")
    else:
        request = rf.post("/state-2959/", data=json.dumps(body), content_type="application/json")
    SessionMiddleware(lambda r: None).process_request(request)
    if session is not None:
        request.session = session
    return request


class LevelView(LiveView):
    template = "<div dj-root>{{ level }}</div>"

    level = state(1)
    _quiet = state(0)

    def mount(self, request: Any, **kwargs: Any) -> None:
        self.level = 3
        self._quiet = 5
        self._note = "private"

    @event_handler()
    def bump(self, **kwargs: Any) -> None:
        self.level += 1


class TestPrivateStateSlots2959:
    def test_public_state_slot_not_in_private_state(self):
        v = LevelView()
        v.mount(None)
        v._snapshot_user_private_attrs()
        private = v._get_private_state()
        assert "_state_level" not in private
        assert "_reactive_state" not in private
        assert private["_note"] == "private"

    def test_private_state_field_is_still_persisted(self):
        """A ``_``-named state() field never reaches the public context, so its
        slot is the only copy — it stays in the private session."""
        v = LevelView()
        v.mount(None)
        v._snapshot_user_private_attrs()
        assert v._get_private_state()["_state__quiet"] == 5

    def test_old_session_keys_are_restored_but_not_resaved(self):
        v = LevelView()
        v._restore_private_state({"_state_level": 7, "_note": "old"})
        assert v.level == 7
        assert "_state_level" not in v._get_private_state()

    @pytest.mark.django_db
    def test_http_fallback_round_trip_keeps_state_value(self):
        view = LevelView()
        get = _session_request("get")
        view.get(get)
        view_key = "liveview_/state-2959/"
        assert "_state_level" not in get.session.get(f"{view_key}__private", {})
        assert get.session[view_key]["level"] == 3

        post = _session_request("post", {"event": "bump", "params": {}}, session=get.session)
        view2 = LevelView()
        response = view2.post(post)
        assert response.status_code == 200
        assert view2.level == 4
        assert view2._quiet == 5


# ─────────────────────────────────────────────────────────────────────────────
# #2974 — reset_form is a handler and survives form_valid
# ─────────────────────────────────────────────────────────────────────────────


class NameForm(forms.Form):
    name = forms.CharField(max_length=50)
    note = forms.CharField(required=False, initial="hello")


class ResetInValidView(FormMixin, LiveView):
    form_class = NameForm
    template = "<div dj-root>{{ form_data.name }}</div>"

    def form_valid(self, form: Any) -> None:
        self.success_message = "Saved"
        self.reset_form()


class PlainValidView(FormMixin, LiveView):
    form_class = NameForm
    template = "<div dj-root>{{ form_data.name }}</div>"


class TestResetForm2974:
    def test_reset_form_is_an_event_handler(self):
        assert is_event_handler(FormMixin.reset_form)

    @pytest.mark.django_db
    def test_reset_inside_form_valid_clears_form_data(self):
        v = ResetInValidView()
        v.mount(None)
        v.submit_form(name="Ada", note="x")
        assert v.form_data == {"name": "", "note": "hello"}
        assert v._should_reset_form is True

    @pytest.mark.django_db
    def test_form_valid_without_reset_still_syncs(self):
        v = PlainValidView()
        v.mount(None)
        v.submit_form(name="  Ada  ", note="x")
        # _sync_form_data wrote the cleaned value back (CharField strips).
        assert v.form_data["name"] == "Ada"
        assert v._should_reset_form is False

    @pytest.mark.django_db
    def test_pending_reset_signal_survives_a_later_submit(self):
        v = PlainValidView()
        v.mount(None)
        v.reset_form()
        v.submit_form(name="Bo")
        assert v._should_reset_form is True
        assert v.form_data["name"] == "Bo"
