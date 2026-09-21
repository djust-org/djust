# ADR-034 concrete binding type proof (staged API)

Run `make test-component-binding-types` with mypy in the selected Python
environment and Pyright on PATH. Override `PYTHON` and `PYRIGHT_COMMAND` if needed.
No checker is installed by this target. Missing tools fail, never silently skip.
Checked with Python 3.12, mypy 1.16.1 and Pyright 1.1.408. The original isolated
prototype passed both; `prototype.py` now re-exports the **actual** private
framework dropdown and LiveView, with no alternate implementation or casts.

Both checkers now reject all twenty-one negative locations on the real framework
classes. The mypy configuration follows Django's installed source declarations
(`follow_untyped_imports` for `django.*`) instead of treating its View base as
`Any`. The LiveView stub declares the actual runtime constructor signature; an
AST regression checks that they agree. No Django-stubs dependency or checker
plugin was added, and no negative location or strict fixture check was removed.
This proves the listed component contracts, not complete typing of Django APIs.

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
- The protocol's positional-only bottom-type receiver (`NoReturn`, available on
  Python 3.10, equivalent here to `Never`) permits methods belonging to
  any owner class. It is a **decorator compatibility constraint**, not an
  invocation type. Production dispatch separately validates/rebinds the receiver;
  it never casts a real owner to `Never` to call this protocol.
- Descriptor access actually returns a concrete `DropdownMenu`, with isolated
  state and copied configuration. Both class and instance access are typed.
- Inherited declarations, ordinary method override/rename, typed configuration,
  keyword-only callbacks and async execution work in the positive fixture.
- Twenty-one invalid locations cover callback types/names/arity/returns, inherited
  override, misspelled/undefined symbols, read-only key and typed configuration,
  including the visibility ownership mode.

## What is NOT proven

The small runtime assertions here call callbacks directly. Separate framework
tests in `python/djust/tests/test_interactive_bindings.py` exercise real HTTP,
WebSocket, reconnect, native registry lookup, output injection and callback errors.
The new component remains private: browser keyboard/focus acceptance, observations,
collections, browser signed-navigation/debug transport and publication are not complete.
`runtime_check.py` supplies minimal Django settings for the standalone identity
assertions. Fixed pilot configuration is constructor-owned; `open` is mutable
state, while labels/items are not a dynamic configuration API yet.
