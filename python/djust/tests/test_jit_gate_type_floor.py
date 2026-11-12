"""The JIT attribute gate applies the column-type floor like the serialization path does.

``docs/SECURE_DEFAULTS.md`` says the serialization TYPE floor drops a ``BinaryField``
column, a column of a type named in ``LIVEVIEW_CONFIG['sensitive_field_types']``, and a
column whose type is encrypted-named, whatever the column is called and whatever a per-model
allowlist says. The eager encoder and the sidecar proxy both apply it
(``_field_type_is_excluded`` / ``_field_type_excluded_for``). The JIT attribute gate,
``optimization.codegen.emittable_names``, decided by NAME only, so a template that named such a
column (``{{ member.blob }}``) read it, on every route the gate sits behind:

* a model on a public view attribute (compiled serializer),
* a ``list`` of models (compiled serializer, per row),
* a model reached through a foreign key (the gate runs per object level),
* a lazy-wrapped model (``SimpleLazyObject``),
* a QuerySet (the Rust serializer calls the same gate).

The gate is memoised, so the model class and the configured type names are part of its key:
the verdict depends on a column's TYPE, which the name sets do not carry.

Each route is exercised through a real HTTP GET and a real ``WebsocketCommunicator`` mount and
event, with a control column that must still render.
"""

from __future__ import annotations

import pytest
from asgiref.sync import sync_to_async
from django.db import connection, models
from django.utils.functional import SimpleLazyObject

from djust import LiveView
from djust.config import get_config
from djust.decorators import event_handler
from djust.optimization import codegen as _codegen
from djust.serialization import normalize_django_value
from djust import state

LEAK = "LEAK-TYPE-FLOOR"
MODULE = "djust.tests.test_jit_gate_type_floor"

# A CharField subclass whose CLASS NAME contains "Encrypted" and one containing "Fernet": the
# best-effort encrypted-field detection, with no real crypto dependency.
EncryptedCharField = type("EncryptedCharField", (models.CharField,), {"__module__": __name__})
FernetTextField = type("FernetTextField", (models.TextField,), {"__module__": __name__})
# A type a project names in LIVEVIEW_CONFIG['sensitive_field_types'].
PinField = type("PinField", (models.CharField,), {"__module__": __name__})


def _columns(extra=None):
    cols = {
        "__module__": __name__,
        "label": models.CharField(max_length=40, default=""),
        "blob": models.BinaryField(default=b""),
        "token": EncryptedCharField(max_length=64, default=""),
        "note": FernetTextField(default=""),
        "pin": PinField(max_length=16, default=""),
        "avatar": models.FileField(upload_to="uploads/", blank=True, default=""),
        "__str__": lambda self: f"row({self.pk})",
        "Meta": type("Meta", (), {"app_label": "tests"}),
    }
    cols.update(extra or {})
    return cols


Plain = type("TypeFloorPlain", (models.Model,), _columns())
Allowlisted = type(
    "TypeFloorAllowlisted",
    (models.Model,),
    _columns({"djust_serializable_fields": ("label", "blob", "token", "note", "pin")}),
)
OptedOut = type(
    "TypeFloorOptedOut",
    (models.Model,),
    _columns({"djust_serialize_sensitive_fields": ("blob", "token", "note", "pin")}),
)
Child = type(
    "TypeFloorChild",
    (models.Model,),
    {
        "__module__": __name__,
        "title": models.CharField(max_length=40, default=""),
        "owner": models.ForeignKey(Plain, on_delete=models.CASCADE),
        "__str__": lambda self: f"child({self.pk})",
        "Meta": type("Meta", (), {"app_label": "tests"}),
    },
)

#: Every column of the three models that the TYPE floor drops, and the control that stays.
TYPE_FLOORED = ("blob", "token", "note", "pin")
CONTROL = "label"
ALL_COLUMNS = (CONTROL, *TYPE_FLOORED, "avatar")


@pytest.fixture(autouse=True)
def _configured_pin_type():
    """Name ``PinField`` as a sensitive type for the module, and restore the config."""
    cfg = get_config()
    before = cfg._config.get("sensitive_field_types")
    cfg._config["sensitive_field_types"] = ["PinField"]
    _codegen._GATE_CACHE.clear()
    try:
        yield
    finally:
        cfg._config["sensitive_field_types"] = before
        _codegen._GATE_CACHE.clear()


def _row(model=Plain, pk=1):
    obj = model(
        label="visible",
        blob=LEAK.encode(),
        token=f"{LEAK}:token",
        note=f"{LEAK}:note",
        pin=f"{LEAK}:pin",
    )
    obj.pk = obj.id = pk
    return obj


def _cells(var):
    return " ".join(f"{c}=[{{{{ {var}.{c} }}}}]" for c in (CONTROL, *TYPE_FLOORED))


class _Bump(LiveView):
    exposure_policy = "explicit"
    n = state()

    @event_handler()
    def bump(self, **kwargs):
        self.n += 1


class _DirectView(_Bump):
    m = state()
    template = f'<div dj-view="{MODULE}._DirectView" dj-id="0">n={{{{ n }}}} {_cells("m")}</div>'

    def mount(self, request, **kwargs):
        self.n = 0
        self.m = _row()


class _ListView(_Bump):
    rows = state()
    template = (
        f'<div dj-view="{MODULE}._ListView" dj-id="0">n={{{{ n }}}}'
        f"{{% for r in rows %}}<p>{_cells('r')}</p>{{% endfor %}}</div>"
    )

    def mount(self, request, **kwargs):
        self.n = 0
        self.rows = [_row(pk=1), _row(pk=2)]


class _FkView(_Bump):
    child = state()
    template = (
        f'<div dj-view="{MODULE}._FkView" dj-id="0">n={{{{ n }}}} '
        f"title=[{{{{ child.title }}}}] {_cells('child.owner')}</div>"
    )

    def mount(self, request, **kwargs):
        self.n = 0
        child = Child(title="kid")
        child.pk = child.id = 1
        child.owner = _row()
        self.child = child


class _LazyView(_Bump):
    m = state()
    template = f'<div dj-view="{MODULE}._LazyView" dj-id="0">n={{{{ n }}}} {_cells("m")}</div>'

    def mount(self, request, **kwargs):
        self.n = 0
        self.m = SimpleLazyObject(_row)


class _AllowlistedView(_Bump):
    m = state()
    template = (
        f'<div dj-view="{MODULE}._AllowlistedView" dj-id="0">n={{{{ n }}}} {_cells("m")}</div>'
    )

    def mount(self, request, **kwargs):
        self.n = 0
        self.m = _row(Allowlisted)


class _OptedOutView(_Bump):
    m = state()
    template = f'<div dj-view="{MODULE}._OptedOutView" dj-id="0">n={{{{ n }}}} {_cells("m")}</div>'

    def mount(self, request, **kwargs):
        self.n = 0
        self.m = _row(OptedOut)


class _QuerySetView(_Bump):
    rows = state()
    template = (
        f'<div dj-view="{MODULE}._QuerySetView" dj-id="0">n={{{{ n }}}}'
        f"{{% for r in rows %}}<p>{_cells('r')}</p>{{% endfor %}}</div>"
    )

    def mount(self, request, **kwargs):
        self.n = 0
        self.rows = Plain.objects.order_by("pk")


ROUTES = {
    "direct": _DirectView,
    "list": _ListView,
    "foreign_key": _FkView,
    "lazy": _LazyView,
    "allowlisted": _AllowlistedView,
    "opted_out": _OptedOutView,
}


def _get(view_cls):
    from django.contrib.sessions.middleware import SessionMiddleware
    from django.test import RequestFactory

    request = RequestFactory().get("/gate/")
    SessionMiddleware(lambda r: r).process_request(request)
    request.session.save()
    response = view_cls.as_view()(request)
    return response.content.decode() if hasattr(response, "content") else str(response)


async def _wire(view_cls):
    """Every frame a WebSocket client receives for a mount and one event, as text."""
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

    with override_settings(LIVEVIEW_ALLOWED_MODULES=[MODULE]):
        comm = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        comm.scope["session"] = _ScopeSession(session_key)
        connected, _ = await comm.connect()
        assert connected
        await comm.receive_json_from()  # connect frame
        await comm.send_json_to(
            {"type": "mount", "view": f"{MODULE}.{view_cls.__name__}", "url": "/gate/"}
        )
        frames = []
        for _ in range(6):
            f = await comm.receive_json_from()
            frames.append(f)
            if f.get("type") == "mount":
                break
        assert frames[-1].get("type") == "mount", frames
        await comm.send_json_to({"type": "event", "event": "bump", "params": {}, "ref": 1})
        for _ in range(6):
            f = await comm.receive_json_from()
            frames.append(f)
            if f.get("type") in ("patch", "html_update"):
                break
        await comm.disconnect()
    return repr(frames)


def _assert_floored(text, *, where):
    assert f"{CONTROL}=[visible]" in text, (where, text)
    for col in TYPE_FLOORED:
        assert f"{col}=[]" in text, (where, col, text)
    assert LEAK not in text, (where, text)


# ---------------------------------------------------------------------------
# Real render paths: HTTP GET and a WebSocket mount + event, per route.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestHttpGet:
    @pytest.mark.parametrize("route", sorted(ROUTES))
    def test_a_type_floored_column_is_not_rendered(self, route):
        _assert_floored(_get(ROUTES[route]), where=f"GET {route}")


@pytest.mark.django_db(transaction=True)
class TestWebSocket:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("route", sorted(ROUTES))
    async def test_a_type_floored_column_is_not_on_the_wire(self, route):
        wire = await _wire(ROUTES[route])
        assert f"{CONTROL}=[visible]" in wire, (route, wire)
        assert LEAK not in wire, (route, wire)


@pytest.fixture
def plain_table(transactional_db):
    with connection.schema_editor() as editor:
        editor.create_model(Plain)
    for pk in (1, 2):
        Plain.objects.create(
            pk=pk,
            label="visible",
            blob=LEAK.encode(),
            token=f"{LEAK}:token",
            note=f"{LEAK}:note",
            pin=f"{LEAK}:pin",
        )
    try:
        yield
    finally:
        with connection.schema_editor() as editor:
            editor.delete_model(Plain)


class TestQuerySetRoute:
    """A QuerySet goes to the Rust serializer, which calls the same gate."""

    def test_http_get(self, plain_table):
        _assert_floored(_get(_QuerySetView), where="GET queryset")

    @pytest.mark.asyncio
    async def test_websocket(self, plain_table):
        wire = await _wire(_QuerySetView)
        assert f"{CONTROL}=[visible]" in wire, wire
        assert LEAK not in wire, wire

    def test_the_rust_serializer_omits_each_type_floored_column(self):
        rust = pytest.importorskip("djust._rust")
        rows = rust.serialize_queryset([_row()], [CONTROL, *TYPE_FLOORED])
        assert rows == [{CONTROL: "visible"}], rows


# ---------------------------------------------------------------------------
# The gate itself, and its agreement with the serialization path.
# ---------------------------------------------------------------------------


class TestTheGate:
    @pytest.mark.parametrize("model", [Plain, Allowlisted, OptedOut])
    def test_the_gate_and_the_eager_channel_agree_for_every_column(self, model):
        """One answer per column from the gate and from ``normalize_django_value``.

        An allowlist names the type-floored columns and an opt-out names them as sensitive:
        neither lifts the TYPE floor, on the serialization path or in the gate.
        """
        obj = _row(model)
        eager = normalize_django_value(obj)
        gated = _codegen.emittable_names(obj, ALL_COLUMNS)
        for col in ALL_COLUMNS:
            assert (col in gated) == (col in eager), (model.__name__, col, sorted(gated), eager)

    @pytest.mark.parametrize("model", [Plain, Allowlisted, OptedOut])
    def test_each_type_floored_column_is_refused(self, model):
        permitted = _codegen.emittable_names(_row(model), ALL_COLUMNS)
        for col in TYPE_FLOORED:
            assert col not in permitted, (model.__name__, col)
        assert CONTROL in permitted

    def test_a_file_column_is_still_emitted(self):
        """``FileField`` serializes a URL, the intended payload: never type-floored."""
        assert "avatar" in _codegen.emittable_names(_row(), ("avatar", CONTROL))

    @pytest.mark.parametrize("model", [Plain, Allowlisted, OptedOut])
    def test_a_lazy_wrapped_model_gets_the_same_verdict_as_the_model(self, model):
        """The per-model sets and the column types belong to the wrapped class.

        ``type(lazy)`` is ``SimpleLazyObject``: read from it, a per-model allowlist
        (``avatar`` is not in ``Allowlisted``'s) was never seen and the gate answered as if
        the model had none.
        """
        lazy = SimpleLazyObject(lambda: _row(model))
        assert _codegen.emittable_names(lazy, ALL_COLUMNS) == _codegen.emittable_names(
            _row(model), ALL_COLUMNS
        )

    def test_a_lazy_wrapped_model_keeps_its_allowlist(self):
        lazy = SimpleLazyObject(lambda: _row(Allowlisted))
        assert "avatar" not in _codegen.emittable_names(lazy, ("avatar", CONTROL))

    def test_an_object_that_is_not_a_model_is_unaffected(self):
        class Plainish:
            blob = "x"
            label = "y"

        assert _codegen.emittable_names(Plainish(), ("blob", "label")) == frozenset(
            {"blob", "label"}
        )

    def test_the_compiled_serializer_omits_type_floored_columns(self):
        code = _codegen.generate_serializer_code("Plain", [CONTROL, *TYPE_FLOORED], "ser_floor")
        out = _codegen.compile_serializer(code, "ser_floor")(_row())
        assert out == {CONTROL: "visible"}, out

    def test_take_by_tree_omits_type_floored_columns(self):
        tree = {CONTROL: {}, "blob": {}, "token": {}}
        assert _codegen.take_by_tree([_row()], tree) == [{CONTROL: "visible"}]


class TestTheGateCache:
    """The verdict depends on the column TYPE, which the name sets do not carry."""

    def test_two_models_with_the_same_name_sets_do_not_share_a_verdict(self):
        """Same names, same denylist, no allowlist, no opt-out: only the class differs.

        ``Bare`` has no ``blob`` BinaryField (its ``blob`` is a CharField), ``Plain`` does.
        A key without the model class serves whichever was asked first to the other.
        """
        Bare = type(
            "TypeFloorBare",
            (models.Model,),
            {
                "__module__": __name__,
                "blob": models.CharField(max_length=10, default=""),
                "Meta": type("Meta", (), {"app_label": "tests"}),
            },
        )
        names = ("blob",)
        for order in ((Bare, Plain), (Plain, Bare)):
            _codegen._GATE_CACHE.clear()
            verdicts = {m: _codegen.emittable_names(m(), names) for m in order}
            assert verdicts[Bare] == frozenset({"blob"}), (order, verdicts)
            assert verdicts[Plain] == frozenset(), (order, verdicts)

    def test_two_lazy_wrapped_models_with_the_same_name_sets_do_not_share_a_verdict(self):
        """The same isolation through a lazy wrapper, in BOTH orders.

        ``type(lazy)`` is ``SimpleLazyObject`` for every wrapped model, so a key built from
        ``type(obj)`` instead of the model class collapses the two twins to one entry and the
        second asked gets the first's verdict. The non-lazy test above cannot see that:
        there ``type(obj)`` IS the model class.
        """
        Bare = type(
            "TypeFloorLazyBare",
            (models.Model,),
            {
                "__module__": __name__,
                "blob": models.CharField(max_length=10, default=""),
                "Meta": type("Meta", (), {"app_label": "tests"}),
            },
        )
        Binary = type(
            "TypeFloorLazyBinary",
            (models.Model,),
            {
                "__module__": __name__,
                "blob": models.BinaryField(default=b""),
                "Meta": type("Meta", (), {"app_label": "tests"}),
            },
        )
        names = ("blob",)
        for order in ((Bare, Binary), (Binary, Bare)):
            _codegen._GATE_CACHE.clear()
            verdicts = {m: _codegen.emittable_names(SimpleLazyObject(m), names) for m in order}
            assert verdicts[Bare] == frozenset({"blob"}), (order, verdicts)
            assert verdicts[Binary] == frozenset(), (order, verdicts)

    def test_a_full_cache_keeps_memoising_the_keys_in_use(self, monkeypatch):
        """Past the cap the cache is cleared and refilled, not frozen at its first 512 keys."""
        monkeypatch.setattr(_codegen, "_GATE_CACHE_MAX", 3)
        obj = _row()
        _codegen._GATE_CACHE.clear()
        for i in range(10):
            _codegen.emittable_names(obj, (CONTROL, f"unknown_{i}"))
            assert len(_codegen._GATE_CACHE) <= 3, _codegen._GATE_CACHE
        latest = (CONTROL, "unknown_9")
        assert any(k[0] == frozenset(latest) for k in _codegen._GATE_CACHE), _codegen._GATE_CACHE

    def test_a_change_to_the_configured_types_changes_the_verdict(self):
        cfg = get_config()
        obj = _row()
        assert "pin" not in _codegen.emittable_names(obj, ("pin", CONTROL))
        cfg._config["sensitive_field_types"] = []
        assert "pin" in _codegen.emittable_names(obj, ("pin", CONTROL))
        cfg._config["sensitive_field_types"] = ["PinField"]
        assert "pin" not in _codegen.emittable_names(obj, ("pin", CONTROL))

    def test_the_key_still_discriminates_between_name_sets(self):
        """Gate-off sibling: the key is not collapsed to one entry."""
        obj = _row()
        _codegen._GATE_CACHE.clear()
        _codegen.emittable_names(obj, (CONTROL,))
        _codegen.emittable_names(obj, (CONTROL, "blob"))
        assert len(_codegen._GATE_CACHE) == 2, _codegen._GATE_CACHE


class TestTheRustModelArm:
    """``_rust.serialize_context`` builds a model's dict with ``model_to_dict`` and holds each
    name to the same gate."""

    def test_an_encrypted_named_column_is_not_shipped(self):
        rust = pytest.importorskip("djust._rust")
        out = rust.serialize_context({"m": _row()})["m"]
        assert out[CONTROL] == "visible", out
        for col in ("token", "note", "pin"):
            assert col not in out, (col, out)
        assert LEAK not in repr(out), out


class TestStructuralPins:
    """The gate asks the one authority, and the authority asks both floors (#1125)."""

    def test_the_gate_asks_the_combined_authority(self):
        import inspect

        src = inspect.getsource(_codegen.emittable_names)
        assert "_attr_is_readable(" in src
        assert "_attr_is_serializable(" not in src

    def test_the_combined_authority_asks_both_floors(self):
        import inspect

        from djust.serialization import DjangoJSONEncoder

        src = inspect.getsource(DjangoJSONEncoder._attr_is_readable)
        assert "_attr_is_serializable(" in src
        assert "_field_type_excluded_for(" in src

    def test_the_cache_key_names_the_class_and_the_configured_types(self):
        import inspect

        src = inspect.getsource(_codegen.emittable_names)
        assert "model_class" in src and "_configured_sensitive_types()" in src
