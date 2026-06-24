"""Anti-drift structural net for the mount/state-apply chokepoint (WU1, #1646).

The companion to ``test_transport_parity_security.py``. Where that file proves the
three transports produce IDENTICAL security verdicts at *runtime*, this file
proves *statically* that no transport (or future module) grows a NEW unsanctioned
site that bypasses the shared chokepoint. It mirrors the #1125 count-test pattern:
enumerate the EXPECTED sanctioned sites, scan the source, and fail if an
UNEXPECTED one appears.

Four drift classes are pinned by AST scans of the top-level ``python/djust/*.py``
modules (websocket.py, sse.py, runtime.py and their siblings):

1. **Arbitrary import of a view-path-like value.** The ONLY module allowed to call
   ``importlib.import_module(...)`` / ``__import__(...)`` on a NON-LITERAL argument
   is ``security/mount.py`` (the chokepoint). A literal-string import
   (``__import__("logging")``) is always fine; a dynamic import of a Name/expression
   is the F22 surface and must live behind the resolver. ``testing.py``'s
   INSTALLED_APPS auto-discovery import is whitelisted (server-derived module
   name, test harness only).

2. **``setattr`` of a non-literal key onto a view/component instance.** Client-
   derived keys applied to a mounted view must go through ``safe_setattr``
   (``security/attribute_guard.py``). A bare ``setattr(view, key, ...)`` with a
   non-literal key is flagged unless explicitly whitelisted. The function-view
   decorator dict-apply in ``live_view.py`` is the known, developer-controlled
   (not client-controlled) exception.

3. **``RequestFactory().get(<non-literal>)``.** A request built from a client-
   derived URL must occur in a function that also calls
   ``validate_mount_url`` / ``_validate_mount_url``. A ``factory.get(literal)``
   (e.g. ``"/"``) is always fine.

4. **Mount-orchestration security sequence (#1850 / #1853, CONVERGED; SSE folded
   in #1887).** The pre-mount security SEQUENCE — view-level auth
   (``check_view_auth``), then on auth success the tenant resolve
   (``_ensure_tenant``) + tenant ContextVar bind — is single-sourced in ONE
   helper, ``djust.auth.core.run_pre_mount_auth`` (#1853). Post-#1887 (ADR-022
   Iter 1) the legacy SSE mount (``_sse_mount_view``) was DELETED and SSE now
   mounts via ``runtime.py`` ``dispatch_mount``, so the LIVE mount modules are
   ``websocket.py`` ``handle_mount`` + ``runtime.py`` ``dispatch_mount`` (via
   ``_check_auth``); SSE inherits the sequence through the runtime. Both route
   their pre-mount auth+tenant orchestration through that single helper, so a
   future edit cannot reorder the steps or drop one on one path without both
   drifting together. This concern PINS:
     (a) each live mount module references ``run_pre_mount_auth`` (the
         shared sequence) — a count-canary;
     (b) ``run_pre_mount_auth`` itself references the leaf chokepoints it now
         single-sources (``check_view_auth`` + the tenant bind), so the sequence
         body cannot be hollowed out;
     (c) the controls NOT folded into the helper still live where they did —
         the WS post-mount object-permission (``check_object_permission``), the
         runtime object-permission (``enforce_object_permission`` — BOTH the
         dispatch_mount post-mount check, which is the converged SSE path #1887,
         AND the url-change re-check), and the reconstructed-Host binding
         (``validated_host_from_scope`` on WS + runtime). These were deliberately
         LEFT in place (object-perm is post-mount / url-change, not part of the
         pre-mount sequence; the Host binding is request construction, not auth) —
         #1853 narrowed the convergence to the genuinely-shared pre-mount
         auth+tenant sequence.
   A future re-divergence (one path re-growing its own auth/tenant copy, or the
   helper losing a step) turns this concern red. Updating these pins is the
   deliberate signal that a further convergence/divergence happened (the same
   prune-the-whitelist-on-purpose discipline concerns 1-3 use, #1125).

Empirical canary / non-tautology (#1459 / #1468): adding a dummy
``importlib.import_module(view_path)`` to websocket.py makes
``test_no_unsanctioned_dynamic_import_sites`` FAIL; removing it restores green.
Recorded in the PR body. The AST approach (not regex) means comments and string
literals never false-positive.
"""

import ast
import pathlib

import pytest

# Scan target: the top-level djust package modules. Subpackages (security/,
# components/, theming/, …) are intentionally OUT of scope — the chokepoint and
# its guard live in security/, and the transports + their request-building /
# state-apply helpers all live at the top level. Adding a transport at the top
# level is exactly what this net must cover.
_PKG_DIR = pathlib.Path(__file__).resolve().parents[1]  # python/djust/
_TOP_LEVEL_MODULES = sorted(p for p in _PKG_DIR.glob("*.py") if p.name != "__init__.py")


def _module_label(path: pathlib.Path) -> str:
    return path.name


def _parse(path: pathlib.Path) -> ast.Module:
    return ast.parse(path.read_text(), filename=str(path))


# --------------------------------------------------------------------------- #
# Concern 1 — dynamic import of a view-path-like (non-literal) value.
# --------------------------------------------------------------------------- #
# Whitelist: (filename, callee-description). Each entry is a sanctioned dynamic
# import. A NEW non-literal import_module/__import__ in any top-level module that
# is NOT one of these fails the test.
#
#   - security/mount.py is NOT scanned (it's the chokepoint, in a subpackage).
#   - testing.py: INSTALLED_APPS auto-discovery (``app_config.name + suffix`` —
#     server-derived, test harness only, never a client view path).
_DYNAMIC_IMPORT_WHITELIST = {
    # filename: set of acceptable "kind" markers seen at the call site
    "testing.py": {"importlib.import_module"},  # INSTALLED_APPS views auto-discovery
}


def _is_import_callee(node: ast.Call) -> str | None:
    """Return a marker string if ``node`` is import_module/__import__, else None."""
    func = node.func
    # importlib.import_module(...) / something.import_module(...)
    if isinstance(func, ast.Attribute) and func.attr == "import_module":
        return "importlib.import_module"
    # __import__(...)
    if isinstance(func, ast.Name) and func.id == "__import__":
        return "__import__"
    return None


def _first_arg_is_string_literal(node: ast.Call) -> bool:
    if not node.args:
        return False
    arg = node.args[0]
    return isinstance(arg, ast.Constant) and isinstance(arg.value, str)


def _find_dynamic_imports(tree: ast.Module):
    """Yield (lineno, kind) for each non-literal import_module/__import__ call."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        kind = _is_import_callee(node)
        if kind is None:
            continue
        # A literal-string import (e.g. __import__("logging")) is always safe.
        if _first_arg_is_string_literal(node):
            continue
        yield node.lineno, kind


class TestDynamicImportChokepoint:
    def test_no_unsanctioned_dynamic_import_sites(self):
        """Only whitelisted modules may dynamically import a non-literal value."""
        violations = []
        for path in _TOP_LEVEL_MODULES:
            label = _module_label(path)
            tree = _parse(path)
            for lineno, kind in _find_dynamic_imports(tree):
                allowed = _DYNAMIC_IMPORT_WHITELIST.get(label, set())
                if kind not in allowed:
                    violations.append(f"{label}:{lineno} -> {kind}(<non-literal>)")
        assert not violations, (
            "Unsanctioned dynamic import(s) of a non-literal value found outside the "
            "shared chokepoint (djust.security.mount). View-path imports MUST route "
            "through resolve_view_class (#1646 / F22). Offenders: " + "; ".join(violations)
        )

    def test_whitelisted_import_sites_still_present(self):
        """Pin that every whitelisted import site still exists (count-test, #1125).

        If a whitelisted site is removed/renamed, this fails so the whitelist is
        pruned deliberately rather than silently rotting.
        """
        for label, kinds in _DYNAMIC_IMPORT_WHITELIST.items():
            path = _PKG_DIR / label
            assert path.exists(), f"whitelisted module {label} no longer exists"
            tree = _parse(path)
            seen = {kind for _ln, kind in _find_dynamic_imports(tree)}
            for kind in kinds:
                assert kind in seen, (
                    f"whitelisted dynamic-import site {label} -> {kind} is gone; "
                    f"prune the whitelist intentionally."
                )


# --------------------------------------------------------------------------- #
# Concern 2 — setattr of a non-literal key onto a view/component instance.
# --------------------------------------------------------------------------- #
# Names that denote a HELD view/component reference an external holder (a
# transport, time-travel restore, the function-view decorator) writes onto. The
# drift class this net guards is "a transport writes a client-frame key onto the
# mounted view it holds" — i.e. ``setattr(self.view_instance, key, ...)`` (attr
# "view_instance"), ``setattr(view, key, ...)`` or ``setattr(component, key, ...)``.
#
# ``self`` is deliberately EXCLUDED: ``setattr(self, ...)`` inside a LiveView /
# mixin method is the view writing its OWN framework state (state restore,
# default-assign init, formset field update) with framework-derived attribute
# names — not a transport applying a client-controlled key. Those are not the
# chokepoint surface and including them would flood the scan with framework
# internals (decorators.py, formsets.py, live_view default-assigns).
_VIEW_TARGET_NAMES = {"view", "view_instance", "component"}

# Whitelist of (filename, lineno) for bare-``setattr`` sites that write a
# non-literal key onto a view-like target but are NOT a client-controlled-key
# application. Each MUST be justified by a comment here.
_SETATTR_WHITELIST = {
    # live_view.py function-view decorator: applies the DEVELOPER's own returned
    # state dict onto a locally-constructed DynamicLiveView. The keys come from the
    # app author's function return value, not from a client frame — so they don't
    # need safe_setattr's client-key guard. Two adjacent lines (callable vs not).
    # Line numbers shifted +11 in ADR-022 Iter 3 Phase 3.1 (#1913) when
    # ``_mounted_from_restore`` was added before the ``_framework_attrs`` snapshot
    # in ``LiveView.__init__``.
    ("live_view.py", 1195),
    ("live_view.py", 1197),
}


def _setattr_target_name(node: ast.Call) -> str | None:
    """Return the base name of a setattr() target if it looks like a view.

    Matches ``setattr(self.view_instance, ...)`` -> "view_instance",
    ``setattr(view, ...)`` -> "view", ``setattr(self, ...)`` -> "self".
    """
    if not (isinstance(node.func, ast.Name) and node.func.id == "setattr"):
        return None
    if not node.args:
        return None
    target = node.args[0]
    if isinstance(target, ast.Attribute):
        # e.g. self.view_instance -> attr "view_instance"
        return target.attr
    if isinstance(target, ast.Name):
        return target.id
    return None


def _setattr_key_is_literal(node: ast.Call) -> bool:
    if len(node.args) < 2:
        return False
    key = node.args[1]
    return isinstance(key, ast.Constant) and isinstance(key.value, str)


def _find_unsafe_setattrs(tree: ast.Module):
    """Yield (lineno,) for setattr(view-like, <non-literal key>, ...) calls."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = _setattr_target_name(node)
        if target is None or target not in _VIEW_TARGET_NAMES:
            continue
        # A literal key (setattr(self, "count", v)) is the developer's own attribute
        # write — never a client-controlled key.
        if _setattr_key_is_literal(node):
            continue
        yield node.lineno


class TestSetattrChokepoint:
    def test_no_unsanctioned_view_setattr_sites(self):
        """A non-literal key onto a view/component must use safe_setattr."""
        violations = []
        for path in _TOP_LEVEL_MODULES:
            label = _module_label(path)
            tree = _parse(path)
            for lineno in _find_unsafe_setattrs(tree):
                if (label, lineno) not in _SETATTR_WHITELIST:
                    violations.append(f"{label}:{lineno}")
        assert not violations, (
            "Bare setattr(view-like, <non-literal key>, ...) found outside "
            "safe_setattr (djust.security.attribute_guard). Client-derived keys "
            "applied to a mounted view MUST go through safe_setattr (#1646). "
            "Offenders: " + "; ".join(violations)
        )

    def test_whitelisted_setattr_sites_still_present(self):
        """Pin the whitelisted setattr sites (count-test, #1125)."""
        # Group expected sites by file, then assert each line still contains a
        # flagged setattr (so a refactor that moves them updates the whitelist).
        by_file: dict[str, set[int]] = {}
        for label, lineno in _SETATTR_WHITELIST:
            by_file.setdefault(label, set()).add(lineno)
        for label, expected_lines in by_file.items():
            path = _PKG_DIR / label
            assert path.exists(), f"whitelisted module {label} no longer exists"
            tree = _parse(path)
            found = set(_find_unsafe_setattrs(tree))
            missing = expected_lines - found
            assert not missing, (
                f"whitelisted setattr site(s) {label}:{sorted(missing)} no longer "
                f"present at the expected line(s); the function-view decorator was "
                f"moved/refactored — update _SETATTR_WHITELIST deliberately."
            )


# --------------------------------------------------------------------------- #
# Concern 3 — RequestFactory().get(<non-literal>) must follow validate_mount_url.
# --------------------------------------------------------------------------- #
_VALIDATORS = {"validate_mount_url", "_validate_mount_url"}


def _is_factory_get(node: ast.Call) -> bool:
    """``<x>.get(...)`` where x is a RequestFactory instance.

    Heuristic on the receiver name: ``factory``, ``request_factory``, or a
    direct ``RequestFactory().get(...)``.
    """
    func = node.func
    if not (isinstance(func, ast.Attribute) and func.attr == "get"):
        return False
    recv = func.value
    if isinstance(recv, ast.Name) and recv.id in {"factory", "request_factory"}:
        return True
    # RequestFactory().get(...) — receiver is a Call to RequestFactory.
    if isinstance(recv, ast.Call):
        rfn = recv.func
        if isinstance(rfn, ast.Name) and rfn.id == "RequestFactory":
            return True
        if isinstance(rfn, ast.Attribute) and rfn.attr == "RequestFactory":
            return True
    return False


def _get_arg_is_literal(node: ast.Call) -> bool:
    if not node.args:
        return True  # .get() with no positional arg — treat as non-client
    arg = node.args[0]
    return isinstance(arg, ast.Constant) and isinstance(arg.value, str)


def _enclosing_funcs_call_validator(tree: ast.Module) -> dict[int, bool]:
    """Map each FunctionDef's lineno-range to whether it calls a mount-URL validator.

    Returns a flat helper: for any node we can ask "is line L inside a function
    that calls validate_mount_url?".
    """
    func_validates: list[tuple[int, int, bool]] = []  # (start, end, calls_validator)
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        calls_validator = False
        for sub in ast.walk(fn):
            if isinstance(sub, ast.Call):
                f = sub.func
                if isinstance(f, ast.Name) and f.id in _VALIDATORS:
                    calls_validator = True
                    break
                if isinstance(f, ast.Attribute) and f.attr in _VALIDATORS:
                    calls_validator = True
                    break
        start = fn.lineno
        end = max(
            (n.lineno for n in ast.walk(fn) if hasattr(n, "lineno")),
            default=fn.lineno,
        )
        func_validates.append((start, end, calls_validator))
    return func_validates


def _line_in_validating_func(line: int, func_validates) -> bool:
    """True if ``line`` is inside SOME function that calls a validator."""
    for start, end, validates in func_validates:
        if start <= line <= end and validates:
            return True
    return False


def _find_unvalidated_factory_gets(tree: ast.Module):
    """Yield (lineno,) for factory.get(<non-literal>) NOT in a validating function."""
    func_validates = _enclosing_funcs_call_validator(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not _is_factory_get(node):
            continue
        if _get_arg_is_literal(node):
            continue
        if not _line_in_validating_func(node.lineno, func_validates):
            yield node.lineno


class TestFactoryGetChokepoint:
    def test_factory_get_on_client_url_always_validated(self):
        """Every RequestFactory.get(<non-literal>) is in a validate_mount_url fn."""
        violations = []
        for path in _TOP_LEVEL_MODULES:
            label = _module_label(path)
            tree = _parse(path)
            for lineno in _find_unvalidated_factory_gets(tree):
                violations.append(f"{label}:{lineno}")
        assert not violations, (
            "RequestFactory().get(<non-literal>) found in a function that does NOT "
            "call validate_mount_url / _validate_mount_url. A client-derived URL "
            "fed to RequestFactory MUST be neutralised first (#1646 / F23 / #1819). "
            "Offenders: " + "; ".join(violations)
        )

    def test_known_factory_get_sites_are_validated(self):
        """Pin the known client-URL request-build sites as validated (count-test).

        These are the WS handle_mount (2 sites) + runtime _build_request sites that
        rebuild a request from the client URL. They must remain inside a
        validate_mount_url-calling function.
        """
        # (filename, the function those .get(non-literal) calls live in)
        expected = {
            "websocket.py": 2,  # handle_mount + handle_live_redirect_mount
            "runtime.py": 1,  # _build_request
        }
        for label, count in expected.items():
            path = _PKG_DIR / label
            tree = _parse(path)
            func_validates = _enclosing_funcs_call_validator(tree)
            n_validated_nonliteral = 0
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and _is_factory_get(node)
                    and not _get_arg_is_literal(node)
                    and _line_in_validating_func(node.lineno, func_validates)
                ):
                    n_validated_nonliteral += 1
            assert n_validated_nonliteral >= count, (
                f"{label}: expected >= {count} validated factory.get(<non-literal>) "
                f"site(s) but found {n_validated_nonliteral}. A client-URL request "
                f"build was removed or stopped routing through validate_mount_url."
            )


# --------------------------------------------------------------------------- #
# Sanity: the scan actually inspects the transports (not a vacuous empty set).
# --------------------------------------------------------------------------- #
def test_scan_covers_the_transport_modules():
    names = {p.name for p in _TOP_LEVEL_MODULES}
    for required in ("websocket.py", "sse.py", "runtime.py"):
        assert required in names, f"structural scan must include {required}"


@pytest.mark.parametrize("module", ["websocket.py", "sse.py", "runtime.py"])
def test_transport_modules_parse(module):
    """Each scanned transport module is syntactically parseable (guards the scan)."""
    _parse(_PKG_DIR / module)


# --------------------------------------------------------------------------- #
# Concern 4 — mount-orchestration security sequence single-sourced at the
# shared chokepoint (#1850; CONVERGED under #1853).
#
# The pre-mount auth+tenant SEQUENCE is now single-sourced in
# djust.auth.core.run_pre_mount_auth. This concern pins that all three live
# mount paths (websocket.py / runtime.py / sse.py) route through that ONE helper
# for the pre-mount sequence, that the helper itself still contains the sequence
# body, and that the controls deliberately LEFT OUT of the helper (post-mount /
# url-change object-permission, reconstructed-Host binding) still live where they
# did. A future edit re-growing a per-path auth/tenant copy, or hollowing out the
# helper, or dropping one of the un-folded controls, turns this concern red.
#
# We count NON-IMPORT name references (a call like ``run_pre_mount_auth(view, req)``
# OR a reference like ``sync_to_async(run_pre_mount_auth)`` — the live transports
# use the latter shape) of each symbol, per module. Import-statement aliases
# (``from .auth import run_pre_mount_auth``) are excluded so adding/removing the
# lazy import does not move the count.
# --------------------------------------------------------------------------- #

# auth/core.py is a SUBPACKAGE module, intentionally outside _TOP_LEVEL_MODULES;
# the helper-body pin (concern 4b) parses it explicitly below.
_AUTH_CORE = _PKG_DIR / "auth" / "core.py"

# api/dispatch.py is also a SUBPACKAGE module (outside _TOP_LEVEL_MODULES). The
# HTTP-API + @server_function mount paths each enforce object-permission via
# enforce_object_permission (#1857); concern 4c parses it explicitly below so a
# future removal re-opens finding #10/#11/#12 on the API transport.
_API_DISPATCH = _PKG_DIR / "api" / "dispatch.py"

# Each pinned mount-orchestration symbol → the modules that MUST still reference
# it, with the minimum non-import reference count expected on the current tree.
# Post-#1853 (converged):
#   * run_pre_mount_auth        — the SHARED pre-mount auth+tenant sequence;
#                                 every transport routes through it (WS + runtime
#                                 via _check_auth + legacy SSE).
#   * object-permission          — NOT part of the pre-mount sequence, left in
#                                 place: WS uses check_object_permission (the
#                                 post-mount inner step, websocket.py); runtime
#                                 uses enforce_object_permission (the ADR-017
#                                 url-change re-check, runtime.py). Counted as a
#                                 FAMILY (either name satisfies a module's req).
#   * validated_host_from_scope — reconstructed-Host binding (request build, not
#                                 auth), left in place on WS + runtime.
_MOUNT_ORCHESTRATION_PINS = {
    # 4a — the shared pre-mount auth+tenant sequence. Post-#1887 (ADR-022 Iter 1)
    # the legacy SSE mount (``_sse_mount_view``) was deleted: SSE now mounts via
    # ``runtime.py`` ``dispatch_mount`` (the SAME runtime spine), so sse.py no
    # longer carries its own ``run_pre_mount_auth`` reference — it inherits the
    # sequence through the runtime. Two live mount modules remain (WS handle_mount
    # + runtime dispatch_mount), each pinned here.
    "run_pre_mount_auth": {"websocket.py": 1, "runtime.py": 1},
    # Object-permission family: any of these names counts toward the module's req.
    # sse.py was DROPPED from this pin post-#1887: the SSE mount's post-mount
    # object-perm check now runs inside ``runtime.py`` ``dispatch_mount`` (the
    # converged path), not in sse.py. api/dispatch.py is a subpackage module
    # pinned explicitly in concern 4c below (not via this dict, which path-joins
    # labels under _PKG_DIR top-level).
    ("check_object_permission", "enforce_object_permission"): {
        "websocket.py": 1,
        "runtime.py": 1,
    },
    "validated_host_from_scope": {"websocket.py": 1, "runtime.py": 1},
}

# 4b — the leaf chokepoints run_pre_mount_auth itself MUST still invoke, so the
# shared sequence body cannot be silently hollowed out. ``_bind_current_tenant``
# is the helper-local tenant-bind step; ``check_view_auth`` is the view-level
# auth leaf.
_HELPER_BODY_PINS = ("check_view_auth", "_bind_current_tenant")


def _import_aliased_lines(tree: ast.Module) -> set:
    """Line numbers that are import statements (to exclude their alias Names)."""
    return {
        node.lineno for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
    }


def _count_symbol_refs(tree: ast.Module, names: "tuple | set", import_lines: set) -> int:
    """Count non-import Load references of any name in ``names``.

    Catches both the direct-call shape ``name(...)`` and the wrapped shape
    ``sync_to_async(name)(...)`` (the live transports use the wrapped form), since
    both surface ``name`` as an ``ast.Name`` in a Load context outside an import.
    """
    wanted = set(names)
    count = 0
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Name)
            and node.id in wanted
            and isinstance(node.ctx, ast.Load)
            and node.lineno not in import_lines
        ):
            count += 1
    return count


class TestMountOrchestrationChokepoint:
    def test_shared_security_calls_present_at_mount_chokepoints(self):
        """Each pinned mount-orchestration symbol is still referenced by the
        modules that must carry it post-#1853 (count-canary, #1125 / #1850 / #1853).

        Concern 4a + 4c: all three transports reference the shared
        ``run_pre_mount_auth`` sequence; the object-permission family and
        ``validated_host_from_scope`` (deliberately NOT folded into the helper)
        still live on WS / runtime.

        Gate-off (#1468): deleting (or no longer routing through)
        ``run_pre_mount_auth`` from websocket.py OR runtime.py OR sse.py drops
        that module's count below the pin → FAILS. Recorded gate-off in the PR
        body: removing the ``run_pre_mount_auth`` reference in handle_mount makes
        this test report "websocket.py references run_pre_mount_auth 0 time(s)
        but expected >= 1".
        """
        shortfalls = []
        for symbol, module_reqs in _MOUNT_ORCHESTRATION_PINS.items():
            names = symbol if isinstance(symbol, tuple) else (symbol,)
            human = " / ".join(names)
            for label, expected in module_reqs.items():
                path = _PKG_DIR / label
                tree = _parse(path)
                import_lines = _import_aliased_lines(tree)
                found = _count_symbol_refs(tree, names, import_lines)
                if found < expected:
                    shortfalls.append(
                        f"{label} references {human} {found} time(s) but expected >= {expected}"
                    )
        assert not shortfalls, (
            "Mount-orchestration security symbol(s) dropped from a shared mount "
            "chokepoint. Post-#1853, the WS (handle_mount), runtime "
            "(dispatch_mount via _check_auth), and legacy SSE (_sse_mount_view) "
            "paths each MUST route the pre-mount auth+tenant sequence through the "
            "shared run_pre_mount_auth; the object-permission family and "
            "validated_host_from_scope must remain where they were left. If this "
            "fired because of a further refactor, update _MOUNT_ORCHESTRATION_PINS "
            "deliberately. Shortfalls: " + "; ".join(shortfalls)
        )

    def test_all_live_mount_transports_route_pre_mount_sequence_through_shared_helper(self):
        """#1853 convergence pin: every LIVE mount module invokes the single
        ``run_pre_mount_auth`` helper for the pre-mount auth+tenant sequence — no
        transport carries its own hand-copied orchestration.

        Post-#1887 (ADR-022 Iter 1) the legacy SSE mount (``_sse_mount_view``) was
        deleted; SSE now mounts through ``runtime.py`` ``dispatch_mount``, so the
        live mount modules are WS (``handle_mount``) + runtime (``dispatch_mount``
        via ``_check_auth``). sse.py is no longer a mount module and is correctly
        NOT in this list — it inherits the shared sequence through the runtime.

        This is the strengthened #1850 pin: previously each transport carried its
        OWN ``check_view_auth`` + ``_ensure_tenant`` + tenant-bind copy; #1853
        single-sourced the SEQUENCE so a future divergence (one path re-growing a
        private copy of the auth/tenant order) is caught here.

        Gate-off (#1468): replace ``run_pre_mount_auth`` with an inline
        ``check_view_auth`` copy on ANY one transport and that transport's count
        drops to 0 → FAILS.
        """
        missing = []
        for label in ("websocket.py", "runtime.py"):
            tree = _parse(_PKG_DIR / label)
            import_lines = _import_aliased_lines(tree)
            found = _count_symbol_refs(tree, ("run_pre_mount_auth",), import_lines)
            if found < 1:
                missing.append(f"{label} references run_pre_mount_auth {found} time(s)")
        assert not missing, (
            "A mount transport no longer routes its pre-mount auth+tenant sequence "
            "through the shared djust.auth.core.run_pre_mount_auth helper — it has "
            "re-grown a private copy of the auth/tenant orchestration "
            "(parallel-path drift, #1646 / #1853). Offenders: " + "; ".join(missing)
        )

    def test_shared_helper_body_still_invokes_the_leaf_chokepoints(self):
        """Concern 4b: ``run_pre_mount_auth`` (in auth/core.py) MUST itself invoke
        the leaf chokepoints it single-sources — ``check_view_auth`` (view-level
        auth) and ``_bind_current_tenant`` (the tenant ContextVar bind) — so the
        single source of the sequence cannot be silently hollowed out.

        Gate-off (#1468): deleting the ``check_view_auth(...)`` call from inside
        ``run_pre_mount_auth`` (so the helper authorises nothing yet every
        transport happily routes through it) drops the auth/core.py count for
        ``check_view_auth`` to 1 (only the lightweight wrapper) and would fail the
        per-helper-scoped assertion below.
        """
        tree = _parse(_AUTH_CORE)
        import_lines = _import_aliased_lines(tree)

        # Locate the run_pre_mount_auth function node and scan ONLY its body, so a
        # reference elsewhere in auth/core.py (e.g. check_view_auth's own def)
        # cannot mask a hollowed-out helper.
        helper = None
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                node.name == "run_pre_mount_auth"
            ):
                helper = node
                break
        assert helper is not None, (
            "run_pre_mount_auth not found in auth/core.py — the shared pre-mount "
            "sequence helper (#1853) was removed or renamed."
        )

        body_names = {
            n.id
            for n in ast.walk(helper)
            if isinstance(n, ast.Name)
            and isinstance(n.ctx, ast.Load)
            and n.lineno not in import_lines
        }
        for leaf in _HELPER_BODY_PINS:
            assert leaf in body_names, (
                f"run_pre_mount_auth no longer invokes {leaf!r} in its body — the "
                f"single-sourced pre-mount sequence has been hollowed out (#1853). "
                f"Every transport still routes through this helper, so dropping a "
                f"leaf here silently weakens ALL of them at once."
            )

    def test_object_permission_controls_left_in_place_post_1853(self):
        """Concern 4c: the object-permission controls were deliberately NOT folded
        into run_pre_mount_auth (they are post-mount / url-change, not part of the
        pre-mount sequence). Pin that WS still uses check_object_permission (the
        post-mount inner step) and runtime still uses enforce_object_permission
        (the ADR-017 url-change re-check).

        Extended by #1857, refined by #1887: the HTTP-API dispatch paths
        (api/dispatch.py — both dispatch_api and dispatch_server_function) enforce
        the object-perm check via enforce_object_permission; pin those so a future
        removal re-opens finding #10/#11/#12 on the API transport. The SSE mount's
        post-mount object-perm check was originally added to sse.py
        (``_sse_mount_view``, #1857), but #1887 (ADR-022 Iter 1) deleted that
        legacy mount and converged SSE onto ``runtime.py`` ``dispatch_mount`` — so
        the SSE object-perm check now lives in the runtime (already pinned via
        ``rt_*`` below, which includes dispatch_mount's post-mount check). sse.py
        is therefore NOT pinned here anymore.

        A regression that drops ANY of these re-opens finding #10/#11/#12.

        Gate-off (#1468): removing the enforce_object_permission call from
        runtime.py drops its count → the runtime assertion below FAILS; removing
        BOTH api/dispatch.py calls drops its count to 0 → the API assertion
        FAILS (api/dispatch.py imports it lazily inside each function, so the
        import-line exclusion leaves only the genuine call references).
        """
        ws_tree = _parse(_PKG_DIR / "websocket.py")
        rt_tree = _parse(_PKG_DIR / "runtime.py")
        api_tree = _parse(_API_DISPATCH)
        ws_imports = _import_aliased_lines(ws_tree)
        rt_imports = _import_aliased_lines(rt_tree)
        api_imports = _import_aliased_lines(api_tree)

        ws_inner = _count_symbol_refs(ws_tree, ("check_object_permission",), ws_imports)
        # runtime.py now carries BOTH the dispatch_mount post-mount object-perm
        # check (the converged SSE path, #1887) AND the url-change re-check → >= 2.
        rt_wrapper = _count_symbol_refs(rt_tree, ("enforce_object_permission",), rt_imports)
        # dispatch_api + dispatch_server_function each call it → expect >= 2.
        api_wrapper = _count_symbol_refs(api_tree, ("enforce_object_permission",), api_imports)

        assert ws_inner >= 1, (
            "websocket.py mount path no longer references check_object_permission "
            "(ADR-017 post-mount object-perm inner step) — finding #10/#11/#12 "
            "regression."
        )
        assert rt_wrapper >= 2, (
            "runtime.py must reference enforce_object_permission on BOTH the "
            "dispatch_mount post-mount check (the converged SSE path, #1887) and the "
            "dispatch_url_change re-check (ADR-017). Dropping either re-opens finding "
            "#10/#11/#12 on the runtime/SSE transport. Found %d." % rt_wrapper
        )
        assert api_wrapper >= 2, (
            "api/dispatch.py no longer references enforce_object_permission on BOTH "
            "the dispatch_api and dispatch_server_function mount paths (#1857) — "
            "finding #10/#11/#12 regression on the HTTP-API transport. Expected >= 2 "
            "call sites, found %d." % api_wrapper
        )
