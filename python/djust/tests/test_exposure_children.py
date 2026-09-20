"""Bound child persistence; lifecycle integration remains gated separately."""

from dataclasses import replace

import pytest
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.backends.signed_cookies import SessionStore as CookieStore

from djust._exposure import ExposureContract, ExposureError, FieldExposure
from djust._exposure_children import ChildStateSession
from djust._exposure_sessions import StateBinding


@pytest.fixture
def setup(db):
    session = SessionStore()
    session.create()
    return {
        "session": session,
        "parents": (ExposureContract("app.Page", {}),),
        "contract": ExposureContract(
            "app.Menu",
            {
                "selected": FieldExposure(persist="server"),
                "preview": FieldExposure(client=True),
            },
        ),
        "binding": StateBinding(session.session_key, "user:1", "tenant:1", "/orders/"),
        "slots": ("primary",),
        "mount_inputs": {"object_id": 42},
    }


def test_only_server_state_round_trips_in_reopened_session(setup):
    first = ChildStateSession(**setup)
    first.save({"selected": [1], "preview": "CLIENT_SENTINEL", "extra": "SECRET_SENTINEL"})
    reopened = dict(setup, session=SessionStore(setup["session"].session_key))
    second = ChildStateSession(**reopened)
    assert second.load() == {"selected": [1]}
    assert "SENTINEL" not in repr(second.session.load())
    restored = second.load()
    restored["selected"].append(2)
    assert second.load() == {"selected": [1]}


def test_same_type_siblings_and_nested_slots_are_independent(setup):
    first = ChildStateSession(**setup)
    second = ChildStateSession(**dict(setup, slots=("secondary",)))
    nested = ChildStateSession(
        **dict(
            setup,
            parents=setup["parents"] + (ExposureContract("app.Sidebar", {}),),
            slots=("primary", "secondary"),
        )
    )
    for index, adapter in enumerate((first, second, nested)):
        adapter.save({"selected": index})
    assert len({first.key, second.key, nested.key}) == 3
    assert [adapter.load() for adapter in (first, second, nested)] == [
        {"selected": 0},
        {"selected": 1},
        {"selected": 2},
    ]


@pytest.mark.parametrize("part", ["user", "tenant", "view", "session"])
def test_copied_envelope_cannot_cross_request_identity(setup, part):
    original = ChildStateSession(**setup)
    original.save({"selected": 9})
    changed = dict(setup)
    if part == "session":
        session = SessionStore()
        session.create()
        changed["session"] = session
        changed["binding"] = replace(setup["binding"], session=session.session_key)
    else:
        changed["binding"] = replace(setup["binding"], **{part: "other"})
    other = ChildStateSession(**changed)
    other.session[other.key] = original.session[original.key]
    with pytest.raises(ExposureError):
        other.load()


@pytest.mark.parametrize(
    "change",
    ["slot", "parent_class", "parent_schema", "child_class", "child_schema", "mount_inputs"],
)
def test_copied_envelope_cannot_cross_component_identity_or_schema(setup, change):
    original = ChildStateSession(**setup)
    original.save({"selected": 9})
    changed = dict(setup)
    if change == "slot":
        changed["slots"] = ("secondary",)
    elif change == "parent_class":
        changed["parents"] = (ExposureContract("app.OtherPage", {}),)
    elif change == "parent_schema":
        changed["parents"] = (ExposureContract("app.Page", {}, version=2),)
    elif change == "child_class":
        changed["contract"] = ExposureContract("app.OtherMenu", setup["contract"].fields)
    elif change == "child_schema":
        changed["contract"] = replace(setup["contract"], version=2)
    else:
        changed["mount_inputs"] = {"object_id": 43}
    other = ChildStateSession(**changed)
    if change != "slot":
        # Redeploys and different mount inputs reuse one slot instead of leaking
        # an unreachable entry for each schema/object.
        assert other.key == original.key
    other.session[other.key] = original.session[original.key]
    with pytest.raises(ExposureError):
        other.load()


def test_mount_input_order_does_not_change_identity_and_values_are_not_retained(setup):
    inputs = {"object_id": 42, "filter": {"b": 2, "a": [1]}}
    first = ChildStateSession(**dict(setup, mount_inputs=inputs))
    first.save({"selected": 3})
    inputs["filter"]["a"].append(2)
    same = ChildStateSession(
        **dict(setup, mount_inputs={"filter": {"a": [1], "b": 2}, "object_id": 42})
    )
    assert same.load() == {"selected": 3}
    changed = ChildStateSession(**dict(setup, mount_inputs=inputs))
    with pytest.raises(ExposureError):
        changed.load()
    assert "object_id" not in repr(first.session[first.key])


@pytest.mark.parametrize("invalid", [object(), float("nan"), {"value": "x" * 65536}])
def test_mount_inputs_require_bounded_json_without_object_coercion(setup, invalid):
    with pytest.raises(ExposureError):
        ChildStateSession(**dict(setup, mount_inputs={"value": invalid}))
    assert dict(setup["session"].items()) == {}


@pytest.mark.parametrize(
    "change",
    [
        {"slots": ()},
        {"slots": ["primary"]},
        {"slots": ("",)},
        {"slots": ("x" * 129,)},
        {"slots": ("a", "b")},
        {"parents": ()},
        {"parents": (object(),)},
        {"mount_inputs": []},
    ],
)
def test_invalid_provider_identity_rejected_before_session_write(setup, change):
    with pytest.raises(ExposureError):
        ChildStateSession(**dict(setup, **change))
    assert dict(setup["session"].items()) == {}


def test_cookie_sessions_are_never_a_fallback(setup):
    session = CookieStore()
    with pytest.raises(ExposureError, match="server-side"):
        ChildStateSession(**dict(setup, session=session))
    assert dict(session.items()) == {}


@pytest.mark.parametrize("mutation", ["legacy", "extra", "undeclared"])
def test_invalid_restore_returns_no_partial_values(setup, mutation):
    adapter = ChildStateSession(**setup)
    adapter.save({"selected": 3})
    raw = adapter.session[adapter.key]
    if mutation == "legacy":
        raw = {"selected": 3}
    elif mutation == "extra":
        raw["extra"] = 9
    else:
        raw["state"]["values"]["preview"] = "PRIVATE_SENTINEL"
    adapter.session[adapter.key] = raw
    with pytest.raises(ExposureError):
        adapter.load()


def test_rotation_and_expiry_rejected(setup, monkeypatch):
    monkeypatch.setattr("djust._exposure_sessions.time.time", lambda: 1000)
    adapter = ChildStateSession(**setup, max_age=10)
    adapter.save({"selected": 3})
    monkeypatch.setattr("djust._exposure_sessions.time.time", lambda: 1010)
    with pytest.raises(ExposureError):
        adapter.load()
    setup["session"].cycle_key()
    with pytest.raises(ExposureError):
        adapter.save({"selected": 4})


def test_storage_failure_has_no_legacy_or_client_fallback(setup, monkeypatch):
    adapter = ChildStateSession(**setup)

    def fail():
        raise OSError("storage unavailable")

    monkeypatch.setattr(adapter.session, "save", fail)
    before = dict(adapter.session.items())
    with pytest.raises(ExposureError, match="persistence unavailable"):
        adapter.save({"selected": 3, "preview": "CLIENT_SENTINEL"})
    assert dict(adapter.session.items()) == before
    assert "CLIENT_SENTINEL" not in repr(dict(adapter.session.items()))


def test_changed_ancestor_invalidates_nested_child_even_if_direct_parent_matches(setup):
    parents = setup["parents"] + (ExposureContract("app.Sidebar", {}),)
    args = dict(setup, parents=parents, slots=("sidebar", "menu"))
    original = ChildStateSession(**args)
    original.save({"selected": 5})
    changed = ChildStateSession(
        **dict(args, parents=(ExposureContract("app.ReplacementPage", {}), parents[1]))
    )
    assert changed.key == original.key
    with pytest.raises(ExposureError):
        changed.load()


def test_child_namespace_cannot_consume_parent_envelope(setup):
    from djust._exposure_sessions import ServerStateSession

    parent = ServerStateSession(setup["session"], setup["contract"], setup["binding"])
    parent.save({"selected": 5})
    child = ChildStateSession(**setup)
    assert child.key != parent.key
    child.session[child.key] = parent.session[parent.key]
    with pytest.raises(ExposureError):
        child.load()


@pytest.mark.django_db(transaction=True)
async def test_async_and_sync_storage_have_identical_envelopes():
    session = SessionStore()
    await session.acreate()
    args = {
        "session": session,
        "parents": (ExposureContract("app.Page", {}),),
        "contract": ExposureContract("app.Menu", {"selected": FieldExposure(persist="server")}),
        "binding": StateBinding(session.session_key, "user:1", "none", "/"),
        "slots": ("menu",),
        "mount_inputs": {},
    }
    adapter = ChildStateSession(**args)
    await adapter.asave({"selected": 8})
    reopened = ChildStateSession(**dict(args, session=SessionStore(session.session_key)))
    assert await reopened.aload() == {"selected": 8}
    from asgiref.sync import sync_to_async

    assert await sync_to_async(reopened.load)() == {"selected": 8}
