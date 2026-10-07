"""``asyncio.iscoroutinefunction`` is deprecated since Python 3.14 (#3255).

CPython 3.14 deprecated it and slates it for removal in 3.16, so the three
places djust used it (``@background``, the async-work runner and the
``track_performance`` decorator) would warn on 3.14 and 3.15 and break on 3.16.
They use ``asgiref.sync.iscoroutinefunction`` now: the same answer for an
``async def`` and for a sync wrapper marked with ``markcoroutinefunction``, on
every supported interpreter, with no warning.

The source scan is the guard on interpreters older than 3.14, where the
deprecated call is silent; the behavioural tests are the guard on 3.14+, where
it warns (``-W error`` below), and pin that the replacement still dispatches an
``async def`` to the async branch.
"""

from __future__ import annotations

import ast
import asyncio
import warnings
from pathlib import Path

from asgiref.sync import iscoroutinefunction

import djust
from djust.decorators import background
from djust.mixins.async_work import run_async_callback
from djust.performance import track_performance

PACKAGE = Path(djust.__file__).resolve().parent


def _deprecated_spelling(tree: ast.AST) -> list[int]:
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr == "iscoroutinefunction"
        and isinstance(node.value, ast.Name)
        and node.value.id == "asyncio"
    ]


def test_no_module_calls_the_deprecated_asyncio_spelling() -> None:
    hits = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if "tests" in path.relative_to(PACKAGE).parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        hits += [f"{path.relative_to(PACKAGE)}:{line}" for line in _deprecated_spelling(tree)]
    assert hits == []


def test_the_scan_finds_the_spelling_it_forbids() -> None:
    """Gate-off for the scan above: it must flag the bare call."""
    assert _deprecated_spelling(ast.parse("import asyncio\nasyncio.iscoroutinefunction(f)\n")) == [
        2
    ]
    assert _deprecated_spelling(ast.parse("from asgiref.sync import iscoroutinefunction\n")) == []


class _View:
    def __init__(self) -> None:
        self.scheduled: list = []

    def start_async(self, callback, *args, name=None, **kwargs):
        self.scheduled.append(callback)


def test_background_picks_the_async_branch_without_a_deprecation_warning() -> None:
    view = _View()
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)

        @background
        async def work(self, **kwargs):
            return "async"

        @background
        def sync_work(self, **kwargs):
            return "sync"

        work(view)
        sync_work(view)
    assert iscoroutinefunction(view.scheduled[0])
    assert not iscoroutinefunction(view.scheduled[1])


def test_track_performance_wraps_async_and_sync_alike() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)

        @track_performance("op")
        async def async_op() -> str:
            return "a"

        @track_performance("op")
        def sync_op() -> str:
            return "s"

    assert iscoroutinefunction(async_op)
    assert not iscoroutinefunction(sync_op)
    assert asyncio.run(async_op()) == "a"
    assert sync_op() == "s"


def test_the_async_work_runner_dispatches_both_kinds_without_a_warning() -> None:
    calls: list[str] = []

    async def async_callback() -> str:
        calls.append("async")
        return "a"

    def sync_callback() -> str:
        calls.append("sync")
        return "s"

    async def run_both() -> list[str]:
        return [await run_async_callback(async_callback), await run_async_callback(sync_callback)]

    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        assert asyncio.run(run_both()) == ["a", "s"]
    assert calls == ["async", "sync"]
