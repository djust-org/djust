# ADR-036 strict parameter core: staged implementation

This is implementation evidence for P1, not a public strict-policy guide.
[`ParameterContract`](../../python/djust/_parameter_contract.py) is internal.
Neither a decorator switch nor project-wide strict dispatch is enabled by this
slice. Existing `validate_handler_params` callers retain legacy behavior.

## One compiled contract

Compile an already-bound server-owned callable once. Its signature, supported
annotations and parameter kinds provide both value-free metadata and validation.
Binding returns `inspect.BoundArguments`: callers must invoke with both `.args`
and `.kwargs`, preserving positional-only, keyword-only and variadic semantics.
Compilation and binding never invoke the handler. Defaults stay server-owned;
metadata reports whether an argument is required without serializing its default.

Named inputs require a supported annotation or explicit `Any`. An unannotated
catch-all is intentionally open. `Any`, including nested occurrences, is marked
as reduced checking in metadata. Unsupported annotations and unresolved references
raise `ContractError`; invalid invocations raise `ParameterError`. Client keys and
values are omitted from these parameter errors. Type errors name the declared
parameter and expected type; structural binding errors direct callers to check
missing, extra, duplicate and positional values.

The conversion matrix implements ADR-036's bounded scalar, nullable and list
types. Numeric text uses ASCII decimal grammar, not Python literal syntax:
underscores, Unicode digits and partial numbers are rejected. Surrounding ASCII
whitespace is removed for scalar conversion, never for a declared string.
`coerce=False` still validates and rejects merely convertible values. A date is
not a datetime; a bool is not an integer. Nonfinite float/Decimal values fail.
Lists are copied during typed member conversion, without mutating the input.

## Initial resource bounds

These fixed internal bounds apply before binding/conversion, in addition to the
transport's separate message-byte limit. They are deliberately conservative and
must be exercised in P2's forms/upload and dispatch matrix before activation.

| Limit | Boundary |
| --- | --- |
| Structural depth | 32; top-level keyword and positional containers each start at depth 0 |
| Visited nodes | 10,000 across both containers, including dictionary keys and repeated references |
| Collection length | 1,024 per built-in list, tuple or dictionary |
| Aggregate text | 65,536 Unicode code points, including keys, across both containers |
| Numeric conversion text | 1,024 characters after ASCII whitespace removal |
| Existing integer | At most 4,096 bits, measured without converting it to text |
| Declared Decimal | Finite, at most 1,024 coefficient digits and absolute stored exponent at most 10,000 |
| Annotation nesting / declared parameters | 32 / 1,024 |

Cyclic containers are rejected. Built-in list/tuple/dict/str/int subclasses are
rejected even under `Any`, without invoking custom iteration or conversion
methods. An opaque server-owned object under explicit `Any` is not inspected or
serialized; this escape hatch does not constitute an upload provider, object
authorization or wire codec. Arbitrary objects cannot be sent by JSON decoding.
Transport adapters must normalize their documented payload representation rather
than depend on accepting a `QueryDict` or another container subclass directly.

## Evidence and remaining work

[`test_parameter_contract.py`](../../python/djust/tests/test_parameter_contract.py)
has 144 cases covering conversion, all boolean spellings, disabled conversion,
signature binding and actual Python invocation, metadata/default redaction,
unsupported declarations, hostile conversion methods, bounds and cycles.
An independent review reproduced subclass budget bypasses; seven added regression
cases failed before the fix. An additional regression confirmed that annotation
metadata was silently discarded by Python's default hint resolver; unsupported
`Annotated` declarations now fail explicitly. The focused strict and existing
legacy/security validation set passes 377 tests.

Independent final review ran all 144 core cases successfully. Mypy passed 1,031
source files, and repository-pinned Ruff checks/formatting passed. The final
unchanged code passed the full three-root Python suite: 30,504 passed, 952 skipped
in 273.00 seconds with four workers. An earlier full run passed 30,503 tests but
predated the final annotation-metadata regression; only the final run is evidence
for the committed code.

P1 remains open for resolved policy registration and trusted argument separation.
P2 must integrate every invoker, preserve authentication and trusted source
injection, test open form/upload payloads and actual DOM/wire parsing, and reject
wire-type/collision conflicts. P3 must execute public examples and the complete
transport matrix. No browser, website, cross-worker or exposure-activation claim
is made by these core tests. See the
[implementation ledger](component-conventions-implementation.md).
