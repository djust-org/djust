"""
Rust-template-engine handler for the ``{% live_render %}`` tag (#1145).

Design (Stage 4 design pass)
============================

The Django path's ``live_render`` template tag (in
``djust.templatetags.live_tags``) implements ~210 lines of behaviour
for ``lazy=True`` — argument validation, sticky+lazy collision check,
config normalization (True / "visible" / dict), CSP nonce
propagation, thunk-closure construction, parent-scoped thunk stash,
and synchronous ``<dj-lazy-slot>`` placeholder emit. The Rust
template engine (``crates/djust_templates``) had no handler for the
tag at all — production users on ``RustLiveView`` got a "no handler
registered for tag: live_render" template error if they used
``lazy=True``, which is the failure mode that surfaced in PR #1138's
integration tests against the inline-attribute mode
``template = "{% live_render ... lazy=True %}"``.

Approach: register a tag handler that delegates to the existing
Django ``live_render`` template-tag function. The handler:

1. Parses the ``args`` list (a list of strings the Rust parser
   forwards verbatim) into a positional ``view_path`` and a kwargs
   dict, honouring the same shapes Django's parser would produce
   (string-literal first arg; ``key=value`` pairs; bare-identifier
   kwargs that resolve from the template context). Args resolution
   delegates to :py:meth:`TagHandler._resolve_arg` for parity with
   the other handlers.

2. Reaches into ``context`` for ``view`` (the parent ``LiveView``)
   and ``request``. These flow through the Rust→Python bridge via
   the new ``raw_py_objects`` sidecar threaded by
   ``call_handler_with_py_sidecar`` — see commit message and
   ``crates/djust_templates/src/registry.rs``.

3. Synthesizes a Django ``Context``/``RenderContext`` shape so the
   existing ``live_render`` function can run without modification,
   then returns the resulting HTML string. Critical: the thunk
   stash on ``parent._lazy_thunks`` happens by side-effect inside
   the Django function, exactly as on the Django path.
   ``RequestMixin.aget`` then transfers thunks onto the chunk
   emitter at the same flush point.

Raises identical-shape errors as the Django path:

- ``TemplateSyntaxError`` on ``sticky=True + lazy=True`` collision
- ``TemplateSyntaxError`` on dict-form ``lazy=`` validation failures
- ``TemplateSyntaxError`` on missing/invalid ``view_path``

The CSP nonce, placeholder HTML, and thunk closure are emitted by
the Django function we delegate to — no duplication, byte-for-byte
parity guaranteed by construction.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

from . import TagHandler, register

logger = logging.getLogger(__name__)


# Sentinel for unparseable args — bubble the original token back so the
# Django function raises the right TemplateSyntaxError shape if the user
# slipped through with a malformed call.
_UNPARSEABLE = object()


def _parse_lazy_value(raw: Any) -> Any:
    """Coerce a kwarg value that may carry literal ``True``/``"visible"``
    forms back to the Python types ``live_render`` expects.

    The Rust template engine pre-resolves ``True``/``False`` to
    ``Value::Bool(true|false)`` and stringifies them via Rust's
    ``Display`` impl, which emits lowercase ``"true"`` / ``"false"``.
    The Python tag-handler base then sees the equals-pair as
    ``("lazy", "true")`` (string). On the Django path the raw token is
    passed verbatim and Django's ``simple_tag`` parser converts it to
    ``True``. We bridge that gap here.

    Accepts:
    - ``True`` / ``False`` (already a Python bool)
    - The strings ``"True"``, ``"true"``, ``"False"``, ``"false"``
    - The string ``"'visible'"`` / ``'"visible"'`` (quoted literal)
    - A bare ``"visible"`` (already-resolved)
    - A dict (already-resolved from the context)

    Anything else returns the value unchanged — the Django function
    will raise on it.
    """
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        stripped = raw.strip()
        # "'visible'" or '"visible"' — strip outer quotes
        if (stripped.startswith("'") and stripped.endswith("'")) or (
            stripped.startswith('"') and stripped.endswith('"')
        ):
            inner = stripped[1:-1]
            if inner == "visible":
                return "visible"
            return inner
        # True/False string forms — Django passes capitalized tokens,
        # the Rust pre-resolution path emits lowercase via Rust's
        # ``bool::Display``. Accept both.
        if stripped in ("True", "true"):
            return True
        if stripped in ("False", "false"):
            return False
        if stripped == "visible":
            return "visible"
    return raw


@register("live_render")
class LiveRenderTagHandler(TagHandler):
    """Rust-template-engine handler for ``{% live_render %}`` (#1145).

    Delegates to the Django implementation in
    ``djust.templatetags.live_tags.live_render`` so the lazy=True
    branch (and the eager branch) produce byte-for-byte identical
    HTML on both rendering paths. Production users on
    ``RustLiveView`` who want streaming via ``lazy=True`` can now use
    it without falling back to the slower Python template engine.
    """

    def _split_args(self, args: List[str], context: Dict[str, Any]) -> Tuple[Any, Dict[str, Any]]:
        """Split the raw Rust-parser arg list into ``(view_path, kwargs)``.

        Mirrors the Django parser's behaviour for ``simple_tag`` so the
        downstream Django function sees exactly what it would on the
        Python path. View path may be a string literal (quoted) or a
        context variable that the Rust pre-resolution already inlined.
        """
        if not args:
            return _UNPARSEABLE, {}

        view_path = self._resolve_arg(args[0], context)
        # Strip surrounding quotes if Rust passed through the literal
        # form (e.g. ``"'myapp.views.X'"``). ``_resolve_arg`` already
        # strips outer quotes for string literals — this is defensive
        # in case the arg shape changes.
        if isinstance(view_path, str):
            view_path = view_path.strip("'\"")

        kwargs: Dict[str, Any] = {}
        for arg in args[1:]:
            resolved = self._resolve_arg(arg, context)
            if not isinstance(resolved, tuple):
                # Positional after kwargs makes no sense for live_render —
                # Django would have raised at parse time. Be lenient: drop
                # silently rather than crash the render. The Django
                # function's signature only accepts (view_path, **kwargs)
                # so unexpected positionals would TypeError anyway.
                logger.debug(
                    "live_render Rust handler: ignoring positional arg %r "
                    "after first; live_render only accepts kwargs after "
                    "view_path.",
                    arg,
                )
                continue
            key, value = resolved
            # The lazy= and sticky= keys carry types that the Django
            # function pattern-matches on (bool, "visible", dict, etc).
            # Coerce string-literal forms back to the Python types we
            # need.
            if key in ("lazy", "sticky"):
                value = _parse_lazy_value(value)
            kwargs[key] = value

        return view_path, kwargs

    def _build_django_context(self, raw_context: Dict[str, Any]) -> Any:
        """Build a Django ``Context`` instance carrying the raw Python
        objects (``view``, ``request``, etc.) that ``live_render``
        reads via ``context.get(...)`` and ``context.render_context``.

        We use a real ``Context`` so the Django function's
        ``context.render_context.setdefault`` path (used for sticky_id
        uniqueness tracking) works without monkey-patching.
        """
        from django.template import Context

        # Django's Context() accepts a flat dict and exposes
        # render_context as a stack-like object created lazily. The
        # values we need at the top of the stack: view, request, and
        # any user-set keys.
        ctx = Context(raw_context)
        return ctx

    def render(self, args: List[str], context: Dict[str, Any]) -> str:
        from django.template import TemplateSyntaxError

        view_path, kwargs = self._split_args(args, context)
        if view_path is _UNPARSEABLE:
            raise TemplateSyntaxError(
                "{% live_render %} requires a view_path positional argument; got an empty arg list."
            )

        # Delegate to the Django template-tag function. It expects a
        # Django ``Context`` instance as the first argument
        # (``takes_context=True`` simple_tag).
        from djust.templatetags.live_tags import live_render as _django_live_render

        django_ctx = self._build_django_context(context)
        result = _django_live_render(django_ctx, view_path, **kwargs)

        # ``live_render`` returns ``mark_safe(...)`` HTML strings. Coerce
        # to ``str`` for the Rust handler's string-return contract.
        # ``SafeString`` is a ``str`` subclass so this is a no-op at
        # the byte level — the safety marking is irrelevant here
        # because the Rust renderer doesn't re-escape custom-tag
        # output.
        return str(result)
