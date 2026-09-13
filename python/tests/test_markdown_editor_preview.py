"""Preview parity and XSS safety across the editor's public render paths."""

import pytest
from django.template import Context, Variable

from djust.components.components.markdown_editor import MarkdownEditor
from djust.components.templatetags._dev_tools import MarkdownEditorNode
from djust.components.rust_handlers import MarkdownEditorHandler


@pytest.mark.parametrize(
    "source", ["**Hello**", "<script>alert(1)</script>\n\n[bad](javascript:alert(1))"]
)
def test_editor_preview_uses_sanitized_markdown_in_all_renderers(source):
    outputs = [
        MarkdownEditor(name="body", value=source)._render_custom(),
        str(
            MarkdownEditorNode({"name": "body", "value": Variable("source")}).render(
                Context({"source": source})
            )
        ),
        str(MarkdownEditorHandler().render(['name="body"', "value=source"], {"source": source})),
    ]
    for output in outputs:
        preview = output.split('aria-label="Preview">', 1)[1].split("</div>", 1)[0]
        assert "<script>" not in preview
        assert 'href="javascript:' not in preview
        if source == "**Hello**":
            assert "<strong>Hello</strong>" in preview


def test_preview_false_keeps_plain_editing_without_preview():
    assert (
        'aria-label="Preview"' not in MarkdownEditor(value="text", preview=False)._render_custom()
    )


def test_visual_mode_declares_a_stable_controls_host_in_every_renderer():
    outputs = [
        str(MarkdownEditor(name="body", value="Hello", mode="visual")),
        str(MarkdownEditorNode({"name": "body", "mode": "visual"}).render(Context())),
        str(MarkdownEditorHandler().render(['name="body"', 'mode="visual"'], {})),
    ]
    for output in outputs:
        assert 'data-mode="visual"' in output
        assert 'data-markdown-editor="visual"' in output
        assert 'data-markdown-ui dj-update="ignore"' in output


def test_invalid_component_mode_is_rejected():
    with pytest.raises(ValueError, match="mode must be"):
        MarkdownEditor(mode='" onclick="bad()')
