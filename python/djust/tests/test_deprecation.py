"""Unit tests for the internal ``djust._deprecation`` helper.

Covers the standardized ``warn_deprecated`` helper (message format, warning
category, ``stacklevel`` correctness, optional kwargs) plus regression tests
that the three migrated call sites (``@event``, ``LiveViewForm``, the legacy
theming module) still emit a ``DeprecationWarning`` with a corrected,
concrete ``removed_in`` version.
"""

import os
import warnings

import pytest

from djust._deprecation import warn_deprecated


# ---------------------------------------------------------------------------
# warn_deprecated helper
# ---------------------------------------------------------------------------


def test_warn_deprecated_emits_deprecationwarning():
    """The helper emits a DeprecationWarning (not a generic UserWarning)."""
    with pytest.warns(DeprecationWarning):
        warn_deprecated(
            "@event",
            since="0.3",
            removed_in="1.1.0",
            instead="@event_handler",
        )


def test_warn_deprecated_message_contains_all_parts():
    """The message includes what, since, removed_in, and instead."""
    with pytest.warns(DeprecationWarning) as record:
        warn_deprecated(
            "LiveViewForm",
            since="0.3",
            removed_in="1.1.0",
            instead="django.forms.Form",
        )
    msg = str(record[0].message)
    assert "LiveViewForm" in msg
    assert "0.3" in msg
    assert "1.1.0" in msg
    assert "django.forms.Form" in msg


def test_warn_deprecated_message_format():
    """The message follows the exact policy-mandated format string."""
    with pytest.warns(DeprecationWarning) as record:
        warn_deprecated(
            "thing",
            since="1.0",
            removed_in="2.0.0",
            instead="other_thing",
        )
    msg = str(record[0].message)
    expected = (
        "thing is deprecated since djust 1.0 and will be removed "
        "no earlier than 2.0.0. Use other_thing instead."
    )
    assert msg == expected


def test_warn_deprecated_instead_omitted():
    """When instead is omitted the message stays well-formed (no migration clause)."""
    with pytest.warns(DeprecationWarning) as record:
        warn_deprecated("thing", since="1.0", removed_in="2.0.0")
    msg = str(record[0].message)
    expected = "thing is deprecated since djust 1.0 and will be removed no earlier than 2.0.0."
    assert msg == expected
    assert "Use" not in msg


def test_warn_deprecated_instead_none_explicit():
    """Passing instead=None explicitly behaves the same as omitting it."""
    with pytest.warns(DeprecationWarning) as record:
        warn_deprecated("thing", since="1.0", removed_in="2.0.0", instead=None)
    assert "Use" not in str(record[0].message)


def test_warn_deprecated_stacklevel_points_at_caller():
    """The warning's filename is the caller's file, not _deprecation.py.

    Mirrors Action #1196 — verify the wiring, not just the method. We call
    the helper through an intermediate frame and ask the helper to skip it
    so the warning still points at *this* test file.
    """

    def _caller():
        # stacklevel=3: warnings.warn frame -> warn_deprecated -> _caller -> test
        warn_deprecated(
            "thing",
            since="1.0",
            removed_in="2.0.0",
            instead="other",
            stacklevel=3,
        )

    with pytest.warns(DeprecationWarning) as record:
        _caller()
    assert os.path.basename(record[0].filename) == "test_deprecation.py"


def test_warn_deprecated_default_stacklevel_points_at_direct_caller():
    """With the default stacklevel, a direct call points at the caller's file."""
    with pytest.warns(DeprecationWarning) as record:
        warn_deprecated(
            "thing",
            since="1.0",
            removed_in="2.0.0",
            instead="other",
        )
    assert os.path.basename(record[0].filename) == "test_deprecation.py"


def test_warn_deprecated_keyword_only_args():
    """since / removed_in / instead are keyword-only (positional call fails)."""
    with pytest.raises(TypeError):
        warn_deprecated("thing", "1.0", "2.0.0")  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Migration regression tests — the 3 fixed call sites
# ---------------------------------------------------------------------------


def test_event_alias_still_warns_with_concrete_version():
    """@event still emits a DeprecationWarning naming a concrete removal floor.

    Gate-off check: this assertion fails if the message reverts to the vague
    "a future release" text.
    """
    from djust.decorators import event

    def sample(self):  # pragma: no cover - body never executed
        pass

    with pytest.warns(DeprecationWarning) as record:
        event(sample)
    msg = str(record[0].message)
    assert "@event" in msg
    assert "1.1" in msg
    assert "@event_handler" in msg
    assert "future release" not in msg


def test_liveviewform_warns_with_corrected_version():
    """Subclassing LiveViewForm warns; the message no longer says "0.4".

    F1-regression test (Action #254 gate-off): reverting the forms.py edit
    makes this fail on the "0.4"-absent assertion.
    """
    from djust.forms import LiveViewForm

    with pytest.warns(DeprecationWarning) as record:

        class _MyForm(LiveViewForm):
            pass

    msg = str(record[0].message)
    assert "LiveViewForm" in msg
    assert "0.4" not in msg
    assert "1.1" in msg
    assert "django.forms.Form" in msg


def test_legacy_theming_warns_with_concrete_version():
    """Accessing the legacy THEMES dict warns with a concrete removal version."""
    from djust.theming.themes._legacy import THEMES

    with pytest.warns(DeprecationWarning) as record:
        _ = THEMES["material"]
    msg = str(record[0].message)
    assert "THEMES" in msg
    assert "1.1" in msg


def test_legacy_theming_get_theme_warns_with_concrete_version():
    """get_theme() still warns with a concrete removal version."""
    from djust.theming.themes._legacy import get_theme

    with pytest.warns(DeprecationWarning) as record:
        get_theme("material")
    msg = str(record[0].message)
    assert "get_theme" in msg
    assert "1.1" in msg


def test_legacy_theming_list_themes_warns_with_concrete_version():
    """list_themes() still warns with a concrete removal version."""
    from djust.theming.themes._legacy import list_themes

    with pytest.warns(DeprecationWarning) as record:
        list_themes()
    msg = str(record[0].message)
    assert "list_themes" in msg
    assert "1.1" in msg


def test_no_deprecation_warning_when_helper_unused():
    """Sanity: importing the helper alone emits nothing."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        import djust._deprecation  # noqa: F401  (re-import is a no-op, no warning)


# ---------------------------------------------------------------------------
# Per-site stacklevel regression tests — the warning must point at the USER's
# frame (this test file), not at a djust-internal module. These tests fail on
# a wrong stacklevel and pass once it is correct (Action #1196 — verify the
# wiring; Action #254 — gate-off self-test for each migrated site).
# ---------------------------------------------------------------------------

_THIS_FILE = "test_deprecation.py"


def test_event_alias_stacklevel_points_at_user_frame():
    """@event's warning filename is the caller's file, not decorators.py.

    Chain: warnings.warn -> warn_deprecated -> event -> THIS test (the user).
    Correct stacklevel is 3. A stacklevel of 2 makes the warning point at
    decorators.py — this assertion fails in that case.
    """
    from djust.decorators import event

    def sample(self):  # pragma: no cover - body never executed
        pass

    with pytest.warns(DeprecationWarning) as record:
        event(sample)  # <- this line is the user frame
    assert os.path.basename(record[0].filename) == _THIS_FILE, (
        f"@event warning points at {record[0].filename!r}; "
        "expected the caller's file (wrong stacklevel)"
    )


def test_liveviewform_stacklevel_points_at_user_frame():
    """LiveViewForm's warning filename is the user's file, not forms.py/widgets.py.

    The warning fires inside __init_subclass__, which runs within Django's
    DeclarativeFieldsMetaclass __new__ chain (two extra metaclass frames).
    Chain: warnings.warn -> warn_deprecated -> __init_subclass__ ->
    widgets.py metaclass __new__ -> forms.py metaclass __new__ ->
    user `class` statement. Correct stacklevel is 5. A stacklevel of 3
    makes the warning point at Django's widgets.py — this assertion fails.
    """
    from djust.forms import LiveViewForm

    with pytest.warns(DeprecationWarning) as record:

        class _MyForm(LiveViewForm):  # <- this `class` statement is the user frame
            pass

    assert os.path.basename(record[0].filename) == _THIS_FILE, (
        f"LiveViewForm warning points at {record[0].filename!r}; "
        "expected the caller's file (wrong stacklevel)"
    )


def test_legacy_theming_getitem_stacklevel_points_at_user_frame():
    """Legacy THEMES.__getitem__ warning points at the user's file, not _legacy.py.

    Chain: warnings.warn -> warn_deprecated -> _warn_legacy ->
    __getitem__ -> THIS test (the user). Correct stacklevel is 4.
    """
    from djust.theming.themes._legacy import THEMES

    with pytest.warns(DeprecationWarning) as record:
        _ = THEMES["material"]  # <- this line is the user frame
    assert os.path.basename(record[0].filename) == _THIS_FILE, (
        f"legacy THEMES warning points at {record[0].filename!r}; "
        "expected the caller's file (wrong stacklevel)"
    )


def test_legacy_theming_get_theme_stacklevel_points_at_user_frame():
    """Legacy get_theme() warning points at the user's file, not _legacy.py.

    Chain: warnings.warn -> warn_deprecated -> _warn_legacy ->
    get_theme -> THIS test (the user). Correct stacklevel is 4.
    """
    from djust.theming.themes._legacy import get_theme

    with pytest.warns(DeprecationWarning) as record:
        get_theme("material")  # <- this line is the user frame
    assert os.path.basename(record[0].filename) == _THIS_FILE, (
        f"legacy get_theme() warning points at {record[0].filename!r}; "
        "expected the caller's file (wrong stacklevel)"
    )
