"""Uniqueness canary (#1859) for #2070 -- ``djust.S004`` was allocated to
TWO different checks: "DEBUG=True with non-localhost ALLOWED_HOSTS"
(``python/djust/checks/configuration.py``) and "LiveView gates auth via
dispatch()" (``python/djust/checks/security.py``, originally shipped in
PR #154 / finding #14). Suppressing one via ``suppress_checks`` /
``SILENCED_SYSTEM_CHECKS`` silently suppressed the other, and
``docs/system-checks.md`` could describe only one meaning per ID. The
dispatch-auth check was reallocated to ``djust.S012`` (the true next-free
S-ID: ``S010`` is intentionally reserved -- see the note in
docs/system-checks.md -- for a not-yet-shipped rate-limit-presence check,
and ``S011`` is already taken, so ``S012`` is the first genuinely free slot).

This test enumerates every djust check ID from the REAL registration
source: Django's check registry (``django.core.checks.registry.registry``),
filtered to functions tagged ``"djust"`` -- i.e. exactly the functions
``@register("djust")`` wires up, not a directory listing. Runtime execution
alone can't enumerate every id a check can EMIT (most branches are
conditional on project state -- e.g. S004 only fires when ``DEBUG=True``),
so once the set of registered FUNCTIONS is known from the runtime registry,
each function's owning module is AST-parsed (the documented fallback for
"IDs are literals embedded in conditional code") to find every
``id="djust.XXXX"`` keyword literal, tagged with the NEAREST ENCLOSING
FUNCTION that emits it.

The invariant: no id string may be emitted from more than one DISTINCT
function. A single check function legitimately emitting the same id from
multiple branches (e.g. ``check_liveviews`` emits ``djust.V009`` from two
code paths for the same logical check) is fine -- that's one function, one
concern. Two DIFFERENT functions emitting the same id -- the #2070 bug
shape -- is not.

Gate-off (#1468) performed manually during development, per the CLAUDE.md
uniqueness-canary rule (#1859): temporarily reverted security.py's
``id="djust.S012"`` occurrences back to ``id="djust.S004"``—
``test_no_check_id_is_emitted_by_more_than_one_function`` failed with:

    duplicate check IDs shared by multiple functions:
    djust.S004 is emitted by 2 different check functions:
    [('djust.checks.configuration.check_configuration', 830),
     ('djust.checks.security.check_security', 171),
     ('djust.checks.security.check_security', 208)]

Then restored. See the PR description for the exact diff used.
"""

from __future__ import annotations

import ast
import inspect
import re
import sys
from collections import defaultdict
from pathlib import Path

# Importing djust.checks fires every @register("djust") decorator (the
# #1822 package-split side-effect import) so the registry below is
# populated regardless of import order.
import djust.checks  # noqa: F401
from django.core.checks.registry import registry

_ID_RE = re.compile(r"^djust\.[A-Z]+[0-9]+$")


def _registered_djust_check_functions() -> list:
    """The REAL source of registration: every function Django's check
    registry has under the 'djust' tag. Preferred over grepping
    python/djust/checks/*.py because it can't be fooled by a check
    function that's defined but never actually wired up with
    @register(...), and it would also catch a djust-tagged check
    registered from somewhere outside that directory."""
    funcs = []
    for check in registry.registered_checks:
        tags = getattr(check, "tags", ()) or ()
        if "djust" in tags:
            funcs.append(check)
    return funcs


class _EnclosingFunctionIdVisitor(ast.NodeVisitor):
    """AST walk recording every ``id="djust.XXXX"`` keyword literal found in
    a module, tagged with the dotted qualname of its nearest enclosing
    function (module.func, or module.outer.inner for a nested def)."""

    def __init__(self, module_name: str) -> None:
        self.module_name = module_name
        self._stack: list[str] = []
        self.hits: list[tuple[str, str, int]] = []  # (id, qualname, lineno)

    def _qualname(self) -> str:
        if not self._stack:
            # An id literal outside any function -- shouldn't happen for
            # CheckMessage construction, but attribute it to the module so
            # it's still visible in a failure message rather than silently
            # dropped.
            return self.module_name
        return "%s.%s" % (self.module_name, ".".join(self._stack))

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._stack.append(node.name)
        self.generic_visit(node)
        self._stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]  # noqa: N815

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        for kw in node.keywords:
            if (
                kw.arg == "id"
                and isinstance(kw.value, ast.Constant)
                and isinstance(kw.value.value, str)
                and _ID_RE.match(kw.value.value)
            ):
                self.hits.append((kw.value.value, self._qualname(), node.lineno))
        self.generic_visit(node)


def _collect_all_check_id_emissions() -> list[tuple[str, str, int, str]]:
    """(id, enclosing-function-qualname, lineno, filename) for every
    check-message id literal found in every module that owns at least one
    registered djust check function."""
    modules = {}
    for func in _registered_djust_check_functions():
        mod = sys.modules.get(func.__module__)
        if mod is not None:
            modules[func.__module__] = mod

    all_hits: list[tuple[str, str, int, str]] = []
    for mod_name, mod in modules.items():
        filename = inspect.getsourcefile(mod)
        assert filename, "could not find source file for module %s" % mod_name
        source = Path(filename).read_text()
        tree = ast.parse(source, filename=filename)
        visitor = _EnclosingFunctionIdVisitor(mod_name)
        visitor.visit(tree)
        for id_, qualname, lineno in visitor.hits:
            all_hits.append((id_, qualname, lineno, filename))
    return all_hits


def test_registry_discovers_check_functions_across_multiple_modules():
    """Sanity/non-vacuousness guard: the registry must actually surface
    check functions spread across several modules -- if a future refactor
    silently broke discovery (e.g. everything ending up registered from a
    single module, or the registry returning nothing), the uniqueness test
    below would vacuously pass with zero real coverage."""
    funcs = _registered_djust_check_functions()
    modules = {f.__module__ for f in funcs}
    assert len(funcs) >= 8, "expected at least 8 registered djust check functions, found %d: %r" % (
        len(funcs),
        sorted(f.__name__ for f in funcs),
    )
    assert len(modules) >= 6, (
        "expected registered checks spread across at least 6 modules, found %r" % sorted(modules)
    )


def test_no_check_id_is_emitted_by_more_than_one_function():
    """#2070 / #1859 uniqueness canary: no djust check ID may be emitted by
    more than one DISTINCT function. Two functions sharing an ID is exactly
    the S004 bug -- suppressing one check silently suppressed a
    logically-unrelated one, and docs/system-checks.md could only describe
    one meaning per ID."""
    hits = _collect_all_check_id_emissions()
    assert hits, "no check-message ids were discovered at all -- enumeration is broken"

    by_id_functions: dict[str, set[str]] = defaultdict(set)
    locations: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for id_, qualname, lineno, _filename in hits:
        by_id_functions[id_].add(qualname)
        locations[id_].append((qualname, lineno))

    duplicates = {id_: funcs for id_, funcs in by_id_functions.items() if len(funcs) > 1}
    assert not duplicates, "duplicate check IDs shared by multiple functions:\n" + "\n".join(
        "%s is emitted by %d different check functions: %s"
        % (id_, len(by_id_functions[id_]), sorted(locations[id_]))
        for id_ in sorted(duplicates)
    )


def test_djust_s012_is_the_reallocated_dispatch_auth_check():
    """Pin: djust.S012 (not S004) is the id security.py's dispatch-auth
    check now emits, and it lives in a distinct function from
    configuration.py's djust.S004 (DEBUG/ALLOWED_HOSTS) check. A narrower,
    more readable companion to the general uniqueness sweep above."""
    hits = _collect_all_check_id_emissions()

    s012_functions = {qualname for id_, qualname, _lineno, _filename in hits if id_ == "djust.S012"}
    assert s012_functions == {"djust.checks.security.check_security"}, (
        "djust.S012 should be emitted only by check_security, found: %r" % s012_functions
    )

    s004_functions = {qualname for id_, qualname, _lineno, _filename in hits if id_ == "djust.S004"}
    assert s004_functions == {"djust.checks.configuration.check_configuration"}, (
        "djust.S004 should be emitted only by check_configuration post-#2070, found: %r"
        % s004_functions
    )
