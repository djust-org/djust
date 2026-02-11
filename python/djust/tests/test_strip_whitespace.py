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
        """Whitespace OUTSIDE textarea is still collapsed."""
        html = "<div>   <textarea>keep\nme</textarea>   </div>"
        result = mixin._strip_comments_and_whitespace(html)
        assert "keep\nme" in result
        # Runs of whitespace outside are collapsed to single space
        assert "<div> <textarea>" in result

    def test_whitespace_between_sibling_tags_collapsed(self, mixin):
        """Whitespace between sibling tags is collapsed to single space."""
        html = "<span>a</span>   \n   <textarea>keep\nme</textarea>"
        result = mixin._strip_comments_and_whitespace(html)
        assert "keep\nme" in result
        # Multi-whitespace between close/open tags collapsed to single space
        assert "</span> <textarea>" in result

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
        html = "<div>\n" "  <textarea>text\narea</textarea>\n" "  <pre>pre\nblock</pre>\n" "</div>"
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

    def test_private_attrs_excluded(self):
        """Underscore-prefixed attrs should not appear in snapshot."""
        from djust.live_view import LiveView
        from djust.websocket import _snapshot_assigns

        view = LiveView()
        view.count = 0
        view._private = "hidden"

        snap = _snapshot_assigns(view)
        assert "count" in snap
        assert "_private" not in snap

    def test_list_same_content_no_render(self):
        """Reassigning a list with identical content should match (no render)."""
        from djust.live_view import LiveView
        from djust.websocket import _snapshot_assigns

        view = LiveView()
        view.items = [1, 2, 3]

        snap1 = _snapshot_assigns(view)
        view.items = [1, 2, 3]  # New list object, same content
        snap2 = _snapshot_assigns(view)
        assert snap1 == snap2  # Same content → no render needed

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
