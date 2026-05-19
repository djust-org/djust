"""Regression tests for #1489 — top-level re-export of four decorators.

`optimistic`, `cache`, `client_state`, and `background` are stable
public-API decorators that live in ``djust.decorators``. Issue #1489
(API-stability audit finding F3) asked for them to also be reachable from
the top-level ``djust`` package so the canonical import path is
``from djust import optimistic`` rather than only
``from djust.decorators import optimistic``.

These tests pin that re-export: the names must be importable from
``djust``, must be listed in ``djust.__all__``, and must be the *same
object* as the ``djust.decorators`` originals (a re-export, not a
shadowing redefinition).
"""

from __future__ import annotations

import djust
import djust.decorators as _decorators

_REEXPORTED = ["optimistic", "cache", "client_state", "background"]


class TestTopLevelReexports1489:
    """The four decorators are reachable from the top-level ``djust`` package."""

    def test_importable_from_top_level_djust(self):
        """``from djust import optimistic, cache, client_state, background``."""
        from djust import optimistic, cache, client_state, background  # noqa: F401

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
