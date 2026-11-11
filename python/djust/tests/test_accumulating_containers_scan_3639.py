"""Which ``dj-update`` containers accumulate is read from the template (#3639).

The BEST_PRACTICES guide says a container accumulates when its body reads a
``temporary_assigns`` key, ``streams`` or an ``{% include %}``, that it needs a
static ``id`` on its tag, and that any other ``dj-update`` list is re-rendered
whole. Each claim is a case here.
"""

from __future__ import annotations

from djust import LiveView
from djust._accumulating_containers import (
    accumulating_container_ids,
    view_accumulating_container_ids,
)

PAGE = """<div class="root">
  {% if banner %}<p>b</p>{% endif %}
  <ul id="list" dj-update="append">
    {% for r in rows %}
    <li id="rows-{{ r.id }}">{{ r.body }}</li>
    {% endfor %}
  </ul>
  <ul id="plain" dj-update="append">{% for r in items %}<li id="i{{ r.id }}">x</li>{% endfor %}</ul>
  <ol id="feed" dj-stream="x" dj-update="prepend">{% for r in streams.x %}<li id="x{{ r.id }}"></li>{% endfor %}</ol>
  <ul id="inc" dj-update="append">{% include "row.html" %}</ul>
  <ul id="attr" dj-update="append">{{ obj.rows }}</ul>
  <ul id="dyn{{ n }}" dj-update="append">{{ rows }}</ul>
  <ul id="rep" dj-update="replace">{{ rows }}</ul>
  <ul dj-update="append">{{ rows }}</ul>
  <ul id="unmarked">{{ rows }}</ul>
</div>"""


def test_a_container_whose_body_reads_a_temporary_key_accumulates():
    assert "list" in accumulating_container_ids(PAGE, ("rows",))


def test_a_container_fed_by_an_ordinary_attribute_does_not():
    assert "plain" not in accumulating_container_ids(PAGE, ("rows",))


def test_a_stream_container_and_an_include_are_assumed_to_read_one():
    found = accumulating_container_ids(PAGE, ("rows",))
    assert "feed" in found and "inc" in found


def test_an_attribute_of_another_object_is_not_the_key():
    assert "attr" not in accumulating_container_ids(PAGE, ("rows",))


def test_a_container_needs_a_static_id_on_its_tag():
    found = accumulating_container_ids(PAGE, ("rows",))
    assert not [i for i in found if i.startswith("dyn")]
    assert accumulating_container_ids('<ul dj-update="append">{{ rows }}</ul>', ("rows",)) == ()


def test_only_append_and_prepend_containers_count():
    found = accumulating_container_ids(PAGE, ("rows",))
    assert "rep" not in found and "unmarked" not in found


def test_a_view_without_temporary_assigns_has_none():
    assert accumulating_container_ids(PAGE, ()) == ()


def test_the_exact_set_for_the_page():
    assert accumulating_container_ids(PAGE, ("rows",)) == ("feed", "inc", "list")


def test_the_view_helper_reads_the_template_once_per_class():
    calls = []

    class Counted(LiveView):
        temporary_assigns = {"rows": []}
        template = PAGE

        def get_template(self):
            calls.append(1)
            return super().get_template()

    view = Counted()
    assert view_accumulating_container_ids(view) == ("feed", "inc", "list")
    view_accumulating_container_ids(Counted())
    assert len(calls) == 1
    assert view_accumulating_container_ids(LiveView()) == ()
