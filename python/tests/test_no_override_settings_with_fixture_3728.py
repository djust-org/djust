"""No test combines ``@override_settings`` with the ``settings`` fixture (#3728).

On one test the decorator exits first and restores the pre-decorator settings;
the fixture's teardown then restores the settings *it* saw, which are the
decorator's. Everything the decorator set stays set for the rest of the xdist
worker, so an unrelated test far away fails depending on how the shards were
dealt (#3728: T017 suppressed by two tests in one file, seen by a line-counter
test only after ``.test_durations`` changed the order).

This scans every test root for a function that has a ``settings`` parameter and
an ``override_settings`` decorator, or a class decorated with it whose methods
take ``settings``. Put every override in the decorator, or use only the fixture.
"""

from __future__ import annotations

import ast
import pathlib
import textwrap

ROOTS = ("tests", "python/tests", "python/djust/tests")
REPO = pathlib.Path(__file__).resolve().parents[2]


def _is_override_settings(decorator: ast.expr) -> bool:
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
    return name == "override_settings"


def _takes_settings(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return "settings" in {a.arg for a in fn.args.args + fn.args.kwonlyargs}


def mixes_decorator_and_fixture(source: str) -> list[str]:
    """Names of the tests in *source* that combine the decorator and the fixture."""
    found: list[str] = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if _takes_settings(node) and any(_is_override_settings(d) for d in node.decorator_list):
                found.append(node.name)
        elif isinstance(node, ast.ClassDef) and any(
            _is_override_settings(d) for d in node.decorator_list
        ):
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)) and _takes_settings(
                    member
                ):
                    found.append(f"{node.name}.{member.name}")
    return found


def test_no_test_mixes_the_decorator_with_the_settings_fixture():
    offenders = []
    scanned = 0
    for root in ROOTS:
        for path in sorted((REPO / root).rglob("*.py")):
            scanned += 1
            for name in mixes_decorator_and_fixture(path.read_text(encoding="utf-8")):
                offenders.append(f"{path.relative_to(REPO)}: {name}")
    assert scanned > 100, "the scan found almost no test files, so it proves nothing"
    assert not offenders, (
        "These tests combine @override_settings with the `settings` fixture, which leaks the "
        "decorator's settings to every later test on the worker (#3728). Use only one of the "
        "two:\n  " + "\n  ".join(offenders)
    )


def test_the_scan_sees_the_three_shapes_that_leak():
    function_level = textwrap.dedent(
        """
        @override_settings(A=1)
        def test_x(settings):
            pass
        """
    )
    method_level = textwrap.dedent(
        """
        class TestY:
            @override_settings(A=1)
            def test_y(self, settings):
                pass
        """
    )
    class_level = textwrap.dedent(
        """
        @override_settings(A=1)
        class TestZ:
            def test_z(self, settings):
                pass
        """
    )
    assert mixes_decorator_and_fixture(function_level) == ["test_x"]
    assert mixes_decorator_and_fixture(method_level) == ["test_y"]
    assert mixes_decorator_and_fixture(class_level) == ["TestZ.test_z"]


def test_the_scan_leaves_the_safe_shapes_alone():
    only_decorator = "@override_settings(A=1)\ndef test_a():\n    pass\n"
    only_fixture = "def test_b(settings):\n    settings.A = 1\n"
    assert mixes_decorator_and_fixture(only_decorator) == []
    assert mixes_decorator_and_fixture(only_fixture) == []
