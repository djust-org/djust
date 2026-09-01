"""#2510 — a PyO3 panic ("dictionary changed size during iteration") on any
LiveView render when `TEMPLATES.OPTIONS.context_processors` has `request`
but not `auth` — a real, reported 500-on-every-page regression.

Root cause, confirmed by reading `crates/djust_core/src/lib.rs` (not
assumed): two arms of `impl FromPyObject for Value` hold a LIVE PyO3
iterator directly over a Python dict and, for each value, recursively call
`.extract::<Value>()` — which can run arbitrary Python code (any dunder
check on an unresolved `SimpleLazyObject`, e.g. `__bool__`, triggers Django's
lazy-object `_setup()`). If that code mutates the SAME dict being iterated
— which is exactly what `AuthenticationMiddleware.get_user`'s
`request._cached_user = auth.get_user(request)` does to `request.__dict__`
— the dict resizes mid-iteration and PyO3 panics.

  1. `public_dict_attrs` (the `__dict__` bulk-dump carrier, reached by any
     "truthy, non-iterable object with public attributes" — a real
     `HttpRequest` is exactly this shape).
  2. The genuine-`PyDict` arm a few lines above it (`Value::Object` for an
     actual Python dict whose VALUES include something reentrant).

Neither is Django- or `HttpRequest`-specific: any object whose attribute
access can mutate its own `__dict__` on first touch — which is precisely
what `functools.cached_property` does — hits the same class.

Minimal reproduction needs NO Django settings, no WSGI, no `django.test`
client — a real HTTP dispatch reproduces it, but `LiveViewTestClient` and
`django.test.Client` did not in manual investigation (the exact reason was
not resolved and does not matter: the bug is reachable and confirmed at the
Rust boundary directly, which is what these tests exercise).
"""

import functools

from djust import _rust


class _LazyLike:
    """Mimics `SimpleLazyObject`: first-touch resolution mutates the PARENT
    object's `__dict__`, exactly like `AuthenticationMiddleware.get_user`'s
    `request._cached_user = auth.get_user(request)`."""

    def __init__(self, parent):
        self._parent = parent
        self._resolved = False

    def __bool__(self):
        if not self._resolved:
            self._resolved = True
            self._parent.newly_added_key = "mutated mid-iteration"
        return True


class _RequestLike:
    """A real `HttpRequest`'s shape: several public attrs, one of them
    unresolved-lazy, sitting in `__dict__` before rendering ever starts —
    exactly as `AuthenticationMiddleware.process_request` leaves `request`
    when nothing has forced `request.user` yet."""

    def __init__(self):
        self.path = "/"
        self.method = "GET"
        self.user = _LazyLike(self)
        self.other_attr = "z"


class TestPublicDictAttrsArm:
    """`public_dict_attrs` — the `__dict__` bulk-dump carrier."""

    def test_a_lazy_attribute_that_mutates_its_parent_does_not_panic(self):
        """The template never references `.user` — `public_dict_attrs`
        dumps the WHOLE `__dict__` regardless of which attribute the
        template needs, so the hazard fires even for `{{ c.path }}`."""
        req = _RequestLike()
        html = _rust.render_template("{{ c.path }}", {"c": req})
        assert html == "/"

    def test_the_mutation_still_happens_but_does_not_corrupt_the_result(self):
        req = _RequestLike()
        _rust.render_template("{{ c.path }}", {"c": req})
        assert req.newly_added_key == "mutated mid-iteration"

    def test_cached_property_is_not_actually_the_same_hazard(self):
        """Checked, not assumed (#1468): `cached_property.__get__` writes
        `instance.__dict__[name]` only on ATTRIBUTE ACCESS
        (`obj.computed`). `public_dict_attrs` iterates `obj.__dict__`
        DIRECTLY and never calls `getattr(obj, name)` for a name that is
        not already a key — so an un-accessed `cached_property` is simply
        absent from the dict being walked, and this case cannot panic
        regardless of the fix. Kept as a documented negative, since the
        plan's own text originally claimed this was an equivalent trigger
        and it is not.
        """

        class WithCachedProperty:
            def __init__(self):
                self.plain_attr = "x"

            @functools.cached_property
            def computed(self):
                return "computed-value"

        obj = WithCachedProperty()
        assert "computed" not in obj.__dict__, "premise: not yet cached"
        html = _rust.render_template("{{ c.plain_attr }}", {"c": obj})
        assert html == "x"


class _LazyLikeDictValue:
    """`_LazyLike`'s dict-shaped twin: the parent here is a `dict`, which
    has no attribute-assignment protocol, so the mutation must be a
    `__setitem__` call, not an attribute set.

    Writing this as a SEPARATE class rather than branching inside
    `_LazyLike` is deliberate: a version of this test that reused
    `_LazyLike` unchanged (`self._parent.newly_added_key = ...` against a
    `dict` parent) raised `AttributeError` inside `__bool__`, which the
    Rust side's `if let Ok(b) = ob.extract::<bool>()` silently swallows —
    so the mutation never actually happened and the test passed for the
    wrong reason (#1468). Caught by checking why an expected-panic case
    came back green instead of trusting it.
    """

    def __init__(self, parent):
        self._parent = parent
        self._resolved = False

    def __bool__(self):
        if not self._resolved:
            self._resolved = True
            self._parent["newly_added_key"] = "mutated mid-iteration"
        return True


class TestTheGenuinePyDictArm:
    """The `Value::Object` map-building arm for an actual Python `dict`,
    a few lines above `public_dict_attrs` in the same match chain — a
    structurally identical hazard on a different carrier."""

    def test_a_dict_value_that_mutates_the_dict_during_conversion_does_not_panic(self):
        d: dict = {}
        d["lazy"] = _LazyLikeDictValue(d)
        d["other"] = "y"
        html = _rust.render_template("{{ c.other }}", {"c": d})
        assert html == "y"

    def test_the_mutation_actually_happened(self):
        """Without this, the test above could pass because the mutation
        silently failed (exactly the bug this class's docstring
        describes), not because the fix handled it."""
        d: dict = {}
        d["lazy"] = _LazyLikeDictValue(d)
        d["other"] = "y"
        _rust.render_template("{{ c.other }}", {"c": d})
        assert d.get("newly_added_key") == "mutated mid-iteration"


class TestReentrancyDoesNotOverreach:
    """The fix's scope is "don't panic on THIS dict resizing mid-iteration",
    not "refuse all reentrancy" — an attribute mutating some OTHER,
    unrelated dict must keep working exactly as it does today."""

    def test_mutating_an_unrelated_dict_is_unaffected(self):
        unrelated: dict = {}

        class MutatesElsewhere:
            def __init__(self):
                self.a = "a-value"
                self.b = _Toucher(unrelated)

        class _Toucher:
            def __init__(self, target):
                self._target = target

            def __bool__(self):
                self._target["touched"] = True
                return True

        obj = MutatesElsewhere()
        html = _rust.render_template("{{ c.a }}", {"c": obj})
        assert html == "a-value"
        assert unrelated == {"touched": True}
