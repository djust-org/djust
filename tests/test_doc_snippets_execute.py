"""Execute every documented LiveView / LiveComponent snippet, not just parse it.

Extends ``tests/test_readme_snippets_execute.py`` from ``README.md`` to every
fenced ``python`` block under ``docs/website/guides/*.md``, ``QUICKSTART.md``
and ``docs/website/getting-started/*.md``. ``scripts/check-doc-snippets.py``
AST-parses and import-resolves those blocks; it cannot catch a class attribute
no mixin reads, a handler that touches state ``mount()`` never set, a state
variable the inline template never renders, or a ``@event_handler`` that
raises on its documented call.

For each block that defines a ``LiveView`` or ``LiveComponent`` subclass the
test:

1. executes the block as a throwaway module (a ``<!-- doc-snippet-check: skip -->``
   (or ``anti-pattern``) marker before the fence excludes it, as for the checker);
2. for every ``LiveView`` subclass, mounts it through
   ``djust.testing.LiveViewTestClient``, renders it, checks that each mounted
   ``{{ var }}`` the inline template names reaches the HTML, and fires every
   ``@event_handler`` with no arguments; a view that names a ``template_name``
   file the repo does not ship is mounted on a dynamic subclass with a stub
   inline template, so ``mount()`` / ``get_context_data()`` / the handlers
   still run;
3. for every ``LiveComponent`` subclass, mounts it through
   ``LiveComponentTestClient``, renders it and fires its handlers.

Outcomes that are not doc defects are *not* failures and are recorded as
such: a block importing the reader's own app or a library the test env lacks,
a block defining a Django model (no app to own it), a ``mount()`` that needs
URL kwargs, a handler that requires arguments, a ``template_name`` the reader
is told to write.

Blocks that genuinely fail today are listed in ``KNOWN_FAILURES`` below with
the reason, as ``xfail(strict=True)``: a new failure turns the suite red, and
a doc fix that makes a listed block pass also turns it red until the entry is
removed. The inventory those entries came from is
``docs/testing/doc-snippet-execution-inventory.md``.

Set ``DJUST_DOC_SNIPPET_INVENTORY=<path.jsonl>`` to append one JSON record per
block (used to regenerate that inventory).
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import inspect
import json
import os
import pathlib
import re
import sys
import traceback
import types
from dataclasses import dataclass, field

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_CHECKER = REPO_ROOT / "scripts" / "check-doc-snippets.py"

DOC_FILES: list[pathlib.Path] = sorted(
    [
        *REPO_ROOT.glob("docs/website/guides/*.md"),
        REPO_ROOT / "QUICKSTART.md",
        *REPO_ROOT.glob("docs/website/getting-started/*.md"),
        *REPO_ROOT.glob("docs/website/getting-started*.md"),
    ]
)

# Block key (``<path relative to repo>@<first 8 hex of the sha1 of the block's
# code>``) -> reason it fails today. Each entry is a documented example a reader
# cannot run as written. Remove the entry when the doc is fixed; the strict
# xfail flips red until you do.
#
# Keyed by content, not by fence line: guides are edited in almost every PR, and
# a line-number key goes stale whenever anything above the block changes. A docs
# only PR does not run this suite in CI (``paths-ignore`` in test.yml), so a
# stale key would land on main unseen and turn an unrelated code PR red. A
# content key goes stale only when the block itself changes, which is exactly
# when the entry should be looked at again. ``Block.key`` computes it; the
# comment above each entry gives the line it had when it was recorded.
KNOWN_FAILURES: dict[str, str] = {
    # --- phantom attribute: the class reads something it never sets/defines ---
    # was docs/website/guides/declarative-ux-attrs.md line 710
    "docs/website/guides/declarative-ux-attrs.md@f5710695": (
        "ChatView.send calls self._post(value); the block defines no _post (elided helper)"
    ),
    # was docs/website/guides/error-codes.md line 533
    "docs/website/guides/error-codes.md@6bf612fb": (
        "V004 'Fix' block: MyView.toggle_sidebar reads self.sidebar_open, which no mount() sets"
    ),
    # was docs/website/guides/hooks.md line 468
    "docs/website/guides/hooks.md@31c5e48b": (
        "DashboardView.mount calls self.fetch_sales(); the block defines no fetch_sales (elided helper)"
    ),
    # was docs/website/guides/http-api.md line 416
    "docs/website/guides/http-api.md@f2ab03ab": (
        "MyView.mount calls self.subscribe_presence() and self._seed(); neither exists on LiveView "
        "nor in the block (subscribe_presence reads like an API but djust has no such method)"
    ),
    # was docs/website/guides/tutorial-multi-step-wizard.md line 117
    "docs/website/guides/tutorial-multi-step-wizard.md@572ee429": (
        "SignupWizardView.back_step reads self.step; the block says '# ... state as above ...' "
        "and the earlier block's state() declarations are not carried"
    ),
    # --- template contract ---
    # was docs/website/guides/time-travel-debugging.md line 25
    "docs/website/guides/time-travel-debugging.md@bd19ed38": (
        "CounterView.template = '<div>{{ count }}</div>' has no dj-root / dj-view root; "
        "template-requirements.md says the client attaches to one"
    ),
    # --- public-API collision ---
    # was docs/website/guides/tutorial-streaming-ai.md line 78
    "docs/website/guides/tutorial-streaming-ai.md@9c911c22": (
        "`from djust import rate_limit` resolves to the djust.rate_limit MODULE once that submodule "
        "is imported (runtime.py:81 imports it; djust/__init__.py re-binds only live_view), so "
        "`@rate_limit(rate=0.1, burst=3)` raises TypeError: 'module' object is not callable. "
        "Import it from djust.decorators"
    ),
    # --- deliberate ---
    # was docs/website/guides/error-overlay.md line 44
    "docs/website/guides/error-overlay.md@7a3e8f4c": (
        "deliberate: OrderView.charge raises ValueError('amount must be positive') to show what "
        "the overlay displays; fired with no args, amount=0 takes that branch"
    ),
}

_MISSING_ARGS_RE = re.compile(r"missing \d+ required (?:positional|keyword-only) argument")
_UNEXPECTED_KW_RE = re.compile(r"got an unexpected keyword argument")
_NO_ATTR_RE = re.compile(r"'(\w+)' object has no attribute '(\w+)'")
_NETWORK_RE = re.compile(
    r"ConnectError|ConnectionError|nodename nor servname|Name or service not known|Errno 8\b"
)
_DENIED_RE = re.compile(r"^(Permission denied|Access denied)")
_TEMPLATE_MISSING_RE = re.compile(
    r"TemplateDoesNotExist|Template not found|template.*(?:not found|does not exist)",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------
# Block discovery
# --------------------------------------------------------------------------


def _load_extractor():
    spec = importlib.util.spec_from_file_location("check_doc_snippets", _CHECKER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.extract_python_blocks


def _defines_live_class(code: str) -> bool:
    """True when the block has a ``class X(...LiveView...)`` / ``LiveComponent`` def."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                text = ast.unparse(base)
                if "LiveView" in text or "LiveComponent" in text:
                    return True
    return False


@dataclass(frozen=True)
class Block:
    path: pathlib.Path
    lineno: int
    code: str

    @property
    def id(self) -> str:
        return f"{self.path.relative_to(REPO_ROOT)}:L{self.lineno}"

    @property
    def key(self) -> str:
        digest = hashlib.sha1(self.code.encode("utf-8")).hexdigest()[:8]
        return f"{self.path.relative_to(REPO_ROOT).as_posix()}@{digest}"

    @property
    def module_name(self) -> str:
        stem = re.sub(r"[^0-9a-zA-Z]+", "_", str(self.path.relative_to(REPO_ROOT)))
        return f"doc_snippet_{stem}_L{self.lineno}"


def _blocks_in(paths) -> list[Block]:
    extract = _load_extractor()
    blocks: list[Block] = []
    for path in paths:
        if not path.exists():
            continue
        for lineno, code, marker in extract(path):
            # ``<!-- doc-snippet-check: anti-pattern -->`` marks code the guide
            # shows as WRONG; executing it would report the lesson as a defect.
            if marker == "anti-pattern":
                continue
            if _defines_live_class(code):
                blocks.append(Block(path, lineno, code))
    return blocks


def _candidate_blocks() -> list[Block]:
    return _blocks_in(DOC_FILES)


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------


@dataclass
class Outcome:
    """What happened to one block. ``status`` is ``pass`` / ``fail`` / ``skip``."""

    block: str
    status: str
    category: str
    classes: list[str] = field(default_factory=list)
    detail: str = ""
    notes: list[str] = field(default_factory=list)

    def as_record(self) -> dict:
        return {
            "block": self.block,
            "status": self.status,
            "category": self.category,
            "classes": self.classes,
            "detail": self.detail,
            "notes": self.notes,
        }


class _Skip(Exception):
    """Raised inside the harness for a non-defect outcome."""

    def __init__(self, category: str, detail: str):
        super().__init__(detail)
        self.category = category
        self.detail = detail


class _Fail(Exception):
    """Raised inside the harness for a doc defect."""

    def __init__(self, category: str, detail: str):
        super().__init__(detail)
        self.category = category
        self.detail = detail


def _exc_text(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}"
    return " ".join(text.split())[:400]


def _origin_line(exc: BaseException, block: Block) -> str:
    """``L<n>`` of the deepest frame inside the snippet, when there is one."""
    for frame in reversed(traceback.extract_tb(exc.__traceback__)):
        if frame.filename.endswith(f"#L{block.lineno}"):
            return f" (snippet line {frame.lineno}, doc line {block.lineno + frame.lineno})"
    return ""


def _prelude() -> dict:
    """Names a guide block may use without importing them.

    Guides build a view across several fenced blocks; a later block omits
    ``from djust import LiveView`` because an earlier one had it. Seeding the
    namespace with djust's public ``__all__`` (plus Django's ``models`` /
    ``forms``) runs those blocks as the reader experiences them. An explicit
    ``from djust import Phantom`` still raises, so phantom symbols are caught;
    a name that is in no namespace surfaces as ``NameError`` and is recorded.
    """
    import djust
    from django import forms
    from django.db import models

    names = {name: getattr(djust, name) for name in djust.__all__}
    names["models"] = models
    names["forms"] = forms
    return names


def _undefined_name(exc: NameError) -> str:
    return getattr(exc, "name", None) or (re.search(r"name '([^']+)'", str(exc)) or [None, "?"])[1]


def _exec_block(block: Block, outcome: Outcome) -> dict:
    module = types.ModuleType(block.module_name)
    module.__file__ = f"{block.path}#L{block.lineno}"
    prelude = _prelude()
    module.__dict__.update(prelude)
    sys.modules[block.module_name] = module
    try:
        exec(compile(block.code, module.__file__, "exec"), module.__dict__)
    except ModuleNotFoundError as exc:
        sys.modules.pop(block.module_name, None)
        name = exc.name or ""
        if name == "djust" or name.startswith("djust."):
            raise _Fail("phantom djust module", _exc_text(exc)) from exc
        raise _Skip("imports a module the test env lacks", f"{name}") from exc
    except ImportError as exc:
        sys.modules.pop(block.module_name, None)
        if "relative import" in str(exc):
            raise _Skip("relative import of the reader's app", _exc_text(exc)) from exc
        name = getattr(exc, "name", "") or ""
        if name == "djust" or name.startswith("djust.") or "djust" in str(exc):
            raise _Fail("phantom djust symbol", _exc_text(exc)) from exc
        raise _Skip("imports a symbol the test env lacks", _exc_text(exc)) from exc
    except RuntimeError as exc:
        sys.modules.pop(block.module_name, None)
        if "app_label" in str(exc) or "INSTALLED_APPS" in str(exc):
            raise _Skip("defines a Django model (needs an app)", _exc_text(exc)) from exc
        raise _Fail("exec raised", _exc_text(exc) + _origin_line(exc, block)) from exc
    except NameError as exc:
        sys.modules.pop(block.module_name, None)
        raise _Skip(
            "references a name the block does not define",
            f"{_undefined_name(exc)}: {_exc_text(exc)}{_origin_line(exc, block)}",
        ) from exc
    except Exception as exc:  # noqa: BLE001 — every exception is an inventory row
        sys.modules.pop(block.module_name, None)
        raise _Fail("exec raised", _exc_text(exc) + _origin_line(exc, block)) from exc
    return module.__dict__


def _live_classes(namespace: dict, block: Block, outcome: Outcome) -> tuple[list[type], list[type]]:
    from djust import LiveView
    from djust.components.base import LiveComponent
    from djust.components.descriptors.base import LiveComponent as DescriptorComponent

    views, components = [], []
    for obj in namespace.values():
        if not isinstance(obj, type) or obj.__module__ != block.module_name:
            continue
        if issubclass(obj, LiveView) and obj is not LiveView:
            views.append(obj)
        elif issubclass(obj, DescriptorComponent):
            # ADR-031 class-level components bind to a view attribute; their
            # ``self.state`` exists only through that binding, so they are
            # exercised by the owning view's mount/render, not standalone.
            outcome.notes.append(f"{obj.__name__}: descriptor component, exercised via its view")
        elif issubclass(obj, LiveComponent) and obj is not LiveComponent:
            components.append(obj)
    return views, components


def _event_handlers(cls: type) -> list[str]:
    from djust.decorators import is_event_handler

    names = []
    for name in dir(cls):
        if name.startswith("_"):
            continue
        attr = getattr(cls, name, None)
        if callable(attr) and is_event_handler(attr):
            names.append(name)
    return sorted(names)


def _requires_args(fn) -> bool:
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return False
    params = list(sig.parameters.values())
    if params and params[0].name == "self":
        params = params[1:]
    return any(
        p.default is inspect.Parameter.empty
        and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
        for p in params
    )


def _template_names(cls: type) -> set[str]:
    """``{{ name }}`` / ``{{ name|filter }}`` roots in the inline template."""
    template = getattr(cls, "template", None) or ""
    return {
        m.group(1)
        for m in re.finditer(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)", template)
        if m.group(1) not in {"request", "csrf_token", "user", "forloop"}
    }


def _phantom_attribute(error: str, block_classes: set[str]) -> str | None:
    """``Owner.attr`` when ``error`` is an AttributeError on one of the block's classes."""
    m = _NO_ATTR_RE.search(error)
    if m and m.group(1) in block_classes:
        return f"{m.group(1)}.{m.group(2)}"
    return None


def _failure_category(error: str, block_classes: set[str], default: str) -> str:
    return (
        "phantom attribute (never set/defined on the class)"
        if _phantom_attribute(error, block_classes)
        else default
    )


def _non_defect(error: str, block_classes: set[str]) -> tuple[str, str] | None:
    """(category, detail) when ``error`` is the reader's context, not a defect.

    * ``NameError`` / ``AttributeError`` on a block class, a user object or an
      ``...`` placeholder: the block references a model, helper or earlier
      block it does not carry. The undefined name is recorded so a phantom
      API can still be spotted in the inventory.
    * a network call at mount; an authorization gate denying the test user.
    """
    m = re.search(r"name '([^']+)' is not defined", error)
    if m:
        return ("references a name the block does not define", m.group(1))
    m = _NO_ATTR_RE.search(error)
    if m:
        owner, attr = m.group(1), m.group(2)
        if owner in {"User", "AnonymousUser"}:
            return ("references a name the block does not define", f"{owner}.{attr}")
        # ``owner in block_classes`` is NOT a skip: ``self.sidebar_open`` that
        # mount() never set, or ``self.fetch_sales()`` the block never defines,
        # is the phantom-attribute class this test exists to report.
    if "'ellipsis' object" in error:
        return ("uses a ... placeholder", error[:160])
    if _NETWORK_RE.search(error):
        return ("performs a network call", error[:160])
    if _DENIED_RE.search(error):
        return ("authorization-gated; the test user is denied", error[:160])
    return None


_BINDING_RE = re.compile(
    r"dj-(?:click|submit|change|input|keydown|keyup|keypress|blur|focus|dblclick|mouseenter|mouseleave)"
    r"(?:\.[A-Za-z0-9_.-]+)?=\"\s*([A-Za-z_][A-Za-z0-9_]*)"
)


def _check_bindings(cls: type, outcome: Outcome) -> None:
    """Every ``dj-click="name"`` in the inline template names an exposed handler.

    The test client calls methods by name and does not enforce decoration, so
    a handler that lost its ``@event_handler`` would otherwise pass; the
    consumer refuses it (``websocket_utils._format_handler_not_found_error``).
    """
    from djust.decorators import is_action, is_event_handler, is_server_function

    template = getattr(cls, "template", None) or ""
    for target in sorted({m.group(1) for m in _BINDING_RE.finditer(template)}):
        fn = getattr(cls, target, None)
        if fn is None or not callable(fn):
            raise _Fail(
                "missing handler",
                f"{cls.__name__}: the template binds `{target}` but the class has no such method",
            )
        if not (is_event_handler(fn) or is_action(fn) or is_server_function(fn)):
            raise _Fail(
                "missing handler",
                f"{cls.__name__}.{target} is bound in the template but is not decorated "
                "(@event_handler / @action); the consumer refuses undecorated methods",
            )


def _classify_handler_error(error: str) -> str | None:
    """``"skip"`` when the handler's documented signature needs arguments.

    The test client folds the handler's exception into ``result["error"]`` as
    text, so the classification is by message.
    """
    if _MISSING_ARGS_RE.search(error):
        return "skip"
    return None


def _run_view(cls: type, outcome: Outcome, block_classes: set[str]) -> None:
    from django.template import TemplateDoesNotExist

    from djust.testing import LiveViewTestClient

    name = cls.__name__
    stub_used = False
    if not getattr(cls, "template", None):
        # The README hero bug: a template-ish attribute no mixin reads
        # (``template_string = ...``). Without this check the stub below
        # would mask it as "no template declared".
        phantom = sorted(
            attr
            for attr in vars(cls)
            if "template" in attr.lower()
            and not any(hasattr(base, attr) for base in cls.__mro__[1:])
        )
        if phantom:
            raise _Fail(
                "phantom template attribute",
                f"{name} sets {', '.join(phantom)}, which no djust mixin reads; a reader gets "
                "`ValueError: Either template_name or template must be set`",
            )
        template_name = getattr(cls, "template_name", None)
        shipped = False
        if template_name:
            from django.template.loader import get_template

            try:
                get_template(template_name)
                shipped = True
            except TemplateDoesNotExist:
                shipped = False
        if not shipped:
            # The template is the reader's to write (or the block is a
            # fragment that shows only the Python side); run mount(),
            # get_context_data() and the handlers on a stub root instead.
            cls = type(
                name,
                (cls,),
                {
                    "__module__": cls.__module__,
                    "template": "<div dj-root>doc-snippet stub</div>",
                    "template_name": None,
                },
            )
            stub_used = True
            outcome.notes.append(
                f"{name}: template_name={template_name!r} not shipped; stub used"
                if template_name
                else f"{name}: no template declared (fragment); stub used"
            )

    # A real, signed-in user: guides read ``request.user.first_name`` and the
    # like; an AnonymousUser would turn every such read into a false defect.
    from django.contrib.auth import get_user_model

    user, _created = get_user_model().objects.get_or_create(
        username="doc-snippet-reader", defaults={"first_name": "Doc"}
    )
    client = LiveViewTestClient(cls, user=user)
    try:
        client.mount()
    except TypeError as exc:
        if _MISSING_ARGS_RE.search(str(exc)) or _UNEXPECTED_KW_RE.search(str(exc)):
            raise _Skip("mount() requires URL kwargs", f"{name}: {_exc_text(exc)}") from exc
        raise _Fail("mount raised", f"{name}: {_exc_text(exc)}") from exc
    except Exception as exc:  # noqa: BLE001
        kind = _non_defect(_exc_text(exc), block_classes)
        if kind:
            raise _Skip(kind[0], f"{kind[1]}: {name}.mount: {_exc_text(exc)}") from exc
        raise _Fail(
            _failure_category(_exc_text(exc), block_classes, "mount raised"),
            f"{name}: {_exc_text(exc)}",
        ) from exc

    try:
        html = client.render()
    except Exception as exc:  # noqa: BLE001
        if _TEMPLATE_MISSING_RE.search(_exc_text(exc)):
            raise _Skip("needs a template file", f"{name}: {_exc_text(exc)}") from exc
        kind = _non_defect(_exc_text(exc), block_classes)
        if kind:
            raise _Skip(kind[0], f"{kind[1]}: {name}.render: {_exc_text(exc)}") from exc
        raise _Fail(
            _failure_category(_exc_text(exc), block_classes, "render raised"),
            f"{name}: {_exc_text(exc)}",
        ) from exc
    if "dj-root" not in html and "dj-view" not in html:
        raise _Fail(
            "inline template has no dj-root / dj-view root",
            f"{name}: the client has nothing to attach to (template-requirements.md)",
        )
    if not stub_used:
        _check_bindings(cls, outcome)

    if not stub_used:
        state = client.get_state()
        for key in sorted(_template_names(cls)):
            value = state.get(key)
            if isinstance(value, (int, str)) and not isinstance(value, bool) and value != "":
                if str(value) not in html:
                    raise _Fail(
                        "mounted state did not reach the HTML",
                        f"{name}: `{key}={value!r}` is in the template but not the render",
                    )

    for handler in _event_handlers(cls):
        fn = getattr(cls, handler)
        if _requires_args(fn):
            outcome.notes.append(f"{name}.{handler}: requires arguments, not fired")
            continue
        try:
            result = client.send_event(handler)
        except Exception as exc:  # noqa: BLE001
            raise _Fail("handler raised", f"{name}.{handler}: {_exc_text(exc)}") from exc
        if not result["success"]:
            error = str(result.get("error"))
            if _classify_handler_error(error):
                outcome.notes.append(f"{name}.{handler}: {error[:160]}")
                continue
            kind = _non_defect(error, block_classes)
            if kind:
                outcome.notes.append(f"{name}.{handler}: {kind[0]} ({kind[1]}); not fired further")
                continue
            raise _Fail(
                _failure_category(error, block_classes, "handler failed"),
                f"{name}.{handler}: {error[:400]}",
            )
        try:
            client.render()
        except Exception as exc:  # noqa: BLE001
            raise _Fail(
                "render after handler raised", f"{name}.{handler}: {_exc_text(exc)}"
            ) from exc


def _run_component(cls: type, outcome: Outcome, block_classes: set[str]) -> None:
    from djust.testing import LiveComponentTestClient

    name = cls.__name__
    client = LiveComponentTestClient(cls)
    try:
        client.mount()
    except TypeError as exc:
        if _MISSING_ARGS_RE.search(str(exc)):
            raise _Skip("component mount() requires props", f"{name}: {_exc_text(exc)}") from exc
        raise _Fail(
            _failure_category(_exc_text(exc), block_classes, "component mount raised"),
            f"{name}: {_exc_text(exc)}",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        kind = _non_defect(_exc_text(exc), block_classes)
        if kind:
            raise _Skip(kind[0], f"{kind[1]}: {name}.mount: {_exc_text(exc)}") from exc
        raise _Fail(
            _failure_category(_exc_text(exc), block_classes, "component mount raised"),
            f"{name}: {_exc_text(exc)}",
        ) from exc
    try:
        client.render()
    except Exception as exc:  # noqa: BLE001
        if _TEMPLATE_MISSING_RE.search(_exc_text(exc)):
            raise _Skip("needs a template file", f"{name}: {_exc_text(exc)}") from exc
        kind = _non_defect(_exc_text(exc), block_classes)
        if kind:
            raise _Skip(kind[0], f"{kind[1]}: {name}.render: {_exc_text(exc)}") from exc
        raise _Fail(
            _failure_category(_exc_text(exc), block_classes, "component render raised"),
            f"{name}: {_exc_text(exc)}",
        ) from exc
    _check_bindings(cls, outcome)
    for handler in _event_handlers(cls):
        fn = getattr(cls, handler)
        if _requires_args(fn):
            outcome.notes.append(f"{name}.{handler}: requires arguments, not fired")
            continue
        try:
            result = client.send_event(handler)
        except Exception as exc:  # noqa: BLE001
            raise _Fail("component handler raised", f"{name}.{handler}: {_exc_text(exc)}") from exc
        if not result["success"]:
            error = str(result.get("error"))
            if _classify_handler_error(error):
                outcome.notes.append(f"{name}.{handler}: {error[:160]}")
                continue
            kind = _non_defect(error, block_classes)
            if kind:
                outcome.notes.append(f"{name}.{handler}: {kind[0]} ({kind[1]}); not fired further")
                continue
            raise _Fail(
                _failure_category(error, block_classes, "component handler failed"),
                f"{name}.{handler}: {error[:400]}",
            )


def run_block(block: Block) -> Outcome:
    outcome = Outcome(block=block.id, status="pass", category="ok")
    try:
        namespace = _exec_block(block, outcome)
        views, components = _live_classes(namespace, block, outcome)
        outcome.classes = [c.__name__ for c in views + components]
        block_classes = {
            obj.__name__
            for obj in namespace.values()
            if isinstance(obj, type) and obj.__module__ == block.module_name
        }
        if not views and not components:
            raise _Skip("defines no concrete LiveView/LiveComponent", "")
        for cls in views:
            _run_view(cls, outcome, block_classes)
        for cls in components:
            _run_component(cls, outcome, block_classes)
    except _Skip as skip:
        outcome.status, outcome.category, outcome.detail = "skip", skip.category, skip.detail
    except _Fail as fail:
        outcome.status, outcome.category, outcome.detail = "fail", fail.category, fail.detail
    finally:
        sys.modules.pop(block.module_name, None)
    return outcome


def _record(outcome: Outcome) -> None:
    path = os.environ.get("DJUST_DOC_SNIPPET_INVENTORY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(outcome.as_record()) + "\n")


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------

_BLOCKS = _candidate_blocks()


def test_doc_files_define_liveview_snippets():
    assert len(_BLOCKS) > 100, "the guides no longer carry fenced LiveView examples to execute"


def test_known_failures_are_current_blocks():
    keys = {b.key for b in _BLOCKS}
    stale = sorted(set(KNOWN_FAILURES) - keys)
    assert not stale, (
        f"KNOWN_FAILURES names blocks that no longer exist or whose code changed: {stale}. "
        "If the block was fixed, delete the entry; if it still fails, re-key it with Block.key."
    )


def _param(block: Block):
    marks = []
    if block.key in KNOWN_FAILURES:
        marks.append(pytest.mark.xfail(reason=KNOWN_FAILURES[block.key], strict=True))
    return pytest.param(block, id=block.id, marks=marks)


_LOOPBACK = {"localhost", "127.0.0.1", "::1", "", None}


def _is_ip_literal(host) -> bool:
    import ipaddress

    try:
        ipaddress.ip_address(host)
    except (TypeError, ValueError):
        return False
    return True


@pytest.fixture
def no_network(monkeypatch):
    """A guide block that fetches ``https://api.example.com`` must fail the same
    way on every machine: ``ConnectError``, which the harness records as "performs
    a network call". Left alone it resolves a real name, so a resolver that
    answers for unknown hosts (a captive portal, an ISP) turns the same block
    into a ``JSONDecodeError`` failure, and a slow DNS makes the test slow."""
    import socket

    real_getaddrinfo = socket.getaddrinfo
    real_connect = socket.socket.connect

    def getaddrinfo(host, *args, **kwargs):
        if host in _LOOPBACK or _is_ip_literal(host):
            return real_getaddrinfo(host, *args, **kwargs)
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    def connect(self, address):
        host = address[0] if isinstance(address, tuple) else None
        if self.family == socket.AF_UNIX or host in _LOOPBACK:
            return real_connect(self, address)
        raise ConnectionRefusedError(f"doc snippets run offline: {address!r}")

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", connect)


def test_the_offline_guard_blocks_names_and_addresses_but_not_loopback(no_network):
    import socket

    with pytest.raises(socket.gaierror):
        socket.getaddrinfo("api.example.com", 443)
    with pytest.raises(ConnectionRefusedError):
        socket.create_connection(("93.184.216.34", 80), timeout=1)
    assert socket.getaddrinfo("localhost", 80)  # in-process transports still resolve


def test_an_anti_pattern_block_is_not_executed(tmp_path, monkeypatch):
    doc = tmp_path / "doc.md"
    fence = "```"
    body = "from djust import LiveView\n\nclass Wrong(LiveView):\n    template = '<div>no root</div>'\n"
    doc.write_text(
        f"<!-- doc-snippet-check: anti-pattern -->\n{fence}python\n{body}{fence}\n\n"
        f"{fence}python\n{body}{fence}\n"
    )
    monkeypatch.setattr(sys.modules[__name__], "REPO_ROOT", tmp_path)
    found = _blocks_in([doc])
    assert [b.lineno for b in found] == [9]  # only the unmarked copy


def test_known_failure_keys_follow_the_block_not_its_line(tmp_path, monkeypatch):
    doc = tmp_path / "doc.md"
    monkeypatch.setattr(sys.modules[__name__], "REPO_ROOT", tmp_path)
    fence = "```"
    code = "from djust import LiveView\n\nclass A(LiveView):\n    pass\n"
    doc.write_text(f"{fence}python\n{code}{fence}\n")
    (before,) = _blocks_in([doc])
    doc.write_text(f"intro line\nanother\n{fence}python\n{code}{fence}\n")
    (after,) = _blocks_in([doc])
    assert before.lineno != after.lineno and before.key == after.key


@pytest.mark.django_db
@pytest.mark.parametrize("block", [_param(b) for b in _BLOCKS])
def test_doc_snippet_executes(block: Block, settings, no_network):
    """The block executes, mounts, renders and handles events as documented."""
    settings.LIVEVIEW_ALLOWED_MODULES = [block.module_name]
    outcome = run_block(block)
    _record(outcome)
    if outcome.status == "skip":
        pytest.skip(f"{outcome.category}: {outcome.detail}"[:300])
    assert outcome.status == "pass", (
        f"{block.id} [{', '.join(outcome.classes)}] {outcome.category}: {outcome.detail}"
    )
