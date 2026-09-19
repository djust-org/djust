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

import re
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
# The index cards
# ---------------------------------------------------------------------------


class TestCardThumbnails:
    """A card shows the component, not just its name.

    The thumbnail used to answer "" for anything outside
    ``COMPONENT_CONTRACTS``, because the registry once carried examples for
    the contracted components only. It now carries them for the python ones
    too, so that rule was blanking nine cards out of ten on a page whose job
    is to let someone recognise a component by looking at it.
    """

    #: Renders nothing until opened, so a blank card is the honest answer.
    #: A preview of a closed modal would be a preview of nothing.
    OPENS_ON_DEMAND = {
        "bottom_sheet",
        "export_dialog",
        "image_lightbox",
        "server_event_toast",
        "tour",
    }
    #: Known gaps: no registry example yet. Kept explicit so the number
    #: cannot creep up unnoticed.
    NO_EXAMPLE_YET = {"form_validation", "prompt_editor"}

    def _names(self):
        from djust.theming.gallery.component_registry import COMPONENT_CATEGORIES

        seen, names = set(), []
        for group in COMPONENT_CATEGORIES.values():
            for name in group:
                if name not in seen:
                    seen.add(name)
                    names.append(name)
        return names

    def test_almost_every_card_shows_its_component(self):
        from djust.theming.templatetags.theme_tags import _thumbnail_html

        blank = [n for n in self._names() if not (_thumbnail_html(n) or "").strip()]
        assert set(blank) <= self.OPENS_ON_DEMAND | self.NO_EXAMPLE_YET, (
            "these cards render no preview and are not accounted for: "
            f"{sorted(set(blank) - self.OPENS_ON_DEMAND - self.NO_EXAMPLE_YET)}"
        )

    def test_a_python_component_gets_a_thumbnail(self):
        """The specific regression: `rating` is not a template contract."""
        from djust.theming.contracts import COMPONENT_CONTRACTS
        from djust.theming.templatetags.theme_tags import _thumbnail_html

        assert "rating" not in COMPONENT_CONTRACTS
        assert "rating-star" in _thumbnail_html("rating")

    def test_a_contracted_component_still_gets_one(self):
        from djust.theming.templatetags.theme_tags import _thumbnail_html

        assert _thumbnail_html("alert").strip()

    def test_a_thumbnail_never_nests_a_link_inside_the_cards_link(self):
        """The card is an ``<a>``. An ``<a>`` inside it is invalid HTML, and
        the browser fixes it by closing the card's link early — which lifts
        the card out of its own link and leaves an empty anchor holding a
        grid cell. Thirteen components render links, so the index had
        thirteen holes in it."""
        from djust.theming.templatetags.theme_tags import _thumbnail_html

        offenders = []
        for name in self._names():
            html = _thumbnail_html(name) or ""
            if re.search(r"<a\b", html) or re.search(r"<button\b", html):
                offenders.append(name)
        assert offenders == [], (
            "these thumbnails still contain an interactive tag, which splits "
            f"the card out of its link: {offenders}"
        )

    def test_a_thumbnail_keeps_the_classes_that_style_it(self):
        """Neutralising the tag must not neutralise the look: a preview of a
        breadcrumb still has to look like a breadcrumb."""
        from djust.theming.templatetags.theme_tags import _inert_markup

        html = _inert_markup('<a href="/" class="breadcrumb-link" aria-current="page">Home</a>')
        assert html == '<span class="breadcrumb-link" aria-current="page">Home</span>'

    def test_escaped_markup_in_a_code_preview_is_left_alone(self):
        """`code_snippet` previews HTML as TEXT; it is not a tag to rewrite."""
        from djust.theming.templatetags.theme_tags import _inert_markup

        source = "<pre>&lt;a href=&quot;/&quot;&gt;link&lt;/a&gt;</pre>"
        assert _inert_markup(source) == source

    def test_an_unknown_component_is_blank_not_an_error(self):
        from djust.theming.templatetags.theme_tags import _thumbnail_html

        assert _thumbnail_html("no_such_component") == ""


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

    def test_parameter_descriptions_come_from_the_components_own_docstring(self):
        """Neither the signature nor the template contract records what a
        parameter MEANS; the class's Args block does, and both the catalogue's
        props table and the generated reference were showing an em dash in
        every row until this read it."""
        described = describe_component("rating")
        docs = {param["name"]: param["doc"] for param in described["params"]}
        assert "current rating value" in docs["value"]
        assert docs["max_stars"]

        # A contracted component has no descriptions of its own, but most are
        # also a Python class whose docstring documents the same names.
        contracted = describe_component("alert")
        assert contracted["component_type"] == "template"
        assert (
            "Alert text content" in {p["name"]: p["doc"] for p in contracted["params"]}["message"]
        )

    def test_a_contracted_component_names_its_python_class_too(self):
        """`{% theme_alert %}` and `Alert` are two ways to render ONE
        component, and a reader who found either needs to know about the
        other."""
        described = describe_component("alert")
        python_class = described["python_class"]
        assert python_class["class_name"] == "Alert"
        assert python_class["import_line"] == "from djust.components import Alert"
        assert [p["name"] for p in python_class["params"]][:2] == ["message", "variant"]
        # A component that IS a class carries it in its own keys instead.
        assert describe_component("rating")["python_class"] is None

    def test_a_sentinel_default_is_named_rather_than_addressed(self):
        """`repr(object())` carries a memory address, so a page generated
        from it changes on every run and tells a reader nothing."""
        from djust.theming.gallery.component_registry import NOT_SUPPLIED

        defaults = {p["name"]: p["default"] for p in describe_component("status_dot")["params"]}
        assert defaults["animate"] == NOT_SUPPLIED
        assert "object at 0x" not in " ".join(str(v) for v in defaults.values())

    def test_required_and_kind_are_read_not_guessed(self):
        """A parameter defaulting to `None` is not required, and `**kwargs`
        is a VAR_KEYWORD rather than a parameter named "kwargs"."""
        params = {p["name"]: p for p in describe_component("alert")["python_class"]["params"]}
        assert params["message"]["required"] is True
        assert params["variant"]["required"] is False
        assert params["icon"]["required"] is False, "a None default is not a missing default"
        assert params["kwargs"]["kind"] == "VAR_KEYWORD"

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
