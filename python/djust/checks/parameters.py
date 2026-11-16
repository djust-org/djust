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
    recovery = _recovery_target(cls, name)
    envelope = _envelope_names(function, recovery)
    if envelope or (recovery and _declares_recovery_parameters(function)):
        if not _is_check_suppressed("djust.V031"):
            message = _v031(cls, name, function, label, envelope, project_policy_valid)
            if message is not None:
                messages.append(message)
        if envelope:
            # The "_" names are also a V016 reserved-name error under strict;
            # V031 names the replacement, so it is reported once, here.
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


def _recovery_target(cls: type, name: str) -> bool:
    """Whether a literal dj-auto-recover in the view's template targets it.

    The template binding scan follows ``{% include %}`` and ``{% extends %}``
    (ADR-037 row 18). Recovery events always reach the page's view, so only a
    LiveView can be a target.
    """
    from djust._template_bindings import recovery_targets
    from djust.live_view import LiveView

    if not issubclass(cls, LiveView):
        return False
    targets = _RECOVERY_TARGETS.get(cls)
    if targets is None:
        targets = _RECOVERY_TARGETS[cls] = recovery_targets(cls)
    return name in targets


#: One template scan per class per check run (cleared at the start of each run).
_RECOVERY_TARGETS: "weakref.WeakKeyDictionary[type, frozenset[str]]" = weakref.WeakKeyDictionary()


def _envelope_names(function: Any, recovery_target: bool) -> list[str]:
    """The 1.x envelope names the handler declares, or reads as a recovery target.

    A declared parameter is found from the signature for every handler. The
    ``**kwargs`` form (``kwargs.get("_form_values")``) is found in the source,
    only for handlers a template binding targets: that read means nothing on
    any other handler.
    """
    origin = inspect.unwrap(function)
    try:
        declared = inspect.signature(origin).parameters
    except (TypeError, ValueError):
        declared = {}  # type: ignore[assignment]
    found = [name for name in _RECOVERY_ENVELOPE if name in declared]
    if found or not recovery_target:
        return found
    try:
        source = textwrap.dedent(inspect.getsource(origin))
    except (OSError, TypeError):
        return []  # No source (generated or C-level): the signature is all there is.
    if not any(name in source for name in _RECOVERY_ENVELOPE):
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    read = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and node.value in _RECOVERY_ENVELOPE
    }
    return [name for name in _RECOVERY_ENVELOPE if name in read]


def _declares_recovery_parameters(function: Any) -> bool:
    try:
        declared = inspect.signature(inspect.unwrap(function)).parameters
    except (TypeError, ValueError):
        return False
    return any(name in declared for name in _RECOVERY_PARAMETERS)


def _v031(
    cls: type,
    name: str,
    function: Any,
    label: str,
    envelope: list[str],
    project_policy_valid: bool,
) -> Optional[CheckMessage]:
    """``djust.V031``: a dj-auto-recover handler that cannot receive its values.

    Either it still reads the 1.x ``_form_values`` / ``_data_attrs`` envelope,
    which strict dispatch never delivers (an error under strict, a warning
    under legacy, which 2.0 removes), or it declares the 2.0 parameters but
    runs under the legacy policy, which sends only the envelope.
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
    if envelope:
        strict = policy == "strict"
        return (DjustError if strict else DjustWarning)(
            "%s: reads the dj-auto-recover values as %s, which %s."
            % (
                label,
                " / ".join(envelope),
                "strict dispatch never delivers"
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
    if policy != "legacy":
        return None
    return DjustError(
        "%s: declares form_values / data_attrs for dj-auto-recover but runs under the "
        "legacy parameter policy, which sends them only as _form_values / _data_attrs; "
        "the event is refused." % label,
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

    _RECOVERY_TARGETS.clear()
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
