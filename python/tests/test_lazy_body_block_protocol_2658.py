"""The ``LAZY_BODY`` two-phase block-handler protocol (#2658).

``{% cache %}`` is its first and, today, only caller, so the tag's own
behaviour is pinned next door in ``test_cache_tag_django_parity_2658.py``. This
file pins the PROTOCOL: what a handler must declare, what the renderer promises
about ordering and about the state it carries, what it refuses, and that a
handler which does NOT declare it is called exactly as before.

The contract, as ``read_lazy_body`` in ``crates/djust_templates/src/registry.rs``
states it:

* ``LAZY_BODY = True`` on the handler;
* ``before_body(args, context) -> (str | None, state)`` — called BEFORE the
  children render; a ``str`` ends the render and the body never runs;
* ``after_body(args, content, context, state) -> str`` — reached only when
  ``before_body`` returned ``None``, with the state it handed back;
* refused at registration in combination with ``RETURNS_BINDINGS`` /
  ``WANTS_AUTOESCAPE``, or with either phase method missing. Refusing is what
  makes the contract total rather than half-built: those combinations have no
  defined meaning and no caller.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import pytest

pytest.importorskip("django")

from djust import _rust  # noqa: E402


def _render(source: str, ctx: Optional[Dict[str, Any]] = None) -> str:
    return _rust.render_template(source, dict(ctx or {}))


class _Recorder:
    """A tag that records every render, for use inside a lazy body."""

    RESOLVE_ARG_POSITIONS: frozenset = frozenset()

    def __init__(self) -> None:
        self.calls: List[str] = []

    def render(self, args: List[str], context: Dict[str, Any]) -> str:
        self.calls.append("body")
        return "B"


class _Lazy:
    """A minimal `LAZY_BODY` handler whose answer the test chooses."""

    LAZY_BODY: bool = True
    RESOLVE_ARG_POSITIONS: frozenset = frozenset()

    def __init__(self, answer: Optional[str], state: Any = None) -> None:
        self.answer = answer
        self.state = object() if state is None else state
        self.events: List[str] = []
        self.seen: Dict[str, Any] = {}

    def before_body(self, args: List[str], context: Dict[str, Any]) -> Tuple[Optional[str], Any]:
        self.events.append("before")
        self.seen["before_args"] = list(args)
        self.seen["before_context"] = dict(context)
        return self.answer, self.state

    def after_body(self, args: List[str], content: str, context: Dict[str, Any], state: Any) -> str:
        self.events.append("after")
        self.seen["after_args"] = list(args)
        self.seen["content"] = content
        self.seen["state"] = state
        return f"[{content}]"


@pytest.fixture
def probe() -> Any:
    """A body-side tag whose renders are counted."""
    rec = _Recorder()
    _rust.register_tag_handler("lb2658_body", rec)
    try:
        yield rec
    finally:
        _rust.unregister_tag_handler("lb2658_body")


def _register(handler: Any, name: str = "lb2658") -> None:
    _rust.register_block_tag_handler(name, "end" + name, handler)


@pytest.fixture
def unregister() -> Any:
    """Unregister whatever a test registered, whatever the outcome."""
    names: List[str] = []
    yield names.append
    for name in names:
        _rust.unregister_block_tag_handler(name)


class TestPhaseOneCanDeclineTheBody:
    def test_an_answer_from_before_body_means_the_body_never_renders(
        self, probe: Any, unregister: Any
    ) -> None:
        handler = _Lazy(answer="HIT")
        unregister("lb2658")
        _register(handler)

        out = _render("{% lb2658 %}{% lb2658_body %}{% endlb2658 %}")

        assert out == "HIT"
        assert probe.calls == [], "the body rendered despite before_body answering"
        assert handler.events == ["before"], "after_body must not be reached on an answer"

    def test_none_from_before_body_renders_the_body_and_calls_after_body(
        self, probe: Any, unregister: Any
    ) -> None:
        handler = _Lazy(answer=None)
        unregister("lb2658")
        _register(handler)

        out = _render("{% lb2658 %}{% lb2658_body %}{% endlb2658 %}")

        assert out == "[B]"
        assert probe.calls == ["body"]
        assert handler.events == ["before", "after"]
        assert handler.seen["content"] == "B"

    def test_before_body_runs_BEFORE_the_body(self, unregister: Any) -> None:
        """Ordering, not just presence — the point of the protocol.

        The body's tag appends to the SAME list the handler appends to, so the
        order of the three entries is the order the renderer ran them in.
        """
        handler = _Lazy(answer=None)

        class Ordered:
            RESOLVE_ARG_POSITIONS: frozenset = frozenset()

            def render(self, args: List[str], context: Dict[str, Any]) -> str:
                handler.events.append("body")
                return "B"

        _rust.register_tag_handler("lb2658_ordered", Ordered())
        unregister("lb2658")
        _register(handler)
        try:
            _render("{% lb2658 %}{% lb2658_ordered %}{% endlb2658 %}")
        finally:
            _rust.unregister_tag_handler("lb2658_ordered")

        assert handler.events == ["before", "body", "after"]


class TestTheStateIsCarriedAcrossTheBody:
    def test_after_body_receives_the_very_object_before_body_returned(
        self, unregister: Any
    ) -> None:
        """Identity, not equality: the renderer carries the object, opaque.

        This is what lets a handler avoid redoing phase one's work —
        ``{% cache %}`` hands over its ``(backend, key, expire_time)`` rather
        than resolving every operand a second time.
        """
        sentinel = {"key": object()}
        handler = _Lazy(answer=None, state=sentinel)
        unregister("lb2658")
        _register(handler)

        _render("{% lb2658 %}x{% endlb2658 %}")

        assert handler.seen["state"] is sentinel

    def test_a_none_state_is_carried_unchanged(self, unregister: Any) -> None:
        """``None`` is a legal state — only the OUTPUT's ``None`` is a signal."""
        handler = _Lazy(answer=None, state="__none__")
        handler.state = None
        unregister("lb2658")
        _register(handler)

        _render("{% lb2658 %}x{% endlb2658 %}")

        assert handler.events == ["before", "after"], "a None state must not read as an answer"
        assert handler.seen["state"] is None


class TestBothPhasesSeeTheSameArgsAndContext:
    def test_args_and_context_are_identical_in_both_phases(self, unregister: Any) -> None:
        """The pre-body snapshot, in both phases.

        The eager path resolves args AFTER the body; the lazy path must resolve
        them BEFORE, or a handler that declined the body would key off a
        context the body never got to write. Both phases then see the same
        thing, so a handler that does recompute gets the same answer.
        """
        handler = _Lazy(answer=None)
        unregister("lb2658")
        _register(handler)

        _render("{% lb2658 v %}x{% endlb2658 %}", {"v": "seen"})

        assert handler.seen["before_args"] == handler.seen["after_args"] == ["v"]
        assert handler.seen["before_context"]["v"] == "seen"


class TestEscapingIsTheSameRuleInBothPhases:
    @pytest.mark.parametrize("phase", ["before", "after"])
    def test_a_plain_str_return_is_escaped(self, unregister: Any, phase: str) -> None:
        class Plain:
            LAZY_BODY: bool = True
            RESOLVE_ARG_POSITIONS: frozenset = frozenset()

            def before_body(
                self, args: List[str], context: Dict[str, Any]
            ) -> Tuple[Optional[str], Any]:
                return ("<b>hi</b>" if phase == "before" else None), None

            def after_body(
                self, args: List[str], content: str, context: Dict[str, Any], state: Any
            ) -> str:
                return "<b>hi</b>"

        unregister("lb2658")
        _register(Plain())
        assert _render("{% lb2658 %}x{% endlb2658 %}") == "&lt;b&gt;hi&lt;/b&gt;"

    @pytest.mark.parametrize("phase", ["before", "after"])
    def test_a_safestring_return_is_inserted_raw(self, unregister: Any, phase: str) -> None:
        from django.utils.safestring import mark_safe

        class Safe:
            LAZY_BODY: bool = True
            RESOLVE_ARG_POSITIONS: frozenset = frozenset()

            def before_body(
                self, args: List[str], context: Dict[str, Any]
            ) -> Tuple[Optional[str], Any]:
                return (mark_safe("<b>hi</b>") if phase == "before" else None), None

            def after_body(
                self, args: List[str], content: str, context: Dict[str, Any], state: Any
            ) -> str:
                return mark_safe("<b>hi</b>")

        unregister("lb2658")
        _register(Safe())
        assert _render("{% lb2658 %}x{% endlb2658 %}") == "<b>hi</b>"


class TestExceptionsCrossWhole:
    @pytest.mark.parametrize("phase,message", [("before_body", "one"), ("after_body", "two")])
    def test_a_handler_exception_keeps_its_type(
        self, unregister: Any, phase: str, message: str
    ) -> None:
        """As the single-phase sidecar path does since #2563."""

        class Boom:
            LAZY_BODY: bool = True
            RESOLVE_ARG_POSITIONS: frozenset = frozenset()

            def before_body(
                self, args: List[str], context: Dict[str, Any]
            ) -> Tuple[Optional[str], Any]:
                if phase == "before_body":
                    raise KeyError("phase one")
                return None, None

            def after_body(
                self, args: List[str], content: str, context: Dict[str, Any], state: Any
            ) -> str:
                raise KeyError("phase two")

        unregister("lb2658")
        _register(Boom())
        with pytest.raises(KeyError) as caught:
            _render("{% lb2658 %}x{% endlb2658 %}")
        assert message in str(caught.value)

    def test_a_body_that_raises_never_reaches_after_body(self, unregister: Any) -> None:
        handler = _Lazy(answer=None)

        class Boom:
            RESOLVE_ARG_POSITIONS: frozenset = frozenset()

            def render(self, args: List[str], context: Dict[str, Any]) -> str:
                raise RuntimeError("body blew up")

        _rust.register_tag_handler("lb2658_boom", Boom())
        unregister("lb2658")
        _register(handler)
        try:
            with pytest.raises(Exception) as caught:
                _render("{% lb2658 %}{% lb2658_boom %}{% endlb2658 %}")
        finally:
            _rust.unregister_tag_handler("lb2658_boom")

        assert "body blew up" in str(caught.value)
        assert handler.events == ["before"], "after_body ran on a body that raised"


class TestBeforeBodyMustReturnAPair:
    def test_a_bare_string_return_is_a_clear_error(self, unregister: Any) -> None:
        class Bad:
            LAZY_BODY: bool = True
            RESOLVE_ARG_POSITIONS: frozenset = frozenset()

            def before_body(self, args: List[str], context: Dict[str, Any]) -> str:
                return "not a pair"

            def after_body(
                self, args: List[str], content: str, context: Dict[str, Any], state: Any
            ) -> str:
                return content

        unregister("lb2658")
        _register(Bad())
        with pytest.raises(Exception) as caught:
            _render("{% lb2658 %}x{% endlb2658 %}")
        assert "before_body() must return a 2-tuple" in str(caught.value)


class TestRegistrationRefusesAnIncompleteContract:
    """A total contract, refused at REGISTRATION rather than half-honoured.

    Each combination below has no defined meaning and no caller; letting one
    register would mean discovering it at render time, on someone's page.
    """

    @pytest.mark.parametrize("missing", ["before_body", "after_body"])
    def test_lazy_body_without_both_phases_is_refused(self, missing: str) -> None:
        body = {
            "LAZY_BODY": True,
            "before_body": lambda self, a, c: (None, None),
            "after_body": lambda self, a, b, c, s: b,
        }
        del body[missing]
        handler = type("Half", (), body)()

        with pytest.raises(TypeError) as caught:
            _rust.register_block_tag_handler("lb2658_half", "endlb2658_half", handler)
        assert "LAZY_BODY" in str(caught.value) and missing in str(caught.value)
        assert not _rust.has_block_tag_handler("lb2658_half")

    @pytest.mark.parametrize("capability", ["RETURNS_BINDINGS", "WANTS_AUTOESCAPE"])
    def test_lazy_body_with_an_unsupported_capability_is_refused(self, capability: str) -> None:
        handler = type(
            "Combo",
            (),
            {
                "LAZY_BODY": True,
                capability: True,
                "before_body": lambda self, a, c: (None, None),
                "after_body": lambda self, a, b, c, s: b,
            },
        )()

        with pytest.raises(TypeError) as caught:
            _rust.register_block_tag_handler("lb2658_combo", "endlb2658_combo", handler)
        assert capability in str(caught.value)
        assert not _rust.has_block_tag_handler("lb2658_combo")

    def test_a_lazy_handler_needs_no_render_method(self, unregister: Any) -> None:
        """It is never called through ``render``, so requiring one would only
        get a shim written that nothing calls (#1646)."""
        handler = _Lazy(answer="HIT")
        assert not hasattr(handler, "render")

        unregister("lb2658")
        _register(handler)
        assert _render("{% lb2658 %}x{% endlb2658 %}") == "HIT"

    def test_a_non_lazy_handler_still_needs_render(self) -> None:
        """The historical requirement, unchanged for everyone else."""
        handler = type("NoRender", (), {})()
        with pytest.raises(TypeError) as caught:
            _rust.register_block_tag_handler("lb2658_norender", "endlb2658_norender", handler)
        assert "must have a 'render' method" in str(caught.value)


class TestANonLazyHandlerIsUnaffected:
    def test_the_single_phase_contract_still_gets_a_rendered_body(
        self, probe: Any, unregister: Any
    ) -> None:
        """No ``LAZY_BODY``: ``render(args, content, context)``, body first."""

        class Eager:
            RESOLVE_ARG_POSITIONS: frozenset = frozenset()

            def __init__(self) -> None:
                self.seen: List[str] = []

            def render(self, args: List[str], content: str, context: Dict[str, Any]) -> str:
                self.seen.append(content)
                return f"({content})"

        handler = Eager()
        unregister("lb2658_eager")
        _rust.register_block_tag_handler("lb2658_eager", "endlb2658_eager", handler)

        out = _render("{% lb2658_eager %}{% lb2658_body %}{% endlb2658_eager %}")

        assert out == "(B)"
        assert handler.seen == ["B"]
        assert probe.calls == ["body"], "the eager path must still render the body"

    @pytest.mark.parametrize("value", [False, None, 0])
    def test_a_falsy_lazy_body_declaration_is_the_eager_path(
        self, unregister: Any, value: Any
    ) -> None:
        """Absent OR falsy = the historical contract, as every other
        capability reader treats it."""

        class Falsy:
            RESOLVE_ARG_POSITIONS: frozenset = frozenset()
            LAZY_BODY = value

            def render(self, args: List[str], content: str, context: Dict[str, Any]) -> str:
                return f"({content})"

        unregister("lb2658_falsy")
        _rust.register_block_tag_handler("lb2658_falsy", "endlb2658_falsy", Falsy())
        assert _render("{% lb2658_falsy %}x{% endlb2658_falsy %}") == "(x)"
