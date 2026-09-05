"""The template-cache generation gate must actually HIT for real templates.

#2668 review: every `{% load %}` of a tag-bearing library re-registered its
handlers during the parse, bumping the registry generation, so such templates
never hit the cache and invalidated everyone else's entry. Measured: three
consecutive compiles of a `{% load i18n %}` template took 62/65/63 ms — no hit.

These assertions are deterministic (the two `_rust` probes), not timing-based.
"""

import pytest

from djust import _rust

pytestmark = pytest.mark.django_db


def _compile(src: str) -> None:
    _rust.compile_template(src, None)


def _is_hit_next_time(src: str) -> bool:
    """The gate's own definition of a hit: stored generation == current."""
    stored = _rust.template_compiled_at_generation(src)
    return stored is not None and stored == _rust.registry_generation()


class TestLoadTemplatesHitTheCache:
    LOAD_SRC = "{% load i18n %}{% translate 'x' as t %}[{{ t }}]-2668-load"
    PLAIN_SRC = "{% for i in xs %}{{ i }}{% endfor %}-2668-plain"

    def test_second_compile_of_a_load_template_is_a_hit(self):
        _compile(self.LOAD_SRC)  # first parse may bridge the library (bumps)
        _compile(self.LOAD_SRC)  # re-parse under the new generation; bridge is now idempotent
        assert _is_hit_next_time(self.LOAD_SRC), (
            "a template that {% load %}s an already-bridged library must not "
            "bump the generation on every parse"
        )
        gen = _rust.registry_generation()
        _compile(self.LOAD_SRC)  # the hit itself
        assert _rust.registry_generation() == gen, "a cache hit must not bump"

    def test_a_load_template_does_not_thrash_other_entries(self):
        _compile(self.PLAIN_SRC)
        assert _is_hit_next_time(self.PLAIN_SRC)
        _compile(self.LOAD_SRC)
        _compile(self.LOAD_SRC)
        # The plain template's entry must still be current.
        assert _is_hit_next_time(self.PLAIN_SRC), (
            "compiling a {% load %} template invalidated an unrelated cached parse"
        )


class TestLoadFromFormHitsTheCache:
    """`{% load x from lib %}`: Django's `load_from_library` builds a fresh
    `Library()` per call, so an identity guard on the SUBSET never matches —
    the re-verification of #2668 measured this form bumping on every parse
    (81→83→85→87→89) and demoting the plain label to a miss afterwards."""

    FROM_SRC = "{% load translate from i18n %}{% translate 'y' as t %}[{{ t }}]-2668-from"
    FULL_SRC = "{% load i18n %}{% translate 'z' as t %}[{{ t }}]-2668-full"
    # A DIFFERENT subset key from FROM_SRC: with the same key, the first test
    # seeds `_loaded_subsets` and the early return means `_bridge_library`
    # never runs here, so the parent-restore is never exercised and its
    # gate-off passes vacuously in natural file order (#2668 verification).
    FROM_SRC_2 = (
        "{% load blocktranslate from i18n %}{% blocktranslate %}w{% endblocktranslate %}-2668-from2"
    )

    def test_from_form_second_compile_is_a_hit(self):
        _compile(self.FROM_SRC)
        _compile(self.FROM_SRC)
        assert _is_hit_next_time(self.FROM_SRC)
        gen = _rust.registry_generation()
        _compile(self.FROM_SRC)
        assert _rust.registry_generation() == gen, "a from-form load must not bump on re-parse"

    def test_from_form_does_not_demote_the_full_library(self):
        _compile(self.FULL_SRC)
        _compile(self.FULL_SRC)
        assert _is_hit_next_time(self.FULL_SRC)
        _compile(self.FROM_SRC_2)
        _compile(self.FROM_SRC_2)
        # The full-library template must still be current, and a further
        # compile of it must not re-bridge.
        gen = _rust.registry_generation()
        _compile(self.FULL_SRC)
        assert _rust.registry_generation() == gen, (
            "a `from` load overwrote _loaded[label] with the subset, so the plain label re-bridged"
        )


class TestRegistryMutationsInvalidate:
    SRC = "{{ v|stalefilter2668 }}"

    def test_custom_filter_registration_bumps_the_generation(self):
        before = _rust.registry_generation()
        _rust.register_custom_filter("stalefilter2668", lambda v: v, False, False)
        try:
            assert _rust.registry_generation() > before, (
                "the parser validates filter names against this registry, so "
                "registering one must invalidate cached parses"
            )
            _compile(self.SRC)
            assert _is_hit_next_time(self.SRC)
        finally:
            _rust.unregister_custom_filter("stalefilter2668")
        # Unregistering bumped again: the cached parse of a now-unknown filter
        # must NOT be served — Django raises `Invalid filter` at parse time.
        assert not _is_hit_next_time(self.SRC)


class TestBridgeSurvivesRegistryClear:
    """`_loaded` says "bridged"; the registry may disagree. Test isolation
    calls `clear_block_tag_handlers()` between tests, and the first version of
    the idempotent bridge trusted `_loaded` alone — `{% load cache %}` then
    skipped re-registration and every `{% cache %}` died with
    "Invalid block tag 'endcache'". The guard must consult the registry."""

    SRC = "{% load cache %}{% cache 500 k %}body{% endcache %}-2668-clear"

    def test_load_rebridges_after_a_registry_clear(self):
        from djust.template import DjustTemplateBackend

        be = DjustTemplateBackend({"NAME": "t", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}})
        assert "body" in be.from_string(self.SRC).render({})
        _rust.clear_block_tag_handlers()  # what test isolation does
        # Same library object is still in `_loaded`; the guard must notice
        # the registry no longer has `cache`/`endcache` and re-bridge.
        assert "body" in be.from_string(self.SRC + "2").render({})
