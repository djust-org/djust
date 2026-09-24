"""Executable ADR-038 prerequisite: typed, instance-owned state defaults."""

import pytest

from djust.decorators import state


def test_nested_mutable_defaults_are_owned_by_each_instance():
    default = {"items": []}

    class View:
        payload = state(default)

    first, second = View(), View()
    first.payload["items"].append("first")
    assert first.payload == {"items": ["first"]}
    assert second.payload == default == {"items": []}
    assert first.payload is first.payload


def test_factory_is_lazy_and_runs_once_per_instance():
    calls = []

    def factory():
        calls.append(True)
        return []

    class View:
        items = state(default_factory=factory)

    first, second = View(), View()
    assert calls == []
    assert first.items == []
    first.items.append(1)
    assert first.items == [1]
    assert second.items == []
    assert len(calls) == 2


def test_assignment_does_not_evaluate_factory_and_marks_reactivity():
    def factory():
        raise AssertionError("assigned values must not evaluate defaults")

    class View:
        count = state(default_factory=factory)

    view = View()
    view.count = 3
    assert view.count == 3
    assert view._reactive_state == {"count"}


def test_inherited_defaults_are_independent_and_overridable():
    class Parent:
        items = state(default_factory=list)

    class Child(Parent):
        pass

    class Override(Parent):
        items = state(["override"])

    child = Child()
    child.items.append("child")
    assert Parent().items == Child().items == []
    assert Override().items == ["override"]


def test_none_and_omitted_default_remain_supported():
    class View:
        omitted = state()
        explicit = state(None)

    assert View().omitted is View().explicit is None


def test_factory_and_default_are_mutually_exclusive():
    with pytest.raises(TypeError, match="default.*default_factory"):
        state(None, default_factory=list)


def test_non_callable_factory_is_rejected():
    with pytest.raises(TypeError, match="callable"):
        state(default_factory=[])


def test_descriptor_cannot_be_reused_under_another_name():
    field = state(0)
    with pytest.raises((TypeError, RuntimeError)):

        class View:
            first = field
            second = field


def test_liveview_context_observes_nested_mutation_without_shared_defaults():
    from djust import LiveView
    from djust.change_detection import deep_fingerprint

    class View(LiveView):
        payload = state({"items": []})

    view = View()
    before = deep_fingerprint(view.get_context_data()["payload"])
    view.payload["items"].append("changed")
    after = deep_fingerprint(view.get_context_data()["payload"])
    assert before != after
    assert View().get_context_data()["payload"] == {"items": []}
