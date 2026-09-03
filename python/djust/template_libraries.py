"""``{% load app_tags %}`` — import the Django template library, bridge it (#2547).

Until this module the Rust parser's ``load`` arm kept the library names only
so template inheritance could re-emit the tag; nothing imported them. A
project's ``templatetags/`` modules were invisible to the Rust engine unless
re-registered by hand through ``register_tag_handler`` /
``register_django_filter``. Django's ``template_tests/test_custom.py`` is
entirely this behaviour.

How it works
------------
The parser calls :func:`load_libraries` (installed through
``_rust.register_library_loader``) with the tag's arguments, at PARSE time,
for every parse — the primary template, an ``{% include %}``d file, a
``{% load %}`` inside a ``{% block %}`` of an extended template. The loader
resolves each name the way Django's ``load`` tag does
(``defaulttags.find_library`` over the engine's library map, or
``load_from_library`` for ``{% load x from lib %}``), imports the library
with Django's own ``import_library``, and bridges every entry:

* **filters** through ``template_filters.register_django_filter`` (the #1121
  bridge; ``is_safe`` / ``needs_autoescape`` read off the callable, and the
  #2548 input-gated ``is_safe`` rule applies unchanged);
* **tags** through ONE generic handler per tag that calls the library's OWN
  compile function on a synthetic ``Parser`` / ``Token`` and renders the
  Django node it returns. djust never re-implements ``parse_bits``,
  ``SimpleNode``, ``SimpleBlockNode`` or ``InclusionNode`` — Django's code
  resolves the arguments, applies ``conditional_escape``, renders the body,
  pushes the inclusion context. Byte-equal by construction.

Two contract points the handler relies on (the Rust side of #2547):

* ``RETURNS_BINDINGS = True`` — ``render`` returns ``(output, bindings)``
  where ``bindings`` is the diff of ``ctx.dicts[-1]`` after ``node.render``.
  That is how ``{% one_param 37 as out %}``, ``{% div as d %}…{% enddiv %}``
  and every ``get_* … as x`` tag write the context: Django's node writes
  it, the diff carries it across, the renderer binds it for the siblings
  that follow. And a Python exception the handler lets through crosses
  WHOLE (``DjangoRustError::PythonException``), so Django's
  ``TemplateSyntaxError`` from ``parse_bits`` and a library's own
  ``RuntimeError`` reach the caller with their type.
* ``RESOLVE_ARG_POSITIONS = frozenset()`` — the handler receives the tag's
  operands as TOKENS (``Token.split_contents()[1:]``), never pre-resolved
  values, because Django's node resolves them itself.

Every ``node.render()`` return crosses back ``mark_safe``'d. Django never
re-escapes a node's output: ``SimpleNode.render`` has already applied
``conditional_escape`` to a ``simple_tag``'s return, a ``TextNode``'s bytes
are author content, an ``InclusionNode``'s output is a rendered template.
The registry's ``escape_handler_return`` (#2379) would otherwise escape a
plain-``str`` node result a second time. This grant is Django's own stance
and does NOT extend to anything data-derived — the node's inputs were
resolved and escaped by Django's machinery inside the node.

What is refused, loudly
-----------------------
A raw ``@register.tag`` compile function that CONSUMES A BODY
(``parser.parse((...))``, ``next_token``, ``skip_past``) cannot be bridged:
the synthetic parser has no token stream to hand it. Such a tag is refused
PER TAG, at parse time, the moment a template uses it — a
``TemplateSyntaxError`` naming the tag and the library and pointing at
#2558, which adds the raw-body registration kind — while the rest of its
library (every other tag, every filter) bridges normally. A silent partial
bridge would be worse than an error. A raw tag that builds a node from its
own token (``echo``, ``counter``) bridges fine.

Scoping
-------
Django scopes a loaded library to the template that loaded it; djust's tag
and filter registries are process-global (ADR-001), so once any parse loads
a library every later parse sees its tags, ``{% load %}`` or not — the same
divergence ``{% url %}`` and every ``register_tag_handler`` user already
has. A MISSING library is still refused at parse time with Django's exact
message. Django's own libraries (``i18n``, ``l10n``, ``tz``, ``cache``,
``static``) resolve but are not bridged here — they are separate rows.

Engine paths (#1051)
--------------------
Both the plain backend (``DjustTemplateBackend`` → ``DjustTemplate.render``
→ ``render_template_with_dirs``) and the LiveView entry (``RustLiveView``)
parse through the one ``Template::new``, so both fire the loader. The
registration is process-global, so a library loaded by either path is
visible to the other. The backend an ``inclusion_tag`` renders its template
through travels in a ``ContextVar`` set by ``DjustTemplate.render`` — NOT
in the context dict, which is the #2516 segfault class — and the LiveView
entry, which never sets it, falls back to the project's djust backend.
"""

from __future__ import annotations

import contextlib
import contextvars
import inspect
import logging
import threading
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: Libraries that resolve but are NOT bridged: djust's own
#: ``djust.templatetags.*`` — the Rust engine already has native handlers
#: for their tags (``live_render``, ``dj_flash``, …), and ``live_tags``
#: carries a raw block tag (``colocated_hook``) that the loud refusal would
#: otherwise turn into a parse error for every sticky-child template
#: (measured: 15 suite failures).
_UNBRIDGED_PREFIXES = ("djust.templatetags.",)

#: Django's own libraries this row bridges (#2558). Every OTHER
#: ``django.templatetags.*`` (``static``, ``cache``, …) still resolves and
#: parses exactly as #2547 left it — those rows have not shipped.
_DJANGO_LIBRARIES_BRIDGED = frozenset(
    {
        "django.templatetags.i18n",
        "django.templatetags.l10n",
        "django.templatetags.tz",
    }
)

#: Tags of the bridged Django libraries whose body must keep rendering in
#: Rust — native parser scope nodes (#2558, §4 of the plan). Skipped at
#: bridge time so a ``{% load i18n %}`` never installs a Python handler
#: over the native node; the parser arms are gated on the loader having
#: armed the names (``_rust.arm_scope_tags``).
_NATIVE_SCOPE_TAGS = {
    "django.templatetags.i18n": ("language",),
    "django.templatetags.l10n": ("localize",),
    "django.templatetags.tz": ("localtime", "timezone"),
}

#: The raw block-consuming tags (#2558, §2 of the plan): the body is DATA —
#: the msgid Django's ``render_token_list`` builds from the SOURCE tokens —
#: and crosses to Django un-rendered through ``LibraryRawBlockTagHandler``.
_RAW_BLOCK_TAGS = frozenset({"blocktranslate", "blocktrans"})

#: The ``tz`` FILTERS that need a datetime OBJECT on the wire (#2216: the
#: Rust ``Value`` has no date variant, so a datetime arrives as its ISO
#: string and Django's ``do_timezone`` answers ``""`` for a non-datetime —
#: a silent blank). Refused by name at bridge time with a filter that
#: raises: loud, not blank. The two ``l10n`` filters (``localize`` /
#: ``unlocalize``) work on numbers and bridge normally.
_TZ_FILTER_REFUSALS = frozenset({"localtime", "utc", "timezone"})

#: ``Engine.default_builtins`` — the Rust natives. Never bridged as
#: ``OPTIONS['builtins']`` entries.
_DJANGO_DEFAULT_BUILTINS = frozenset(
    {
        "django.template.defaulttags",
        "django.template.defaultfilters",
        "django.template.loader_tags",
    }
)

#: Attribute stamped on an exception that came out of a bridged library (the
#: library's own code, Django's ``parse_bits``, or this loader), so
#: ``DjustTemplate.render`` passes it through WHOLE instead of re-wrapping it
#: in a bare ``Exception`` with an engine hint that would be wrong for it.
_RAISED_BY_LIBRARY = "_djust_raised_by_library"

#: The backend the CURRENT ``DjustTemplate.render`` is rendering through.
_current_backend: contextvars.ContextVar[Any] = contextvars.ContextVar(
    "djust_template_backend", default=None
)

_lock = threading.RLock()

#: ``OPTIONS['libraries']`` from every ``DjustTemplateBackend`` constructed in
#: this process (label → dotted path). Not cleared by :func:`reset` — the
#: backends that registered them outlive any test.
_extra_libraries: Dict[str, str] = {}

#: ``get_installed_libraries()``, computed once per process (module name →
#: dotted path). Dropped by :func:`reset`.
_installed_cache: Optional[Dict[str, str]] = None

#: Tag name → (library label, handler) for every handler this module owns.
#: The collision policy consults it: a name owned here may be re-registered
#: (last load wins); a name registered by djust itself or by a project's
#: ``register_tag_handler`` is never overridden. :func:`reassert` re-registers
#: exactly these.
_owned_tags: Dict[str, Tuple[str, Any]] = {}

#: Compile function → the handler built for it, so a re-load re-registers
#: the SAME object and a stateful node (Django's own ``CounterNode``) keeps
#: its state across parses, as Django's cached loader keeps it across
#: ``{% include %}``s.
_handlers: Dict[Any, "LibraryTagHandler"] = {}

#: Django's three default builtins as ``Library`` objects, imported once.
_default_builtins: Optional[List[Any]] = None

#: The libraries loaded so far, in load order (label → ``Library``), added to
#: every synthetic parser so a filter from one library resolves inside a tag
#: argument of another, exactly as ``Parser.add_library`` accumulates them.
_loaded: Dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def raised_by_library(exc: BaseException) -> bool:
    """Did ``exc`` come out of a bridged library tag or this loader?"""
    return bool(getattr(exc, _RAISED_BY_LIBRARY, False))


@contextlib.contextmanager
def rendering_with_backend(backend: Any) -> Iterator[None]:
    """Make ``backend`` the one a bridged ``inclusion_tag`` renders through
    and a ``{% load %}`` resolves against, for the duration of a render."""
    token = _current_backend.set(backend)
    try:
        yield
    finally:
        _current_backend.reset(token)


def install_loader() -> bool:
    """Install :func:`load_libraries` as the parser's ``{% load %}`` hook.

    Idempotent; ``False`` without the Rust extension.
    """
    try:
        from djust._rust import register_library_loader
    except ImportError:
        return False
    register_library_loader(load_libraries)
    return True


def install_translator() -> bool:
    """Install the ``_("…")`` translator hook (#2558).

    The callable receives the %-DOUBLED msgid and returns the string the
    ACTIVE language renders — read per RENDER, on the render thread, which
    is the issue's "no registration-time capture" requirement: gettext
    resolves ``translation.get_language()`` live. ``mark_safe`` on the msgid
    keeps Django's ``SafeData``-preserving ``gettext`` path
    (``trans_real.py:387-388``) — a quoted literal translates to raw bytes
    exactly as ``{{ _("<") }}`` must render ``<``.

    Idempotent; ``False`` without the Rust extension.
    """
    try:
        from djust._rust import register_translator
    except ImportError:
        return False
    from django.utils import translation
    from django.utils.safestring import mark_safe

    def translate(msgid: str) -> str:
        return str(translation.gettext(mark_safe(msgid)))

    register_translator(translate)
    return True


def register_backend_libraries(libraries: Dict[str, str], builtins: List[str]) -> None:
    """A ``DjustTemplateBackend``'s ``OPTIONS['libraries']`` / ``['builtins']``.

    ``libraries`` extend the ``{% load %}`` name map for every render in the
    process (they are process-global like everything else here);
    ``builtins`` are imported and bridged NOW, so their tags and filters
    need no ``{% load %}`` — Django's meaning for both. Django's own three
    default builtins are skipped: they are the Rust natives.
    """
    with _lock:
        _extra_libraries.update(libraries)
        for path in builtins:
            if path in _DJANGO_DEFAULT_BUILTINS:
                continue
            from django.template.library import import_library

            _bridge_library(path, import_library(path))


def load_libraries(args: List[str]) -> None:
    """The ``{% load %}`` hook: Django's ``defaulttags.load``, minus the parser.

    ``args`` are the tag's arguments as written. Mirrors Django's branch
    exactly: ``{% load x y from lib %}`` when there are at least three
    arguments and the penultimate one is ``from`` (Django: ``len(bits) >= 4
    and bits[-2] == "from"`` over ``bits = ["load", *args]``), otherwise
    every argument is a library name — so ``{% load from testtags %}`` and
    ``{% load echo from %}`` fail with Django's own messages.
    """
    from django.template.defaulttags import load_from_library

    bits = ["load", *args]
    try:
        with _lock:
            if len(bits) >= 4 and bits[-2] == "from":
                name = bits[-1]
                library = _find_library(name)
                subset = load_from_library(library, name, bits[1:-2])
                _bridge_library(name, subset)
            else:
                for name in bits[1:]:
                    _bridge_library(name, _find_library(name))
    except BaseException as exc:
        _stamp(exc)
        raise


def reassert() -> None:
    """Re-register every bridged tag with the Rust registries (the #1928
    re-assert pattern; ``djust.test_isolation`` runs it before each test).

    A test that calls ``clear_tag_handlers()`` / ``clear_block_tag_handlers()``
    leaves every ``{% load %}``-bridged tag gone for the rest of the worker —
    and the Rust ``TEMPLATE_CACHE`` is keyed by SOURCE, so a template parsed
    while the tags were registered is served from cache with ``CustomTag``
    nodes that no longer resolve, and its ``{% load %}`` never runs again.
    Re-asserting the SAME handler objects restores every cached template.
    Also drops the installed-library cache so a newly installed app is seen.
    Nothing is unregistered: the registries are process-global by design.
    """
    global _installed_cache
    with _lock:
        _installed_cache = None
        try:
            from djust._rust import (
                register_block_tag_handler,
                register_raw_block_tag_handler,
                register_tag_handler,
                unregister_block_tag_handler,
                unregister_tag_handler,
            )
        except ImportError:
            return
        for name, (_label, handler) in list(_owned_tags.items()):
            if isinstance(handler, LibraryRawBlockTagHandler):
                register_raw_block_tag_handler(name, handler.end_name, handler)
                unregister_tag_handler(name)
                unregister_block_tag_handler(name)
            elif isinstance(handler, LibraryBlockTagHandler):
                register_block_tag_handler(name, handler.end_name, handler)
                unregister_tag_handler(name)
            else:
                register_tag_handler(name, handler)
                unregister_block_tag_handler(name)


def owned_tags() -> Dict[str, str]:
    """Tag name → library label for every tag this module registered."""
    return {name: label for name, (label, _handler) in _owned_tags.items()}


# ---------------------------------------------------------------------------
# Resolution: which libraries does ``{% load %}`` know?
# ---------------------------------------------------------------------------


def _library_map() -> Dict[str, Any]:
    """Label → dotted path or ``Library``, the way Django's ``Engine`` sees it.

    A real ``django.template.engine.Engine`` as the current backend (the
    #2517 scoreboard adapter) contributes EXACTLY its ``template_libraries``
    — Django parity, including the sorted "Must be one of" list. Otherwise
    (``DjustTemplateBackend``, the LiveView entry) the map is what
    ``DjangoTemplates`` builds: ``get_installed_libraries()`` plus every
    backend's ``OPTIONS['libraries']``.
    """
    global _installed_cache
    backend = _current_backend.get()
    try:
        from django.template.engine import Engine
    except ImportError:  # pragma: no cover — Django is a hard dependency
        Engine = ()  # type: ignore[assignment]
    if isinstance(backend, Engine):
        return dict(backend.template_libraries)
    if _installed_cache is None:
        from django.template.backends.django import get_installed_libraries

        _installed_cache = dict(get_installed_libraries())
    known: Dict[str, Any] = dict(_installed_cache)
    known.update(_extra_libraries)
    if backend is not None:
        known.update(getattr(backend, "template_libraries", None) or {})
    return known


def _find_library(name: str) -> Any:
    """Django's ``defaulttags.find_library``, over :func:`_library_map`."""
    from django.template import TemplateSyntaxError
    from django.template.library import Library, import_library

    known = _library_map()
    try:
        entry = known[name]
    except KeyError:
        raise TemplateSyntaxError(
            "'%s' is not a registered tag library. Must be one of:\n%s"
            % (name, "\n".join(sorted(known)))
        ) from None
    if isinstance(entry, Library):
        return entry
    # ``InvalidTemplateLibrary`` crosses whole, as it does from
    # ``Engine.__init__`` on Django.
    return import_library(entry)


# ---------------------------------------------------------------------------
# Bridging: one library → the Rust registries
# ---------------------------------------------------------------------------


def _library_module(library: Any) -> str:
    """The module a ``Library``'s entries were defined in (``""`` if empty)."""
    for mapping in (getattr(library, "tags", {}), getattr(library, "filters", {})):
        for fn in mapping.values():
            module = getattr(fn, "__module__", None)
            if module:
                return str(module)
    return ""


def _bridge_library(label: str, library: Any) -> None:
    """Register every filter and tag of ``library`` with the Rust engine."""
    module = _library_module(library)
    if module.startswith(_UNBRIDGED_PREFIXES):
        return
    if module.startswith("django.templatetags.") and module not in _DJANGO_LIBRARIES_BRIDGED:
        # ``{% load static %}`` resolves and parses as it did before this
        # module existed; Django's other libraries are still separate rows.
        return
    from .template_filters import bridge_library_filters

    _arm_scope_tags(module)
    refuse = _TZ_FILTER_REFUSALS if module == "django.templatetags.tz" else frozenset()
    bridge_library_filters(library, refuse=refuse)
    native = _NATIVE_SCOPE_TAGS.get(module, ())
    for name, compile_func in library.tags.items():
        if name in _RAW_BLOCK_TAGS:
            _bridge_raw_block_tag(label, name, compile_func)
        elif name in native:
            continue
        else:
            _bridge_tag(label, name, compile_func)
    _loaded[label] = library


def _arm_scope_tags(module: str) -> None:
    """Arm the parser's native scope nodes for a bridged library (#2558)."""
    names = _NATIVE_SCOPE_TAGS.get(module)
    if not names:
        return
    try:
        from djust._rust import arm_scope_tags
    except ImportError:
        logger.warning("djust._rust extension not available; scope tags stay unarmed")
        return
    arm_scope_tags(list(names))


def _bridge_raw_block_tag(label: str, name: str, compile_func: Callable[..., Any]) -> None:
    """Register a raw block-consuming tag through the raw-body kind (#2558).

    The collision policy is the SAME ``_may_override`` rule the inline
    registry applies — a name djust itself owns is never displaced.

    ``blocktranslate`` and ``blocktrans`` share ONE compile function, so the
    ``_handlers`` dedup the inline path uses would give both names the FIRST
    handler's ``end_name`` — ``{% blocktranslate %}`` hunting for
    ``{% endblocktrans %}`` (measured in this row's first pass). A
    raw-block handler is per-NAME by construction: one is minted per name
    even when the compile function is shared.
    """
    if not _may_override(name):
        logger.warning(
            "Template library '%s' registers tag '%s', which djust already provides; "
            "the library's version is not bridged (the built-in wins process-wide).",
            label,
            name,
        )
        return
    try:
        from djust._rust import register_raw_block_tag_handler, unregister_tag_handler
    except ImportError:
        logger.warning(
            "djust._rust extension not available; tag '%s' from '%s' will not work "
            "in the Rust engine",
            name,
            label,
        )
        return
    handler = _handlers.get(compile_func)
    if not isinstance(handler, LibraryRawBlockTagHandler) or handler.end_name != "end" + name:
        handler = LibraryRawBlockTagHandler(label, name, compile_func)
        _handlers[compile_func] = handler
    register_raw_block_tag_handler(name, handler.end_name, handler)
    unregister_tag_handler(name)
    _owned_tags[name] = (label, handler)


def _bridge_tag(label: str, name: str, compile_func: Callable[..., Any]) -> None:
    kind = _classify(compile_func)
    if not _may_override(name):
        logger.warning(
            "Template library '%s' registers tag '%s', which djust already provides; "
            "the library's version is not bridged (the built-in wins process-wide).",
            label,
            name,
        )
        return
    try:
        from djust._rust import (
            register_block_tag_handler,
            register_tag_handler,
            unregister_block_tag_handler,
            unregister_tag_handler,
        )
    except ImportError:
        logger.warning(
            "djust._rust extension not available; tag '%s' from '%s' will not work "
            "in the Rust engine",
            name,
            label,
        )
        return
    handler = _handlers.get(compile_func)
    if kind == "simple_block_tag":
        # Per-NAME, not per-compile-function (#2558): decorator aliases
        # (`@register.simple_block_tag` + `@register.tag("other")`) share one
        # compile function but each name needs its own end tag/token, exactly
        # as the raw-block kind below needs its own end_name.
        if not isinstance(handler, LibraryBlockTagHandler) or handler.name != name:
            handler = LibraryBlockTagHandler(label, name, compile_func)
            _handlers[compile_func] = handler
        register_block_tag_handler(name, handler.end_name, handler)
        unregister_tag_handler(name)
    elif kind == "raw" and _consumes_body(compile_func):
        # Refused per TAG, at parse time, the moment a template uses it —
        # the rest of the library bridges normally. The Rust parser reads
        # `REFUSE_AT_PARSE` and raises Django's `TemplateSyntaxError`.
        if not isinstance(handler, RefusedTagHandler):
            handler = RefusedTagHandler(label, name)
            _handlers[compile_func] = handler
        register_tag_handler(name, handler)
        unregister_block_tag_handler(name)
    else:
        # Per-NAME, not per-compile-function (#2558): `translate`/`trans` are
        # one compile function registered under two names, and Django's
        # `do_translate` quotes `bits[0]` — the ACTUAL tag spelling — in
        # every syntax error. A handler shared across names raised
        # "'trans' takes at least one argument" for `{% translate %}` (6
        # suite cells). The per-(name, args) node cache inside the handler
        # already keys on the name, so this widens nothing.
        if handler is None or isinstance(handler, LibraryBlockTagHandler) or handler.name != name:
            handler = LibraryTagHandler(label, name, compile_func)
            _handlers[compile_func] = handler
        register_tag_handler(name, handler)
        unregister_block_tag_handler(name)
    _owned_tags[name] = (label, handler)


def _may_override(name: str) -> bool:
    """Collision policy: never displace a handler this module does not own."""
    if name in _owned_tags:
        return True
    try:
        from djust._rust import has_assign_tag_handler, has_block_tag_handler, has_tag_handler
    except ImportError:
        return True
    return not (
        has_tag_handler(name) or has_block_tag_handler(name) or has_assign_tag_handler(name)
    )


#: The ``__code__.co_qualname`` of Django's three decorator-made compile
#: functions. ``@wraps(func)`` copies the USER function's ``__qualname__``
#: onto ``compile_func`` (so ``compile_func.__qualname__`` is useless), but
#: the code object's qualname is the closure's own and survives.
_KIND_BY_CODE_QUALNAME = {
    "Library.simple_tag.<locals>.dec.<locals>.compile_func": "simple_tag",
    "Library.simple_block_tag.<locals>.dec.<locals>.compile_func": "simple_block_tag",
    "Library.inclusion_tag.<locals>.dec.<locals>.compile_func": "inclusion_tag",
}


def _classify(compile_func: Callable[..., Any]) -> str:
    """``simple_tag`` / ``simple_block_tag`` / ``inclusion_tag`` / ``raw``.

    Reads the compile function's code-object qualname (Python ≥ 3.11); on
    an older interpreter falls back to the closure's free variables
    (``end_name`` only exists in ``simple_block_tag``'s, ``filename`` only
    in ``inclusion_tag``'s). ``test_load_imports_django_libraries_2547`` pins
    both against the installed Django so a rename fails loudly rather than
    silently reclassifying every tag as raw.
    """
    code = getattr(compile_func, "__code__", None)
    if code is None:
        return "raw"
    qualname = getattr(code, "co_qualname", None)
    if qualname is not None:
        return _KIND_BY_CODE_QUALNAME.get(qualname, "raw")
    if code.co_name != "compile_func" or not getattr(compile_func, "__closure__", None):
        return "raw"
    free = set(code.co_freevars)
    if "end_name" in free:
        return "simple_block_tag"
    if "filename" in free:
        return "inclusion_tag"
    if "takes_context" in free and "params" in free:
        return "simple_tag"
    return "raw"


#: Anything in a raw compile function's source that reads the token stream.
_BODY_CONSUMERS = ("parser.parse(", ".parse((", "next_token(", "skip_past(", "parse_until")


def _consumes_body(compile_func: Callable[..., Any]) -> bool:
    """Does a raw compile function read past its own token?"""
    try:
        source = inspect.getsource(compile_func)
    except (OSError, TypeError):
        return False
    return any(marker in source for marker in _BODY_CONSUMERS)


def _end_name(compile_func: Callable[..., Any], name: str) -> str:
    """``simple_block_tag``'s end tag: the ``end_name`` its closure carries."""
    try:
        value = inspect.getclosurevars(compile_func).nonlocals.get("end_name")
    except (TypeError, ValueError):
        value = None
    return str(value) if value else f"end{name}"


def _stamp(exc: BaseException) -> None:
    try:
        setattr(exc, _RAISED_BY_LIBRARY, True)
    except Exception:  # noqa: BLE001 — an exception type with __slots__; nothing to do
        logger.debug("Could not stamp %r as library-raised", exc)


# ---------------------------------------------------------------------------
# The generic handlers
# ---------------------------------------------------------------------------


def _builtin_libraries() -> List[Any]:
    global _default_builtins
    if _default_builtins is None:
        from django.template.engine import Engine
        from django.template.library import import_library

        _default_builtins = [import_library(path) for path in Engine.default_builtins]
    return _default_builtins


def _parser(tokens: List[Any]) -> Any:
    """A Django ``Parser`` over ``tokens`` that knows the builtins (so
    ``40|add:2`` inside an operand compiles) and every library loaded so
    far (so a cross-library filter inside an operand resolves)."""
    from django.template.base import Parser

    parser = Parser(tokens, libraries=dict(_loaded), builtins=_builtin_libraries())
    for library in _loaded.values():
        parser.add_library(library)
    return parser


def _token(name: str, args: List[str]) -> Any:
    from django.template.base import Token, TokenType

    return Token(TokenType.BLOCK, " ".join([name, *args]))


class _StubEngine:
    """What a Django node reads off ``context.template.engine``.

    ``string_if_invalid`` / ``debug`` for ``FilterExpression.resolve`` and
    ``Variable._resolve_lookup``; ``get_template`` / ``select_template`` for
    ``InclusionNode.render``. A template object handed in by the tag
    (``@register.inclusion_tag(engine.get_template("x.html"))``) is returned
    as it is; a name goes to the backend of the enclosing render, else to
    the project's djust backend, else to Django's loader.
    """

    string_if_invalid = ""
    debug = False
    autoescape = True

    def get_template(self, name: Any) -> Any:
        if hasattr(name, "render"):
            return name
        return _template_backend().get_template(name)

    def select_template(self, names: Any) -> Any:
        return _template_backend().select_template(names)


class _StubTemplate:
    engine = _StubEngine()
    name = "<djust library tag>"
    origin = None


_STUB_TEMPLATE = _StubTemplate()


class _LoaderBackend:
    """Django's ``template.loader`` as a backend-shaped object."""

    def get_template(self, name: str) -> Any:
        from django.template import loader

        return loader.get_template(name)

    def select_template(self, names: Any) -> Any:
        from django.template import loader

        return loader.select_template(names)


def _template_backend() -> Any:
    backend = _current_backend.get()
    if backend is not None:
        return backend
    try:
        from django.template import engines

        from .template.backend import DjustTemplateBackend

        for engine in engines.all():
            if isinstance(engine, DjustTemplateBackend):
                return engine
    except Exception:  # noqa: BLE001 — no configured engines; fall through
        logger.debug("No djust template backend configured; using Django's loader")
    return _LoaderBackend()


def _materialize_lazy(value: Any) -> Any:
    """``str()`` every lazy ``Promise`` reachable in a binding value (#2558).

    Walks dicts and lists; a ``SafeString`` proxy keeps its marker (``str``
    of a ``SafeString`` is a ``SafeString``). Anything that is not a promise
    crosses untouched.
    """
    from django.utils.functional import Promise

    if isinstance(value, Promise):
        return str(value)
    if isinstance(value, dict):
        return {k: _materialize_lazy(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        materialized = [_materialize_lazy(v) for v in value]
        return type(value)(materialized) if isinstance(value, tuple) else materialized
    return value


def _render_node(node: Any, context: Dict[str, Any]) -> Tuple[Any, Dict[str, Any]]:
    """``node.render`` on a Django ``Context`` over ``context``; the output
    ``mark_safe``'d (see the module docstring) and the context writes the
    node made, as the diff of ``ctx.dicts[-1]``."""
    from django.template import Context
    from django.utils.safestring import mark_safe

    before = dict(context)
    ctx = Context(context, autoescape=True)
    ctx.template = _STUB_TEMPLATE
    output = node.render(ctx)
    after = ctx.dicts[-1]
    bindings = {
        key: value for key, value in after.items() if key not in before or before[key] is not value
    }
    # A `gettext_lazy` proxy must cross the boundary as the STRING it
    # renders as (#2558): `get_language_info` binds a dict whose
    # `name_translated` is a lazy proxy, and the Rust `Value` extraction
    # mangled the un-materialized proxy into a list of characters. Only
    # lazy PROMISES are materialized — a list, a dict or a model instance a
    # sibling might consume as an object must cross as itself.
    bindings = {key: _materialize_lazy(value) for key, value in bindings.items()}
    if output is None:
        output = ""
    return mark_safe(str(output)), bindings


class LibraryTagHandler:
    """The generic inline handler: a ``simple_tag``, an ``inclusion_tag``, or
    a raw ``@register.tag`` that builds its node from its own token."""

    RESOLVE_ARG_POSITIONS: frozenset = frozenset()
    RETURNS_BINDINGS = True

    def __init__(self, label: str, name: str, compile_func: Callable[..., Any]) -> None:
        self.label = label
        self.name = name
        self.compile_func = compile_func
        # Compiled once per distinct argument list, like Django compiles a
        # tag once per template. A stateful node therefore keeps its state
        # per process rather than per template — documented divergence.
        self._nodes: Dict[Tuple[str, ...], Any] = {}

    def _compile(self, args: List[str]) -> Any:
        key = tuple(args)
        node = self._nodes.get(key)
        if node is None:
            node = self.compile_func(_parser([]), _token(self.name, args))
            self._nodes[key] = node
        return node

    def render(self, args: List[str], context: Dict[str, Any]) -> Tuple[Any, Dict[str, Any]]:
        try:
            return _render_node(self._compile(args), context)
        except BaseException as exc:
            _stamp(exc)
            raise


class RefusedTagHandler:
    """A raw ``@register.tag`` that consumes a body — registered so the parser
    refuses it LOUDLY, per tag, the moment a template uses it (#2547).

    The Rust parser reads ``REFUSE_AT_PARSE`` at registration and raises
    Django's ``TemplateSyntaxError`` with this message instead of building a
    node; ``render`` is never reached (it exists only because the registry
    requires one).
    """

    RETURNS_BINDINGS = True

    def __init__(self, label: str, name: str) -> None:
        self.label = label
        self.name = name
        self.REFUSE_AT_PARSE = (
            "'%s' from library '%s' is a raw @register.tag that consumes a block "
            "(it calls parser.parse / next_token). djust bridges raw tags that build "
            "a node from their own token only; port it to @register.simple_block_tag, "
            "or wait for the raw-body registration kind (#2558)." % (name, label)
        )

    def render(self, args: List[str], context: Dict[str, Any]) -> Tuple[Any, Dict[str, Any]]:
        from django.template import TemplateSyntaxError

        exc = TemplateSyntaxError(self.REFUSE_AT_PARSE)
        _stamp(exc)
        raise exc


class LibraryBlockTagHandler(LibraryTagHandler):
    """The generic block handler: a ``simple_block_tag``.

    The Rust engine has already rendered the body (it arrives as a
    ``SafeString``, #2379). Django's compile function is handed a parser
    whose token stream is exactly ``[TEXT(body), BLOCK(end_name)]``, so its
    own ``parser.parse((end_name,))`` + ``delete_first_token()`` consume the
    rendered body as one ``TextNode`` — ``nodelist.render`` then yields the
    body, marked safe, as ``content``.
    """

    def __init__(self, label: str, name: str, compile_func: Callable[..., Any]) -> None:
        super().__init__(label, name, compile_func)
        self.end_name = _end_name(compile_func, name)

    def render(  # type: ignore[override]
        self, args: List[str], content: Any, context: Dict[str, Any]
    ) -> Tuple[Any, Dict[str, Any]]:
        from django.template.base import Token, TokenType

        try:
            tokens = [Token(TokenType.TEXT, content), Token(TokenType.BLOCK, self.end_name)]
            node = self.compile_func(_parser(tokens), _token(self.name, args))
            return _render_node(node, context)
        except BaseException as exc:
            _stamp(exc)
            raise


def _stub_template_with(string_if_invalid: str, debug: bool) -> Any:
    """A ``_StubTemplate`` whose engine carries the CURRENT render's
    ``string_if_invalid`` (#2558).

    ``BlockTranslateNode.render`` resolves a MISSING placeholder variable to
    ``context.template.engine.string_if_invalid`` — that is how the suite's
    i18n34 / invalidstr07 cells render ``INVALID`` under the scoreboard's
    second engine. The scoreboard's ``DjustEngine`` IS a real Django
    ``Engine``, so its attribute is read directly; the plain
    ``DjustTemplateBackend`` does not carry one and contributes ``""``.
    """
    engine = _StubEngine()
    engine.string_if_invalid = string_if_invalid
    engine.debug = debug
    template = _StubTemplate()
    template.engine = engine
    return template


class LibraryRawBlockTagHandler:
    """The raw-body block handler (#2558): the body is Django's to parse.

    The Rust parser hands over the body as UN-rendered template source (the
    msgid data ``blocktranslate`` builds its catalog key from). This handler
    re-lexes it with Django's OWN ``Lexer``, appends the end token Django's
    compile function breaks on, and calls the library's OWN compile function
    on a synthetic ``Parser`` — so ``with`` / ``count`` / ``plural`` /
    ``context`` / ``trimmed`` / ``asvar``, both legacy spellings, and all
    seven syntax errors are Django's code with Django's text, byte for byte.
    Compiled once per ``(args, body)`` like Django compiles once per
    template; ``BlockTranslateNode`` is stateless so the cache is sound.

    ``count`` with a non-number raises Django's render-time
    ``TemplateSyntaxError`` from inside the node; it crosses WHOLE through
    the ``RETURNS_BINDINGS`` exception channel (#2547) — parity is the
    point, a LiveView page 500s exactly as Django's does.
    """

    RETURNS_BINDINGS = True

    def __init__(self, label: str, name: str, compile_func: Callable[..., Any]) -> None:
        self.label = label
        self.name = name
        self.compile_func = compile_func
        self.end_name = "end" + name
        self._nodes: Dict[Tuple[Tuple[str, ...], str], Any] = {}

    def _compile(self, args: List[str], body: str) -> Any:
        from django.template.base import Lexer, Token, TokenType

        key = (tuple(args), body)
        node = self._nodes.get(key)
        if node is None:
            tokens = Lexer(body).tokenize()
            tokens.append(Token(TokenType.BLOCK, self.end_name))
            node = self.compile_func(_parser(tokens), _token(self.name, args))
            self._nodes[key] = node
        return node

    def _string_if_invalid(self) -> Tuple[str, bool]:
        backend = _current_backend.get()
        if backend is None:
            return "", False
        return (
            str(getattr(backend, "string_if_invalid", "") or ""),
            bool(getattr(backend, "debug", False)),
        )

    def render(  # type: ignore[override]
        self, args: List[str], body: str, context: Dict[str, Any]
    ) -> Tuple[Any, Dict[str, Any]]:
        from django.template import Context
        from django.utils.safestring import mark_safe

        try:
            before = dict(context)
            # autoescape=True until #2556 wires the engine's flag through
            # (§3 of the #2558 plan); the kwarg below is where it lands.
            ctx = Context(dict(context), autoescape=True)
            string_if_invalid, debug = self._string_if_invalid()
            ctx.template = _stub_template_with(string_if_invalid, debug)
            output = self._compile(list(args), body).render(ctx)
            after = ctx.dicts[-1]
            bindings = {
                key: value
                for key, value in after.items()
                if key not in before or before[key] is not value
            }
            if output is None:
                output = ""
            # Django never re-escapes a node's output (the module docstring):
            # BlockTranslateNode escaped its placeholders INSIDE the node and
            # the `%`-formatted result is final text.
            return mark_safe(str(output)), bindings
        except BaseException as exc:
            _stamp(exc)
            raise


__all__ = [
    "LibraryBlockTagHandler",
    "LibraryRawBlockTagHandler",
    "LibraryTagHandler",
    "RefusedTagHandler",
    "install_loader",
    "install_translator",
    "load_libraries",
    "owned_tags",
    "raised_by_library",
    "register_backend_libraries",
    "reassert",
    "rendering_with_backend",
]
