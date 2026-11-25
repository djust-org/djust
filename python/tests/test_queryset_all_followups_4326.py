"""#4326 -- the QuerySet ``.all`` residuals left after #3862.

* A QuerySet INSIDE a container (``{"d": {"users": qs}}``): ``{{ d.users.all|length }}``,
  ``{% if d.users.all %}``, ``{{ d.users.all.1.username }}`` and ``{{ d.users.count }}``
  were empty on a LiveView. The JIT handed the QuerySet's own ``all`` / method reads to
  the row serializer, and the serialized list answers neither. The container now keeps a
  raw twin for the render sidecar, built from the rows the JIT already fetched.
* Item 5: ``{{ x.groups.all.count }}`` beside ``{% for g in x.groups.all %}`` over a
  QuerySet in a container. The same twin answers it.
* Item 2: a to-many relation read only through a method, by index or for its length was
  prefetched WHOLE. When the parent QuerySet is sliced to at most
  ``queryset_light_relation_max_parents`` rows (default 1, which never adds a query),
  each parent row reads it with its own ``COUNT`` / ``LIMIT``; otherwise the one
  prefetch query stays. Pinned on both sides of the threshold.
* ``{% with n=u.groups.all|length %}`` binds a count: a length read, no row text.
* ``{{ u.groups.all.0 }}``, the row at an index printed whole, keeps the index
  (``groups.all.0.__str__``) instead of reading every row.

Django's own engine is the oracle for every rendered string, on REAL ``User`` /
``Group`` rows (#1650 / #1638, #2727).
"""

from __future__ import annotations

import html as html_lib
import re

import pytest
from django.contrib.auth.models import Group, User
from django.db import connection
from django.test.utils import CaptureQueriesContext

from djust import _rust
from djust.config import config
from djust.live_view import LiveView
from djust.optimization.codegen import compile_serializer, generate_serializer_code
from djust.optimization.query_optimizer import analyze_queryset_optimization
from python.tests.test_queryset_all_traversal_3777 import (
    _backend_render,
    _django_render,
    _liveview_render,
)

RENDERS = [
    pytest.param(_backend_render, id="DjustTemplateBackend"),
    pytest.param(_liveview_render, id="LiveView"),
]

MANY = 12


@pytest.fixture
def world(db):
    groups = [Group.objects.create(name=f"g{i:02d}") for i in range(MANY)]
    User.objects.create(username="u0").groups.add(*groups)
    User.objects.create(username="u1").groups.add(groups[3], groups[5])
    User.objects.create(username="u2")


def _users():
    return User.objects.order_by("pk")


def _text(html: str) -> str:
    return re.sub(r"<!--/?dj-if[^>]*-->", "", html_lib.unescape(html))


def _agrees(render, source, make_ctx) -> None:
    expected = _django_render(source, make_ctx())
    assert re.search(r"[1-9]|Y|True|u\d|g\d", expected), expected
    assert _text(render(source, make_ctx())) == expected


def _codegen(paths, name):
    return compile_serializer(generate_serializer_code("User", paths, name), name)


class _Cap:
    """``queryset_light_relation_max_parents`` set for one block."""

    def __init__(self, value) -> None:
        self.value = value

    def __enter__(self):
        self.previous = config.get("queryset_light_relation_max_parents")
        config.set("queryset_light_relation_max_parents", self.value)

    def __exit__(self, *exc):
        config.set("queryset_light_relation_max_parents", self.previous)


# ===========================================================================
# A QuerySet inside a container, and item 5
# ===========================================================================
def _in_dict():
    return {"d": {"users": _users()}}


def _in_list_of_dicts():
    return {"d": {"rows": [{"users": _users()}]}}


NESTED = {
    "all-length": ("{{ d.users.all|length }}", _in_dict),
    "all-if": ("[{% if d.users.all %}Y{% endif %}]", _in_dict),
    "all-index": ("[{{ d.users.all.1.username }}]", _in_dict),
    "count": ("[{{ d.users.count }}]", _in_dict),
    "all-count-beside-rows": (
        "{{ d.users.all.count }}{% for u in d.users %}{{ u.username }},{% endfor %}",
        _in_dict,
    ),
    "with-alias": ("{% with n=d.users.all %}{{ n|length }}{% endwith %}", _in_dict),
    "list-of-dicts": (
        "{% for x in d.rows %}{{ x.users.all|length }}{% endfor %}",
        _in_list_of_dicts,
    ),
    # Item 5: a method beside the rows of a relation of a nested QuerySet's rows.
    "item5-dict": (
        "{% for x in d.users %}[{{ x.groups.all.count }}:"
        "{% for g in x.groups.all %}{{ g.name }},{% endfor %}]{% endfor %}",
        _in_dict,
    ),
    "item5-list-of-dicts": (
        "{% for r in d.rows %}{% for x in r.users %}[{{ x.groups.all.count }}:"
        "{% for g in x.groups.all %}{{ g.name }},{% endfor %}]{% endfor %}{% endfor %}",
        _in_list_of_dicts,
    ),
}


@pytest.mark.django_db
class TestAQuerySetInsideAContainer:
    @pytest.mark.parametrize("render", RENDERS)
    @pytest.mark.parametrize("name", list(NESTED))
    def test_renders_like_django(self, world, render, name) -> None:
        source, make_ctx = NESTED[name]
        _agrees(render, source, make_ctx)

    def test_the_twin_answers_from_the_rows_already_fetched(self, world) -> None:
        source = "[{{ d.users.count }}|{{ d.users.all|length }}|{{ d.users.all.1.username }}]"
        with CaptureQueriesContext(connection) as queries:
            got = _text(_liveview_render(source, _in_dict()))
        assert got == _django_render(source, _in_dict()) == "[3|3|u1]"
        user_selects = [
            q["sql"]
            for q in queries.captured_queries
            if q["sql"].startswith("SELECT") and 'FROM "auth_user"' in q["sql"]
        ]
        assert len(user_selects) == 1, user_selects


# ===========================================================================
# Item 2: the prefetch of a lightly read relation
# ===========================================================================
LIGHT = {
    "all-count": "{% for u in users %}[{{ u.groups.all.count }}]{% endfor %}",
    "count": "{% for u in users %}[{{ u.groups.count }}]{% endfor %}",
    "first-name": "{% for u in users %}[{{ u.groups.all.first.name }}]{% endfor %}",
    "exists": "{% for u in users %}[{{ u.groups.exists }}]{% endfor %}",
    "index": "{% for u in users %}[{{ u.groups.all.1.name }}]{% endfor %}",
    "length": "{% for u in users %}[{{ u.groups.all|length }}]{% endfor %}",
    "if": "{% for u in users %}[{% if u.groups.all %}Y{% endif %}]{% endfor %}",
}


def _jit(queryset, source):
    class _V(LiveView):
        template = source

    return _V()._jit_serialize_queryset(queryset, source, "users")


def _group_sql(queries):
    return [q["sql"] for q in queries.captured_queries if "auth_group" in q["sql"]]


@pytest.mark.django_db
class TestALightlyReadRelation:
    def test_the_classification(self) -> None:
        light = ["groups.all.count", "groups.count", "groups.all.first.name", "groups.exists"]
        light += ["groups.all.0.name", "groups.all.__len__", "groups.last.name"]
        for path in light:
            assert analyze_queryset_optimization(User, [path], True).prefetch_related == set()
            assert analyze_queryset_optimization(User, [path]).prefetch_related == {"groups"}
        # Every row is read: a loop, a field of every row, the rows printed.
        for paths in (
            ["groups.all", "groups.all.name"],
            ["groups.all"],
            ["groups.all.count", "groups.all.name"],
        ):
            assert analyze_queryset_optimization(User, paths, True).prefetch_related == {"groups"}

    @pytest.mark.parametrize("render", RENDERS)
    @pytest.mark.parametrize("name", list(LIGHT))
    @pytest.mark.parametrize("bound", [1, 2, None], ids=["sliced-1", "sliced-2", "unsliced"])
    def test_renders_like_django(self, world, render, name, bound) -> None:
        source = LIGHT[name]

        def make_ctx():
            return {"users": _users()[:bound] if bound else _users()}

        with _Cap(2):
            _agrees(render, source, make_ctx)

    @pytest.mark.parametrize("name", list(LIGHT))
    def test_one_parent_reads_its_relation_alone_at_the_default(self, world, name) -> None:
        """The default threshold (1): one parent, one relation query either way, but
        the relation is read with COUNT / LIMIT / EXISTS, not loaded whole."""
        with CaptureQueriesContext(connection) as queries:
            _jit(_users()[:1], LIGHT[name])
        assert len(queries.captured_queries) == 2, [q["sql"] for q in queries.captured_queries]
        sql = _group_sql(queries)
        assert len(sql) == 1 and re.search(r"COUNT\(|LIMIT|EXISTS|SELECT 1 AS", sql[0]), sql

    def test_unsliced_parents_keep_the_one_prefetch(self, world) -> None:
        with CaptureQueriesContext(connection) as queries:
            rows = _jit(_users(), LIGHT["all-count"])
        assert [r["groups"]["all"]["count"] for r in rows] == [MANY, 2, 0]
        assert len(queries.captured_queries) == 2
        assert "COUNT(" not in _group_sql(queries)[0]

    def test_both_sides_of_a_raised_threshold(self, world) -> None:
        with _Cap(3):
            with CaptureQueriesContext(connection) as below:
                rows = _jit(_users()[:3], LIGHT["all-count"])
            with CaptureQueriesContext(connection) as above:
                _jit(_users()[:4], LIGHT["all-count"])
        assert [r["groups"]["all"]["count"] for r in rows] == [MANY, 2, 0]
        # At the threshold: one COUNT per parent, no prefetch.
        assert len(below.captured_queries) == 1 + 3
        assert all("COUNT(" in s for s in _group_sql(below))
        # Past it: the one prefetch query.
        assert len(above.captured_queries) == 2
        assert "COUNT(" not in _group_sql(above)[0]

    def test_zero_always_prefetches(self, world) -> None:
        with _Cap(0):
            with CaptureQueriesContext(connection) as queries:
                _jit(_users()[:1], LIGHT["all-count"])
        assert "COUNT(" not in _group_sql(queries)[0]

    def test_a_row_read_beside_it_keeps_the_prefetch(self, world) -> None:
        source = (
            "{% for u in users %}{{ u.groups.all.count }}"
            "{% for g in u.groups.all %}{{ g.name }}{% endfor %}{% endfor %}"
        )
        with CaptureQueriesContext(connection) as queries:
            _jit(_users()[:1], source)
        assert len(queries.captured_queries) == 2
        assert "COUNT(" not in _group_sql(queries)[0]


# ===========================================================================
# ``{% with n=...|length %}`` and the row at an index printed whole
# ===========================================================================
SHAPES = {
    "with-length": "{% for u in users %}{% with n=u.groups.all|length %}[{{ n }}"
    "{% if n > 1 %}M{% endif %}]{% endwith %}{% endfor %}",
    "row-whole": "{% for u in users %}[{{ u.groups.all.0 }}]{% endfor %}",
    "row-whole-upper": "{% for u in users %}[{{ u.groups.all.1|upper }}]{% endfor %}",
    "row-whole-and-field": "{% for u in users %}[{{ u.groups.all.0 }}={{ u.groups.all.0.name }}]"
    "{% endfor %}",
    "row-if": "{% for u in users %}[{% if u.groups.all.1 %}Y{% endif %}]{% endfor %}",
}


@pytest.mark.django_db
class TestTheWithLengthAndTheWholeIndexedRow:
    @pytest.mark.parametrize("render", RENDERS)
    @pytest.mark.parametrize("name", list(SHAPES))
    def test_renders_like_django(self, world, render, name) -> None:
        _agrees(render, SHAPES[name], lambda: {"users": _users()})

    def test_the_extracted_shapes(self) -> None:
        found = _rust.extract_template_variables
        assert found(SHAPES["with-length"])["users"] == ["groups.all.__len__"]
        assert found(SHAPES["row-whole-and-field"])["users"] == [
            "groups.all.0.__str__",
            "groups.all.0.name",
        ]

    def test_the_whole_row_reads_one_row(self, world) -> None:
        rows = _jit(_users(), SHAPES["row-whole"])
        assert [len(r["groups"]["all"]) for r in rows] == [1, 1, 0]
        assert rows[0]["groups"]["all"][0]["__str__"] == "g00"

    @pytest.mark.parametrize(
        "paths",
        [["groups.all.0.__str__"], ["groups.all.0.__str__", "groups.all.0.name", "username"]],
    )
    def test_the_codegen_twin_agrees(self, world, paths) -> None:
        users = list(_users())
        assert [_codegen(paths, "ser_row_whole_4326")(u) for u in users] == (
            _rust.serialize_queryset(users, paths)
        )


# ===========================================================================
# #4326 review: never more queries at the default; count / exists fetch no rows;
# no stale twin on a preloaded-context render
# ===========================================================================
NEVER_WORSE = {
    # Two or three light reads of one relation: per-row they are two or three queries
    # per parent, so the one prefetch stays (review item 1).
    "count-exists": "{% for u in users %}{{ u.groups.count }}{{ u.groups.exists }}{% endfor %}",
    "count-index-exists": "{% for u in users %}[{{ u.groups.count }}|{{ u.groups.all.1.name }}"
    "|{{ u.groups.exists }}]{% endfor %}",
    # One read, two fields off it: one query.
    "one-index-two-fields": "{% for u in users %}[{{ u.groups.all.1.name }}"
    "|{{ u.groups.all.1.id }}]{% endfor %}",
    "length-and-if": "{% for u in users %}[{% if u.groups.all %}Y{% endif %}"
    "{{ u.groups.all|length }}]{% endfor %}",
    **LIGHT,
}


def _queries(render, source, setting, bound):
    with _Cap(setting):
        with CaptureQueriesContext(connection) as queries:
            got = _text(render(source, {"users": _users()[:bound]}))
    assert got == _django_render(source, {"users": _users()[:bound]})
    return len(queries.captured_queries)


@pytest.mark.django_db
class TestTheDefaultNeverAddsAQuery:
    @pytest.mark.parametrize("render", RENDERS)
    @pytest.mark.parametrize("name", list(NEVER_WORSE))
    def test_no_more_queries_than_the_prefetch(self, world, render, name) -> None:
        source = NEVER_WORSE[name]
        assert _queries(render, source, 1, 1) <= _queries(render, source, 0, 1)

    @pytest.mark.parametrize("render", RENDERS)
    @pytest.mark.parametrize("name", ["count-exists", "count-index-exists"])
    def test_the_reviewed_shapes_keep_the_prefetch(self, world, render, name) -> None:
        assert _queries(render, NEVER_WORSE[name], 1, 1) == 2

    def test_the_budget_counts_distinct_reads(self) -> None:
        two = ["groups.count", "groups.exists"]
        assert analyze_queryset_optimization(User, two, 1).prefetch_related == {"groups"}
        with _Cap(2):
            assert analyze_queryset_optimization(User, two, 1).prefetch_related == set()
            assert analyze_queryset_optimization(User, two, 2).prefetch_related == {"groups"}
        # One read with two fields off it is one query.
        same = ["groups.all.1.name", "groups.all.1.id"]
        assert analyze_queryset_optimization(User, same, 1).prefetch_related == set()


@pytest.mark.django_db
class TestANestedCountReadsNoRows:
    @pytest.mark.parametrize(
        "source, expected_sql",
        [
            ("[{{ d.users.count }}]", ["COUNT("]),
            ("[{{ d.users.all.count }}]", ["COUNT("]),
            ("[{{ d.users.exists }}{{ d.users.count }}]", ["SELECT 1 AS", "COUNT("]),
        ],
    )
    def test_one_query_per_method_like_django(self, world, source, expected_sql) -> None:
        with CaptureQueriesContext(connection) as queries:
            got = _text(_liveview_render(source, _in_dict()))
        assert got == _django_render(source, _in_dict())
        sql = sorted(q["sql"] for q in queries.captured_queries if "auth_user" in q["sql"])
        assert len(sql) == len(expected_sql), sql
        for fragment in expected_sql:
            assert any(fragment in s for s in sql), sql

    @pytest.mark.parametrize(
        "source",
        [
            "[{{ d.users.count }}{% if d.users %}Y{% endif %}]",
            "[{{ d.users.count }}{{ d.users|length }}]",
            "[{{ d.users.count }}{% for u in d.users %}{{ u.username }}{% endfor %}]",
        ],
    )
    def test_beside_a_read_of_the_rows_they_are_fetched(self, world, source) -> None:
        _agrees(_liveview_render, source, _in_dict)


@pytest.mark.django_db
class TestNoStaleTwinOnAPreloadedRender:
    def test_a_preloaded_render_drops_the_last_renders_twin(self, world) -> None:
        from djust.testing import LiveViewTestClient

        class _V(LiveView):
            template = "<div dj-root>[{{ d.users.all|length }}]</div>"

            def get_context_data(self, **kwargs):
                ctx = super().get_context_data(**kwargs)
                ctx["d"] = {"users": _users()}
                return ctx

        client = LiveViewTestClient(_V)
        client.mount()
        client.render()
        view = client.view_instance
        assert "d" in view.__dict__["_all_method_rows"]  # control: the twin is held

        preloaded = view.get_context_data()
        preloaded["d"] = {"users": ["only"]}  # no QuerySet any more
        view._sync_done_this_cycle = False  # a new cycle, as the async websocket path starts one
        html, _patches, _version = view.render_with_diff(preloaded_context=preloaded)
        kept = view.__dict__["_all_method_rows"]
        assert "d" not in kept and "__djust_nested_twins__" not in kept, kept
        assert "[3]" not in html, html
