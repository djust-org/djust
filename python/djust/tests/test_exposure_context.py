"""Explicit context pipeline, using gated-view fixtures (not enabled mounts)."""

import pytest
from django.template import Context, Engine

from djust import LiveView
from djust._exposure import ExposureConfigurationError, ExposureError
from djust.decorators import action, state


class ContextView(LiveView):
    exposure_policy = "explicit"
    configuration = "CONFIG_SENTINEL"
    count = state(2, persist="server")

    # E2-0: an action's render key must be declared by an @action method.
    @action
    def save_item(self):
        return None

    @property
    def unrelated(self):
        raise AssertionError("must not discover properties")


@pytest.fixture
def view():
    view = object.__new__(ContextView)
    view.public_note = "PUBLIC_SENTINEL"
    view._private_note = "PRIVATE_SENTINEL"
    view._components = {}
    view._streams = {}
    return view


def test_base_context_does_not_discover_attributes_or_state(view):
    assert view.get_context_data() == {}
    assert "_state_count" not in view.__dict__


def test_deliberate_context_retains_native_objects_and_is_not_a_client_projection(view):
    from djust._exposure import ExposureContract

    class DisplayObject:
        name = "deliberately rendered"

        def __str__(self):
            raise AssertionError("no serializer fallback")

    item = DisplayObject()
    context = view.get_context_data(count=view.count, item=item)
    assert context["item"] is item
    assert Engine().from_string("{{ count }}: {{ item.name }}").render(Context(context)) == (
        "2: deliberately rendered"
    )
    assert ExposureContract.from_view_class(type(view)).project_view(view, "client") == {}


@pytest.mark.parametrize("key", ["view", "streams"])
def test_reserved_framework_names_cannot_be_supplied_as_kwargs(view, key):
    with pytest.raises(ExposureError, match="reserved|collision"):
        view.get_context_data(**{key: view})


def test_action_and_stream_context_are_explicit_framework_providers(view):
    view._action_state = {"save_item": {"pending": True, "result": None}}
    view._get_streams_context = lambda: {"items": ["one"]}
    assert view.get_context_data() == {
        "save_item": {"pending": True, "result": None},
        "streams": {"items": ["one"]},
    }
    with pytest.raises(ExposureError, match="collision"):
        view.get_context_data(save_item="shadow")
    # E2-0: action state for a name no @action method declares is refused.
    view._action_state = {"undeclared": {"pending": False}}
    with pytest.raises(ExposureError, match="Undeclared"):
        view.get_context_data()


def test_cached_context_is_copied_and_kwargs_do_not_mutate_cache(view):
    view._cached_context = {"count": 1}
    assert view.get_context_data(count=3) == {"count": 3}
    assert view._cached_context == {"count": 1}
    view._cached_context = {"view": view}
    with pytest.raises(ExposureError, match="reserved"):
        view.get_context_data()


def test_context_processors_are_render_only_and_preserve_view_precedence(view, rf):
    view._get_context_processors = lambda: []
    view._get_resolved_processors = lambda paths: [
        lambda request: {"processor_only": "PROCESSOR_SENTINEL", "count": 99}
    ]
    context = view.get_context_data(count=2)
    rendered = view._apply_context_processors(context, rf.get("/"))
    assert rendered == {"count": 2, "processor_only": "PROCESSOR_SENTINEL"}
    assert context == {"count": 2}
    assert view.get_context_data() == {}
    assert view._context_processor_keys == {"processor_only"}


@pytest.mark.parametrize("key", ["view", "streams", "save_item"])
def test_processors_cannot_overwrite_reserved_providers(view, rf, key):
    view._action_state = {"save_item": {"pending": False}}
    context = view.get_context_data()
    view._get_context_processors = lambda: []
    view._get_resolved_processors = lambda paths: [lambda request: {key: "shadow"}]
    with pytest.raises(ExposureError, match="reserved|collision"):
        view._apply_context_processors(context, rf.get("/"))


def test_unknown_policy_is_not_a_reflection_fallback(view):
    view.exposure_policy = "explict"
    with pytest.raises(ExposureError):
        view.get_context_data()


@pytest.mark.parametrize("engine", ["django", "rust"])
def test_deliberate_models_render_natively_without_serializing_other_fields(view, engine):
    from django.contrib.auth.models import User

    item = User(username="display-name", password="MODEL_PASSWORD_SENTINEL")
    context = view.get_context_data(item=item)
    assert context["item"] is item
    source = "<p>{{ item.username }}</p>"
    if engine == "django":
        output = Engine().from_string(source).render(Context(context))
    else:
        from djust.template_backend import DjustTemplateBackend

        backend = DjustTemplateBackend(
            params={"NAME": "djust", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}}
        )
        output = backend.from_string(source).render(context)
    assert output == "<p>display-name</p>"


def test_registered_components_are_bound_per_view_and_not_discovered_from_attrs():
    from djust.components.descriptors.base import LiveComponent, TypedState

    class Toggle(LiveComponent):
        class State(TypedState):
            active: str = "initial"

    class Page(LiveView):
        exposure_policy = "explicit"
        menu = Toggle()

    first, second = object.__new__(Page), object.__new__(Page)
    for instance in (first, second):
        instance._components = {}
        instance._streams = {}
    # D-h: an instance-assigned component is never discovered; it is a
    # configuration error at the first explicit render, naming the attribute.
    stray = object.__new__(Page)
    stray._components, stray._streams = {}, {}
    stray.unregistered = Toggle()
    with pytest.raises(ExposureConfigurationError, match="'unregistered'"):
        stray.get_context_data()
    first_context = first.get_context_data()
    second_context = second.get_context_data()
    assert set(first_context) == {"menu"}
    assert first_context["menu"] is first._components["menu"]
    first_context["menu"].state.active = "changed"
    assert second_context["menu"].state.active == "initial"

    class Shadowed(Page):
        @property
        def menu(self):
            raise AssertionError("stale registry must not evaluate a replacement property")

    with pytest.raises(ExposureError, match="declaration"):
        object.__new__(Shadowed).get_context_data()


def test_render_context_provider_collision_cannot_silently_replace_component():
    from djust.components.descriptors.base import LiveComponent, TypedState

    class Toggle(LiveComponent):
        class State(TypedState):
            active: bool = False

    class Page(LiveView):
        exposure_policy = "explicit"
        menu = Toggle()

    view = object.__new__(Page)
    view._streams = {}
    view._action_state = {"menu": {"pending": False}}
    with pytest.raises(ExposureError, match="collision"):
        view.get_context_data()
