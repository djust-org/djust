"""djust system checks — LiveView/component checks (V0xx) + sticky-child checks.

Split from the former monolithic ``checks.py`` (#1822). No behavior change.
"""

import ast
import inspect
import logging
import os
import re
from collections.abc import Iterator
from typing import Any, Optional

from django.core.checks import CheckMessage, register

import djust.checks as _root
from djust.checks.utils import (
    DjustError,
    DjustInfo,
    DjustWarning,
    _has_noqa,
    _is_check_suppressed,
    _iter_python_files,
    _iter_template_files,
    _parse_python_file,
    _walk_subclasses,
    _get_template_dirs,
    _strip_verbatim_blocks,
    _LIVE_RENDER_TAG_RE,
    _LIVE_RENDER_STICKY_TRUTHY_RE,
    _LIVE_RENDER_STICKY_FALSY_RE,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EVENT_HANDLER_LIKE_NAMES = re.compile(
    r"^(handle_|on_|toggle_|select_|update_|delete_|create_|add_|remove_|save_|cancel_|submit_|close_|open_)"
)

_SERVICE_INSTANCE_KEYWORDS = re.compile(r"(Service|Client|Session|API|Connection)", re.IGNORECASE)

# V012 (#1803) — match a REAL ``<div ... dj-view ...>`` opening tag (a
# standalone ``dj-view`` attribute), not the bare substring ``_DJ_VIEW_RE``
# uses. Anchoring to an actual tag with the negative-lookbehind / lookahead
# guards (mirrors ``mixins/template.py:_DJ_VIEW_RE``) means prose / comment
# text that merely mentions ``dj-view`` (e.g. the "do not add another
# ``dj-view`` here" note in the sticky example template) does NOT false-match —
# only an authored attribute on a real element does.
_DJ_VIEW_TAG_RE = re.compile(
    r"<div\b[^>]*?(?<![A-Za-z0-9_-])dj-view(?=[\s=>/])[^>]*>",
    re.IGNORECASE,
)

# V012 (#1803) — comment regions to strip before the ``<div ... dj-view ...>``
# root scan. A sticky-child template commonly documents the wrapper it lives
# inside (the demo's ``audio_player.html`` comment literally shows
# ``<div dj-view dj-sticky-view="audio-player" ...>``); that example tag is
# inside a comment, not the real root, so it must NOT trigger V012. Covers
# Django block comments, Django inline comments, and HTML comments.
_DJANGO_COMMENT_BLOCK_RE = re.compile(
    r"\{%\s*comment\b[^%]*%\}.*?\{%\s*endcomment\s*%\}", re.DOTALL
)
_DJANGO_INLINE_COMMENT_RE = re.compile(r"\{#.*?#\}", re.DOTALL)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def _strip_template_comments(content: str) -> str:
    """Remove Django/HTML comment regions so a commented-out / documented
    example tag never false-matches the V012 root scan.

    Returns ``content`` unchanged when no comment region is present (the
    common case). Line numbers are not preserved (V012 reports the class
    source line, not a template line, so alignment is unnecessary here).
    """
    if "{%" not in content and "{#" not in content and "<!--" not in content:
        return content
    content = _DJANGO_COMMENT_BLOCK_RE.sub("", content)
    content = _DJANGO_INLINE_COMMENT_RE.sub("", content)
    content = _HTML_COMMENT_RE.sub("", content)
    return content


def _routed_liveview_classes() -> Iterator[type]:
    """Yield LiveView subclasses reachable from the root URLconf.

    ``check_liveviews`` otherwise discovers views only via ``__subclasses__()``,
    which sees a class only once its module has been imported. A URL-routed
    LiveView whose module isn't imported by anything else at check time is
    therefore invisible to that walk — so its missing-from-allowlist
    misconfiguration (``V005``) is never flagged at ``manage.py check`` time,
    and it silently degrades to HTTP fallback in the browser (#1674).

    Walking the resolver imports the view modules (Django stamps
    ``view_class`` on every ``as_view()`` callback) and discovers them
    deterministically. URLconf import errors are surfaced by Django's own URL
    system checks, so they're swallowed here rather than crashing this check.
    """
    try:
        from django.urls import get_resolver

        from djust.live_view import LiveView
    except Exception:
        return

    def _walk(node: Any) -> Iterator[type]:
        nested = getattr(node, "url_patterns", None)
        if nested is not None:
            for child in nested:
                yield from _walk(child)
            return
        view_class = getattr(getattr(node, "callback", None), "view_class", None)
        if isinstance(view_class, type) and issubclass(view_class, LiveView):
            yield view_class

    try:
        yield from _walk(get_resolver())
    except Exception:
        return


@register("djust")
def check_liveviews(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    """Validate LiveView subclasses."""
    errors: list[CheckMessage] = []

    try:
        from djust.live_view import LiveView
    except ImportError:
        return errors

    from django.conf import settings
    from djust.decorators import is_event_handler

    # Discover LiveViews from BOTH __subclasses__() (imported classes) AND the
    # root URLconf (URL-routed views, whose module may not be imported anywhere
    # else at check time). The union closes the #1674 gap where a routed view
    # missing from LIVEVIEW_ALLOWED_MODULES was never flagged at check time.
    # Sorted for deterministic check output across runs.
    discovered = {*_walk_subclasses(LiveView), *_routed_liveview_classes()}
    for cls in sorted(
        discovered,
        key=lambda c: (getattr(c, "__module__", ""), getattr(c, "__qualname__", "")),
    ):
        # Skip abstract-looking classes (mixins, bases defined in djust itself)
        module = getattr(cls, "__module__", "") or ""
        if module.startswith("djust.") or module.startswith("djust_"):
            # Skip internal djust classes -- only check user classes
            # But still check classes in djust's own examples/tests
            if "test" not in module and "example" not in module:
                continue

        # User-declared abstract base classes opt out of all per-class V/Q checks
        # by setting `abstract = True` on the class body (#1605). Consulted via
        # __dict__ so the marker is NOT inherited: subclasses of an abstract base
        # are validated as concrete unless they also redeclare abstract = True.
        # Mirrors Django's Meta.abstract semantics.
        if cls.__dict__.get("abstract") is True:
            continue

        cls_label = "%s.%s" % (cls.__module__, cls.__qualname__)

        # V001 -- missing template_name
        has_template_name = (
            cls.__dict__.get("template_name") is not None
            or cls.__dict__.get("template") is not None
        )
        if not has_template_name:
            # Check parent classes (but not LiveView itself)
            found_in_parent = False
            for parent in cls.__mro__[1:]:
                if parent is LiveView:
                    break
                if parent.__dict__.get("template_name") or parent.__dict__.get("template"):
                    found_in_parent = True
                    break
            if not found_in_parent and not _is_check_suppressed("djust.V001"):
                cls_file = ""
                cls_line = None
                try:
                    cls_file = inspect.getfile(cls)
                    cls_line = inspect.getsourcelines(cls)[1]
                except (OSError, TypeError):
                    pass  # Source introspection may fail for built-in or C-extension classes
                errors.append(
                    DjustWarning(
                        "%s: missing 'template_name' attribute." % cls_label,
                        hint=(
                            "Set template_name on your LiveView class. "
                            "Mark abstract base classes with `abstract = True` to skip this check, "
                            "or suppress globally with "
                            "DJUST_CONFIG = {'suppress_checks': ['V001']}."
                        ),
                        id="djust.V001",
                        fix_hint=(
                            "Add `template_name = 'your_template.html'` as a class "
                            "attribute on `%s`." % cls.__qualname__
                        ),
                        file_path=cls_file,
                        line_number=cls_line,
                    )
                )

        # V002 -- missing mount() method
        if "mount" not in cls.__dict__:
            # Check if any parent (other than LiveView/mixins) defines mount
            has_mount = False
            for parent in cls.__mro__[1:]:
                if parent is LiveView:
                    break
                if "mount" in parent.__dict__:
                    has_mount = True
                    break
            if not has_mount and not _is_check_suppressed("djust.V002"):
                cls_file = ""
                cls_line = None
                try:
                    cls_file = inspect.getfile(cls)
                    cls_line = inspect.getsourcelines(cls)[1]
                except (OSError, TypeError):
                    pass  # Source introspection may fail for built-in or C-extension classes
                errors.append(
                    DjustInfo(
                        "%s: no mount() method defined." % cls_label,
                        hint="Define mount(self, request, **kwargs) to initialise state.",
                        id="djust.V002",
                        fix_hint=(
                            "Add a `def mount(self, request, **kwargs):` method to `%s`."
                            % cls.__qualname__
                        ),
                        file_path=cls_file,
                        line_number=cls_line,
                    )
                )

        # V003 -- mount() has wrong signature
        mount_method = cls.__dict__.get("mount")
        if mount_method and callable(mount_method):
            sig = inspect.signature(mount_method)
            params = list(sig.parameters.keys())
            # Should be (self, request, **kwargs) at minimum
            if (len(params) < 2 or params[1] != "request") and not _is_check_suppressed(
                "djust.V003"
            ):
                cls_file = ""
                cls_line = None
                try:
                    cls_file = inspect.getfile(cls)
                    cls_line = inspect.getsourcelines(mount_method)[1]
                except (OSError, TypeError):
                    pass  # Source introspection may fail for built-in or C-extension classes
                errors.append(
                    DjustError(
                        "%s: mount() should accept (self, request, **kwargs)." % cls_label,
                        hint="Change signature to: def mount(self, request, **kwargs):",
                        id="djust.V003",
                        fix_hint=(
                            "Change the `mount()` signature to "
                            "`def mount(self, request, **kwargs):` in `%s`." % cls.__qualname__
                        ),
                        file_path=cls_file,
                        line_number=cls_line,
                    )
                )

        # V004 -- public method looks like event handler but missing @event_handler
        for name, method in cls.__dict__.items():
            if name.startswith("_"):
                continue
            if not callable(method):
                continue
            # Skip known lifecycle methods — these are called by the framework, not
            # by user events, and should never carry @event_handler.
            if name in (
                "mount",
                "get_context_data",
                "dispatch",
                "setup",
                "get",
                "post",
                "handle_params",
                "handle_disconnect",
                "handle_connect",
                "handle_event",
                # Framework-invoked lifecycle hooks (#1684). The framework calls
                # these directly (self.X() / getattr / hasattr), NOT via the
                # user-event router, so they must NOT carry @event_handler — but
                # their names match the event-handler-like regex. Fix originally
                # landed on the 1.1 branch (#1685) against the pre-split checks.py;
                # ported here so main's split checks/ carries it too.
                "handle_presence_join",  # presence.py track_presence()
                "handle_presence_leave",  # presence.py untrack_presence()
                "handle_cursor_move",  # websocket.py cursor_move (LiveCursorMixin)
                "handle_tick",  # websocket.py tick loop (LiveView)
                "handle_async_result",  # websocket.py/sse.py background-work completion
                "handle_component_event",  # mixins/components.py child-component callback
                "handle_info",  # websocket.py channel-layer/info (NotificationMixin)
                "on_wizard_complete",  # wizard.py submit_wizard() (WizardMixin)
            ):
                continue
            if is_event_handler(method):
                continue
            if _EVENT_HANDLER_LIKE_NAMES.match(name) and not _is_check_suppressed("djust.V004"):
                method_file = ""
                method_line = None
                try:
                    method_file = inspect.getfile(method)
                    method_line = inspect.getsourcelines(method)[1]
                except (OSError, TypeError):
                    pass  # Source introspection may fail for built-in or C-extension classes
                errors.append(
                    DjustInfo(
                        "%s.%s() looks like an event handler but is missing @event_handler."
                        % (cls_label, name),
                        hint="Add @event_handler decorator or prefix with _ if it is private.",
                        id="djust.V004",
                        fix_hint=(
                            "Add `@event_handler()` decorator above the method `%s` in `%s`."
                            % (name, method_file or cls_label)
                        ),
                        file_path=method_file,
                        line_number=method_line,
                    )
                )

        # Q007 -- overlapping static_assigns and temporary_assigns
        static = set(getattr(cls, "static_assigns", []))
        temporary = set(getattr(cls, "temporary_assigns", {}).keys())
        overlap = static & temporary
        if overlap and not _is_check_suppressed("djust.Q007"):
            cls_file = ""
            cls_line = None
            try:
                cls_file = inspect.getfile(cls)
                cls_line = inspect.getsourcelines(cls)[1]
            except (OSError, TypeError):
                pass  # Source introspection may fail for built-in or C-extension classes
            errors.append(
                DjustWarning(
                    "%s: keys %s appear in both static_assigns and temporary_assigns."
                    % (cls_label, overlap),
                    hint="A key cannot be both static (never re-sent) and temporary (cleared after render).",
                    id="djust.Q007",
                    fix_hint=(
                        "Remove overlapping keys from either static_assigns or "
                        "temporary_assigns in `%s`." % cls.__qualname__
                    ),
                    file_path=cls_file,
                    line_number=cls_line,
                )
            )

        # V005 -- module not allowlisted. Mirror the WebSocket mount
        # enforcement (websocket.py): the allowlist is enforced ONLY when
        # non-empty, and a view is permitted when an allowed entry is a PREFIX
        # of its view path — NOT by exact membership. Keeping V005 in sync with
        # the runtime check avoids false positives now that URL-routed views are
        # also discovered (#1674; parallel-path-drift, CLAUDE.md #1646): e.g. an
        # allowlist of ['myapp'] permits 'myapp.views.MyView', and an explicitly
        # empty [] means allow-all (no longer flags every view).
        allowed = getattr(settings, "LIVEVIEW_ALLOWED_MODULES", None)
        view_path = "%s.%s" % (module, getattr(cls, "__name__", ""))
        is_allowlisted = allowed and any(
            view_path.startswith(m) or module.startswith(m) for m in allowed
        )
        if allowed and not is_allowlisted and not _is_check_suppressed("djust.V005"):
            errors.append(
                DjustWarning(
                    "%s is not in LIVEVIEW_ALLOWED_MODULES. "
                    "WebSocket mount will silently fail." % cls_label,
                    hint=(
                        "Add '%s' to LIVEVIEW_ALLOWED_MODULES in settings. "
                        "Mark abstract base classes with `abstract = True` to skip this check, "
                        "or suppress globally with "
                        "DJUST_CONFIG = {'suppress_checks': ['V005']}." % module
                    ),
                    id="djust.V005",
                    fix_hint=(
                        "Add `'%s'` to the `LIVEVIEW_ALLOWED_MODULES` list in your "
                        "Django settings file." % module
                    ),
                )
            )

        # V007 -- event handler missing **kwargs
        for name, method in cls.__dict__.items():
            if not callable(method):
                continue
            if not is_event_handler(method):
                continue
            # Unwrap decorators to get original function
            inner = method
            for _attempt in range(10):
                inner = getattr(inner, "__wrapped__", None) or getattr(inner, "func", None)
                if inner is None:
                    break
            sig_target = inner if inner is not None else method
            try:
                sig = inspect.signature(sig_target)
            except (ValueError, TypeError):
                continue
            has_var_keyword = any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
            )
            if not has_var_keyword and not _is_check_suppressed("djust.V007"):
                method_file = ""
                method_line = None
                try:
                    method_file = inspect.getfile(sig_target)
                    method_line = inspect.getsourcelines(sig_target)[1]
                except (OSError, TypeError):
                    pass  # Source introspection may fail for built-in or C-extension classes
                errors.append(
                    DjustWarning(
                        "%s.%s() event handler missing **kwargs in signature." % (cls_label, name),
                        hint="Add **kwargs to the event handler signature to receive event parameters.",
                        id="djust.V007",
                        fix_hint=(
                            "Add `**kwargs` to the `%s` method signature in `%s`."
                            % (name, method_file or cls_label)
                        ),
                        file_path=method_file,
                        line_number=method_line,
                    )
                )

        # V009 -- on_mount contains non-callable items
        on_mount_hooks = cls.__dict__.get("on_mount")
        if on_mount_hooks is not None:
            if not isinstance(on_mount_hooks, (list, tuple)):
                cls_file = ""
                cls_line = None
                try:
                    cls_file = inspect.getfile(cls)
                    cls_line = inspect.getsourcelines(cls)[1]
                except (OSError, TypeError):
                    pass  # Fall back to empty file/line for built-in or C-extension classes
                errors.append(
                    DjustWarning(
                        "%s: 'on_mount' should be a list of hook functions." % cls_label,
                        hint="Set on_mount = [hook1, hook2, ...] on your LiveView class.",
                        id="djust.V009",
                        fix_hint=("Change `on_mount` to a list in `%s`." % cls.__qualname__),
                        file_path=cls_file,
                        line_number=cls_line,
                    )
                )
            else:
                for i, hook in enumerate(on_mount_hooks):
                    if not callable(hook):
                        cls_file = ""
                        cls_line = None
                        try:
                            cls_file = inspect.getfile(cls)
                            cls_line = inspect.getsourcelines(cls)[1]
                        except (OSError, TypeError):
                            pass  # Fall back to empty file/line for built-in or C-extension classes
                        errors.append(
                            DjustWarning(
                                "%s: on_mount[%d] is not callable (%s)."
                                % (cls_label, i, type(hook).__name__),
                                hint="Each on_mount entry must be a callable hook function.",
                                id="djust.V009",
                                fix_hint=(
                                    "Ensure all items in `on_mount` are callable "
                                    "in `%s`." % cls.__qualname__
                                ),
                                file_path=cls_file,
                                line_number=cls_line,
                            )
                        )

    # V010 -- TutorialMixin listed after LiveView in MRO (#691)
    _check_tutorial_mixin_mro(errors, LiveView)

    # V006 -- service instance in mount() (AST-based scan of project files)
    _check_service_instances_in_mount(errors)

    # V008 -- non-primitive type assignments in mount() (broader than V006)
    _check_non_primitive_assignments_in_mount(errors)

    return errors


def _check_tutorial_mixin_mro(errors: list[CheckMessage], LiveView: type) -> None:
    """V010 (Error): Detect TutorialMixin listed after LiveView in the MRO.

    Django's ``View.__init__`` does not call ``super().__init__()``, so any
    mixin listed after a ``View``-derived class in the bases tuple never gets
    its ``__init__`` called.  When ``TutorialMixin`` is listed after
    ``LiveView``, its instance state (``tutorial_running``, signals, etc.) is
    never initialised and the tour silently fails at runtime.

    Fires ``djust.V010`` as an **Error** because the class is guaranteed to
    break at runtime — not a style issue.

    See: https://github.com/djust-org/djust/issues/691
    """
    if _is_check_suppressed("djust.V010"):
        return

    try:
        from djust.tutorials.mixin import TutorialMixin
    except ImportError:
        return

    from django.views import View

    for cls in _walk_subclasses(LiveView):
        module = getattr(cls, "__module__", "") or ""
        if module.startswith("djust.") or module.startswith("djust_"):
            if "test" not in module and "example" not in module:
                continue

        if TutorialMixin not in cls.__mro__:
            continue

        # Check that TutorialMixin appears before any View-derived class
        # in the *direct bases* (not the full MRO). If a user writes
        # ``class MyView(LiveView, TutorialMixin)``, TutorialMixin.__init__
        # is unreachable because View.__init__ breaks the super() chain.
        bases = cls.__bases__
        tutorial_idx = None
        view_idx = None
        for i, base in enumerate(bases):
            if tutorial_idx is None and issubclass(base, TutorialMixin):
                tutorial_idx = i
            if view_idx is None and issubclass(base, View):
                view_idx = i

        if tutorial_idx is not None and view_idx is not None and tutorial_idx > view_idx:
            cls_label = "%s.%s" % (cls.__module__, cls.__qualname__)
            cls_file = ""
            cls_line = None
            try:
                cls_file = inspect.getfile(cls)
                cls_line = inspect.getsourcelines(cls)[1]
            except (OSError, TypeError):
                pass  # Source introspection may fail for built-in or C-extension classes
            errors.append(
                DjustError(
                    "%s: TutorialMixin must be listed before LiveView in bases." % cls_label,
                    hint=(
                        "Change `class %s(LiveView, TutorialMixin)` to "
                        "`class %s(TutorialMixin, LiveView)`. Django's View.__init__ "
                        "does not call super().__init__(), so mixins listed after "
                        "LiveView never get initialised." % (cls.__qualname__, cls.__qualname__)
                    ),
                    id="djust.V010",
                    fix_hint=(
                        "Reorder bases: `class %s(TutorialMixin, LiveView):`" % cls.__qualname__
                    ),
                    file_path=cls_file,
                    line_number=cls_line,
                )
            )


# First-positional-arg extraction for a ``{% live_render %}`` tag body. The
# ``_LIVE_RENDER_TAG_RE`` capture group holds everything after ``live_render``;
# the first positional arg (the quoted dotted child path) is at the START.
# A bare-identifier first arg (``{% live_render some_var %}``) yields no match
# and is skipped — a dynamic path is unresolvable statically.
_LIVE_RENDER_FIRST_ARG_RE = re.compile(r"""^\s*["']([^"']+)["']""")


@register("djust")
def check_sticky_child_optin(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    """V011 (Warning): sticky child opts into ``enable_state_snapshot`` but its
    embedding parent does not — ADR-018 Decision 5 enforcement.

    A sticky child is persisted across a WebSocket reconnect only when BOTH
    the child class AND its embedding parent class set
    ``enable_state_snapshot = True`` (the ``sticky_child_should_persist``
    both-opt-in gate). A child that opts in under a parent that does not gets
    silently-incomplete persistence — its save is skipped, its state is lost
    on reconnect. V011 surfaces that misconfiguration at ``manage.py check``
    time.

    Discovery (mirrors the A075 template scanner):

    1. Walk ``LiveView`` subclasses, build a ``template_name -> [parent cls]``
       map (a child template may be embedded under several parents).
    2. Walk every template file, for each ``{% live_render ... sticky=True %}``
       tag resolve the child class via ``import_string``.
    3. If the child opts in (``enable_state_snapshot`` + truthy ``sticky_id``)
       and a matched parent does NOT opt in → emit V011.

    Conservative skips (no false positives): unresolvable dynamic view paths,
    ``import_string`` failures, templates with no matching parent class, and
    children that themselves don't opt in are all skipped silently — the
    runtime one-shot warning (``warn_sticky_child_optin_skip``) is the safety
    net for cases the static scan can't see.
    """
    errors: list[CheckMessage] = []

    if _is_check_suppressed("djust.V011"):
        return errors

    try:
        from djust.live_view import LiveView
    except ImportError:
        return errors

    from django.utils.module_loading import import_string

    # Build template_name -> [parent LiveView class, ...]. Mirrors
    # check_liveviews' internal-class skip: djust-internal classes are
    # skipped UNLESS the module is a test/example module.
    parent_map: dict[str, list[type]] = {}
    for cls in _walk_subclasses(LiveView):
        module = getattr(cls, "__module__", "") or ""
        if module.startswith("djust.") or module.startswith("djust_"):
            if "test" not in module and "example" not in module:
                continue
        tpl = cls.__dict__.get("template_name")
        if not tpl:
            continue
        parent_map.setdefault(tpl, []).append(cls)

    for filepath in _iter_template_files(_get_template_dirs()):
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except OSError:
            continue

        # The template's path RELATIVE to its template dir is what
        # ``template_name`` holds (e.g. ``parent_page.html`` or
        # ``app/parent.html``). Resolve against each configured dir.
        relname = None
        for tpl_dir in _get_template_dirs():
            try:
                candidate = os.path.relpath(filepath, tpl_dir)
            except ValueError:
                continue
            if not candidate.startswith(".."):
                relname = candidate
                break
        if relname is None:
            continue

        scan_source = _strip_verbatim_blocks(content)
        for match in _LIVE_RENDER_TAG_RE.finditer(scan_source):
            args = match.group(1)

            # Only sticky embeds are persistable (Decision 1) — a non-sticky
            # ``{% live_render %}`` is explicitly unsupported, not flagged.
            sticky_falsy = bool(_LIVE_RENDER_STICKY_FALSY_RE.search(args))
            sticky_truthy = bool(_LIVE_RENDER_STICKY_TRUTHY_RE.search(args)) and not sticky_falsy
            if not sticky_truthy:
                continue

            arg_match = _LIVE_RENDER_FIRST_ARG_RE.match(args)
            if not arg_match:
                # Dynamic (bare-identifier) child path — unresolvable
                # statically; the runtime warning covers it.
                continue
            child_path = arg_match.group(1)

            try:
                child_cls = import_string(child_path)
            except (ImportError, AttributeError, ModuleNotFoundError, ValueError):
                # A broken path is A075/runtime's problem, not V011's.
                continue

            # Child opt-in test: only an opted-in sticky child can be a
            # Decision-5 misconfiguration. "Neither opts in" is silent.
            child_opts_in = bool(getattr(child_cls, "enable_state_snapshot", False)) and bool(
                getattr(child_cls, "sticky_id", None)
            )
            if not child_opts_in:
                continue

            matched_parents = parent_map.get(relname, [])
            if not matched_parents:
                # No parent class maps to this template — can't statically
                # determine the parent. Skip conservatively.
                continue

            for parent_cls in matched_parents:
                if getattr(parent_cls, "enable_state_snapshot", False):
                    continue  # this parent opts in — tree-consistent restore

                child_label = "%s.%s" % (child_cls.__module__, child_cls.__qualname__)
                parent_label = "%s.%s" % (parent_cls.__module__, parent_cls.__qualname__)

                child_file = ""
                child_line = None
                try:
                    child_file = inspect.getfile(child_cls)
                    child_line = inspect.getsourcelines(child_cls)[1]
                except (OSError, TypeError):
                    pass  # Source introspection may fail for some classes.

                errors.append(
                    DjustWarning(
                        "%s: used as a sticky child with enable_state_snapshot=True, "
                        "but embedding parent %s does not opt in — the child's state "
                        "will be silently dropped on reconnect." % (child_label, parent_label),
                        hint=(
                            "ADR-018 Decision 5: a sticky child is persisted across a "
                            "WebSocket reconnect only when BOTH the child class and its "
                            "embedding parent class set enable_state_snapshot = True. "
                            "Requiring the parent too keeps the reconnect-restored "
                            "subtree tree-consistent — a child must not restore to "
                            "saved state while its parent re-mounts fresh. Suppress "
                            "with DJUST_CONFIG = {'suppress_checks': ['V011']} if you "
                            "have a deliberate reason."
                        ),
                        id="djust.V011",
                        fix_hint=(
                            "Add `enable_state_snapshot = True` to `%s`, or remove it "
                            "from `%s`." % (parent_cls.__qualname__, child_cls.__qualname__)
                        ),
                        file_path=child_file,
                        line_number=child_line,
                    )
                )

    return errors


def _sticky_child_template_source(cls: type) -> Optional[str]:
    """Return the raw template source for a sticky-child view, or None.

    Reads ``cls.template`` (inline string) directly, otherwise resolves
    ``cls.template_name`` via Django's template loader. Returns None on any
    failure (missing template, loader error, no template configured) — V012
    must skip conservatively rather than raise from a startup check.

    Consulted via ``__dict__`` for ``template`` / ``template_name`` so the
    own-declaration is read, mirroring how V001 looks up ``template_name``.
    """
    inline = cls.__dict__.get("template")
    if isinstance(inline, str) and inline:
        return inline
    tpl_name = cls.__dict__.get("template_name")
    if not tpl_name:
        # Fall back to an inherited template_name (a subclass may inherit it).
        tpl_name = getattr(cls, "template_name", None)
    if not isinstance(tpl_name, str) or not tpl_name:
        return None
    try:
        from django.template import loader

        source: str = loader.get_template(tpl_name).template.source
        return source
    except Exception:
        # TemplateDoesNotExist, backend errors, missing engine, etc. — the
        # template-resolution problem is V001/runtime's concern, not V012's.
        return None


@register("djust")
def check_sticky_child_own_dj_view(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    """V012 (Warning): a sticky-child LiveView declares its own ``dj-view`` on
    its template root — a nested/duplicate binding footgun (closes #1803).

    A sticky child is embedded via ``{% live_render "...Path" sticky=True %}``,
    which makes the framework emit the wrapper element itself::

        <div dj-view dj-sticky-view="<id>" dj-sticky-root
             data-djust-embedded="<id>">…child HTML…</div>

    (see ``static/djust/src/45-child-view.js``). If the child's OWN template
    root ALSO carries a ``dj-view`` attribute, the rendered page ends up with a
    nested/duplicate ``dj-view`` inside the wrapper. The child's client-side
    mount breaks and its ``dj-click`` / ``dj-input`` events silently don't bind.

    The footgun is subtle precisely because normal *page* views REQUIRE
    ``dj-view="<path>"`` on their root to be browser-mountable — so authors
    (and code-generating agents) reasonably add it everywhere, including sticky
    children, where it is wrong. V012 converts the silent footgun into a
    ``manage.py check`` warning.

    Discovery + false-positive guards:

    - Only ``LiveView`` subclasses with a truthy ``sticky`` attribute are
      scanned. Normal page views (which legitimately declare ``dj-view``) are
      NEVER inspected, so V012 cannot false-positive on them.
    - The scan uses an anchored ``<div ... dj-view ...>`` opening-tag regex,
      not a bare substring — comment/prose text that merely mentions
      ``dj-view`` (e.g. the "do not add another dj-view here" note in the
      sticky example template) does not match.
    - Internal djust classes are skipped unless their module is a test/example
      module (mirrors ``check_liveviews``).
    - Conservatively skipped (no false positives): views with no resolvable
      template, unresolvable ``template_name``, and any loader/IO failure.

    Suppress with ``DJUST_CONFIG = {'suppress_checks': ['V012']}``.
    """
    errors: list[CheckMessage] = []

    if _is_check_suppressed("djust.V012"):
        return errors

    try:
        from djust.live_view import LiveView
    except ImportError:
        return errors

    for cls in sorted(
        _walk_subclasses(LiveView),
        key=lambda c: (getattr(c, "__module__", ""), getattr(c, "__qualname__", "")),
    ):
        # Only sticky children can hit this footgun — the wrapper that supplies
        # the duplicate dj-view is only emitted for sticky embeds.
        if not getattr(cls, "sticky", False):
            continue

        module = getattr(cls, "__module__", "") or ""
        if module.startswith("djust.") or module.startswith("djust_"):
            # Skip internal djust classes — only check user classes (but still
            # check classes in djust's own tests/examples).
            if "test" not in module and "example" not in module:
                continue

        source = _sticky_child_template_source(cls)
        if not source:
            continue

        # Strip comment regions first: a sticky-child template commonly
        # documents the wrapper it lives inside (which itself shows a
        # ``<div dj-view ...>`` example), and that example must not false-match.
        if not _DJ_VIEW_TAG_RE.search(_strip_template_comments(source)):
            continue

        cls_label = "%s.%s" % (cls.__module__, cls.__qualname__)
        cls_file = ""
        cls_line = None
        try:
            cls_file = inspect.getfile(cls)
            cls_line = inspect.getsourcelines(cls)[1]
        except (OSError, TypeError):
            pass  # Source introspection may fail for some classes.

        errors.append(
            DjustWarning(
                "%s: sticky child template declares its own 'dj-view' on its root "
                "— this nests a duplicate dj-view inside the sticky wrapper and "
                "breaks the child's client-side mount (its events won't bind)." % cls_label,
                hint=(
                    "A sticky child is embedded via {% live_render ... sticky=True %}, "
                    "which makes the framework emit the wrapper element itself: "
                    '<div dj-view dj-sticky-view="<id>" dj-sticky-root ...>. That '
                    "wrapper already provides the dj-view binding, so the child's "
                    "template must NOT declare its own dj-view (unlike a normal page "
                    "view, which requires dj-view on its root). Remove the dj-view "
                    "attribute from this sticky child's root element. Suppress with "
                    "DJUST_CONFIG = {'suppress_checks': ['V012']} if you have a "
                    "deliberate reason."
                ),
                id="djust.V012",
                fix_hint=(
                    "Remove the `dj-view` attribute from the root element of `%s`'s "
                    "template — the {%% live_render ... sticky=True %%} wrapper provides "
                    "it." % cls.__qualname__
                ),
                file_path=cls_file,
                line_number=cls_line,
            )
        )

    return errors


def _check_service_instances_in_mount(errors: list[CheckMessage]) -> None:
    """V006 (Warning): Detect service/client/session instantiation in mount() methods via AST.

    High-confidence subset of V008. Fires for names matching _SERVICE_INSTANCE_KEYWORDS
    (Service, Client, Session, API, Connection). Because V006 already emits a Warning for
    these patterns, V008 explicitly skips them so developers see only one message per line.
    """
    app_dirs = _root._get_project_app_dirs()  # type: ignore[attr-defined]  # _root.* is dynamic re-export (patch-by-path; #1822 split)
    if not app_dirs:
        return

    for filepath in _iter_python_files(app_dirs):
        tree, source_lines = _parse_python_file(filepath)
        if tree is None:
            continue

        relpath = os.path.relpath(filepath)

        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue

            # Find mount() methods inside class definitions
            for item in node.body:
                if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if item.name != "mount":
                    continue

                # Walk the mount body looking for self.X = SomeService(...)
                for stmt in ast.walk(item):
                    if not isinstance(stmt, ast.Assign):
                        continue
                    for target in stmt.targets:
                        if not isinstance(target, ast.Attribute):
                            continue
                        if not (isinstance(target.value, ast.Name) and target.value.id == "self"):
                            continue
                        # Check if the value is a Call whose function name
                        # contains service-like keywords
                        if not isinstance(stmt.value, ast.Call):
                            continue
                        call_name = _get_call_name(stmt.value)
                        if call_name and _SERVICE_INSTANCE_KEYWORDS.search(call_name):
                            if not _has_noqa(source_lines, stmt.lineno, "V006"):
                                errors.append(
                                    DjustWarning(
                                        "%s:%d -- Service instance '%s' assigned in mount(). "
                                        "Service instances cannot be serialized."
                                        % (relpath, stmt.lineno, target.attr),
                                        hint=(
                                            "Use a helper method pattern instead. "
                                            "See: docs/guides/services.md"
                                        ),
                                        id="djust.V006",
                                        fix_hint=(
                                            "Move `self.%s = %s(...)` out of mount() into a "
                                            "helper method or property at line %d in `%s`."
                                            % (target.attr, call_name, stmt.lineno, relpath)
                                        ),
                                        file_path=filepath,
                                        line_number=stmt.lineno,
                                    )
                                )


def _check_non_primitive_assignments_in_mount(errors: list[CheckMessage]) -> None:
    """V008: Detect assignments of non-primitive types in mount() methods via AST.

    This is a broader, lower-confidence check than V006. V006 covers a specific
    well-known pattern (service/client/session names → Warning); V008 catches
    *all* non-primitive call results that V006 doesn't already flag (→ Info).

    The two checks are deliberately non-overlapping:
    - Assignments whose call name matches _SERVICE_INSTANCE_KEYWORDS are left
      to V006 (Warning), so developers see one message, not two.
    - Everything else that is not a primitive literal is reported by V008 (Info)
      because it *might* be serializable (e.g. a dataclass) but needs annotation.

    Catches assignments like:
    - self.items = []  (OK - primitive)
    - self.data = CustomClass()  (V008 Info - check serialisability)
    - self.service = PaymentService()  (V006 Warning - skipped here)
    - self.count = 0  (OK - primitive)

    Related to issue #292: Silent str() fallback when non-serializable objects
    are stored in LiveView state. This check helps catch these at development time
    before they cause runtime AttributeError on deserialization.

    Users can suppress with # noqa: V008 if they know the type is serializable,
    or globally with DJUST_CONFIG = {'suppress_checks': ['V008']}.
    """
    if _is_check_suppressed("djust.V008"):
        return

    app_dirs = _root._get_project_app_dirs()  # type: ignore[attr-defined]  # _root.* is dynamic re-export (patch-by-path; #1822 split)
    if not app_dirs:
        return

    # Primitive type constructors AND stdlib builtins that always return
    # JSON-serializable primitives. The check fires only when the bare
    # call name is NOT in this set.
    SAFE_TYPES = {
        # Container/collection constructors. Element JSON-serializability is
        # the user's responsibility — same trust contract for every entry here.
        "list",
        "dict",
        "set",
        "tuple",
        "frozenset",
        "List",
        "Dict",
        "Set",
        "Tuple",
        # Scalar primitive constructors.
        "str",
        "int",
        "float",
        "bool",
        "bytes",
        # Stdlib builtins that always return JSON-serializable primitives (#1609).
        # Numeric → int/float/tuple-of-ints:
        "max",
        "min",
        "sum",
        "abs",
        "round",
        "pow",
        "divmod",
        "len",
        "ord",
        "hash",
        "id",
        # Conversion → str:
        "bin",
        "oct",
        "hex",
        "repr",
        "chr",
        "ascii",
        "format",
        # Container builtin returning list. Same element-serializability
        # contract as `list()` above.
        "sorted",
        # Iterator-returning builtins (reversed, enumerate, zip, map, filter,
        # range, iter) are INTENTIONALLY EXCLUDED — they return iterator/
        # generator objects that are not directly JSON-serializable when
        # stored on a view; the user must materialize via `list()` first.
        # `complex` and `slice` are also excluded — not JSON-serializable.
        #
        # Stdlib module functions that return JSON-serializable primitives
        # (#1628). `_get_call_name` returns the dotted qualified name for
        # `mod.fn(...)` call sites, so the entries here match the qualified
        # form (e.g. `inspect.getsource`, NOT bare `getsource`).
        "inspect.getsource",
        "inspect.getsourcefile",
        "inspect.getmodule",
        "inspect.getdoc",
        "os.path.join",
        "os.path.basename",
        "os.path.dirname",
        "os.path.exists",
        "os.path.isfile",
        "os.path.isdir",
        "os.path.abspath",
        "os.path.relpath",
        "os.getenv",
        "os.getcwd",
        "pathlib.Path.read_text",
        "pathlib.Path.exists",
        "pathlib.Path.is_file",
        "pathlib.Path.is_dir",
        "json.dumps",
        "datetime.datetime.isoformat",
        "datetime.date.isoformat",
        # Bare method names matching chained-call forms like
        # `Path(p).read_text()` and `datetime.now().isoformat()` — these
        # resolve to BARE method names because the receiver is a Call
        # node (not an Attribute chain `_get_call_name` can walk). Only
        # methods that are distinctive enough to make user-code
        # collisions rare are included; `exists`/`is_file`/`is_dir` are
        # intentionally omitted because user code commonly uses those
        # names (e.g. `some_record.exists()`).
        "isoformat",
        "read_text",
    }

    for filepath in _iter_python_files(app_dirs):
        tree, source_lines = _parse_python_file(filepath)
        if tree is None:
            continue

        relpath = os.path.relpath(filepath)

        # Build a set of module-level function names whose return annotation is a
        # primitive type.  Calls to these functions are safe to assign in mount().
        primitive_return_funcs = _build_primitive_return_funcs(tree)

        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue

            # Find mount() methods inside class definitions
            for item in node.body:
                if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if item.name != "mount":
                    continue

                # Walk the mount body looking for self.X = NonPrimitive(...)
                for stmt in ast.walk(item):
                    if not isinstance(stmt, ast.Assign):
                        continue
                    for target in stmt.targets:
                        if not isinstance(target, ast.Attribute):
                            continue
                        if not (isinstance(target.value, ast.Name) and target.value.id == "self"):
                            continue

                        # Skip private attributes (self._foo)
                        if target.attr.startswith("_"):
                            continue

                        # Check if the value is a Call (instantiation or function call)
                        if not isinstance(stmt.value, ast.Call):
                            continue

                        call_name = _get_call_name(stmt.value)
                        if call_name and call_name not in SAFE_TYPES:
                            # Skip patterns already reported by V006 (Warning) to
                            # avoid emitting a duplicate V008 (Info) for the same line.
                            if _SERVICE_INSTANCE_KEYWORDS.search(call_name):
                                continue
                            # Skip calls to module-level functions whose return
                            # annotation declares a primitive type (e.g. -> str).
                            if call_name in primitive_return_funcs:
                                continue
                            # This is a non-primitive instantiation
                            if not _has_noqa(source_lines, stmt.lineno, "V008"):
                                errors.append(
                                    DjustInfo(
                                        "%s:%d -- Non-primitive type '%s' assigned to self.%s in mount(). "
                                        "Ensure this type is JSON-serializable."
                                        % (relpath, stmt.lineno, call_name, target.attr),
                                        hint=(
                                            "If '%s' is not serializable, use self._%s instead "
                                            "or re-initialize in event handlers. "
                                            "See: docs/guides/services.md"
                                            % (call_name, target.attr)
                                        ),
                                        id="djust.V008",
                                        fix_hint=(
                                            "If `%s` is not serializable, rename to `self._%s` "
                                            "or move initialization out of mount() at line %d in `%s`."
                                            % (target.attr, target.attr, stmt.lineno, relpath)
                                        ),
                                        file_path=filepath,
                                        line_number=stmt.lineno,
                                    )
                                )


def _get_call_name(call_node: ast.Call) -> Optional[str]:
    """Extract a human-readable name from a Call node's function."""
    func = call_node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        # e.g., boto3.client -> "boto3.client"
        parts: list[str] = []
        current: ast.expr = func
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
        return ".".join(reversed(parts))
    return None


_PRIMITIVE_ANNOTATION_NAMES = frozenset(
    {
        "str",
        "int",
        "bool",
        "float",
        "bytes",
        "list",
        "dict",
        "set",
        "tuple",
        "List",
        "Dict",
        "Set",
        "Tuple",
    }
)


def _build_primitive_return_funcs(tree: ast.Module) -> set[str]:
    """Return the set of top-level function names whose return annotation is a primitive type.

    Only inspects module-level (top-level) function definitions.  If a function
    is annotated with ``-> str``, ``-> int``, ``-> bool``, ``-> float``,
    ``-> bytes``, or any of the collection primitives (``list``, ``dict``,
    ``set``, ``tuple`` and their capitalised aliases), its name is included in
    the returned set.

    This is used by the V008 check to avoid false-positive warnings when
    ``mount()`` assigns the result of a helper function that is provably
    primitive because of its return-type annotation.
    """
    safe_funcs = set()
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.returns is None:
            continue
        annotation = node.returns
        ann_name = None
        if isinstance(annotation, ast.Name):
            ann_name = annotation.id
        elif isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
            # PEP 563 / ``from __future__ import annotations`` stringifies all annotations
            ann_name = annotation.value
        if ann_name in _PRIMITIVE_ANNOTATION_NAMES:
            safe_funcs.add(node.name)
    return safe_funcs
