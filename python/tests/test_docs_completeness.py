"""
TDD: Documentation completeness tests.

These tests verify that all required documentation files exist and contain
the expected content sections. They run as part of the standard Python test suite.
"""

import os
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_ROOT = _REPO_ROOT / "docs" / "website"
README_PATH = _REPO_ROOT / "README.md"


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# File existence
# ---------------------------------------------------------------------------


class TestDocFilesExist:
    def test_css_frameworks_guide_exists(self):
        """docs/website/guides/css-frameworks.md must exist (README links to it)."""
        path = os.path.join(DOCS_ROOT, "guides", "css-frameworks.md")
        assert os.path.isfile(
            path
        ), "docs/website/guides/css-frameworks.md is missing. README line 454 links to it."

    def test_template_cheatsheet_exists(self):
        """docs/website/guides/template-cheatsheet.md must exist."""
        path = os.path.join(DOCS_ROOT, "guides", "template-cheatsheet.md")
        assert os.path.isfile(path), "docs/website/guides/template-cheatsheet.md is missing."

    def test_vdom_architecture_exists(self):
        path = os.path.join(DOCS_ROOT, "advanced", "vdom-architecture.md")
        assert os.path.isfile(path)

    def test_deployment_guide_exists(self):
        path = os.path.join(DOCS_ROOT, "guides", "deployment.md")
        assert os.path.isfile(path)


# ---------------------------------------------------------------------------
# README content
# ---------------------------------------------------------------------------


class TestReadmeContent:
    def test_readme_has_vdom_section(self):
        """README must have a section explaining the VDOM/reactivity model."""
        content = _read(README_PATH)
        assert "dj-root" in content, "README must document dj-root attribute"
        assert "dj-view" in content, "README must document dj-view attribute"

    def test_readme_documents_dj_key(self):
        """README must document keyed list diffing."""
        content = _read(README_PATH)
        assert (
            "dj-key" in content or "data-key" in content
        ), "README must document keyed list diffing (dj-key or data-key)"

    def test_readme_has_installed_apps_setup(self):
        """README must show INSTALLED_APPS setup step."""
        content = _read(README_PATH)
        assert "INSTALLED_APPS" in content

    def test_readme_has_asgi_setup(self):
        """README must show asgi.py setup step."""
        content = _read(README_PATH)
        assert "asgi.py" in content or "ProtocolTypeRouter" in content

    def test_readme_css_framework_link_points_to_existing_file(self):
        """The CSS framework link in README must resolve to an existing file."""
        content = _read(README_PATH)
        # Check specifically that the css-frameworks.md link resolves
        assert "css-frameworks.md" in content, "README must link to css-frameworks.md"
        # Find the css-frameworks.md link target
        links = re.findall(r"\[.*?\]\((docs/[^)]*css-frameworks[^)]*)\)", content)
        assert links, "README must have a link to css-frameworks.md"
        for link in links:
            full_path = _REPO_ROOT / link
            assert os.path.isfile(full_path), f"Link target '{link}' in README does not exist"

    def test_readme_documents_one_sided_if_pitfall(self):
        """README must warn about the one-sided {% if %} in class attribute pitfall."""
        content = _read(README_PATH)
        # Look for any mention of the pitfall
        has_pitfall = (
            "one-sided" in content.lower()
            or ("class" in content and "{% if" in content and "pitfall" in content.lower())
            or "{% if" in content
            and "class=" in content
            and "{% endif" in content
        )
        assert (
            has_pitfall
        ), "README should document the one-sided {%% if %%} in class attribute pitfall"

    def test_readme_links_to_template_cheatsheet(self):
        """README must link to the template cheat sheet."""
        content = _read(README_PATH)
        assert (
            "template-cheatsheet" in content.lower() or "cheat sheet" in content.lower()
        ), "README must link to template cheat sheet"

    def test_readme_links_to_vdom_architecture(self):
        """README must link to the VDOM architecture guide."""
        content = _read(README_PATH)
        assert (
            "vdom-architecture" in content.lower() or "vdom architecture" in content.lower()
        ), "README must link to VDOM architecture guide"

    def test_readme_links_to_deployment_guide(self):
        """README must link to the deployment guide."""
        content = _read(README_PATH)
        assert "deployment" in content.lower(), "README must reference deployment"

    def test_readme_references_phoenix_liveview(self):
        """README must reference Phoenix LiveView for context."""
        content = _read(README_PATH)
        assert (
            "phoenix" in content.lower() or "liveview" in content.lower()
        ), "README must reference Phoenix LiveView"

    def test_readme_has_getting_started_section(self):
        """README must have a Getting Started section with step-by-step guide."""
        content = _read(README_PATH)
        assert (
            "getting started" in content.lower() or "step 1" in content.lower()
        ), "README must have a getting started walkthrough"


# ---------------------------------------------------------------------------
# Template cheat sheet content
# ---------------------------------------------------------------------------


class TestTemplateCheatsheet:
    def _content(self):
        path = os.path.join(DOCS_ROOT, "guides", "template-cheatsheet.md")
        return _read(path)

    def test_documents_dj_click(self):
        assert "dj-click" in self._content()

    def test_documents_dj_input(self):
        assert "dj-input" in self._content()

    def test_documents_dj_submit(self):
        assert "dj-submit" in self._content()

    def test_documents_dj_model(self):
        assert "dj-model" in self._content()

    def test_documents_dj_loading(self):
        assert "dj-loading" in self._content()

    def test_documents_dj_root(self):
        assert "dj-root" in self._content()

    def test_documents_dj_key_or_data_key(self):
        content = self._content()
        assert "dj-key" in content or "data-key" in content

    def test_documents_one_sided_if_pitfall(self):
        content = self._content()
        # Must have a pitfall/warning section about {% if %} in class attributes
        has_warning = (
            "pitfall" in content.lower()
            or "warning" in content.lower()
            or "caution" in content.lower()
            or "gotcha" in content.lower()
        )
        assert has_warning, "Template cheatsheet must include a pitfall/warning section"

    def test_has_title(self):
        assert self._content().startswith("---") or self._content().startswith(
            "#"
        ), "Template cheatsheet must have a title"


# ---------------------------------------------------------------------------
# CSS Frameworks guide content
# ---------------------------------------------------------------------------


class TestCssFrameworksGuide:
    def _content(self):
        path = os.path.join(DOCS_ROOT, "guides", "css-frameworks.md")
        return _read(path)

    def test_documents_tailwind(self):
        assert "tailwind" in self._content().lower()

    def test_documents_bootstrap(self):
        assert "bootstrap" in self._content().lower()

    def test_documents_setup_command(self):
        assert "djust_setup_css" in self._content()

    def test_documents_liveview_config_css_framework(self):
        assert "css_framework" in self._content()

    def test_documents_production_minification(self):
        content = self._content()
        assert "--minify" in content or "minif" in content.lower()


# ---------------------------------------------------------------------------
# VDOM architecture doc
# ---------------------------------------------------------------------------


class TestVdomArchitectureDoc:
    def _content(self):
        path = os.path.join(DOCS_ROOT, "advanced", "vdom-architecture.md")
        return _read(path)

    def test_documents_dj_root(self):
        assert "dj-root" in self._content()

    def test_documents_dj_key_or_data_key(self):
        content = self._content()
        assert "dj-key" in content or "data-key" in content

    def test_documents_one_sided_if_pitfall(self):
        content = self._content()
        has_pitfall = "one-sided" in content.lower() or (
            "if" in content
            and "class" in content
            and ("pitfall" in content.lower() or "warning" in content.lower())
        )
        assert has_pitfall, "vdom-architecture.md must document the one-sided {% if %} pitfall"

    def test_documents_form_value_preservation(self):
        content = self._content()
        assert "form" in content.lower() and (
            "value" in content.lower() or "preserv" in content.lower()
        ), "vdom-architecture.md must mention form value preservation"


# ---------------------------------------------------------------------------
# Components guide
# ---------------------------------------------------------------------------


class TestComponentsGuide:
    def _content(self):
        path = os.path.join(DOCS_ROOT, "guides", "components.md")
        return _read(path)

    def test_components_guide_exists(self):
        path = os.path.join(DOCS_ROOT, "guides", "components.md")
        assert os.path.isfile(path), "docs/website/guides/components.md must exist"

    def test_documents_register_component(self):
        assert "register_component" in self._content()

    def test_documents_theming(self):
        content = self._content().lower()
        assert (
            "css" in content or "theme" in content or "styling" in content
        ), "Components guide must cover theming/styling"


# ---------------------------------------------------------------------------
# Deployment guide content
# ---------------------------------------------------------------------------


class TestDeploymentGuide:
    def _content(self):
        path = os.path.join(DOCS_ROOT, "guides", "deployment.md")
        return _read(path)

    def test_documents_uvicorn_or_daphne(self):
        content = self._content().lower()
        assert (
            "uvicorn" in content or "daphne" in content
        ), "Deployment guide must mention ASGI server"

    def test_documents_nginx_or_load_balancer(self):
        content = self._content().lower()
        assert (
            "nginx" in content or "load balanc" in content
        ), "Deployment guide must cover load balancing"

    def test_documents_websocket_configuration(self):
        content = self._content().lower()
        assert "websocket" in content, "Deployment guide must cover WebSocket configuration"
