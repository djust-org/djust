"""
Tests for _strip_comments_and_whitespace() in TemplateMixin.

Regression tests for the textarea newline bug: the method was collapsing
all whitespace (including \\n inside <textarea>) via re.sub(r"\\s+", " ").
The fix adds <textarea> to the preserved-block list alongside <pre> and <code>.
"""

# Configure Django settings BEFORE any djust imports
import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        DEBUG=True,
        SECRET_KEY="test-secret-key",
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
        ],
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": ":memory:",
            }
        },
        SESSION_ENGINE="django.contrib.sessions.backends.signed_cookies",
    )
    django.setup()

import pytest
from djust.mixins.template import TemplateMixin


@pytest.fixture
def mixin():
    """Create a bare TemplateMixin instance for testing."""
    return TemplateMixin()


class TestStripCommentsAndWhitespace:
    """Tests for _strip_comments_and_whitespace()."""

    # ── Basic whitespace normalization ──

    def test_collapses_runs_of_whitespace(self, mixin):
        html = "<div>   hello   world   </div>"
        result = mixin._strip_comments_and_whitespace(html)
        assert result == "<div> hello world </div>"

    def test_removes_whitespace_between_tags(self, mixin):
        html = "<div>  </div>  \n  <span>hi</span>"
        result = mixin._strip_comments_and_whitespace(html)
        assert "<div>" in result
        assert "><span>" in result

    def test_removes_html_comments(self, mixin):
        html = "<div><!-- comment --><span>text</span></div>"
        result = mixin._strip_comments_and_whitespace(html)
        assert "comment" not in result
        assert "<span>text</span>" in result

    def test_removes_multiline_comments(self, mixin):
        html = "<div><!--\n  multi\n  line\n  comment\n--><span>ok</span></div>"
        result = mixin._strip_comments_and_whitespace(html)
        assert "multi" not in result
        assert "<span>ok</span>" in result

    # ── <textarea> preservation (the main regression) ──

    def test_textarea_newlines_preserved(self, mixin):
        """Regression: textarea content with newlines must not be collapsed."""
        html = '<textarea name="doc">line1\nline2\nline3</textarea>'
        result = mixin._strip_comments_and_whitespace(html)
        assert "line1\nline2\nline3" in result

    def test_textarea_indentation_preserved(self, mixin):
        html = "<textarea>  indented\n  text  </textarea>"
        result = mixin._strip_comments_and_whitespace(html)
        assert "  indented\n  text  " in result

    def test_textarea_with_attributes_preserved(self, mixin):
        html = '<textarea id="editor" name="content" class="big">hello\nworld</textarea>'
        result = mixin._strip_comments_and_whitespace(html)
        assert "hello\nworld" in result

    def test_textarea_empty_lines_preserved(self, mixin):
        html = "<textarea>first\n\n\nlast</textarea>"
        result = mixin._strip_comments_and_whitespace(html)
        assert "first\n\n\nlast" in result

    def test_textarea_tabs_preserved(self, mixin):
        html = "<textarea>\tindented\n\t\tmore</textarea>"
        result = mixin._strip_comments_and_whitespace(html)
        assert "\tindented\n\t\tmore" in result

    def test_multiple_textareas_preserved(self, mixin):
        html = (
            "<div>"
            '<textarea name="a">one\ntwo</textarea>'
            "   "
            '<textarea name="b">three\nfour</textarea>'
            "</div>"
        )
        result = mixin._strip_comments_and_whitespace(html)
        assert "one\ntwo" in result
        assert "three\nfour" in result

    def test_textarea_surrounded_by_whitespace(self, mixin):
        """Whitespace between a tag boundary and a <textarea> is STRIPPED.

        #1737: a <pre>/<code>/<textarea> tag is a tag boundary for
        inter-element whitespace collapse, matching the Rust
        ``render_with_diff()`` whitespace pass. Pre-#1737 this normalizer
        left a single space (``<div> <textarea>``) which diverged from Rust
        and re-opened the first-hydration whitespace mismatch. Content
        INSIDE the textarea is still preserved.
        """
        html = "<div>   <textarea>keep\nme</textarea>   </div>"
        result = mixin._strip_comments_and_whitespace(html)
        assert "keep\nme" in result
        # Whitespace adjacent to the preserved block is stripped (Rust parity)
        assert "<div><textarea>" in result
        assert "</textarea></div>" in result

    def test_whitespace_between_sibling_tags_collapsed(self, mixin):
        """Whitespace between a sibling tag and a <textarea> is STRIPPED (#1737)."""
        html = "<span>a</span>   \n   <textarea>keep\nme</textarea>"
        result = mixin._strip_comments_and_whitespace(html)
        assert "keep\nme" in result
        # A <textarea> boundary is a tag boundary — whitespace collapses to
        # nothing, matching the Rust render_with_diff() pass (#1737).
        assert "</span><textarea>" in result

    def test_textarea_case_insensitive(self, mixin):
        html = "<TEXTAREA>hello\nworld</TEXTAREA>"
        result = mixin._strip_comments_and_whitespace(html)
        assert "hello\nworld" in result

    # ── <pre> preservation ──

    def test_pre_newlines_preserved(self, mixin):
        html = "<pre>def foo():\n    return 42</pre>"
        result = mixin._strip_comments_and_whitespace(html)
        assert "def foo():\n    return 42" in result

    def test_pre_with_code_preserved(self, mixin):
        html = "<pre><code>line1\nline2</code></pre>"
        result = mixin._strip_comments_and_whitespace(html)
        assert "line1\nline2" in result

    # ── <code> preservation ──

    def test_code_whitespace_preserved(self, mixin):
        html = "<code>  x = 1  </code>"
        result = mixin._strip_comments_and_whitespace(html)
        assert "  x = 1  " in result

    # ── Mixed content ──

    def test_textarea_and_pre_both_preserved(self, mixin):
        html = "<div>\n  <textarea>text\narea</textarea>\n  <pre>pre\nblock</pre>\n</div>"
        result = mixin._strip_comments_and_whitespace(html)
        assert "text\narea" in result
        assert "pre\nblock" in result

    def test_realistic_notepad_html(self, mixin):
        """Simulate the collab-notepad textarea with real content."""
        html = (
            '<div class="notepad-container">\n'
            '  <div class="editor">\n'
            '    <textarea id="notepad-editor" name="notepad-editor"\n'
            '              dj-input="update_content">'
            "# Welcome\n\nThis is a shared document.\n\n"
            "Try these:\n- Open multiple tabs\n- Start typing"
            "</textarea>\n"
            "  </div>\n"
            '  <div class="sidebar">\n'
            "    <span>2 online</span>\n"
            "  </div>\n"
            "</div>"
        )
        result = mixin._strip_comments_and_whitespace(html)
        # Textarea content preserved
        assert "# Welcome\n\nThis is a shared document." in result
        assert "- Open multiple tabs\n- Start typing" in result
        # Outside whitespace collapsed
        assert "><div" in result

    # ── <script> / <style> preservation (#1927) ──
    #
    # The Rust VDOM parser preserves whitespace inside <script>/<style>
    # (crates/djust_vdom/src/parser.rs:475), and this normalizer exists to
    # match Rust parser behavior — but previously collapsed every newline
    # inside an inline <script>, neutering it. The canonical #1927 trigger: a
    # `//`-led line comment on the first line. Once the body is one line, the
    # `//` swallows the rest, so the script silently never executes (no console
    # error). This is the live-morph "inline <script> never runs" symptom the
    # browser-smoke canary (tests/playwright/test_browser_smoke.py #1848 class)
    # catches — the #1871 `_runInsertedScripts` morph fix couldn't cure it
    # because the script was already neutered before any re-execution.

    def test_script_newlines_preserved(self, mixin):
        """Regression (#1927): inline <script> newlines must NOT be collapsed."""
        html = "<div><script>\n  // setup\n  window.x = 1;\n</script></div>"
        result = mixin._strip_comments_and_whitespace(html)
        assert "// setup\n  window.x = 1;" in result

    def test_script_line_comment_not_swallowed(self, mixin):
        """The exact #1927 bug shape: a `//` line comment leading the body.

        If newlines collapse, the `//` comments out the entire single-line
        body and `window.__wired` never gets set — the silent-no-execute bug.
        """
        html = (
            "<div dj-root>"
            "<script>\n"
            "  // delegated listener — must keep its own line\n"
            "  (function(){ window.__wired = true; })();\n"
            "</script>"
            "</div>"
        )
        result = mixin._strip_comments_and_whitespace(html)
        # The newline AFTER the comment line must survive so the IIFE is on its
        # own line and is not commented out.
        assert "// delegated listener — must keep its own line\n" in result
        assert "window.__wired = true;" in result
        # The code line is NOT on the same line as the `//` comment.
        comment_idx = result.index("// delegated listener")
        code_idx = result.index("window.__wired = true;")
        between = result[comment_idx:code_idx]
        assert "\n" in between, "a newline must separate the // comment from the code"

    def test_style_newlines_preserved(self, mixin):
        """Inline <style> CSS newlines must be preserved (#1927)."""
        html = "<div><style>\n  .a { color: red; }\n  .b { color: blue; }\n</style></div>"
        result = mixin._strip_comments_and_whitespace(html)
        assert ".a { color: red; }\n" in result
        assert ".b { color: blue; }" in result

    def test_script_with_attributes_preserved(self, mixin):
        html = '<script type="text/javascript">\n  var a = 1;\n  var b = 2;\n</script>'
        result = mixin._strip_comments_and_whitespace(html)
        assert "var a = 1;\n  var b = 2;" in result

    def test_script_case_insensitive(self, mixin):
        html = "<SCRIPT>\n  // c\n  go();\n</SCRIPT>"
        result = mixin._strip_comments_and_whitespace(html)
        assert "// c\n  go();" in result

    def test_multiple_scripts_preserved(self, mixin):
        html = (
            "<div>"
            "<script>\n  // one\n  a();\n</script>"
            "   "
            "<script>\n  // two\n  b();\n</script>"
            "</div>"
        )
        result = mixin._strip_comments_and_whitespace(html)
        assert "// one\n  a();" in result
        assert "// two\n  b();" in result

    def test_non_script_whitespace_still_collapsed_with_script_present(self, mixin):
        """The fix preserves <script> WITHOUT disabling collapse elsewhere."""
        html = "<div>   lots   of   space   <script>\n  // x\n  y();\n</script></div>"
        result = mixin._strip_comments_and_whitespace(html)
        # Script body intact
        assert "// x\n  y();" in result
        # Ordinary text still collapsed to single spaces
        assert "lots of space" in result

    def test_html_comment_inside_script_not_stripped(self, mixin):
        """A comment-strip pass must not eat an HTML-comment-looking token that
        lives inside a <script> raw-text body (the script is preserved whole)."""
        html = "<div><script>\n  var s = '<!-- not a comment -->';\n  use(s);\n</script></div>"
        result = mixin._strip_comments_and_whitespace(html)
        assert "<!-- not a comment -->" in result
        assert "use(s);" in result


class TestSkipRender:
    """Tests for the _skip_render flag on LiveView instances.

    When a view sets self._skip_render = True in a handler, the consumer
    should skip render_with_diff() and just flush push events.
    """

    def test_skip_render_default_false(self):
        """Views should not have _skip_render set by default."""
        from djust.live_view import LiveView

        view = LiveView()
        assert getattr(view, "_skip_render", False) is False

    def test_skip_render_can_be_set(self):
        """Views can set _skip_render = True in handlers."""
        from djust.live_view import LiveView

        view = LiveView()
        view._skip_render = True
        assert view._skip_render is True

    def test_skip_render_pattern_heartbeat(self):
        """Simulate the heartbeat skip pattern from collab-notepad."""
        from djust.live_view import LiveView

        view = LiveView()

        # Simulate: first heartbeat with changes → should render
        view._last_content = None
        new_content = "hello"
        if new_content != view._last_content:
            view._last_content = new_content
            # Would call _sync_state() and render normally
            should_render = True
        else:
            view._skip_render = True
            should_render = False

        assert should_render is True
        assert getattr(view, "_skip_render", False) is False

        # Simulate: second heartbeat with no changes → should skip
        new_content = "hello"
        if new_content != view._last_content:
            view._last_content = new_content
            should_render = True
        else:
            view._skip_render = True
            should_render = False

        assert should_render is False
        assert view._skip_render is True


class TestSnapshotAssigns:
    """Tests for _snapshot_assigns auto-detection of unchanged state."""

    def test_unchanged_assigns_match(self):
        """Snapshot before/after should match if no assigns changed."""
        from djust.live_view import LiveView
        from djust.websocket import _snapshot_assigns

        view = LiveView()
        view.count = 0
        view.name = "test"

        snap1 = _snapshot_assigns(view)
        # No changes
        snap2 = _snapshot_assigns(view)
        assert snap1 == snap2

    def test_reassignment_detected(self):
        """Reassigning an attribute should change the snapshot."""
        from djust.live_view import LiveView
        from djust.websocket import _snapshot_assigns

        view = LiveView()
        view.count = 0

        snap1 = _snapshot_assigns(view)
        view.count = 1
        snap2 = _snapshot_assigns(view)
        assert snap1 != snap2

    def test_new_attribute_detected(self):
        """Adding a new attribute should change the snapshot."""
        from djust.live_view import LiveView
        from djust.websocket import _snapshot_assigns

        view = LiveView()
        view.count = 0

        snap1 = _snapshot_assigns(view)
        view.name = "new"
        snap2 = _snapshot_assigns(view)
        assert snap1 != snap2

    def test_framework_private_attrs_excluded_but_user_private_included(self):
        """Framework ``_``-prefixed attrs (set at __init__) are excluded from
        the snapshot via ``_framework_attrs`` membership. User ``_``-prefixed
        attrs set AFTER __init__ (in mount() or event handlers) ARE included
        so that a handler mutating only private state triggers a render rather
        than a false noop (#1281)."""
        from djust.live_view import LiveView
        from djust.websocket import _snapshot_assigns

        view = LiveView()
        view.count = 0
        view._private = "hidden"

        snap = _snapshot_assigns(view)
        assert "count" in snap
        # User _-prefixed attrs are now included (#1281)
        assert "_private" in snap
        # Framework _-prefixed attrs (set at __init__) are still excluded
        assert "_changed_keys" not in snap

    def test_list_reassign_detected_by_identity(self):
        """Reassigning a list (even to identical content) changes identity and
        therefore changes the snapshot. This matches `_snapshot_assigns`'
        documented contract: identity-based detection, with a shallow content
        fingerprint layered on top. Content-equal reassignment is INTENTIONALLY
        treated as dirty — avoiding it is the caller's responsibility (e.g.
        via ``_changed_keys`` marking, or by mutating in place).
        """
        from djust.live_view import LiveView
        from djust.websocket import _snapshot_assigns

        view = LiveView()
        view.items = [1, 2, 3]

        snap1 = _snapshot_assigns(view)
        view.items = [1, 2, 3]  # New list object — different id() → dirty.
        snap2 = _snapshot_assigns(view)
        assert snap1 != snap2, "reassignment should register as a state change"

    def test_list_different_content_detected(self):
        """Reassigning a list with different content should differ."""
        from djust.live_view import LiveView
        from djust.websocket import _snapshot_assigns

        view = LiveView()
        view.items = [1, 2, 3]

        snap1 = _snapshot_assigns(view)
        view.items = [1, 2, 4]
        snap2 = _snapshot_assigns(view)
        assert snap1 != snap2

    def test_inplace_mutation_detected(self):
        """In-place dict mutation within a list should be detected."""
        from djust.live_view import LiveView
        from djust.websocket import _snapshot_assigns

        view = LiveView()
        view.todos = [{"id": 1, "text": "Buy groceries", "completed": False}]

        snap1 = _snapshot_assigns(view)
        view.todos[0]["completed"] = True  # In-place mutation
        snap2 = _snapshot_assigns(view)
        assert snap1 != snap2  # Deep copy detects the mutation

    def test_inplace_list_append_detected(self):
        """In-place list.append should be detected."""
        from djust.live_view import LiveView
        from djust.websocket import _snapshot_assigns

        view = LiveView()
        view.items = [1, 2]

        snap1 = _snapshot_assigns(view)
        view.items.append(3)
        snap2 = _snapshot_assigns(view)
        assert snap1 != snap2


class TestEndTagWhitespacePreservation:
    """#2482 (CodeQL py/bad-tag-filter): an end tag may carry trailing
    whitespace (``</script >``, ``</style\\n>``, ``</pre >``) per the HTML spec —
    browsers accept it. The preserve-block regexes must match those forms;
    a bare ``</tag>`` pattern misses them, so the block is NOT preserved and
    the HTML-comment strip / whitespace collapse corrupts the raw-text body.
    These are gate-off tests: each FAILS against the pre-fix ``</tag>`` pattern.
    """

    def test_script_with_whitespace_end_tag_is_preserved(self, mixin):
        # JS body containing a comment-looking token, closed with </script >.
        # Pre-fix the block wasn't preserved → the comment-strip removed the token.
        html = "<div><script>var s = '<!-- not a comment -->';</script ></div>"
        result = mixin._strip_comments_and_whitespace(html)
        assert "<!-- not a comment -->" in result

    def test_style_with_whitespace_end_tag_is_preserved(self, mixin):
        html = "<style>.x{content:'<!-- y -->'}</style >"
        result = mixin._strip_comments_and_whitespace(html)
        assert "<!-- y -->" in result

    def test_newline_before_script_end_tag_is_preserved(self, mixin):
        html = "<script>a = 1; // keep\nb = 2;</script\n>"
        result = mixin._strip_comments_and_whitespace(html)
        assert "// keep" in result
        assert "b = 2;" in result

    def test_script_end_tag_with_bogus_attributes_is_preserved(self, mixin):
        # Per the HTML5 tokenizer, bogus attributes on an end tag still close
        # the element: ``</script bar>`` / ``</script\t\n foo="x">`` close a
        # <script> in a browser (CodeQL py/bad-tag-filter requires matching these).
        html = '<div><script>var s = "<!-- y -->";</script foo="x"></div>'
        result = mixin._strip_comments_and_whitespace(html)
        assert "<!-- y -->" in result
        html2 = "<script>z = '<!-- w -->';</script\t\n bar>"
        result2 = mixin._strip_comments_and_whitespace(html2)
        assert "<!-- w -->" in result2

    @pytest.mark.parametrize("tag", ["pre", "code", "textarea"])
    def test_rawtext_block_with_whitespace_end_tag_preserves_inner_whitespace(self, mixin, tag):
        # Significant newlines/indent inside the block must survive the collapse;
        # pre-fix </tag > didn't match </tag>, so the block wasn't preserved and
        # the whitespace was flattened.
        html = f"<{tag}>line1\n   line2</{tag} >"
        result = mixin._strip_comments_and_whitespace(html)
        assert "line1\n   line2" in result
