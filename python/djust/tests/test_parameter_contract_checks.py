"""ADR-036 P1: strict declaration problems are reported before an event arrives.

The fixture views live in a module written to a temporary directory and
imported under a top-level name, so the annotations are deferred
(``from __future__ import annotations``), the classes are reachable by
qualified name exactly like application code, and they are unloaded after
each test: no other test's system-check run ever sees them.
"""

from __future__ import annotations

import gc
import importlib.util
import sys
import textwrap
import types
import weakref

import pytest

from djust._parameter_contract import ContractError
from djust.checks.configuration import check_configuration
from djust._parameter_metadata import DECLARATION_OWNER as _DECLARATION_OWNER
from djust.checks.parameters import check_event_parameter_contracts
from djust.config import config
from djust.validation import (
    get_strict_handler_contract,
    validate_handler_params,
    validated_call_arguments,
)

MODULE = "strict_contract_check_fixture"

SOURCE = """
from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Annotated, Any, Optional

from djust import LiveView
from djust.components._interactive import DropdownMenu
from djust.components.base import LiveComponent
from djust.decorators import event_handler, server_function

if TYPE_CHECKING:
    from decimal import Decimal as CheckingOnly

ModuleAlias = int
Shadowed = str


class Valid(LiveView):
    template = "<div dj-root></div>"
    ItemId = int
    Shadowed = int

    @event_handler(parameter_policy="strict")
    def shadowed(self, value: Shadowed):
        return value

    @event_handler(parameter_policy="strict")
    def class_local(self, item_id: ItemId, when: Optional[date] = None) -> Valid:
        return (item_id, when)

    @event_handler(parameter_policy="strict")
    def module_alias(self, _count: ModuleAlias, /, tags: list[str], *, flag: bool = False, **rest: Any):
        return (_count, tags, flag, rest)

    @event_handler(parameter_policy="strict")
    def defined_later(self, value: "DefinedLater"):
        return value

    @server_function(parameter_policy="strict")
    def rpc(self, query: str) -> list[str]:
        return [query]

    @event_handler(parameter_policy="strict", params=["amount"])
    def matching_params(self, amount: int):
        return amount

    @event_handler
    def legacy_unannotated(self, value, **kwargs):
        return value

    @event_handler(parameter_policy="legacy")
    def explicit_legacy(self, view_id, **kwargs):
        return view_id

    @event_handler(parameter_policy="strict")
    async def async_ok_without_actors(self, count: int):
        return count

    @event_handler
    def legacy_closed(self, value: int):
        return value


DefinedLater = float


class StrictMixin:
    MixinLocal = int

    @event_handler(parameter_policy="strict")
    def mixin_local(self, value: MixinLocal):
        return value

    @event_handler(parameter_policy="strict")
    def mixin_invalid(self, value: dict):
        pass


class UsesMixin(StrictMixin, LiveView):
    template = "<div dj-root></div>"


class Sibling(LiveView):
    template = "<div dj-root></div>"
    SiblingOnly = int


class Invalid(LiveView):
    template = "<div dj-root></div>"

    @event_handler(parameter_policy="strict")
    def unresolved(self, value: NotDefinedAnywhere):
        pass

    @event_handler(parameter_policy="strict")
    def type_checking_only(self, value: CheckingOnly):
        pass

    @event_handler(parameter_policy="strict")
    def other_class_local(self, value: SiblingOnly):
        pass

    @event_handler(parameter_policy="strict")
    def mapping(self, value: dict[str, int]):
        pass

    @event_handler(parameter_policy="strict")
    def ambiguous_union(self, value: int | str):
        pass

    @event_handler(parameter_policy="strict")
    def optional_mapping(self, value: Optional[dict]):
        pass

    @event_handler(parameter_policy="strict")
    def bare_list(self, value: list):
        pass

    @event_handler(parameter_policy="strict")
    def list_of_mapping(self, value: list[dict]):
        pass

    @event_handler(parameter_policy="strict")
    def annotated(self, value: Annotated[int, "meta"]):
        pass

    @event_handler(parameter_policy="strict")
    def unannotated(self, value):
        pass

    @event_handler(parameter_policy="strict")
    def reserved_view_id(self, view_id: str):
        pass

    @event_handler(parameter_policy="strict")
    def reserved_keyword_only(self, *, _token: str):
        pass

    @server_function(parameter_policy="strict")
    def rpc_unsupported(self, value: set[int]):
        pass

    @event_handler(parameter_policy="strict", params=["other"])
    def conflicting_params(self, amount: int):
        pass

    @event_handler
    def inherits_project_policy(self, value, **kwargs):
        pass


class InheritsInvalid(Invalid):
    pass


class ActorView(LiveView):
    template = "<div dj-root></div>"
    use_actors = True

    @event_handler(parameter_policy="strict")
    async def async_strict(self, count: int):
        pass

    @event_handler(parameter_policy="strict")
    def sync_strict(self, count: int):
        pass

    @event_handler
    async def async_legacy(self, count, **kwargs):
        pass


class Subscribed(LiveView):
    template = "<div dj-root>{{ menu }}</div>"
    menu = DropdownMenu(label="Menu", items=[{"label": "Edit", "value": "edit"}])
    other = DropdownMenu(label="Other", items=[{"label": "Edit", "value": "edit"}])

    @menu.on.selected
    def picked(self, component: DropdownMenu, value: str) -> None:
        pass

    @other.on.selected
    def loose(self, component: DropdownMenu, value: object) -> None:
        pass


class Widget(LiveComponent):
    template = "<div></div>"

    @event_handler(parameter_policy="strict")
    def pick(self, value: frozenset):
        pass
"""

# Handler -> (check id, exact message tail after "<label>: ").
EXPECTED_INVALID = {
    "unresolved": (
        "djust.V016",
        "strict event parameter contract is invalid: Parameter 'value' annotation cannot "
        "be resolved; define the name in the module or the defining class body, outside "
        "an 'if TYPE_CHECKING:' block.",
    ),
    "mapping": (
        "djust.V016",
        "strict event parameter contract is invalid: Parameter 'value': Unsupported "
        "annotation; use str, int, float, bool, Decimal, UUID, date, Optional[T], "
        "list[T] or explicit Any.",
    ),
    "unannotated": (
        "djust.V016",
        "strict event parameter contract is invalid: Parameter 'value' requires an "
        "annotation; use Any for unchecked input.",
    ),
    "reserved_view_id": (
        "djust.V016",
        "strict event parameter contract is invalid: Parameter 'view_id' uses a "
        "framework-reserved name; application arguments cannot be named view_id, "
        "component_id or start with '_'.",
    ),
    "conflicting_params": (
        "djust.V018",
        "@event_handler(params=['other']) disagrees with the strict signature's "
        "parameters ['amount'].",
    ),
}
for _name in ("type_checking_only", "other_class_local"):
    EXPECTED_INVALID[_name] = EXPECTED_INVALID["unresolved"]
for _name in (
    "ambiguous_union",
    "optional_mapping",
    "bare_list",
    "list_of_mapping",
    "annotated",
    "rpc_unsupported",
):
    EXPECTED_INVALID[_name] = EXPECTED_INVALID["mapping"]
EXPECTED_INVALID["reserved_keyword_only"] = (
    "djust.V016",
    EXPECTED_INVALID["reserved_view_id"][1].replace("'view_id' uses", "'_token' uses"),
)


def _load(tmp_path) -> types.ModuleType:
    path = tmp_path / ("%s.py" % MODULE)
    path.write_text(textwrap.dedent(SOURCE), encoding="utf-8")
    spec = importlib.util.spec_from_file_location(MODULE, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE] = module
    spec.loader.exec_module(module)
    return module


def _unload(module: types.ModuleType) -> None:
    sys.modules.pop(MODULE, None)
    # pytest still holds the fixture value during teardown, so the classes can
    # outlive this call; ``abstract`` makes every per-class check skip them.
    for value in vars(module).values():
        if isinstance(value, type) and value.__module__ == MODULE:
            value.abstract = True
    gc.collect()


@pytest.fixture
def fixture_module(tmp_path):
    old_policy = config.get("event_parameter_policy", "legacy")
    module = _load(tmp_path)
    try:
        yield module
    finally:
        config.set("event_parameter_policy", old_policy)
        _unload(module)


def test_unloaded_fixture_views_are_collected(tmp_path):
    module = _load(tmp_path)
    assert _messages(module)
    probe = weakref.ref(module.Invalid)
    _unload(module)
    del module
    gc.collect()
    assert probe() is None


def _messages(module: types.ModuleType) -> dict[str, list[tuple[str, str]]]:
    """This module's messages, keyed by the owner-qualified handler label."""
    found: dict[str, list[tuple[str, str]]] = {}
    for message in check_event_parameter_contracts(None):
        if message.msg.startswith(MODULE + "."):
            label, _, tail = message.msg.partition(": ")
            found.setdefault(label, []).append((message.id, tail))
    return found


def _label(owner: str, handler: str) -> str:
    return "%s.%s.%s()" % (MODULE, owner, handler)


def test_invalid_matrix_reports_exactly_one_expected_message_each(fixture_module):
    found = _messages(fixture_module)
    for handler, expected in EXPECTED_INVALID.items():
        assert found.pop(_label("Invalid", handler)) == [expected], handler
    assert found.pop(_label("ActorView", "async_strict")) == [
        (
            "djust.V017",
            "async strict event handler on an actor view (use_actors=True); actor "
            "dispatch rejects it before the handler runs.",
        )
    ]
    assert found.pop(_label("Widget", "pick")) == [("djust.V016", EXPECTED_INVALID["mapping"][1])]
    assert found.pop(_label("Subscribed", "loose")) == [
        (
            "djust.V016",
            "other.selected output callback contract is invalid: Parameter 'value': "
            "Unsupported annotation; use str, int, float, bool, Decimal, UUID, date, "
            "Optional[T], list[T] or explicit Any.",
        )
    ]
    found.pop(
        "%s (declared as %s.StrictMixin.mixin_invalid)"
        % (_label("UsesMixin", "mixin_invalid"), MODULE)
    )
    # Valid declarations, legacy handlers (unannotated, reserved names, async
    # actors) and the inherited copies of Invalid's handlers report nothing.
    assert found == {}


def test_messages_carry_location_and_severity(fixture_module):
    messages = [
        m
        for m in check_event_parameter_contracts(None)
        if m.msg.startswith(_label("Invalid", "unresolved"))
        or m.msg.startswith(_label("Invalid", "conflicting_params"))
    ]
    assert {m.id: m.level for m in messages} == {"djust.V016": 40, "djust.V018": 30}
    for message in messages:
        assert message.file_path.endswith(MODULE + ".py")
        assert message.line_number is not None and message.fix_hint and message.hint


def test_mixin_handler_is_reported_once_under_its_user_with_its_declaration(fixture_module):
    found = _messages(fixture_module)
    label = "%s (declared as %s.StrictMixin.mixin_invalid)" % (
        _label("UsesMixin", "mixin_invalid"),
        MODULE,
    )
    assert found[label] == [EXPECTED_INVALID["mapping"]]
    assert not any("mixin_local" in key for key in found)


def test_project_strict_policy_reports_inheriting_handlers_but_not_legacy_overrides(
    fixture_module,
):
    config.set("event_parameter_policy", "strict")
    found = _messages(fixture_module)
    assert found[_label("Invalid", "inherits_project_policy")] == [EXPECTED_INVALID["unannotated"]]
    assert found[_label("Valid", "legacy_unannotated")] == [EXPECTED_INVALID["unannotated"]]
    assert _label("Valid", "explicit_legacy") not in found
    assert _label("ActorView", "async_legacy") in found  # unannotated under strict


def test_invalid_project_policy_is_one_configuration_error(fixture_module):
    config.set("event_parameter_policy", "Strict")
    c022 = [m for m in check_configuration(None) if m.id == "djust.C022"]
    assert [m.msg for m in c022] == [
        "LIVEVIEW_CONFIG['event_parameter_policy'] is 'Strict'; it must be 'legacy' or 'strict'."
    ]
    assert c022[0].level == 40
    found = _messages(fixture_module)
    # Handlers inheriting the invalid value are covered by C022, not repeated;
    # explicitly strict declarations are still checked.
    assert _label("Invalid", "inherits_project_policy") not in found
    assert found[_label("Invalid", "unresolved")] == [EXPECTED_INVALID["unresolved"]]


@pytest.mark.parametrize("policy", [None, "legacy", "strict"])
def test_valid_project_policy_has_no_configuration_error(policy):
    old = config.get("event_parameter_policy", "legacy")
    try:
        config.set("event_parameter_policy", policy)
        assert not [m for m in check_configuration(None) if m.id == "djust.C022"]
    finally:
        config.set("event_parameter_policy", old)


def test_invalid_handler_policy_metadata_is_reported(fixture_module):
    function = fixture_module.Valid.__dict__["rpc"]
    function._djust_decorators["server_function"]["parameter_policy"] = "Strict"
    assert _messages(fixture_module)[_label("Valid", "rpc")] == [
        (
            "djust.V016",
            "strict event parameter contract is invalid: parameter_policy must be "
            "'legacy' or 'strict'.",
        )
    ]


def test_suppression(fixture_module, settings):
    settings.DJUST_CONFIG = {"suppress_checks": ["V016", "V017", "V018"]}
    assert _messages(fixture_module) == {}


# --- the check and dispatch share one resolver -----------------------------

VALID_CALLS = {
    "class_local": ({"item_id": "7", "when": "2026-09-24"}, [], "(7, datetime.date(2026, 9, 24))"),
    "module_alias": (
        {"tags": ["a"], "flag": "yes", "extra": 1},
        ["3"],
        "(3, ['a'], True, {'extra': 1})",
    ),
    "defined_later": ({"value": "1.5"}, [], "1.5"),
    # The class body's name wins over the module's, as in eager evaluation.
    "shadowed": ({"value": "5"}, [], "5"),
    "rpc": ({"query": "q"}, [], "['q']"),
}


@pytest.mark.parametrize("handler", sorted(VALID_CALLS))
def test_check_accepted_declarations_bind_and_run_at_dispatch(fixture_module, handler):
    assert _label("Valid", handler) not in _messages(fixture_module)
    view = fixture_module.Valid()
    method = getattr(view, handler)
    declaration = types.MethodType(fixture_module.Valid.__dict__[handler], _DECLARATION_OWNER)
    assert get_strict_handler_contract(method) is get_strict_handler_contract(declaration)
    params, positional, expected = VALID_CALLS[handler]
    validation = validate_handler_params(
        method, {**params, "_args": positional} if positional else params, handler
    )
    assert validation["valid"], validation["error"]
    args, kwargs = validated_call_arguments(validation)
    assert repr(method(*args, **kwargs)) == expected


@pytest.mark.parametrize(
    "handler",
    sorted(h for h, (check_id, _) in EXPECTED_INVALID.items() if check_id == "djust.V016"),
)
def test_check_rejected_declarations_are_rejected_at_dispatch(fixture_module, handler):
    reported = _messages(fixture_module)[_label("Invalid", handler)]
    view = fixture_module.Invalid()
    with pytest.raises(ContractError) as runtime:
        validate_handler_params(getattr(view, handler), {"value": "1"}, handler)
    assert reported == [
        ("djust.V016", "strict event parameter contract is invalid: %s" % runtime.value)
    ]


def test_function_local_classes_resolve_against_module_globals_only():
    # A class defined inside a function is not reachable by qualified name, so
    # dispatch (and therefore the check) cannot see its class-body names.
    from djust.decorators import event_handler

    class Local:
        Alias = int

        @event_handler(parameter_policy="strict")
        def handler(self, value: Alias):
            pass

    with pytest.raises(ContractError, match="'value' annotation cannot be resolved"):
        get_strict_handler_contract(Local().handler)


def test_actor_bridge_rejects_what_v017_reports(fixture_module):
    from djust.validation import actor_handler_arguments

    view = fixture_module.ActorView()
    with pytest.raises(ContractError):
        actor_handler_arguments(view.async_strict, {"count": 1})
    assert actor_handler_arguments(view.sync_strict, {"count": "2"}) == ((2,), {})


def test_no_handler_is_told_to_add_kwargs(fixture_module):
    from djust.checks.components import check_liveviews

    # V007 is retired (ADR-037 D3): closed signatures, legacy or strict, are
    # never reported for lacking **kwargs.
    assert [m for m in check_liveviews(None) if m.id == "djust.V007"] == []
