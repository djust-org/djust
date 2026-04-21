"""
Tests for `register_assign_tag_handler` — context-mutating custom tags.

An assign tag returns a dict that's merged into the template
context rather than emitting HTML. Subsequent sibling nodes see the
new variables.
"""

import pytest

from djust._rust import (
    RustLiveView,
    clear_assign_tag_handlers,
    has_assign_tag_handler,
    register_assign_tag_handler,
    unregister_assign_tag_handler,
)


class _StaticAssign:
    """Assign handler that always emits a fixed dict."""

    def __init__(self, updates):
        self._updates = updates

    def render(self, args, context):  # noqa: ARG002 — args/context unused in this stub
        return dict(self._updates)


class _EchoAssign:
    """Assign handler that emits the first arg under the second arg's name."""

    def render(self, args, context):  # noqa: ARG002
        # Args: {% echo 'greeting' name %}  -> {"name": "greeting"}
        if len(args) < 2:
            return {}
        value = args[0].strip("'\"")
        key = args[1].strip("'\"")
        return {key: value}


@pytest.fixture(autouse=True)
def _reset_registry():
    """Clear assign-tag handlers before and after each test."""
    clear_assign_tag_handlers()
    yield
    clear_assign_tag_handlers()


def _render(template: str, **state) -> str:
    view = RustLiveView(template)
    view.update_state(state)
    return view.render()


def test_assign_tag_mutates_context_for_sibling_nodes():
    """After an assign tag, subsequent variables see the assigned vars."""
    register_assign_tag_handler("set_hi", _StaticAssign({"greeting": "hello"}))
    html = _render("{% set_hi %}{{ greeting }}!")
    assert html == "hello!"


def test_assign_tag_multiple_args_resolved_against_context():
    """Args are resolvable from context (just like custom tags)."""
    register_assign_tag_handler("echo", _EchoAssign())
    # `body` resolves to the string literal "body"; `varname` is
    # looked up in context and becomes "label".
    html = _render(
        '{% echo "body" varname %}[{{ label }}]',
        varname="label",
    )
    assert html == "[body]"


def test_empty_dict_is_noop():
    """A handler returning {} must render as empty string without errors."""
    register_assign_tag_handler("noop", _StaticAssign({}))
    html = _render(
        "before{% noop %}{{ missing }}after",
    )
    assert html == "beforeafter"


def test_has_assign_tag_handler_roundtrip():
    """register / has / unregister / has roundtrip behaves as expected."""
    assert not has_assign_tag_handler("mytag")
    register_assign_tag_handler("mytag", _StaticAssign({"x": 1}))
    assert has_assign_tag_handler("mytag")
    assert unregister_assign_tag_handler("mytag") is True
    assert not has_assign_tag_handler("mytag")


def test_assign_tag_inside_for_loop_mutates_per_iteration():
    """Each loop iteration should be able to re-assign the context."""

    class _CounterDouble:
        """Doubles the current loop item and assigns it as `doubled`."""

        def render(self, args, context):
            val = context.get("n", 0)
            if isinstance(val, (int, float)):
                return {"doubled": val * 2}
            return {"doubled": 0}

    register_assign_tag_handler("double", _CounterDouble())
    html = _render(
        "{% for n in nums %}{% double %}[{{ doubled }}]{% endfor %}",
        nums=[1, 2, 3],
    )
    assert html == "[2][4][6]"


def test_assign_tag_emits_no_html():
    """Assign tag's own slot in the template is empty."""
    register_assign_tag_handler("mk", _StaticAssign({"v": "ok"}))
    html = _render(
        "pre|{% mk %}|post|{{ v }}",
    )
    # The `{% mk %}` slot is empty — no visible text there.
    assert html == "pre||post|ok"
