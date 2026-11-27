"""Plain scalars skip the type tests of the three per-value render walks.

``serialize_value``, ``_protect_sidecar_tree`` and ``_collect_safe_keys`` visit
every leaf of a context on each render; most leaves are an exact ``str`` /
``int`` / ``float`` / ``bool`` / ``None``, which every branch returned
unchanged. They now answer for those first (``make benchmark-components``:
``render/table/djust-backend`` 35 -> 19 ms). A subclass still takes the full
path: a ``SafeString`` is still a safe key, an ``IntEnum`` still serializes as
before, a lazy translation is still forced to ``str``.
"""

from __future__ import annotations

import enum

from django.utils.functional import lazy
from django.utils.safestring import SafeString, mark_safe

from djust.mixins.rust_bridge import _collect_safe_keys
from djust.serialization import _protect_sidecar_tree
from djust.template.serialization import serialize_context, serialize_value


class Level(enum.IntEnum):
    LOW = 1


def test_exact_scalars_come_back_as_themselves():
    for value in ("text", 3, 2.5, True, None):
        assert serialize_value(value) is value
        assert serialize_value(value, for_render=True) is value
        assert _protect_sidecar_tree(value, 0, {}, set()) is value
        assert _collect_safe_keys(value, "k") == []


def test_subclasses_still_take_the_full_path():
    safe = mark_safe("<b>x</b>")
    assert _collect_safe_keys(safe, "k") == ["k"]
    assert _collect_safe_keys({"a": [1, safe, "s"]}, "ctx") == ["ctx.a.1"]
    assert isinstance(serialize_value(safe, for_render=True), SafeString)
    assert serialize_value(Level.LOW, for_render=True) == 1
    forced = serialize_value(lazy(lambda: "later", str)(), for_render=True)
    assert forced == "later" and type(forced) is str


def test_containers_of_scalars_serialize_unchanged():
    rows = [{"id": i, "name": "row %d" % i, "price": i * 1.5, "on": i % 2 == 0} for i in range(5)]
    out = serialize_context({"rows": rows, "title": "T", "n": None}, for_render=True)
    assert out == {"rows": rows, "title": "T", "n": None}
    assert out["rows"] is not rows  # still a new container, as before
