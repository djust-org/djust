"""``__djust_typed_args__`` is the engine's key, never the application's (#3773).

The typed-operand channel (#3756) hands a handler that declares ``TYPED_ARGS = True`` its resolved
``key=<expression>`` operands under the reserved context key ``__djust_typed_args__``
(``renderer.rs::TYPED_ARGS_KEY``). The engine inserted the key only when it had a typed operand to
hand over. A tag whose operands are all quoted literals has none, so nothing was inserted, and an
application context that happened to carry a top-level key of that name reached the handler in the
engine's place: ``{% json_viewer data="x" class="plain" %}`` rendered the application's
``{"data": ..., "class": ...}``. Output stayed escaped (low severity), but the channel is an
engine-to-handler contract and an application value must not stand in for it.

The sinks, each pinned below:

* the flat map of the view's top-level context (``bridged_context_map``),
* the raw-Python sidecar (``build_py_context`` / ``build_py_context_memo``: objects that cannot
  cross as values, laid over the flat map),
* the live frames a bridged tag reads (``build_py_context_memo`` over ``Context::bridged_py_dict``).

Each class proves one mechanism and carries its own gate-off in the PR; a test that is green with
the mechanism removed is a finding, not a pass.

Refs #3773, #3756, #1468.
"""

from __future__ import annotations

import datetime

import pytest

from djust import _rust
from djust.components.rust_handlers import TYPED_ARGS_KEY

pytestmark = pytest.mark.pr_tier

RESERVED = "__djust_typed_args__"


class _Opaque:
    """An object that cannot cross as a value, so the engine keeps it on the raw-Python sidecar."""

    def __str__(self) -> str:
        return "opaque"


#: What an application might put under the reserved name: a dict (the shape ``json_viewer`` reads),
#: and every non-dict shape ``_typed_args`` could be handed.
USER_VALUES = [
    pytest.param({"data": {"evil": 1}, "class": 'evil"x'}, id="dict"),
    pytest.param({}, id="empty-dict"),
    pytest.param("a string", id="str"),
    pytest.param(["data", "class"], id="list"),
    pytest.param(7, id="int"),
    pytest.param(None, id="none"),
    pytest.param(_Opaque(), id="opaque-object"),
]


@pytest.fixture
def probes():
    """Register one probe per handler kind; each records whether the reserved key reached it."""
    seen: dict[str, object] = {}

    class Typed:
        TYPED_ARGS = True

        def render(self, args, context):
            seen["typed"] = context.get(TYPED_ARGS_KEY, "<absent>")
            seen["typed_has_key"] = TYPED_ARGS_KEY in context
            return ""

    class Plain:
        def render(self, args, context):
            seen["plain_has_key"] = TYPED_ARGS_KEY in context
            return ""

    class Block:
        def render(self, args, content, context):
            seen["block_has_key"] = TYPED_ARGS_KEY in context
            return content

    class Assign:
        def render(self, args, context):
            seen["assign_has_key"] = TYPED_ARGS_KEY in context
            return {}

    class Bridged:
        """A ``{% load %}``-bridge-shaped handler: its context is the LIVE frames."""

        RETURNS_BINDINGS = True

        def render(self, args, context):
            seen["bridged_has_key"] = TYPED_ARGS_KEY in context
            return "", {}

    class RawBlock:
        """A raw-block handler (``{% blocktranslate %}`` is the shipped one): the body crosses as
        un-rendered source and the handler must declare ``RETURNS_BINDINGS``."""

        RETURNS_BINDINGS = True

        def render(self, args, body, context):
            seen["raw_block_has_key"] = TYPED_ARGS_KEY in context
            return body, {}

    class LazyBody:
        """The two-phase block contract (#2658): ``before_body`` then ``after_body``, each handed
        its own context dict."""

        LAZY_BODY = True

        def before_body(self, args, context):
            seen["before_body_has_key"] = TYPED_ARGS_KEY in context
            return None, object()

        def after_body(self, args, content, context, state):
            seen["after_body_has_key"] = TYPED_ARGS_KEY in context
            return content

    _rust.register_tag_handler("bridged_probe_3773", Bridged())
    _rust.register_raw_block_tag_handler("raw_probe_3773", "endraw_probe_3773", RawBlock())
    _rust.register_block_tag_handler("lazy_probe_3773", "endlazy_probe_3773", LazyBody())
    _rust.register_tag_handler("typed_probe_3773", Typed())
    _rust.register_tag_handler("plain_probe_3773", Plain())
    _rust.register_block_tag_handler("block_probe_3773", "endblock_probe_3773", Block())
    _rust.register_assign_tag_handler("assign_probe_3773", Assign())
    try:
        yield seen
    finally:
        for name in ("typed_probe_3773", "plain_probe_3773", "bridged_probe_3773"):
            _rust.unregister_tag_handler(name)
        for unregister, name in (
            (_rust.unregister_block_tag_handler, "block_probe_3773"),
            (_rust.unregister_block_tag_handler, "lazy_probe_3773"),
            (_rust.unregister_assign_tag_handler, "assign_probe_3773"),
            (_rust.unregister_raw_block_tag_handler, "raw_probe_3773"),
        ):
            unregister(name)


class TestAUserKeyNeverStandsInForTheChannel:
    """The reported reproduction: a tag with no typed operand, a context key of the reserved name."""

    @pytest.mark.parametrize("planted", USER_VALUES)
    @pytest.mark.parametrize(
        "src",
        [
            pytest.param('{% typed_probe_3773 data="lit" class="plain" %}', id="literals-only"),
            pytest.param("{% typed_probe_3773 %}", id="no-operands"),
            pytest.param("{% typed_probe_3773 a=missing %}", id="unresolved-operand"),
        ],
    )
    def test_no_typed_operand_means_no_channel(self, probes, src, planted):
        _rust.render_template(src, {RESERVED: planted})
        assert probes["typed_has_key"] is False, (
            f"an application value stood in for the engine's channel: {probes['typed']!r}"
        )

    @pytest.mark.parametrize("planted", USER_VALUES)
    def test_a_resolved_operand_is_the_engines_not_the_applications(self, probes, planted):
        when = datetime.datetime(2024, 3, 15, 11, 0, 0, tzinfo=datetime.timezone.utc)
        _rust.render_template(
            '{% typed_probe_3773 a=when d="lit" %}', {RESERVED: planted, "when": when}
        )
        typed = probes["typed"]
        assert isinstance(typed, dict) and set(typed) == {"a"}, typed
        assert typed["a"] == when and type(typed["a"]) is datetime.datetime

    def test_the_user_key_is_dropped_even_when_it_is_the_only_context_key(self, probes):
        _rust.render_template("{% typed_probe_3773 x=1 %}", {RESERVED: {"x": "from the app"}})
        assert probes["typed"] == {"x": 1}, probes["typed"]


class TestNoHandlerKindSeesTheUserKey:
    """The same removal covers every call path, not only the inline typed one.

    One test per handler kind, so a gate-off of one scrub names the kind it reaches rather than
    failing a single test with four asserts behind it (#3787). Every kind is also rendered
    against every planted shape, the opaque object included, which rides the raw-Python sidecar
    instead of the flat map.
    """

    #: ``(probe key, template)``: one render per handler kind, nothing else in the template, so a
    #: red test cannot be blamed on a neighbouring tag.
    KINDS = [
        pytest.param("plain_has_key", "{% plain_probe_3773 a=1 %}", id="plain-inline"),
        pytest.param("bridged_has_key", "{% bridged_probe_3773 a=1 %}", id="bridged-inline"),
        pytest.param(
            "block_has_key", "{% block_probe_3773 %}x{% endblock_probe_3773 %}", id="block"
        ),
        pytest.param("assign_has_key", "{% assign_probe_3773 %}", id="assign"),
        pytest.param(
            "raw_block_has_key", "{% raw_probe_3773 %}x{% endraw_probe_3773 %}", id="raw-block"
        ),
        pytest.param(
            "before_body_has_key",
            "{% lazy_probe_3773 %}x{% endlazy_probe_3773 %}",
            id="lazy-before_body",
        ),
        pytest.param(
            "after_body_has_key",
            "{% lazy_probe_3773 %}x{% endlazy_probe_3773 %}",
            id="lazy-after_body",
        ),
    ]

    @pytest.mark.parametrize("planted", USER_VALUES)
    @pytest.mark.parametrize(("probe_key", "src"), KINDS)
    def test_the_handler_is_not_handed_the_user_key(self, probes, probe_key, src, planted):
        _rust.render_template(src, {RESERVED: planted})
        assert probe_key in probes, f"the probe never ran: {sorted(probes)} (a vacuous pass)"
        assert probes[probe_key] is False, (
            f"{probe_key}: an application value under {RESERVED!r} reached the handler"
        )


class TestTheSidecar:
    """An object that cannot cross as a value rides the raw-Python sidecar, laid over the map."""

    def test_an_opaque_object_under_the_name_does_not_reach_the_handler(self, probes):
        # With a typed operand the engine's channel is in the map; the sidecar is laid over the
        # map, so a sidecar copy of the key would REPLACE the engine's value.
        _rust.render_template(
            "{% typed_probe_3773 x=1 %}", {RESERVED: _Opaque(), "keep": _Opaque()}
        )
        assert probes["typed"] == {"x": 1}, probes["typed"]

    def test_the_sidecar_itself_is_exercised(self, probes):
        """Looking is not the same as finding: ``keep`` reaches the handler through the sidecar,
        so the test above exercises the overlay and not an empty one."""
        seen: dict[str, object] = {}

        class Probe:
            def render(self, args, context):
                seen["keep"] = context.get("keep")
                return ""

        _rust.register_tag_handler("sidecar_control_3773", Probe())
        try:
            keep = _Opaque()
            _rust.render_template("{% sidecar_control_3773 %}", {"keep": keep})
        finally:
            _rust.unregister_tag_handler("sidecar_control_3773")
        assert seen["keep"] is keep, "the control: an opaque object must ride the sidecar"
