"""Materializing lazy library bindings preserves named tuple construction."""

from collections import namedtuple
from typing import NamedTuple

import pytest
from django.template.defaulttags import GroupedResult
from django.utils.functional import lazy
from djust.template_libraries import _materialize_lazy

Group = namedtuple("Group", "grouper list")


class TypedGroup(NamedTuple):
    grouper: str
    items: list


@pytest.mark.parametrize("group_type", [Group, TypedGroup, GroupedResult])
def test_lazy_namedtuple_values_keep_fields_and_type(group_type):
    label = lazy(lambda: "label", str)()
    value = group_type(label, [label])
    actual = _materialize_lazy({"groups": [value]})["groups"][0]
    assert type(actual) is group_type
    assert actual == group_type("label", ["label"])
    assert type(actual[0]) is str
    assert type(actual[1][0]) is str
    assert actual.grouper == "label"


def test_plain_tuple_and_nested_namedtuple_keep_their_shapes():
    label = lazy(lambda: "nested", str)()
    value = (Group(label, [Group(label, [])]),)
    actual = _materialize_lazy(value)
    assert type(actual) is tuple
    assert type(actual[0]) is Group
    assert type(actual[0].list[0]) is Group
    assert actual == (Group("nested", [Group("nested", [])]),)


@pytest.mark.parametrize("value", [None, True, 23, -0.5, "<unsafe>é", b"bytes"])
def test_plain_leaves_cross_unchanged(value):
    assert _materialize_lazy(value) is value


def test_lazy_safety_errors_and_container_copying():
    from django.utils.safestring import SafeString, mark_safe

    safe = lazy(lambda: mark_safe("<b>safe</b>"), str)()
    plain = lazy(lambda: "<unsafe>", str)()
    value = {"values": [safe, plain]}
    actual = _materialize_lazy(value)
    assert actual is not value and actual["values"] is not value["values"]
    assert isinstance(actual["values"][0], SafeString)
    assert type(actual["values"][1]) is str

    def fail():
        raise RuntimeError("lazy failure")

    with pytest.raises(RuntimeError, match="lazy failure"):
        _materialize_lazy([lazy(fail, str)()])


def test_custom_metaclass_equality_cannot_claim_builtin_fast_path():
    class EqualToEverything(type):
        def __eq__(cls, other):
            return True

    class CustomList(list, metaclass=EqualToEverything):
        pass

    value = CustomList([lazy(lambda: "resolved", str)()])
    actual = _materialize_lazy(value)
    assert type(actual) is list
    assert type(actual[0]) is str
    assert actual == ["resolved"]
