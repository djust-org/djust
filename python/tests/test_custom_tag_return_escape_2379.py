"""A custom tag handler's return is ESCAPED unless it is already HTML (#2379).

The defect
----------
Django's ``SimpleNode.render`` runs ``conditional_escape`` over a
``simple_tag``'s return unless it carries ``__html__``::

    output = self.func(*resolved_args, **resolved_kwargs)
    if context.autoescape:
        output = conditional_escape(output)

``renderer.rs``'s ``Node::CustomTag`` arm inserted the return **verbatim**, so
a handler as ordinary as::

    @register.simple_tag
    def greet(name):
        return f"Hello {name}"

emitted ``Hello <img src=x onerror=alert(1)>`` live where Django renders
``Hello &lt;img …&gt;``. This is the fail-OPEN half of the asymmetry #2290
found on the way IN, and it reached every ``register_tag_handler`` /
``register_block_tag_handler`` user — djust's own handlers and any project's.

The audit is the work, not the one-line bridge change
-----------------------------------------------------
Escaping a return that legitimately IS markup is a rendering regression rather
than a fix, so every handler djust registers was enumerated MECHANICALLY — by
intercepting the three ``register_*_tag_handler`` functions and triggering
every registration path — and then CALLED.

The issue's own table names ten modules and reads as about twenty handlers.
Measured: **221 handlers across thirteen modules**, and the direction is the
opposite of the issue's premise. It says "almost none of them ``mark_safe``";
in fact **195 already carried ``__html__``**, 13 return the empty string and 5
return plain text — leaving **6** that returned markup as a plain ``str``.
Those six are fixed here. :class:`TestEveryRegisteredHandlerIsAccountedFor`
re-runs the enumeration, so a handler added later without the marker fails a
test rather than shipping as escaped text on someone's page.

The half a return-only fix would have got wrong
-----------------------------------------------
Django's ``simple_block_tag`` hands the handler ``nodelist.render(context)`` —
already-rendered, already-escaped markup, and therefore ``SafeData``. djust
passed it across PyO3 as a bare ``str``, so a handler returning its content
unchanged lost the marker and the escape applied it a SECOND time:
``&lt;img …&gt;`` became ``&amp;lt;img …&amp;gt;``. The bridge now marks the
block body safe on the way in, which is the block-path twin of #2290's
marker loss.

Every expectation here is LIVE Django, never a transcription.
"""

from __future__ import annotations

import importlib
import pkgutil
import re

import pytest

pytest.importorskip("django")

import django  # noqa: E402
from django.conf import settings  # noqa: E402

if not settings.configured:  # pragma: no cover — import-time bootstrap
    settings.configure(
        DEBUG=False,
        USE_I18N=False,
        USE_TZ=False,
        TIME_ZONE="UTC",
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [],
                "APP_DIRS": False,
                "OPTIONS": {"context_processors": []},
            }
        ],
        INSTALLED_APPS=[],
    )
    django.setup()

from django import template as dj_template  # noqa: E402
from django.template import Context as DjangoContext  # noqa: E402
from django.template.base import Template as DjangoTemplate  # noqa: E402
from django.template.engine import Engine  # noqa: E402
from django.utils.html import conditional_escape  # noqa: E402
from django.utils.safestring import mark_safe  # noqa: E402

from djust import _rust  # noqa: E402
from djust.mixins.rust_bridge import _collect_safe_keys  # noqa: E402

XSS = "<img src=x onerror=alert(1)>"

#: An unescaped tag OPENER. Substring-matching the payload would also match
#: inside fully-escaped text, which is the split `--compare` makes and the
#: reason a "the payload appears" assertion is not a leak test.
UNESCAPED_TAG = re.compile(r"<[a-zA-Z/!]")

_LIB = dj_template.Library()


@_LIB.simple_tag(name="e2379_ident")
def _ident(value):
    """Returns its argument untouched — the sharpest probe there is.

    Django escapes iff the FINAL value lacks ``__html__``, so this is the cell
    that isolates the return channel from everything the handler might do.
    """
    return value


@_LIB.simple_tag(name="e2379_greet")
def _greet(name):
    """The issue's own example, verbatim."""
    return f"Hello {name}"


@_LIB.simple_tag(name="e2379_safe")
def _safe(value):
    return mark_safe("[" + str(value) + "]")  # noqa: S308


@_LIB.simple_tag(name="e2379_cond")
def _cond(value):
    return conditional_escape(value)


def _block_ident(content):
    return content


def _block_plain(content):
    return "[" + str(content) + "]"


_LIB.simple_block_tag(name="e2379_bident", end_name="ende2379_bident")(_block_ident)


class _Probe:
    """The ``.render(args, context)`` shape ``registry.rs`` calls a handler with."""

    def __init__(self, fn, kind="tag"):
        self.fn, self.kind = fn, kind

    def render(self, args, *rest):
        if self.kind == "block":
            content, _context = rest
            return self.fn(content)
        return self.fn(*args)


@pytest.fixture(scope="module", autouse=True)
def _probes():
    """Register on BOTH engines from one function body, and clean up after."""
    Engine.get_default().template_builtins.append(_LIB)
    _rust.register_tag_handler("e2379_ident", _Probe(_ident))
    _rust.register_tag_handler("e2379_greet", _Probe(_greet))
    _rust.register_tag_handler("e2379_safe", _Probe(_safe))
    _rust.register_tag_handler("e2379_cond", _Probe(_cond))
    _rust.register_block_tag_handler(
        "e2379_bident", "ende2379_bident", _Probe(_block_ident, "block")
    )
    yield
    for name in ("e2379_ident", "e2379_greet", "e2379_safe", "e2379_cond"):
        _rust.unregister_tag_handler(name)
    _rust.unregister_block_tag_handler("e2379_bident")
    Engine.get_default().template_builtins.remove(_LIB)


def both(source: str, ctx: dict) -> tuple[str, str]:
    """Both engines, over the SAME input — grants included (#2416).

    Django reads a value's `SafeData` off the object itself; djust is told
    which context paths carry it through a separate `safe_keys` channel, and
    `render_template` has no parameter for it — only `render_template_with_dirs`
    does (#2287). Before #2416 this helper called `render_template`, so no row
    in this file could grant anything and the marked-context rows measured "the
    engine was never told" rather than "the marker did not survive the hop".
    The grants are DERIVED with djust's own `_collect_safe_keys` rather than
    hand-listed, so a test cannot claim one the bridge would not produce.
    """
    dj = DjangoTemplate(source, engine=Engine.get_default()).render(DjangoContext(dict(ctx)))
    safe_keys: list[str] = []
    for key, value in ctx.items():
        safe_keys.extend(_collect_safe_keys(value, key))
    if safe_keys:
        du = _rust.render_template_with_dirs(source, dict(ctx), [], safe_keys)
    else:
        du = _rust.render_template(source, dict(ctx))
    return dj, du


# ---------------------------------------------------------------------------
# The XSS the issue reports
# ---------------------------------------------------------------------------


class TestAHandlersPlainReturnIsEscaped:
    def test_the_issues_own_example(self) -> None:
        dj, du = both("{% e2379_greet p %}", {"p": XSS})
        assert du == dj
        assert not UNESCAPED_TAG.search(du), du

    @pytest.mark.parametrize(
        "payload",
        [XSS, "</script><script>alert(1)</script>", "a < b", '" onmouseover="x', "a & b"],
    )
    def test_the_identity_probe_over_every_hostile_shape(self, payload: str) -> None:
        dj, du = both("{% e2379_ident p %}", {"p": payload})
        assert du == dj
        assert not UNESCAPED_TAG.search(du), du

    def test_a_block_handlers_plain_wrapper_is_escaped(self) -> None:
        source = "{% e2379_bident %}{{ p }}{% ende2379_bident %}"
        dj, du = both(source, {"p": XSS})
        assert du == dj

    def test_no_shape_emits_a_live_payload_django_does_not(self) -> None:
        """The permissiveness question on its own, over the whole probe grid."""
        shapes = [
            "{% e2379_ident p %}",
            "{% e2379_greet p %}",
            "{% e2379_safe p %}",
            "{% e2379_cond p %}",
            "{% e2379_bident %}{{ p }}{% ende2379_bident %}",
        ]
        leaked = []
        for source in shapes:
            dj, du = both(source, {"p": XSS})
            if UNESCAPED_TAG.search(du) and not UNESCAPED_TAG.search(dj):
                leaked.append((source, dj, du))
        assert not leaked, leaked


class TestAMarkedReturnIsStillLive:
    """The half that makes this a fix rather than a blanket escape."""

    def test_a_mark_safe_return_renders_markup(self) -> None:
        dj, du = both("{% e2379_safe p %}", {"p": "x"})
        assert du == dj
        assert "[x]" in du

    def test_a_mark_safe_return_carrying_a_payload_matches_django(self) -> None:
        """Django trusts a `mark_safe`d return and so does djust — the
        handler's own decision, unchanged by this fix."""
        dj, du = both("{% e2379_safe p %}", {"p": XSS})
        assert du == dj
        assert "<img" in du


class TestTheBlockBodyIsSafeData:
    """The regression a return-only fix would have shipped.

    Django's `simple_block_tag` hands the handler `nodelist.render(context)`,
    which is `SafeData`. Without the bridge marking it, the body is escaped
    twice — once by the `{{ p }}` inside it and again by the return escape.
    """

    def test_an_identity_block_handler_emits_the_body_once(self) -> None:
        source = "{% e2379_bident %}{{ p }}{% ende2379_bident %}"
        dj, du = both(source, {"p": XSS})
        assert du == dj
        assert du == "&lt;img src=x onerror=alert(1)&gt;", du
        assert "&amp;lt;" not in du, "the body was escaped twice"

    def test_the_handler_actually_receives_SafeData(self) -> None:
        """Measured at the handler rather than inferred from the output: the
        output alone cannot tell "the bridge marked it" from "the handler
        marked it"."""
        seen: dict[str, object] = {}

        class _Capture:
            def render(self, args, content, context):  # noqa: ARG002
                seen["html"] = hasattr(content, "__html__")
                return content

        _rust.register_block_tag_handler("e2379_cap", "ende2379_cap", _Capture())
        try:
            _rust.render_template("{% e2379_cap %}x{% ende2379_cap %}", {})
        finally:
            _rust.unregister_block_tag_handler("e2379_cap")
        assert seen["html"] is True, "the block body reached the handler as a bare str"


# ---------------------------------------------------------------------------
# The shared predicate, and the XSS its str-subclass half guards
# ---------------------------------------------------------------------------


class _Impostor:
    """NOT a `str`, advertises `__html__`, renders a payload through `__str__`.

    The object #2290's predicate exists to refuse. Trusting `__html__` alone
    would let it through, because `Value`'s `FromPyObject` stringifies an
    arbitrary object via `__str__`.
    """

    def __html__(self):
        return XSS

    def __str__(self):
        return XSS


class _SafeSubclass(str):
    """A genuine `str` subclass carrying the marker — what Django's
    `SafeString` is, spelled without importing it."""

    def __html__(self):
        return self


class TestTheSharedPredicateRefusesAnImpostor:
    """`py_value_is_safe_string` is #2290's test, EXTRACTED for three callers.

    Requiring `str` subclass-ness and not just `__html__` is the security half,
    and it is load-bearing on a DIFFERENT path from the one #2379 adds — which
    is why gating it off leaves the tag suite green and is not a decorative
    duplicate:

    * on the FILTER path, `extract::<Value>()` stringifies an arbitrary object,
      so the predicate is the ONLY thing standing between the impostor and an
      unescaped payload;
    * on the TAG path, `extract::<String>()` refuses a non-`str` outright, so
      the impostor never reaches the marker test at all.

    Both are asserted, because "the impostor cannot leak" has two different
    proofs and only one of them is the predicate's.
    """

    def test_a_custom_FILTER_does_not_trust_a_non_str_html_marker(self) -> None:
        _rust.register_custom_filter("e2379_impostor", lambda value: _Impostor())
        try:
            out = _rust.render_template("{{ p|e2379_impostor }}", {"p": "x"})
        finally:
            _rust.unregister_custom_filter("e2379_impostor")
        assert not UNESCAPED_TAG.search(out), f"the impostor leaked: {out!r}"

    def test_a_custom_FILTER_does_trust_a_real_str_subclass(self) -> None:
        """The other half — otherwise "refuses the impostor" would be
        satisfied by refusing everything."""
        _rust.register_custom_filter("e2379_realsafe", lambda value: _SafeSubclass(XSS))
        try:
            out = _rust.render_template("{{ p|e2379_realsafe }}", {"p": "x"})
        finally:
            _rust.unregister_custom_filter("e2379_realsafe")
        assert out == XSS

    def test_a_TAG_handler_returning_a_non_str_is_REFUSED(self) -> None:
        """`extract::<String>()` is the tag path's own guard, and it refuses
        before the marker test is reached. Never more permissive."""

        class _Probe:
            def render(self, args, context):  # noqa: ARG002
                return _Impostor()

        _rust.register_tag_handler("e2379_imp", _Probe())
        try:
            with pytest.raises(Exception) as exc:
                _rust.render_template("{% e2379_imp %}", {})
        finally:
            _rust.unregister_tag_handler("e2379_imp")
        assert XSS not in str(exc.value), "the refusal echoed the payload"

    def test_a_TAG_handler_returning_a_real_str_subclass_is_LIVE(self) -> None:
        class _Probe:
            def render(self, args, context):  # noqa: ARG002
                return _SafeSubclass(XSS)

        _rust.register_tag_handler("e2379_realsafe_tag", _Probe())
        try:
            out = _rust.render_template("{% e2379_realsafe_tag %}", {})
        finally:
            _rust.unregister_tag_handler("e2379_realsafe_tag")
        assert out == XSS


# ---------------------------------------------------------------------------
# The audit, re-run
# ---------------------------------------------------------------------------


def _registered_handlers() -> dict[str, tuple[str, object]]:
    """Every handler djust registers, by intercepting the registries.

    Not a list: enumerating "the places I know call X" is reliably one short
    (v1.1.1-2 retro), and this repo registers through a decorator, two
    ``register_with_rust_engine`` functions and a component registry.

    **This leaks, irreversibly, and that is not fixable here (#2427).** The
    ``importlib.reload`` below is what re-triggers registration, and a reload
    rebinds every class in the module AND registers a NEW instance of it. It
    cannot be undone — the ``finally`` restores the intercepted ``_rust``
    functions, not the module state — so from here on, a module-level
    ``from djust.template_tags.X import SomeHandler`` elsewhere in the suite is
    a STALE class object that the engine will never call.

    That reddened
    ``test_regroup_string_source_2385_2394.py::…::test_the_renderer_hands_a_``
    ``string_source_over_quoted`` intermittently on CI, whenever xdist put this
    audit on the same worker ahead of it. The cure is on the reading side: a
    test that patches a handler must resolve the class from
    ``djust.template_tags._registered_handlers`` rather than from its own
    import. See ``live_regroup_class()`` in that file.
    """
    import djust
    import djust.template_tags

    recorded: dict[str, tuple[str, object]] = {}
    real = {
        "tag": _rust.register_tag_handler,
        "block": _rust.register_block_tag_handler,
        "assign": _rust.register_assign_tag_handler,
    }

    def wrap(kind, fn):
        def inner(name, *rest):
            recorded[name] = (kind, rest[-1])
            return fn(name, *rest)

        return inner

    attrs = {
        "tag": "register_tag_handler",
        "block": "register_block_tag_handler",
        "assign": "register_assign_tag_handler",
    }
    for kind, attr in attrs.items():
        setattr(_rust, attr, wrap(kind, real[kind]))
    try:
        # RELOAD, not import: `djust/__init__.py` already imported
        # `template_tags`, so an `import_module` here is a no-op and the
        # family would be silently missing.
        for mod in pkgutil.iter_modules(djust.template_tags.__path__):
            importlib.reload(importlib.import_module(f"djust.template_tags.{mod.name}"))
        for path in ("djust.components.rust_handlers", "djust.theming.rust_handlers"):
            module = importlib.import_module(path)
            if hasattr(module, "register_with_rust_engine"):
                # `register_with_rust_engine` SKIPS a name the registry
                # already knows, and the theming AppConfig may have
                # registered during `django.setup()`.
                for name in getattr(module, "_THEME_TAG_NAMES", ()):
                    _rust.unregister_tag_handler(name)
                module.register_with_rust_engine()
    finally:
        for kind, attr in attrs.items():
            setattr(_rust, attr, real[kind])
    return recorded


class TestEveryRegisteredHandlerIsAccountedFor:
    """The audit, as a test — so a handler added later is caught here.

    A handler that returns markup without ``__html__`` does not fail loudly:
    its markup renders as escaped TEXT on the page. That is the failure mode
    this class exists to convert into a red test.
    """

    @pytest.fixture(scope="class")
    def handlers(self) -> dict[str, tuple[str, object]]:
        found = _registered_handlers()
        assert len(found) > 100, (
            f"the enumeration found only {len(found)} handlers — it is not "
            "reaching the registration paths it is meant to"
        )
        return found

    def test_the_enumeration_covers_djusts_own_registry(self, handlers) -> None:
        """A precondition, not a result: djust keeps its own registry of
        decorator-registered handlers, and every name in it must appear here.

        Scoped to handlers DEFINED IN djust. `_registered_handlers` is a
        module-level accumulator with no teardown, so any test that registers
        through the `@register` decorator leaves its handler there for the
        rest of the process — `tests/unit/test_tag_registry.py` registers one
        named `custom`. This enumeration reloads djust's own modules, so a
        foreign handler is legitimately absent from it, and asserting over the
        raw accumulator makes this test fail on TEST ORDERING rather than on
        anything about djust. It passed locally and failed on CI's xdist
        distribution for exactly that reason.
        """
        import djust.template_tags

        declared = {
            name
            for name, handler in djust.template_tags.get_registered_handlers().items()
            if type(handler).__module__.startswith("djust.")
        }
        assert declared, "djust's own handler registry is empty — nothing was triggered"
        assert not (declared - set(handlers)), sorted(declared - set(handlers))

    def test_that_scoping_still_covers_the_real_handlers(self) -> None:
        """Non-vacuity for the filter above: it must not have narrowed
        `declared` to nothing, or to a set that excludes the tags this PR
        touches."""
        import djust.template_tags

        declared = {
            name
            for name, handler in djust.template_tags.get_registered_handlers().items()
            if type(handler).__module__.startswith("djust.")
        }
        for name in ("djust_markdown", "static", "url", "regroup"):
            assert name in declared, f"{name} was scoped out — the filter is too narrow"

    def test_a_FOREIGN_handler_does_not_break_the_enumeration(self, handlers) -> None:
        """The empirical canary for the failure that got past a green local run.

        `_registered_handlers` is process-global with no teardown, so a handler
        another test registers through the `@register` decorator stays there.
        The first version of the precondition above asserted over the raw
        accumulator, which made it true only in a pristine process: it passed
        locally and failed on CI, where xdist put
        `tests/unit/test_tag_registry.py` (which registers one named `custom`)
        and this file on the same worker.

        A green full-suite run genuinely cannot rule that out — the scheduling
        is what decides it. So the property is asserted DIRECTLY here instead:
        register a handler under a foreign name, from a foreign module, and
        the enumeration's precondition must still hold.
        """
        import djust.template_tags

        registry = djust.template_tags._registered_handlers

        class _ForeignHandler:
            """Defined HERE, so `__module__` is this test file, not `djust.*`."""

            def render(self, args, context):  # noqa: ARG002
                return "x"

        name = "e2379_foreign_probe"
        assert name not in registry, "the probe name is already taken"
        djust.template_tags.register(name)(_ForeignHandler)
        try:
            assert name in registry, (
                "the decorator did not reach the accumulator, so this canary "
                "is not reproducing the real pollution"
            )
            declared = {
                n
                for n, handler in registry.items()
                if type(handler).__module__.startswith("djust.")
            }
            assert name not in declared, "the foreign handler was not scoped out"
            assert not (declared - set(handlers)), sorted(declared - set(handlers))
        finally:
            # Restore, or this canary becomes the pollution it is testing for.
            registry.pop(name, None)
            _rust.unregister_tag_handler(name)
        assert name not in djust.template_tags._registered_handlers

    def test_the_theming_family_is_reached(self, handlers) -> None:
        """The family the first version of this enumeration silently missed,
        because its AppConfig had registered before the interception."""
        from djust.theming.rust_handlers import _THEME_TAG_NAMES

        assert not (set(_THEME_TAG_NAMES) - set(handlers))

    def test_no_handler_returns_MARKUP_without_marking_it_safe(self, handlers) -> None:
        """The audit's load-bearing assertion.

        Each handler is CALLED through the shape the Rust bridge calls it
        with, never read: a ``mark_safe`` on line 3 of a body with four
        returns answers this for one of them.
        """
        offenders = []
        for name, (kind, handler) in sorted(handlers.items()):
            render = getattr(handler, "render", handler)
            try:
                out = render([], "<b>body</b>", {}) if kind == "block" else render([], {})
            except Exception:  # noqa: BLE001 — a handler needing real args
                continue
            if not isinstance(out, str) or hasattr(out, "__html__"):
                continue
            if UNESCAPED_TAG.search(out):
                offenders.append((name, kind, out[:80]))
        assert not offenders, (
            "these handlers return markup without `__html__`, so the bridge "
            f"will render it as escaped text: {offenders}"
        )

    def test_the_six_this_pr_fixed_are_marked(self, handlers) -> None:
        """Named individually, so a revert of any one is a red test rather
        than a smaller count nobody reads."""
        #: The argument each needs to reach the branch that RETURNS MARKUP.
        #: `djust_markdown` with no source returns `""` and would pass this
        #: for the wrong reason — a tautology the first version of this test
        #: had until it went red on exactly that.
        args = {"djust_markdown": ["'# hi'"]}
        for name in ("call", "component", "dj_suspense", "djust_markdown", "slot"):
            kind, handler = handlers[name]
            out = (
                handler.render(args.get(name, []), "<b>body</b>", {})
                if kind == "block"
                else handler.render(args.get(name, []), {})
            )
            assert hasattr(out, "__html__"), f"{name} lost its mark_safe"
            assert UNESCAPED_TAG.search(str(out)), (
                f"{name} returned no markup here, so this row proves nothing: {out!r}"
            )
        # `toast_container` needs no args but is a `tag`, and its EMPTY-list
        # branch is the one that was missing the module's own `_safe()`.
        _kind, toast = handlers["toast_container"]
        assert hasattr(toast.render([], {}), "__html__")
        assert hasattr(toast.render([], {"toasts": [{"id": "1", "message": "m"}]}), "__html__")


#: Argument vectors a handler is plausibly called with — the shapes the Rust
#: dispatch actually produces after `value_to_arg_string`, not invented ones.
#: A bare word, a value carrying MARKUP (which is where the unmarked-return
#: question lives), a JSON-encoded structured value, dotted paths, and a kwarg
#: whose value carries a double quote.
#:
#: That last vector is #2434's (added with its fix). A handler's SUCCESS branch
#: and its FAILURE branch are different returns, and every vector above reaches
#: only the first — so the four PWA handlers, which delegate to a Django tag
#: and diagnose a failure with an HTML comment, were audited entirely on the
#: exit that cannot go wrong. A double quote in a kwarg VALUE makes the Django
#: source those handlers assemble unparseable, which is how a failure branch
#: gets reached from an argument rather than from a settings change; before
#: #2434 it turns `test_the_only_unmarked_markup_return_is_the_named_limit`
#: red with all four of them as offenders.
_ARG_VECTORS: tuple[list[str], ...] = (
    [],
    ["x"],
    ["<em>cell</em>"],
    ['{"name":"col","attrs":{},"content":"<em>cell</em>"}'],
    ["slots.col.0"],
    ["slots.col.0.content"],
    ["p"],
    ["x", "y"],
    ['name=he said "hi"'],
)

#: A context those vectors can resolve against.
_ARG_CONTEXT = {
    "slots": {"col": [{"name": "col", "attrs": {}, "content": "<em>cell</em>"}]},
    "col": [{"name": "col", "attrs": {}, "content": "<em>cell</em>"}],
    "p": "<em>cell</em>",
}


class TestTheEnumerationReachesTheMarkupBranchesToo:
    """The audit gap #2423 names, closed (#2416).

    :class:`TestEveryRegisteredHandlerIsAccountedFor` calls every handler with
    ``render([], {})``. A handler that returns ``""`` for no arguments was
    therefore audited on a branch that CANNOT return markup — and #2421 is what
    that cost: ``render_slot`` returns ``""`` with no args, sat in the
    empty-string bucket, and its markup branch was never reached, so #2379
    escaped a slot's already-escaped content and every function component
    rendered its own markup as visible text.

    So the enumeration is re-run here with REPRESENTATIVE arguments. The
    measurement, on this build:

    * **18** of the 221 handlers return ``""`` for the no-argument call;
    * **4** of those 18 reach a non-empty return once given an argument —
      ``djust_markdown``, ``kbd``, ``render_slot``, ``static`` — so the
      no-argument audit could see nothing about them at all;
    * of every return reached, exactly one carries MARKUP without ``__html__``:
      ``render_slot``, which is #2423's own limit and is asserted as such
      below rather than left as a silence.

    (The three counts are what this build measures under the repo's own test
    settings; a differently-configured project can legitimately move them,
    because several handlers delegate to a Django tag whose availability
    depends on ``INSTALLED_APPS``.)

    The counts are not asserted — a handler added later legitimately moves
    them, and a count assertion would fail on the addition rather than on
    anything about safety. What IS asserted is the offender SET, so a NEW
    handler returning unmarked markup on an argument-bearing branch fails here.
    """

    @pytest.fixture(scope="class")
    def handlers(self) -> dict[str, tuple[str, object]]:
        return _registered_handlers()

    @staticmethod
    def _reached(handler, kind: str) -> list[tuple[list[str], str]]:
        """Every string return this handler produces over `_ARG_VECTORS`."""
        render = getattr(handler, "render", handler)
        out: list[tuple[list[str], str]] = []
        for vector in _ARG_VECTORS:
            try:
                got = (
                    render(vector, "<b>body</b>", dict(_ARG_CONTEXT))
                    if kind == "block"
                    else render(vector, dict(_ARG_CONTEXT))
                )
            except Exception:  # noqa: BLE001 — a handler needing different args
                continue
            if isinstance(got, str):
                out.append((vector, got))
        return out

    def test_arguments_reach_branches_the_no_arg_call_cannot(self, handlers) -> None:
        """The premise, measured rather than asserted from the issue.

        Non-vacuous by construction: if giving arguments reached nothing new,
        this file's whole audit-gap claim would be false and the set is empty.
        """
        newly_reached = set()
        for name, (kind, handler) in handlers.items():
            render = getattr(handler, "render", handler)
            try:
                base = render([], "<b>body</b>", {}) if kind == "block" else render([], {})
            except Exception:  # noqa: BLE001
                continue
            if not isinstance(base, str) or base != "":
                continue
            if any(got for _vector, got in self._reached(handler, kind)):
                newly_reached.add(name)
        assert "render_slot" in newly_reached, (
            "`render_slot` is the handler whose unreached markup branch cost "
            f"#2421; if it is not here the vectors are wrong: {sorted(newly_reached)}"
        )
        assert len(newly_reached) >= 3, sorted(newly_reached)

    def test_the_only_unmarked_markup_return_is_the_named_limit(self, handlers) -> None:
        """The audit's assertion, on the branches arguments unlock.

        `render_slot` is #2423: its Shape-3 scalar passthrough cannot tell a
        slot's already-escaped `.content` from a hostile bare context string,
        because the engine resolved both to an opaque string before the call,
        so it takes the escape. Over-escaping, never a leak — and named here so
        it is a limit rather than a silence.
        """
        offenders = {}
        for name, (kind, handler) in sorted(handlers.items()):
            for vector, got in self._reached(handler, kind):
                if hasattr(got, "__html__"):
                    continue
                if UNESCAPED_TAG.search(got):
                    offenders.setdefault(name, (vector, got[:90]))
        assert set(offenders) <= {"render_slot"}, (
            "these handlers return markup without `__html__` on an "
            f"argument-bearing branch, so the bridge renders it as escaped "
            f"text: { {k: v for k, v in offenders.items() if k != 'render_slot'} }"
        )


# ---------------------------------------------------------------------------
# What this does NOT close
# ---------------------------------------------------------------------------


class TestTheDivergencesThisUnmaskedAreNowCLOSED:
    """The two argument-channel defects this PR's first version left open.

    Both were MASKED by the raw return — the marker was lost on the way IN and
    the bridge emitted the result raw on the way OUT, so two wrongs cancelled
    and djust matched Django's live output for the wrong reason. They were
    pinned here as named limits and are now fixed in #2416, so these rows
    become PARITY assertions and name their successor rather than being
    deleted: a revert of #2416 must go red somewhere, and this file is where
    the divergence was first written down.

    Full coverage of the fix lives in
    `python/tests/test_tag_argument_safedata_2416.py`.
    """

    def test_a_marked_context_value_reaches_the_handler_as_SafeData(self) -> None:
        dj, du = both("{% e2379_cond p %}", {"p": mark_safe(XSS)})  # noqa: S308
        assert dj == XSS
        assert du == dj, "the SafeData marker was lost on the way IN again (#2416)"

    def test_a_quoted_literal_argument_LOSES_its_quotes(self) -> None:
        """Django's `Variable('"<b>"')` runs
        `mark_safe(unescape_string_literal(…))`, so the literal loses its
        quotes AND arrives as SafeData."""
        dj, du = both('{% e2379_ident "<b>" %}', {})
        assert dj == "<b>"
        assert du == dj, "the literal kept its quotes / lost its grant again (#2416)"


class TestTheDifferentialHasNoCtagExemptionAnyMore:
    """`unmasked()`'s `@ctag` arm is GONE, and its absence is the point (#2416).

    #2379 added it because a custom-tag cell had a second way of having agreed
    on the baseline: the input-side marker loss cancelled the raw return. The
    arm excused a cell whose new output was Django's escaped once more WHEN the
    `ct-cond` probe over the same input diverged on both builds.

    #2416 fixed the input-side loss, so `ct-cond` AGREES on any build carrying
    it and the arm's second condition can never hold again — it became a
    classifier that could only ever mask a future custom-tag regression, i.e. a
    second mechanism shadowing the first (#2233). Deleted rather than tested
    around, so a `@ctag` regression is now always REPORTED.
    """

    @staticmethod
    def _module():
        import importlib.util
        import pathlib

        path = (
            pathlib.Path(__file__).resolve().parents[2]
            / "scripts"
            / "filter-parity-differential.py"
        )
        spec = importlib.util.spec_from_file_location("_fpd_2379", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_a_ctag_cell_is_never_excused(self) -> None:
        """The exact rows the deleted arm used to excuse. All three are now
        reported, which is what a regression on this axis should be."""
        module = self._module()
        key = "s-marked"
        cond_twin = f"@ctag ct-cond\t{key}\tctag"
        cid = f"@ctag ct-ident\t{key}\tctag"
        base = {cond_twin: ["<b>", "&lt;b&gt;"], cid: ["<b>", "<b>"]}
        for after in (
            {cond_twin: ["<b>", "&lt;b&gt;"], cid: ["<b>", "&lt;b&gt;"]},
            {cond_twin: ["<b>", "&lt;b&gt;"], cid: ["<b>", "something else"]},
            {cond_twin: ["<b>", "<b>"], cid: ["<b>", "&lt;b&gt;"]},
        ):
            assert not module.unmasked(cid, base, after), after

    def test_the_generic_tag_arm_still_works(self) -> None:
        """Non-vacuity: deleting the `@ctag` arm must not have deleted the
        `{% tag %}`-operand rule #2325 added, which is a different mechanism
        and still load-bearing."""
        module = self._module()
        cid = "p|upper\ts-img\ttag-if"
        twin = "p|upper\ts-img"
        base = {twin: ["a", "b"]}
        after = {twin: ["a", "b"], cid: ["x", "y"]}
        assert module.unmasked(cid, base, after)

    def test_a_plain_variable_cell_is_unaffected(self) -> None:
        """A `{{ }}` cell has no mask to be behind."""
        module = self._module()
        assert not module.unmasked("p|upper\ts-img", {}, {})
