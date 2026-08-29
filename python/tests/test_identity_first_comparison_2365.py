"""Python's container comparison is identity-first; djust's `Value` has no identity (#2365).

`list.__eq__` compares elements with ``x is y or x == y``, and
``list.__contains__`` is ``any(x is e or x == e)``. For every ordinary value
that shortcut is a no-op — it only becomes observable for a **NaN**, which is
not equal to itself, so whether two containers compare equal depends on whether
they hold the *same object*:

    n = float("nan")
    [n] == [n]                        # True  -- the identity shortcut fires
    [float("nan")] == [float("nan")]  # False -- distinct objects
    n == n                            # False -- no shortcut for a bare scalar

**djust cannot express this and the gap is structural, not an oversight.** A
float crossing the PyO3 boundary becomes an `f64`; `Value` carries no identity,
and could not carry a meaningful one — the same context serialised to msgpack
and restored would have different object identities on the far side, so a
LiveView would answer differently before and after a reconnect for the same
data. Modelling it faithfully means adding identity to every `Value` and
accepting that it is unstable across the wire, which is a worse property than
the divergence.

So this is CLOSED as an accepted divergence rather than fixed, and these tests
exist so it stays a decision rather than becoming folklore. **If a future change
gives `Value` a stable identity, `test_the_aliased_case_still_diverges` goes red
— that is the signal to reopen #2365 and delete this file, not to adjust the
assertion.**

Scope note: the *distinct*-object case already agrees with Django, and the bare
scalar case agrees too (#2349 fixed non-finite scalar comparison). Only the
aliased-container case diverges, which is why the pin asserts all three — a test
that checked only the divergent one could not tell a real fix from a regression
in the other two.
"""

from __future__ import annotations

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402

from djust import _rust  # noqa: E402

EQ = "{% if a == b %}same{% else %}diff{% endif %}"


def _both(source: str, ctx: dict) -> tuple[str, str]:
    return _rust.render_template(source, ctx), DjangoTemplate(source).render(DjangoContext(ctx))


def test_the_aliased_case_still_diverges() -> None:
    """The accepted divergence. Red here means `Value` gained an identity."""
    nan = float("nan")
    aliased = [nan]
    mine, django = _both(EQ, {"a": aliased, "b": aliased})

    assert django == "same", "Django stopped using the identity shortcut"
    assert mine == "diff", (
        "djust now agrees with Django on an aliased NaN container. If `Value` "
        "gained a stable identity, reopen #2365 and delete this file rather "
        "than relaxing this assertion."
    )


def test_distinct_nan_containers_agree() -> None:
    """No identity shortcut applies, so both engines answer False."""
    mine, django = _both(EQ, {"a": [float("nan")], "b": [float("nan")]})
    assert mine == django == "diff"


def test_a_bare_nan_scalar_agrees() -> None:
    """Fixed in #2349 — there is no shortcut for a scalar, so both say False."""
    nan = float("nan")
    mine, django = _both(EQ, {"a": nan, "b": nan})
    assert mine == django == "diff"


def test_an_aliased_ordinary_container_agrees() -> None:
    """The shortcut is unobservable for anything that equals itself."""
    aliased = [1, "x"]
    mine, django = _both(EQ, {"a": aliased, "b": aliased})
    assert mine == django == "same"
