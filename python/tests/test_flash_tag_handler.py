"""
Tests for DjFlashTagHandler — the Rust template engine tag handler for dj_flash.

Covers rendering with defaults, custom args, and XSS sanitization of position.
"""

from djust.template_tags.flash import DjFlashTagHandler


class TestDjFlashTagHandlerDefaults:
    def test_render_no_args(self):
        handler = DjFlashTagHandler()
        html = handler.render([], {})

        assert 'id="dj-flash-container"' in html
        assert 'class="dj-flash-container"' in html
        assert 'data-dj-auto-dismiss="5000"' in html
        assert 'dj-update="ignore"' in html
        assert 'aria-live="polite"' in html

    def test_render_custom_auto_dismiss(self):
        handler = DjFlashTagHandler()
        html = handler.render(["auto_dismiss=8000"], {})

        assert 'data-dj-auto-dismiss="8000"' in html

    def test_render_custom_position(self):
        handler = DjFlashTagHandler()
        html = handler.render(["position='top-right'"], {})

        assert "dj-flash-top-right" in html

    def test_render_both_args(self):
        handler = DjFlashTagHandler()
        html = handler.render(["auto_dismiss=3000", "position='bottom-left'"], {})

        assert 'data-dj-auto-dismiss="3000"' in html
        assert "dj-flash-bottom-left" in html


class TestDjFlashTagHandlerXSSSanitization:
    def test_position_strips_script_injection(self):
        handler = DjFlashTagHandler()
        html = handler.render(["position='\"><script>alert(1)</script>'"], {})

        assert "<script>" not in html
        assert "alert(1)" not in html
        # Only alphanumeric and hyphens survive — angle brackets/parens stripped
        assert "dj-flash-scriptalert1script" in html

    def test_position_strips_html_entities(self):
        handler = DjFlashTagHandler()
        html = handler.render(["position='top&right'"], {})

        # & is stripped, only alphanumeric and hyphens allowed
        assert "dj-flash-topright" in html

    def test_position_strips_spaces(self):
        handler = DjFlashTagHandler()
        html = handler.render(["position='top right'"], {})

        assert "dj-flash-topright" in html

    def test_position_allows_hyphens(self):
        handler = DjFlashTagHandler()
        html = handler.render(["position='top-center'"], {})

        assert "dj-flash-top-center" in html

    def test_invalid_auto_dismiss_uses_default(self):
        handler = DjFlashTagHandler()
        html = handler.render(["auto_dismiss=notanumber"], {})

        assert 'data-dj-auto-dismiss="5000"' in html

    def test_format_html_escapes_output(self):
        """Verify format_html is used (not f-string) by checking return type."""
        handler = DjFlashTagHandler()
        html = handler.render([], {})

        # format_html returns a SafeString, which is a subclass of str
        from django.utils.safestring import SafeData

        assert isinstance(html, SafeData)
