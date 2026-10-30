"""A property that returns a list serializes as a list when the template reads
inside its items.

Reported downstream (Point Guard N-32, djust 1.3.0rc11): ``Agent.tools`` is a
property returning a list of dicts. ``{% for tool in agent.tools %}{{ tool.name }}``
gave ``agent.tools == {}`` on the codegen path (a single model, a list of
models): the nested path ``tools.name`` was taken as an attribute of the list.
The Rust QuerySet path already kept the list. Each item is now taken by the
rest of the paths, under the same serialization gate.
"""

from dataclasses import dataclass

import pytest
from django.db import connection, models

from djust import LiveView
from djust.mixins.jit import JITMixin
from djust.optimization.codegen import compile_serializer, generate_serializer_code
from djust.session_utils import _jit_serializer_cache


@dataclass
class Owner:
    name: str
    password: str = "hunter2"


class N32Agent(models.Model):
    name = models.CharField(max_length=20)

    @property
    def tools(self):
        return [{"name": "search", "title": "Search"}, {"name": "fetch", "title": "Fetch"}]

    @property
    def owners(self):
        return (Owner("ada"), Owner("grace"))

    @property
    def tags(self):
        return ["red", "blue"]

    @property
    def nothing(self):
        return []

    @property
    def me(self):
        return self

    class Meta:
        app_label = "jit_list_property"


TEMPLATE = (
    "{% for tool in agent.tools %}<b>{{ tool.name }}</b>{% endfor %}"
    "{% for o in agent.owners %}<u>{{ o.name }}{{ o.password }}</u>{% endfor %}"
    "{% for t in agent.tags %}<i>{{ t }}</i>{% endfor %}"
    "{% for n in agent.nothing %}{{ n.x }}{% endfor %}"
    "{% for tool in agent.me.tools %}<s>{{ tool.title }}</s>{% endfor %}"
)


@pytest.fixture
def agent(transactional_db):
    with connection.schema_editor() as editor:
        editor.create_model(N32Agent)
    _jit_serializer_cache.clear()
    try:
        yield N32Agent.objects.create(name="bot")
    finally:
        _jit_serializer_cache.clear()
        with connection.schema_editor() as editor:
            editor.delete_model(N32Agent)


def _serializer(paths):
    code = generate_serializer_code("N32Agent", paths, "serialize_agent")
    return compile_serializer(code, "serialize_agent")


def test_list_of_dicts_property_keeps_its_items(agent):
    data = JITMixin()._jit_serialize_model(agent, TEMPLATE, "agent")
    assert [tool["name"] for tool in data["tools"]] == ["search", "fetch"]


def test_list_of_objects_property_is_taken_item_by_item_with_the_gate(agent):
    data = JITMixin()._jit_serialize_model(agent, TEMPLATE, "agent")
    assert data["owners"] == [{"name": "ada"}, {"name": "grace"}]


def test_list_of_strings_and_empty_list_are_unchanged(agent):
    data = JITMixin()._jit_serialize_model(agent, TEMPLATE, "agent")
    assert data["tags"] == ["red", "blue"]
    assert data["nothing"] == []


def test_nested_list_property(agent):
    data = JITMixin()._jit_serialize_model(agent, TEMPLATE, "agent")
    assert [tool["title"] for tool in data["me"]["tools"]] == ["Search", "Fetch"]


def test_generated_code_and_queryset_path_agree(agent):
    from djust._rust import serialize_queryset

    paths = ["tools.name", "tags"]
    by_codegen = _serializer(paths)(agent)
    by_rust = serialize_queryset([agent], paths)[0]
    assert by_codegen["tools"] == by_rust["tools"]
    assert by_codegen["tags"] == by_rust["tags"]


class AgentListView(LiveView):
    template = (
        '<div dj-root dj-view="tests.AgentListView">'
        "{% for a in agents %}{% for tool in a.tools %}<b>{{ tool.name }}</b>{% endfor %}"
        "{% empty %}This agent answers without calling a tool{% endfor %}</div>"
    )

    def mount(self, request, **kwargs):
        self.agents = list(N32Agent.objects.all())


def test_model_list_context_renders_the_tools(agent):
    view = AgentListView()
    view.mount(None)

    rows = view.get_context_data()["agents"]

    assert [tool["name"] for tool in rows[0]["tools"]] == ["search", "fetch"]
