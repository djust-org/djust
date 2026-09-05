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
# ``codegen._attr_is_emittable`` for every attribute it reads; a denied name
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


class TestStructuralChokepointCodegen:
    """Codegen is a pinned caller of the chokepoint (#1125) — three layers, so
    gating any one off (the helper, the emission, the binding) goes red."""

    def test_helper_calls_the_chokepoint(self):
        src = inspect.getsource(_codegen._attr_is_emittable)
        assert "_attr_is_serializable(" in src
        assert "_field_is_serializable(" not in src
        assert "_SENSITIVE_MODEL_METHODS" not in src

    def test_every_emission_is_guarded(self):
        code = _codegen.generate_serializer_code(
            "M", ["a", "b.c", "d.0.e", "f.all.g", "get_h", "i.get_j"], "s"
        )
        reads = [ln for ln in code.splitlines() if "hasattr(" in ln]
        assert reads, code
        for ln in reads:
            assert "_djust_attr_ok(" in ln, ln
        assert len(re.findall(r"_djust_attr_ok\(", code)) == len(reads), code

    def test_compiled_namespace_binds_the_helper(self):
        code = _codegen.generate_serializer_code("M", ["a"], "s")
        fn = _codegen.compile_serializer(code, "s")
        assert fn.__globals__["_djust_attr_ok"] is _codegen._attr_is_emittable
