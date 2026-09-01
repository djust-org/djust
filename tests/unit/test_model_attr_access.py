"""
Tests for PyO3 `getattr` fallback on model attribute access.

The Rust template engine now falls back to Python `getattr` when
a nested key like `{{ user.username }}` isn't present in the JSON-
serialized state. Covers Django-model-style objects, nested access,
missing attributes, property exceptions, and mixed dict+model
contexts.
"""

import pytest

from djust._rust import RustLiveView


class _DummyProfile:
    """Plain Python stand-in for a Django ``Profile`` instance."""

    def __init__(self, bio: str, followers: int = 0) -> None:
        self.bio = bio
        self.followers = followers


class _DummyUser:
    """Plain Python stand-in for a Django ``User`` instance.

    Using a vanilla class rather than Django's ORM keeps the test
    hermetic (no DB setup) while exercising the same ``getattr``
    fallback path.
    """

    def __init__(
        self,
        username: str,
        email: str = "",
        profile: _DummyProfile | None = None,
    ) -> None:
        self.username = username
        self.email = email
        if profile is not None:
            self.profile = profile

    def __str__(self) -> str:
        return f"<User:{self.username}>"


def _render(template: str, *, state=None, raw=None) -> str:
    """Helper: build a RustLiveView with both JSON state and raw sidecar."""
    view = RustLiveView(template)
    view.update_state(state or {})
    if raw:
        view.set_raw_py_values(raw)
    return view.render()


def test_model_single_attribute():
    """`{{ user.username }}` resolves via getattr when user is a model."""
    user = _DummyUser("alice")
    html = _render("{{ user.username }}", raw={"user": user})
    assert html == "alice"


def test_model_nested_attribute():
    """Two-level attr walk: `{{ user.profile.bio }}`."""
    user = _DummyUser("alice", profile=_DummyProfile("Hello world"))
    html = _render("{{ user.profile.bio }}", raw={"user": user})
    assert html == "Hello world"


def test_missing_attribute_renders_empty():
    """Undefined attributes yield empty output rather than crashing."""
    user = _DummyUser("alice")
    html = _render("[{{ user.nonexistent }}]", raw={"user": user})
    assert html == "[]"


def test_property_that_raises_propagates_like_django():
    """A property raising during access surfaces the error (#2506).

    This asserted `== "before[]after"` until #2506. The premise it stated — "a
    property raising during access must not crash the render" — was never a
    property of the engine, only of the property half of it. Measured on the
    pre-#2506 build:

        {{ obj.broken_method }}    -> RuntimeError propagates
        {{ obj.broken_property }}  -> renders ""

    because the walk's `getattr` step discarded ANY exception while
    `Context::maybe_call` (ADR-024) has always propagated one raised INSIDE a
    nullary method. So the two halves of the same lookup disagreed, and this
    test pinned the disagreeing half.

    Django catches `(TypeError, AttributeError)` at that step and nothing else
    — and even then re-raises when the name is in `dir(current)`, its "raised
    by a @property" branch — so both halves now propagate, as Django does.
    The security reading is the reason it is a fix rather than a preference:
    an attribute implementing an access check by raising previously failed
    silently, and for a raise-is-deny check that is silently OPEN.
    """

    class _Bad:
        @property
        def broken(self):
            raise RuntimeError("kaboom")

        def broken_method(self):
            raise RuntimeError("kaboom-method")

    with pytest.raises(RuntimeError, match="kaboom"):
        _render("before[{{ obj.broken }}]after", raw={"obj": _Bad()})

    # The half that already behaved this way, asserted alongside so the
    # convergence is visible rather than implied.
    with pytest.raises(RuntimeError, match="kaboom-method"):
        _render("before[{{ obj.broken_method }}]after", raw={"obj": _Bad()})


def test_an_absent_attribute_still_renders_empty():
    """The narrowing's other half: an ordinary miss is still Django's
    `VariableDoesNotExist` and still renders `string_if_invalid` (`""`).
    Without this, a change that propagated everything would pass the test
    above and break every template with a missing name."""

    class _Plain:
        present = "here"

    assert _render("[{{ obj.absent }}]", raw={"obj": _Plain()}) == "[]"
    assert _render("[{{ obj.present }}]", raw={"obj": _Plain()}) == "[here]"


def test_mix_dict_and_model_in_same_context():
    """Dict values still resolve via the fast JSON path; models fall back."""
    user = _DummyUser("alice")
    html = _render(
        "{{ config.title }}/{{ user.username }}",
        state={"config": {"title": "Demo"}},
        raw={"user": user},
    )
    assert html == "Demo/alice"


def test_model_str_override_used_for_attr_less_object():
    """A raw object with no usable `__dict__` attributes falls back to ``__str__``.

    Objects that expose nothing via `__dict__` extraction (e.g. use
    ``__slots__`` and nothing else) drop through the FromPyObject
    chain to ``ob.str()``, returning the custom ``__str__`` output.
    """

    class _Opaque:
        __slots__ = ()

        def __str__(self) -> str:
            return "<opaque>"

    html = _render("{{ obj }}", raw={"obj": _Opaque()})
    # HTML-escaped because we're in text context
    assert html == "&lt;opaque&gt;"


def test_getattr_on_dict_backed_entry():
    """If the key is already a dict in JSON state, no getattr is attempted."""
    # `user` is in the JSON state as a dict — `{{ user.username }}`
    # should resolve via the object path, not attempt getattr.
    html = _render(
        "{{ user.username }}",
        state={"user": {"username": "bob"}},
    )
    assert html == "bob"


def test_setting_raw_values_to_empty_clears_sidecar():
    """An empty dict passed to set_raw_py_values clears prior entries."""
    user = _DummyUser("alice")
    view = RustLiveView("{{ user.username }}")
    view.set_raw_py_values({"user": user})
    assert view.render() == "alice"
    # Clear the sidecar
    view.set_raw_py_values({})
    # `user` no longer resolves — renders empty
    assert view.render() == ""
