# ADR-034 type proof (not a framework API)

Run `make test-component-binding-types` with mypy in the selected Python
environment and Pyright on PATH. Override `PYTHON` and `PYRIGHT_COMMAND` if needed.
No checker is installed by this target. Missing tools fail, never silently skip.
Verified with Python 3.12, mypy 1.16.1 and Pyright 1.1.408.

The isolated configurations are essential: the framework's broad mypy config
suppresses errors in tests. This runner requires a diagnostic at **every**
`# error:` line in `negative.py`, rejects diagnostics elsewhere, and runs the
positive fixture's runtime assertions. A failed import is not a successful
negative test. Re-run after formatting because source lines are discovered fresh.

## What is proven

- Concrete output namespace, with no `Any`, catch-all attribute fallback, casts,
  checker plugin, stub facade, or ignored type errors.
- Callable protocols enforce named component/payload arguments and sync/async
  `None` results. A bounded type variable preserves the original method type.
- The protocol's positional-only `Never` receiver permits methods belonging to
  any owner class. It is a **decorator compatibility constraint**, not an
  invocation type. Production dispatch must separately validate/rebind the
  receiver; it must never cast a real owner to `Never` to call this protocol.
- Descriptor access actually returns a concrete `DropdownMenu`, with isolated
  state and copied configuration. Both class and instance access are typed.
- Inherited declarations, ordinary method override/rename, typed configuration,
  keyword-only callbacks and async execution work in the positive fixture.
- Twenty invalid locations cover callback types/names/arity/returns, inherited
  override, misspelled/undefined symbols, read-only key and typed configuration.

## What is NOT proven

`PrototypeOwner` is not `LiveView`. Decorators are identity functions; they do not
register or invoke subscribers. Runtime assertions call callbacks explicitly,
not through an event dispatcher. This proves the type shape and object identity,
not the subscription lifecycle or framework integration. Untyped callbacks,
duplicate/foreign declarations, conflicting decorators, subclass declaration
replacement, restored state and collection membership still need runtime checks.

Do not import this experiment into application or production framework code.
Its owner storage is intentionally a small test double, not template exposure or
session serialization. It has no rendering, authorization, routing or transport.
There is no change to the legacy `LiveComponent` descriptor or plain dropdown.
