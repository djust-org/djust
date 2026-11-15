"""Marked-safe arguments are escaped in attribute slots, on both engines (#4009).

``conditional_escape`` (and ``format_html``, and Django's autoescape of ``{{ x }}``) leaves a
marked-safe value as it is. That is right in a TEXT slot: the marker means "this is markup". In an
attribute it lets the value out: ``mark_safe('x" data-pwn="1')`` closes the attribute and adds
another. Every attribute slot of a component tag, a component class and a Rust component escapes
its value whatever it is marked as (``attr_text``, or ``|force_escape`` in a template); a text slot
keeps the developer's markup.

What is swept (``marked_safe_sweep_4009``), each axis pinned to NO finding:

* ``TestEveryTag``: every tag of ``djust_components`` (with its ``_forms`` / ``_advanced`` /
  ``_charts`` / ``_dev_tools`` tags), the four theming libraries, ``live_tags`` and ``djust_pwa``
  (the parity inventory), each argument ON ITS OWN, the others a typical value, as a scalar and as
  each string member of a list and of a tuple (strings, and item dicts), through the Django engine
  AND the Rust engine (which runs the kept native twins). Sampled before merge, full nightly.
* ``TestEveryClass``: every component class the URL sweep can construct, each argument on its own,
  under the three CSS frameworks. Sampled before merge, full nightly.
* ``TestEveryRustComponent``: the ``Rust*`` components ``djust._rust`` exposes, each string argument.
* ``test_no_tag_node_or_function_lets_a_marked_safe_value_out``: the tag functions and nodes
  driven directly, every argument at once (``breakouts``, the first version of this sweep).
* The inclusion tags through their templates, one argument at a time.

A finding is an element that gained a ``data-pwn`` attribute (the value closed a double- or a
single-quoted attribute) or an attribute whose value holds the payload unescaped. Named exceptions
(none today) go in :data:`EXCEPTIONS` with a reason; a stale one fails.

``TestTextSlots`` pins the other half: a marked-safe value in a TEXT slot still renders as markup.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.utils.safestring import mark_safe

import marked_safe_sweep_4009 as safe_sweep
from tests import sweep_sample

pytestmark = pytest.mark.components

ROOT = Path(__file__).resolve().parents[2]

#: ``(axis, unit, parameter) -> reason``: a marked-safe value that is written into an attribute on
#: purpose. Empty: every attribute slot escapes.
EXCEPTIONS: dict[tuple[str, str, str], str] = {}

#: What the sweeps run through for every unit: a change here drives every unit (sampled mode).
SHARED_PATHS = (
    "python/tests/marked_safe_sweep_4009.py",
    "python/tests/test_marked_safe_attribute_sweep_4009.py",
    "python/tests/payload_sweep_3774.py",
    "python/djust/components/utils.py",
    "scripts/lib/template_library_parity/url_sweep.py",
    "scripts/lib/template_library_parity/csp_sweep.py",
    "scripts/lib/template_library_parity/harness.py",
    "tests/sweep_sample.py",
)
TAG_BUDGET = 12
CLASS_BUDGET = 12

TAGS = tuple(safe_sweep.tag_units())
CLASSES = tuple(safe_sweep.class_units())
RUST_COMPONENTS = tuple(safe_sweep.rust_component_units())

SWEEP_POPULATIONS = {
    "marked-safe-attr/tags": lambda: list(TAGS),
    "marked-safe-attr/classes": lambda: list(CLASSES),
}


def _tag_files() -> dict[str, str]:
    out = {}
    for base in ("python/djust/components/templatetags", "python/djust/theming/templatetags",
                 "python/djust/templatetags"):  # fmt: skip
        for p in sorted((ROOT / base).rglob("*.py")):
            out[p.relative_to(ROOT).as_posix()] = p.read_text()
    return out


def _tag_paths(tag: str) -> list[str]:
    """The tag modules that name the tag, and the templates of its library (a theming tag draws
    through ``djust_theming/components/<name>.html``)."""
    lib, name = tag.split(":", 1)
    word = re.compile(r"\b%s\b" % re.escape(name))
    paths = [path for path, text in _tag_files().items() if word.search(text)]
    if lib.startswith("theme_"):
        paths += [
            p.relative_to(ROOT).as_posix()
            for p in (ROOT / "python/djust/theming/templates").rglob("*.html")
        ]
    return paths


def _class_paths(key: str) -> list[str]:
    from scripts.lib.template_library_parity import url_sweep

    cls = url_sweep.component_classes().get(key)
    return sweep_sample.module_paths(cls) if cls is not None else []


def _judged(found: dict, axis: str) -> list:
    """The findings that are not a named exception, as sorted rows."""
    return sorted((k, v) for k, v in found.items() if (axis, k[1], k[2]) not in EXCEPTIONS)


class TestEveryTag:
    @pytest.mark.sweep("marked-safe-attr/tags")
    @pytest.mark.parametrize("tag", TAGS)
    def test_no_argument_leaves_an_attribute(self, tag):
        sweep_sample.skip_unless_sampled(
            "marked-safe-attr/tags",
            tag,
            TAGS,
            budget=TAG_BUDGET,
            paths=_tag_paths,
            shared=SHARED_PATHS,
        )
        found, _rendered = safe_sweep.tag_findings(only={tag})
        assert _judged(found, "tag") == []

    @pytest.mark.pr_tier
    def test_the_inventory_is_the_libraries(self):
        libs = {t.split(":")[0] for t in TAGS}
        assert libs == {
            "djust_components", "theme_tags", "theme_components", "theme_pages",
            "theme_form_tags", "live_tags", "djust_pwa",
        }  # fmt: skip
        assert len(TAGS) >= 279

    @pytest.mark.pr_tier
    def test_both_engines_render_and_the_value_reaches_attributes(self):
        """Non-vacuity (#1468), on a fixed set of tags: both engines render each, and the escaped
        value arrives in an attribute (the slot the defect would be in) on both."""
        sample = {
            "djust_components:rating", "djust_components:alert", "djust_components:dj_input",
            "djust_components:data_table", "djust_components:accordion",
            "theme_components:theme_button", "theme_tags:theme_switcher", "djust_pwa:djust_pwa_head",
        }  # fmt: skip
        reached: dict[str, set] = {"django": set(), "rust": set()}

        def seen(cell, out):
            if safe_sweep.reaches_an_attribute(out):
                reached[cell[0]].add(cell[1])

        found, rendered = safe_sweep.tag_findings(only=sample, on_output=seen)
        assert found == {}
        assert rendered["django"] == sample and rendered["rust"] == sample
        assert reached["rust"] == sample
        # the Django engine draws ``data_table`` through its inclusion template, which needs
        # rows and columns the per-argument call does not give it (swept by the inclusion test)
        assert reached["django"] == sample - {"djust_components:data_table"}

    @pytest.mark.pr_tier
    def test_the_sweep_finds_a_raw_attribute_on_each_engine(self, monkeypatch):
        """Gate-off: ``attr_text`` made a no-op for marked-safe text (what ``conditional_escape``
        did) brings the defect back in a tag on BOTH engines (the Rust engine renders the Python
        node). The kept native handlers are not reached this way: the engine hands them every
        operand as plain text (``plain_operand``), and their own sites are held by the safe-markup
        guard (``conditional_escape`` in a quoted value is a finding, #4009)."""
        from django.utils.html import conditional_escape

        from djust.components import utils

        import djust.components.templatetags.djust_components as dc

        monkeypatch.setattr(dc, "attr_text", conditional_escape)
        assert dc.attr_text is conditional_escape and utils.attr_text is not conditional_escape
        found, _ = safe_sweep.tag_findings(only={"djust_components:rating"})
        cells = {(k[0], k[1], k[2]) for k in found}
        assert ("django", "djust_components:rating", "size") in cells
        assert ("rust", "djust_components:rating", "size") in cells


class TestEveryClass:
    @pytest.mark.sweep("marked-safe-attr/classes")
    @pytest.mark.parametrize("key", CLASSES)
    def test_no_argument_leaves_an_attribute(self, key):
        sweep_sample.skip_unless_sampled(
            "marked-safe-attr/classes",
            key,
            CLASSES,
            budget=CLASS_BUDGET,
            paths=_class_paths,
            shared=SHARED_PATHS,
        )
        found, _rendered = safe_sweep.class_findings(only={key})
        assert _judged(found, "class") == []

    @pytest.mark.pr_tier
    def test_the_sweep_finds_a_raw_attribute(self, monkeypatch):
        """Gate-off: ``Badge`` with ``attr_text`` made ``conditional_escape`` leaks ``variant``."""
        from django.utils.html import conditional_escape

        import djust.components.components.badge as badge

        monkeypatch.setattr(badge, "attr_text", conditional_escape)
        key = "djust.components.components.badge.Badge"
        found, rendered = safe_sweep.class_findings(only={key})
        assert key in rendered
        assert {k[2] for k in found} >= {"variant"}


class TestEveryRustComponent:
    def test_no_argument_leaves_an_attribute(self):
        found, rendered = safe_sweep.rust_component_findings()
        assert found == {}
        assert rendered == set(RUST_COMPONENTS) and len(RUST_COMPONENTS) >= 15


@pytest.mark.slow
@pytest.mark.pr_tier
def test_no_tag_node_or_function_lets_a_marked_safe_value_out():
    """The tag functions and nodes driven directly, every argument at once (live_tags and
    djust_pwa included); the inclusion tags through their templates."""
    assert safe_sweep.breakouts() == set()


@pytest.mark.parametrize("name", safe_sweep.inclusion_tags())
def test_no_inclusion_tag_lets_a_marked_safe_argument_out_of_an_attribute(name):
    """Each inclusion tag, through its template, with a marked-safe attribute-breaking value in each
    of its arguments in turn (the templates' attribute slots use ``force_escape``)."""
    assert safe_sweep.inclusion_tags(), "no inclusion tag found: the sweep is vacuous"
    found = {n for n in safe_sweep.inclusion_breakouts() if n.endswith(":" + name)}
    assert found == set(), found


def test_the_inclusion_sweep_sees_a_raw_attribute():
    """Gate-off for the inclusion sweep: with ``force_escape`` taken out of ``progress.html``'s
    ``aria-label`` (a copy of the template, rendered the same way), the payload breaks out."""
    from django.template import engines

    from djust.components.templatetags.djust_components import progress

    template = (
        '{% load djust_components %}<div{% if label %} aria-label="{{ label }}"{% endif %}></div>'
    )
    ctx = progress(value=3, label=safe_sweep.SAFE_PAYLOAD)
    raw = engines["django"].from_string(template).render(ctx)
    from payload_sweep_3774 import injections

    assert any("onmouseover" in hit for hit in injections(raw))
    shipped = safe_sweep.render_inclusion("progress", "label", safe_sweep.SAFE_PAYLOAD)
    assert not any("onmouseover" in hit for hit in injections(shipped)), shipped


def test_the_detectors_see_a_breakout_and_not_a_text_slot():
    """Non-vacuity (#1468): both detectors flag a marked-safe value in an attribute (double- and
    single-quoted) and not the same value rendered raw as text, which is what it is for."""
    from payload_sweep_3774 import injections

    payload = safe_sweep.SAFE_PAYLOAD
    assert any("onmouseover" in hit for hit in injections(f'<b title="{payload}">x</b>'))
    assert any("onmouseover" in hit for hit in injections(f"<b title='{payload}'>x</b>"))
    assert injections(f"<b>{payload}</b>") == []
    value = safe_sweep.ATTR_PAYLOAD_TEXT
    assert "(breakout)" in " ".join(safe_sweep.attr_findings(f'<b title="{value}">x</b>'))
    assert "(breakout)" in " ".join(safe_sweep.attr_findings(f"<b title='{value}'>x</b>"))
    assert "(raw)" in " ".join(safe_sweep.attr_findings(f'<b data-x="1" title="{value}">x</b>'))
    assert safe_sweep.attr_findings(f"<b>{value}</b>") == []
    from django.utils.html import escape

    assert safe_sweep.attr_findings(f'<b title="{escape(value)}">x</b>') == []
    assert safe_sweep.reaches_an_attribute(f'<b title="{escape(value)}">x</b>')


def test_the_single_path_is_the_python_tag():
    """The Rust engine renders the Python tag's node: ``rating`` (the tag #4009 confirmed) is
    escaped through both engines, byte for byte the same."""
    from django.template import engines

    from djust import _rust
    from djust.components.rust_handlers import register_with_rust_engine

    register_with_rust_engine()
    ctx = {"e": mark_safe('x" onmouseover="alert(1)')}
    src = "{% rating value=2 event=e %}"
    django = engines["django"].from_string("{% load djust_components %}" + src).render(ctx)
    assert str(_rust.render_template(src, ctx)) == django
    assert 'dj-click="x&quot; onmouseover=&quot;alert(1)"' in django


class TestTextSlots:
    """A marked-safe value in a TEXT slot still renders as markup (the other half of the rule)."""

    MARKUP = mark_safe("<b>bold</b>")

    def _both(self, src: str, ctx: dict) -> list[str]:
        from django.template import engines

        from djust import _rust
        from djust.components.rust_handlers import register_with_rust_engine

        register_with_rust_engine()
        return [
            engines["django"].from_string("{% load djust_components %}" + src).render(ctx),
            str(_rust.render_template("{% load djust_components %}" + src, ctx)),
        ]

    @pytest.mark.parametrize(
        "src",
        [
            "{% alert title=v %}body{% endalert %}",
            "{% dj_input name='q' label=v %}",
            "{% dj_button label=v %}",
            "{% thinking_indicator label=v %}",
            "{% connection_status reconnecting_text=v %}",
        ],
    )
    def test_marked_safe_text_keeps_its_markup_on_both_engines(self, src):
        for out in self._both(src, {"v": self.MARKUP}):
            assert "<b>bold</b>" in out, out

    def test_the_same_value_in_an_attribute_of_the_same_tag_is_escaped(self):
        # thinking_indicator writes its label as text AND as aria-label (#4009)
        for out in self._both("{% thinking_indicator label=v %}", {"v": self.MARKUP}):
            assert 'aria-label="&lt;b&gt;bold&lt;/b&gt;"' in out, out
            assert '<span class="dj-thinking__label"><b>bold</b></span>' in out, out


def test_no_named_exception_is_stale():
    """Each entry of :data:`EXCEPTIONS` still writes a raw attribute (else remove it)."""
    for (axis, unit, param), reason in EXCEPTIONS.items():
        assert reason
        if axis == "tag":
            found, _ = safe_sweep.tag_findings(only={unit})
        else:
            found, _ = safe_sweep.class_findings(only={unit})
        assert any(k[2] == param for k in found), (axis, unit, param)
