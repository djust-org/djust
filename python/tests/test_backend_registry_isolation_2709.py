"""Engine bindings survive compile order, includes, and concurrent renders."""

from concurrent.futures import ThreadPoolExecutor
from types import ModuleType
import pytest
from django.template import Library
from djust.template import DjustTemplateBackend


@pytest.fixture
def engines(tmp_path, monkeypatch):
    backends = []
    for letter in ("A", "B"):
        name = "isolation_2709_" + letter
        module = ModuleType(name)
        module.register = Library()

        def who(value=letter):
            return value

        who.__module__ = name
        module.register.simple_tag(who, name="isolation_who")

        def prefix(value, marker=letter):
            return marker + str(value)

        prefix.__module__ = name
        module.register.filter("isolation_prefix", prefix)

        def include(marker=letter):
            return {"marker": marker}

        include.__module__ = name
        module.register.inclusion_tag("inclusion.html", name="isolation_include")(include)

        def block(content, marker=letter):
            return marker + content

        block.__module__ = name
        module.register.simple_block_tag(block, name="isolation_block")
        monkeypatch.setitem(__import__("sys").modules, name, module)
        backends.append(
            DjustTemplateBackend(
                {
                    "NAME": letter,
                    "DIRS": [tmp_path],
                    "APP_DIRS": False,
                    "OPTIONS": {"libraries": {"isolated": name}},
                }
            )
        )
    (tmp_path / "inclusion.html").write_text("{{ marker }}")
    return backends


@pytest.mark.parametrize(
    "source",
    ["{% load isolated %}{% isolation_who %}", '{% load isolated %}{{ "x"|isolation_prefix }}'],
)
def test_compiled_engines_keep_their_bindings(engines, source):
    templates = [engine.from_string(source) for engine in engines]
    suffix = "x" if "prefix" in source else ""
    for i in [0, 1, 0, 1]:
        assert templates[i].render({}) == ["A", "B"][i] + suffix
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda i: templates[i % 2].render({}), range(20)))
    assert results == [("A" if i % 2 == 0 else "B") + suffix for i in range(20)]


def test_shared_include_does_not_reuse_another_engine_parse(engines, tmp_path):
    (tmp_path / "part.html").write_text("{% load isolated %}{% isolation_who %}")
    templates = [e.from_string('{% include "part.html" %}') for e in engines]
    for i in [0, 1, 0, 1]:
        assert templates[i].render({}) == ["A", "B"][i]


def test_registry_namespace_restored_on_error(engines):
    from djust.template_libraries import rendering_with_backend
    from djust._rust import set_registry_namespace

    previous = set_registry_namespace(0)
    try:
        with pytest.raises(ValueError):
            with rendering_with_backend(engines[0]):
                raise ValueError("probe")
        assert set_registry_namespace(0) == 0
    finally:
        set_registry_namespace(previous)


def test_unchanged_engine_compile_cache_stabilizes(engines):
    from djust import _rust, template_libraries

    calls = []

    def loader(args):
        calls.append(tuple(args))
        return template_libraries.load_libraries(args)

    _rust.register_library_loader(loader)
    try:
        source = "{% load isolated %}{% isolation_who %}stable-2711"
        engines[0].from_string(source)
        engines[0].from_string(source)
        warmed = len(calls)
        for _ in range(4):
            assert engines[0].from_string(source).render({}) == "Astable-2711"
        assert len(calls) == warmed
    finally:
        _rust.register_library_loader(template_libraries.load_libraries)


@pytest.mark.parametrize(
    "source",
    [
        "{% load isolated %}{% isolation_include %}",
        "{% load isolated %}{% isolation_block %}{% endisolation_block %}",
    ],
)
def test_inclusion_and_block_handlers_are_isolated(engines, source):
    templates = [engine.from_string(source) for engine in engines]
    for i in [1, 0, 1, 0]:
        assert templates[i].render({}) == ["A", "B"][i]


def test_unloaded_engine_does_not_gain_tags(engines):
    from django.template import TemplateSyntaxError

    engines[0].from_string("{% load isolated %}{% isolation_who %}")
    with pytest.raises(TemplateSyntaxError):
        engines[1].from_string("{% isolation_who %}")


def test_nested_backend_switch_restores_outer_engine(engines):
    from djust.template_libraries import rendering_with_backend
    from djust import _rust

    source = "{% load isolated %}{% isolation_who %}"
    templates = [engine.from_string(source) for engine in engines]
    with rendering_with_backend(engines[0]):
        assert templates[1].render({}) == "B"
        assert _rust.render_template(source, {}) == "A"


def test_retired_backend_releases_native_storage(engines):
    import gc
    import weakref
    from djust import _rust

    engine = engines.pop()
    source = "{% load isolated %}{% isolation_who %}retirement"
    engine.from_string(source)
    namespace = engine._djust_registry_namespace
    reference = weakref.ref(engine)
    del engine
    gc.collect()
    assert reference() is None
    previous = _rust.set_registry_namespace(namespace)
    try:
        assert not _rust.has_tag_handler("isolation_who")
        assert _rust.template_compiled_at_generation(source) is None
    finally:
        _rust.set_registry_namespace(previous)


@pytest.mark.parametrize(
    "body,suffix", [("{% isolation_who %}", ""), ('{{ "x"|isolation_prefix }}', "x")]
)
def test_compiled_parent_and_child_keep_different_bindings(engines, body, suffix):
    parent = engines[0].from_string(
        "{% load isolated %}" + body + "{% block main %}" + body + "{% endblock %}"
    )
    child = engines[1].from_string(
        "{% extends parent %}{% load isolated %}{% block main %}"
        + body
        + "{{ block.super }}{% endblock %}"
    )
    assert child.render({"parent": parent}) == "A" + suffix + "B" + suffix + "A" + suffix


def test_low_level_library_loading_does_not_claim_engine_bindings(engines):
    from djust import _rust

    source = "{% load isolated %}{% isolation_who %}"
    # Low-level fallback resolves the last application's library mapping.
    assert _rust.render_template(source, {}) == "B"
    assert engines[0].from_string(source).render({}) == "A"
    assert engines[1].from_string(source).render({}) == "B"


@pytest.mark.parametrize(
    "source,clear,suffix",
    [
        ("{% load isolated %}{% isolation_who %}", "clear_tag_handlers", ""),
        ('{% load isolated %}{{ "x"|isolation_prefix }}', "clear_custom_filters", "x"),
    ],
)
def test_load_restores_engine_bindings_after_clear_despite_global_fallback(
    engines, source, clear, suffix
):
    from djust import _rust
    from djust.template_libraries import rendering_with_backend

    assert _rust.render_template(source, {}) == "B" + suffix
    assert engines[0].from_string(source).render({}) == "A" + suffix
    with rendering_with_backend(engines[0]):
        getattr(_rust, clear)()
    assert engines[0].from_string(source).render({}) == "A" + suffix
