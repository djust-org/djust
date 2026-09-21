"""ADR-036 strict core: deliberately not yet enabled by dispatch policy."""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal, Optional
from uuid import UUID

import pytest

from djust._parameter_contract import ContractError, ParameterContract, ParameterError


def contract(annotation):
    def handler(value):
        raise AssertionError("Validation must not invoke application code")

    handler.__annotations__ = {"value": annotation}
    return ParameterContract.compile(handler)


@pytest.mark.parametrize(
    "annotation,value,expected",
    [
        (str, "  text  ", "  text  "),
        (int, "42", 42),
        (int, "\t-42\n", -42),
        (int, "+0", 0),
        (float, "1.25e2", 125.0),
        (float, 2, 2.0),
        (bool, "true", True),
        (bool, "FALSE", False),
        (bool, " on ", True),
        (bool, "0", False),
        (Decimal, "1.25", Decimal("1.25")),
        (Decimal, 2, Decimal(2)),
        (date, "2026-09-21", date(2026, 9, 21)),
        (date, "2024-02-29", date(2024, 2, 29)),
        (
            UUID,
            "12345678-1234-5678-1234-567812345678",
            UUID(int=0x12345678123456781234567812345678),
        ),
        (Optional[int], None, None),
        (int | None, "2", 2),
        (list[int], [1, "2"], [1, 2]),
        (list[list[bool]], [["off", True]], [[False, True]]),
    ],
)
def test_conversion_matrix(annotation, value, expected):
    bound = contract(annotation).bind({"value": value})
    actual = bound.arguments["value"]
    assert actual == expected
    assert type(actual) is type(expected)


@pytest.mark.parametrize(
    "annotation,value",
    [
        (str, 7),
        (int, ""),
        (int, True),
        (int, "1_000"),
        (int, "42px"),
        (int, 1.5),
        (int, "١٢"),
        (int, "\u00a02"),
        (float, "nan"),
        (float, "inf"),
        (float, True),
        (float, "1_000"),
        (float, "1e9999"),
        (float, float("nan")),
        (bool, "not-a-bool"),
        (bool, 1),
        (bool, ""),
        (Decimal, "NaN"),
        (Decimal, Decimal("Infinity")),
        (Decimal, 1.25),
        (Decimal, True),
        (Decimal, "1_000"),
        (date, datetime(2026, 9, 21)),
        (date, "2026-02-30"),
        (date, "20260921"),
        (date, "2026-W39-1"),
        (UUID, "invalid"),
        (Optional[int], ""),
        (list[int], "1,2"),
        (list[int], [True]),
        (list[int], (1, 2)),
    ],
)
def test_invalid_values(annotation, value):
    with pytest.raises(ParameterError):
        contract(annotation).bind({"value": value})


@pytest.mark.parametrize("annotation", [int, float, bool, Decimal, UUID, date, list[int]])
def test_no_coercion_still_validates(annotation):
    with pytest.raises(ParameterError):
        contract(annotation).bind({"value": "1"}, coerce=False)


@pytest.mark.parametrize(
    "annotation,value",
    [
        (int, 1),
        (float, 1.0),
        (bool, False),
        (Decimal, Decimal("1")),
        (UUID, UUID(int=0)),
        (date, date(2026, 1, 1)),
        (list[int], [1]),
        (Any, object()),
    ],
)
def test_already_typed(annotation, value):
    assert contract(annotation).bind({"value": value}, coerce=False).arguments["value"] == value


@pytest.mark.parametrize(
    "annotation", [dict[str, int], int | str, Literal[1], list, "MissingType", datetime]
)
def test_unsupported_declarations_fail_closed(annotation):
    with pytest.raises(ContractError):
        contract(annotation)


def test_binding_and_invocation_preserve_python_signature():
    def handler(first: int, /, second: int = 2, *, last: bool):
        return first, second, last

    compiled = ParameterContract.compile(handler)
    bound = compiled.bind({"last": "yes"}, ["1"])
    assert handler(*bound.args, **bound.kwargs) == (1, 2, True)
    for params, args in [
        ({"first": 1, "last": True}, []),
        ({"last": True, "second": 3}, [1, 2]),
        ({}, [1, 2, True]),
        ({"last": True, "unknown": 1}, [1]),
    ]:
        with pytest.raises(ParameterError):
            compiled.bind(params, args)


def test_optional_still_required_and_defaults_are_not_exposed():
    with pytest.raises(ParameterError):
        contract(Optional[int]).bind({})

    def handler(value: str = "DEFAULT_SECRET"):
        return value

    compiled = ParameterContract.compile(handler)
    bound = compiled.bind({})
    assert handler(*bound.args, **bound.kwargs) == "DEFAULT_SECRET"
    assert "DEFAULT_SECRET" not in repr(compiled.metadata())


def test_bound_methods_varargs_and_open_kwargs():
    class Owner:
        def handler(self, *values: int, **fields: bool):
            return values, fields

    owner = Owner()
    compiled = ParameterContract.compile(owner.handler)
    bound = compiled.bind({"active": "false"}, ["1", "2"])
    assert owner.handler(*bound.args, **bound.kwargs) == ((1, 2), {"active": False})


def test_unannotated_named_parameters_require_explicit_any():
    def handler(value):
        pass

    with pytest.raises(ContractError):
        ParameterContract.compile(handler)


def test_unannotated_catchall_is_reported_as_reduced_checking():
    def handler(**fields):
        pass

    compiled = ParameterContract.compile(handler)
    assert compiled.metadata()[0]["reduced_checking"] is True
    assert compiled.bind({"field": object()}).kwargs.keys() == {"field"}


def test_errors_do_not_echo_payload_keys_or_values():
    for params in [{"PAYLOAD_SECRET": 1}, {"value": "PAYLOAD_SECRET"}]:
        with pytest.raises(ParameterError) as error:
            contract(int).bind(params)
        assert "PAYLOAD_SECRET" not in str(error.value)
        assert "PAYLOAD_SECRET" not in repr(error.value)


@pytest.mark.parametrize("value", [[0] * 1025, "x" * 65537, 1 << 4096, "1" * 1025])
def test_resource_limits(value):
    annotation = int if type(value) in (int, str) and not str(value).startswith("x") else Any
    with pytest.raises(ParameterError):
        contract(annotation).bind({"value": value})


def test_cycles_and_deep_payloads_rejected_even_for_any():
    cycle = []
    cycle.append(cycle)
    deep = []
    for _ in range(33):
        deep = [deep]
    for value in [cycle, deep]:
        with pytest.raises(ParameterError):
            contract(Any).bind({"value": value})


def test_aggregate_budget_and_decimal_exponent():
    for annotation, value in [(Any, [list(range(1024))] * 10), (Decimal, "1e10001")]:
        with pytest.raises(ParameterError):
            contract(annotation).bind({"value": value})


@pytest.mark.parametrize(
    "text,expected",
    [
        ("true", True),
        ("1", True),
        ("yes", True),
        ("on", True),
        ("false", False),
        ("0", False),
        ("no", False),
        ("off", False),
    ],
)
@pytest.mark.parametrize("padding", ["", " ", "\t", "\r\n", "\v\f"])
def test_all_boolean_spellings(text, expected, padding):
    assert (
        contract(bool).bind({"value": padding + text.upper() + padding}).arguments["value"]
        is expected
    )


@pytest.mark.parametrize(
    "annotation,value", [(float, 1), (Decimal, 1), (list[int], ["1"]), (Optional[int], "1")]
)
def test_no_coercion_rejects_convertible_values(annotation, value):
    with pytest.raises(ParameterError):
        contract(annotation).bind({"value": value}, coerce=False)


def test_resource_boundary_values():
    from djust._parameter_contract import MAX_COLLECTION, MAX_INTEGER_BITS, MAX_TEXT

    for annotation, value in [
        (Any, [0] * MAX_COLLECTION),
        (str, "x" * (MAX_TEXT - len("value"))),
        (int, (1 << MAX_INTEGER_BITS) - 1),
        (int, "9" * 1024),
        (Decimal, "1e10000"),
    ]:
        contract(annotation).bind({"value": value})


@pytest.mark.parametrize("params,args", [(None, []), ({}, None), ({1: 2}, []), ({}, "text")])
def test_malformed_invocation_envelopes(params, args):
    with pytest.raises(ParameterError):
        contract(int).bind(params, args)


def test_values_with_hostile_protocols_are_not_stringified_or_converted():
    class Hostile:
        def __str__(self):
            raise AssertionError("must not stringify")

        def __repr__(self):
            raise AssertionError("must not repr")

        def __int__(self):
            raise AssertionError("must not convert")

    for annotation in (str, int, float, Decimal, date, UUID, bool):
        with pytest.raises(ParameterError):
            contract(annotation).bind({"value": Hostile()})


def test_bind_does_not_mutate_input_or_retain_values_between_calls():
    compiled = contract(list[int])
    payload = {"value": ["1", "2"]}
    first = compiled.bind(payload)
    first.arguments["value"].append(3)
    assert payload == {"value": ["1", "2"]}
    assert compiled.bind(payload).arguments["value"] == [1, 2]
    metadata = compiled.metadata()
    metadata[0]["type"] = "corrupted"
    assert compiled.metadata()[0]["type"] == "list[int]"


def test_numeric_conversions_are_independent_of_decimal_context():
    from decimal import localcontext

    with localcontext() as context:
        context.prec = 2
        assert contract(Decimal).bind({"value": "123.456"}).arguments["value"] == Decimal("123.456")


def test_nested_optional_any_reduced_checking_metadata():
    assert contract(list[Optional[Any]]).metadata()[0]["reduced_checking"] is True


@pytest.mark.parametrize("base", [list, tuple, dict, str, int])
def test_any_rejects_builtin_subclasses_without_invoking_protocols(base):
    class Payload(base):
        def __len__(self):
            raise AssertionError("Do not call client-controlled protocols")

        def __iter__(self):
            raise AssertionError("Do not call client-controlled protocols")

    with pytest.raises(ParameterError):
        contract(Any).bind({"value": Payload()})


@pytest.mark.parametrize("cyclic", [False, True])
def test_review_any_subclass_resource_bypass(cyclic):
    class PayloadList(list):
        pass

    value = PayloadList([0] * 1025)
    if cyclic:
        value.clear()
        value.append(value)
    with pytest.raises(ParameterError):
        contract(Any).bind({"value": value})


def test_unsupported_annotation_metadata_is_not_silently_discarded():
    with pytest.raises(ContractError):
        contract(Annotated[int, "not-a-supported-contract"])
