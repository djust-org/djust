"""Regression tests for #1489 — top-level re-export of decorators.

`cache` and `background` are stable public-API decorators that live in
``djust.decorators``. Issue #1489 (API-stability audit finding F3) asked for
them to also be reachable from the top-level ``djust`` package so the
canonical import path is ``from djust import cache`` rather than only
``from djust.decorators import cache``. (#4006) `optimistic` and `client_state` were
re-exported too, until djust 2.0 removed them because they did nothing (#4006).

These tests pin that re-export: the names must be importable from
``djust``, must be listed in ``djust.__all__``, and must be the *same
object* as the ``djust.decorators`` originals (a re-export, not a
shadowing redefinition).
"""

from __future__ import annotations

import djust
import djust.decorators as _decorators

_REEXPORTED = ["cache", "background"]


class TestTopLevelReexports1489:
    """The four decorators are reachable from the top-level ``djust`` package."""

    def test_importable_from_top_level_djust(self):
        """``from djust import cache, background``."""
        from djust import cache, background  # noqa: F401

        # If the import line above resolved, all four names exist on the
        # package. Assert they are callable decorators, not sentinels.
        for name in _REEXPORTED:
            assert callable(getattr(djust, name)), f"djust.{name} is not callable"

    def test_listed_in_djust_dunder_all(self):
        """Each re-exported name appears in ``djust.__all__``."""
        for name in _REEXPORTED:
            assert name in djust.__all__, f"{name!r} missing from djust.__all__"

    def test_reexport_is_same_object_as_decorators_original(self):
        """The top-level name is the *same* object as ``djust.decorators.<name>``.

        Guards against a future edit that redefines one of these names at the
        top level instead of re-exporting it.
        """
        for name in _REEXPORTED:
            assert getattr(djust, name) is getattr(_decorators, name), (
                f"djust.{name} is not the same object as djust.decorators.{name}"
            )

    def test_originals_still_in_decorators_dunder_all(self):
        """The re-export does not disturb ``djust.decorators.__all__``."""
        for name in _REEXPORTED:
            assert name in _decorators.__all__, (
                f"{name!r} unexpectedly missing from djust.decorators.__all__"
            )


def test_the_inert_decorators_are_refused_with_the_replacement_4006():
    """#4006: @optimistic and @client_state recorded metadata nothing read, and
    djust 2.0 removed them. Both import paths, and attribute access, raise an
    ImportError that says why and what to do instead (the 2.0 removed-names
    convention, ``_removed_settings.REMOVED_NAMES``)."""
    import pytest

    advice = {"optimistic": "dj-loading", "client_state": "set both in one handler"}
    for name, hint in advice.items():
        assert name not in djust.__all__
        assert name not in _decorators.__all__
        for module in ("djust", "djust.decorators"):
            with pytest.raises(ImportError) as exc:
                exec(f"from {module} import {name}", {})
            message = str(exc.value)
            assert message.startswith(f"{module}.{name} was removed in djust 2.0"), message
            assert hint in message and "migrating-to-2.0" in message, message
        with pytest.raises(ImportError):
            getattr(_decorators, name)
    # An unknown name is still an AttributeError.
    with pytest.raises(AttributeError):
        _decorators.no_such_decorator  # noqa: B018
    with pytest.raises(AttributeError):
        djust.no_such_name  # noqa: B018
