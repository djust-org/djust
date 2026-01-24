"""
Test template inheritance resolution for Djust.

Tests the Python-level template inheritance resolution that handles
{% extends %} and {% block %} tags before passing to the Rust renderer.

These tests verify:
1. Single-level inheritance (child extends parent)
2. Multi-level inheritance (grandchild extends child extends parent)
3. Block override behavior
4. Empty block handling
5. Edge cases
"""

import re
import tempfile
from pathlib import Path

import pytest


class MockBackend:
    """Mock backend for testing template inheritance resolution."""

    def __init__(self, template_dirs):
        self.template_dirs = [Path(d) for d in template_dirs]


class TestTemplateInheritanceResolution:
    """Test the _resolve_template_inheritance method."""

    def create_template(self, template_string, template_dirs):
        """Create a DjustTemplate instance for testing."""
        from djust.template_backend import DjustTemplate

        backend = MockBackend(template_dirs)
        template = DjustTemplate.__new__(DjustTemplate)
        template.template_string = template_string
        template.backend = backend
        return template

    def test_single_level_inheritance(self):
        """Test basic single-level inheritance (child extends parent)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Create parent template
            parent = tmpdir / "base.html"
            parent.write_text("""<!DOCTYPE html>
<html>
<head><title>{% block title %}Default{% endblock %}</title></head>
<body>
{% block content %}Default content{% endblock %}
</body>
</html>""")

            # Create child template
            child_source = """{% extends "base.html" %}
{% block title %}My Page{% endblock %}
{% block content %}My content{% endblock %}"""

            template = self.create_template(child_source, [tmpdir])
            resolved = template._resolve_template_inheritance()

            assert "My Page" in resolved
            assert "My content" in resolved
            assert "Default content" not in resolved
            assert "<!DOCTYPE html>" in resolved

    def test_multi_level_inheritance_two_levels(self):
        """Test two-level inheritance (child extends parent extends grandparent)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Create grandparent (root) template
            grandparent = tmpdir / "base.html"
            grandparent.write_text("""<!DOCTYPE html>
<html>
<head><title>{% block title %}Site{% endblock %}</title></head>
<body>
<nav>Navigation</nav>
{% block content %}{% endblock %}
<footer>Footer</footer>
</body>
</html>""")

            # Create parent (middle) template
            parent = tmpdir / "base_docs.html"
            parent.write_text("""{% extends "base.html" %}
{% block content %}
<div class="docs-layout">
    <aside>Sidebar</aside>
    <main>{% block docs_content %}Docs default{% endblock %}</main>
</div>
{% endblock %}""")

            # Create child (leaf) template
            child_source = """{% extends "base_docs.html" %}
{% block title %}Getting Started{% endblock %}
{% block docs_content %}
<h1>Getting Started</h1>
<p>Welcome to the docs!</p>
{% endblock %}"""

            template = self.create_template(child_source, [tmpdir])
            resolved = template._resolve_template_inheritance()

            # Should have grandparent structure
            assert "<!DOCTYPE html>" in resolved
            assert "<nav>Navigation</nav>" in resolved
            assert "<footer>Footer</footer>" in resolved

            # Should have parent structure
            assert '<div class="docs-layout">' in resolved
            assert "<aside>Sidebar</aside>" in resolved

            # Should have child content
            assert "Getting Started" in resolved
            assert "<h1>Getting Started</h1>" in resolved
            assert "Welcome to the docs!" in resolved

            # Should NOT have defaults
            assert "Docs default" not in resolved

    def test_multi_level_inheritance_three_levels(self):
        """Test three-level inheritance (great-grandchild -> grandchild -> child -> parent)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Create great-grandparent (root) template
            (tmpdir / "root.html").write_text("""<!DOCTYPE html>
<html>
<body>
{% block wrapper %}
<div class="root">{% block content %}ROOT{% endblock %}</div>
{% endblock %}
</body>
</html>""")

            # Create grandparent template
            (tmpdir / "grandparent.html").write_text("""{% extends "root.html" %}
{% block content %}
<section class="grandparent">{% block section %}GRANDPARENT{% endblock %}</section>
{% endblock %}""")

            # Create parent template
            (tmpdir / "parent.html").write_text("""{% extends "grandparent.html" %}
{% block section %}
<article class="parent">{% block article %}PARENT{% endblock %}</article>
{% endblock %}""")

            # Create child (leaf) template
            child_source = """{% extends "parent.html" %}
{% block article %}
<div class="child">CHILD CONTENT</div>
{% endblock %}"""

            template = self.create_template(child_source, [tmpdir])
            resolved = template._resolve_template_inheritance()

            # Should have the full hierarchy structure
            assert "<!DOCTYPE html>" in resolved
            assert '<div class="root">' in resolved
            assert '<section class="grandparent">' in resolved
            assert '<article class="parent">' in resolved
            assert '<div class="child">CHILD CONTENT</div>' in resolved

            # Should NOT have any default placeholders
            assert "ROOT" not in resolved
            assert "GRANDPARENT" not in resolved
            assert "PARENT" not in resolved

    def test_empty_block_override(self):
        """Test that empty blocks properly override parent content."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Create parent template
            parent = tmpdir / "base.html"
            parent.write_text("""<!DOCTYPE html>
<html>
<body>
{% block header %}Default Header{% endblock %}
{% block content %}Default Content{% endblock %}
</body>
</html>""")

            # Create child template with empty header block
            child_source = """{% extends "base.html" %}
{% block header %}{% endblock %}
{% block content %}My Content{% endblock %}"""

            template = self.create_template(child_source, [tmpdir])
            resolved = template._resolve_template_inheritance()

            assert "Default Header" not in resolved
            assert "My Content" in resolved
            assert "Default Content" not in resolved

    def test_block_with_whitespace_and_newlines(self):
        """Test blocks with various whitespace patterns."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Create parent template
            parent = tmpdir / "base.html"
            parent.write_text("""<html>
<body>
{%   block   content   %}
Default
{% endblock  content  %}
</body>
</html>""")

            # Create child template
            child_source = """{%  extends   "base.html"  %}
{%block content%}
<div>
    Multi-line
    Content
</div>
{%endblock%}"""

            template = self.create_template(child_source, [tmpdir])
            resolved = template._resolve_template_inheritance()

            assert "Multi-line" in resolved
            assert "Content" in resolved
            assert "Default" not in resolved

    def test_nested_blocks_not_extracted_incorrectly(self):
        """Test that nested blocks within content are handled correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Create parent with nested block
            parent = tmpdir / "base.html"
            parent.write_text("""<html>
{% block content %}
<div>
{% block inner %}Inner Default{% endblock %}
</div>
{% endblock %}
</html>""")

            # Child that only overrides inner block
            child_source = """{% extends "base.html" %}
{% block inner %}New Inner{% endblock %}"""

            template = self.create_template(child_source, [tmpdir])
            resolved = template._resolve_template_inheritance()

            # The outer block should be preserved, inner should be replaced
            assert "New Inner" in resolved
            assert "Inner Default" not in resolved

    def test_parent_template_not_found(self):
        """Test error handling when parent template doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Child template extending non-existent parent
            child_source = """{% extends "nonexistent.html" %}
{% block content %}Test{% endblock %}"""

            template = self.create_template(child_source, [tmpdir])

            from djust.template_backend import TemplateDoesNotExist

            with pytest.raises(TemplateDoesNotExist):
                template._resolve_template_inheritance()

    def test_block_preservation_through_inheritance(self):
        """Test that blocks are properly preserved through inheritance chain."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Root template
            (tmpdir / "root.html").write_text("""<html>
{% block a %}A{% endblock %}
{% block b %}B{% endblock %}
{% block c %}C{% endblock %}
</html>""")

            # Middle template overrides block b only
            (tmpdir / "middle.html").write_text("""{% extends "root.html" %}
{% block b %}B-MIDDLE{% endblock %}""")

            # Leaf template overrides block c only
            leaf_source = """{% extends "middle.html" %}
{% block c %}C-LEAF{% endblock %}"""

            template = self.create_template(leaf_source, [tmpdir])
            resolved = template._resolve_template_inheritance()

            # Verify the exact structure: block wrappers stripped, content preserved
            # The resolved output should be: <html>\nA\nB-MIDDLE\nC-LEAF\n</html>
            assert "<html>" in resolved
            assert "</html>" in resolved

            # Block a should have root default (single 'A' not part of another word)
            # Remove known strings to check 'A' appears on its own
            resolved_without_known = resolved.replace("B-MIDDLE", "").replace("C-LEAF", "")
            assert "\nA\n" in resolved_without_known or "A\n" in resolved_without_known

            # Block b should have middle override
            assert "B-MIDDLE" in resolved

            # Block c should have leaf override
            assert "C-LEAF" in resolved

            # Original defaults 'B' and 'C' (standalone) should not be present
            # Check there's no standalone 'B' (only B-MIDDLE should exist)
            # Match standalone B or C (word boundary)
            assert not re.search(r"\bB\b(?!-)", resolved), f"Found standalone 'B' in: {resolved}"
            assert not re.search(r"\bC\b(?!-)", resolved), f"Found standalone 'C' in: {resolved}"


class TestDjustOrgDocsInheritance:
    """Test cases that mirror the djust.org documentation page structure."""

    def create_template(self, template_string, template_dirs):
        """Create a DjustTemplate instance for testing."""
        from djust.template_backend import DjustTemplate

        backend = MockBackend(template_dirs)
        template = DjustTemplate.__new__(DjustTemplate)
        template.template_string = template_string
        template.backend = backend
        return template

    def test_docs_page_structure(self):
        """
        Test the exact structure used by djust.org docs pages:
        getting-started.html -> base_docs.html -> base.html
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Create base.html (like djust.org's marketing/base.html)
            (tmpdir / "base.html").write_text("""<!DOCTYPE html>
<html>
<head>
    <title>{% block title %}djust{% endblock %}</title>
</head>
<body>
    <nav>Navbar</nav>
    <main>{% block content %}{% endblock %}</main>
    <footer>Footer</footer>
</body>
</html>""")

            # Create base_docs.html (like djust.org's marketing/docs/base_docs.html)
            (tmpdir / "base_docs.html").write_text("""{% extends "base.html" %}
{% block title %}{{ page_title|default:"Documentation" }} | djust{% endblock %}
{% block content %}
<div class="pt-16 min-h-screen bg-brand-dark">
    <div class="max-w-7xl mx-auto px-6 py-12">
        <div class="flex flex-col lg:flex-row gap-8">
            <aside class="lg:w-64 flex-shrink-0">
                <nav class="sticky top-20">Sidebar Navigation</nav>
            </aside>
            <main class="flex-1 min-w-0">
                <article class="docs-prose">
                    {% block docs_content %}{% endblock %}
                </article>
            </main>
        </div>
    </div>
</div>
{% endblock %}""")

            # Create getting-started.html (like djust.org's getting-started.html)
            child_source = """{% extends "base_docs.html" %}
{% block title %}Getting Started | djust{% endblock %}
{% block docs_content %}
<h1>Getting Started</h1>
<p class="text-xl text-brand-text mb-8">
    Get djust up and running in your Django project in minutes.
</p>
<h2>Installation</h2>
<p>pip install djust</p>
{% endblock %}"""

            template = self.create_template(child_source, [tmpdir])
            resolved = template._resolve_template_inheritance()

            # Should have base.html structure
            assert "<!DOCTYPE html>" in resolved
            assert "<nav>Navbar</nav>" in resolved
            assert "<footer>Footer</footer>" in resolved

            # Should have base_docs.html structure
            assert 'class="pt-16 min-h-screen bg-brand-dark"' in resolved
            assert 'class="docs-prose"' in resolved
            assert "Sidebar Navigation" in resolved

            # Should have getting-started.html content
            assert "<h1>Getting Started</h1>" in resolved
            assert "Get djust up and running" in resolved
            assert "<h2>Installation</h2>" in resolved
            assert "pip install djust" in resolved

            # Title should be from child
            assert "<title>Getting Started | djust</title>" in resolved


class TestBlockSuperSupport:
    """Test {{ block.super }} functionality (if supported)."""

    def create_template(self, template_string, template_dirs):
        """Create a DjustTemplate instance for testing."""
        from djust.template_backend import DjustTemplate

        backend = MockBackend(template_dirs)
        template = DjustTemplate.__new__(DjustTemplate)
        template.template_string = template_string
        template.backend = backend
        return template

    @pytest.mark.skip(reason="block.super not yet implemented in Python resolver")
    def test_block_super_basic(self):
        """Test basic {{ block.super }} functionality."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Create parent template
            parent = tmpdir / "base.html"
            parent.write_text("""<html>
{% block content %}
<div class="parent">Parent Content</div>
{% endblock %}
</html>""")

            # Create child that uses block.super
            child_source = """{% extends "base.html" %}
{% block content %}
{{ block.super }}
<div class="child">Child Content</div>
{% endblock %}"""

            template = self.create_template(child_source, [tmpdir])
            resolved = template._resolve_template_inheritance()

            # Should have both parent and child content
            assert "Parent Content" in resolved
            assert "Child Content" in resolved
