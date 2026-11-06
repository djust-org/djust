"""A namedtuple inside a LIST or DICT operand is a recorded gap, not a namedtuple gap (#3849).

The review of #3856 listed it: ``{% approval_gate reject_label=rows %}`` over ``rows = [P(1, 2)]``
prints the JSON text ``[[1, 2]]`` where Django's tag prints ``[P(x=1, y=2)]``. It is *declined* in that
PR for a measured reason, pinned here so the decision is mechanical and not a memory:

* **It is not about namedtuples.** The engine's narrow typed channel (``TYPED_ARGS_STANDINS_ONLY``,
  ``typed_operands`` in ``crates/djust_templates/src/renderer.rs``) hands a handler an operand
  only when the operand ITSELF is a tuple, a namedtuple or an object. A list or dict operand stays
  its JSON text, and whatever the JSON cannot carry inside it is lost: a plain tuple (``[(1, 2)]``
  prints ``[[1, 2]]``), a ``UUID`` or a ``Path`` (they print as quoted strings). The namedtuple is
  one member of that class (the cases below are the same invariant with three different values).
* **A fix is a different mechanism with a cost.** Handing over a container that merely HOLDS such a
  value means walking every list and dict operand on every tag call to find one, and then
  materialising the whole container a second time as Python objects: the cost the narrow channel
  exists to avoid for a 100,000-row operand (``TYPED_ARGS_STANDINS_ONLY`` is the knob that
  ``json_viewer``, the one handler without it, pays). Measured (release extension, a 100,000-row
  list of dicts as ``reject_label``, median of 5 renders, two runs each): peak RSS 779 to 782 MB
  with the narrow channel and 968 to 969 MB without it (+24%, about 1.9 KB a row), time unchanged
  (2.7 to 2.9 s either way; the 14 MB of output dominates). That is the memory of the operand held
  a second time, before the walk that would find the namedtuple at all. ``_parse_args`` would also
  have to walk
  lists and dicts to put the types back, and every OTHER value the typed channel carries
  (``datetime``, ``Decimal``) would then print through a handler where it prints as JSON text
  today, a behaviour change for ~130 handlers that is not a namedtuple fix.

Each case is ``xfail(strict=True)``: it turns red the day the gap closes, which is the prompt to
delete the marker (and this paragraph).

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

_GAP = pytest.mark.xfail(
    strict=True,
    reason="#3849: a value inside a list/dict operand is its JSON text; declined, see the docstring",
)


class TestAContainerOperandIsItsJsonText:
    @_GAP
    @pytest.mark.parametrize("route", sorted(ROUTES))
    @pytest.mark.parametrize("value", [v for _, v in CASES], ids=[i for i, _ in CASES])
    def test_native_equals_django(self, engines, route, value):  # noqa: F811
        assert ROUTES[route](engines, SRC, {"rows": value}) == render_django(
            engines, SRC, {"rows": value}
        )

    def test_the_gap_is_the_same_for_every_member_of_the_class(self, engines):  # noqa: F811
        """The control for 'it is not about namedtuples': each of the six values above DIFFERS from
        Django (the xfails above would otherwise hide one that had started to agree)."""
        differing = [
            name
            for name, value in CASES
            if render_backend(engines, SRC, {"rows": value})
            != render_django(engines, SRC, {"rows": value})
        ]
        assert differing == [name for name, _ in CASES]

    def test_the_operand_itself_a_namedtuple_is_not_the_gap(self, engines):  # noqa: F811
        """What #3856 fixed, as the contrast: the SAME value as the operand agrees with Django."""
        assert render_backend(engines, GATE, {"v": P(1, 2)}) == render_django(
            engines, GATE, {"v": P(1, 2)}
        )
