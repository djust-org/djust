"""A namedtuple (or tuple, UUID, Path) inside a LIST or DICT operand reaches the tag as itself (#3849).

The review of #3856 listed it: ``{% approval_gate reject_label=rows %}`` over ``rows = [P(1, 2)]``
printed the JSON text ``[[1, 2]]`` where Django's tag prints ``[P(x=1, y=2)]``, because the native
handler was handed a list or dict operand as its JSON text. The gap was declined then (a fix in the
native handler meant walking and materialising every container operand). Since 2.0 ``approval_gate``
has no native twin: the Rust engine renders the Python tag's own node through the ``{% load %}``
bridge (one render path per tag), which is handed the operand as the object it is, so every case
below agrees with Django on both routes. (A kept native twin still gets the JSON text.)

Engine path: the Django-backend engine and ``RustLiveView``; Django's own tag is the reference.

Refs #3849, #3856, #3768.
"""

from __future__ import annotations

import pathlib
import uuid

import pytest

from python.tests.test_typed_tuple_shapes_3849 import (  # noqa: F401
    GATE,
    P,
    engines,
    render_backend,
    render_django,
    render_live,
)

_UUID = uuid.UUID("12345678-1234-5678-1234-567812345678")

#: ``(id, value)``: a stand-in-carrying value inside a list, then inside a dict.
CASES = [
    ("namedtuple_in_list", [P(1, 2)]),
    ("namedtuple_in_dict", {"k": P(1, 2)}),
    ("plain_tuple_in_list", [(1, 2)]),
    ("plain_tuple_in_dict", {"k": (1, 2)}),
    ("uuid_in_list", [_UUID]),
    ("path_in_list", [pathlib.PurePosixPath("/a/b")]),
]
ROUTES = {"backend": render_backend, "live": render_live}
SRC = "{% approval_gate reject_label=rows %}"


class TestAContainerOperandIsItsObject:
    @pytest.mark.parametrize("route", sorted(ROUTES))
    @pytest.mark.parametrize("value", [v for _, v in CASES], ids=[i for i, _ in CASES])
    def test_native_equals_django(self, engines, route, value):  # noqa: F811
        assert ROUTES[route](engines, SRC, {"rows": value}) == render_django(
            engines, SRC, {"rows": value}
        )

    def test_the_django_render_shows_each_value_as_itself(self, engines):  # noqa: F811
        """Non-vacuity: Django's render of each case is not the JSON text the native handler
        printed, so the equality above is about the value, not two empty strings."""
        for name, value in CASES:
            out = render_django(engines, SRC, {"rows": value})
            assert "[[1, 2]]" not in out and out, name

    def test_the_operand_itself_a_namedtuple_is_not_the_gap(self, engines):  # noqa: F811
        """What #3856 fixed, as the contrast: the SAME value as the operand agrees with Django."""
        assert render_backend(engines, GATE, {"v": P(1, 2)}) == render_django(
            engines, GATE, {"v": P(1, 2)}
        )
