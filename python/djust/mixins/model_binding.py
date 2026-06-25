"""
ModelBindingMixin — Server-side support for dj-model two-way data binding.

Provides a default `update_model` event handler that sets attributes on the
view instance when the client sends dj-model changes.
"""

import logging
from typing import Any, List, Optional

try:
    from ..decorators import event_handler
except (ImportError, SystemError):
    # Fallback for direct-file imports (e.g. test_model_binding.py)
    def event_handler(fn: Any = None, **kw: Any) -> Any:  # type: ignore[misc,no-redef]
        return fn if fn is not None else (lambda f: f)


# Rust template walker that derives the dj-model allowlist from the TEMPLATE
# SOURCE (immune to rendered-output poisoning; see _dj_model_fields below).
# Resolve once, tolerating both the package-relative import and the
# direct-file import the unit tests use (when this module is loaded standalone
# the ``..`` parent package doesn't exist).
try:
    from .._rust import dj_model_fields_from_template  # type: ignore[attr-defined]
except (ImportError, SystemError):
    try:
        from djust._rust import dj_model_fields_from_template  # type: ignore[no-redef]
    except (ImportError, SystemError):  # pragma: no cover — Rust ext unavailable
        dj_model_fields_from_template = None  # type: ignore[assignment]


logger = logging.getLogger(__name__)

# Attributes that cannot be set via dj-model for security
FORBIDDEN_MODEL_FIELDS = frozenset(
    {
        "template_name",
        "request",
        "kwargs",
        "args",
        "session",
        "use_actors",
        "temporary_assigns",
        "_handler_metadata",
        "_rust_view",
        "_session_id",
        "_channel_name",
        "_view_path",
    }
)


class ModelBindingMixin:
    """
    Provides automatic `update_model` handler for dj-model bindings.

    Any LiveView attribute can be updated via dj-model unless it's in
    FORBIDDEN_MODEL_FIELDS or starts with underscore.

    Usage in template:
        <input type="text" dj-model="search_query">

    The view just needs the attribute defined:
        class MyView(LiveView):
            search_query = ""
    """

    # Optional: subclasses can restrict which fields are bindable. This is a
    # UNION with the auto-allowlist below — a field is bindable if it is in
    # EITHER. Subclasses set this to allow a field that is never rendered as a
    # ``dj-model`` binding (e.g. a value written purely programmatically).
    allowed_model_fields: Optional[List[str]] = None

    # Auto-allowlist (CWE-915 mass-assignment fix): the set of fields the
    # developer exposed via static ``dj-model="<field>"`` bindings in the
    # TEMPLATE SOURCE. Populated each render by
    # ``_record_dj_model_fields_from_rust()`` (LiveView paths) /
    # ``_record_dj_model_fields_from_source()`` (embedded children), which read
    # the binding set from the Rust template engine.
    #
    # SECURITY (finding #3): the set is derived from the parsed template AST's
    # ``Node::Text`` literals — developer-authored template text. Attacker data
    # can NEVER reach a ``Node::Text`` literal; it only ever flows through
    # ``{{ }}`` ``Node::Variable`` substitution at render time. This is the
    # reason the collection is done from the template SOURCE and not from the
    # RENDERED HTML: rendered output is attacker-influenceable (text nodes,
    # unquoted-interpolated attrs ``<div x={{ v }}>``, ``|safe`` content) and
    # parsing it re-opened the mass-assignment hole twice in review. A dynamic
    # ``dj-model="{{ var }}"`` binding straddles Text/Variable nodes so its
    # value is NOT captured — that field must be opted in via
    # ``allowed_model_fields`` (fail-closed).
    #
    # Defaults to an empty frozenset as a CLASS attribute so the attribute
    # always exists even before the first render and for non-LiveView users of
    # this mixin. ``update_model`` is fail-closed against this set: a public
    # attribute that the template never binds via ``dj-model`` (and is not in
    # ``allowed_model_fields``) cannot be set by the client, even though it
    # passes the ``_``-prefix / FORBIDDEN_MODEL_FIELDS / ``hasattr`` checks.
    _dj_model_fields: "frozenset[str]" = frozenset()

    def _record_dj_model_fields_from_rust(self, rust_view: Any) -> None:
        """Populate ``_dj_model_fields`` from a live ``RustLiveView``'s template.

        Called from every LiveView render site (``render``,
        ``render_full_template``/``_render_full_template_inner``, and
        ``render_with_diff``) AFTER the Rust view's template is current
        (post ``_initialize_rust_view`` / ``update_template``) so the
        auto-allowlist tracks the dj-model bindings the developer currently
        exposes on EVERY render — mount and re-render alike. A single shared
        helper avoids parallel-path drift (the same invariant must hold on all
        render paths; see CLAUDE.md #1646).

        ``rust_view.dj_model_fields()`` walks the parsed template AST in Rust
        (Text-node literals only; ``{% extends %}``/``{% include %}`` covered),
        so the result is immune to rendered-output poisoning.

        The result is stored as an instance ``frozenset``, shadowing the class
        default. It is a framework slot (recomputed each render, never
        persisted), so it must NOT leak into user-private state serialization;
        see the ``_dj_model_fields`` placeholder assigned before the
        ``_framework_attrs`` snapshot in ``LiveView.__init__``.
        """
        if rust_view is None:
            self._dj_model_fields = frozenset()
            return
        try:
            fields = rust_view.dj_model_fields()
        except Exception:  # noqa: BLE001 — never let allowlist collection break a render
            # Fail closed: leave no auto-allowed fields (explicit
            # allowed_model_fields still applies).
            logger.warning("[dj-model] auto-allowlist collection failed; failing closed")
            self._dj_model_fields = frozenset()
            return
        self._dj_model_fields = frozenset(fields)

    def _record_dj_model_fields_from_source(
        self, template_source: Optional[str], template_dirs: Optional[List[str]] = None
    ) -> None:
        """Populate ``_dj_model_fields`` from a raw template source string.

        Used by embedded ``{% live_render %}`` children, which render through
        Django's template engine (bypassing ``render_with_diff``) and so have no
        ``RustLiveView`` to query — their dj-model allowlist is derived from the
        CHILD's own template source via the Rust template walker
        (``dj_model_fields_from_template``). Same security property as
        :meth:`_record_dj_model_fields_from_rust` (Text-node literals only).
        """
        if not template_source or dj_model_fields_from_template is None:
            self._dj_model_fields = frozenset()
            return
        try:
            fields = dj_model_fields_from_template(template_source, template_dirs)
        except Exception:  # noqa: BLE001 — never let allowlist collection break a render
            logger.warning("[dj-model] auto-allowlist collection failed; failing closed")
            self._dj_model_fields = frozenset()
            return
        self._dj_model_fields = frozenset(fields)

    @event_handler
    def update_model(self, field: str = "", value: Any = None, **kwargs: Any) -> None:
        """
        Default handler for dj-model changes from the client.

        Args:
            field: The attribute name to update
            value: The new value
        """
        if not field or not isinstance(field, str):
            logger.warning("[dj-model] Missing or invalid field name")
            return

        # Security checks
        if field.startswith("_"):
            logger.warning("[dj-model] Blocked attempt to set private field: %s", field)
            return

        if field in FORBIDDEN_MODEL_FIELDS:
            logger.warning("[dj-model] Blocked attempt to set forbidden field: %s", field)
            return

        # Mass-assignment guard (CWE-915): only fields the developer actually
        # exposed in the template (auto-allowlist, populated each render) OR
        # explicitly listed in ``allowed_model_fields`` are bindable. This is
        # fail-closed — when neither contains ``field``, the write is denied.
        # Defends the standard djust state pattern: without this, ANY public,
        # existing view attribute (is_admin, account_id, total_price, …) could
        # be set by the client, not just the ``dj-model``-bound inputs.
        auto_allowed = field in self._dj_model_fields
        explicit_allowed = (
            self.allowed_model_fields is not None and field in self.allowed_model_fields
        )
        if not auto_allowed and not explicit_allowed:
            logger.warning(
                "[dj-model] Blocked attempt to set '%s' on %s: not bound via "
                "dj-model in the rendered template and not in "
                "allowed_model_fields (mass-assignment guard)",
                field,
                self.__class__.__name__,
            )
            return

        # Only update existing attributes (don't create new ones)
        if not hasattr(self, field):
            logger.warning(
                "[dj-model] Field '%s' does not exist on %s", field, self.__class__.__name__
            )
            return

        # Type coercion: try to match the existing attribute's type
        current = getattr(self, field)
        if current is not None and value is not None:
            try:
                # bool check MUST be before int (bool is subclass of int)
                if isinstance(current, bool) and not isinstance(value, bool):
                    value = str(value).lower() in ("true", "1", "yes", "on")
                elif (
                    isinstance(current, int)
                    and not isinstance(current, bool)
                    and not isinstance(value, int)
                ):
                    value = int(value)
                elif isinstance(current, float) and not isinstance(value, float):
                    value = float(value)
            except (ValueError, TypeError):
                logger.warning(
                    "[dj-model] Could not coerce '%s' to %s for field '%s'",
                    value,
                    type(current).__name__,
                    field,
                )
                return

        setattr(self, field, value)
        logger.debug("[dj-model] Set %s.%s = %r", self.__class__.__name__, field, value)

        # Skip re-render: update_model only stores a value on the view
        # instance — no DOM change is needed. Re-rendering would cause a
        # wasteful server round-trip that replaces the DOM and loses focus
        # on the input the user is actively typing in.
        self._skip_render = True
