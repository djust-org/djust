"""#4094: only djust's own packages are framework code to the system checks.

Every per-class check skipped modules whose name starts with ``djust_``, so a
project app called ``djust_shop`` (and the demo project's ``djust_demos``,
``djust_forms``, ...) was never checked.
"""

from __future__ import annotations

import pathlib

import pytest

from djust import LiveView
from djust._framework_code import (
    framework_top_level_packages,
    is_djust_module,
    is_djust_production_module,
)


def _view(module: str, **attrs):
    namespace = {"__module__": module, "exposure_policy": "explicit", **attrs}
    return type("ShopView", (LiveView,), namespace)


def test_the_set_is_the_python_tree():
    """djust ships the packages under ``python/`` (``python-source``), and only those."""
    root = pathlib.Path(__file__).resolve().parents[2]
    assert root.name == "python"
    shipped = {p.name for p in root.iterdir() if p.is_dir() and (p / "__init__.py").is_file()}
    assert shipped == {"djust"}
    assert shipped <= framework_top_level_packages()
    assert not any(name.startswith("djust_") for name in framework_top_level_packages())


@pytest.mark.parametrize(
    "module, own",
    [
        ("djust", True),
        ("djust.live_view", True),
        ("djust.contrib.uploads.views", True),
        ("djust.theming.mixins", True),
        ("djust.auth.mixins", True),
        ("djust_shop.views", False),
        ("djust_demos.views.demos", False),
        ("djustice.views", False),
        ("myapp.djust.views", False),
        ("", False),
    ],
)
def test_which_modules_are_djust(module, own):
    assert is_djust_module(module) is own


def test_djust_tests_and_examples_are_still_checked():
    assert is_djust_production_module("djust.contrib.uploads.views")
    assert not is_djust_production_module("djust.tests.test_x")
    assert not is_djust_production_module("djust_shop.views")


def test_a_project_app_named_djust_shop_is_checked():
    """The bug: a ``djust_shop`` view with no template got no V001."""
    from djust.checks.components import check_liveviews
    from djust.checks.utils import _is_framework_internal_class
    from djust.management._introspect import is_user_class

    view = _view("djust_shop.views")
    assert not _is_framework_internal_class(view)
    assert is_user_class(view)
    messages = check_liveviews(None)
    assert [m for m in messages if m.id == "djust.V001" and "djust_shop.views.ShopView" in m.msg]


def test_djust_own_contrib_code_is_still_framework_code():
    from djust.checks.components import check_liveviews
    from djust.checks.utils import _is_framework_internal_class

    view = _view("djust.contrib.shop_fixture_4094")
    assert _is_framework_internal_class(view)
    messages = check_liveviews(None)
    assert not [m for m in messages if "djust.contrib.shop_fixture_4094" in m.msg]
