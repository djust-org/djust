"""Fixed interactive bindings across signed back-navigation, not debug scrubbers."""

from copy import deepcopy
import json

from asgiref.sync import async_to_sync
import pytest

from djust.components._interactive import DropdownMenu
from djust.tests.test_interactive_bindings import MenuPage, mounted


KEY = "__interactive_bindings__"


class SignedMenuPage(MenuPage):
    def mount(self, request, **kwargs):
        super().mount(request, **kwargs)
        self.menu.open = True


def captured():
    source = mounted()
    async_to_sync(source.menu.select)(value="edit")
    return source, source._capture_snapshot_state(strict=True)


def test_signed_capture_has_named_binding_records_not_configuration_or_callbacks():
    source, state = captured()
    assert state[KEY] == {
        "version": 1,
        "bindings": {
            "menu": source.menu._dump_binding(),
            "other": source.other._dump_binding(),
        },
    }
    assert "__components__" not in state
    assert KEY not in source._capture_snapshot_state()
    assert "label" not in json.dumps(state[KEY])
    assert "callback" not in json.dumps(state[KEY])


@pytest.mark.parametrize("damage", ["extra", "wrong-type", "alias"])
def test_capture_rejects_undeclared_or_malformed_binding_exports(monkeypatch, damage):
    source = mounted()
    record = source.menu._dump_binding()
    if damage == "extra":
        record["private_token"] = "must-not-be-signed"
    elif damage == "wrong-type":
        record["open"] = "true"
    else:
        source._component_bindings["alias"] = source.menu
    monkeypatch.setattr(source.menu, "_dump_binding", lambda: record)
    with pytest.raises(ValueError, match="snapshot"):
        source._capture_snapshot_state(strict=True)


def test_restore_rebinds_current_callbacks_and_preserves_independent_identities():
    source, state = captured()
    target = MenuPage()
    target._restore_snapshot(state)
    assert target.menu is not source.menu
    assert target.menu.component_id == source.menu.component_id
    assert target.other.component_id == source.other.component_id
    assert target.menu.selected == "edit" and not target.menu.open
    assert target.other.open
    async_to_sync(target.other.select)(value="settings")
    assert (target.calls, target.result) == (2, "account:settings")
    assert (source.calls, source.result) == (1, "project:edit")


@pytest.mark.parametrize(
    "damage",
    ["version", "shape", "unknown", "duplicate", "value", "extra", "too-many", "private-name"],
)
def test_invalid_manifest_rejected_before_view_or_registry_mutation(damage):
    _, state = captured()
    manifest = state[KEY]
    if damage == "version":
        manifest["version"] = True
    elif damage == "shape":
        manifest["bindings"] = []
    elif damage == "unknown":
        manifest["bindings"]["removed"] = manifest["bindings"].pop("other")
    elif damage == "duplicate":
        manifest["bindings"]["other"]["binding_id"] = manifest["bindings"]["menu"]["binding_id"]
    elif damage == "value":
        manifest["bindings"]["other"]["open"] = 1
    elif damage == "too-many":
        manifest["bindings"] = dict.fromkeys(range(257), {})
    elif damage == "private-name":
        manifest["bindings"]["_menu"] = manifest["bindings"].pop("menu")
    else:
        manifest["bindings"]["other"]["callback"] = "select"
    target = MenuPage()
    with pytest.raises(ValueError, match="snapshot"):
        target._restore_snapshot(state)
    assert target._component_bindings == {} and target._components == {}
    assert "calls" not in vars(target) and "result" not in vars(target)


def test_replaced_descriptor_is_not_evaluated_during_restore():
    _, state = captured()

    class Changed(MenuPage):
        pass

    Changed.menu = property(lambda self: pytest.fail("untrusted descriptor evaluated"))
    with pytest.raises((TypeError, ValueError)):
        Changed()._restore_snapshot(state)


def test_restored_selection_uses_current_items_without_replaying_old_configuration():
    _, state = captured()

    class Changed(MenuPage):
        menu = DropdownMenu(label="Current", items=[{"label": "New", "value": "new"}])

    target = Changed()
    target._restore_snapshot(state)
    assert target.menu.selected == ""
    assert target.menu.component_id == state[KEY]["bindings"]["menu"]["binding_id"]
    assert target.menu.label == "Current" and target.menu.items == [
        {"label": "New", "value": "new"}
    ]
    assert target.calls == 1  # saved application state, not an emitted callback


def test_identity_collision_does_not_mutate_existing_bindings():
    _, state = captured()
    target = mounted()
    target.menu.open = True
    before = deepcopy(target.menu._dump_binding())
    state[KEY]["bindings"]["other"]["binding_id"] = target.menu.component_id
    with pytest.raises(ValueError, match="snapshot"):
        target._restore_snapshot(state)
    assert target.menu._dump_binding() == before
    assert target.calls == 0 and target.result == "initial"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_native_signed_mount_roundtrip_retains_ids_and_dispatches_to_correct_owner(
    monkeypatch,
):
    from django.test import override_settings
    from djust.tests.test_state_snapshot_signing import _connect, _receive_mount, _make_session_key

    slug = __name__ + ".SignedMenuPage"
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        session = await _make_session_key()
        first = await _connect(session)
        await first.send_json_to({"type": "mount", "view": slug, "url": "/"})
        initial = await _receive_mount(first)
        blob = initial["state_snapshot_signed"]
        from djust.security import unsign_snapshot

        state = json.loads(unsign_snapshot(blob, slug, session))
        identity = state[KEY]["bindings"]["menu"]["binding_id"]
        await first.disconnect()

        def must_not_mount(*args, **kwargs):
            pytest.fail("valid signed state fell back to mount")

        monkeypatch.setattr(SignedMenuPage, "mount", must_not_mount)
        second = await _connect(session)
        try:
            await second.send_json_to(
                {
                    "type": "live_redirect_mount",
                    "view": slug,
                    "url": "/",
                    "has_prerendered": True,
                    "state_snapshot": {"view_slug": slug, "state_json": blob},
                }
            )
            restored = await _receive_mount(second)
            assert identity in restored["html"] and 'aria-expanded="true"' in restored["html"]
            await second.send_json_to(
                {
                    "type": "event",
                    "event": "select",
                    "params": {"component_id": identity, "value": "edit"},
                }
            )
            response = await second.receive_json_from(timeout=3)
            assert response["type"] == "html_update"
            assert "project:edit" in response["html"]
        finally:
            await second.disconnect()


@pytest.mark.parametrize("existing", [False, True])
def test_binding_failure_rolls_back_registry_and_existing_state(monkeypatch, existing):
    _, state = captured()
    target = mounted()
    if existing:
        target.menu.open = True
    old_registry = dict(target._components)
    old_bindings = dict(target._component_bindings)
    old_state = target.menu._dump_binding() if existing else None
    original = DropdownMenu.__get__

    def bind(self, owner, owner_type=None):
        result = original(self, owner, owner_type)
        if owner is target and self.key == "other":
            raise RuntimeError("registration failed")
        return result

    monkeypatch.setattr(DropdownMenu, "__get__", bind)
    with pytest.raises(RuntimeError, match="registration failed"):
        target._restore_snapshot(state)
    assert target._components == old_registry
    assert target._component_bindings == old_bindings
    if existing:
        assert target.menu._dump_binding() == old_state
    assert (target.calls, target.result) == (0, "initial")


@pytest.mark.parametrize(
    "damage", ["unsigned", "wrong-session", "unknown-declaration", "invalid-state"]
)
@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_signed_transport_rejects_invalid_binding_restore_and_mounts_fresh(damage):
    from django.test import override_settings
    from djust.security import sign_snapshot
    from djust.tests.test_state_snapshot_signing import _connect, _receive_mount, _make_session_key

    source = mounted()
    await source.menu.select(value="edit")
    state = source._capture_snapshot_state(strict=True)
    slug = __name__ + ".SignedMenuPage"
    session = await _make_session_key()
    old_id = state[KEY]["bindings"]["menu"]["binding_id"]
    if damage == "unknown-declaration":
        state[KEY]["bindings"]["removed"] = state[KEY]["bindings"].pop("other")
    elif damage == "invalid-state":
        state[KEY]["bindings"]["other"]["open"] = "true"
    raw = json.dumps(state)
    blob = (
        raw
        if damage == "unsigned"
        else sign_snapshot(raw, slug, "another-session" if damage == "wrong-session" else session)
    )
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        socket = await _connect(session)
        try:
            await socket.send_json_to(
                {
                    "type": "live_redirect_mount",
                    "view": slug,
                    "url": "/",
                    "has_prerendered": True,
                    "state_snapshot": {"view_slug": slug, "state_json": blob},
                }
            )
            frame = await _receive_mount(socket)
            assert "initial" in frame["html"]
            assert "project:edit" not in frame["html"]
            assert old_id not in frame["html"]
            await socket.send_json_to(
                {
                    "type": "event",
                    "event": "select",
                    "params": {"component_id": old_id, "value": "edit"},
                }
            )
            rejected = await socket.receive_json_from(timeout=3)
            assert rejected["type"] == "error"
            assert "Component not found" in json.dumps(rejected)
        finally:
            await socket.disconnect()
