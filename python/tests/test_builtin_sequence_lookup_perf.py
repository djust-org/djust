"""Numeric sequence lookup must retain Django's custom-object semantics."""

import pytest
from django.template import Context, Engine

from djust import _rust
from djust.template.backend import DjustTemplateBackend


class Holder:
    # Force the live-object lookup path instead of a serialized dictionary.
    __slots__ = ("sequence",)

    def __init__(self, sequence):
        self.sequence = sequence


class NumericAttributeList(list):
    def __getattr__(self, name):
        if name == "0":
            return "attribute wins"
        raise AttributeError(name)


class NumericDescriptorMixin:
    def __dir__(self):
        return [*super().__dir__(), "0"]

    def __getattr__(self, name):
        if name == "0":
            raise AttributeError("numeric descriptor failed")
        raise AttributeError(name)


class NumericDescriptorList(NumericDescriptorMixin, list):
    pass


class NumericDescriptorTuple(NumericDescriptorMixin, tuple):
    pass


class NumericDescriptorString(NumericDescriptorMixin, str):
    pass


class NumericItemTuple(tuple):
    def __getitem__(self, key):
        if key == "0":
            return "string item wins"
        return super().__getitem__(key)


class NumericAttributeString(str):
    def __getattr__(self, name):
        if name == "0":
            return "string attribute wins"
        raise AttributeError(name)


def render(mode, source, context):
    if mode == "direct":
        return _rust.render_template(source, context)
    if mode == "backend":
        backend = DjustTemplateBackend(
            {"NAME": "djust", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}}
        )
        return backend.from_string(source).render(context)
    view = _rust.RustLiveView(source)
    for key, value in context.items():
        view.set_state(key, value)
    return view.render()


@pytest.mark.parametrize("mode", ["direct", "backend", "view"])
@pytest.mark.parametrize("sequence", [list("a<&"), tuple("a<&"), "a<&", [], (), ""])
@pytest.mark.parametrize("index", ["0", "1", "2", "00", "99", "9999999999999999999999999999"])
def test_exact_sequence_index_matches_django(mode, sequence, index):
    source = "{{ obj.sequence." + index + '|default:"missing" }}'
    context = {"obj": Holder(sequence)}
    expected = Engine().from_string(source).render(Context(context))
    assert render(mode, source, context) == expected


@pytest.mark.parametrize("mode", ["direct", "backend", "view"])
@pytest.mark.parametrize(
    "sequence",
    [NumericAttributeList(["item"]), NumericItemTuple(["item"]), NumericAttributeString("item")],
)
def test_subclass_lookup_order_matches_django(mode, sequence):
    source = "{{ obj.sequence.0 }}"
    context = {"obj": Holder(sequence)}
    expected = Engine().from_string(source).render(Context(context))
    assert render(mode, source, context) == expected


@pytest.mark.parametrize("mode", ["direct", "backend", "view"])
@pytest.mark.parametrize(
    "sequence_type", [NumericDescriptorList, NumericDescriptorTuple, NumericDescriptorString]
)
def test_subclass_dir_keeps_descriptor_failure(mode, sequence_type):
    source = "{{ obj.sequence.0 }}"
    context = {"obj": Holder(sequence_type("item"))}
    with pytest.raises(AttributeError, match="numeric descriptor failed"):
        Engine().from_string(source).render(Context(context))
    # The Rust boundary wraps Python lookup exceptions as RuntimeError.
    with pytest.raises((RuntimeError, AttributeError), match="numeric descriptor failed"):
        render(mode, source, context)


@pytest.mark.parametrize("mode", ["direct", "backend", "view"])
def test_sequence_attributes_and_mutations_remain_visible(mode):
    sequence = ["first"]
    context = {"obj": Holder(sequence)}
    source = "{{ obj.sequence.0 }}:{{ obj.sequence.count }}"
    for value in ["first", "<changed>"]:
        sequence[0] = value
        expected = Engine().from_string(source).render(Context(context))
        assert render(mode, source, context) == expected
