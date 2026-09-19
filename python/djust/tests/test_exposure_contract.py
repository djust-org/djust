"""ADR-038 internal projection contract; this does not enable explicit views."""

import copy
from decimal import Decimal

import pytest

from djust._exposure import (
    ExposureContract,
    ExposureError,
    FieldExposure,
    StateLimits,
    clone_json_state,
)


def contract(**kwargs):
    return ExposureContract(
        "app.Counter",
        {
            "transient": FieldExposure(),
            "server": FieldExposure(persist="server"),
            "client": FieldExposure(client=True),
            "snapshot": FieldExposure(client=True, persist="client"),
        },
        **kwargs,
    )


def test_destinations_are_independent():
    values = {"transient": 1, "server": 2, "client": 3, "snapshot": [4], "secret": 5}
    schema = contract()
    assert schema.project(values, "server") == {"server": 2}
    assert schema.project(values, "client") == {"client": 3, "snapshot": [4]}
    assert schema.project(values, "snapshot") == {"snapshot": [4]}
    assert schema.project(values, "debug") == {
        "transient": "[redacted]",
        "server": "[redacted]",
        "client": 3,
        "snapshot": [4],
    }


def test_unselected_values_are_not_inspected_or_stringified():
    class Secret:
        def __str__(self):
            raise AssertionError("no str fallback")

        def __repr__(self):
            raise AssertionError("no repr fallback")

    values = {
        "transient": Secret(),
        "server": Secret(),
        "secret": Secret(),
        "client": 1,
        "snapshot": [],
    }
    assert contract().project(values, "client") == {"client": 1, "snapshot": []}
    assert contract().project(values, "debug")["server"] == "[redacted]"


@pytest.mark.parametrize("kwargs", [{"persist": "client"}, {"persist": "cookie"}, {"client": 1}])
def test_invalid_permissions_fail_closed(kwargs):
    with pytest.raises(ExposureError):
        FieldExposure(**kwargs)


@pytest.mark.parametrize(
    "name",
    [
        "__class__",
        "_secret",
        "a.b",
        "",
        "bad-name",
        "request",
        "exposure_policy",
        "mount",
        "get_context_data",
    ],
)
def test_invalid_field_names_are_rejected(name):
    with pytest.raises(ExposureError):
        ExposureContract("app.View", {name: FieldExposure()})


def test_schema_is_independent_of_order_and_values_but_not_permissions_or_version():
    first = ExposureContract("app.View", {"a": FieldExposure(), "b": FieldExposure(client=True)})
    same = ExposureContract("app.View", {"b": FieldExposure(client=True), "a": FieldExposure()})
    assert first.schema == same.schema
    assert first.schema != ExposureContract("app.View", {"a": FieldExposure()}, version=2).schema
    assert first.schema != ExposureContract("app.Other", dict(first.fields)).schema
    with pytest.raises(TypeError):
        first.fields["new"] = FieldExposure()


def test_snapshot_restore_validates_before_returning_detached_values():
    schema = contract()
    original = {"snapshot": ["ok"]}
    envelope = schema.capture(original, "snapshot")
    restored = schema.prepare_restore(envelope, "snapshot")
    restored["snapshot"].append("new")
    assert original == {"snapshot": ["ok"]}
    assert envelope["values"] == original


@pytest.mark.parametrize(
    "mutation", ["extra", "missing", "schema", "destination", "envelope", "legacy"]
)
def test_restore_rejects_widened_or_old_payloads(mutation):
    schema = contract()
    payload = schema.capture({"snapshot": []}, "snapshot")
    if mutation == "extra":
        payload["values"]["server"] = "MUST_NOT_RESTORE"
    elif mutation == "missing":
        payload["values"] = {}
    elif mutation == "schema":
        payload["schema"] = "old"
    elif mutation == "destination":
        payload["destination"] = "server"
    elif mutation == "envelope":
        payload["extra"] = "MUST_NOT_RESTORE"
    else:
        payload = {"snapshot": []}
    before = copy.deepcopy(payload)
    with pytest.raises(ExposureError):
        schema.prepare_restore(payload, "snapshot")
    assert payload == before


@pytest.mark.parametrize(
    "value",
    [
        object(),
        Decimal(1),
        b"bytes",
        (1,),
        {1},
        float("nan"),
        float("inf"),
        {1: "key"},
        2**53,
        "\ud800",
    ],
)
def test_codec_rejects_unsupported_values_without_fallback(value):
    with pytest.raises(ExposureError):
        clone_json_state(value)


def test_codec_rejects_subclasses_without_invoking_their_methods():
    class UntrustedDict(dict):
        def items(self):
            raise AssertionError("must not evaluate arbitrary containers")

    with pytest.raises(ExposureError):
        clone_json_state(UntrustedDict(secret="hidden"))


def test_cycles_depth_nodes_and_encoded_size_are_bounded():
    cycle = []
    cycle.append(cycle)
    for value, limits in [
        (cycle, StateLimits()),
        ([[[0]]], StateLimits(max_depth=2)),
        ([0] * 5, StateLimits(max_nodes=5)),
        ("\n" * 10, StateLimits(max_bytes=20)),
    ]:
        with pytest.raises(ExposureError):
            clone_json_state(value, limits=limits)


def test_payload_budget_is_shared_across_fields():
    schema = ExposureContract(
        "app.View",
        {"a": FieldExposure(client=True), "b": FieldExposure(client=True)},
        limits=StateLimits(max_bytes=30),
    )
    with pytest.raises(ExposureError):
        schema.project({"a": "x" * 15, "b": "y" * 15}, "client")


def test_errors_never_include_values_or_nested_keys():
    with pytest.raises(ExposureError) as caught:
        clone_json_state({"SENSITIVE_KEY": {"SENSITIVE_VALUE": object()}})
    assert "SENSITIVE" not in str(caught.value)


def test_compiler_uses_only_descriptors_and_does_not_evaluate_values():
    from djust.decorators import state

    def fail():
        raise AssertionError("must not inspect values at registration")

    class View:
        annotation_only: str
        public_attribute = "not declared state"
        count = state(default_factory=fail)
        visible = state(0, client=True)

        @property
        def expensive(self):
            return fail()

    compiled = ExposureContract.from_view_class(View)
    assert dict(compiled.fields) == {
        "count": FieldExposure(),
        "visible": FieldExposure(client=True),
    }


def test_inherited_grants_require_redeclaration_and_shadowing_follows_mro():
    from djust.decorators import state

    class Base:
        inherited = state(0)
        exposed = state(0, client=True)

    class Child(Base):
        pass

    with pytest.raises(ExposureError, match="redeclaration"):
        ExposureContract.from_view_class(Child)

    class Redeclared(Base):
        exposed = Base.exposed

    assert ExposureContract.from_view_class(Redeclared).fields["exposed"].client

    class Shadowed(Base):
        exposed = "ordinary attribute"

    assert set(ExposureContract.from_view_class(Shadowed).fields) == {"inherited"}


def test_restore_does_not_invoke_comparison_on_arbitrary_objects():
    class Malicious:
        def __eq__(self, other):
            raise AssertionError("no custom equality")

    schema = contract()
    payload = schema.capture({"snapshot": []}, "snapshot")
    payload["schema"] = Malicious()
    with pytest.raises(ExposureError):
        schema.prepare_restore(payload, "snapshot")


@pytest.mark.parametrize(
    "limits", [{"max_depth": 0}, {"max_depth": 65}, {"max_nodes": True}, {"max_bytes": -1}]
)
def test_invalid_resource_limits_are_rejected(limits):
    with pytest.raises(ExposureError):
        StateLimits(**limits)


def test_encoded_budget_matches_json_for_unicode_and_escapes():
    import json

    value = {"unicode": "é🎉", "escaped": '\n\t"', "numbers": [None, False, True, -1, 1.2]}
    size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    assert clone_json_state(value, limits=StateLimits(max_bytes=size)) == value
    with pytest.raises(ExposureError):
        clone_json_state(value, limits=StateLimits(max_bytes=size - 1))


def test_view_projection_never_reads_nonpermitted_descriptors():
    from djust.decorators import state

    def fail():
        raise AssertionError("server-only defaults must not be evaluated for client exports")

    class View:
        server = state(default_factory=fail, persist="server")
        client = state([1], client=True)
        transient = state(default_factory=fail)

        def get_context_data(self):
            return fail()

    compiled = ExposureContract.from_view_class(View)
    view = View()
    assert compiled.project_view(view, "client") == {"client": [1]}
    assert compiled.project_view(view, "debug") == {
        "server": "[redacted]",
        "client": [1],
        "transient": "[redacted]",
    }
    assert "_state_server" not in view.__dict__
    assert "_state_transient" not in view.__dict__


def test_stale_contract_cannot_read_a_replacement_property():
    from djust.decorators import state

    class View:
        count = state(0, client=True)

    compiled = ExposureContract.from_view_class(View)

    def fail(self):
        raise AssertionError("must not inspect replaced declarations")

    View.count = property(fail)
    with pytest.raises(ExposureError, match="no longer match"):
        compiled.project_view(View(), "client")
