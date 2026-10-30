"""The JIT finds the paths a template reads, wherever it reads them.

Reported downstream (Point Guard, djust 1.3.0rc11):

- N-33: no path for a variable read only inside ``{% tabs %}…{% tab %}``. A
  raw-block tag hands its body to Python as source, so the extractor's AST had
  no nodes for it, and a ``list[Model]`` read only in a tab was serialized with
  every column and none of its annotations.
- N-34: an inline ``template`` string (and a file template with no
  ``{% extends %}``, once ``get_template()`` has run) was not given its
  ``{% include %}`` partials, so a variable read only in a partial had no path.
- N-36: no public way to hand a template a non-model object by the paths it
  reads. ``template_paths()`` and ``serialize_for_template()`` are that way.
"""

from dataclasses import dataclass, field
from typing import List

import pytest
from django.contrib.auth.models import User

from djust import LiveView
from djust._rust import extract_template_variables
from djust.utils import clear_template_dirs_cache

TABS = (
    "{% load djust_components %}"
    '{% tabs id="t" %}'
    '{% tab id="a" label="A" %}'
    "{% for r in rows %}{{ r.username }}{% endfor %}"
    "{% endtab %}"
    "{% endtabs %}"
)


# ---------------------------------------------------------------------------
# N-33: raw-block tag bodies
# ---------------------------------------------------------------------------


def test_paths_inside_tabs_are_extracted():
    found = extract_template_variables(TABS)
    assert found["rows"] == ["username"]


def test_paths_inside_tabs_inside_a_loop_reach_the_iterable():
    src = (
        "{% load djust_components %}{% for g in groups %}"
        '{% tabs id="t" %}{% tab id="a" label="A" %}{{ g.title }}{% endtab %}{% endtabs %}'
        "{% endfor %}"
    )
    assert extract_template_variables(src)["groups"] == ["title"]


def test_tab_arguments_inside_tabs_count():
    src = (
        "{% load djust_components %}"
        '{% tabs id="t" active=current %}{% tab id="a" label=label_a %}x{% endtab %}{% endtabs %}'
    )
    found = extract_template_variables(src)
    assert "current" in found
    assert "label_a" in found


def test_a_raw_body_that_does_not_parse_alone_contributes_nothing():
    src = (
        "{% load i18n %}{% blocktranslate count n=items|length %}one {{ thing.name }}"
        "{% plural %}{{ n }} things{% endblocktranslate %}{{ other.x }}"
    )
    found = extract_template_variables(src)
    assert found["other"] == ["x"]
    assert "items" in found


class TabsView(LiveView):
    template = '<div dj-root dj-view="tests.TabsView">' + TABS + "</div>"

    def mount(self, request, **kwargs):
        self.rows = list(User.objects.filter(username__startswith="n33-").order_by("username"))


@pytest.mark.django_db
def test_a_model_list_read_only_in_a_tab_is_serialized_by_its_paths():
    User.objects.create(username="n33-ada", first_name="Ada", email="ada@example.com")
    view = TabsView()
    view.mount(None)

    rows = view.get_context_data()["rows"]

    assert [row["username"] for row in rows] == ["n33-ada"]
    assert "email" not in rows[0]
    assert "first_name" not in rows[0]


# ---------------------------------------------------------------------------
# N-34: includes of an inline template / a file template without extends
# ---------------------------------------------------------------------------


@pytest.fixture
def partial_dir(tmp_path, settings):
    (tmp_path / "jit_paths").mkdir()
    (tmp_path / "jit_paths" / "_rows.html").write_text(
        "{% for r in rows %}<i>{{ r.username }}</i>{% endfor %}"
    )
    (tmp_path / "jit_paths" / "page.html").write_text(
        '<div dj-root dj-view="tests.FileView">{% include "jit_paths/_rows.html" %}</div>'
    )
    templates = [dict(t) for t in settings.TEMPLATES]
    templates[0] = {**templates[0], "DIRS": [str(tmp_path), *templates[0].get("DIRS", [])]}
    settings.TEMPLATES = templates
    clear_template_dirs_cache()
    yield tmp_path
    clear_template_dirs_cache()


class InlineView(LiveView):
    template = '<div dj-root dj-view="tests.InlineView">{% include "jit_paths/_rows.html" %}</div>'

    def mount(self, request, **kwargs):
        self.rows = list(User.objects.filter(username__startswith="n33-").order_by("username"))


class FileView(LiveView):
    template_name = "jit_paths/page.html"

    def mount(self, request, **kwargs):
        self.rows = list(User.objects.filter(username__startswith="n33-").order_by("username"))


def test_inline_template_includes_are_inlined_for_extraction(partial_dir):
    content = InlineView()._get_template_content()
    assert "{{ r.username }}" in content
    assert extract_template_variables(content)["rows"] == ["username"]


def test_file_template_without_extends_includes_are_inlined(partial_dir):
    view = FileView()
    view.get_template()  # sets _full_template to the file's own source
    assert view._full_template and "{% include" in view._full_template
    assert "{{ r.username }}" in view._get_template_content()


@pytest.mark.django_db
@pytest.mark.parametrize("view_class", [InlineView, FileView])
def test_a_model_list_read_only_in_an_include_is_serialized_by_its_paths(partial_dir, view_class):
    User.objects.create(username="n33-ada", first_name="Ada", email="ada@example.com")
    view = view_class()
    view.mount(None)
    view.get_template()

    rows = view.get_context_data()["rows"]

    assert [row["username"] for row in rows] == ["n33-ada"]
    assert "email" not in rows[0]


# ---------------------------------------------------------------------------
# N-36: template_paths() and serialize_for_template()
# ---------------------------------------------------------------------------


@dataclass
class Call:
    tool: str
    secret: str = "s3cret"

    @property
    def label(self):
        return "call to %s" % self.tool


@dataclass
class Card:
    title: str
    call: Call
    tags: List[str] = field(default_factory=list)
    password: str = "hunter2"
    notes: str = "not read"

    def get_badge(self):
        return self.title.upper()


class CardsView(LiveView):
    template = (
        '<div dj-root dj-view="tests.CardsView">'
        "{% for card in cards %}<h2>{{ card.title }} {{ card.get_badge }}</h2>"
        "<p>{{ card.call.label }}</p>{% for t in card.tags %}{{ t }}{% endfor %}"
        "{{ card.password }}{% endfor %}"
        "{{ featured.title }}{{ whole }}"
        "</div>"
    )


def _cards():
    return [Card("one", Call("search"), ["a", "b"]), Card("two", Call("fetch"))]


def test_template_paths_returns_the_jit_extraction():
    paths = CardsView().template_paths()
    assert sorted(paths["cards"]) == [
        "call.label",
        "get_badge",
        "password",
        "tags",
        "title",
    ]
    assert paths["featured"] == ["title"]
    assert paths["whole"] == []
    assert "*" not in paths


def test_template_paths_is_a_copy():
    view = CardsView()
    view.template_paths()["cards"].append("injected")
    assert "injected" not in view.template_paths()["cards"]


def test_serialize_for_template_takes_only_the_template_paths():
    data = CardsView().serialize_for_template(_cards(), "cards")

    assert data == [
        {
            "title": "one",
            "get_badge": "ONE",
            "call": {"label": "call to search"},
            "tags": ["a", "b"],
        },
        {"title": "two", "get_badge": "TWO", "call": {"label": "call to fetch"}, "tags": []},
    ]


def test_serialize_for_template_keeps_the_serialization_floor():
    data = CardsView().serialize_for_template(_cards(), "cards")
    assert all("password" not in row for row in data)
    assert all("notes" not in row and "secret" not in row["call"] for row in data)


def test_serialize_for_template_single_object_and_plain_values():
    view = CardsView()
    assert view.serialize_for_template(_cards()[0], "featured") == {"title": "one"}
    assert view.serialize_for_template({"title": "x"}, "featured") == {"title": "x"}
    assert view.serialize_for_template("text", "featured") == "text"
    assert view.serialize_for_template(None, "featured") is None


@pytest.mark.django_db
def test_serialize_for_template_models_go_through_the_jit():
    User.objects.create(username="n33-ada", email="ada@example.com")

    class UsersView(LiveView):
        template = (
            '<div dj-root dj-view="tests.UsersView">'
            "{% for u in people %}{{ u.username }}{% endfor %}</div>"
        )

    class UserListView(LiveView):
        template = (
            '<div dj-root dj-view="tests.UserListView">'
            "{% for u in members %}{{ u.username }}{% endfor %}</div>"
        )

    rows = UsersView().serialize_for_template(
        User.objects.filter(username__startswith="n33-"), "people"
    )
    assert [row["username"] for row in rows] == ["n33-ada"]
    assert "email" not in rows[0]
    as_list = UserListView().serialize_for_template(
        list(User.objects.filter(username__startswith="n33-")), "members"
    )
    assert [row["username"] for row in as_list] == ["n33-ada"]
    assert "email" not in as_list[0]


def test_serialized_cards_render_like_the_objects():
    from djust._rust import render_template

    view = CardsView()
    data = {"cards": view.serialize_for_template(_cards(), "cards")}
    html = render_template(view.template, data)
    assert "one ONE" in html and "call to search" in html and "ab" in html
    assert "hunter2" not in html
