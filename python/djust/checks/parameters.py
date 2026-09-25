"""djust event parameter contract checks, ``djust.V016``-``V018`` (ADR-036).

A strict-policy handler's declaration problems surface at startup instead of
on the first event. Each check calls the runtime's own resolvers
(``get_handler_parameter_policy`` and ``get_strict_handler_contract``) on the
same bound shape dispatch uses, so the check and the runtime share one cached
contract and cannot disagree. Legacy-policy handlers are never reported:
legacy is the default and its behavior is unchanged. The project-level policy
value is ``djust.C021`` in ``configuration.py``. V016 also covers ADR-034
output-subscription callbacks, whose payload is bound by the same strict
contract with the source component as trusted framework context.

No view, component or handler is constructed, mounted or invoked.
"""

from __future__ import annotations

import inspect
import types
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

# Stands in for a view instance when binding a method: compilation reads only
# the declaration, and a bound method shares the runtime's cache entry.
_DECLARATION_OWNER = object()


def _declared_handlers(
    cls: type, stop: Callable[[type], bool]
) -> Iterator[tuple[str, Any, types.FunctionType]]:
    """Public event handlers and server functions ``cls`` exposes, nearest first.

    Mirrors ``_parameter_metadata._event_methods`` over class declarations only:
    it resolves the same MRO shadowing without an instance or a descriptor call.
    """
    from djust.decorators import is_event_handler, is_server_function

    seen: set[str] = set()
    for klass in cls.__mro__:
        if stop(klass):
            break
        for name, member in vars(klass).items():
            if name in seen:
                continue
            seen.add(name)
            if name.startswith("_"):
                continue
            function = (
                member.__func__ if isinstance(member, (staticmethod, classmethod)) else member
            )
            if isinstance(function, types.FunctionType) and (
                is_event_handler(function) or is_server_function(function)
            ):
                yield name, member, function


def _bound(member: Any, function: types.FunctionType, cls: type) -> Callable[..., Any]:
    """The callable shape dispatch compiles: bound unless a staticmethod."""
    if isinstance(member, staticmethod):
        return function
    if isinstance(member, classmethod):
        return types.MethodType(function, cls)
    return types.MethodType(function, _DECLARATION_OWNER)


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
    actor_owner: bool,
    first_report: bool,
) -> list[CheckMessage]:
    from djust._parameter_contract import ContractError
    from djust.decorators import is_event_handler
    from djust.validation import get_handler_parameter_policy, get_strict_handler_contract

    messages: list[CheckMessage] = []
    label = _label(cls, name, function)
    file_path, line_number = _location(function)
    decorators = getattr(function, "_djust_decorators", {})
    own_policy = decorators.get("event_handler", decorators.get("server_function", {})).get(
        "parameter_policy"
    )
    if own_policy is None and not project_policy_valid:
        return messages  # djust.C021 reports the project value once.

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
                    "Decimal, UUID, date, Optional[T] and list[T]; use explicit Any "
                    "for unchecked input, or parameter_policy='legacy' to opt out.",
                    label,
                    function,
                )
            )
        return messages

    if (
        actor_owner
        and is_event_handler(function)  # server functions never reach actors
        and inspect.iscoroutinefunction(handler)
        and not _is_check_suppressed("djust.V017")
    ):
        messages.append(
            DjustError(
                "%s: async strict event handler on an actor view (use_actors=True); "
                "actor dispatch rejects it before the handler runs." % label,
                hint=(
                    "Strict async handlers need a non-actor transport. Make the handler "
                    "synchronous, set use_actors = False, or use parameter_policy='legacy'."
                ),
                id="djust.V017",
                fix_hint="Make `%s` synchronous or disable actors on the view." % label,
                file_path=file_path,
                line_number=line_number,
            )
        )

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


def _owner_classes() -> Iterator[tuple[type, Callable[[type], bool], bool]]:
    """User LiveViews and LiveComponents, deterministic, each with its MRO stop."""
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
            yield cls, lambda klass: klass is object, getattr(cls, "use_actors", False) is True

    def component_stop(klass: type) -> bool:
        return klass is LiveComponent or bool(klass.__dict__.get("_djust_framework_component_base"))

    for cls in sorted(components, key=by_name):
        if not _is_framework_internal_class(cls) and cls.__dict__.get("abstract") is not True:
            yield cls, component_stop, False


@register("djust")
def check_event_parameter_contracts(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    """``djust.V016``-``V018``: strict-policy handler declarations (ADR-036)."""
    from djust._parameter_contract import ContractError
    from djust.validation import get_project_parameter_policy

    try:
        get_project_parameter_policy()
        project_policy_valid = True
    except ContractError:
        project_policy_valid = False

    entries = [
        (cls, name, member, function, actor_owner)
        for cls, stop, actor_owner in _owner_classes()
        for name, member, function in _declared_handlers(cls, stop)
    ]
    # A declaration problem is reported once, under the class that declares the
    # handler when that class is itself checked, else under its first user.
    primary: dict[Any, type] = {}
    for cls, name, member, function, _actor in entries:
        current = primary.get(function)
        if current is None or (
            vars(cls).get(name) is member and vars(current).get(name) is not member
        ):
            primary[function] = cls
    messages: list[CheckMessage] = []
    for cls, name, member, function, actor_owner in entries:
        messages.extend(
            _handler_messages(
                cls,
                name,
                member,
                function,
                project_policy_valid,
                actor_owner,
                primary[function] is cls,
            )
        )
    checked: set[Any] = set()
    for cls, _stop, _actor in _owner_classes():
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
                types.MethodType(function, _DECLARATION_OWNER), frozenset({SOURCE_PARAMETER})
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
                    "UUID, date, Optional[T], list[T]) or explicit Any.",
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
