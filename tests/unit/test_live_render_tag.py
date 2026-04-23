"""Unit tests for the {% live_render %} template tag — Phase A of Sticky LiveViews (v0.6.0).

Assertion discipline: every HTML assertion parses the output via
:class:`html.parser.HTMLParser` and walks the collected attribute list.
Substring assertions like ``'data-djust-embedded="X"' in out`` are
forbidden — they mask attribute-injection bugs (see the fix pass that
rewrote ``_stamp_view_id`` after a substring-only test regime hid a
critical injection-outside-tag defect). A small ``_parse_attrs`` helper
maps tag name + index to its attribute dict.

Covers the 10 cases from the plan plus regression cases for the six
critical defects found in review:

1.  Dotted path resolution.
2.  Subclass validation (non-LiveView + unresolvable path).
3.  Unique ``view_id`` per embed.
4.  Child registered on parent.
5.  ``data-djust-embedded`` stamped inside event-bearing tags (the
    attribute MUST live on the element's attributes, not as text
    content preceding the tag — this is the fix for the original
    "regex injects outside the tag" bug).
6.  Consumer-level dispatch routes events by ``view_id``.
7.  Parent + child render with independent context state.
8.  Tag kwargs reach child ``mount``.
9.  ``start_async`` is isolated to the child.
10. WS disconnect cleanup invokes ``_unregister_child`` per child.

Regression cases:

* ``test_data_djust_embedded_is_inside_tag_not_text_content`` — the
  attribute appears in element's attrib dict, not as PCDATA.
* ``test_view_id_not_used_as_dom_attribute`` — the legacy ``view_id``
  HTML attribute name (invalid under HTML5 attribute-name rules
  because of underscore / mixed-case quirks) is gone from all emitted
  tags.
* ``test_nested_live_render_each_child_distinct_embed_id`` — a child
  that itself embeds a grandchild produces two distinct data-djust-
  embedded spans.
* ``test_kwargs_view_id_is_honored`` — passing ``view_id=`` as a tag
  kwarg pins the id instead of the auto-generated stamp.
* ``test_idempotent_re_render_distinct_ids`` — rendering the same
  template twice in one pass emits distinct view_ids, not duplicate
  stamps.
* ``test_script_blocks_are_not_stamped`` — ``<script>`` / ``<!-- -->``
  spans are preserved untouched.
* ``test_quoted_attribute_values_with_gt_are_preserved`` — regex-in-
  attrs fix (empty values, embedded ``>``).
* ``test_live_render_denies_unauthorized_child`` — ``check_view_auth``
  is invoked and refuses to mount a permission-gated child.
* ``test_live_render_allowed_modules_blocks_disallowed_path`` — the
  ``DJUST_LIVE_RENDER_ALLOWED_MODULES`` allowlist.
"""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Dict, List, Tuple

import pytest
from django.core.exceptions import PermissionDenied
from django.template import Context, Template, TemplateSyntaxError
from django.test import override_settings

from djust.live_view import LiveView


# ---------------------------------------------------------------------------
# HTML attribute parsing helpers — used in every rendered-output assertion.
# ---------------------------------------------------------------------------


class _AttrCollector(HTMLParser):
    """Collect (tag, attrs_dict) pairs from every start + startend tag.

    ``attrs`` preserves the last value for duplicate attribute names (same
    as a browser) but we also stash the raw list so tests can count.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        # List of (tag_name, attrs_dict) in document order.
        self.elements: List[Tuple[str, Dict[str, str]]] = []
        # Concatenated text data — used to verify stamps do NOT leak as
        # PCDATA.
        self.text_data: List[str] = []
        # Comment contents (raw) — verify stamps aren't inserted inside
        # HTML comments.
        self.comments: List[str] = []
        # Capture script/style element bodies — verify stamps aren't
        # injected inside <script>.
        self.scripts: List[str] = []
        self._in_script = False

    def handle_starttag(self, tag, attrs):  # noqa: D401 — stdlib hook
        d: Dict[str, str] = {}
        for name, value in attrs:
            d[name] = value if value is not None else ""
        self.elements.append((tag, d))
        if tag == "script":
            self._in_script = True

    def handle_startendtag(self, tag, attrs):  # self-closing
        d = {name: (value if value is not None else "") for name, value in attrs}
        self.elements.append((tag, d))

    def handle_endtag(self, tag):
        if tag == "script":
            self._in_script = False

    def handle_data(self, data):
        if self._in_script:
            self.scripts.append(data)
        else:
            self.text_data.append(data)

    def handle_comment(self, data):
        self.comments.append(data)


def _parse(html: str) -> _AttrCollector:
    p = _AttrCollector()
    p.feed(html)
    p.close()
    return p


def _attr_values(html: str, attr: str) -> List[str]:
    """Return the list of ``attr`` values across every element in ``html``."""
    tree = _parse(html)
    return [attrs[attr] for _tag, attrs in tree.elements if attr in attrs]


def _elements_with_attr(html: str, attr: str) -> List[Tuple[str, Dict[str, str]]]:
    tree = _parse(html)
    return [(tag, attrs) for tag, attrs in tree.elements if attr in attrs]


# ---------------------------------------------------------------------------
# Module-level child classes — must live at module scope so that dotted-path
# import resolution via ``django.utils.module_loading.import_string`` can
# find them by ``tests.unit.test_live_render_tag.<ClassName>``.
# ---------------------------------------------------------------------------


class _ChildLiveView(LiveView):
    """Embedded child — simple counter exposed to the template."""

    template = '<div><button dj-click="bump">Bump {{ count }}</button></div>'

    def mount(self, request, **kwargs):  # noqa: D401 — LiveView contract
        self.count = kwargs.get("initial", 0)

    def bump(self, **kwargs):
        self.count += 1


class _ChildWithKwargs(LiveView):
    """Child that surfaces mount kwargs on the instance for inspection."""

    template = "<div>{{ foo }}/{{ bar }}</div>"

    def mount(self, request, **kwargs):
        self.foo = kwargs.get("foo")
        self.bar = kwargs.get("bar")


class _ChildWithClickHandler(LiveView):
    """Child that carries multiple event-attribute-bearing elements."""

    template = (
        "<section>"
        '<button dj-click="inc">inc</button>'
        '<form dj-submit="save"><input dj-input="set_q"/></form>'
        '<select dj-change="pick"></select>'
        "</section>"
    )

    def mount(self, request, **kwargs):
        self.q = ""
        self.hits = 0

    def inc(self, **kwargs):
        self.hits += 1


class _ChildWithTrickyAttrs(LiveView):
    """Child with quoted attribute values containing '>' and static content.

    Exercises the fix to the attribute-value-safe regex. Before the fix,
    ``<input value="a>b" dj-click="x">`` would stop the match at the
    first ``>`` inside the quoted value and stamp the wrong place.
    """

    template = (
        "<div>"
        '<input value="a>b" dj-click="x"/>'
        '<button title="&lt;foo&gt;" dj-click="y">Click</button>'
        "<script>var x = '<button dj-click=\"in_script\">';</script>"
        '<!-- comment: <button dj-click="in_comment"> -->'
        "</div>"
    )

    def mount(self, request, **kwargs):
        pass

    def x(self, **kwargs):
        pass

    def y(self, **kwargs):
        pass


class _NotALiveView:
    """Intentionally NOT a LiveView — used for the subclass-validation test."""

    template = "<div>not a live view</div>"


class _ParentView(LiveView):
    """Parent whose template embeds a ``{% live_render %}`` child."""

    template = (
        "{% load live_tags %}"
        "<div dj-root>"
        "<h1>{{ title }}</h1>"
        '{% live_render "tests.unit.test_live_render_tag._ChildLiveView" %}'
        "</div>"
    )

    def mount(self, request, **kwargs):
        self.title = "Parent"


class _GrandparentView(LiveView):
    """Parent whose child itself embeds another live_render — nested test."""

    template = (
        "{% load live_tags %}"
        "<div dj-root>"
        '{% live_render "tests.unit.test_live_render_tag._MidChildView" %}'
        "</div>"
    )

    def mount(self, request, **kwargs):
        pass


class _MidChildView(LiveView):
    """Child that embeds a grandchild."""

    template = (
        "{% load live_tags %}"
        "<section>"
        '<button dj-click="mid_click">mid</button>'
        '{% live_render "tests.unit.test_live_render_tag._ChildLiveView" %}'
        "</section>"
    )

    def mount(self, request, **kwargs):
        pass

    def mid_click(self, **kwargs):
        pass


class _AdminOnlyChildView(LiveView):
    """Child guarded by permission_required — must trip the auth gate."""

    permission_required = "auth.admin"
    template = '<div><button dj-click="secret">x</button></div>'

    def mount(self, request, **kwargs):
        pass

    def secret(self, **kwargs):
        pass


class _LoginOnlyChildView(LiveView):
    """Child guarded by login_required — returns redirect URL when anonymous."""

    login_required = True
    template = '<div><button dj-click="private">x</button></div>'

    def mount(self, request, **kwargs):
        pass

    def private(self, **kwargs):
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _render_tag(source: str, context: dict | None = None) -> str:
    """Render ``source`` with live_tags loaded. Caller must supply a parent view."""
    full = "{% load live_tags %}" + source
    return Template(full).render(Context(context or {}))


def _make_parent(rf, parent_cls=_ParentView):
    """Instantiate a parent view with a lightweight request for tests that need it."""
    request = rf.get("/")
    # Tests run without AuthenticationMiddleware — attach an anonymous user
    # so that check_view_auth's user lookup doesn't blow up on getattr.
    from django.contrib.auth.models import AnonymousUser

    request.user = AnonymousUser()
    parent = parent_cls()
    parent.request = request
    mount = getattr(parent, "mount", None)
    if callable(mount):
        mount(request)
    return parent


# ---------------------------------------------------------------------------
# 1. Dotted-path resolution
# ---------------------------------------------------------------------------


class TestDottedPathResolution:
    def test_live_render_resolves_view_class_from_dotted_path(self, rf):
        """{% live_render 'x.y.Z' %} runs Z's mount + render."""
        parent = _make_parent(rf)
        out = _render_tag(
            '{% live_render "tests.unit.test_live_render_tag._ChildLiveView" %}',
            {"view": parent, "request": parent.request},
        )
        tree = _parse(out)
        # A <button dj-click="bump"> element is present in the output.
        buttons = [attrs for tag, attrs in tree.elements if tag == "button"]
        assert len(buttons) == 1
        assert buttons[0].get("dj-click") == "bump"
        # And its text shows the rendered count (Phase A renders on mount).
        assert "Bump" in "".join(tree.text_data)


# ---------------------------------------------------------------------------
# 2. Subclass validation
# ---------------------------------------------------------------------------


class TestSubclassValidation:
    def test_live_render_raises_on_non_liveview_class(self, rf):
        parent = _make_parent(rf)
        with pytest.raises(TemplateSyntaxError):
            _render_tag(
                '{% live_render "tests.unit.test_live_render_tag._NotALiveView" %}',
                {"view": parent, "request": parent.request},
            )

    def test_live_render_raises_on_unresolvable_path(self, rf):
        parent = _make_parent(rf)
        with pytest.raises(TemplateSyntaxError):
            _render_tag(
                '{% live_render "does.not.exist.NoSuchView" %}',
                {"view": parent, "request": parent.request},
            )


# ---------------------------------------------------------------------------
# 3. Unique view_id per invocation
# ---------------------------------------------------------------------------


class TestUniqueViewId:
    def test_live_render_assigns_unique_view_id(self, rf):
        parent = _make_parent(rf)
        out = _render_tag(
            '{% live_render "tests.unit.test_live_render_tag._ChildLiveView" %}'
            '{% live_render "tests.unit.test_live_render_tag._ChildLiveView" %}',
            {"view": parent, "request": parent.request},
        )
        assert len(parent._child_views) == 2
        ids = list(parent._child_views.keys())
        assert ids[0] != ids[1]
        # Both data-djust-embedded values appear on element attribute slots.
        embedded_values = _attr_values(out, "data-djust-embedded")
        for vid in ids:
            assert vid in embedded_values


# ---------------------------------------------------------------------------
# 4. Child registered on parent
# ---------------------------------------------------------------------------


class TestChildRegistration:
    def test_live_render_registers_child_on_parent(self, rf):
        parent = _make_parent(rf)
        _render_tag(
            '{% live_render "tests.unit.test_live_render_tag._ChildLiveView" %}',
            {"view": parent, "request": parent.request},
        )
        assert len(parent._child_views) == 1
        child = next(iter(parent._child_views.values()))
        assert isinstance(child, _ChildLiveView)
        assert child._parent_view is parent
        assert child._view_id in parent._child_views


# ---------------------------------------------------------------------------
# 5. data-djust-embedded stamped inside event-attribute-bearing tags
# ---------------------------------------------------------------------------


class TestViewIdStamping:
    def test_live_render_emits_data_djust_embedded_on_event_attributes(self, rf):
        parent = _make_parent(rf)
        out = _render_tag(
            '{% live_render "tests.unit.test_live_render_tag._ChildWithClickHandler" %}',
            {"view": parent, "request": parent.request},
        )
        child_id = next(iter(parent._child_views))

        # Every element carrying a dj-* event attribute also carries the
        # data-djust-embedded attribute with the correct view_id.
        event_attrs = {"dj-click", "dj-submit", "dj-input", "dj-change"}
        tree = _parse(out)
        tagged = [
            (tag, attrs) for tag, attrs in tree.elements if any(a in attrs for a in event_attrs)
        ]
        # We expect exactly four event-bearing elements in
        # _ChildWithClickHandler.template: button, form, input, select.
        assert len(tagged) == 4
        for tag, attrs in tagged:
            assert attrs.get("data-djust-embedded") == child_id, (
                "Expected data-djust-embedded={%r} on <%s>, got attrs=%r" % (child_id, tag, attrs)
            )

        # And the wrapper <div dj-view> carries it too (for the client's
        # [dj-view][data-djust-embedded="..."] scoping selector).
        wrappers = [attrs for tag, attrs in tree.elements if tag == "div" and "dj-view" in attrs]
        assert len(wrappers) == 1
        assert wrappers[0].get("data-djust-embedded") == child_id

    def test_data_djust_embedded_is_inside_tag_not_text_content(self, rf):
        """Regression for the injection-outside-tag bug.

        The original regex captured ``<TagName attr=`` and replaced the
        whole match, causing the marker to appear as PCDATA before the
        tag. This test parses the HTML and asserts:
          * No free-floating text in the output contains
            ``data-djust-embedded=``.
          * Every element with a dj-* event attr has the marker in its
            attrs dict (verified above).
        """
        parent = _make_parent(rf)
        out = _render_tag(
            '{% live_render "tests.unit.test_live_render_tag._ChildLiveView" %}',
            {"view": parent, "request": parent.request},
        )
        tree = _parse(out)
        # PCDATA must not contain the marker.
        text_blob = "".join(tree.text_data)
        assert "data-djust-embedded" not in text_blob, "Marker leaked into PCDATA: %r" % text_blob

    def test_view_id_not_used_as_dom_attribute(self, rf):
        """The legacy `view_id` HTML attribute (with underscore) is gone.

        HTML-level attribute name is ``data-djust-embedded``. ``view_id``
        survives only as a wire-protocol field in outbound event params.
        """
        parent = _make_parent(rf)
        out = _render_tag(
            '{% live_render "tests.unit.test_live_render_tag._ChildWithClickHandler" %}',
            {"view": parent, "request": parent.request},
        )
        tree = _parse(out)
        for tag, attrs in tree.elements:
            assert "view_id" not in attrs, (
                "Found legacy 'view_id' attr on <%s>: %r — should be "
                "'data-djust-embedded'" % (tag, attrs)
            )


# ---------------------------------------------------------------------------
# 6. Event dispatch routes to the child
# ---------------------------------------------------------------------------


class TestEventDispatch:
    def test_live_render_child_event_dispatches_to_child(self, rf):
        parent = _make_parent(rf)
        _render_tag(
            '{% live_render "tests.unit.test_live_render_tag._ChildLiveView" %}',
            {"view": parent, "request": parent.request},
        )
        assert len(parent._child_views) == 1
        child_id = next(iter(parent._child_views))
        child = parent._child_views[child_id]

        children = parent._get_all_child_views()
        target = children.get(child_id)
        assert target is child
        target.bump()
        assert child.count == 1
        assert not hasattr(parent, "count")


# ---------------------------------------------------------------------------
# 7. Independent contexts
# ---------------------------------------------------------------------------


class TestIndependentContexts:
    def test_live_render_child_get_context_data_independent(self, rf):
        parent = _make_parent(rf)
        parent_ctx = parent.get_context_data()
        out = _render_tag(
            '{% live_render "tests.unit.test_live_render_tag._ChildLiveView" %}',
            {"view": parent, "request": parent.request},
        )
        assert parent_ctx.get("title") == "Parent"
        assert "count" not in parent_ctx

        child = next(iter(parent._child_views.values()))
        child_ctx = child.get_context_data()
        assert child_ctx.get("count") == 0
        assert "title" not in child_ctx

        tree = _parse(out)
        assert "Bump 0" in "".join(tree.text_data)


# ---------------------------------------------------------------------------
# 8. kwargs pass through to child's mount
# ---------------------------------------------------------------------------


class TestKwargsPassthrough:
    def test_live_render_kwargs_pass_to_child_mount(self, rf):
        parent = _make_parent(rf)
        out = _render_tag(
            '{% live_render "tests.unit.test_live_render_tag._ChildWithKwargs"'
            ' foo="hello" bar=42 %}',
            {"view": parent, "request": parent.request},
        )
        child = next(iter(parent._child_views.values()))
        assert child.foo == "hello"
        assert child.bar == 42
        tree = _parse(out)
        assert "hello/42" in "".join(tree.text_data)

    def test_kwargs_view_id_is_honored(self, rf):
        """Passing ``view_id=`` as a kwarg pins a stable id instead of
        the auto-generated ``child_N`` stamp."""
        parent = _make_parent(rf)
        out = _render_tag(
            '{% live_render "tests.unit.test_live_render_tag._ChildLiveView"'
            ' view_id="pinned-audio-player" %}',
            {"view": parent, "request": parent.request},
        )
        assert "pinned-audio-player" in parent._child_views
        assert _attr_values(out, "data-djust-embedded") == [
            "pinned-audio-player",  # wrapper
            "pinned-audio-player",  # button
        ]


# ---------------------------------------------------------------------------
# 9. start_async isolated to the child instance
# ---------------------------------------------------------------------------


class TestAsyncIsolation:
    def test_live_render_child_start_async_isolated(self, rf):
        parent = _make_parent(rf)
        _render_tag(
            '{% live_render "tests.unit.test_live_render_tag._ChildLiveView" %}',
            {"view": parent, "request": parent.request},
        )
        child = next(iter(parent._child_views.values()))

        def _bg():
            child.count = 99

        child.start_async(_bg, name="counter_bg")

        assert hasattr(child, "_async_tasks")
        assert "counter_bg" in child._async_tasks
        assert not getattr(parent, "_async_tasks", {})


# ---------------------------------------------------------------------------
# 10. Cleanup on WS disconnect
# ---------------------------------------------------------------------------


class TestDisconnectCleanup:
    def test_live_render_child_unregister_on_parent_disconnect(self, rf):
        parent = _make_parent(rf)
        _render_tag(
            '{% live_render "tests.unit.test_live_render_tag._ChildLiveView" %}'
            '{% live_render "tests.unit.test_live_render_tag._ChildLiveView" %}',
            {"view": parent, "request": parent.request},
        )
        assert len(parent._child_views) == 2

        cleanup_calls: list[str] = []
        for vid, child in list(parent._child_views.items()):
            child._cleanup_on_unregister = (  # type: ignore[attr-defined]
                lambda v=vid: cleanup_calls.append(v)
            )

        for vid in list(parent._child_views):
            parent._unregister_child(vid)

        assert parent._child_views == {}
        assert len(cleanup_calls) == 2


# ---------------------------------------------------------------------------
# Regression — nested live_render assigns distinct ids to each embed
# ---------------------------------------------------------------------------


class TestNestedLiveRender:
    def test_nested_live_render_each_child_distinct_embed_id(self, rf):
        """A live_render that itself contains live_render must produce
        two distinct data-djust-embedded values — one for the mid child
        and one for the grandchild."""
        parent = _make_parent(rf, parent_cls=_GrandparentView)
        out = Template(_GrandparentView.template).render(
            Context({"view": parent, "request": parent.request})
        )
        # _MidChildView is the child of parent. Grandchild is _ChildLiveView.
        mid_id = next(iter(parent._child_views))
        mid = parent._child_views[mid_id]
        assert isinstance(mid, _MidChildView)
        grand_id = next(iter(mid._child_views))
        assert grand_id != mid_id

        wrappers = _elements_with_attr(out, "data-djust-embedded")
        # Collect unique embed ids on ANY tag.
        unique_ids = {attrs["data-djust-embedded"] for _tag, attrs in wrappers}
        assert mid_id in unique_ids
        assert grand_id in unique_ids

        # And the mid child's <button dj-click="mid_click"> element is
        # stamped with the MID id, not the grandchild's.
        buttons = [
            attrs
            for tag, attrs in _parse(out).elements
            if tag == "button" and attrs.get("dj-click") == "mid_click"
        ]
        assert len(buttons) == 1
        assert buttons[0].get("data-djust-embedded") == mid_id


# ---------------------------------------------------------------------------
# Regression — re-rendering produces distinct view_ids (idempotence hazard)
# ---------------------------------------------------------------------------


class TestIdempotentReRender:
    def test_idempotent_re_render_distinct_ids(self, rf):
        """Rendering the same template twice (same view instance) emits
        TWO children, each with its own view_id — not a duplicate stamp
        on the same embed."""
        parent = _make_parent(rf)
        out = _render_tag(
            '{% live_render "tests.unit.test_live_render_tag._ChildLiveView" %}'
            '{% live_render "tests.unit.test_live_render_tag._ChildLiveView" %}',
            {"view": parent, "request": parent.request},
        )
        assert len(parent._child_views) == 2
        values = _attr_values(out, "data-djust-embedded")
        # Wrappers + stamped buttons = 4 attrs total (2 per embed).
        # Every unique id appears exactly twice (wrapper + <button>).
        from collections import Counter

        counts = Counter(values)
        assert sum(counts.values()) == 4
        assert all(c == 2 for c in counts.values()), counts


# ---------------------------------------------------------------------------
# Regression — tricky attribute values + <script> / <!-- -->
# ---------------------------------------------------------------------------


class TestTrickyAttributeValues:
    def test_quoted_attribute_values_with_gt_are_preserved(self, rf):
        """The stamp regex must not stop at `>` inside a quoted attribute."""
        parent = _make_parent(rf)
        out = _render_tag(
            '{% live_render "tests.unit.test_live_render_tag._ChildWithTrickyAttrs" %}',
            {"view": parent, "request": parent.request},
        )
        child_id = next(iter(parent._child_views))

        tree = _parse(out)
        inputs = [(tag, attrs) for tag, attrs in tree.elements if tag == "input"]
        buttons = [(tag, attrs) for tag, attrs in tree.elements if tag == "button"]

        # The <input value="a>b" dj-click="x"/> was stamped AND its
        # quoted value survived the stamp pass.
        assert len(inputs) == 1
        input_attrs = inputs[0][1]
        assert input_attrs.get("data-djust-embedded") == child_id
        assert input_attrs.get("value") == "a>b"
        assert input_attrs.get("dj-click") == "x"

        # The <button title="<foo>" dj-click="y"> was also stamped;
        # note html.parser decodes the HTML entities back to literal
        # "<foo>" when emitting the attribute value.
        assert len(buttons) == 1
        button_attrs = buttons[0][1]
        assert button_attrs.get("data-djust-embedded") == child_id
        assert button_attrs.get("title") == "<foo>"
        assert button_attrs.get("dj-click") == "y"

    def test_script_blocks_are_not_stamped(self, rf):
        """<script> element contents are preserved verbatim."""
        parent = _make_parent(rf)
        out = _render_tag(
            '{% live_render "tests.unit.test_live_render_tag._ChildWithTrickyAttrs" %}',
            {"view": parent, "request": parent.request},
        )
        tree = _parse(out)
        # Re-assemble the script body and confirm nothing was stamped.
        script_blob = "".join(tree.scripts)
        assert "data-djust-embedded" not in script_blob, (
            "Marker leaked into <script> body: %r" % script_blob
        )
        # The script's literal <button dj-click="in_script"> string is
        # preserved (it's string content, not a real element).
        assert "in_script" in script_blob

    def test_comments_are_not_stamped(self, rf):
        parent = _make_parent(rf)
        out = _render_tag(
            '{% live_render "tests.unit.test_live_render_tag._ChildWithTrickyAttrs" %}',
            {"view": parent, "request": parent.request},
        )
        tree = _parse(out)
        comment_blob = "".join(tree.comments)
        assert "data-djust-embedded" not in comment_blob, (
            "Marker leaked into HTML comment: %r" % comment_blob
        )
        assert "in_comment" in comment_blob


# ---------------------------------------------------------------------------
# Regression — auth enforcement for embedded child
# ---------------------------------------------------------------------------


class TestAuthEnforcement:
    def test_live_render_denies_unauthorized_child_permission(self, rf, db):
        """Permission-gated child with an authenticated user lacking the
        permission → PermissionDenied from check_view_auth."""
        from django.contrib.auth import get_user_model

        User = get_user_model()
        # Use a username unique to this test so parallel runs don't collide.
        user = User.objects.create_user(
            username="live_render_auth_user",
            password="x",
        )
        request = rf.get("/")
        request.user = user
        parent = _ParentView()
        parent.request = request
        parent.mount(request)

        # _AdminOnlyChildView requires 'auth.admin' — user doesn't have it.
        with pytest.raises(PermissionDenied):
            _render_tag(
                '{% live_render "tests.unit.test_live_render_tag._AdminOnlyChildView" %}',
                {"view": parent, "request": parent.request},
            )

    def test_live_render_denies_unauthenticated_login_required_child(self, rf):
        """login_required child + anonymous user → TemplateSyntaxError with
        the login-redirect URL in the message."""
        parent = _make_parent(rf)  # anonymous user attached by _make_parent
        with pytest.raises(TemplateSyntaxError) as exc_info:
            _render_tag(
                '{% live_render "tests.unit.test_live_render_tag._LoginOnlyChildView" %}',
                {"view": parent, "request": parent.request},
            )
        assert "login redirect" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Regression — DJUST_LIVE_RENDER_ALLOWED_MODULES allowlist
# ---------------------------------------------------------------------------


class TestAllowlist:
    def test_allowed_modules_unset_permits_everything(self, rf, settings):
        # Default: setting is absent, no restriction.
        settings.DJUST_LIVE_RENDER_ALLOWED_MODULES = None
        parent = _make_parent(rf)
        out = _render_tag(
            '{% live_render "tests.unit.test_live_render_tag._ChildLiveView" %}',
            {"view": parent, "request": parent.request},
        )
        # Rendered fine.
        assert len(parent._child_views) == 1
        assert _attr_values(out, "data-djust-embedded")

    def test_allowed_modules_blocks_disallowed_path(self, rf):
        """Setting the allowlist blocks paths outside the prefix."""
        parent = _make_parent(rf)
        with override_settings(DJUST_LIVE_RENDER_ALLOWED_MODULES=["myapp.views"]):
            with pytest.raises(TemplateSyntaxError) as exc_info:
                _render_tag(
                    '{% live_render "tests.unit.test_live_render_tag._ChildLiveView" %}',
                    {"view": parent, "request": parent.request},
                )
            assert "DJUST_LIVE_RENDER_ALLOWED_MODULES" in str(exc_info.value)

    def test_allowed_modules_permits_exact_prefix_match(self, rf):
        parent = _make_parent(rf)
        with override_settings(
            DJUST_LIVE_RENDER_ALLOWED_MODULES=["tests.unit.test_live_render_tag"]
        ):
            out = _render_tag(
                '{% live_render "tests.unit.test_live_render_tag._ChildLiveView" %}',
                {"view": parent, "request": parent.request},
            )
        assert len(parent._child_views) == 1
        assert _attr_values(out, "data-djust-embedded")
