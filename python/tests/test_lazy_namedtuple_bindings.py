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
