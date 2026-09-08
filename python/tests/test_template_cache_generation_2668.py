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
        # Bridge the library FIRST. The first parse of a `{% load %}` template
        # legitimately bumps the generation — the test above says so in as many
        # words — so the claim here is about an ALREADY-bridged library, and
        # the plain entry has to be seeded after that bump, not before it.
        #
        # This used to rely on the sibling test having bridged `i18n` earlier in
        # the same process. That holds in file order, but pytest-xdist deals
        # tests to workers individually: when this one landed on a worker where
        # the sibling had not run, its own first `{% load %}` parse did the
        # bridging, bumped, and stranded the plain entry it had just seeded —
        # failing on the legitimate bump rather than on the #2668 regression.
        # Re-balancing CI's shards (#2584) produced exactly that deal.
        #
        # The assertion below is unchanged, and still red under #2668: that bug
        # bumped on EVERY parse, so no amount of pre-warming would leave the
        # plain entry current across the two compiles that follow.
        _compile(self.LOAD_SRC)
        _compile(self.LOAD_SRC)

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


class TestEveryEntryPointIsGenerationGated:
    """#2669: `compile_template` was the only inserter that recorded the
    generation. A template FIRST parsed through any of the five render entry
    points was served from `TEMPLATE_CACHE` forever — after an
    `unregister_custom_filter`, the stale parse of `{{ v|f }}` rendered
    happily where Django (and `compile_template`) raise `Invalid filter`.

    Every entry point now goes through one `cached_template` helper. Each case
    below is the same three beats on a different door: parse-with-filter →
    unregister → the re-render must refuse the stale parse. Each uses its own
    source so no case can be satisfied by another's parse.
    """

    FILTER = "gatefilter2669"

    @staticmethod
    def _via_render_template(src):
        _rust.render_template(src, {"v": "x"})

    @staticmethod
    def _via_render_template_with_dirs(src):
        _rust.render_template_with_dirs(src, {"v": "x"}, [])

    @staticmethod
    def _via_view_render(src):
        view = _rust.RustLiveView(src)
        view.set_state("v", "x")
        view.render()

    @staticmethod
    def _via_view_render_with_diff(src):
        view = _rust.RustLiveView(src)
        view.set_state("v", "x")
        view.render_with_diff()

    @staticmethod
    def _via_view_render_binary_diff(src):
        view = _rust.RustLiveView(src)
        view.set_state("v", "x")
        view.render_binary_diff()

    @staticmethod
    def _via_compile_template(src):
        _compile(src)

    ENTRY_POINTS = {
        "render_template": _via_render_template,
        "render_template_with_dirs": _via_render_template_with_dirs,
        "RustLiveView.render": _via_view_render,
        "RustLiveView.render_with_diff": _via_view_render_with_diff,
        "RustLiveView.render_binary_diff": _via_view_render_binary_diff,
        "compile_template": _via_compile_template,
    }

    @pytest.mark.parametrize("name", sorted(ENTRY_POINTS))
    def test_entry_point_records_generation_and_refuses_a_stale_parse(self, name):
        enter = self.ENTRY_POINTS[name]
        src = f"{{{{ v|{self.FILTER} }}}}-2669-{name}"
        _rust.register_custom_filter(self.FILTER, lambda v: v, False, False)
        try:
            enter(src)
            assert _rust.template_cache_contains(src)
            assert _rust.template_compiled_at_generation(src) == _rust.registry_generation(), (
                f"{name} inserted into TEMPLATE_CACHE without recording the generation"
            )
        finally:
            _rust.unregister_custom_filter(self.FILTER)
        assert not _is_hit_next_time(src)
        with pytest.raises(Exception, match="(?i)invalid filter|unknown filter"):
            enter(src)  # must re-parse, not serve the stale entry


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


class TestSameTagNameInTwoLibraries:
    """Django's own suite registers `badtag` in two libraries. A presence-only
    guard saw "`badtag` is registered", skipped re-bridging, and served the
    OTHER library's handler — the scoreboard ratchet caught it (1032 → 1031,
    `test_compile_tag_error`). The guard must check ownership, not presence."""

    def _backend(self, libs):
        from djust.template import DjustTemplateBackend

        return DjustTemplateBackend(
            {"NAME": "t", "DIRS": [], "APP_DIRS": False, "OPTIONS": {"libraries": libs}}
        )

    def test_each_library_gets_its_own_handler_back(self, tmp_path, monkeypatch):
        import sys

        (tmp_path / "libA.py").write_text(
            "from django import template\nregister = template.Library()\n"
            "@register.simple_tag\ndef who(): return 'A'\n"
        )
        (tmp_path / "libB.py").write_text(
            "from django import template\nregister = template.Library()\n"
            "@register.simple_tag\ndef who(): return 'B'\n"
        )
        monkeypatch.syspath_prepend(str(tmp_path))
        sys.modules.pop("libA", None)
        sys.modules.pop("libB", None)
        be = self._backend({"libA": "libA", "libB": "libB"})
        a = "{% load libA %}{% who %}-2668-A"
        b = "{% load libB %}{% who %}-2668-B"
        assert be.from_string(a).render({}).startswith("A")
        assert be.from_string(b).render({}).startswith("B")
        # Back to A: `who` is registered (by B) — presence alone would skip
        # the re-bridge and render 'B' here.
        assert be.from_string(a + "2").render({}).startswith("A")
        assert be.from_string(b + "2").render({}).startswith("B")
