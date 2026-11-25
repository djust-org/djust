"""#4119: a design system can set the monospace font; ``code`` follows ``--font-mono``."""

import dataclasses

from djust.theming.css_generator import ThemeCSSGenerator
from djust.theming.theme_css_generator import CompleteThemeCSSGenerator
from djust.theming.theme_packs import get_design_system

DEFAULT_MONO = "ui-monospace, SFMono-Regular, monospace"


def test_default_mono_font_output_unchanged():
    gen = CompleteThemeCSSGenerator("material")
    assert gen.ds.typography.mono_font == DEFAULT_MONO
    assert f"--font-mono: {DEFAULT_MONO};" in gen.generate_css()


def test_custom_mono_font_written_to_variable():
    gen = CompleteThemeCSSGenerator("material")
    gen.ds = dataclasses.replace(
        gen.ds,
        typography=dataclasses.replace(gen.ds.typography, mono_font='"Berkeley Mono", monospace'),
    )
    css = gen.generate_css()
    assert '--font-mono: "Berkeley Mono", monospace;' in css
    assert DEFAULT_MONO not in css


def test_every_design_system_has_mono_font():
    assert get_design_system("material").typography.mono_font


def test_code_rule_uses_font_mono_variable():
    css = ThemeCSSGenerator("default")._generate_utilities()
    start = css.index("code,\n.code {")
    rule = css[start : css.index("}", start)]
    assert "font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);" in rule
    assert "font-family: ui-monospace" not in rule  # the stack is only the var() fallback
