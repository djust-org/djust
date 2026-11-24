"""Under explicit exposure a Model / model list / QuerySet the template reads only columns off
reaches the renderer reduced to the paths the template reads, as on 1.x (#4290).

Before the fix only rows whose relations the template walked went through the JIT; a top-level
Model and a plain-column list were normalized with every concrete field.
"""

import pytest
from django.contrib.auth.models import User

from djust import LiveView
from djust.mixins import rust_bridge
from djust.testing import LiveViewTestClient

IDENTITY = {"__model__", "id", "pk"}


def _row():
    return User(id=7, username="ada", email="ada@example.test", first_name="Ada")


def _rendered(view_class):
    client = LiveViewTestClient(view_class)
    client.mount()
    html = client.render()
    return client.rendered_state(), html


class Probe(LiveView):
    template = (
        "<div dj-root>{{ person.username }} {% for p in people %}{{ p.username }}{% endfor %}</div>"
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        row = _row()
        context.update(person=row, people=[row])
        return context


def test_model_and_model_list_carry_only_the_paths_read():
    state, html = _rendered(Probe)
    assert "ada" in html
    for value in (state["person"], state["people"][0]):
        assert value["username"] == "ada"
        assert "email" not in value
        assert "first_name" not in value
        assert set(value) - {"username", "__str__"} <= IDENTITY


class Unread(LiveView):
    template = "<div dj-root>{{ people|length }}</div>"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        row = _row()
        context.update(person=row, people=[row])
        return context


def test_a_model_the_template_reads_nothing_off_keeps_only_identity():
    state, html = _rendered(Unread)
    assert ">1<" in html.replace(" ", "")
    assert set(state["person"]) <= IDENTITY | {"__str__"}
    # A row list read with no field path keeps whole rows, as the JIT's no-path list does
    # (#2736: ``{% regroup %}`` / ``{{ rows.0 }}`` read fields the extractor cannot name).
    assert state["people"][0]["username"] == "ada"


class WholeValue(LiveView):
    template = "<div dj-root>[{{ person }}]</div>"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(person=_row())
        return context


def test_a_model_printed_whole_still_renders_its_text():
    state, html = _rendered(WholeValue)
    assert "[ada]" in html
    assert "email" not in state["person"]


class QS(LiveView):
    template = (
        "<div dj-root>{{ users.count }}|{% for u in users %}{{ u.username }};{% endfor %}</div>"
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["users"] = User.objects.order_by("username")
        return context


@pytest.mark.django_db
def test_queryset_rows_carry_only_the_columns_read_and_count_still_answers(
    django_assert_max_num_queries,
):
    User.objects.create(username="ada", email="ada@example.test")
    User.objects.create(username="bob", email="bob@example.test")
    client = LiveViewTestClient(QS)
    client.mount()
    with django_assert_max_num_queries(1):  # ``users.count`` answers from the JIT's rows
        html = client.render()
    state = client.rendered_state()
    assert "2|" in html
    assert "ada;bob;" in html
    assert [r["username"] for r in state["users"]] == ["ada", "bob"]
    assert all("email" not in r for r in state["users"])


def test_gate_off_restores_the_whole_rows(monkeypatch):
    # The rows that are not walked keep their normalized shape when the gate says no.
    monkeypatch.setattr(rust_bridge, "_plain_rows_take_the_jit", lambda *a: False)
    state, _ = _rendered(Probe)
    assert "email" in state["people"][0]


class ValuesRows(LiveView):
    template = "<div dj-root>{% for r in rows %}{{ r.username }};{% endfor %}</div>"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["rows"] = User.objects.order_by("username").values("username")
        return context


@pytest.mark.django_db
def test_a_values_queryset_keeps_its_rows():
    # Its rows are dicts, which the JIT's model serializer reads empty (#4271); they hold only
    # the columns asked for, so the gate leaves them to the normalize pass.
    User.objects.create(username="ada")
    state, html = _rendered(ValuesRows)
    assert "ada;" in html
    assert state["rows"] == [{"username": "ada"}]


class SortedBy(LiveView):
    template = "<div dj-root>{% for p in people|dictsort:key %}{{ p.username }};{% endfor %}</div>"
    sort_key = "username"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        rows = [
            User(id=2, username="bob", email="b@example.test", first_name="Zed"),
            User(id=1, username="ada", email="a@example.test", first_name="Amy"),
        ]
        context.update(people=rows, key=self.sort_key)
        return context


class SortedByProperty(SortedBy):
    sort_key = "is_authenticated"


def test_dictsort_by_a_column_is_reduced_to_the_paths_read():
    state, html = _rendered(SortedBy)
    assert "ada;bob;" in html
    assert all("email" not in r for r in state["people"])


def test_dictsort_by_a_non_column_keeps_the_normalized_rows():
    # The JIT row carries a column only (#3910); a property key keeps the rows it sorted by.
    state, _ = _rendered(SortedByProperty)
    assert all("email" in r for r in state["people"])
