"""#4205: strict is the only event-parameter path.

A handler's signature is its whole contract. There is no per-handler or
project policy to select anything else, so a handler that never named a policy
and one that pins ``"strict"`` bind identically, and nothing the legacy path
did (conversion by hint, near-miss warnings, ``_target``, ``data-*``) is left.
"""

from __future__ import annotations

from typing import Any

import pytest

from djust import LiveView
from djust._parameter_metadata import handler_metadata, parameter_contract_manifest
from djust.decorators import event_handler
from djust.validation import get_handler_signature_info, validate_handler_params
from djust._parameter_metadata import expand_parameter_manifest


class _View(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root></div>"

    @event_handler()
    def plain(self, item_id: int, label: str = ""):
        pass

    @event_handler(parameter_policy="strict")
    def pinned(self, item_id: int, label: str = ""):
        pass

    @event_handler()
    def open_ended(self, **kwargs: Any):
        pass


class _Empty:
    """A root that declares no event handler at all."""


def _bound(name: str):
    return getattr(_View(), name)


@pytest.mark.parametrize("params", [{"item_id": "7"}, {"item_id": 7, "label": "x"}])
def test_a_handler_with_no_policy_binds_exactly_like_one_pinned_strict(params):
    plain = validate_handler_params(_bound("plain"), dict(params), "plain")
    pinned = validate_handler_params(_bound("pinned"), dict(params), "pinned")
    assert plain["valid"] is True and pinned["valid"] is True
    assert plain["bound_arguments"].arguments == pinned["bound_arguments"].arguments
    assert plain["coerced_params"] == pinned["coerced_params"]


def test_an_undeclared_argument_is_refused_whatever_its_name():
    for name in ("plain", "pinned"):
        for extra in ("_target", "field", "data_item", "item_idd"):
            result = validate_handler_params(_bound(name), {"item_id": 1, extra: "x"}, name)
            assert result["valid"] is False, (name, extra)
            assert "bound_arguments" not in result


def test_only_a_double_star_handler_takes_undeclared_names():
    result = validate_handler_params(_bound("open_ended"), {"anything": 1}, "open_ended")
    assert result["valid"] is True
    assert result["bound_arguments"].arguments["kwargs"] == {"anything": 1}


def test_a_missing_required_argument_is_refused():
    result = validate_handler_params(_bound("plain"), {}, "plain")
    assert result["valid"] is False
    assert "item_id" in result["error"]


def test_the_manifest_lists_only_strict_contracts_and_none_for_a_tree_with_no_handlers():
    manifest = parameter_contract_manifest(_View())
    assert manifest is not None
    handlers = expand_parameter_manifest(manifest)[0]["handlers"]
    assert {"plain", "pinned", "open_ended"} <= set(handlers)
    assert {h["policy"] for h in handlers.values()} == {"strict"}
    assert parameter_contract_manifest(_Empty()) is None


def test_the_published_handler_metadata_names_no_policy():
    for name in ("plain", "pinned"):
        metadata = handler_metadata(_bound(name))
        assert "parameter_policy" not in metadata["event_handler"], metadata
        assert [p["name"] for p in metadata["event_handler"]["params"]] == ["item_id", "label"]


def test_declaration_info_has_no_server_default_values():
    info = get_handler_signature_info(_View.plain, for_declaration=True)
    assert [p["name"] for p in info["params"]] == ["item_id", "label"]
    assert all("default" not in p for p in info["params"]), info["params"]


def test_the_legacy_policy_names_are_gone():
    import djust.validation as validation

    for name in (
        "coerce_parameter_types",
        "validate_parameter_types",
        "format_type_error_hint",
        "get_project_parameter_policy",
        "get_handler_parameter_policy",
    ):
        assert not hasattr(validation, name), name
