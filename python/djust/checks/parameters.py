"""djust event parameter contract checks, ``djust.V016``, ``V018`` and ``V031`` (ADR-036).

A strict-policy handler's declaration problems surface at startup instead of
on the first event. Each check calls the runtime's own resolvers
(``get_handler_parameter_policy`` and ``get_strict_handler_contract``) on the
same bound shape dispatch uses, so the check and the runtime share one cached
contract and cannot disagree. Legacy-policy handlers are never reported:
legacy is the default and its behavior is unchanged. The project-level policy
value is ``djust.C022`` in ``configuration.py``. V016 also covers ADR-034
output-subscription callbacks, whose payload is bound by the same strict
contract with the source component as trusted framework context. Handlers
are found by ``_parameter_metadata.declared_handlers``, the discovery dispatch
uses (ADR-037 D1).

No view, component or handler is constructed, mounted or invoked.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
import types
import weakref
from collections.abc import Iterator
from typing import Any, Callable, Optional

from django.core.checks import CheckMessage, register

from djust.checks.utils import (
    DjustError,
    DjustWarning,
    _is_check_suppressed,
    _is_framework_internal_class,
    _walk_subclasses,
)


def _bound(member: Any, function: types.FunctionType, cls: type) -> Callable[..., Any]:
    """The callable shape dispatch compiles (``declaration_method``), imported
    lazily: django.setup() must not load the contract modules (#2559)."""
    from djust._parameter_metadata import declaration_method

    return declaration_method(member, function, cls)


def _location(function: Any) -> tuple[str, Optional[int]]:
    origin = inspect.unwrap(function)
    try:
        return inspect.getsourcefile(origin) or "", inspect.getsourcelines(origin)[1]
    except (OSError, TypeError):
        return "", None  # Source introspection fails for generated or C-level functions


def _label(cls: type, name: str, function: Any) -> str:
    label = "%s.%s.%s()" % (cls.__module__, cls.__qualname__, name)
    origin = inspect.unwrap(function)
    declared = "%s.%s" % (origin.__module__, origin.__qualname__)
    if not declared.startswith("%s.%s." % (cls.__module__, cls.__qualname__)):
        label += " (declared as %s)" % declared
    return label


def _explicit_param_names(function: Any) -> Optional[list[str]]:
    """``@event_handler(params=[...])`` names when they replace the signature's."""
    metadata = getattr(function, "_djust_decorators", {}).get("event_handler") or {}
    names = metadata.get("param_names")
    derived = [p.get("name") for p in metadata.get("params", ())]
    return list(names) if names is not None and list(names) != derived else None


def _handler_messages(
    cls: type,
    name: str,
    member: Any,
    function: types.FunctionType,
    project_policy_valid: bool,
    first_report: bool,
    recovery: Optional["_Recovery"] = None,
) -> list[CheckMessage]:
    from djust._parameter_contract import ContractError
    from djust.validation import get_handler_parameter_policy, get_strict_handler_contract

    messages: list[CheckMessage] = []
    label = _label(cls, name, function)
    file_path, line_number = _location(function)
    decorators = getattr(function, "_djust_decorators", {})
    own_policy = decorators.get("event_handler", decorators.get("server_function", {})).get(
        "parameter_policy"
    )
    if recovery is not None:
        if recovery.report_here(cls, name) and not _is_check_suppressed("djust.V031"):
            message = _v031(recovery, function, label, project_policy_valid)
            if message is not None:
                messages.append(message)
        if recovery.envelope:
            # The "_" names are also a V016 reserved-name error under strict;
            # V031 names the replacement, so the declaration is reported once.
            return messages
    if own_policy is None and not project_policy_valid:
        return messages  # djust.C022 reports the project value once.

    try:
        policy = get_handler_parameter_policy(function)
        if policy != "strict":
            return messages
        handler = _bound(member, function, cls)
        contract = get_strict_handler_contract(handler)
    except ContractError as exc:
        if first_report and not _is_check_suppressed("djust.V016"):
            messages.append(
                _v016(
                    "%s: strict event parameter contract is invalid: %s" % (label, exc),
                    "Strict dispatch rejects every call to this handler until its "
                    "declaration is fixed. Supported inputs are str, int, float, bool, "
                    "Decimal, UUID, date, Optional[T], list[T] and dict[str, T]; use explicit Any "
                    "for unchecked input, or parameter_policy='legacy' to opt out.",
                    label,
                    function,
                )
            )
        return messages

    explicit = _explicit_param_names(function)
    declared = [
        item["name"]
        for item in contract.metadata()
        if item["kind"] not in ("var_positional", "var_keyword")
    ]
    if (
        first_report
        and explicit is not None
        and set(explicit) != set(declared)
        and not _is_check_suppressed("djust.V018")
    ):
        messages.append(
            DjustWarning(
                "%s: @event_handler(params=%r) disagrees with the strict signature's "
                "parameters %r." % (label, sorted(explicit), sorted(declared)),
                hint=(
                    "Under the strict policy the Python signature is the parameter "
                    "contract; the params= list is ignored by validation and misleads "
                    "tooling that reads it. Remove params= or make it match."
                ),
                id="djust.V018",
                fix_hint="Remove `params=` from the `@event_handler` on `%s`." % label,
                file_path=file_path,
                line_number=line_number,
            )
        )
    return messages


#: The 1.x ``dj-auto-recover`` envelope, and the parameters that replace it (#4178).
_RECOVERY_ENVELOPE = ("_form_values", "_data_attrs")
_RECOVERY_PARAMETERS = ("form_values", "data_attrs")
_RECOVERY_SIGNATURE = "form_values: dict[str, Any], data_attrs: dict[str, str]"

#: One template scan per class per check run (cleared at the start of each run).
_RECOVERY_BINDINGS: "weakref.WeakKeyDictionary[type, tuple[frozenset[str], bool]]" = (
    weakref.WeakKeyDictionary()
)


def _recovery_bindings(cls: type) -> tuple[frozenset[str], bool]:
    """A LiveView's literal dj-auto-recover targets, and whether it has a computed one.

    The template binding scan follows ``{% include %}`` and ``{% extends %}``
    (ADR-037 row 18). Recovery events always reach the page's view, so a
    LiveComponent's handler is never a target.
    """
    from djust._template_bindings import recovery_bindings
    from djust.live_view import LiveView

    if not issubclass(cls, LiveView):
        return frozenset(), False
    found = _RECOVERY_BINDINGS.get(cls)
    if found is None:
        found = _RECOVERY_BINDINGS[cls] = recovery_bindings(cls)
    return found


class _Recovery:
    """What ``djust.V031`` knows about one handler declaration (#4178).

    ``entries`` are the (class, name) pairs whose template targets it, a
    literal target first; the message is reported once, under the first.
    """

    def __init__(self, declared: list[str], read: list[str], literal: bool) -> None:
        self.declared = declared  # envelope names in the signature
        self.read = read  # envelope names read from **kwargs
        self.literal = literal  # a literal dj-auto-recover names it
        self.entries: list[tuple[type, str]] = []

    @property
    def envelope(self) -> list[str]:
        return [n for n in _RECOVERY_ENVELOPE if n in self.declared or n in self.read]

    def report_here(self, cls: type, name: str) -> bool:
        return bool(self.entries) and self.entries[0] == (cls, name)


def _recovery_for(cls: type, name: str, function: Any) -> Optional[_Recovery]:
    """The handler as a dj-auto-recover target of ``cls``, or None.

    A literal ``dj-auto-recover="name"`` makes it one. A computed
    ``dj-auto-recover="{{ handler }}"`` cannot be resolved statically, so then
    a handler counts only when it declares or reads the 1.x envelope: those
    names mean nothing outside recovery.
    """
    literals, computed = _recovery_bindings(cls)
    literal = name in literals
    if not literal and not computed:
        return None
    declared, read = _envelope_names(function)
    if not literal and not (declared or read):
        return None
    if literal and not (declared or read) and not _recovery_parameters(function):
        return None
    return _Recovery(declared, read, literal)


def _envelope_names(function: Any) -> tuple[list[str], list[str]]:
    """The 1.x envelope names the handler declares, and those it reads.

    A declared parameter comes from the signature. The ``**kwargs`` form
    (``kwargs.get("_form_values")``) is found in the source.
    """
    origin = inspect.unwrap(function)
    try:
        signature = inspect.signature(origin).parameters
    except (TypeError, ValueError):
        signature = {}  # type: ignore[assignment]
    declared = [name for name in _RECOVERY_ENVELOPE if name in signature]
    if declared:
        return declared, []
    try:
        source = textwrap.dedent(inspect.getsource(origin))
    except (OSError, TypeError):
        return [], []  # No source (generated or C-level): the signature is all there is.
    if not any(name in source for name in _RECOVERY_ENVELOPE):
        return [], []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return [], []
    read = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and node.value in _RECOVERY_ENVELOPE
    }
    return [], [name for name in _RECOVERY_ENVELOPE if name in read]


def _recovery_parameters(function: Any) -> list[inspect.Parameter]:
    """The ``form_values`` / ``data_attrs`` parameters the handler declares."""
    try:
        declared = inspect.signature(inspect.unwrap(function)).parameters
    except (TypeError, ValueError):
        return []
    return [p for name, p in declared.items() if name in _RECOVERY_PARAMETERS]


def _v031(
    recovery: _Recovery,
    function: Any,
    label: str,
    project_policy_valid: bool,
) -> Optional[CheckMessage]:
    """``djust.V031``: a dj-auto-recover handler that cannot receive its values.

    Either it still takes the 1.x ``_form_values`` / ``_data_attrs`` envelope,
    which strict dispatch never delivers (an error under strict, a warning
    under legacy, which 2.0 removes); or it declares the 2.0 parameters in a
    way the browser cannot fill: positional-only (the browser sends them by
    name), or under the legacy policy (which sends only the envelope).
    """
    from djust._parameter_contract import ContractError
    from djust.validation import get_handler_parameter_policy

    policy: Optional[str] = None
    if project_policy_valid or _own_policy(function) is not None:
        try:
            policy = get_handler_parameter_policy(function)
        except ContractError:
            policy = None  # djust.C022 reports the project value.
    file_path, line_number = _location(function)
    name = inspect.unwrap(function).__name__
    target = (
        "this dj-auto-recover handler"
        if recovery.literal
        else "this handler, a possible target of a computed dj-auto-recover,"
    )
    envelope = recovery.envelope
    if envelope:
        strict = policy == "strict"
        how = []
        if recovery.declared:
            how.append("declares %s" % " / ".join(recovery.declared))
        if recovery.read:
            how.append("reads %s from **kwargs" % " / ".join(recovery.read))
        return (DjustError if strict else DjustWarning)(
            "%s: %s %s, which %s."
            % (
                label,
                target,
                " and ".join(how),
                "strict dispatch never delivers, so the event is refused"
                if strict
                else "only the legacy parameter policy delivers, and 2.0 removes it",
            ),
            hint=(
                "Declare the values as parameters: def %s(self, %s). form_values "
                "holds each named control (a str, a bool for a lone checkbox, a "
                "list of str for a checkbox group or a multiple select) and "
                "data_attrs the container's data-* attributes. Declare only the "
                "one you use; the browser sends only declared names. The handler "
                "needs the strict policy (parameter_policy='strict', or "
                "event_parameter_policy='strict' for the project)." % (name, _RECOVERY_SIGNATURE)
            ),
            id="djust.V031",
            fix_hint="Replace %s with `%s` on `%s`."
            % (", ".join(envelope), _RECOVERY_SIGNATURE, label),
            file_path=file_path,
            line_number=line_number,
        )
    parameters = _recovery_parameters(function)
    if policy == "strict":
        positional = [p.name for p in parameters if p.kind is inspect.Parameter.POSITIONAL_ONLY]
        if not positional:
            return None
        return DjustError(
            "%s: %s declares %s positional-only, but the browser sends the recovery "
            "values by name; the event is refused." % (label, target, " / ".join(positional)),
            hint=(
                "Move the parameters after the '/' (or drop the '/') so they can be "
                "passed by name: def %s(self, %s)." % (name, _RECOVERY_SIGNATURE)
            ),
            id="djust.V031",
            fix_hint="Make %s keyword-passable on `%s`." % (", ".join(positional), label),
            file_path=file_path,
            line_number=line_number,
        )
    if policy != "legacy":
        return None
    return DjustError(
        "%s: %s declares %s but runs under the legacy parameter policy, which sends "
        "the values only as _form_values / _data_attrs; the event is refused."
        % (label, target, " / ".join(p.name for p in parameters)),
        hint=(
            "Recovery values arrive as declared parameters only under the strict "
            "policy. Add parameter_policy='strict' to the handler's @event_handler, "
            "or set LIVEVIEW_CONFIG['event_parameter_policy'] = 'strict'."
        ),
        id="djust.V031",
        fix_hint='Add `parameter_policy="strict"` to the `@event_handler` on `%s`.' % label,
        file_path=file_path,
        line_number=line_number,
    )


def _own_policy(function: Any) -> Optional[str]:
    decorators = getattr(function, "_djust_decorators", {})
    policy = decorators.get("event_handler", decorators.get("server_function", {})).get(
        "parameter_policy"
    )
    return policy if isinstance(policy, str) else None


def _owner_classes() -> Iterator[tuple[type, Callable[[type], bool], bool]]:
    """User LiveViews and LiveComponents, deterministic, each with its MRO stop."""
    from djust._parameter_metadata import component_stop, view_stop

    try:
        from djust.components.base import LiveComponent
        from djust.live_view import LiveView
    except ImportError:
        return

    from djust.checks.components import _routed_liveview_classes

    # Walk the URLconf before __subclasses__(): importing it is what makes
    # routed view modules visible (see check_liveviews, #2559).
    views = set(_routed_liveview_classes()) | set(_walk_subclasses(LiveView))
    components = set(_walk_subclasses(LiveComponent))

    def by_name(cls: type) -> tuple[str, str]:
        return getattr(cls, "__module__", ""), getattr(cls, "__qualname__", "")

    for cls in sorted(views, key=by_name):
        if not _is_framework_internal_class(cls) and cls.__dict__.get("abstract") is not True:
            yield cls, view_stop

    for cls in sorted(components, key=by_name):
        if not _is_framework_internal_class(cls) and cls.__dict__.get("abstract") is not True:
            yield cls, component_stop


@register("djust")
def check_event_parameter_contracts(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    """``djust.V016``, ``V018`` and ``V031``: strict-policy handler declarations (ADR-036)."""
    from djust._parameter_contract import ContractError
    from djust._parameter_metadata import declared_handlers
    from djust.validation import get_project_parameter_policy

    _RECOVERY_BINDINGS.clear()
    try:
        get_project_parameter_policy()
        project_policy_valid = True
    except ContractError:
        project_policy_valid = False

    entries = [
        (cls, name, member, function)
        for cls, stop in _owner_classes()
        for name, member, function, _owner in declared_handlers(cls, stop, server_functions=True)
    ]
    # A declaration problem is reported once, under the class that declares the
    # handler when that class is itself checked, else under its first user.
    primary: dict[Any, type] = {}
    for cls, name, member, function in entries:
        current = primary.get(function)
        if current is None or (
            vars(cls).get(name) is member and vars(current).get(name) is not member
        ):
            primary[function] = cls
    # V031 is about a declaration: one message per handler function, under a
    # class whose template literally targets it when there is one (#4178).
    recovery: dict[Any, _Recovery] = {}
    for cls, name, member, function in entries:
        found = _recovery_for(cls, name, function)
        if found is None:
            continue
        current = recovery.get(function)
        if current is None:
            current = recovery[function] = found
        elif found.literal and not current.literal:
            # The first literal target reports it; the computed ones follow.
            current.literal = True
            current.entries.insert(0, (cls, name))
            continue
        current.entries.append((cls, name))
    messages: list[CheckMessage] = []
    for cls, name, member, function in entries:
        messages.extend(
            _handler_messages(
                cls,
                name,
                member,
                function,
                project_policy_valid,
                primary[function] is cls,
                recovery.get(function),
            )
        )
    checked: set[Any] = set()
    for cls, _stop in _owner_classes():
        messages.extend(_subscription_messages(cls, checked))
    return messages


def _subscription_messages(cls: type, checked: set[Any]) -> list[CheckMessage]:
    """V016 for ADR-034 output callbacks, whose payload is bound strictly.

    The originating component is trusted framework context, not a payload
    parameter; ``_interactive._emit`` compiles the same cached contract.
    """
    from djust._component_subscriptions import (
        SOURCE_PARAMETER,
        compile_subscriptions,
        is_component_subscription,
    )
    from djust._parameter_metadata import DECLARATION_OWNER
    from djust._parameter_contract import ContractError
    from djust.validation import get_strict_handler_contract

    try:
        bindings = compile_subscriptions(cls)
    except TypeError:
        return []  # Class construction already rejected these declarations.
    messages: list[CheckMessage] = []
    for binding in bindings:
        function = inspect.getattr_static(cls, binding.callback, None)
        if not isinstance(function, types.FunctionType) or function in checked:
            continue
        checked.add(function)
        if not is_component_subscription(function):
            continue
        try:
            get_strict_handler_contract(
                types.MethodType(function, DECLARATION_OWNER), frozenset({SOURCE_PARAMETER})
            )
        except ContractError as exc:
            if _is_check_suppressed("djust.V016"):
                continue
            label = _label(cls, binding.callback, function)
            messages.append(
                _v016(
                    "%s: %s.%s output callback contract is invalid: %s"
                    % (label, binding.component, binding.output, exc),
                    "Output payloads are bound with the strict contract; the source "
                    "component is supplied by the framework. Annotate each payload "
                    "parameter with a supported type (str, int, float, bool, Decimal, "
                    "UUID, date, Optional[T], list[T], dict[str, T]) or explicit Any.",
                    label,
                    function,
                )
            )
    return messages


def _v016(message: str, hint: str, label: str, function: Any) -> CheckMessage:
    """The one constructor of ``djust.V016`` (one check ID, one emitter)."""
    file_path, line_number = _location(function)
    return DjustError(
        message,
        hint=hint,
        id="djust.V016",
        fix_hint="Fix the parameter declaration of `%s`." % label,
        file_path=file_path,
        line_number=line_number,
    )
