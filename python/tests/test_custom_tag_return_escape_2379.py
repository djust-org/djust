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
    return (
        DjangoTemplate(source, engine=Engine.get_default()).render(DjangoContext(dict(ctx))),
        _rust.render_template(source, dict(ctx)),
    )


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


# ---------------------------------------------------------------------------
# What this does NOT close
# ---------------------------------------------------------------------------


class TestKnownDivergencesThisUnmasks:
    """Two argument-channel defects that were MASKED by the raw return.

    Both are djust being STRICTER than Django — over-escaping, never a leak —
    and both are #2290's finding on the argument side rather than this issue's.
    They used to agree with Django by coincidence: the marker was lost on the
    way IN and the bridge emitted the result raw on the way OUT, so two wrongs
    cancelled. Pinned so they are a named limit rather than a silent one.
    """

    def test_a_marked_context_value_reaches_the_handler_as_a_bare_str(self) -> None:
        dj, du = both("{% e2379_cond p %}", {"p": mark_safe(XSS)})  # noqa: S308
        assert dj == XSS
        assert du == "&lt;img src=x onerror=alert(1)&gt;"
        assert not UNESCAPED_TAG.search(du), "over-escaping, never a leak"

    def test_a_quoted_literal_argument_keeps_its_quotes(self) -> None:
        """Django's `Variable('"<b>"')` runs
        `mark_safe(unescape_string_literal(…))`, so the literal loses its
        quotes AND arrives as SafeData. djust passes the token verbatim."""
        dj, du = both('{% e2379_ident "<b>" %}', {})
        assert dj == "<b>"
        assert du == "&quot;&lt;b&gt;&quot;"


class TestTheDifferentialCanTELLThatUnmaskingApart:
    """The `@ctag` arm `unmasked()` grew for this change.

    A `@ctag` cell that agreed before and disagrees now is normally a
    regression. The marked-context-value cell has a third way to have agreed:
    the marker was lost on the way IN and the raw return cancelled it on the
    way OUT, so djust matched Django's live output for the wrong reason.

    Without the arm, the differential reports that cell as `REGRESSIONS: 1`
    and exits non-zero — which would train the next reader to ignore the
    number, the failure mode #2325's own comment warns about.
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

    def test_the_unmasking_needs_BOTH_conditions(self) -> None:
        """Not an exemption keyed on the input: a cell whose new output is NOT
        Django's escaped once more is still reported, even over the same
        marked input."""
        module = self._module()
        key = "s-marked"
        cond_twin = f"@ctag ct-cond\t{key}\tctag"
        cid = f"@ctag ct-ident\t{key}\tctag"
        # The twin diverges on both builds — condition 2 holds.
        base = {cond_twin: ["<b>", "&lt;b&gt;"], cid: ["<b>", "<b>"]}

        # Condition 1 holds: djust's new output IS Django's escaped once more.
        after_ok = {cond_twin: ["<b>", "&lt;b&gt;"], cid: ["<b>", "&lt;b&gt;"]}
        assert module.unmasked(cid, base, after_ok)

        # Condition 1 FAILS: a different new output is a real regression.
        after_bad = {cond_twin: ["<b>", "&lt;b&gt;"], cid: ["<b>", "something else"]}
        assert not module.unmasked(cid, base, after_bad)

        # Condition 2 FAILS: the twin agrees, so the marker was NOT already
        # lost and this change is what broke the cell.
        after_no_twin = {cond_twin: ["<b>", "<b>"], cid: ["<b>", "&lt;b&gt;"]}
        assert not module.unmasked(cid, base, after_no_twin)

    def test_a_non_ctag_cell_is_unaffected(self) -> None:
        """The `{{ }}` and tag arms keep their own rule."""
        module = self._module()
        assert not module.unmasked("p|upper\ts-img", {}, {})
