# ADR-036: staged strict client collector

This is a parser foundation, not activation of strict browser event bindings.
`_collectStrictEventParams` in `08-event-parsing.js` is internal and deliberately
not called by legacy binders. It does not resolve handler names, own component
identities or authorize an operation. The server contract remains authoritative.

## Collection rules

- Only `dj-value-*` contributes explicit application arguments. Third-party
  `data-*` and legacy `dj-params` do not silently become strict arguments.
- Kebab-case normalizes once. Duplicate normalized names and explicit/generated
  collisions reject; reserved routing and client bookkeeping names reject.
- Untyped strings remain unchanged. Numeric suffixes require complete ASCII
  numeric text, not permissive prefix parsing. Boolean suffixes use the strict
  true/false, yes/no, on/off and 1/0 spellings; `checked` is not a boolean literal.
- Typed integers must be representable as safe JavaScript integers. Use untyped
  strings for larger Python integers once binder integration is available.
  Nonfinite numbers reject. JSON integer tokens are checked before their spelling
  is lost to parsing; unsafe integer tokens reject, while finite float tokens
  retain normal floating-point semantics (including values such as `1e20`).
  Generated JavaScript numbers already lack lexical provenance; the future
  owner contract must distinguish declared integers from floats at that boundary.
- `array` and `list` suffixes require JSON arrays, not comma-separated text;
  `object` requires a JSON object. Unknown suffixes and malformed JSON reject.
- Generated and positional values become detached JSON snapshots. Accessors,
  sparse arrays, cycles and unsupported objects reject; non-enumerable hooks
  are not carried into the snapshot. Collection never invokes `toJSON`.
- Bounds are 32 levels, 10,000 visited nodes, 1,024 collection members and
  65,536 UTF-16 units of aggregate keys/values. Attribute literals have a separate
  65,536-unit pre-parse bound; numeric literal conversion is bounded to 1,024 units.
  The client text bound is conservative relative to Python's code-point bound.
  Transport byte limits remain separate.

All collector rejection messages are fixed and value-free. This does not yet
provide the final developer-facing error UI or the contract-aware parameter hint.

## Evidence and integration gates

`tests/js/strict_event_collection.test.js` runs the function from the generated
readable client bundle through a unique in-memory test seam. It does not create
a public helper export. The initial implementation tests failed before the
collector existed. Two additional tests reproduced retained mutable containers
and sparse-array acceptance before the snapshot fix. Independent review also
reproduced a serialization-hook budget bypass and verified the corrected
generated/positional snapshots without hooks or accessor execution.
Three additional failing regressions caught an overly restrictive draft that
rejected large finite floats. Float and integer-token checks are now separate;
exact BigInt boundary fixtures and escaped-string cases exercise the JSON token
scan without interpreting digits inside strings as numeric literals.

The final unchanged collector passed three complete JavaScript runs: 2,093 tests
in 190 files each (38.16, 37.60 and 37.81 seconds, four workers). Independent
review separately passed all 57 focused collector tests. The client asset suite
passed 91 tests, and bundle ESLint passed with zero warnings. These are bundle
and packaging checks, not rendered-browser acceptance or a full Python run.

The fixture exercises parser behavior, not real DOM event binding or network
delivery. Production legacy binders remain unchanged. Remaining integration:

1. Deliver public contracts scoped to the actual root/child/component owner,
   with navigation, removal, replacement and reconnect lifecycle handling.
2. Resolve the owner and handler before collection for every native binder.
   Generated `value`, field names and open form data need explicit conventions.
3. Check typed-wire declarations against the resolved Python contract; preserve
   enough provenance to detect incompatible wire hints and positional/name overlap.
4. Route rejection into visible, value-free error handling before optimistic or
   loading effects, then verify real browser and server transport behavior.

The existing global handler-name configuration map cannot satisfy item 1 for
two owners with the same handler name. Do not turn this staged helper into a
global strict switch or infer ADR acceptance from its unit tests.
