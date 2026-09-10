"""#2741: the datetime family DOES carry a live handle — pinned.

The ``walk_from_handle``-first routing comment in
``crates/djust_core/src/context.rs`` claimed the whole datetime family never
carries a live handle. False: ``django_json_encoded`` (``lib.rs``) attaches
``live: Some(..)`` for ``datetime`` / ``date`` / ``time`` / ``timedelta``
unconditionally, unlike ``opaque_value``, whose handle is flag-gated. #2743
reworded the comment; ``docs/architecture/VALUE_BOUNDARY.md`` row I11 recorded
the invariant as "no test", which is how the false claim survived (#1867).

Why Python and not a ``crates/djust_core/tests`` pyo3 test: ``django_json_encoded``
imports ``django.core.serializers.json`` to build its type table, and no Rust
test in this repo imports Django on the embedded interpreter (CI's
``rust-tests`` venv is not on the embedded ``sys.path``) — a first version of
this pin returned ``None`` there and went red. A pin that cannot run on CI is
decorative (#1859), so it lives here, against the public render path.

How the handle is isolated: ``resolution`` / ``max`` / ``min`` are in NEITHER
``ENCODED_ATTR_NAMES`` nor ``ENCODED_CALL_NAMES`` (their values are themselves
temporal, so collecting them would not terminate — see ``Encoded::attrs``), and
``{% with q=xs|first %}`` is the one binding shape the by-name sidecar cannot
reach through ``Context::aliases`` (an alias is registered only over an
UNFILTERED operand; VALUE_BOUNDARY.md §6.2). So through that binding, ONLY the
handle can answer ``q.resolution`` — and it does, byte-for-byte with Django,
under the shipped default. ``year`` (in the name table) is the control: it
renders under BOTH flag states, proving the flag-off blank on ``resolution`` is
the missing handle walk, not a broken render.

Refs #2741, #2743, #1867, #1859.
"""

from __future__ import annotations

import datetime as _dt

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402

from adr027_flag import resolve_lazy, shipped_default  # noqa: E402

from djust import _rust  # noqa: E402

# The isolating binding, one live-only name and one name-table control each.
# `min` is deliberately covered too: `datetime.min.min is datetime.min` is the
# non-termination that keeps these out of the attrs table.
TEMPORAL = [
    pytest.param(_dt.datetime(2026, 3, 4, 5, 6, 7), "resolution", "year", id="datetime"),
    pytest.param(_dt.date(2026, 3, 4), "max", "year", id="date"),
    pytest.param(_dt.time(5, 6, 7), "max", "hour", id="time"),
    pytest.param(_dt.timedelta(days=1, seconds=2), "min", "days", id="timedelta"),
]


def _source(live_only: str, control: str) -> str:
    return (
        "{% with q=xs|first %}" + f"{{{{ q.{live_only} }}}}|{{{{ q.{control} }}}}" + "{% endwith %}"
    )


def _django(source: str, value) -> str:
    return DjangoTemplate(source).render(DjangoContext({"xs": [value]}))


def _djust(source: str, value) -> str:
    return _rust.render_template(source, {"xs": [value]})


def test_the_shipped_default_is_lazy_on():
    """The comment's claim is about the default; make sure the test's 'default'
    IS the shipped one rather than a literal (#1200)."""
    assert shipped_default() is True


@pytest.mark.parametrize("value, live_only, control", TEMPORAL)
def test_a_temporal_value_carries_a_live_handle_under_the_default(value, live_only, control):
    source = _source(live_only, control)
    expected = _django(source, value)
    live_expected, control_expected = expected.split("|")
    assert live_expected, f"Django must render {live_only} for this fixture to isolate anything"

    with resolve_lazy(shipped_default()):
        assert _djust(source, value) == expected, (
            f"{type(value).__name__}.{live_only} through the isolating binding is answerable "
            f"ONLY by the live handle `django_json_encoded` attaches (#2741); a blank here "
            f"means the datetime family no longer carries one"
        )


@pytest.mark.parametrize("value, live_only, control", TEMPORAL)
def test_the_handle_walk_is_what_answers_it(value, live_only, control):
    """Gate-off sibling, in-suite: with the flag OFF the handle is never walked,
    so the live-only name goes blank while the name-table control still
    renders. Proves the test above is reading the handle and not `attrs`."""
    source = _source(live_only, control)
    _, control_expected = _django(source, value).split("|")

    with resolve_lazy(False):
        assert _djust(source, value) == f"|{control_expected}"
