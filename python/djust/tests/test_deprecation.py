"""Unit tests for the internal ``djust._deprecation`` helper.

Covers the standardized ``warn_deprecated`` helper (message format, warning
category, ``stacklevel`` correctness, optional kwargs). The call-site tests
for ``@event``, ``LiveViewForm`` and the legacy theming module went with
those APIs in 2.0.
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


def test_no_deprecation_warning_when_helper_unused():
    """Sanity: importing the helper alone emits nothing."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        import djust._deprecation  # noqa: F401  (re-import is a no-op, no warning)
