"""djust 2.0 upgrade checks (#4190): V034, V035 and T029.

What a 1.3 project must change before 2.0.0 removes the legacy behaviour. V033
(strict readiness of a legacy handler) lives with the other parameter checks in
``parameters.py`` and T028 (a binding a legacy handler accepts that strict
would refuse) in ``bindings.py``.

- ``V034``: a handler declares or reads ``_target``, which a strict client never sends.
- ``V035``: an explicit-exposure view overrides ``_on_sticky_update``, which djust never calls on it.
- ``T029``: Python code emits a ``data-*`` event argument that a strict handler never receives.

Every check is a warning and none constructs, mounts or invokes anything: they
inspect classes and source text. djust's own classes are skipped.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from collections.abc import Iterator
from typing import Any, Optional

from django.core.checks import CheckMessage, register

from djust._framework_code import is_djust_module
from djust.checks.utils import (
    DjustWarning,
    _is_check_suppressed,
    _walk_subclasses,
)


def _user_liveviews() -> list[type]:
    """Application LiveView classes (routed or imported), sorted, concrete."""
    from djust.checks.components import _routed_liveview_classes
    from djust.live_view import LiveView

    found = set(_routed_liveview_classes()) | set(_walk_subclasses(LiveView))
    return sorted(
        (
            cls
            for cls in found
            if not is_djust_module(cls.__module__) and cls.__dict__.get("abstract") is not True
        ),
        key=lambda c: (c.__module__, c.__qualname__),
    )


def _user_handlers() -> Iterator[tuple[type, str, Any, Any]]:
    """Each declared handler function once, under the first user class that has it."""
    from djust._parameter_metadata import declared_handlers
    from djust.checks.parameters import _owner_classes

    seen: set[Any] = set()
    for cls, stop in _owner_classes():
        if is_djust_module(cls.__module__):
            continue
        for name, member, function, _owner in declared_handlers(cls, stop, server_functions=True):
            if function in seen:
                continue
            seen.add(function)
            yield cls, name, member, function


def _label(cls: type, name: str) -> str:
    return "%s.%s.%s()" % (cls.__module__, cls.__qualname__, name)


def _source_location(obj: Any) -> tuple[str, Optional[int]]:
    origin = inspect.unwrap(obj) if callable(obj) else obj
    try:
        return inspect.getsourcefile(origin) or "", inspect.getsourcelines(origin)[1]
    except (OSError, TypeError):
        return "", None  # Source introspection fails for generated or C-level objects


# ---------------------------------------------------------------------------
# V034: _target
# ---------------------------------------------------------------------------


def _target_use(function: Any) -> Optional[str]:
    """``"declares"`` / ``"reads"`` when the handler uses ``_target``, else None."""
    origin = inspect.unwrap(function)
    try:
        if "_target" in inspect.signature(origin).parameters:
            return "declares"
    except (TypeError, ValueError):
        return None
    try:
        source = textwrap.dedent(inspect.getsource(origin))
    except (OSError, TypeError):
        return None
    if "_target" not in source:
        return None
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    if any(isinstance(n, ast.Constant) and n.value == "_target" for n in ast.walk(tree)):
        return "reads"
    return None


def _v034(label: str, use: str, function: Any) -> CheckMessage:
    """The one constructor of ``djust.V034`` (#4190)."""
    from djust._removed_settings import MIGRATION_GUIDE_URL

    file_path, line_number = _source_location(function)
    return DjustWarning(
        "%s: %s _target, which a strict client never sends; djust 2.0.0 removes the legacy "
        "parameter policy that sent it." % (label, use),
        hint=(
            'Declare field: str = "" instead: dj-input, dj-change, dj-blur and dj-focus send '
            "the name of the control that fired as field. A dj-submit handler receives the "
            "pressed submit button as one of its form fields (its name=value). See %s"
            % MIGRATION_GUIDE_URL
        ),
        id="djust.V034",
        fix_hint="Replace _target with `field: str` in `%s`." % label,
        file_path=file_path,
        line_number=line_number,
    )


@register("djust")
def check_target_parameter(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    """V034 (Warning): a handler declares or reads ``_target`` (#4190)."""
    if _is_check_suppressed("djust.V034"):
        return []
    try:
        from djust._parameter_contract import ContractError
        from djust.validation import get_handler_parameter_policy
    except Exception:  # noqa: BLE001 — djust not importable/configured: nothing to check
        return []
    messages: list[CheckMessage] = []
    for cls, name, _member, function in _user_handlers():
        use = _target_use(function)
        if use is None:
            continue
        if use == "declares":
            try:
                if get_handler_parameter_policy(function) == "strict":
                    continue  # V016 already reports the reserved name
            except ContractError:
                pass  # djust.C022 reports the invalid project policy
        messages.append(_v034(_label(cls, name), use, function))
    return messages


# ---------------------------------------------------------------------------
# V035: _on_sticky_update on an explicit view
# ---------------------------------------------------------------------------


def _v035(owner: type) -> CheckMessage:
    """The one constructor of ``djust.V035`` (#4190)."""
    file_path, line_number = _source_location(owner)
    label = "%s.%s" % (owner.__module__, owner.__qualname__)
    return DjustWarning(
        "%s overrides _on_sticky_update, but it is an explicit-exposure view: as a sticky "
        "child it is remounted whenever a {%% live_render %%} input changes, so djust never "
        "calls the hook (it ran only for legacy children)." % label,
        hint=(
            'Name the inputs the hook takes: sticky_update_inputs = ("title",). A change to '
            "those reuses the child and calls the hook; a change to any other input remounts "
            "it. Or delete the hook and read the inputs in mount()."
        ),
        id="djust.V035",
        fix_hint="Set `sticky_update_inputs` on `%s`, or delete its `_on_sticky_update`."
        % owner.__qualname__,
        file_path=file_path,
        line_number=line_number,
    )


@register("djust")
def check_sticky_update_hooks(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    """V035 (Warning): ``_on_sticky_update`` on an explicit-exposure view (#4190)."""
    if _is_check_suppressed("djust.V035"):
        return []
    try:
        from djust._exposure import uses_legacy_exposure
        from djust.mixins.sticky import StickyChildRegistry
    except Exception:  # noqa: BLE001 — djust not importable/configured: nothing to check
        return []
    messages: list[CheckMessage] = []
    reported: set[type] = set()
    for cls in _user_liveviews():
        hook = getattr(cls, "_on_sticky_update", None)
        if hook is None or hook is StickyChildRegistry._on_sticky_update:
            continue
        if uses_legacy_exposure(cls):
            continue  # legacy children still get the hook
        if getattr(cls, "sticky_update_inputs", None):
            continue  # the view names the inputs the hook takes (#4181)
        owner = next((k for k in cls.__mro__ if "_on_sticky_update" in vars(k)), None)
        if owner is None or owner in reported or is_djust_module(owner.__module__):
            continue
        reported.add(owner)
        messages.append(_v035(owner))
    return messages


# ---------------------------------------------------------------------------
# T029: data-* arguments in Python-built markup
# ---------------------------------------------------------------------------


def _t029(file_path: str, line: int, attributes: list[str], binding_label: str) -> CheckMessage:
    """The one constructor of ``djust.T029`` (#4190)."""
    names = ", ".join(attributes)
    first = attributes[0]
    example = first[len("data-") :] if first.startswith("data-") else first
    return DjustWarning(
        "%s:%d: Python code emits %s on an element bound by %s; under the strict policy, the "
        "only one in djust 2.0.0, a handler never receives data-* arguments."
        % (file_path, line, names, binding_label),
        hint=(
            "Emit dj-value-%s instead (format_html('... dj-value-%s=\"{}\"', value)). A data-* "
            "attribute that only styles or scripts the element can stay; suppress with "
            "DJUST_CONFIG = {'suppress_checks': ['T029']}." % (example, example)
        ),
        id="djust.T029",
        fix_hint="Rename %s to dj-value-... in the markup built at %s:%d."
        % (first, file_path, line),
        file_path=file_path,
        line_number=line,
    )


def _candidate_texts(tree: ast.AST) -> Iterator[tuple[str, int]]:
    """String literals and f-strings of a module or class body, minus ``template =``."""
    skipped: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "template" for t in node.targets
        ):
            for inner in ast.walk(node.value):
                skipped.add(id(inner))
    for node in ast.walk(tree):
        if id(node) in skipped:
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value, node.lineno
        elif isinstance(node, ast.JoinedStr):
            parts = [
                p.value if isinstance(p, ast.Constant) and isinstance(p.value, str) else "X"
                for p in node.values
            ]
            yield "".join(parts), node.lineno


def _scan_markup_source(
    source: str, file_path: str, line_offset: int, cls: Optional[type]
) -> list[CheckMessage]:
    from djust._template_bindings import markup_bindings

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    messages: list[CheckMessage] = []
    seen: set[tuple[int, str]] = set()
    for text, lineno in _candidate_texts(tree):
        if "dj-" not in text or "data-" not in text:
            continue
        try:
            bindings = markup_bindings(text)
        except Exception:  # noqa: BLE001 — markup the scanner cannot parse is not reported
            continue
        for binding in bindings:
            data_keys = dict(binding.payload.data_keys)
            if not data_keys:
                continue
            if cls is not None and binding.name:
                function = inspect.getattr_static(cls, binding.name, None)
                if inspect.isfunction(function):
                    try:
                        parameters = inspect.signature(inspect.unwrap(function)).parameters
                    except (TypeError, ValueError):
                        parameters = {}  # type: ignore[assignment]
                    if not any(p.kind is p.VAR_KEYWORD for p in parameters.values()):
                        data_keys = {k: a for k, a in data_keys.items() if k in parameters}
            if not data_keys:
                continue
            attributes = sorted(data_keys.values())
            key = (lineno, ",".join(attributes))
            if key in seen:
                continue
            seen.add(key)
            messages.append(_t029(file_path, lineno + line_offset, attributes, binding.label))
    return messages


@register("djust")
def check_python_markup_arguments(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    """T029 (Warning): Python code emits ``data-*`` arguments on a bound element (#4190)."""
    if _is_check_suppressed("djust.T029"):
        return []
    try:
        from djust._template_bindings import _installed_libraries
        from djust.components.base import Component, LiveComponent
    except Exception:  # noqa: BLE001 — djust not importable/configured: nothing to check
        return []
    classes: set[type] = set(_user_liveviews())
    for base in (LiveComponent, Component):
        classes.update(_walk_subclasses(base))
    messages: list[CheckMessage] = []
    for cls in sorted(classes, key=lambda c: (c.__module__, c.__qualname__)):
        if is_djust_module(cls.__module__) or cls.__dict__.get("abstract") is True:
            continue
        try:
            lines, start = inspect.getsourcelines(cls)
            file_path = inspect.getsourcefile(cls) or ""
        except (OSError, TypeError):
            continue
        messages.extend(
            _scan_markup_source(textwrap.dedent("".join(lines)), file_path, start - 1, cls)
        )
    try:
        modules = sorted(set(_installed_libraries().values()))
    except Exception:  # noqa: BLE001 — a library that cannot be listed is skipped
        modules = []
    for module_name in modules:
        if is_djust_module(module_name) or module_name.startswith("django."):
            continue
        try:
            import importlib

            module = importlib.import_module(module_name)
            source = inspect.getsource(module)
            file_path = inspect.getsourcefile(module) or ""
        except Exception:  # noqa: BLE001 — a module without source is skipped
            continue
        messages.extend(_scan_markup_source(source, file_path, 0, None))
    return messages
