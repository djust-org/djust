"""Component tags and classes 2.0 removed are refused by name, not silently missing (#4002)."""

from __future__ import annotations

import importlib

import pytest
from django.template import Context, Engine, TemplateSyntaxError

from djust._removed_settings import (
    MIGRATION_GUIDE_URL,
    REMOVED_NAMES,
    REMOVED_TEMPLATE_TAGS,
)
from djust.components import rust_handlers as rh
from djust.components.templatetags.djust_components import register

pytestmark = pytest.mark.components

_ENGINE = Engine(
    libraries={"djust_components": "djust.components.templatetags.djust_components"},
)

#: A use of each removed tag the 1.3 tag accepted. ``agent_step`` was a block tag.
_USES = {name: "{%% %s %%}" % name for name in REMOVED_TEMPLATE_TAGS}
_USES["agent_step"] = '{% agent_step title="t" %}body{% endagent_step %}'


def _assert_refusal(message: str, name: str) -> None:
    assert "{%% %s %%} was removed in djust 2.0" % name in message
    assert REMOVED_TEMPLATE_TAGS[name] in message
    assert MIGRATION_GUIDE_URL in message


@pytest.mark.parametrize("name", sorted(REMOVED_TEMPLATE_TAGS))
def test_the_django_engine_names_the_tag_and_its_replacement(name):
    with pytest.raises(TemplateSyntaxError) as exc:
        _ENGINE.from_string("{% load djust_components %}" + _USES[name]).render(Context({}))
    _assert_refusal(str(exc.value), name)


@pytest.mark.parametrize("name", sorted(REMOVED_TEMPLATE_TAGS))
def test_the_rust_engine_names_the_tag_and_its_replacement(name):
    from djust._rust import render_template

    rh.register_with_rust_engine()
    with pytest.raises(Exception) as exc:
        render_template(_USES[name], {})
    _assert_refusal(str(exc.value), name)


def test_every_removed_tag_is_registered_only_as_its_refusal():
    for name in REMOVED_TEMPLATE_TAGS:
        assert register.tags[name].__name__ == "removed_%s" % name


def test_the_refusal_is_what_names_the_tag(monkeypatch):
    """Gate-off: without the registration, Django's own error does not name a replacement."""
    monkeypatch.delitem(register.tags, "tour")
    with pytest.raises(TemplateSyntaxError) as exc:
        _ENGINE.from_string("{% load djust_components %}{% tour %}").render(Context({}))
    assert "was removed in djust 2.0" not in str(exc.value)


_REMOVED_COMPONENT_CLASSES = sorted(
    (module, name)
    for (module, name) in REMOVED_NAMES
    if module in ("djust.components", "djust.components.components")
)


def test_both_component_namespaces_list_the_same_classes():
    by_module: dict[str, set[str]] = {}
    for module, name in _REMOVED_COMPONENT_CLASSES:
        by_module.setdefault(module, set()).add(name)
    assert by_module["djust.components"] == by_module["djust.components.components"]
    assert len(by_module["djust.components"]) == 33


@pytest.mark.parametrize("module,name", _REMOVED_COMPONENT_CLASSES)
def test_importing_a_removed_class_names_it(module, name):
    mod = importlib.import_module(module)
    with pytest.raises(ImportError) as exc:
        getattr(mod, name)
    assert "%s.%s was removed in djust 2.0" % (module, name) in str(exc.value)


def test_a_missing_name_is_still_an_attribute_error():
    import djust.components
    import djust.components.components

    for mod in (djust.components, djust.components.components):
        assert not hasattr(mod, "NoSuchComponent")


_APP_LIBRARY = "djust.tests.removed_tag_collision_lib_4002"


@pytest.fixture
def app_calendar():
    """The app library's ``calendar`` holds the name in the Rust engine for one test, then the
    refusal comes back (the engine's tag registry is process-wide)."""
    from djust import _rust
    from djust import template_libraries as tl

    yield
    for state in (
        tl._owned_tags,
        *[getattr(b, "_djust_library_state", {}).get("_owned_tags", {}) for b in _backends],
    ):
        state.pop("calendar", None)
    _rust.unregister_tag_handler("calendar")
    rh.register_with_rust_engine()


_backends: list = []


def test_an_app_tag_with_a_removed_name_wins_under_both_engines(app_calendar):
    """A removal stub never blocks an app's own tag of that name: it yields as if djust had
    never had one (an ordinary djust tag still wins, the bridge's existing policy)."""
    from djust.template.backend import DjustTemplateBackend

    src = "{% load app4002 %}{% calendar %}"
    django = Engine(libraries={"app4002": _APP_LIBRARY}).from_string(src).render(Context({}))
    assert django == "APP-CALENDAR"

    rh.register_with_rust_engine()
    backend = DjustTemplateBackend(
        {
            "NAME": "app4002",
            "DIRS": [],
            "APP_DIRS": False,
            "OPTIONS": {"libraries": {"app4002": _APP_LIBRARY}},
        }
    )
    _backends.append(backend)
    assert str(backend.from_string(src).render({})) == "APP-CALENDAR"
    # A re-registration of djust's tags (another app's ready(), a test) does not take it back.
    rh.register_with_rust_engine()
    assert str(backend.from_string(src).render({})) == "APP-CALENDAR"


def test_without_the_app_library_the_name_is_still_refused():
    from djust._rust import render_template

    rh.register_with_rust_engine()
    with pytest.raises(Exception) as exc:
        render_template("{% calendar %}", {})
    _assert_refusal(str(exc.value), "calendar")
