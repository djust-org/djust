"""The template-call guard's refusal log: bound, keying, hostile names, races (#3928).

``djust._template_guards.warn_refused_once`` is shared by every entry that refuses a flagged
``render`` (#3815, #3880). These pin what the review of #3879 found untested.
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from typing import Any

import pytest
from django.utils.functional import SimpleLazyObject

from djust import _template_guards as guards

LOGGER = "djust._template_guards"


@pytest.fixture(autouse=True)
def _fresh_refusal_log():
    guards._reset_refusal_log()
    yield
    guards._reset_refusal_log()


def _records(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == LOGGER]


def _kind(name: str) -> type:
    return type(name, (), {"render": lambda self, context=None: "x"})


def test_two_distinct_types_are_two_warnings_and_a_repeat_is_none(caplog):
    a, b = _kind("Alpha")(), _kind("Beta")()
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        for obj in (a, b, a, b, a):
            guards.warn_refused_once("{% include %}", obj, "render", "alters_data")
    messages = [r.getMessage() for r in _records(caplog)]
    assert len(messages) == 2, messages
    assert "Alpha.render()" in messages[0] and "Beta.render()" in messages[1]


def test_a_new_refusal_past_the_cap_is_still_logged(caplog, monkeypatch):
    """Before #3928 a refusal of a NEW type past the cap was dropped for the rest of the process."""
    monkeypatch.setattr(guards, "_WARNED_CAP", 3)
    kinds = [_kind(f"K{i}")() for i in range(6)]
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        for obj in kinds:
            guards.warn_refused_once("{% include %}", obj, "render", "alters_data")
    messages = [r.getMessage() for r in _records(caplog)]
    assert len(messages) == 6, messages
    assert "K5.render()" in messages[-1]
    assert len(guards._WARNED) == 3, "the dedup table is bounded"


def test_the_bound_evicts_the_least_recently_seen_key(caplog, monkeypatch):
    monkeypatch.setattr(guards, "_WARNED_CAP", 2)
    a, b, c = _kind("A")(), _kind("B")(), _kind("C")()
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        for obj in (a, b, a, c, a):  # `a` is refreshed by its repeat, so `c` evicts `b`
            guards.warn_refused_once("{% include %}", obj, "render", "alters_data")
    assert len(_records(caplog)) == 3


def test_lazy_objects_are_keyed_on_what_they_wrap(caplog):
    """``type(SimpleLazyObject(..))`` is one class for every wrapped type: one key, one log."""
    first = SimpleLazyObject(lambda: _kind("Wrapped1")())
    second = SimpleLazyObject(lambda: _kind("Wrapped2")())
    for lazy in (first, second):
        lazy.render  # noqa: B018 -- what the guard does before refusing: evaluates the proxy
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        guards.warn_refused_once("{% include %}", first, "render", "alters_data")
        guards.warn_refused_once("{% include %}", second, "render", "alters_data")
    messages = [r.getMessage() for r in _records(caplog)]
    assert len(messages) == 2, messages
    assert "Wrapped1.render()" in messages[0] and "Wrapped2.render()" in messages[1]


def test_an_unevaluated_lazy_object_is_named_as_the_proxy_without_evaluating_it(caplog):
    evaluated: list[int] = []
    lazy = SimpleLazyObject(lambda: evaluated.append(1) or _kind("Never")())
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        guards.warn_refused_once("{% include %}", lazy, "render", "alters_data")
    assert evaluated == []
    assert "SimpleLazyObject.render()" in _records(caplog)[0].getMessage()


class _RaisingStr:
    def __str__(self) -> str:
        raise RuntimeError("a hostile __module__")

    __format__ = None  # type: ignore[assignment]


def test_a_type_with_a_hostile_module_does_not_make_the_refusal_raise(caplog):
    kind = _kind("Hostile")
    kind.__module__ = _RaisingStr()  # type: ignore[assignment]
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        guards.warn_refused_once("{% include %}", kind(), "render", "alters_data")
    assert "Hostile.render()" in _records(caplog)[0].getMessage()


def test_a_newline_in_a_class_name_cannot_forge_a_log_line(caplog):
    kind = _kind("Real\nWARNING forged: everything is fine")
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        guards.warn_refused_once("{% include %}", kind(), "render", "alters_data")
    message = _records(caplog)[0].getMessage()
    assert "\n" not in message
    assert "Real\\nWARNING forged" in message


def test_concurrent_refusals_of_one_type_log_once(caplog, monkeypatch):
    """The check-then-add is one critical section (``_WARNED_LOCK``; free-threaded builds).

    The table's membership test is slowed (it pauses, releasing the GIL), so an unlocked
    check-then-add lets every thread see "not yet warned" and log; the lock lets one through.
    """
    entered: list[int] = []

    class _SlowTable(OrderedDict):
        def __contains__(self, key: object) -> bool:
            entered.append(1)
            found = super().__contains__(key)
            # widen the window after the read; the assertion is a count, not a time
            threading.Event().wait(0.01)
            return found

    monkeypatch.setattr(guards, "_WARNED", _SlowTable())
    obj = _kind("Raced")()
    errors: list[BaseException] = []

    def refuse() -> None:
        try:
            guards.warn_refused_once("{% include %}", obj, "render", "alters_data")
        except BaseException as exc:  # noqa: BLE001 -- surfaced below
            errors.append(exc)

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        workers = [threading.Thread(target=refuse) for _ in range(8)]
        for w in workers:
            w.start()
        for w in workers:
            w.join()
    assert errors == []
    assert len(entered) == 8
    assert len(_records(caplog)) == 1


def test_a_backend_template_wrapper_is_refused_on_its_inner_render_by_the_rust_engine(caplog):
    """``{% include t %}`` on the Rust-rendered path (``render_template_object`` and the compiled
    operand) with a Django ``BackendTemplate`` wrapper: the inner template's ``render`` is the one
    whose marker counts, and it is not called (#3880 covered the wrapper for Django's engine)."""
    from django.template import Engine, Template
    from django.template.backends.django import Template as BackendTemplate

    from djust import _rust
    from djust.template.operands import render_template_object

    calls: list[int] = []

    class Flagged(Template):
        def render(self, context: Any = None) -> str:
            calls.append(1)
            return super().render(context)

        render.alters_data = True  # type: ignore[attr-defined]

    class FlaggedObject:
        def render(self, context: Any = None) -> str:
            calls.append(2)
            return "RENDERED"

        render.do_not_call_in_templates = True  # type: ignore[attr-defined]

    engine = Engine()
    for inner in (Flagged("INNER", engine=engine), FlaggedObject()):
        wrapper = BackendTemplate(inner, backend=None)  # type: ignore[arg-type]
        assert _rust.render_template("[{% include t %}]", {"t": wrapper}) == "[]"
        assert render_template_object(wrapper, {}, True) == ""
    assert calls == []
    # an unflagged inner template behind the same wrapper still renders
    plain = BackendTemplate(engine.from_string("OK"), backend=None)  # type: ignore[arg-type]
    assert _rust.render_template("[{% include t %}]", {"t": plain}) == "[OK]"


class _RaisingFormatStr(str):
    def __format__(self, spec: str) -> str:
        raise RuntimeError("a hostile __format__")

    def __str__(self) -> str:
        raise RuntimeError("a hostile __str__")


class _LyingStr(str):
    def isprintable(self) -> bool:
        return True


@pytest.mark.parametrize(
    "hostile, label",
    [
        pytest.param(_RaisingFormatStr("Real"), "raising-format", id="str-subclass-raising-format"),
        pytest.param(
            _LyingStr("Real\nWARNING forged"), "lying-isprintable", id="str-subclass-lying"
        ),
    ],
)
def test_a_str_subclass_as_a_type_name_is_not_trusted(caplog, hostile, label):
    """Review of #4068: only an exact ``str`` is used; a subclass can raise or lie about itself."""
    kind = _kind("Plain")
    kind.__qualname__ = hostile  # type: ignore[assignment]
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        guards.warn_refused_once("{% include %}", kind(), label, "alters_data")
    message = _records(caplog)[0].getMessage()
    assert "\n" not in message
    assert f"?.{label}()" in message


def test_a_metaclass_whose_module_read_raises_does_not_make_the_refusal_raise(caplog):
    class _Meta(type):
        @property
        def __module__(cls):  # type: ignore[override]
            raise RuntimeError("no")

    kind = _Meta("Odd", (), {})
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        guards.warn_refused_once("{% include %}", kind(), "render", "alters_data")
    assert "Odd.render()" in _records(caplog)[0].getMessage()
