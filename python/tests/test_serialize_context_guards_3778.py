"""#3778 — ``_rust.serialize_context`` must not run what a template may not.

``serialize_python_value`` (behind the public ``#[pyfunction]``
``_rust.serialize_context``) called ``render()`` on ANY object and four fixed
``get_*`` names on a model, with no ``alters_data`` / ``do_not_call_in_templates``
check, and built a model's dict with ``model_to_dict`` — which ships ``password``,
``is_superuser`` and ``is_staff`` of a ``User`` verbatim.

**Who calls it (measured).** Nothing in production. ``git grep serialize_context``
over ``python/djust``, ``examples``, ``benchmarks`` and ``scripts`` finds only the
Python helper of the same name in ``djust.template.serialization`` (a different
function: it passes models through). Every importer of ``djust._rust.serialize_context``
is a test, and a spy on ``_rust.serialize_context`` records zero calls across a
``DjustTemplateBackend`` render and a LiveView mount. So the exposure is a caller
who hands this function an object, not a template author — which is why the fix is
the conservative one (guard the entry point, keep the API) and not a deletion that
would be a public-surface decision (``_rust.pyi`` and ``docs/TYPE_STUBS.md`` list it).

**The rule** is the one the queryset serializer asks: ``template_call_guard`` for
every call (``do_not_call_in_templates`` first, then ``alters_data``), and the
serialization gate (``djust.optimization.codegen.emittable_names``) for every model
name. A refused ``render`` falls through to ``str()``; a refused ``get_*`` and a
gated field are absent from the dict.

Reproduction fidelity (#1650 / #1638): a REAL table and a callable that really
deletes rows, with the assertion on what is in the database afterwards, plus a
control that proves the detector sees the same callable when it is NOT marked.
"""

from __future__ import annotations

from typing import Any, Callable

import pytest
from django.contrib.auth.models import User
from django.test import override_settings

from djust import _rust

PW = "pbkdf2_sha256$SECRETHASH"

#: The four fixed names the model arm calls, in the order it calls them.
GET_NAMES = ["get_full_name", "get_short_name", "get_absolute_url", "get_username"]


@pytest.fixture
def user(db):
    return User.objects.create(
        username="alice", password=PW, is_superuser=True, is_staff=True, email="a@example.com"
    )


def _recorder(log: list[str], name: str, result: str, **markers: bool) -> Callable[[], str]:
    def method() -> str:
        log.append(name)
        return result

    for marker, on in markers.items():
        setattr(method, marker, on)
    return method


# ---------------------------------------------------------------------------
# render(): the component arm
# ---------------------------------------------------------------------------
class _Component:
    """Whatever carries a ``render`` — the arm keys on the name alone."""

    def __init__(self, log: list[str], **markers: bool) -> None:
        self.render = _recorder(log, "render", "<b>x</b>", **markers)

    def __str__(self) -> str:
        return "component-str"


class TestRenderIsHeldToTheCallGuard:
    def test_an_unmarked_render_is_still_called(self):
        """Control: the guard narrows the arm, it does not remove it."""
        log: list[str] = []
        out = _rust.serialize_context({"c": _Component(log)})
        assert out == {"c": {"render": "<b>x</b>"}}
        assert log == ["render"]

    @pytest.mark.parametrize("marker", ["alters_data", "do_not_call_in_templates"])
    def test_a_marked_render_is_not_called(self, marker):
        log: list[str] = []
        out = _rust.serialize_context({"c": _Component(log, **{marker: True})})
        assert log == [], f"render() ran although it carries {marker}"
        # Falls through to the generic arm, exactly as an uncallable would.
        assert out == {"c": "component-str"}

    def test_both_markers_is_refused_too(self):
        """``do_not_call_in_templates`` wins the guard's order ("use as is"); a
        serializer cannot use a callable as-is, so it is not called either."""
        log: list[str] = []
        _rust.serialize_context(
            {"c": _Component(log, alters_data=True, do_not_call_in_templates=True)}
        )
        assert log == []

    @pytest.mark.parametrize(
        "wrap",
        [
            pytest.param(lambda c: {"k": c}, id="dict"),
            pytest.param(lambda c: [c], id="list"),
            pytest.param(lambda c: (c,), id="tuple"),
            pytest.param(lambda c: {"k": [{"k": (c,)}]}, id="nested"),
        ],
    )
    def test_the_guard_holds_at_every_depth(self, wrap):
        """The recursion is one function; each container arm reaches it."""
        log: list[str] = []
        _rust.serialize_context({"c": wrap(_Component(log, alters_data=True))})
        assert log == []

    def test_a_render_that_deletes_rows_does_not_delete_them(self, user):
        """Against the real table, not a stub that records a call."""
        marked = _Component([], alters_data=True)
        marked.render = lambda: User.objects.all().delete()  # type: ignore[assignment]
        marked.render.alters_data = True  # type: ignore[attr-defined]
        _rust.serialize_context({"c": marked})
        assert User.objects.count() == 1

    def test_the_detector_sees_the_same_render_when_unmarked(self, user):
        """Gate-off sibling inside the suite: an unmarked deleting ``render`` DOES
        delete, so the assertion above can fail."""
        unmarked = _Component([])
        unmarked.render = lambda: str(User.objects.all().delete())  # type: ignore[assignment]
        _rust.serialize_context({"c": unmarked})
        assert User.objects.count() == 0


# ---------------------------------------------------------------------------
# get_*: the model arm
# ---------------------------------------------------------------------------
class TestModelMethodsAreHeldToTheCallGuard:
    def _install(self, user: User, log: list[str], **per_name: dict[str, bool]) -> None:
        for name in GET_NAMES:
            setattr(user, name, _recorder(log, name, f"<{name}>", **per_name.get(name, {})))

    def test_unmarked_methods_are_called_and_shipped(self, user):
        """Control, and the pin on the arm's output shape: strings under the name."""
        log: list[str] = []
        self._install(user, log)
        out = _rust.serialize_context({"u": user})["u"]
        assert log == GET_NAMES
        for name in GET_NAMES:
            assert out[name] == f"<{name}>"

    @pytest.mark.parametrize("marker", ["alters_data", "do_not_call_in_templates"])
    @pytest.mark.parametrize("name", GET_NAMES)
    def test_each_fixed_name_is_refused_when_marked(self, user, name, marker):
        """N sites, N tests (#1104): one per name, per marker."""
        log: list[str] = []
        self._install(user, log, **{name: {marker: True}})
        out = _rust.serialize_context({"u": user})["u"]
        assert name not in log, f"{name}() ran although it carries {marker}"
        assert name not in out, "a refused method is absent, not an empty string"
        # The other three are untouched: the guard is per callable.
        assert sorted(log) == sorted(n for n in GET_NAMES if n != name)

    def test_a_get_that_deletes_rows_does_not_delete_them(self, user):
        def get_username() -> str:
            User.objects.all().delete()
            return "gone"

        get_username.alters_data = True  # type: ignore[attr-defined]
        user.get_username = get_username  # type: ignore[method-assign]
        _rust.serialize_context({"u": user})
        assert User.objects.count() == 1

    def test_the_detector_sees_the_same_get_when_unmarked(self, user):
        def get_username() -> str:
            User.objects.all().delete()
            return "gone"

        user.get_username = get_username  # type: ignore[method-assign]
        _rust.serialize_context({"u": user})
        assert User.objects.count() == 0


# ---------------------------------------------------------------------------
# The floor: model_to_dict used to ship password / is_superuser / is_staff
# ---------------------------------------------------------------------------
class TestModelFieldsAreHeldToTheSerializationFloor:
    def test_the_hardcoded_floor_fields_are_not_shipped(self, user):
        out = _rust.serialize_context({"u": user})["u"]
        assert out["username"] == "alice", "an ordinary field must survive"
        for name in ("password", "is_superuser", "is_staff"):
            assert name not in out, f"{name} reached the output"
        assert PW not in repr(out)

    def test_the_floor_holds_for_a_model_nested_in_a_container(self, user):
        out = _rust.serialize_context(
            {"a": [user], "b": {"k": user}, "c": (user,), "d": {"k": [{"k": user}]}}
        )
        flat = repr(out)
        assert PW not in flat
        assert "is_superuser" not in flat and "is_staff" not in flat

    def test_a_configured_sensitive_field_is_not_shipped(self, user):
        with override_settings(DJUST_SENSITIVE_FIELDS=["email"]):
            out = _rust.serialize_context({"u": user})["u"]
        assert "email" not in out
        assert out["username"] == "alice"

    def test_the_setting_is_not_sticky(self, user):
        """Control for the test above: without the setting, email IS shipped, so
        the denial came from the gate and not from some other path."""
        out = _rust.serialize_context({"u": user})["u"]
        assert out["email"] == "a@example.com"

    def test_a_per_model_allowlist_is_honoured(self, user, monkeypatch):
        monkeypatch.setattr(User, "djust_serializable_fields", ["username"], raising=False)
        out = _rust.serialize_context({"u": user})["u"]
        assert out["username"] == "alice"
        assert "email" not in out and "first_name" not in out

    def test_a_per_model_exclusion_is_honoured(self, user, monkeypatch):
        monkeypatch.setattr(User, "djust_exclude_fields", ["first_name"], raising=False)
        out = _rust.serialize_context({"u": user})["u"]
        assert "first_name" not in out
        assert out["username"] == "alice"

    def test_a_method_is_dropped_when_the_gate_refuses_its_name(self, user, monkeypatch):
        """The allowlist above omits every get_*: the gate decides methods too,
        and a refused one is never CALLED, not merely hidden."""
        log: list[str] = []
        for name in GET_NAMES:
            setattr(user, name, _recorder(log, name, "x"))
        monkeypatch.setattr(User, "djust_serializable_fields", ["username"], raising=False)
        out = _rust.serialize_context({"u": user})["u"]
        assert log == []
        assert not any(name in out for name in GET_NAMES)

    def test_an_unresolvable_gate_fails_closed(self, user, monkeypatch):
        """If the gate cannot answer, the model is NOT shipped as a dict: it falls
        through to ``str()`` (the queryset serializer's own rule)."""
        import djust.optimization.codegen as codegen

        def boom(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("gate unavailable")

        monkeypatch.setattr(codegen, "emittable_names", boom)
        out = _rust.serialize_context({"u": user})["u"]
        assert out == str(user)
        assert PW not in repr(out)


# ---------------------------------------------------------------------------
# The stub documented a parameter the function never took
# ---------------------------------------------------------------------------
class TestTheStubMatchesTheFunction:
    def test_the_stub_arity_is_the_runtime_arity(self):
        """``_rust.pyi`` declared ``serialize_context(context, field_paths)``; the
        function takes ``context`` alone, so nobody calling it as documented could
        have got an answer — one more sign nothing real calls it. Derive both
        sides rather than restating a count."""
        import ast
        from pathlib import Path

        import djust

        stub = ast.parse((Path(djust.__file__).parent / "_rust.pyi").read_text())
        (fn,) = [
            n for n in stub.body if isinstance(n, ast.FunctionDef) and n.name == "serialize_context"
        ]
        declared = [a.arg for a in fn.args.args]

        assert _rust.serialize_context({}) == {}
        with pytest.raises(TypeError):
            _rust.serialize_context({}, {})
        assert declared == ["context"]
