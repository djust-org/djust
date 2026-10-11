"""Manifest section reuse, completeness and theming claim pins."""

import argparse


def test_manifest_reuses_ai_context_sections():
    from djust.ai_discovery import manifest
    from djust.management.commands import djust_ai_context as old
    from djust.schema import get_framework_schema

    fw = get_framework_schema()
    for name in ("directives", "lifecycle", "decorators", "conventions", "security", "state"):
        assert getattr(old, "_section_" + name) is getattr(manifest, "_section_" + name)
    assert old._section_directives(fw) in manifest.render_manifest(fw)


def test_manifest_names_every_catalog_entry():
    from djust.ai_discovery.catalog import load_catalog
    from djust.ai_discovery.manifest import render_manifest

    text = render_manifest()
    for entry in load_catalog():
        needle = "`{% " + entry.name + " %}`" if entry.kind == "tag" else "`" + entry.name + "`"
        assert needle in text


def test_manifest_lists_every_discovery_command():
    from djust.ai_discovery.agents import DISCOVERY_COMMANDS
    from djust.ai_discovery.manifest import render_manifest

    assert all(c.invocation in render_manifest() for c in DISCOVERY_COMMANDS)


def test_manifest_theming_names_resolve():
    from djust.ai_discovery.manifest import manifest_sections
    from djust.theming._config import DEFAULT_CONFIG
    from djust.theming.apps import DjustThemingConfig
    from djust.theming.templatetags.theme_tags import register
    from djust.theming.management.commands.djust_theme import Command

    section = dict(manifest_sections())["theming"]
    assert "theme_head" in register.tags and "theme_head" in section
    assert DjustThemingConfig.name == "djust.theming" and "djust.theming" in section
    for key in ("theme", "preset", "default_mode"):
        assert key in DEFAULT_CONFIG and key in section
    parser = argparse.ArgumentParser()
    Command().add_arguments(parser)
    assert parser.parse_args(["list-presets"]).subcommand == "list-presets"
    assert "list-presets" in section


def test_manifest_sections_ids_and_order():
    from djust.ai_discovery.manifest import manifest_sections

    assert [i for i, _ in manifest_sections()] == [
        "discovery",
        "components",
        "theming",
        "directives",
        "lifecycle",
        "decorators",
        "conventions",
        "security",
        "state",
        "pitfalls",
    ]


def test_manifest_size_bound():
    from djust.ai_discovery.manifest import render_manifest

    assert len(render_manifest()) < 150_000


def test_ai_context_output_unchanged_by_the_move():
    from djust.management.commands.djust_ai_context import _generate_content, _section_directives
    from djust.schema import get_framework_schema

    fw = get_framework_schema()
    text = _generate_content(fw, {}, "claude")
    assert "## Security" in text and _section_directives(fw) in text
