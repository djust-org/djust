"""#2614 — a ``@property`` that shadows a sensitive model-method name must be
refused by BOTH serialization channels, from ONE chokepoint.

Before the fix the two channels disagreed (#1646 shape):

- **eager** (``DjangoJSONEncoder._add_property_values``) consulted only the
  field floor (``_field_is_serializable``) — so a property named
  ``get_session_auth_hash`` (or ``get_all_permissions``, ``get_next_by_x``,
  or any ``_``-prefixed name) was serialized into the model dict that rides
  the HTTP GET context, the WS mount/patch frames and the state snapshot;
- **sidecar** (``_SidecarModelProxy.__getattr__``) refused the same name.

The permissive channel wins wherever the eager dict answers first — which is
every ``{{ m.<name> }}`` the template names, because the Rust engine reads the
eager dict BEFORE it falls back to the sidecar. So the sidecar's refusal was
moot for a shadowing property.

The fix routes every per-attribute decision — the field loop, the ``get_*``
method loop, the ``@property`` loop and the sidecar proxy — through ONE
authority, ``DjangoJSONEncoder._attr_is_serializable``. This file pins:

- the parity matrix (every floor name, every sensitive-method name, both
  prefixes, a ``_``-prefixed name, and the exact-match variants) against BOTH
  channels;
- each channel independently (so gating one off fails its own test, #1468);
- a real LiveView GET and a real ``WebsocketCommunicator`` mount + event;
- the structural pin that every site calls the chokepoint (#1125).
"""

from __future__ import annotations

import ast
import inspect
import re

import pytest
from asgiref.sync import sync_to_async
from django.db import models

from djust import LiveView
from djust.decorators import event_handler
from djust.serialization import (
    _SENSITIVE_MODEL_METHOD_PREFIXES,
    _SENSITIVE_MODEL_METHODS,
    DjangoJSONEncoder,
    _SidecarModelProxy,
    normalize_django_value,
)

LEAK = "LEAK-2614"

# Names the floor / sensitive-method set must refuse on BOTH channels.
REFUSED_NAMES = sorted(
    set(_SENSITIVE_MODEL_METHODS)
    | {p + "created" for p in _SENSITIVE_MODEL_METHOD_PREFIXES}
    | {"password", "is_staff", "is_superuser"}
    | {"_secret"}
)

# Exact-match variants (#1825 rule — probe the alternate spellings). The floor
# is documented name-EXACT, so these are NOT refused; what this file pins is
# that both channels give the SAME answer for them (no drift), and that the
# refused set above stays refused whatever the casing next door.
VARIANT_NAMES = ["Password", "PASSWORD", "pass_word", "get_Session_auth_hash", "is_staff_member"]

ALL_NAMES = REFUSED_NAMES + VARIANT_NAMES

# The Rust parser refuses ``{{ m._secret }}`` at parse time (Django parity), so
# the real-render tests reference every name EXCEPT the ``_``-prefixed one —
# that one is pinned by the unit/parity classes above.
TEMPLATE_NAMES = [n for n in ALL_NAMES if not n.startswith("_")]


def _prop(name):
    return property(lambda self, _n=name: f"{LEAK}:{_n}")


_attrs = {
    "__module__": __name__,
    "label": models.CharField(max_length=32, default=""),
    "__str__": lambda self: f"shadow({self.pk})",
    "Meta": type("Meta", (), {"app_label": "tests"}),
}
for _n in ALL_NAMES:
    _attrs[_n] = _prop(_n)

# Django refuses a concrete FIELD named ``pk``/``id``, but a @property may
# shadow any of these names — that is exactly the #2614 trigger.
ShadowModel = type("ShadowModel2614", (models.Model,), _attrs)


def _instance():
    obj = ShadowModel(label="visible")
    obj.pk = 1
    obj.id = 1
    return obj


# The other two eager sites: a concrete FIELD and an explicit ``get_*`` METHOD
# carrying a sensitive name. Same chokepoint, so the same answer (#1104: N
# sites, N tests).
ShadowFieldModel = type(
    "ShadowFieldModel2614",
    (models.Model,),
    {
        "__module__": __name__,
        "label": models.CharField(max_length=32, default=""),
        "get_session_auth_hash": models.CharField(max_length=32, default=""),
        "get_all_permissions": lambda self: f"{LEAK}:get_all_permissions",
        "__str__": lambda self: f"shadowf({self.pk})",
        "Meta": type("Meta", (), {"app_label": "tests"}),
    },
)


def _field_instance():
    obj = ShadowFieldModel(label="visible", get_session_auth_hash=f"{LEAK}:field")
    obj.pk = 2
    obj.id = 2
    return obj


def _eager(obj):
    return normalize_django_value(obj)


def _sidecar(obj, name):
    try:
        return getattr(_SidecarModelProxy(obj), name)
    except AttributeError:
        return None


class _ShadowView(LiveView):
    """Module-level so the WS mount can name it by dotted path."""

    template = (
        '<div dj-view="djust.tests.test_property_shadow_parity_2614._ShadowView" dj-id="0">'
        "n={{ n }} label=[{{ m.label }}]"
        + "".join(f" {n}=[{{{{ m.{n} }}}}]" for n in TEMPLATE_NAMES)
        + "</div>"
    )

    def mount(self, request, **kwargs):
        self.n = 0
        self._m = _instance()

    @event_handler()
    def bump(self, **kwargs):
        self.n += 1

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["m"] = self._m
        return ctx


# ---------------------------------------------------------------------------
# Channel 1 — eager (its own test, so gating the property/field/method loop
# off fails HERE, independently of the sidecar).
# ---------------------------------------------------------------------------


class TestEagerChannel:
    @pytest.mark.parametrize("name", REFUSED_NAMES)
    def test_shadowing_property_is_not_serialized(self, name):
        d = _eager(_instance())
        assert name not in d, f"eager dict serialized shadowing property {name!r}: {d[name]!r}"
        assert d["label"] == "visible"  # the safe field still ships

    def test_sensitive_name_on_a_field_is_not_serialized(self):
        d = _eager(_field_instance())
        assert "get_session_auth_hash" not in d, d
        assert d["label"] == "visible"

    def test_sensitive_name_on_an_explicit_method_is_not_serialized(self):
        d = _eager(_field_instance())
        assert "get_all_permissions" not in d, d

    def test_no_leak_marker_anywhere_in_the_dict(self):
        assert LEAK not in repr(
            {k: v for k, v in _eager(_instance()).items() if k in REFUSED_NAMES}
        )


# ---------------------------------------------------------------------------
# Channel 2 — sidecar (its own test).
# ---------------------------------------------------------------------------


class TestSidecarChannel:
    @pytest.mark.parametrize("name", REFUSED_NAMES)
    def test_shadowing_property_is_refused(self, name):
        with pytest.raises(AttributeError):
            getattr(_SidecarModelProxy(_instance()), name)

    def test_safe_names_delegate(self):
        p = _SidecarModelProxy(_instance())
        assert p.label == "visible"


# ---------------------------------------------------------------------------
# Parity — both channels give the SAME answer for every name.
# ---------------------------------------------------------------------------


class TestChannelParity:
    @pytest.mark.parametrize("name", ALL_NAMES)
    def test_both_channels_agree(self, name):
        obj = _instance()
        eager = _eager(obj).get(name)
        sidecar = _sidecar(obj, name)
        assert eager == sidecar, (
            f"channels disagree on {name!r}: eager={eager!r} sidecar={sidecar!r} (#2614 / #1646)"
        )
        if name in REFUSED_NAMES:
            assert eager is None, f"{name!r} must be denied on both channels"

    @pytest.mark.parametrize("name", ["get_session_auth_hash", "get_all_permissions", "label"])
    def test_field_and_method_sites_agree_with_the_sidecar(self, name):
        obj = _field_instance()
        eager = _eager(obj).get(name)
        sidecar = _sidecar(obj, name)
        sidecar = sidecar() if callable(sidecar) else sidecar
        assert eager == sidecar, f"{name!r}: eager={eager!r} sidecar={sidecar!r}"
        assert (eager is None) == (name != "label")

    def test_matrix_is_the_full_sensitive_set(self):
        """The matrix enumerates EVERY sensitive-method name and prefix (not a
        sample) — a name added to the set is pinned automatically."""
        for n in _SENSITIVE_MODEL_METHODS:
            assert n in REFUSED_NAMES
        for p in _SENSITIVE_MODEL_METHOD_PREFIXES:
            assert any(n.startswith(p) for n in REFUSED_NAMES)

    def test_chokepoint_unit(self):
        """The single authority, called directly."""
        f = DjangoJSONEncoder._attr_is_serializable
        denied = DjangoJSONEncoder._get_denied_fields(_instance())
        for n in REFUSED_NAMES:
            assert f(n, denied, None) is False, n
        for n in VARIANT_NAMES + ["label", "pk", "id"]:
            assert f(n, denied, None) is True, n
        # Fail-closed precedence: the sensitive-METHOD refusal is not lifted by
        # the deliberate field opt-out (only floor FIELDS can be opted back in),
        # and never by an allowlist naming it.
        assert (
            f("get_session_auth_hash", denied, None, frozenset({"get_session_auth_hash"})) is False
        )
        assert f("get_session_auth_hash", denied, frozenset({"get_session_auth_hash"})) is False
        assert f("_secret", denied, frozenset({"_secret"}), frozenset({"_secret"})) is False
        # ...while a floor FIELD still honours the documented opt-out.
        assert f("password", denied, None, frozenset({"password"})) is True


# ---------------------------------------------------------------------------
# Real render paths.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRealRenderPaths:
    def test_http_get_does_not_ship_a_shadowing_property(self):
        from django.contrib.sessions.middleware import SessionMiddleware
        from django.test import RequestFactory

        request = RequestFactory().get("/shadow/")
        SessionMiddleware(lambda r: r).process_request(request)
        request.session.save()
        response = _ShadowView.as_view()(request)
        html = response.content.decode() if hasattr(response, "content") else str(response)
        assert "label=[visible]" in html, html
        for n in REFUSED_NAMES:
            if n in TEMPLATE_NAMES:
                assert f"{n}=[]" in html, f"{n!r} rendered on GET: {html}"
        for n in REFUSED_NAMES:
            assert f"{LEAK}:{n}]" not in html, f"{n!r} crossed the GET response: {html}"

    @pytest.mark.asyncio
    async def test_ws_mount_and_event_do_not_ship_a_shadowing_property(self):
        pytest.importorskip("channels")
        from channels.testing import WebsocketCommunicator
        from django.contrib.sessions.backends.db import SessionStore
        from django.test import override_settings

        from djust.websocket import LiveViewConsumer

        def _create_session():
            s = SessionStore()
            s.create()
            return s.session_key

        session_key = await sync_to_async(_create_session)()

        class _ScopeSession:
            def __init__(self, key):
                self.session_key = key

        with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
            comm = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
            comm.scope["session"] = _ScopeSession(session_key)
            connected, _ = await comm.connect()
            assert connected
            await comm.receive_json_from(timeout=2)  # connect frame
            await comm.send_json_to(
                {"type": "mount", "view": f"{__name__}._ShadowView", "url": "/shadow/"}
            )
            frames = []
            for _ in range(6):
                f = await comm.receive_json_from(timeout=3)
                frames.append(f)
                if f.get("type") == "mount":
                    break
            assert frames[-1].get("type") == "mount", frames
            await comm.send_json_to({"type": "event", "event": "bump", "params": {}, "ref": 1})
            for _ in range(6):
                f = await comm.receive_json_from(timeout=3)
                frames.append(f)
                if f.get("type") in ("patch", "html_update"):
                    break
            await comm.disconnect()

        wire = repr(frames)
        assert "n=1" in wire or "n=0" in wire, wire
        for n in REFUSED_NAMES:
            # ``]`` closes the marker so ``is_staff`` does not prefix-match the
            # (legitimately rendered) ``is_staff_member`` variant.
            assert f"{LEAK}:{n}]" not in wire, f"{n!r} crossed the WS wire: {wire}"


# ---------------------------------------------------------------------------
# Structural pin — every decision site calls the ONE chokepoint (#1125).
# ---------------------------------------------------------------------------


class TestStructuralChokepoint:
    SITES = (
        "_serialize_model_safely",
        "_add_safe_model_methods",
        "_add_property_values",
    )

    def test_every_eager_site_calls_the_chokepoint(self):
        for site in self.SITES:
            src = inspect.getsource(getattr(DjangoJSONEncoder, site))
            assert "_attr_is_serializable(" in src, f"{site} bypasses the chokepoint"
            assert "_field_is_serializable(" not in src, f"{site} still calls the field-only check"

    def test_sidecar_calls_the_chokepoint(self):
        src = inspect.getsource(_SidecarModelProxy.__getattr__)
        assert "_attr_is_serializable(" in src
        assert "_SENSITIVE_MODEL_METHODS" not in src

    def test_sensitive_set_is_consulted_only_by_the_chokepoint(self):
        """The set is a pin, not a decoration: exactly ONE production site
        reads it — the chokepoint — so no path can drift from it."""
        import djust.serialization as m

        src = inspect.getsource(m)
        uses = [
            ln
            for ln in src.splitlines()
            if re.search(r"\bin _SENSITIVE_MODEL_METHODS\b", ln) and not ln.lstrip().startswith("#")
        ]
        assert len(uses) == 1, uses


# ---------------------------------------------------------------------------
# Channel 3 — the JIT codegen path (#2685, link N+1 of #2614).
#
# ``python/djust/mixins/context.py`` routes every public Model in the context
# through ``_jit_serialize_model`` → ``optimization/codegen.py``, which emitted
# ANY attribute the template named. ``{{ m.password }}`` on a model with a
# ``password`` FIELD shipped the value in ``get_context_data()['m']`` and in
# the rendered html — the floor the eager and sidecar channels enforce was
# never consulted. The generated code now calls the same ONE chokepoint via
# ``codegen.emittable_names`` for every attribute it reads; a denied name
# is omitted (renders as ``string_if_invalid``, i.e. empty), never shipped.
# ---------------------------------------------------------------------------

from djust.optimization import codegen as _codegen  # noqa: E402

LEAK_JIT = "LEAK-2685"

JitModel = type(
    "JitModel2685",
    (models.Model,),
    {
        "__module__": __name__,
        "label": models.CharField(max_length=32, default=""),
        # A real FIELD — exactly the case _ALWAYS_EXCLUDED_FIELDS exists for.
        "password": models.CharField(max_length=64, default=""),
        # A sensitive method NAME carried by a @property (the #2614 trigger).
        "get_session_auth_hash": property(lambda self: f"{LEAK_JIT}:get_session_auth_hash"),
        "_secret": property(lambda self: f"{LEAK_JIT}:_secret"),
        "__str__": lambda self: f"jit({self.pk})",
        "Meta": type("Meta", (), {"app_label": "tests"}),
    },
)


def _jit_instance():
    obj = JitModel(label="visible", password=f"{LEAK_JIT}:password")
    obj.pk = 3
    obj.id = 3
    return obj


def _codegen_serialize(obj, paths):
    code = _codegen.generate_serializer_code(type(obj).__name__, list(paths), "serialize_2685")
    return _codegen.compile_serializer(code, "serialize_2685")(obj)


class _JitView(LiveView):
    """PUBLIC ``self.m`` — the shape the issue reproduced on: the context loop
    in ``mixins/context.py`` JIT-serializes it through codegen."""

    template = (
        '<div dj-view="djust.tests.test_property_shadow_parity_2614._JitView" dj-id="0">'
        "n={{ n }} label=[{{ m.label }}] password=[{{ m.password }}]"
        " get_session_auth_hash=[{{ m.get_session_auth_hash }}]</div>"
    )

    def mount(self, request, **kwargs):
        self.n = 0
        self.m = _jit_instance()

    @event_handler()
    def bump(self, **kwargs):
        self.n += 1


class TestJitCodegenChannel:
    @pytest.mark.parametrize("name", ["password", "get_session_auth_hash", "_secret"])
    def test_denied_name_is_omitted(self, name):
        out = _codegen_serialize(_jit_instance(), ["label", name])
        assert out == {"label": "visible"}, out

    @pytest.mark.parametrize("name", REFUSED_NAMES)
    def test_shadowing_property_is_omitted(self, name):
        out = _codegen_serialize(_instance(), ["label", name])
        assert name not in out and LEAK not in repr(out), out

    def test_nested_object_is_gated_per_object(self):
        """A nested path crosses objects; the gate runs on the INNER object."""
        outer = _jit_instance()
        outer.owner = _jit_instance()
        out = _codegen_serialize(outer, ["owner.label", "owner.password", "owner._secret"])
        assert out == {"owner": {"label": "visible"}}, out

    def test_context_data_omits_the_field(self):
        """The symptom from the issue: ``get_context_data()['m']`` carried it."""
        from django.test import RequestFactory

        view = _JitView()
        view.request = RequestFactory().get("/jit/")
        view.mount(view.request)
        m = view.get_context_data()["m"]
        assert m["label"] == "visible", m
        assert "password" not in m and "get_session_auth_hash" not in m, m
        assert LEAK_JIT not in repr(m), m


# ``get_Session_auth_hash`` is excluded from the codegen parity row only because
# codegen treats every ``get_*`` name as a METHOD and calls it — a ``get_*``
# @property therefore fails the call and is dropped for a reason unrelated to
# the floor (pre-existing codegen shape, not a security decision).
CODEGEN_PARITY_NAMES = ["label"] + [n for n in ALL_NAMES if n != "get_Session_auth_hash"]


class TestJitCodegenParity:
    @pytest.mark.parametrize("name", CODEGEN_PARITY_NAMES)
    def test_codegen_agrees_with_the_eager_channel(self, name):
        eager = _eager(_instance())
        out = _codegen_serialize(_instance(), [name])
        assert (name in out) == (name in eager), (name, out, eager)


@pytest.mark.django_db
class TestJitRealRenderPaths:
    def test_http_get_does_not_ship_the_field(self):
        from django.contrib.sessions.middleware import SessionMiddleware
        from django.test import RequestFactory

        request = RequestFactory().get("/jit/")
        SessionMiddleware(lambda r: r).process_request(request)
        request.session.save()
        response = _JitView.as_view()(request)
        html = response.content.decode() if hasattr(response, "content") else str(response)
        assert "label=[visible]" in html, html
        assert "password=[]" in html and "get_session_auth_hash=[]" in html, html
        assert LEAK_JIT not in html, html

    @pytest.mark.asyncio
    async def test_ws_mount_and_event_do_not_ship_the_field(self):
        pytest.importorskip("channels")
        from channels.testing import WebsocketCommunicator
        from django.contrib.sessions.backends.db import SessionStore
        from django.test import override_settings

        from djust.websocket import LiveViewConsumer

        def _create_session():
            s = SessionStore()
            s.create()
            return s.session_key

        session_key = await sync_to_async(_create_session)()

        class _ScopeSession:
            def __init__(self, key):
                self.session_key = key

        with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
            comm = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
            comm.scope["session"] = _ScopeSession(session_key)
            connected, _ = await comm.connect()
            assert connected
            await comm.receive_json_from(timeout=2)  # connect frame
            await comm.send_json_to(
                {"type": "mount", "view": f"{__name__}._JitView", "url": "/jit/"}
            )
            frames = []
            for _ in range(6):
                f = await comm.receive_json_from(timeout=3)
                frames.append(f)
                if f.get("type") == "mount":
                    break
            assert frames[-1].get("type") == "mount", frames
            await comm.send_json_to({"type": "event", "event": "bump", "params": {}, "ref": 1})
            for _ in range(6):
                f = await comm.receive_json_from(timeout=3)
                frames.append(f)
                if f.get("type") in ("patch", "html_update"):
                    break
            await comm.disconnect()

        wire = repr(frames)
        assert "label=[visible]" in wire, wire
        assert LEAK_JIT not in wire, wire


#: Every path shape the generator can emit: flat, nested object, indexed list,
#: ``.all()`` iteration, a root ``get_*`` method, a nested ``get_*`` method,
#: a list of lists and an object nested under ``.all()``. The structural check
#: below must hold for ALL of them, not a representative few (#1104).
EVERY_SHAPE = [
    "a",
    "b.c",
    "d.0.e",
    "f.all.g",
    "get_h",
    "i.get_j",
    "k.0.l.0.m",
    "n.all.o.p",
]


def _result_writes(code: str):
    """Every ``<result-dict>[...] = ...`` the generated *code* performs.

    Yields ``(key, guards)`` where *key* is the dict key being written and
    *guards* is the set of names membership-tested by every enclosing ``if``.

    Walks the AST rather than grepping for ``hasattr(``: the old text check
    only inspected lines that happened to contain ``hasattr(``, so a
    ``getattr``-shaped emission — or any new emission site spelled differently
    — was invisible to it and the pin passed while the site was ungated. The
    invariant this expresses is about the WRITE, so it cannot be dodged by
    changing how the read is spelled.
    """
    tree = ast.parse(code)

    def root_name(node):
        while isinstance(node, ast.Subscript):
            node = node.value
        return node.id if isinstance(node, ast.Name) else None

    def key_of(node):
        # Innermost subscript slice: result['a']['b'] emits key 'b'.
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
            return node.slice.value
        return None

    def membership_names(test):
        """``'x' in _ok_N`` sub-expressions of an ``if`` test."""
        found = set()
        for sub in ast.walk(test):
            if (
                isinstance(sub, ast.Compare)
                and len(sub.ops) == 1
                and isinstance(sub.ops[0], ast.In)
                and isinstance(sub.left, ast.Constant)
                and isinstance(sub.comparators[0], ast.Name)
                and sub.comparators[0].id.startswith("_ok_")
            ):
                found.add(sub.left.value)
        return found

    out = []

    def walk(body, guards):
        for stmt in body:
            if isinstance(stmt, ast.If):
                inner = guards | membership_names(stmt.test)
                walk(stmt.body, inner)
                walk(stmt.orelse, guards)
                continue
            if isinstance(stmt, (ast.For, ast.While)):
                walk(stmt.body, guards)
                walk(stmt.orelse, guards)
                continue
            if isinstance(stmt, ast.Try):
                walk(stmt.body, guards)
                for h in stmt.handlers:
                    walk(h.body, guards)
                walk(stmt.orelse, guards)
                walk(stmt.finalbody, guards)
                continue
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                walk(stmt.body, guards)
                continue
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if not isinstance(target, ast.Subscript):
                        continue
                    if root_name(target) is None:
                        continue
                    key = key_of(target)
                    if key is not None:
                        out.append((key, guards))

    walk(tree.body, frozenset())
    return out


def unguarded_emissions(code: str):
    """Keys written into a result dict with no enclosing ``'<key>' in _ok_N``."""
    return [key for key, guards in _result_writes(code) if key not in guards]


class TestStructuralChokepointCodegen:
    """Codegen is a pinned caller of the chokepoint (#1125) — three layers, so
    gating any one off (the helper, the emission, the binding) goes red."""

    def test_helper_calls_the_chokepoint(self):
        src = inspect.getsource(_codegen.emittable_names)
        assert "_attr_is_serializable(" in src
        assert "_field_is_serializable(" not in src
        assert "_SENSITIVE_MODEL_METHODS" not in src

    def test_every_emission_is_guarded(self):
        """Every write into the result dict sits under the gate for its key.

        Mechanical, not textual: `_result_writes` walks the AST of the
        generated code, so a site that reads via ``getattr`` (or any other
        spelling) is still caught — the assertion is about the WRITE.
        """
        code = _codegen.generate_serializer_code("M", EVERY_SHAPE, "s")
        writes = _result_writes(code)
        assert len(writes) >= len(EVERY_SHAPE), (len(writes), code)
        assert unguarded_emissions(code) == [], code

    def test_the_guard_check_catches_an_unguarded_getattr_site(self):
        """Empirical canary (#1459): the check must FAIL on a real violation.

        The old text-based pin filtered for lines containing ``hasattr(``, so
        a ``getattr``-shaped emission was invisible to it. Feed exactly that
        shape in and assert the AST check reports it — otherwise this whole
        class is decorative (#1859).
        """
        good = _codegen.generate_serializer_code("M", ["a"], "s")
        assert unguarded_emissions(good) == [], good

        # A getattr-shaped, gate-free emission — the shape the old pin missed.
        leaked = good.replace(
            "    return result",
            "    result['password'] = getattr(obj, 'password', None)\n    return result",
        )
        assert leaked != good
        assert unguarded_emissions(leaked) == ["password"], leaked

        # ...and it is still reported when it hides under an unrelated guard.
        nested = good.replace(
            "    return result",
            "    if 'a' in _ok_0:\n"
            "        result['password'] = getattr(obj, 'password', None)\n"
            "    return result",
        )
        assert unguarded_emissions(nested) == ["password"], nested

    def test_compiled_namespace_binds_the_helper(self):
        code = _codegen.generate_serializer_code("M", ["a"], "s")
        fn = _codegen.compile_serializer(code, "s")
        assert fn.__globals__["_djust_gate"] is _codegen.emittable_names

    def test_gate_resolves_once_per_object_level_not_per_attribute(self):
        """The prologue shape the Rust caller shares (#2685 perf).

        Three flat names on one object = ONE gate call and three membership
        tests; a nested object resolves its own gate because it may be a
        different model.
        """
        flat = _codegen.generate_serializer_code("M", ["a", "b", "c"], "s")
        assert flat.count("_djust_gate(") == 1, flat
        assert len(re.findall(r"in _ok_\d+", flat)) == 3, flat

        nested = _codegen.generate_serializer_code("M", ["a", "child.x", "child.y"], "s")
        assert nested.count("_djust_gate(") == 2, nested


# ---------------------------------------------------------------------------
# Channel 4 — the RUST queryset serializer (#2688, link N+1 of #2685).
#
# A QuerySet in the context does NOT go through the Python codegen:
# ``mixins/jit.py`` routes it to ``djust._rust.serialize_queryset``, whose
# ``serialize_object_with_paths`` did a bare ``obj.getattr(name)`` with no floor
# at all. So gating the codegen path (#2685) left ``self.users =
# User.objects.all()`` + ``{{ u.password }}`` shipping the pbkdf2 hash on the
# GET response AND in both WS frames. Rust now calls the same ONE Python
# authority (``codegen.emittable_names``) once per object level.
# ---------------------------------------------------------------------------

QsModel = type(
    "QsModel2688",
    (models.Model,),
    {
        "__module__": __name__,
        "label": models.CharField(max_length=32, default=""),
        "password": models.CharField(max_length=64, default=""),
        "get_session_auth_hash": property(lambda self: f"{LEAK_JIT}:get_session_auth_hash"),
        "_secret": property(lambda self: f"{LEAK_JIT}:_secret"),
        "__str__": lambda self: f"qs({self.pk})",
        "Meta": type("Meta", (), {"app_label": "tests"}),
    },
)


def _qs_rows(n=2):
    rows = []
    for i in range(n):
        obj = QsModel(label=f"visible{i}", password=f"{LEAK_JIT}:password")
        obj.pk = obj.id = i + 1
        rows.append(obj)
    return rows


class _QsView(LiveView):
    """A real QuerySet on a public attr — the #2688 shape."""

    template = (
        '<div dj-view="djust.tests.test_property_shadow_parity_2614._QsView" dj-id="0">'
        "n={{ n }}"
        "{% for u in users %}[{{ u.username }}|{{ u.password }}|{{ u.is_superuser }}"
        "|{{ u.get_session_auth_hash }}]{% endfor %}</div>"
    )

    def mount(self, request, **kwargs):
        from django.contrib.auth.models import User

        self.n = 0
        self.users = User.objects.all()

    @event_handler()
    def bump(self, **kwargs):
        self.n += 1


class TestRustQuerySetChannel:
    """The Rust serializer is gated by the same authority as every other."""

    def test_rust_serialize_queryset_omits_denied_names(self):
        rust = pytest.importorskip("djust._rust")
        out = rust.serialize_queryset(
            _qs_rows(), ["label", "password", "get_session_auth_hash", "_secret"]
        )
        assert len(out) == 2, out
        for row in out:
            assert row["label"].startswith("visible"), row
            assert "password" not in row, row
            assert "get_session_auth_hash" not in row, row
            assert "_secret" not in row, row
            assert LEAK_JIT not in repr(row), row

    def test_rust_serialize_queryset_still_emits_benign_names(self):
        """Gate-off sibling: the fix must not simply drop everything."""
        rust = pytest.importorskip("djust._rust")
        out = rust.serialize_queryset(_qs_rows(1), ["label"])
        assert out == [{"label": "visible0"}], out

    def test_rust_gate_is_the_same_authority_as_the_codegen_channel(self):
        """Rust and codegen agree name-for-name — no fifth policy (#1646)."""
        rust = pytest.importorskip("djust._rust")
        names = ["label", "password", "get_session_auth_hash", "_secret"]
        obj = _qs_rows(1)[0]
        via_rust = set(rust.serialize_queryset([obj], names)[0])
        via_gate = set(_codegen.emittable_names(obj, tuple(names)))
        assert via_rust == via_gate, (via_rust, via_gate)

    def test_rust_gates_nested_objects_per_level(self):
        rust = pytest.importorskip("djust._rust")
        outer = _qs_rows(1)[0]
        outer.owner = _qs_rows(1)[0]
        out = rust.serialize_queryset([outer], ["owner.label", "owner.password"])
        assert out == [{"owner": {"label": "visible0"}}], out


@pytest.mark.django_db
class TestRustQuerySetRealRenderPaths:
    """The three surfaces #2688 reproduced on: GET html, WS mount, WS event."""

    @staticmethod
    def _user():
        from django.contrib.auth.models import User

        u = User.objects.create_user(username="alice", password="hunter2-2688")
        u.is_superuser = True
        u.save()
        return u

    def test_http_get_does_not_ship_the_hash(self):
        from django.contrib.sessions.middleware import SessionMiddleware
        from django.test import RequestFactory

        user = self._user()
        request = RequestFactory().get("/qs/")
        SessionMiddleware(lambda r: r).process_request(request)
        request.session.save()
        response = _QsView.as_view()(request)
        html = response.content.decode() if hasattr(response, "content") else str(response)
        assert "alice" in html, html
        assert user.password not in html, html
        assert user.get_session_auth_hash() not in html, html

    @pytest.mark.asyncio
    async def test_ws_mount_and_event_do_not_ship_the_hash(self):
        pytest.importorskip("channels")
        from channels.testing import WebsocketCommunicator
        from django.contrib.sessions.backends.db import SessionStore
        from django.test import override_settings

        from djust.websocket import LiveViewConsumer

        user = await sync_to_async(self._user)()
        pw_hash = user.password
        auth_hash = await sync_to_async(user.get_session_auth_hash)()

        def _create_session():
            s = SessionStore()
            s.create()
            return s.session_key

        session_key = await sync_to_async(_create_session)()

        class _ScopeSession:
            def __init__(self, key):
                self.session_key = key

        with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
            comm = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
            comm.scope["session"] = _ScopeSession(session_key)
            connected, _ = await comm.connect()
            assert connected
            await comm.receive_json_from(timeout=2)
            await comm.send_json_to({"type": "mount", "view": f"{__name__}._QsView", "url": "/qs/"})
            mount_frames = []
            for _ in range(6):
                f = await comm.receive_json_from(timeout=3)
                mount_frames.append(f)
                if f.get("type") == "mount":
                    break
            assert mount_frames[-1].get("type") == "mount", mount_frames

            await comm.send_json_to({"type": "event", "event": "bump", "params": {}, "ref": 1})
            event_frames = []
            for _ in range(6):
                f = await comm.receive_json_from(timeout=3)
                event_frames.append(f)
                if f.get("type") in ("patch", "html_update"):
                    break
            assert event_frames, "no frame for the event"
            await comm.disconnect()

        for label, frames in (("mount", mount_frames), ("event", event_frames)):
            wire = repr(frames)
            assert "alice" in wire, (label, wire)
            assert pw_hash not in wire, (label, wire)
            assert auth_hash not in wire, (label, wire)


class TestRustQuerySetCallerSet:
    """Pin the SET of ``djust._rust.serialize_queryset`` importers (#1125).

    The gate lives INSIDE Rust, so a new caller is gated automatically — that
    is the point of putting it there rather than at the call sites (#1646).
    What stays the caller's own responsibility is the failure path: each must
    fall back to ``normalize_django_value`` (itself gated), never to a raw
    read. Grepping for the SINK rather than for the callers one happens to
    remember is the rule this encodes; a third importer turns this red so its
    fallback is decided explicitly rather than by omission.
    """

    EXPECTED_IMPORTERS = {
        "mixins/jit.py",
        "template/rendering.py",
    }

    @staticmethod
    def _importers():
        import pathlib

        pkg = pathlib.Path(_codegen.__file__).resolve().parents[1]
        found = set()
        for path in pkg.rglob("*.py"):
            rel = path.relative_to(pkg).as_posix()
            if rel.startswith("tests/"):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if re.search(r"from djust\._rust import[^\n]*serialize_queryset", text):
                found.add(rel)
        return found

    def test_importer_set_is_exactly_the_expected_two(self):
        assert self._importers() == self.EXPECTED_IMPORTERS, (
            "the set of djust._rust.serialize_queryset importers changed; confirm "
            "the new one falls back to normalize_django_value on failure, then "
            "update EXPECTED_IMPORTERS"
        )

    @pytest.mark.parametrize("rel", sorted(EXPECTED_IMPORTERS))
    def test_each_importer_falls_back_to_the_gated_path(self, rel):
        import pathlib

        pkg = pathlib.Path(_codegen.__file__).resolve().parents[1]
        text = (pkg / rel).read_text(encoding="utf-8")
        assert "normalize_django_value" in text, rel


class TestGateCacheKeyIsOrderInsensitive:
    """The gate memoizes on the SET of names, not their order (#2688).

    The Rust caller iterates a Rust ``HashMap``, whose order differs per
    instance. Keyed on a tuple, twelve identical ``serialize_queryset`` calls
    produced ELEVEN distinct cache keys — every call missed and the cache
    walked toward its cap. This is the pin for that; the fix is one mechanism
    (the frozenset key), deliberately not paired with a sort on the Rust side
    that would shadow it (#2233).
    """

    def test_permutations_share_one_cache_entry(self):
        names = ("label", "password", "get_session_auth_hash", "_secret")
        obj = _qs_rows(1)[0]

        _codegen._GATE_CACHE.clear()
        first = _codegen.emittable_names(obj, names)
        assert len(_codegen._GATE_CACHE) == 1, _codegen._GATE_CACHE

        for perm in (
            ("password", "label", "_secret", "get_session_auth_hash"),
            ("_secret", "get_session_auth_hash", "password", "label"),
            ("get_session_auth_hash", "_secret", "label", "password"),
        ):
            assert _codegen.emittable_names(obj, perm) == first
        assert len(_codegen._GATE_CACHE) == 1, _codegen._GATE_CACHE

    def test_a_different_name_set_is_a_different_entry(self):
        """Gate-off sibling: the key must still DISCRIMINATE (non-vacuous)."""
        obj = _qs_rows(1)[0]
        _codegen._GATE_CACHE.clear()
        _codegen.emittable_names(obj, ("label",))
        _codegen.emittable_names(obj, ("label", "password"))
        assert len(_codegen._GATE_CACHE) == 2, _codegen._GATE_CACHE

    def test_repeated_rust_queryset_calls_do_not_grow_the_cache(self):
        """The real path, end to end — this is what regressed."""
        rust = pytest.importorskip("djust._rust")
        paths = ["label", "password", "get_session_auth_hash", "_secret"]

        rust.serialize_queryset(_qs_rows(1), paths)  # warm
        before = len(_codegen._GATE_CACHE)
        for _ in range(12):
            rust.serialize_queryset(_qs_rows(1), paths)
        assert len(_codegen._GATE_CACHE) == before, (
            "the gate cache grew across identical serialize_queryset calls — the "
            "key is sensitive to the Rust HashMap's iteration order again"
        )
