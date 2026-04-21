"""
Tests for PyO3 `getattr` fallback on model attribute access.

The Rust template engine now falls back to Python `getattr` when
a nested key like `{{ user.username }}` isn't present in the JSON-
serialized state. Covers Django-model-style objects, nested access,
missing attributes, property exceptions, and mixed dict+model
contexts.
"""

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


def test_property_that_raises_handled_gracefully():
    """A property raising during access must not crash the render."""

    class _Bad:
        @property
        def broken(self):
            raise RuntimeError("kaboom")

    html = _render("before[{{ obj.broken }}]after", raw={"obj": _Bad()})
    assert html == "before[]after"


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
