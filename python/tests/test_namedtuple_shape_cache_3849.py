"""The two caches that keep ``collections.namedtuple()`` off the per-conversion path (#3849 review).

PR #3856 made a namedtuple cross the engine as ``(name, fields, items)`` and rebuild its class with
``collections.namedtuple`` whenever a native tag is called. Two caches stand in front of that call, and
the review of #3856 found each unpinned or incomplete:

* the Rust side (``named_tuple_class`` in ``crates/djust_core/src/lib.rs``) cached a shape it BUILT
  but not one the factory REFUSED, so every conversion of a refused namedtuple re-ran
  ``collections.namedtuple()`` (an ``exec`` that raises): 115 ms against 92 ms per 20,000 refused rows;
* the Python side (``_namedtuple_shape_is_buildable_cached``) had an ``lru_cache`` the changelog
  called "cached per shape", with no test.

Both are pinned by COUNTED work, not a duration (#1795): the number of times the factory runs, observed by
wrapping ``collections.namedtuple`` (the engine looks it up on every build, so the wrapper sees it).
Shape names carry a per-run token so no earlier test, and no other shape in the process, has already
warmed the (process-lifetime) Rust cache for them.

Engine path: the native engine (``_rust.render_template`` / ``RustLiveView``) and the Python gate
``serialize_value``.

Refs #3849, #3856, #1795, #1468.
"""

from __future__ import annotations

import collections
import itertools
import uuid
from typing import Any

import pytest

from djust.components import rust_handlers
from djust.template import serialization as serialization_module
from djust.template.serialization import serialize_value

_TOKEN = uuid.uuid4().hex[:8]
_COUNTER = itertools.count()


def _odd(name: str, fields: Any, items: tuple = (1, 2)) -> Any:
    """An instance of a tuple class whose ``_fields`` and ``__name__`` are exactly as given."""
    cls = type(
        name,
        (tuple,),
        {"_fields": fields, "__slots__": (), "__new__": lambda cls, *a: tuple.__new__(cls, a)},
    )
    return cls(*items[: len(fields)])


def _fresh(prefix: str, fields: tuple[str, ...]) -> Any:
    """A tuple of a shape no other test (and no earlier call) has used."""
    return _odd(f"{prefix}_{_TOKEN}_{next(_COUNTER)}", fields)


@pytest.fixture
def factory_calls(monkeypatch):
    """``name -> number of times collections.namedtuple was asked for it``."""
    real = collections.namedtuple
    calls: dict[str, int] = collections.defaultdict(int)

    def counting(typename, *args, **kwargs):
        calls[typename] += 1
        return real(typename, *args, **kwargs)

    # The engine looks ``collections.namedtuple`` up on every build; the Python gate bound the name
    # at import (``from collections import namedtuple``), so it is wrapped where it is used as well.
    monkeypatch.setattr(collections, "namedtuple", counting)
    monkeypatch.setattr(serialization_module, "namedtuple", counting)
    return calls


@pytest.fixture(scope="module", autouse=True)
def _native_tags():
    rust_handlers.register_with_rust_engine()


RENDERS = 25
SRC = "{% badge label='x' %}{{ v|length }}"


def _render(value: Any, times: int = RENDERS) -> None:
    from djust import _rust

    for _ in range(times):
        _rust.render_template(SRC, {"v": value})


class TestTheEnginesFactoryRunsOncePerShape:
    def test_a_refused_shape_is_attempted_once(self, factory_calls):
        """Before: once per conversion (a refused shape was never remembered)."""
        value = _fresh("Refused", ("a", "a"))  # a duplicate field: namedtuple() raises ValueError
        _render(value)
        assert factory_calls[type(value).__name__] == 1

    def test_a_buildable_shape_is_built_once(self, factory_calls):
        """The control the refusal case is measured against: the existing cache for a built shape."""
        value = _fresh("Buildable", ("a", "b"))
        _render(value)
        assert factory_calls[type(value).__name__] == 1

    def test_two_refused_shapes_are_remembered_separately(self, factory_calls):
        """The key is the whole shape: a refusal of one shape does not stand in for another, even
        under the same class name."""
        name = f"Twin_{_TOKEN}_{next(_COUNTER)}"
        refused = _odd(name, ("a", "a"))
        buildable = _odd(name, ("a", "b"))
        _render(refused, 3)
        _render(buildable, 3)
        assert factory_calls[name] == 2  # one per shape
        from djust import _rust

        out = _rust.render_template("{{ v }}", {"v": buildable})
        assert out.startswith(name + "(a=1, b=2)"), out

    def test_a_refused_shape_still_renders_its_items(self, factory_calls):
        """The fallback loses the type, never the value, and keeps doing so once cached."""
        from djust import _rust

        value = _fresh("Items", ("a", "a"))
        _render(value, 2)
        assert _rust.render_template("{{ v.0 }}-{{ v|length }}", {"v": value}) == "1-2"

    def test_a_failure_that_is_not_the_shapes_is_not_remembered(self, monkeypatch):
        """``MemoryError`` / an exception from a patched factory is not a fact about the shape: the
        next conversion tries again and, the factory having recovered, builds the class."""
        from djust import _rust

        real = collections.namedtuple
        name = f"Flaky_{_TOKEN}_{next(_COUNTER)}"
        seen = {"n": 0}

        def flaky(typename, *args, **kwargs):
            if typename == name:
                seen["n"] += 1
                if seen["n"] == 1:
                    raise RuntimeError("transient")
            return real(typename, *args, **kwargs)

        monkeypatch.setattr(collections, "namedtuple", flaky)
        value = _odd(name, ("a", "b"))
        # The first conversion falls back to the plain tuple (it must not fail the render) ...
        src = "{% badge label='x' %}{{ v|length }}"
        assert _rust.render_template(src, {"v": value}).endswith("2")
        assert seen["n"] == 1
        # ... and the second one asks the factory again: the failure was not cached.
        assert _rust.render_template(src, {"v": value}).endswith("2")
        assert seen["n"] == 2
        # A third finds the class the second built.
        assert _rust.render_template(src, {"v": value}).endswith("2")
        assert seen["n"] == 2


class TestThePythonGatesCacheIsPerShape:
    """``_namedtuple_shape_is_buildable_cached`` (``python/djust/template/serialization.py``): the
    changelog said "cached per shape"; this is the test that says it too."""

    def test_a_shape_is_asked_of_the_factory_once(self, factory_calls):
        refused = _fresh("PyRefused", ("a", "a"))
        buildable = _fresh("PyBuildable", ("a", "b"))
        for value in (refused, buildable):
            for _ in range(RENDERS):
                serialize_value(value, for_render=True)
            assert factory_calls[type(value).__name__] == 1, type(value).__name__

    def test_the_cache_is_keyed_by_the_whole_shape(self, factory_calls):
        name = f"PyTwin_{_TOKEN}_{next(_COUNTER)}"
        for _ in range(3):
            serialize_value(_odd(name, ("a", "a")), for_render=True)
            serialize_value(_odd(name, ("a", "b")), for_render=True)
        assert factory_calls[name] == 2
