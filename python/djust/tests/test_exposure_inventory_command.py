"""ADR-038 E6-2: ``djust_exposure_inventory`` is a values-redacted migration aid.

The command reports, per LiveView class, the names legacy mode would infer and
the destinations each would reach, with a suggested explicit declaration. It
must never construct a view, evaluate a property or state factory, or print a
value. The destination claims are checked against the real legacy APIs
(``get_context_data``, ``get_state``, ``_get_private_state``) so the inventory
cannot drift into describing a policy the runtime does not implement.
"""

import json
from io import StringIO

import pytest
from django.core.management import call_command

from djust import LiveView, event_handler
from djust.live_view import PersistentLiveView
from djust.decorators import state

SECRET = "INVENTORY_SECRET_VALUE_7f3a91"
SECRET_BYTES = "INVENTORY_SECRET_BYTES_c02e"


def _exploding_factory():
    raise AssertionError("state factories must not be evaluated: " + SECRET)


class SecretHoldingView(LiveView):
    exposure_policy = "legacy"
    template = "<p>{{ count }}</p>"
    api_token = SECRET
    page_size = 25
    helper = object()
    total = state(0)
    rows = state(default_factory=list)
    lazy = state(default_factory=_exploding_factory)

    def mount(self, request, **kwargs):
        self.count = 0
        self.secret_key = SECRET
        self._token = SECRET_BYTES
        self.rows.append(SECRET)

    @property
    def label(self):
        raise AssertionError("properties must not be evaluated: " + SECRET)

    @event_handler()
    def bump(self, **kwargs):
        self.count += 1
        self._later = [SECRET]
        self.first, self.second = 1, SECRET
        setattr(self, "dynamic", SECRET)


class DeclaredExplicitView(LiveView):
    exposure_policy = "explicit"
    template = "<p>{{ count }}</p>"
    count = state(0, persist="server")
    note = state("", client=True)

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, **kwargs)


class ParityView(LiveView):
    """Small legacy view used to check the inventory against the runtime."""

    exposure_policy = "legacy"
    template = "<p>{{ count }}</p>"
    heading = "Inventory"
    level = state(1)

    def mount(self, request, **kwargs):
        self.count = 0
        self.items = ["a"]
        self._cursor = 3
        self.level = 2


class _ShapesMixin:
    """One name per suggestion shape: undeclared state(), instance, underscore."""

    template = "<p>{{ count }}</p>"
    level = state(1)

    def mount(self, request, **kwargs):
        self.count = 0
        self._cursor = 3


class FreshMountView(_ShapesMixin, LiveView):
    """Legacy view without snapshot persistence: remounts fresh on a reconnect."""

    exposure_policy = "legacy"


class SnapshotFlagView(_ShapesMixin, LiveView):
    exposure_policy = "legacy"
    enable_state_snapshot = True


class PersistentBaseView(_ShapesMixin, PersistentLiveView):
    pass


class PersistentOptedOutView(_ShapesMixin, PersistentLiveView):
    enable_state_snapshot = False


class ExplicitWithFlagView(_ShapesMixin, LiveView):
    """Under explicit exposure the flag has no effect, so nothing persists."""

    exposure_policy = "explicit"
    enable_state_snapshot = True


class DynamicFlagView(_ShapesMixin, LiveView):
    """A flag that is not a plain class value is not established statically."""

    exposure_policy = "legacy"

    @property
    def enable_state_snapshot(self):
        return False


# kind -> name in _ShapesMixin
SHAPES = {"state": "level", "instance": "count", "private": "_cursor"}
NOT_PERSISTING = (
    "FreshMountView",
    "PersistentOptedOutView",
    "ExplicitWithFlagView",
    "DynamicFlagView",
)
PERSISTING = ("SnapshotFlagView", "PersistentBaseView")

VIEW = f"{__name__}.SecretHoldingView"


def run(*args):
    out = StringIO()
    call_command("djust_exposure_inventory", *args, stdout=out)
    return out.getvalue()


def names_of(report, view):
    entry = next(v for v in report["views"] if v["view"] == view)
    return {item["name"]: item for item in entry["names"]}


@pytest.fixture
def no_construction(monkeypatch):
    def forbidden(self, *args, **kwargs):
        raise AssertionError("the inventory must not instantiate views: " + SECRET)

    monkeypatch.setattr(LiveView, "__init__", forbidden)


@pytest.mark.parametrize("fmt", ["text", "json"])
def test_output_names_every_inferred_name_and_no_value(no_construction, fmt):
    output = run("--view", VIEW, *(["--json"] if fmt == "json" else []))
    assert SECRET not in output
    assert SECRET_BYTES not in output
    for name in (
        "count",
        "secret_key",
        "_token",
        "api_token",
        "page_size",
        "total",
        "rows",
        "lazy",
        "label",
        "first",
        "second",
        "dynamic",
        "_later",
    ):
        assert name in output, name
    if fmt == "json":
        # Framework configuration and methods are not application state.
        listed = set(names_of(json.loads(output), VIEW))
        assert not listed & {"template", "mount", "bump", "exposure_policy", "request"}


def test_destinations_types_and_suggestions(no_construction):
    report = json.loads(run("--view", VIEW, "--json"))
    names = names_of(report, VIEW)

    public = {"template_context", "render_cache", "session_state", "client_state", "snapshot"}
    assert set(names["count"]["destinations"]) == public
    assert names["count"]["type"] == "int"
    assert names["count"]["suggestion"].startswith("count: int = state(...)")
    assert set(names["secret_key"]["destinations"]) == public
    # Types come from literals only; a name on the right-hand side stays unknown.
    assert names["secret_key"]["type"] is None
    assert names["first"]["type"] == "int" and names["second"]["type"] is None
    assert set(names["dynamic"]["destinations"]) == public

    # Assigned in mount(): persisted as private session state.
    assert names["_token"]["destinations"] == ["private_session"]
    # Assigned only after mount(): persisted only if registered by the app.
    assert names["_later"]["destinations"] == []
    assert names["_later"]["conditional"] == ["private_session"]

    # Class values reach render context and the context-derived session copy only.
    render_only = {"template_context", "render_cache", "session_state"}
    assert set(names["api_token"]["destinations"]) == render_only
    assert names["api_token"]["type"] == "str"
    assert set(names["label"]["destinations"]) == render_only
    assert names["label"]["kind"] == "property"
    assert names["helper"]["destinations"] == []  # not JSON-serializable

    # Public state() fields: context; their backing slot is saved privately
    # only when it holds a Django model (#2959, #1994), so conditionally.
    assert set(names["total"]["destinations"]) == render_only
    assert set(names["rows"]["destinations"]) == render_only
    assert names["rows"]["conditional"] == ["private_session"]
    assert names["rows"]["type"] == "list"
    assert names["lazy"]["type"] is None
    assert "get_context_data" in names["label"]["suggestion"]


def test_explicit_views_are_reported_with_their_policy(no_construction):
    view = f"{__name__}.DeclaredExplicitView"
    report = json.loads(run("--view", view, "--json"))
    entry = report["views"][0]
    assert entry["policy"] == "explicit"
    names = names_of(report, view)
    assert names["count"]["declared"] == {"persist": "server", "client": False}
    assert names["note"]["declared"] == {"persist": None, "client": True}


def test_default_discovery_finds_user_views(no_construction):
    report = json.loads(run("--json"))
    found = {v["view"] for v in report["views"]}
    assert VIEW in found
    assert not any(v.startswith("djust.live_view.") for v in found)
    assert SECRET not in json.dumps(report)


def test_inventory_matches_the_legacy_runtime():
    """Legacy control: the inventory's claims hold at the real legacy APIs."""
    view_name = f"{__name__}.ParityView"
    names = names_of(json.loads(run("--view", view_name, "--json")), view_name)

    view = ParityView()
    view.mount(request=None)
    view._snapshot_user_private_attrs()
    context = view.get_context_data()
    client = set(view.get_state())
    private = set(view._get_private_state())

    def listed(destination):
        return {n for n, item in names.items() if destination in item["destinations"]}

    assert listed("template_context") <= set(context)
    assert {"count", "items", "heading", "level"} <= listed("template_context")
    assert listed("client_state") == client == {"count", "items"}
    # A public state() field's slot is not saved privately since #2959 (the
    # public state carries it); the inventory lists it as conditional (a
    # model-holding value is still saved there, #1994).
    expected_private = {"_cursor"}
    assert expected_private <= private
    assert "_state_level" not in private
    assert {names[n].get("storage_key", n) for n in listed("private_session")} == expected_private
    assert names["level"]["conditional"] == ["private_session"]


def _shape_report(view_name):
    view = f"{__name__}.{view_name}"
    report = json.loads(run("--view", view, "--json"))
    return report["views"][0], names_of(report, view)


@pytest.mark.parametrize("kind", sorted(SHAPES))
@pytest.mark.parametrize("view_name", NOT_PERSISTING)
def test_no_persistence_grant_for_a_view_that_does_not_persist_today(
    no_construction, view_name, kind
):
    """#4023: such a view remounts fresh, so persist="server" would add behaviour."""
    entry, names = _shape_report(view_name)
    assert entry["persists_today"] is False
    item = names[SHAPES[kind]]
    assert item["kind"] == kind
    assert "persist=" not in item["suggestion"], item["suggestion"]
    assert "client=" not in item["suggestion"], item["suggestion"]


def test_plain_declaration_shapes_without_snapshot_persistence(no_construction):
    _, names = _shape_report("FreshMountView")
    assert names["level"]["suggestion"].startswith("keep level = state(...); ")
    assert names["count"]["suggestion"].startswith("count: int = state(...)  # ")
    assert names["_cursor"]["suggestion"].endswith("leave it instance-only")
    assert "state(" not in names["_cursor"]["suggestion"]


@pytest.mark.parametrize("view_name", PERSISTING)
def test_server_persistence_is_suggested_where_the_view_persists_today(no_construction, view_name):
    entry, names = _shape_report(view_name)
    assert entry["persists_today"] is True
    assert 'add persist="server"' in names["level"]["suggestion"]
    assert names["count"]["suggestion"].startswith('count: int = state(persist="server")')
    assert 'declare cursor: int = state(persist="server")' in names["_cursor"]["suggestion"]
    for name in SHAPES.values():
        # Never a client grant: the browser-readable choice is the owner's.
        assert 'persist="client"' not in names[name]["suggestion"]
        assert "client=" not in names[name]["suggestion"]


def test_text_output_has_no_persist_suggestion_without_snapshot_persistence(no_construction):
    output = run("--view", f"{__name__}.FreshMountView")
    assert "reconnect: fresh mount" in output
    assert "persist=" not in output
    output = run("--view", f"{__name__}.PersistentBaseView")
    assert "reconnect: restored (enable_state_snapshot)" in output
    assert 'persist="server"' in output


def test_reported_persistence_matches_the_runtime_flag():
    """The static read agrees with what the reconnect path reads off an instance."""
    for view_name in (*NOT_PERSISTING, *PERSISTING):
        entry, _ = _shape_report(view_name)
        cls = globals()[view_name]
        view = cls()
        legacy = view.exposure_policy == "legacy"
        assert entry["persists_today"] == (bool(view.enable_state_snapshot) and legacy), view_name
