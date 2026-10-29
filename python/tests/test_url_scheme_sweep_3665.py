"""Every URL parameter of djust's template tags and components is checked, in both engines (#3665).

``{% avatar src=u %}`` printed ``javascript:alert(1)`` under Django's engine while
the native handler printed ``#``. The audit that found it drove only the
parameters NAMED like URLs. ``scripts/template-library-url-sweep.py`` removes the
name from the question (``scripts/lib/template_library_parity/url_sweep.py``):

* it drives EVERY parameter each tag can read, derived from the code (the Python
  signature, the keys the compile function and the native handler read, followed
  through their helpers), not a typed list, so a tag or parameter added tomorrow is
  swept by this test with no edit;
* with every payload (plain, case-folded, whitespace- and control-character-
  obfuscated, entity- and percent-encoded, ``vbscript:``, ``data:text/html``), in four
  argument shapes and as a ``mark_safe`` value, and once with the payload stored
  under a key other arguments NAME (how a table reaches a URL it was not handed);
* through BOTH real engines, and reads the FINAL attributes of the output, so a
  value that reaches a sink by a path no sanitiser guards is found whatever it is
  called (the #1825 lesson: validation that runs before a decode is defeated by
  the encoded form, so the encoded forms are in the corpus).

It runs in subprocesses (it replaces ``uuid`` and ``datetime`` for its whole
process, like the parity harness) and takes about two minutes.

What each class proves:

* ``TestScanner`` — the scanner reads attributes the way a browser does; it is not
  a tautology: each case is classified differently if the rule changes.
* ``TestTheSweep`` — no hole in either engine; the two engines accept and reject
  the same URLs; every tag was driven and rendered; the instrument saw URLs arrive
  at attributes (it did not pass because nothing was ever emitted).
* ``TestGateOffs`` — a planted hole IS found, end to end; and the avatar fix is
  load-bearing (``scripts/url-sweep-gateoff.py`` reverts each fix in a throwaway
  copy of the tree and the test must turn red).

Refs #3665, #3663, #1825, #1468, #1646, #1125.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.template.library import import_library

from scripts.lib.template_library_parity import url_sweep

pytestmark = pytest.mark.pr_tier

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "template-library-url-sweep.py"
TIMEOUT = 900

#: Tags with no named parameter to drive, and why (the sweep derives this; the pin
#: makes a NEW parameterless tag a visible decision rather than a silent skip).
NO_PARAMETERS = {
    "djust_components:kbd": "takes positional key names only; nothing URL-shaped",
    "theme_tags:theme_asset_version": "no arguments",
    "theme_tags:theme_css_link": "no arguments",
    "theme_tags:theme_mode": "no arguments",
    "theme_tags:theme_preset": "no arguments",
    "theme_tags:theme_resolved_mode": "no arguments",
    "theme_form_tags:get_css_prefix": "no arguments",
}

#: Cases where ONE engine keeps a URL the other does not, with the reason. Not a
#: scheme difference: ``nav_item`` is a block tag; the native handler turns a tag
#: with a body into a dropdown parent (no link), the Python node renders its link
#: inside ``nav_menu``. Both pass ``href`` through the same sanitiser.
KNOWN_ENGINE_DISAGREEMENTS = {("djust_components:nav_item", "href", "scalar")}

#: Class components whose ``data=`` mapping becomes ``data-<key>`` attributes: the
#: CALLER names the attribute, and djust emits what it was given. The sweep's
#: dict has URL-worded keys, so ``data-href`` / ``data-url`` appear; there is no
#: framework URL parameter here.
KNOWN_AUTHOR_NAMED = {
    ("djust.components.components.button.Button", "data"),
    ("djust.components.components.card.Card", "data"),
}

#: Renderable classes the sweep cannot drive generically, and why (the sweep DERIVES the
#: set from the class; the pin makes a NEW undriven class a visible decision).
UNDRIVEN = {
    "djust.components.descriptors.%s" % n: "descriptor"
    for n in (
        "accordion.Accordion",
        "base.LiveComponent",
        "carousel.Carousel",
        "collapsible.Collapsible",
        "dropdown.Dropdown",
        "modal.Modal",
        "sheet.Sheet",
        "tabs.Tabs",
        "tooltip.Tooltip",
    )
} | {
    "djust.components.gallery.live_views.%s" % n: "page"
    for n in (
        "DataGalleryView",
        "FeedbackGalleryView",
        "FormGalleryView",
        "GalleryIndexView",
        "IndicatorGalleryView",
        "LayoutGalleryView",
        "MiscGalleryView",
        "NavGalleryView",
        "OverlayGalleryView",
        "TypographyGalleryView",
    )
}

LIBRARIES = url_sweep.SWEPT_LIBRARIES


# --------------------------------------------------------------------------- #
# The scanner


class TestScanner:
    def _flagged(self, markup: str) -> list[tuple[str, str]]:
        return [(e, a) for e, a, _c, _s in url_sweep.findings(markup)]

    @pytest.mark.parametrize(
        "markup",
        [
            '<a href="javascript:alert(1)">',
            "<a href='javascript:alert(1)'>",
            "<a href=javascript:alert(1)>",
            '<a HREF="JaVaScRiPt:alert(1)">',
            '<a href="  javascript:alert(1)">',
            '<a href="\n\tjavascript:alert(1)">',
            '<a href="java\tscript:alert(1)">',
            '<a href="java&#x09;script:alert(1)">',
            '<a href="&#106;avascript:alert(1)">',
            '<a href="jav&#x61;script:alert(1)">',
            '<a href="javascript&colon;alert(1)">',
            '<a href="\x01javascript:alert(1)">',
            '<a href="vbscript:x">',
            '<a href="data:text/html,x">',
            '<form action="javascript:x">',
            '<button formaction="javascript:x">',
            '<iframe src="javascript:x">',
            '<object data="data:text/html,x">',
            '<svg><a xlink:href="javascript:x">',
            '<img src="javascript:x">',
            '<img src="data:text/html,x">',
            '<img srcset="/a.png 1x, javascript:x 2x">',
            '<img srcset="/a.png 10w ((), javascript:alert(1) 100w">',  # parens do not nest
            '<img srcset="/a.png 10w ((), data:text/html,x 100w">',
            '<img srcset="/a.png 1x,javascript:x 2x">',
            '<img srcset="/a.png 1x,\fjavascript:x 2x">',
            '<source srcset="/a.png 1x, vbscript:x 2x">',
            '<div data-url="javascript:x">',
            '<div dj-href="javascript:x">',
            '<link rel="stylesheet" href="javascript:x">',
        ],
    )
    def test_script_urls_are_found_where_a_browser_would_read_them(self, markup):
        assert self._flagged(markup), markup

    @pytest.mark.parametrize(
        "markup",
        [
            '<a href="https://example.com/a?b=1&amp;c=2">',
            '<a href="/rel/path">',
            '<a href="#frag">',
            '<a href="mailto:a@example.com">',
            '<a href="tel:+1555">',
            '<a href="//cdn.example.com/x">',
            '<a href="/a:b/c">',
            '<a href="jav&amp;#x61;script:x">',  # escaped once: the text "jav&#x61;script:", a relative URL
            '<a href="javascript%3Aalert(1)">',  # percent in the scheme is not a scheme
            '<img src="data:image/png;base64,AAAA">',
            '<img src="data:image/svg+xml,<svg onload=x>">',  # an image URI in an image sink
            '<img srcset="/a.png 1x, data:image/png;base64,AAAA 2x">',
            '<img srcset="/a.png 1x, java\tscript:x 2x">',
            '<img srcset="/a.png (javascript:x) 1x">',  # inside parens: a descriptor, not a URL
            '<img srcset="/a.png (javascript:x) 1x">',  # inside parens: a descriptor, not a URL  # to a browser: URL "java" + a descriptor
            '<span aria-label="javascript:alert(1)">',  # a label, not a link
            '<img alt="javascript:alert(1)">',
            '<input value="javascript:alert(1)" title="vbscript:x" placeholder="data:text/html,x">',
            '<div class="javascript:x" id="javascript:y" data-value="javascript:z">',
            '<div dj-click="javascript:x" data-save-event="javascript:x" data-id="javascript:x">',
            '<link rel="manifest" href="data:application/json,{}">',
            '<!-- <a href="javascript:alert(1)"> -->',
            "<p>javascript:alert(1)</p>",
        ],
    )
    def test_safe_or_inert_values_are_not_flagged(self, markup):
        assert not self._flagged(markup), markup

    def test_data_image_is_allowed_only_on_an_image_sink(self):
        assert not self._flagged('<img src="data:image/png;base64,AAAA">')
        assert self._flagged('<a href="data:image/png;base64,AAAA">')
        assert self._flagged('<iframe src="data:image/png;base64,AAAA">')

    @pytest.mark.parametrize(
        "name,url",
        [
            ("data-url", True),
            ("data-href", True),
            ("data-image-src", True),
            ("dj-navigate-url", True),
            ("dj-click", False),
            ("data-save-event", False),
            ("data-navigate-event", False),
            ("data-value", False),
            ("data-id", False),
            ("data-icon", False),
            ("aria-label", False),
            ("href", True),
            ("srcset", True),
        ],
    )
    def test_which_attributes_count_as_urls(self, name, url):
        assert url_sweep.is_url_attr(name) is url

    def test_carries_means_the_value_reached_a_url_attribute(self):
        payload = "https://example.com/%s" % url_sweep.MARK
        assert url_sweep.carries('<a href="%s">' % payload, payload)
        assert not url_sweep.carries('<a href="#">x</a>', payload)
        # echoed into a label or alt text is not "reached a link"
        assert not url_sweep.carries('<img alt="%s" title="%s">' % (payload, payload), payload)

    def test_the_corpus_has_every_family_and_unique_marker(self):
        labels = {label for label, _ in url_sweep.PAYLOADS}
        for family in (
            "js",
            "js-upper",
            "js-embedded-tab",
            "js-lead-newline",
            "js-nul",
            "js-entity-hex",
            "js-entity-dec",
            "js-percent-colon",
            "vbs",
            "data-html",
            "data-svg",
            "https",
            "relative",
            "mailto",
            "data-png",
        ):
            assert family in labels, family
        assert all(url_sweep.MARK in payload for _, payload in url_sweep.PAYLOADS)
        assert len(labels) == len(url_sweep.PAYLOADS)


# --------------------------------------------------------------------------- #
# The sweep (subprocesses)


def _env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k != "DJANGO_SETTINGS_MODULE"}
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(ROOT / "python"), env.get("PYTHONPATH")]))
    return env


def _start(args: list[str]) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        env=_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _finish(proc: subprocess.Popen[str], path: Path) -> dict:
    stdout, stderr = proc.communicate(timeout=TIMEOUT)
    payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    payload["returncode"], payload["stdout"], payload["stderr"] = proc.returncode, stdout, stderr
    return payload


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict]:
    """The sweep over the template tags, over the class components, and a planted hole."""
    out = tmp_path_factory.mktemp("urlsweep3665")
    paths = {n: out / (n + ".json") for n in ("tags", "components", "plant", "control")}
    procs = {
        "tags": _start(["--corpus", "test", "--json", str(paths["tags"])]),
        "components": _start(
            ["--components-only", "--corpus", "test", "--json", str(paths["components"])]
        ),
        # the planted tag is a native-handler tag whose Python side is otherwise clean
        "plant": _start(
            [
                "--only",
                "avatar",
                "--corpus",
                "test",
                "--plant",
                "djust_components:avatar",
                "--json",
                str(paths["plant"]),
            ]
        ),
        "control": _start(
            ["--only", "avatar", "--corpus", "test", "--json", str(paths["control"])]
        ),
    }
    try:
        return {name: _finish(proc, paths[name]) for name, proc in procs.items()}
    finally:
        for proc in procs.values():
            if proc.poll() is None:
                proc.kill()


def _holes(run: dict) -> list[str]:
    return ["%s %s.%s <%s %s>" % tuple(h[:5]) for h in run.get("holes", [])]


class TestTheSweep:
    def test_the_sweep_ran(self, runs):
        for name in ("tags", "components", "control"):
            assert runs[name]["returncode"] in (0, 1), (name, runs[name]["stderr"][-2000:])
            assert runs[name].get("driven") is not None or name == "components", name

    def test_no_tag_emits_a_script_url_in_either_engine(self, runs):
        """The #3665 contract, over every parameter of every tag, not the URL-named ones."""
        assert _holes(runs["tags"]) == [], runs["tags"]["stdout"][-3000:]

    def test_no_class_component_emits_a_script_url(self, runs):
        remaining = [
            h
            for h in runs["components"].get("component_holes", [])
            if (h[0], h[1]) not in KNOWN_AUTHOR_NAMED
        ]
        assert remaining == [], runs["components"]["stdout"][-3000:]

    def test_the_author_named_exceptions_are_still_exactly_those(self, runs):
        """A pin that cannot rot: each exception must still occur (else delete it)."""
        seen = {(h[0], h[1]) for h in runs["components"].get("component_holes", [])}
        assert KNOWN_AUTHOR_NAMED <= seen

    def test_both_engines_keep_and_reject_the_same_urls(self, runs):
        """For every tag with a native handler, the Python tag and the handler agree on
        whether a value reached a URL attribute, for every payload that was driven."""
        found = {(d[0], d[1], d[2]) for d in runs["tags"]["disagreements"]}
        assert found == KNOWN_ENGINE_DISAGREEMENTS, sorted(found ^ KNOWN_ENGINE_DISAGREEMENTS)

    def test_every_tag_of_every_library_was_driven(self, runs):
        """Derived from the libraries as they are NOW: a new tag is swept with no edit here."""
        driven = set(runs["tags"]["driven"])
        registered = set()
        for label, dotted in LIBRARIES.items():
            tags = import_library(dotted).tags
            for name in tags:
                registered.add("%s:%s" % (label, name))
        closing = {
            key
            for key in registered
            if key.split(":", 1)[1].startswith("end")
            and key.split(":", 1)[0] + ":" + key.split(":", 1)[1][3:].lstrip("_") in registered
        }
        assert registered - driven - closing == set(), sorted(registered - driven - closing)
        assert driven - registered == set(), sorted(driven - registered)

    def test_the_only_tags_with_no_parameter_are_the_known_ones(self, runs):
        assert set(runs["tags"]["no_params"]) == set(NO_PARAMETERS), sorted(
            set(runs["tags"]["no_params"]) ^ set(NO_PARAMETERS)
        )

    def test_every_tag_with_parameters_rendered_in_some_call(self, runs):
        """``0 holes`` from a tag that never rendered would say nothing."""
        assert runs["tags"]["unrendered"] == []
        assert runs["components"]["component_unrendered"] == []

    def test_the_instrument_saw_urls_arrive_at_attributes(self, runs):
        """Presence before absence: a sweep that reads the wrong input also finds nothing."""
        reached = {tuple(r) for r in runs["tags"]["reached"]}
        for tag, param in (
            ("djust_components:avatar", "src"),
            ("djust_components:responsive_image", "src"),
            ("djust_components:responsive_image", "srcset"),
            ("djust_components:breadcrumb_dropdown", "items"),
            ("djust_components:sidebar_item", "href"),
            ("theme_components:theme_breadcrumb", "items"),
            ("djust_components:error_page", "action_url"),
            ("theme_components:theme_button", "href"),
            ("theme_components:theme_avatar", "src"),
            ("theme_pages:theme_login_page", "action"),
        ):
            assert (tag, param) in reached, (tag, param)
        assert runs["tags"]["rendered_both"] > runs["tags"]["cells"] // 2

    def test_every_renderable_class_is_driven_or_pinned_as_undriven(self, runs):
        """ "Every class component" means every ``Component``, ``LiveComponent`` and
        ``LiveView`` the packages define. Derived from the code NOW: a class added
        tomorrow is driven by default, and one that cannot be is a visible decision."""
        everything = set(url_sweep._all_component_classes())
        driven = set(runs["components"]["component_driven"])
        undriven = url_sweep.undriven_classes()
        assert driven | set(undriven) == everything, sorted(everything ^ (driven | set(undriven)))
        assert not driven & set(undriven)
        assert {k: v.split(":")[0].split()[0] for k, v in undriven.items()}.keys() == set(UNDRIVEN)
        for key, reason in undriven.items():
            kind = UNDRIVEN[key]
            assert (kind == "descriptor") == ("descriptor component" in reason), (key, reason)
            assert (kind == "page") == ("LiveView page" in reason), (key, reason)

    def test_the_live_components_and_the_avatar_are_among_the_driven(self, runs):
        from djust.components.components.avatar import Avatar

        driven = runs["components"]["component_driven"]
        assert "src" in driven["%s.Avatar" % Avatar.__module__]
        for name in (
            "layout.navbar.NavbarComponent",
            "ui.card.CardComponent",
            "data.table.TableComponent",
            "forms.foreign_key.ForeignKeySelect",
        ):
            assert driven["djust.components.%s" % name], name
        assert "brand_href" in driven["djust.components.layout.navbar.NavbarComponent"]
        assert "image" in driven["djust.components.ui.card.CardComponent"]


class TestGateOffs:
    def test_a_planted_hole_is_found_end_to_end(self, runs):
        """The tag plants ``<a href=javascript:...>`` in the PYTHON engine only: the
        sweep must report it, for that tag, in that engine, on an exec sink."""
        plant, control = runs["plant"], runs["control"]
        assert control["holes"] == [] and control["returncode"] == 0, control["stdout"]
        assert plant["returncode"] == 1, plant["stdout"]
        assert plant["holes"], "the planted hole was not found"
        assert {h[0] for h in plant["holes"]} == {"python"}
        assert {h[1] for h in plant["holes"]} == {"djust_components:avatar"}
        assert {(h[3], h[4]) for h in plant["holes"]} == {("a", "href:exec")}

    def test_the_gateoff_script_still_applies_every_mutation(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "url_sweep_gateoff", ROOT / "scripts" / "url-sweep-gateoff.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for label, path, old, new, _test, _expr in module.MUTATIONS:
            text = path.read_text(encoding="utf-8")
            assert old in text, "MUTATION TEXT NOT FOUND for %s" % label
            assert text.replace(old, new, 1) != text, "NO-OP MUTATION for %s" % label
