"""A body may mutate registrations without changing its enclosing handler."""

import pytest
from django.utils.safestring import mark_safe
from djust import _rust


@pytest.mark.parametrize("mutation", ["replace", "remove"])
def test_lazy_body_keeps_phase_one_handler(mutation):
    calls = []
    state = object()
    name = "identity2707"
    mutator = "mutate2707"

    class Original:
        LAZY_BODY = True
        RESOLVE_ARG_POSITIONS = frozenset()

        def before_body(self, args, context):
            calls.append("before")
            return None, state

        def after_body(self, args, content, context, received):
            assert received is state
            calls.append("after-original")
            return mark_safe(content)

    class Replacement(Original):
        def after_body(self, args, content, context, received):
            raise AssertionError("Replacement received another handler's state")

    class Mutator:
        RESOLVE_ARG_POSITIONS = frozenset()

        def render(self, args, context):
            calls.append("body")
            if mutation == "replace":
                _rust.register_block_tag_handler(name, "end" + name, Replacement())
            else:
                _rust.unregister_block_tag_handler(name)
            return "body"

    _rust.register_block_tag_handler(name, "end" + name, Original())
    _rust.register_tag_handler(mutator, Mutator())
    try:
        source = "{% identity2707 %}{% mutate2707 %}{% endidentity2707 %}"
        assert _rust.render_template(source, {}) == "body"
        assert calls == ["before", "body", "after-original"]
    finally:
        _rust.unregister_block_tag_handler(name)
        _rust.unregister_tag_handler(mutator)
