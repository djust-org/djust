"""Two hardening items in the template-library bridge (#3668).

1. ``_may_override`` did not consult the raw-block registry. ``tabs`` and
   ``split_pane`` have native raw-block handlers (``components/rust_handlers.py``),
   so they were counted as non-native and bridged: a dead handler in the library
   registry (the Rust parser asks the raw-block registry first), and a policy that
   would have let a third-party library displace a djust raw-block handler.

2. ``_library_module`` returned the module of the FIRST-registered entry. Every
   decision keyed by module (``_UNBRIDGED_PREFIXES``, ``_DJUST_TAGS_BRIDGED``, the
   native-tag tables, the shared-name check) therefore depended on the import
   order of a multi-module library such as ``djust_components``, and whether a
   refusal was logged as djust's own (#3655) depended on whichever function
   happened to register first.

Everything runs the real path: ``DjustTemplateBackend`` + ``load_libraries`` +
``caplog``. The synthetic libraries are real ``Library`` objects whose functions
carry real ``__module__`` values, so the classification being tested is the one
production code computes.
"""

from __future__ import annotations

import itertools
import logging
import sys
from types import ModuleType

import pytest
from django.template import Context, Engine
from django.template.library import Library

from djust import _rust
from djust import template_libraries as tl
from djust.template import DjustTemplateBackend
from djust.template_libraries import rendering_with_backend

pytestmark = pytest.mark.django_db

LOGGER = "djust.template_libraries"
OWN = {
    "djust_components": "djust.components.templatetags.djust_components",
    "theme_tags": "djust.theming.templatetags.theme_tags",
}
RAW = "t3668_raw"
CLASH = "t3668_clash"


def _backend(tmp_path, libraries, name="hard3668"):
    return DjustTemplateBackend(
        {
            "NAME": name,
            "DIRS": [tmp_path],
            "APP_DIRS": False,
            "OPTIONS": {"libraries": libraries},
        }
    )


def _fn(name, module):
    def tag():
        return name

    tag.__name__ = tag.__qualname__ = name
    tag.__module__ = module
    return tag


def _library(monkeypatch, label_module, entries):
    """A ``Library`` in ``label_module`` registering ``(tag name, defining module)``
    pairs in the given order, each function claiming its own defining module."""
    library = Library()
    for name, module in entries:
        library.simple_tag(_fn(name, module), name=name)
    module = ModuleType(label_module)
    module.register = library
    monkeypatch.setitem(sys.modules, label_module, module)
    return library


def _refusals(caplog, level):
    return [
        r.getMessage()
        for r in caplog.records
        if r.levelno == level and "already provide" in r.getMessage()
    ]


class _Native:
    def render(self, args, context):
        return "native"


@pytest.fixture(autouse=True)
def _fresh_warned_sets():
    tl._warned_refused_tags.clear()
    tl._warned_shared_names.clear()
    yield
    tl._warned_refused_tags.clear()
    tl._warned_shared_names.clear()


@pytest.fixture
def native_clash_tag():
    """djust provides ``CLASH`` as an ordinary inline tag."""
    previous = _rust.set_registry_namespace(0)
    _rust.register_tag_handler(CLASH, _Native())
    try:
        yield
    finally:
        _rust.set_registry_namespace(0)
        _rust.unregister_tag_handler(CLASH)
        _rust.set_registry_namespace(previous)


@pytest.fixture
def native_raw_block():
    """djust provides ``RAW`` as a native RAW-BLOCK tag, and nothing else."""
    previous = _rust.set_registry_namespace(0)
    _rust.register_raw_block_tag_handler(RAW, "end" + RAW, _Native())
    try:
        yield
    finally:
        _rust.set_registry_namespace(0)
        _rust.unregister_raw_block_tag_handler(RAW)
        _rust.set_registry_namespace(previous)


# ---------------------------------------------------------------------------
# 1. The raw-block registry
# ---------------------------------------------------------------------------


class TestRawBlockRegistry:
    def test_may_override_refuses_a_name_djust_owns_as_a_raw_block(
        self, tmp_path, native_raw_block
    ):
        backend = _backend(tmp_path, {})
        with rendering_with_backend(backend):
            assert not _rust.has_tag_handler(RAW)
            assert not _rust.has_block_tag_handler(RAW)
            assert not _rust.has_assign_tag_handler(RAW)
            assert _rust.has_raw_block_tag_handler(RAW)
            assert tl._may_override(RAW) is False
            assert tl._may_override("t3668_nobody_owns_this") is True

    def test_a_library_cannot_displace_a_raw_block_handler_and_is_told(
        self, tmp_path, monkeypatch, caplog, native_raw_block
    ):
        caplog.set_level(logging.DEBUG, logger=LOGGER)
        _library(monkeypatch, "thirdparty_pkg.templatetags.rawclash", [(RAW, "thirdparty_pkg.x")])
        backend = _backend(tmp_path, {"rawclash": "thirdparty_pkg.templatetags.rawclash"})
        with rendering_with_backend(backend):
            tl.load_libraries(["rawclash"])
            refused = tl._engine_state("_refused_tags", tl._refused_tags)["rawclash"]
            owner = tl._engine_state("_tag_owner", tl._tag_owner).get(RAW)
            local = {
                kind: _rust.registry_entry_is_local(RAW, kind) for kind in ("tag", "block", "raw")
            }

        assert refused == {RAW}
        assert owner is None
        assert local == {"tag": False, "block": False, "raw": False}, (
            "the library's handler reached a registry"
        )
        warnings = _refusals(caplog, logging.WARNING)
        assert len(warnings) == 1 and f"registers tag '{RAW}'" in warnings[0], warnings

    @pytest.mark.parametrize("tag", ["tabs", "split_pane"])
    def test_djust_components_native_raw_tags_are_refused_not_bridged(self, tmp_path, caplog, tag):
        caplog.set_level(logging.DEBUG, logger=LOGGER)
        backend = _backend(tmp_path, OWN)
        with rendering_with_backend(backend):
            tl.load_libraries(list(OWN))
            refused = tl._engine_state("_refused_tags", tl._refused_tags)["djust_components"]
            owner = tl._engine_state("_tag_owner", tl._tag_owner).get(tag)
            owned = tag in tl._engine_state("_owned_tags", tl._owned_tags)
            local_tag = _rust.registry_entry_is_local(tag, "tag")
            local_block = _rust.registry_entry_is_local(tag, "block")
            native_raw = _rust.has_raw_block_tag_handler(tag)

        assert native_raw, "the premise: djust serves this tag through the raw-block registry"
        assert tag in refused
        assert owner is None and not owned
        assert not local_tag and not local_block, "a dead bridged handler was registered"
        # djust's own library meeting djust's own native handler: DEBUG, never WARNING.
        assert _refusals(caplog, logging.WARNING) == []
        assert any(f"'{tag}'" in m for m in _refusals(caplog, logging.DEBUG))

    def test_reloading_does_not_rebridge_the_refused_raw_tags(self, tmp_path, monkeypatch):
        """`_still_bridged` must agree with `_may_override` for a raw-block name."""
        backend = _backend(tmp_path, OWN)
        with rendering_with_backend(backend):
            tl.load_libraries(list(OWN))
            calls = []
            real = tl._bridge_tag

            def spy(label, name, compile_func):
                calls.append(name)
                return real(label, name, compile_func)

            monkeypatch.setattr(tl, "_bridge_tag", spy)
            tl.load_libraries(list(OWN))

        assert calls == []

    def test_rendering_tabs_and_split_pane_is_django_s_own_output(self, tmp_path):
        """The behaviour must not move: both tags still render as Django's compile
        function renders them, loaded or not, and under the Django engine."""
        source = (
            '{% tabs id="t1" active="a" %}'
            '{% tab id="a" label="A" %}AAA{% endtab %}'
            '{% tab id="b" label="B<x>" %}BBB {{ evil }}{% endtab %}'
            "{% endtabs %}|"
            '{% split_pane direction="horizontal" id="sp1" initial="40" %}'
            "LEFT {{ evil }}{% pane %}RIGHT{% endsplit_pane %}"
        )
        context = {"evil": "<b>x</b>"}
        django_engine = Engine(libraries={"djust_components": OWN["djust_components"]})
        expected = django_engine.from_string("{% load djust_components %}" + source).render(
            Context(context)
        )

        backend = _backend(tmp_path, OWN)
        loaded = backend.from_string("{% load djust_components %}" + source).render(context)
        not_loaded = backend.from_string(source).render(context)

        assert 'class="dj-tabs"' in expected and "split-pane-horizontal" in expected
        assert loaded == expected
        assert not_loaded == expected


# ---------------------------------------------------------------------------
# 2. Classification does not depend on registration order
# ---------------------------------------------------------------------------

THIRD = "thirdparty_pkg.templatetags.mixed"


class TestRefusalIsDecidedPerTag:
    """#3660's 'is this djust's own?' reads the clashing tag's own module."""

    @pytest.mark.parametrize("order", list(itertools.permutations(range(3))))
    def test_a_third_party_clash_warns_whatever_registers_first(
        self, tmp_path, monkeypatch, caplog, native_clash_tag, order
    ):
        """The library's first-registered function may be a djust re-export; the
        CLASHING tag is the third party's, so it must still warn."""
        caplog.set_level(logging.DEBUG, logger=LOGGER)
        entries = [
            ("t3668_reexport", "djust.components.templatetags._registry"),
            ("t3668_helper", "thirdparty_pkg.helpers"),
            (CLASH, "thirdparty_pkg.templatetags.mixed"),
        ]
        _library(monkeypatch, THIRD, [entries[i] for i in order])
        backend = _backend(tmp_path, {"mixed": THIRD})
        with rendering_with_backend(backend):
            tl.load_libraries(["mixed"])

        warnings = _refusals(caplog, logging.WARNING)
        assert len(warnings) == 1 and f"registers tag '{CLASH}'" in warnings[0], warnings
        assert _refusals(caplog, logging.DEBUG) == []

    @pytest.mark.parametrize("order", list(itertools.permutations(range(3))))
    def test_a_djust_clash_stays_debug_whatever_registers_first(
        self, tmp_path, monkeypatch, caplog, native_clash_tag, order
    ):
        """A djust-owned library whose first function is OUTSIDE ``djust.`` still
        logs its own clashing tag at DEBUG."""
        caplog.set_level(logging.DEBUG, logger=LOGGER)
        entries = [
            ("t3668_helper_a", "thirdparty_pkg.helpers"),
            ("t3668_helper_b", "thirdparty_pkg.more_helpers"),
            (CLASH, "djust.components.templatetags._t3668_sub"),
        ]
        _library(monkeypatch, "djust.t3668_own_lib", [entries[i] for i in order])
        backend = _backend(tmp_path, {"ownlib": "djust.t3668_own_lib"})
        with rendering_with_backend(backend):
            tl.load_libraries(["ownlib"])

        assert _refusals(caplog, logging.WARNING) == []
        assert len(_refusals(caplog, logging.DEBUG)) == 1


class TestLibraryIdentityIsOrderIndependent:
    def test_module_keyed_policy_does_not_depend_on_registration_order(self, tmp_path, monkeypatch):
        """A library spread over modules is keyed on none of them.

        ``djust.templatetags.t3668_a`` is in the module-keyed tables; a library
        that registers one of its tags together with a tag from another module is
        not that library, whichever tag registered first. Before, [a, b] bridged
        only the allow-listed tag, and [b, a] bridged nothing at all.
        """
        monkeypatch.setattr(tl, "_UNBRIDGED_PREFIXES", ("djust.templatetags.",))
        monkeypatch.setattr(
            tl, "_DJUST_TAGS_BRIDGED", {"djust.templatetags.t3668_a": frozenset({"t3668_x"})}
        )
        a, b = "djust.templatetags.t3668_a", "djust.templatetags.t3668_b"
        bridged = {}
        for order in itertools.permutations([("t3668_x", a), ("t3668_y", b)]):
            label = "mixed" + "".join(name[-1] for name, _ in order)
            module = f"thirdparty_pkg.templatetags.{label}"
            _library(monkeypatch, module, list(order))
            backend = _backend(tmp_path, {label: module}, name=label)
            with rendering_with_backend(backend):
                tl.load_libraries([label])
                owners = tl._engine_state("_tag_owner", tl._tag_owner)
                bridged[order] = frozenset(n for n in ("t3668_x", "t3668_y") if owners.get(n))

        assert len(set(bridged.values())) == 1, bridged
        assert set(bridged.values()) == {frozenset({"t3668_x", "t3668_y"})}

    def test_a_single_module_library_is_still_keyed_on_its_module(self, monkeypatch):
        library = _library(
            monkeypatch,
            "djust.templatetags.t3668_single",
            [
                ("t3668_p", "djust.templatetags.t3668_single"),
                ("t3668_q", "djust.templatetags.t3668_single"),
            ],
        )
        assert tl._library_module(library) == "djust.templatetags.t3668_single"
        assert tl._library_module(Library()) == ""

    def test_the_real_libraries_keep_their_module_keys(self):
        from django.template.library import import_library

        for dotted in (
            "djust.templatetags.live_tags",
            "djust.templatetags.djust_assets",
            "django.templatetags.i18n",
            "django.templatetags.l10n",
            "django.templatetags.tz",
            "django.templatetags.static",
            "django.templatetags.cache",
        ):
            assert tl._library_module(import_library(dotted)) == dotted

    def test_a_multi_module_library_is_not_skipped_by_the_shared_name_check(self, monkeypatch):
        """``djust.T026`` compares libraries by identity; a library spread over
        modules has an identity, and it is the same whatever registered first."""
        members = [
            ("t3668_one", "thirdparty_pkg.m1"),
            ("t3668_two", "thirdparty_pkg.m2"),
            ("t3668_shared", "thirdparty_pkg.m1"),
        ]
        for i, order in enumerate(itertools.permutations(members)):
            _library(monkeypatch, f"thirdparty_pkg.templatetags.multi{i}", list(order))
        _library(monkeypatch, "thirdparty_pkg.templatetags.single", [("t3668_shared", "other.m")])
        libraries = {f"multi{i}": f"thirdparty_pkg.templatetags.multi{i}" for i in range(6)}
        libraries["single"] = "thirdparty_pkg.templatetags.single"
        monkeypatch.setattr(tl, "_extra_libraries", libraries)

        found = tl.shared_library_names(labels=list(libraries))

        # Every multi-module library shares one identity, so they are ONE library
        # (not six) and `single` is the other: exactly one shared name, two libraries.
        assert [(k, n) for k, n, _ in found] == [("tag", "t3668_shared")]
        assert len(found[0][2]) == 2 and "single" in found[0][2]

    def test_two_different_multi_module_libraries_sharing_a_name_still_warn(
        self, tmp_path, monkeypatch, caplog
    ):
        """Neither library is keyed on a module, but they are not the same library:
        'both have no module' must not read as 'one definition under two labels'."""
        caplog.set_level(logging.WARNING, logger=LOGGER)
        _library(
            monkeypatch,
            "thirdparty_pkg.templatetags.left",
            [("t3668_l", "thirdparty_pkg.l1"), ("t3668_dup", "thirdparty_pkg.l2")],
        )
        _library(
            monkeypatch,
            "thirdparty_pkg.templatetags.right",
            [("t3668_r", "thirdparty_pkg.r1"), ("t3668_dup", "thirdparty_pkg.r2")],
        )
        backend = _backend(
            tmp_path,
            {
                "left": "thirdparty_pkg.templatetags.left",
                "right": "thirdparty_pkg.templatetags.right",
            },
        )
        with rendering_with_backend(backend):
            tl.load_libraries(["left", "right"])

        shared = [
            r.getMessage() for r in caplog.records if "both register the tag" in r.getMessage()
        ]
        assert len(shared) == 1 and "'t3668_dup'" in shared[0], shared

    def test_one_library_under_two_labels_is_one_definition_not_a_clash(
        self, tmp_path, monkeypatch, caplog
    ):
        caplog.set_level(logging.WARNING, logger=LOGGER)
        _library(
            monkeypatch,
            "thirdparty_pkg.templatetags.twice",
            [("t3668_a", "thirdparty_pkg.t1"), ("t3668_b", "thirdparty_pkg.t2")],
        )
        backend = _backend(
            tmp_path,
            {
                "twice_one": "thirdparty_pkg.templatetags.twice",
                "twice_two": "thirdparty_pkg.templatetags.twice",
            },
        )
        with rendering_with_backend(backend):
            tl.load_libraries(["twice_one", "twice_two"])

        assert [r for r in caplog.records if "both register" in r.getMessage()] == []
