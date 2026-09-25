"""djust template event binding checks, ``djust.T019``-``T022`` (ADR-037).

Each LiveView and LiveComponent template is scanned by
``djust._template_bindings`` (compiled, never rendered) and every literal
``dj-*`` event binding is checked against its owner:

- ``T019``: the name resolves to no browser-callable handler on the owner.
- ``T020``: a known argument is missing, unexpected or supplied twice.
- ``T021``: a literal value or wire-type hint the handler cannot accept.
- ``T022``: markup supplies routing context (``view_id`` / ``component_id``).

Handlers are resolved through ``_parameter_metadata.declared_handlers`` and
arguments through the runtime's own policy, coercion and strict contract, so
the check and dispatch cannot disagree. No view or component is constructed or
mounted, no handler runs and no queryset is evaluated. What cannot be decided
statically is reported as dynamic or unsupported in the coverage report
(``djust_check --format json``), never guessed.

A finding is suppressed only by a local comment with a reason, on the binding's
line or the line above: ``{# noqa: T019 -- reason #}``.
"""

from __future__ import annotations

import inspect
import linecache
import re
import types
from dataclasses import dataclass, field
from typing import Any, Optional

from django.core.checks import CheckMessage, register

from djust.checks.utils import DjustWarning, _is_check_suppressed

BINDING_IDS = ("T019", "T020", "T021", "T022")
_ABSENT = object()
_NOQA = re.compile(r"\{#\s*noqa\b(?P<body>[^#]*)#\}")


class BindingWarning(DjustWarning):
    """A binding finding with the machine-readable facts ADR-037 D4 names."""

    def __init__(
        self,
        *args: Any,
        owner: str = "",
        binding: str = "",
        expected: Optional[list[str]] = None,
        supplied: Optional[list[str]] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.owner = owner
        self.binding = binding
        self.expected = expected
        self.supplied = supplied


@dataclass
class Finding:
    check_id: str
    message: str
    hint: str
    expected: Optional[list[str]] = None
    supplied: Optional[list[str]] = None


@dataclass
class BindingResult:
    binding: Any
    status: str  # "checked", "dynamic" or "unsupported"
    reason: str = ""
    findings: list[Finding] = field(default_factory=list)


@dataclass
class OwnerReport:
    owner: type
    template: str
    file: str
    results: list[BindingResult] = field(default_factory=list)
    gaps: list[Any] = field(default_factory=list)
    error: str = ""
    #: Every template file the scan read: the template, its includes and parents.
    files: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return "%s.%s" % (self.owner.__module__, self.owner.__qualname__)


# ---------------------------------------------------------------------------
# Owners and their templates
# ---------------------------------------------------------------------------


def _django_engine() -> Any:
    from django.template import engines

    for backend in engines.all():
        engine = getattr(backend, "engine", None)
        if engine is not None and hasattr(engine, "get_template"):
            return engine
    return None


def _owners() -> list[tuple[type, str]]:
    """User LiveViews and LiveComponents, deterministic, with their kind."""
    from djust.checks.components import _routed_liveview_classes
    from djust.checks.utils import _is_framework_internal_class, _walk_subclasses
    from djust.components.base import LiveComponent
    from djust.live_view import LiveView

    # Walk the URLconf first: importing it registers routed views (#2559).
    views = set(_routed_liveview_classes()) | set(_walk_subclasses(LiveView))
    components = set(_walk_subclasses(LiveComponent))
    found = [(cls, "view") for cls in views] + [(cls, "component") for cls in components]
    return sorted(
        (
            (cls, kind)
            for cls, kind in found
            if not _is_framework_internal_class(cls) and cls.__dict__.get("abstract") is not True
        ),
        key=lambda item: (item[0].__module__, item[0].__qualname__),
    )


def _overrides(cls: type, name: str, framework: tuple[type, ...]) -> bool:
    for klass in cls.__mro__:
        if klass in framework:
            return False
        if name in vars(klass):
            return True
    return False


def _uncompiled(engine: Any, name: str, exc: Exception) -> Any:
    """Tokens of a template Django finds but cannot compile, else the failure."""
    from django.template import TemplateSyntaxError

    from djust._template_bindings import scan_tokens

    if not isinstance(exc, TemplateSyntaxError):
        return type(exc).__name__
    for loader in engine.template_loaders:
        for origin in loader.get_template_sources(name):
            try:
                source = loader.get_contents(origin)
            except Exception:  # noqa: BLE001, S112 -- try the next source, as Django's loader does
                continue
            return scan_tokens(source, name, origin.name, type(exc).__name__)
    return type(exc).__name__


def _inline_location(cls: type) -> tuple[str, int]:
    """The file of ``cls`` and the line its ``template`` string starts on."""
    try:
        file = inspect.getsourcefile(cls) or ""
        lines, start = inspect.getsourcelines(cls)
    except (OSError, TypeError):
        return "<%s.%s.template>" % (cls.__module__, cls.__qualname__), 1
    for index, text in enumerate(lines):
        if re.match(r"\s*template\s*(:[^=]*)?=", text):
            return file, start + index
    return file, start


def scan_owner(cls: type, kind: str, engine: Any, cache: dict[Any, Any]) -> OwnerReport:
    """Scan the template ``cls`` renders, or report why it cannot be."""
    from djust._template_bindings import scan_source, scan_tokens
    from djust.components.base import LiveComponent
    from djust.live_view import LiveView

    framework = (LiveView, LiveComponent)
    inline = getattr(cls, "template", None)
    name = getattr(cls, "template_name", None)
    if kind == "view" and (
        _overrides(cls, "get_template", framework)
        or _overrides(cls, "get_template_names", framework)
    ):
        return OwnerReport(cls, "", "", error="the template is chosen at runtime")
    if isinstance(inline, str) and inline:
        file, first_line = _inline_location(cls)
        key = ("inline", inline, file, first_line)
        if key not in cache:
            try:
                template = engine.from_string(inline)
            except Exception as exc:  # noqa: BLE001 -- djust-only syntax falls back to tokens
                scan = scan_tokens(inline, "<inline>", file, type(exc).__name__)
            else:
                scan = scan_source(engine, template, "<inline>", file, inline)
            offset = first_line - 1
            for item in [*scan.bindings, *scan.gaps]:
                if item.file == file:
                    item.line += offset
            cache[key] = scan
        label = "%s.template" % cls.__qualname__
    elif isinstance(name, str) and name:
        key = ("file", name)
        if key not in cache:
            try:
                template = engine.get_template(name)
            except Exception as exc:  # noqa: BLE001 -- a loader or syntax failure is examined below
                cache[key] = _uncompiled(engine, name, exc)
            else:
                path = getattr(getattr(template, "origin", None), "name", None) or name
                cache[key] = scan_source(engine, template, name, path, template.source)
        label = name
    else:
        return OwnerReport(cls, "", "", error="no template or template_name")
    scan = cache[key]
    if isinstance(scan, str):
        return OwnerReport(cls, label, "", error="the template cannot be compiled (%s)" % scan)
    report = OwnerReport(cls, label, scan.file, gaps=list(scan.gaps), files=tuple(scan.files))
    context = _OwnerContext(cls, kind, scan)
    report.results = [context.check(binding) for binding in scan.bindings]
    return report


# ---------------------------------------------------------------------------
# One owner's bindings
# ---------------------------------------------------------------------------


class _OwnerContext:
    def __init__(self, cls: type, kind: str, scan: Any) -> None:
        from djust._parameter_metadata import component_stop, declared_handlers, view_stop

        self.cls = cls
        self.kind = kind
        stop = component_stop if kind == "component" else view_stop
        self.handlers = {h.name: h for h in declared_handlers(cls, stop)}
        self.meta_event = self._meta_event() if kind == "component" else None
        self.recovery = {b.name for b in scan.bindings if b.directive == "dj-auto-recover"}
        if kind == "view":
            from djust.validation import recovery_handler_names

            self.recovery |= set(recovery_handler_names(cls))

    def _meta_event(self) -> Optional[str]:
        meta = inspect.getattr_static(self.cls, "Meta", None)
        event = inspect.getattr_static(meta, "event", None) if isinstance(meta, type) else None
        return event if isinstance(event, str) and not event.startswith("_") else None

    def check(self, binding: Any) -> BindingResult:
        if binding.region == "outside":
            return BindingResult(binding, "unsupported", "outside the live root")
        if binding.region in ("component", "embedded"):
            return BindingResult(
                binding,
                "dynamic",
                "inside markup a %s owns" % binding.region.replace("embedded", "child view"),
            )
        if binding.unresolved:
            return BindingResult(binding, "dynamic", binding.unresolved)
        if binding.malformed:
            return self._result(binding, [Finding("T019", binding.malformed, _NAME_HINT)])
        if not binding.spec.owner_context and self.kind == "component":
            return BindingResult(
                binding, "dynamic", "%s always reaches the host view" % binding.directive
            )
        name = binding.name
        handler = self.handlers.get(name)
        if handler is None:
            if name == self.meta_event:
                return BindingResult(binding, "checked", "Meta.event handler; arguments unchecked")
            finding = self._missing(name)
            if finding is None:
                return BindingResult(
                    binding, "dynamic", "the owner resolves attributes dynamically"
                )
            return self._result(binding, [finding])
        return self._result(binding, self._arguments(binding, handler))

    def _result(self, binding: Any, findings: list[Finding]) -> BindingResult:
        return BindingResult(binding, "checked", findings=findings)

    # -- T019 -------------------------------------------------------------

    def _missing(self, name: str) -> Optional[Finding]:
        from djust._component_subscriptions import is_component_subscription

        member = inspect.getattr_static(self.cls, name, _ABSENT)
        where = "%s.%s" % (self.cls.__module__, self.cls.__qualname__)
        if member is _ABSENT:
            if "__getattr__" in {n for k in self.cls.__mro__[:-1] for n in vars(k)}:
                return None
            owned = self._component_member(name)
            if owned is not None:
                return Finding("T019", owned[0] % (name, where), owned[1])
            return Finding(
                "T019",
                "%r is not a handler on %s." % (name, where),
                "Add an @event_handler method named %r to %s, or correct the binding."
                % (name, self.cls.__qualname__),
            )
        function = member.__func__ if isinstance(member, (staticmethod, classmethod)) else member
        if isinstance(function, types.FunctionType) and is_component_subscription(function):
            return Finding(
                "T019",
                "%r on %s is a component output callback, which the browser cannot call."
                % (name, where),
                "The component calls it when it emits the output. Bind the browser "
                "control to the component's own action instead.",
            )
        if callable(function):
            from djust.config import config as djust_config

            if djust_config.get("event_security", "strict") != "strict":
                return None
            return Finding(
                "T019",
                "%r on %s is not an @event_handler, so the server refuses the event."
                % (name, where),
                "Decorate %s.%s with @event_handler." % (self.cls.__qualname__, name),
            )
        return Finding(
            "T019",
            "%r on %s is not a method." % (name, where),
            "Bind the event to an @event_handler method.",
        )

    def _component_member(self, name: str) -> Optional[tuple[str, str]]:
        """A name that belongs to a declared interactive component, not the view."""
        from djust._component_subscriptions import DECLARATIONS_ATTR
        from djust._parameter_metadata import component_stop, declared_handlers

        declarations = getattr(self.cls, DECLARATIONS_ATTR, None) or {}
        for attribute, declaration in sorted(declarations.items()):
            outputs = {output.name for output in getattr(declaration, "outputs", ())}
            if name in outputs:
                return (
                    "%%r is an output of the %r component, not a handler on %%s." % attribute,
                    "Subscribe to it with @%s.on.%s; the component renders its own controls."
                    % (attribute, name),
                )
            component_type = getattr(declaration, "component_type", None)
            if isinstance(component_type, type) and any(
                h.name == name for h in declared_handlers(component_type, component_stop)
            ):
                return (
                    "%%r is an action of the %r component; a view-owned binding "
                    "never reaches it, and %%s has no handler of that name." % attribute,
                    "Render the component ({{ %s }}) so its own markup sends the action."
                    % attribute,
                )
        return None

    # -- T020-T022 --------------------------------------------------------

    def _arguments(self, binding: Any, handler: Any) -> list[Finding]:
        from djust._parameter_contract import ContractError
        from djust.checks.parameters import _bound
        from djust.validation import get_handler_parameter_policy

        method = _bound(handler.member, handler.function, self.cls)
        try:
            policy = get_handler_parameter_policy(method)
        except ContractError:
            return []  # djust.C021 / V016 report the invalid policy.
        if binding.directive == "dj-auto-recover" or binding.name in self.recovery:
            policy = "legacy"
        payload = binding.payload
        findings = _routing_findings(payload)
        if policy == "strict":
            findings += _strict_findings(binding, method)
        else:
            findings += _legacy_findings(binding, method)
        findings += _form_field_findings(self.cls, binding, handler)
        return findings


_NAME_HINT = (
    "Event names are lowercase identifiers. dj-submit, dj-keydown, dj-keyup and "
    "dj-click-away send their value verbatim; only call-style directives such as "
    "dj-click accept name(args)."
)


def _routing_findings(payload: Any) -> list[Finding]:
    from djust._template_bindings import ROUTING_KEYS

    findings = []
    seen = set()
    for key, (attribute, _value, _hint) in [*payload.legacy.items(), *payload.values.items()]:
        if key in ROUTING_KEYS and attribute not in seen:
            seen.add(attribute)
            findings.append(
                Finding(
                    "T022",
                    "%s supplies %r, which the server reads as routing context, not an argument."
                    % (attribute, key),
                    "The framework attaches view and component context itself. Rename "
                    "the attribute; it could redirect the event to another owner.",
                    supplied=[key],
                )
            )
    return findings


def _signature(method: Any) -> tuple[list[inspect.Parameter], bool]:
    signature = inspect.signature(method)
    params = [
        p
        for p in signature.parameters.values()
        if p.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
    ]
    open_ = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values())
    return params, open_


def _legacy_findings(binding: Any, method: Any) -> list[Finding]:
    from djust._template_bindings import ROUTING_KEYS

    payload = binding.payload
    try:
        params, open_ = _signature(method)
    except (TypeError, ValueError):
        return []
    accepted = [p.name for p in params]
    required = [p.name for p in params if p.default is inspect.Parameter.empty]
    keys = [*payload.generated, *payload.legacy_generated]
    keys += [k for k in payload.legacy if k not in keys]
    positional = payload.positional or []
    positional_names = accepted[: len(positional)]
    findings: list[Finding] = []
    if not open_:
        unexpected = [k for k in keys if k not in accepted and k not in ROUTING_KEYS]
        if unexpected:
            findings.append(
                Finding(
                    "T020",
                    "%s sends %s, which %s() does not accept."
                    % (binding.directive, _names(unexpected), binding.name),
                    _unexpected_hint(binding, unexpected),
                    expected=accepted,
                    supplied=keys,
                )
            )
    if len(positional) > len(accepted):
        findings.append(
            Finding(
                "T020",
                "%s passes %d positional arguments; %s() takes %d, and the rest are dropped."
                % (binding.directive, len(positional), binding.name, len(accepted)),
                "Remove the extra arguments or add parameters for them.",
                expected=accepted,
            )
        )
    if payload.legacy_complete and payload.generated_complete:
        missing = [n for n in required if n not in keys and n not in positional_names]
        if missing:
            findings.append(
                Finding(
                    "T020",
                    "%s() requires %s, which this binding never sends."
                    % (binding.name, _names(missing)),
                    "Add data-%s (or dj-value-%s) to the element, or give the parameter "
                    "a default only if it is genuinely optional."
                    % (missing[0].replace("_", "-"), missing[0].replace("_", "-")),
                    expected=accepted,
                    supplied=keys,
                )
            )
    for first, second, key in payload.legacy_duplicates:
        findings.append(
            Finding(
                "T020",
                "%s and %s both send %r; the browser keeps only one." % (first, second, key),
                "Remove one of the attributes.",
                supplied=[key],
            )
        )
    findings += _legacy_literal_findings(binding, method, positional_names, positional)
    return findings


def _unexpected_hint(binding: Any, unexpected: list[str]) -> str:
    generated = set(binding.payload.generated) | set(binding.payload.legacy_generated)
    if generated & set(unexpected):
        return (
            "Under the legacy parameter policy %s always sends %s. Accept them "
            "(for example with **kwargs) or use the strict policy, which sends only "
            "the names the handler declares." % (binding.directive, _names(sorted(generated)))
        )
    return "Rename the attribute to a parameter of the handler, or add the parameter."


def _client_hint_value(value: str, hint: Optional[str]) -> Any:
    """The value the legacy client sends for a typed data-* / dj-value-* literal."""
    if hint in ("int", "integer"):
        if value == "":
            return 0
        match = re.match(r"\s*[+-]?\d+", value)
        return int(match.group()) if match else None
    if hint in ("float", "number"):
        if value == "":
            return 0.0
        match = re.match(r"\s*[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?", value)
        return float(match.group()) if match else None
    if hint in ("bool", "boolean"):
        return value.lower() in ("true", "1", "yes", "on", "checked")
    if hint in ("json", "object", "array"):
        import json

        try:
            return json.loads(value)
        except ValueError:
            return value
    if hint == "list":
        return [v.strip() for v in value.split(",") if v.strip()]
    return value


def _legacy_literal_findings(
    binding: Any, method: Any, positional_names: list[str], positional: list[Any]
) -> list[Finding]:
    from djust.validation import coerce_parameter_types, validate_parameter_types

    literals: dict[str, tuple[str, Any]] = {}
    for key, (attribute, value, hint) in binding.payload.legacy.items():
        if value is not None:
            literals[key] = (attribute, _client_hint_value(value, hint))
    for name, value in zip(positional_names, positional):
        literals[name] = ("%s argument %r" % (binding.directive, value), value)
    findings = []
    for key, (source, value) in literals.items():
        coerced = coerce_parameter_types(method, {key: value})
        errors = validate_parameter_types(method, coerced)
        if errors:
            error = errors[0]
            findings.append(
                Finding(
                    "T021",
                    "%s gives %s() %r for %r, which is not a valid %s."
                    % (source, binding.name, value, key, error["expected"]),
                    "Correct the literal, or change the parameter's annotation.",
                    expected=[key],
                    supplied=[key],
                )
            )
    return findings


def _strict_findings(binding: Any, method: Any) -> list[Finding]:
    from djust._parameter_contract import ContractError, _convert
    from djust._template_bindings import ROUTING_KEYS, STRICT_HINT_TYPES
    from djust.validation import get_handler_coercion, get_strict_handler_contract

    try:
        contract = get_strict_handler_contract(method)
    except ContractError:
        return []  # V016 reports the declaration.
    payload = binding.payload
    metadata = contract.metadata()
    named = {
        p["name"]: p for p in metadata if p["kind"] in ("positional_or_keyword", "keyword_only")
    }
    open_ = any(p["kind"] == "var_keyword" for p in metadata)
    positional_params = [p for p in metadata if p["kind"].startswith("positional")]
    types_by_name = dict(contract.types)
    coerce = get_handler_coercion(method)
    generated = set(payload.generated)
    sent_generated = [g for g in payload.generated if g in named or open_]
    positional = payload.positional or []
    positional_names = [p["name"] for p in positional_params[: len(positional)]]
    findings: list[Finding] = []
    for attribute, key in payload.strict_invalid:
        if key in ROUTING_KEYS:
            continue  # T022
        findings.append(
            Finding(
                "T020",
                "%s is not a valid strict argument: names are identifiers, "
                "each given once, with at most one type hint." % attribute,
                "Rename the attribute (dj-value-item-id becomes item_id).",
                supplied=[key],
            )
        )
    values = {k: v for k, v in payload.values.items() if k not in ROUTING_KEYS}
    for key, (attribute, value, hint) in values.items():
        if key in generated:
            findings.append(
                Finding(
                    "T020",
                    "%s reuses %r, which %s generates itself; the browser rejects the event."
                    % (attribute, key, binding.directive),
                    "Rename the attribute.",
                    supplied=[key],
                )
            )
            continue
        declared = named.get(key)
        if declared is None and not open_:
            findings.append(
                Finding(
                    "T020",
                    "%s sends %r, which %s() does not declare." % (attribute, key, binding.name),
                    "Rename the attribute to a declared parameter, or add the parameter.",
                    expected=sorted(named),
                    supplied=sorted(values),
                )
            )
            continue
        label = (declared or {"type": "Any"})["type"]
        base = re.sub(r"^Optional\[(.*)\]$", r"\1", label)
        base = "list" if base.startswith("list[") else base
        if hint is not None:
            fits = STRICT_HINT_TYPES.get(hint, ())
            if hint not in STRICT_HINT_TYPES or (
                fits is not None and label != "Any" and base not in fits
            ):
                findings.append(
                    Finding(
                        "T021",
                        "%s: the hint %r does not fit %s() parameter %r (%s); the browser "
                        "rejects the event." % (attribute, hint, binding.name, key, label),
                        "Use a hint that produces %s, or remove the hint." % label,
                        expected=[key],
                        supplied=[key],
                    )
                )
                continue
        if value is None or declared is None:
            continue
        wire = _strict_wire_value(value, hint)
        if wire is _ABSENT:
            findings.append(
                Finding(
                    "T021",
                    "%s=%r is not a valid %s literal; the browser rejects the event."
                    % (attribute, value, hint),
                    "Correct the literal.",
                    expected=[key],
                    supplied=[key],
                )
            )
            continue
        try:
            _convert(wire, types_by_name[key], coerce)
        except (ValueError, TypeError, ArithmeticError):
            findings.append(
                Finding(
                    "T021",
                    "%s gives %s() %r for %r, which is not a valid %s."
                    % (attribute, binding.name, value, key, label),
                    "Correct the literal, or change the parameter's annotation.",
                    expected=[key],
                    supplied=[key],
                )
            )
    var_positional = any(p["kind"] == "var_positional" for p in metadata)
    if len(positional) > len(positional_params) and not var_positional:
        findings.append(
            Finding(
                "T020",
                "%s passes %d positional arguments; %s() takes %d."
                % (binding.directive, len(positional), binding.name, len(positional_params)),
                "Remove the extra arguments.",
                expected=[p["name"] for p in positional_params],
            )
        )
    for name, value in zip(positional_names, positional):
        if name in values:
            findings.append(
                Finding(
                    "T020",
                    "%r is supplied both positionally and by %s." % (name, values[name][0]),
                    "Supply it once.",
                    supplied=[name],
                )
            )
        elif name in types_by_name:
            try:
                _convert(value, types_by_name[name], coerce)
            except (ValueError, TypeError, ArithmeticError):
                findings.append(
                    Finding(
                        "T021",
                        "%s argument %r is not a valid %s for %s() parameter %r."
                        % (
                            binding.directive,
                            value,
                            named.get(name, {}).get("type", "value"),
                            binding.name,
                            name,
                        ),
                        "Correct the literal.",
                        expected=[name],
                        supplied=[name],
                    )
                )
    if payload.values_complete and payload.generated_complete:
        supplied = set(sent_generated) | set(values) | set(positional_names)
        missing = [n for n, p in named.items() if p["required"] and n not in supplied]
        if missing:
            data = [payload.data_keys[n] for n in missing if n in payload.data_keys]
            hint = (
                "Strict handlers receive dj-value-* attributes, never data-*: rename %s "
                "to dj-value-%s." % (data[0], data[0][5:])
                if data
                else "Add dj-value-%s to the element, or give the parameter a default "
                "only if it is genuinely optional." % missing[0].replace("_", "-")
            )
            findings.append(
                Finding(
                    "T020",
                    "%s() requires %s, which this binding never sends."
                    % (binding.name, _names(missing)),
                    hint,
                    expected=sorted(named),
                    supplied=sorted(supplied),
                )
            )
    return findings


def _strict_wire_value(value: str, hint: Optional[str]) -> Any:
    """The value the strict collector sends for a dj-value-* literal, or _ABSENT."""
    import json

    text = value.strip(" \t\n\r\v\f")
    if hint is None:
        return value
    if hint in ("int", "integer"):
        return int(text) if re.fullmatch(r"[+-]?[0-9]+", text) else _ABSENT
    if hint in ("float", "number"):
        pattern = r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?"
        return float(text) if re.fullmatch(pattern, text) else _ABSENT
    if hint in ("bool", "boolean"):
        lower = text.lower()
        if lower not in ("true", "false", "1", "0", "yes", "no", "on", "off"):
            return _ABSENT
        return lower in ("true", "1", "yes", "on")
    try:
        parsed = json.loads(value)
    except ValueError:
        return _ABSENT
    if hint in ("array", "list") and not isinstance(parsed, list):
        return _ABSENT
    if hint == "object" and not isinstance(parsed, dict):
        return _ABSENT
    return parsed


def _form_field_findings(cls: type, binding: Any, handler: Any) -> list[Finding]:
    """ADR-037 Q9: field names against a static ``form_class``'s ``base_fields``."""
    from djust.forms import FormMixin

    if handler.function not in (
        vars(FormMixin).get("validate_field"),
        vars(FormMixin).get("submit_form"),
    ):
        return []
    if _overrides(cls, "get_form_class", (FormMixin,)) or _overrides(
        cls, "get_prefix", (FormMixin,)
    ):
        return []
    form_class = getattr(cls, "form_class", None)
    fields = getattr(form_class, "base_fields", None)
    if not isinstance(fields, dict):
        return []
    prefix = getattr(cls, "prefix", None)
    marker = "%s-" % prefix if isinstance(prefix, str) and prefix else ""
    if handler.name == "validate_field":
        names = [binding.payload.field_name] if binding.payload.field_name else []
    else:
        names = [n for n in binding.payload.generated if n != "csrfmiddlewaretoken"]
    findings = []
    for name in names:
        logical = name[len(marker) :] if marker and name.startswith(marker) else name
        if logical not in fields:
            findings.append(
                Finding(
                    "T021",
                    "%r is not a field of %s, so %s() never sees it as one."
                    % (name, form_class.__name__, handler.name),
                    "Use one of: %s." % ", ".join(sorted(fields)),
                    expected=sorted(fields),
                    supplied=[name],
                )
            )
    return findings


def _names(names: list[str]) -> str:
    return ", ".join(repr(n) for n in names)


# ---------------------------------------------------------------------------
# Suppression, messages and coverage
# ---------------------------------------------------------------------------


def noqa_state(file: str, line: int, check_id: str) -> Optional[str]:
    """``"suppressed"``, ``"no-reason"`` (a bare noqa), or None for no comment."""
    if not file or line < 1:
        return None
    lines = linecache.getlines(file)
    state = None
    for number in (line, line - 1):
        if not 1 <= number <= len(lines):
            continue
        for match in _NOQA.finditer(lines[number - 1]):
            body = match.group("body").strip()
            if not body.startswith(":"):
                state = state or "no-reason"
                continue
            codes, _, reason = body[1:].partition("--")
            wanted = {c.strip() for c in codes.split(",")}
            if check_id not in wanted and "djust.%s" % check_id not in wanted:
                continue
            if reason.strip():
                return "suppressed"
            state = "no-reason"
    return state


def binding_reports() -> list[OwnerReport]:
    """Scan every owner's template. Nothing is constructed, mounted or rendered."""
    engine = _django_engine()
    if engine is None:
        return []
    cache: dict[Any, Any] = {}
    return [scan_owner(cls, kind, engine, cache) for cls, kind in _owners()]


def owner_kinds() -> dict[type, str]:
    """``{owner class: "view" | "component"}`` for every checked owner."""
    return dict(_owners())


def coverage(reports: list[OwnerReport]) -> dict[str, Any]:
    """ADR-037 Q2: the ``coverage`` object of ``djust_check --format json``."""
    totals = {"checked": 0, "dynamic": 0, "unsupported": 0}
    entries = []
    for report in reports:
        counts = {"checked": 0, "dynamic": 0, "unsupported": 0}
        bindings = []
        for result in report.results:
            counts[result.status] += 1
            entry = {
                "file": result.binding.file,
                "line": result.binding.line,
                "binding": result.binding.label,
                "status": result.status,
            }
            if result.reason:
                entry["reason"] = result.reason
            bindings.append(entry)
        for key, value in counts.items():
            totals[key] += value
        entry = {
            "owner": report.label,
            "template": report.template,
            "file": report.file,
            "complete": not report.error
            and not report.gaps
            and counts["dynamic"] == counts["unsupported"] == 0,
            "counts": counts,
            "bindings": bindings,
            "gaps": [{"file": g.file, "line": g.line, "reason": g.reason} for g in report.gaps],
        }
        if report.error:
            entry["error"] = report.error
        entries.append(entry)
    return {
        "bindings": totals,
        "owners": len(entries),
        "complete_owners": sum(1 for e in entries if e["complete"]),
        "templates": entries,
    }


def summary_line(data: dict[str, Any]) -> str:
    totals = data["bindings"]
    return (
        "Event bindings: %d checked, %d dynamic, %d unsupported across %d owners "
        "(%d with a complete event graph)."
        % (
            totals["checked"],
            totals["dynamic"],
            totals["unsupported"],
            data["owners"],
            data["complete_owners"],
        )
    )


def _messages(reports: list[OwnerReport]) -> list[CheckMessage]:
    messages: list[CheckMessage] = []
    for report in reports:
        for result in report.results:
            binding = result.binding
            for finding in result.findings:
                check_id = "djust.%s" % finding.check_id
                if _is_check_suppressed(check_id):
                    continue
                state = noqa_state(binding.file, binding.line, finding.check_id)
                if state == "suppressed":
                    continue
                message = "%s:%d: %s" % (binding.file, binding.line, finding.message)
                if state == "no-reason":
                    message += (
                        " (A noqa comment without a reason does not suppress %s; write "
                        "{# noqa: %s -- <reason> #}.)" % (finding.check_id, finding.check_id)
                    )
                messages.append(_emit(check_id, message, finding, report, binding))
    return messages


def _emit(
    check_id: str, message: str, finding: Finding, report: OwnerReport, binding: Any
) -> CheckMessage:
    """The one constructor of ``djust.T019``-``T022`` (one ID, one emitter)."""
    details: dict[str, Any] = {
        "hint": finding.hint,
        "fix_hint": finding.hint,
        "file_path": binding.file,
        "line_number": binding.line,
        "owner": report.label,
        "binding": binding.label,
        "expected": finding.expected,
        "supplied": finding.supplied,
    }
    if check_id == "djust.T019":
        return BindingWarning(message, id="djust.T019", **details)
    if check_id == "djust.T020":
        return BindingWarning(message, id="djust.T020", **details)
    if check_id == "djust.T021":
        return BindingWarning(message, id="djust.T021", **details)
    return BindingWarning(message, id="djust.T022", **details)


@register("djust")
def check_event_bindings(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    """``djust.T019``-``T022``: template event bindings against their owners."""
    if all(_is_check_suppressed("djust.%s" % check_id) for check_id in BINDING_IDS):
        return []
    try:
        from djust.live_view import LiveView  # noqa: F401
    except ImportError:
        return []
    return _messages(binding_reports())
