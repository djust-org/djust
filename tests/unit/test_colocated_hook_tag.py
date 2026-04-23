"""
Unit tests for the {% colocated_hook %} template tag (Phoenix 1.1 parity).
"""

import pytest
from django.template import Context, Template, TemplateSyntaxError
from django.test import override_settings


def _render(tmpl_source, context=None):
    """Render a template source string with live_tags loaded."""
    full = "{% load live_tags %}" + tmpl_source
    return Template(full).render(Context(context or {}))


class TestBasicRendering:
    def test_emits_script_tag_with_type_and_data_hook(self):
        out = _render(
            '{% colocated_hook "Chart" %}'
            "hook.mounted = function() { return 1; };"
            "{% endcolocated_hook %}"
        )
        assert '<script type="djust/hook" data-hook="Chart">' in out
        assert "hook.mounted" in out
        assert out.endswith("</script>")
        # Auditor banner present for grep-ability
        assert "/* COLOCATED HOOK: Chart */" in out

    def test_missing_name_raises_template_syntax_error(self):
        with pytest.raises(TemplateSyntaxError):
            _render("{% colocated_hook %}hook.mounted = function(){};{% endcolocated_hook %}")

    def test_empty_name_raises_template_syntax_error(self):
        with pytest.raises(TemplateSyntaxError):
            _render('{% colocated_hook "" %}hook.mounted = function(){};{% endcolocated_hook %}')


class TestScriptTagEscaping:
    def test_closing_script_tag_in_body_is_escaped(self):
        out = _render(
            '{% colocated_hook "XSS" %}'
            'var x = "</script><img onerror=alert(1)>";'
            "{% endcolocated_hook %}"
        )
        # Literal </script> must NOT appear inside the hook body — must be
        # escaped so the tag isn't prematurely closed.
        # The ONLY </script> in the output is the tag's own closer.
        assert out.count("</script>") == 1
        assert "<\\/script>" in out

    def test_uppercase_closing_script_tag_is_escaped(self):
        out = _render('{% colocated_hook "Upper" %}var x = "</SCRIPT>";{% endcolocated_hook %}')
        assert "</SCRIPT>" not in out
        assert "<\\/SCRIPT>" in out

    def test_mixed_case_closing_script_tag_is_escaped(self):
        # HTML tokenizers match tag names case-insensitively, so </Script>,
        # </sCrIpT> etc. would also terminate the script block if not escaped.
        # Regression test for Stage 11 self-review finding on PR for
        # feat/phoenix-11-hook-polish-v050.
        for bad in ("</Script>", "</sCRIPT>", "</ScRiPt>", "</script>"):
            body = f'var x = "{bad}";'
            out = _render('{% colocated_hook "Mixed" %}' + body + "{% endcolocated_hook %}")
            # Raw closing tag must not appear in any casing inside the body.
            # (The tag's own </script> closer at the end is expected — use
            # rfind to isolate the body region.)
            closing_idx = out.rfind("</script>")
            body_region = out[:closing_idx]
            assert bad.lower() not in body_region.lower(), (
                f"Raw {bad!r} leaked into body region: {body_region!r}"
            )
            # Escaped form preserves original casing.
            expected_escaped = bad.replace("</", "<\\/")
            assert expected_escaped in out, f"Expected escaped {expected_escaped!r} in {out!r}"


class TestNamespacing:
    def test_namespacing_off_by_default_produces_bare_name(self):
        out = _render(
            '{% colocated_hook "Chart" %}hook.mounted=function(){};{% endcolocated_hook %}'
        )
        assert 'data-hook="Chart"' in out

    @override_settings(DJUST_CONFIG={"hook_namespacing": "strict"})
    def test_namespacing_strict_with_view_in_context(self):
        class FakeView:
            pass

        FakeView.__module__ = "myapp.views"
        FakeView.__qualname__ = "DashboardView"

        out = _render(
            '{% colocated_hook "Chart" %}hook.mounted=function(){};{% endcolocated_hook %}',
            context={"view": FakeView()},
        )
        assert 'data-hook="myapp.views.DashboardView.Chart"' in out
        assert "/* COLOCATED HOOK: myapp.views.DashboardView.Chart */" in out

    @override_settings(DJUST_CONFIG={"hook_namespacing": "strict"})
    def test_namespacing_strict_without_view_degrades_to_bare(self):
        out = _render(
            '{% colocated_hook "Chart" %}hook.mounted=function(){};{% endcolocated_hook %}'
        )
        # No `view` in context — fall back to bare name.
        assert 'data-hook="Chart"' in out

    @override_settings(DJUST_CONFIG={"hook_namespacing": "strict"})
    def test_global_keyword_opts_out_of_namespacing(self):
        class FakeView:
            pass

        FakeView.__module__ = "myapp.views"
        FakeView.__qualname__ = "DashboardView"

        out = _render(
            '{% colocated_hook "Chart" global %}hook.mounted=function(){};{% endcolocated_hook %}',
            context={"view": FakeView()},
        )
        # `global` keyword → bare name even when strict namespacing is on
        assert 'data-hook="Chart"' in out
        assert "DashboardView" not in out

    @override_settings(DJUST_CONFIG={"hook_namespacing": "strict"})
    def test_namespacing_degrades_gracefully_when_view_type_lacks_module_qualname(self):
        """Issue #817: AttributeError fallback in _namespace must hold.

        All real Python classes carry ``__module__`` and ``__qualname__``,
        so the ``except AttributeError`` fallback at live_tags.py:616 is
        dead code under normal conditions. But nothing prevents a user
        from passing a dynamic / proxy object as ``view`` whose class
        exposes descriptors that raise AttributeError for those names
        (C-extension shims, certain mocking libraries). This regression
        test pins graceful degradation so a later refactor doesn't turn
        the fallback into an outright crash.

        Uses a metaclass whose ``__qualname__`` property raises
        ``AttributeError`` on class-level access. ``type(view).__qualname__``
        then triggers the fallback path without touching the class's real
        attrs.
        """

        class _RaisingMeta(type):
            def __getattribute__(cls, name):
                if name == "__qualname__":
                    raise AttributeError("simulated C-extension quirk")
                return super().__getattribute__(name)

        class _OddView(metaclass=_RaisingMeta):
            pass

        out = _render(
            '{% colocated_hook "Chart" %}hook.mounted=function(){};{% endcolocated_hook %}',
            context={"view": _OddView()},
        )
        # Must degrade to bare "Chart" rather than raising AttributeError.
        assert 'data-hook="Chart"' in out
