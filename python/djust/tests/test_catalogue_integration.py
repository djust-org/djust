"""The catalogue as a page other sites host: names, chrome, versions, links.

The component catalogue moved from ``/theme/gallery/storybook/`` to
``/theme/components/``, the name the documentation and the marketing site
already use for it. Three things follow from being a surface those sites
point at rather than an internal page:

* the old URLs keep working, permanently redirected, so a bookmark or a link
  in a blog post still lands on the component it named;
* a host site can wrap the pages in its own chrome, by shadowing ONE template
  path — not by a setting, because djust's Rust engine resolves
  ``{% extends %}`` targets literally;
* every page says which djust it is running and links to the prose half of
  its own documentation, because the catalogue runs a release and the docs
  site pins a checkout, and those can differ.
"""

# Import conftest first to configure Django settings before we add ours.
import tests.conftest  # noqa: F401

from pathlib import Path

import pytest
from django.test import Client, override_settings

from djust import __version__ as DJUST_VERSION
from djust.theming.gallery.component_registry import (
    COMPONENT_DESCRIPTION_KEYS,
    describe_component,
)
from djust.theming.gallery.live_views import CATALOGUE_DOCUMENT_TEMPLATE, docs_anchor

pytestmark = pytest.mark.theming

_SETTINGS = {"ROOT_URLCONF": "djust.tests.urls_theming", "DJUST_THEMING_GALLERY_PUBLIC": True}


@pytest.fixture
def catalogue_urls():
    """The catalogue mounted and public — the shape a host site serves."""
    with override_settings(**_SETTINGS):
        yield


# ---------------------------------------------------------------------------
# The rename, from the outside
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestOldUrlsStillLand:
    @pytest.mark.parametrize(
        "old, new",
        [
            ("/theme/gallery/storybook/", "/theme/components/"),
            ("/theme/gallery/storybook/button/", "/theme/components/button/"),
            (
                "/theme/gallery/storybook/category/Core%20UI/",
                "/theme/components/category/Core%20UI/",
            ),
            ("/theme/gallery/", "/theme/themes/"),
            ("/theme/gallery/editor/", "/theme/themes/editor/"),
            ("/theme/gallery/diff/", "/theme/themes/diff/"),
        ],
    )
    def test_the_old_path_redirects_permanently_to_the_new_one(self, catalogue_urls, old, new):
        response = Client().get(old)
        assert response.status_code == 301, f"{old} answered {response.status_code}"
        assert response["Location"] == new

    def test_a_component_bookmark_keeps_its_component(self, catalogue_urls):
        """The redirect carries the captured name, so a bookmarked page does
        not dump the reader on the index."""
        response = Client().get("/theme/gallery/storybook/data_table/", follow=True)
        assert response.status_code == 200
        assert response.redirect_chain[-1] == ("/theme/components/data_table/", 301)
        assert b"Data Table" in response.content


# ---------------------------------------------------------------------------
# What a page says about itself
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestVersionAndDocsLinks:
    def test_the_topbar_names_the_running_version(self, catalogue_urls):
        content = Client().get("/theme/components/").content.decode()
        assert f"v{DJUST_VERSION}" in content

    def test_a_component_links_to_its_reference_entry_and_the_guide(self, catalogue_urls):
        content = Client().get("/theme/components/data_table/").content.decode()
        assert "https://docs.djust.org/reference/components/#data-table" in content
        assert "https://docs.djust.org/guides/components/" in content

    def test_the_docs_host_is_configurable(self):
        with override_settings(DJUST_THEMING_DOCS_URL="https://docs.example.test/", **_SETTINGS):
            content = Client().get("/theme/components/rating/").content.decode()
        assert "https://docs.example.test/reference/components/#rating" in content
        assert "https://docs.djust.org/reference" not in content

    def test_the_anchor_slug_is_the_one_the_docs_generator_derives(self, catalogue_urls):
        """Pinned on both sides: `docs.djust.org` builds the same slug from the
        same name, so a renamed component cannot quietly break the link."""
        assert docs_anchor("data_table") == "data-table"
        assert docs_anchor("rating") == "rating"


# ---------------------------------------------------------------------------
# The host hook
# ---------------------------------------------------------------------------


_HOST_DOCUMENT = """{% load theme_tags %}<!DOCTYPE html>
<html lang="en"><head><title>{% block title %}Components{% endblock %} — Host</title>
{% include "djust_theming/catalogue/_assets.html" %}</head>
<body><nav class="host-nav">Host navigation</nav>
{% block body %}{% endblock %}
</body></html>
"""


@pytest.fixture
def host_site(tmp_path, settings):
    """A host project shadowing the catalogue's document template.

    This is the whole override mechanism: an app (or a ``DIRS`` entry, as
    here) ahead of ``djust.theming`` that ships a file at the same path.
    """
    shadow = tmp_path / "djust_theming" / "catalogue"
    shadow.mkdir(parents=True)
    (shadow / "_document.html").write_text(_HOST_DOCUMENT)
    templates = [dict(t) for t in settings.TEMPLATES]
    for entry in templates:
        entry["DIRS"] = [str(tmp_path), *entry.get("DIRS", [])]
    with override_settings(TEMPLATES=templates, **_SETTINGS):
        yield


@pytest.mark.django_db
class TestAHostSuppliesItsOwnChrome:
    def test_the_host_document_replaces_the_framework_chrome(self, host_site):
        content = Client().get("/theme/components/button/").content.decode()
        assert "Host navigation" in content
        # The framework's topbar lives in the file the host replaced, so a
        # host page renders ONE navigation, not two.
        assert "dc-topbar" not in content

    def test_the_page_itself_is_unchanged(self, host_site):
        """Everything inside the mount root is the catalogue's, so the sidebar
        (whose search box is a server event) and the content still render."""
        content = Client().get("/theme/components/button/").content.decode()
        assert "dj-root" in content
        assert 'id="dc-sidebar"' in content
        assert "dc-content" in content
        assert "Button" in content

    def test_the_assets_partial_is_what_a_host_includes(self, host_site):
        content = Client().get("/theme/components/button/").content.decode()
        assert "djust_theming/css/catalogue.css" in content

    def test_the_default_document_is_the_frameworks_own(self):
        assert CATALOGUE_DOCUMENT_TEMPLATE == "djust_theming/catalogue/_document.html"
        assert (
            Path(__file__).resolve().parent.parent
            / "theming"
            / "templates"
            / "djust_theming"
            / "catalogue"
            / "_document.html"
        ).exists()


# ---------------------------------------------------------------------------
# The data contract the documentation generator consumes
# ---------------------------------------------------------------------------


class TestDescribeComponent:
    def test_the_shape_is_pinned(self):
        described = describe_component("rating")
        assert set(described) == set(COMPONENT_DESCRIPTION_KEYS)

    def test_a_python_component_carries_what_a_reference_entry_needs(self):
        described = describe_component("rating")
        assert described["component_type"] == "python"
        assert described["class_name"] == "Rating"
        assert "Rating" in described["import_line"]
        assert described["description"]
        names = [p["name"] for p in described["params"]]
        assert "value" in names and "max_stars" in names
        assert described["examples"], "the catalogue previews these; a snippet can use them"
        assert "set_rating" in described["events"]

    def test_a_contracted_component_carries_its_template_contract(self):
        described = describe_component("button")
        assert described["component_type"] == "template"
        assert [p["name"] for p in described["params"]]
        assert any(label == "template" for label, _path in described["style_paths"])

    def test_an_unknown_component_raises(self):
        with pytest.raises(KeyError):
            describe_component("no_such_component")

    def test_every_registered_component_can_be_described(self):
        """The generator walks the registry; one component that cannot be
        described is a hole in the reference page."""
        from djust.theming.gallery.component_registry import COMPONENT_CATEGORIES

        failures = []
        for names in COMPONENT_CATEGORIES.values():
            for name in names:
                try:
                    described = describe_component(name)
                except Exception as exc:  # noqa: BLE001 — collect them all, then report
                    failures.append(f"{name}: {type(exc).__name__}: {exc}")
                    continue
                if set(described) != set(COMPONENT_DESCRIPTION_KEYS):
                    failures.append(f"{name}: wrong keys")
        assert failures == [], failures
