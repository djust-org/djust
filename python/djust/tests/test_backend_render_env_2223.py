"""The Django-template backend is a render path too, and it was unwired (#2223).

#2209 (timezone) and #2221 (number format) each pushed their setting into Rust
from two Python render paths — ``RustBridgeMixin`` and ``simple_live_view`` —
and a structural test pinned that caller set. The set was wrong: a plain Django
template rendered through ``DjustTemplateBackend`` goes through
``template/rendering.py`` instead, which pushed nothing.

So the same page could render a number correctly inside a LiveView and
incorrectly in a template beside it, with nothing raised.

Why the gap survived two PRs
----------------------------
The thread-local persists, so a worker that has already served a LiveView
render carries a correct environment into any later backend render on the same
thread. In production that hides the bug most of the time; it shows on a thread
that has not — the first request a worker handles, or a process whose traffic
is all plain templates.

Which of the cases below actually detect it, measured rather than assumed
-------------------------------------------------------------------------
Gating the fix off:

* ``test_the_backend_localizes_numbers_and_timestamps`` — FAILS. The real
  detector.
* ``test_the_backend_re_reads_the_environment_between_renders`` — FAILS.
* ``test_the_backend_leaves_values_alone_when_the_settings_say_to`` — **passes
  either way**, and cannot detect this bug by construction: with ``USE_TZ``
  off and no thousand separator, doing nothing produces the right answer. It
  is kept as a guard against an UNCONDITIONAL conversion, which is a different
  defect, and is labelled so nobody reads it as coverage of #2223.

And ``_fresh_thread`` turns out **not** to be load-bearing either: gating the
fix off with the fixture neutered still fails, because ``override_settings``
moves the timezone and language away from whatever a previous test pushed, so
a stale environment does not match anyway. It is kept for order-independence —
worth having, but it is hygiene, not the thing that makes these tests work.
Recorded because the first draft of this docstring claimed the opposite, and a
guard nobody has measured is how #1859 happens.
"""

import datetime as dt

import pytest
from django.test import override_settings
from django.utils import translation

AWARE = dt.datetime(2026, 8, 22, 23, 30, tzinfo=dt.timezone.utc)


@pytest.fixture
def _fresh_thread():
    """Clear the Rust thread-locals, modelling a worker that has served nothing.

    Hygiene, not a detector — see the module docstring. Gating the fix off with
    this neutered still fails, because ``override_settings`` moves the timezone
    and language away from whatever a previous test pushed. Kept so these cases
    do not depend on execution order.
    """
    from djust._rust import set_active_timezone, set_number_format

    set_active_timezone(None)
    set_number_format(None)
    yield
    set_active_timezone(None)
    set_number_format(None)


def _djust_engine():
    """Build the backend directly rather than reading it out of ``TEMPLATES``.

    The suite's settings do not configure a ``DjustTemplateBackend``, and a
    lookup that ``pytest.skip``s when it is absent is a test that silently does
    nothing — which is how this path went unwired through two PRs in the first
    place. Constructing it here makes the case unconditional.
    """
    from djust.template.backend import DjustTemplateBackend

    return DjustTemplateBackend({"NAME": "djust", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}})


@override_settings(
    USE_TZ=True,
    TIME_ZONE="America/New_York",
    USE_I18N=True,
    USE_THOUSAND_SEPARATOR=True,
    LANGUAGE_CODE="en-us",
)
def test_the_backend_localizes_numbers_and_timestamps(_fresh_thread):
    # Django renders '1,234,567|19:30'. Pre-#2223 this path gave
    # '1234567|23:30' — both settings ignored, on a genuine top-level render.
    engine = _djust_engine()
    out = engine.from_string('{{ n }}|{{ d|date:"H:i" }}').render({"n": 1234567, "d": AWARE})
    assert out == "1,234,567|19:30", (
        "the Django-template backend must honour TIME_ZONE and the active "
        f"locale, like the LiveView paths do. Got {out!r}"
    )


@override_settings(
    USE_TZ=True,
    TIME_ZONE="America/New_York",
    USE_I18N=True,
    USE_THOUSAND_SEPARATOR=True,
    LANGUAGE_CODE="en-us",
)
def test_the_backend_re_reads_the_environment_between_renders(_fresh_thread):
    # Same reasoning as the LiveView paths: `activate()` is per-request, so a
    # value captured once would pin the first render's environment.
    engine = _djust_engine()
    tpl = engine.from_string("{{ n }}")
    assert tpl.render({"n": 1234567}) == "1,234,567"
    translation.activate("de")
    try:
        assert tpl.render({"n": 1234567}) == "1.234.567"
    finally:
        translation.deactivate()


@override_settings(USE_TZ=False, USE_I18N=True, USE_THOUSAND_SEPARATOR=False)
def test_the_backend_leaves_values_alone_when_the_settings_say_to(_fresh_thread):
    # NOT a detector for #2223 — verified: it passes with the fix gated off,
    # because doing nothing is the right answer when USE_TZ is off and there is
    # no thousand separator. It guards the opposite defect: a push that
    # converted unconditionally instead of handing over an environment.
    engine = _djust_engine()
    out = engine.from_string('{{ n }}|{{ d|date:"H:i" }}').render({"n": 1234567, "d": AWARE})
    assert out == "1234567|23:30"
