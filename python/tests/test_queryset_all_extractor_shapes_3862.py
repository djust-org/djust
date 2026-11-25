"""#3862 items 1 and 3 -- two ``.all`` reads the extractor now files in their own shape.

1. ``{{ u.groups.all.0.name }}`` was O(relation): the extractor erased the index
   (``groups.all.0.name`` -> ``groups.all.name``, the same path as a loop), so
   the serializers built EVERY row of the relation for one name. The index now
   survives when it is the only read of that ``all``, and both serializers read
   ``all()[:index + 1]``: a SQL ``LIMIT`` on an unevaluated relation, a slice of
   a prefetched cache.
3. ``{{ u.groups.all|length }}`` and ``{% if u.groups.all %}`` ran ``str(row)``
   per row: the leaf shape was the same as a loop printing ``{{ g }}``. A read
   of the length or truthiness only now files ``groups.all.__len__`` and ships
   ``count()`` ``None``s, so no row becomes text. ``Permission.__str__`` reads
   its ``content_type``, which made the old shape one query PER PERMISSION.

REAL ``User`` / ``Group`` / ``Permission`` rows; Django's own engine is the oracle
for every rendered string (#1650 / #1638, #2727).
"""

from __future__ import annotations

import html as html_lib
import re

import pytest
from django.contrib.auth.models import Group, Permission, User
from django.db import connection
from django.test.utils import CaptureQueriesContext

from djust import _rust
from djust.live_view import LiveView
from djust.mixins.jit import public_template_paths
from djust.optimization.codegen import compile_serializer, generate_serializer_code
from python.tests.test_queryset_all_traversal_3777 import (
    _backend_render,
    _django_render,
    _liveview_render,
)

RENDERS = [
    pytest.param(_backend_render, id="DjustTemplateBackend"),
    pytest.param(_liveview_render, id="LiveView"),
]

#: Groups held by the first user: enough that "every row" and "one row" differ.
MANY = 30


@pytest.fixture
def world(db):
    groups = [Group.objects.create(name=f"g{i:02d}") for i in range(MANY)]
    perms = list(Permission.objects.order_by("pk")[:MANY])
    assert len(perms) == MANY
    u0 = User.objects.create(username="u0")
    u0.groups.add(*groups)
    u0.user_permissions.add(*perms)
    u1 = User.objects.create(username="u1")
    u1.groups.add(groups[3])
    u1.user_permissions.add(perms[0])
    User.objects.create(username="u2")


def _users():
    return User.objects.order_by("pk")


def _ctx() -> dict:
    return {"users": _users()}


def _text(html: str) -> str:
    """The rendered text, without the LiveView's empty-branch ``<!--dj-if-->`` marker."""
    return re.sub(r"<!--/?dj-if[^>]*-->", "", html_lib.unescape(html))


def _codegen(paths, name):
    return compile_serializer(generate_serializer_code("User", paths, name), name)


def _jit_rows(source: str):
    """``users`` through the LiveView JIT (extraction, optimizer, Rust serializer)."""

    class _V(LiveView):
        template = source

    return _V()._jit_serialize_queryset(_users(), source, "users")


# ===========================================================================
# 1. An index read off a relation's ``all``
# ===========================================================================
INDEXED = {
    "first-row": "{% for u in users %}[{{ u.groups.all.0.name }}]{% endfor %}",
    "two-indices": "{% for u in users %}[{{ u.groups.all.0.name }}|{{ u.groups.all.2.name }}]"
    "{% endfor %}",
    "past-the-end": "{% for u in users %}[{{ u.groups.all.5.id }}]{% endfor %}",
    "beside-a-loop": "{% for u in users %}[{{ u.groups.all.1.name }}:"
    "{% for g in u.groups.all %}{{ g.name }},{% endfor %}]{% endfor %}",
}


@pytest.mark.django_db
class TestAnIndexReadsOneRow:
    @pytest.mark.parametrize("render", RENDERS)
    @pytest.mark.parametrize("name", list(INDEXED))
    def test_renders_like_django(self, world, render, name) -> None:
        source = INDEXED[name]
        expected = _django_render(source, _ctx())
        assert expected.startswith("[") and expected != "[][][]", expected
        assert _text(render(source, _ctx())) == expected

    def test_the_extractor_keeps_the_index(self) -> None:
        found = _rust.extract_template_variables(INDEXED["two-indices"])
        assert found["users"] == ["groups.all.0.name", "groups.all.2.name"]
        # The readers of "what the template reads" see the field, as before.
        assert public_template_paths(found)["users"] == ["groups.all.name"]
        # Beside a loop over the same ``all`` the rows are all needed.
        assert (
            "groups.all.1.name"
            not in _rust.extract_template_variables(INDEXED["beside-a-loop"])["users"]
        )

    def test_the_jit_ships_only_the_rows_up_to_the_index(self, world) -> None:
        with CaptureQueriesContext(connection) as queries:
            rows = _jit_rows(INDEXED["two-indices"])
        assert [len(row["groups"]["all"]) for row in rows] == [3, 1, 0]
        assert [g["name"] for g in rows[0]["groups"]["all"]] == ["g00", "g01", "g02"]
        # The users and the one prefetch of their groups: no query per row.
        assert len(queries.captured_queries) == 2, [q["sql"] for q in queries]

    def test_an_unprefetched_relation_is_read_with_a_limit(self, world) -> None:
        users = list(_users())
        with CaptureQueriesContext(connection) as queries:
            out = _rust.serialize_queryset(users, ["groups.all.0.name"])
        assert [[g["name"] for g in row["groups"]["all"]] for row in out] == [
            ["g00"],
            ["g03"],
            [],
        ]
        group_sql = [q["sql"] for q in queries.captured_queries if "auth_group" in q["sql"]]
        assert len(group_sql) == 3 and all("LIMIT 1" in s for s in group_sql), group_sql

    def test_a_prefetched_relation_is_sliced_without_a_query(self, world) -> None:
        users = list(_users().prefetch_related("groups"))
        with CaptureQueriesContext(connection) as queries:
            out = _rust.serialize_queryset(users, ["groups.all.1.name"])
        assert queries.captured_queries == []
        assert [len(row["groups"]["all"]) for row in out] == [2, 1, 0]

    def test_the_codegen_twin_agrees(self, world) -> None:
        paths = ["groups.all.0.name", "groups.all.2.name", "username"]
        users = list(_users())
        codegen = _codegen(paths, "ser_indexed_3862")
        assert [codegen(u) for u in users] == _rust.serialize_queryset(users, paths)
        assert [len(codegen(u)["groups"]["all"]) for u in users] == [3, 1, 0]


# ===========================================================================
# 3. A length or truthiness read of a relation's ``all``
# ===========================================================================
LENGTH = {
    "length": "{% for u in users %}[{{ u.groups.all|length }}]{% endfor %}",
    "if": "{% for u in users %}[{% if u.groups.all %}Y{% else %}N{% endif %}]{% endfor %}",
    "if-not": "{% for u in users %}[{% if not u.groups.all %}N{% endif %}]{% endfor %}",
    "if-length-gt": "{% for u in users %}[{% if u.groups.all|length > 1 %}M{% endif %}]"
    "{% endfor %}",
    "length-add": "{% for u in users %}[{{ u.groups.all|length|add:1 }}]{% endfor %}",
    "perms": "{% for u in users %}[{{ u.user_permissions.all|length }}"
    "{% if u.user_permissions.all %}Y{% endif %}]{% endfor %}",
    # Beside a row read the rows ship, and answer the length too.
    "beside-join": "{% for u in users %}[{{ u.groups.all|length }}:"
    "{{ u.groups.all|join:',' }}]{% endfor %}",
}


@pytest.mark.django_db
class TestALengthReadBuildsNoRowText:
    @pytest.mark.parametrize("render", RENDERS)
    @pytest.mark.parametrize("name", list(LENGTH))
    def test_renders_like_django(self, world, render, name) -> None:
        source = LENGTH[name]
        expected = _django_render(source, _ctx())
        assert expected.startswith("[") and expected != "[][][]", expected
        assert _text(render(source, _ctx())) == expected

    def test_the_extractor_files_the_length_path(self) -> None:
        found = _rust.extract_template_variables(LENGTH["perms"])
        assert found["users"] == ["user_permissions.all.__len__"]
        assert public_template_paths(found)["users"] == ["user_permissions.all"]
        assert _rust.extract_template_variables(LENGTH["beside-join"])["users"] == ["groups.all"]

    def test_the_jit_ships_the_count_as_placeholders(self, world) -> None:
        rows = _jit_rows(LENGTH["perms"])
        assert [row["user_permissions"]["all"] for row in rows] == [[None] * MANY, [None], []]

    def test_no_query_per_row_where_str_row_ran_one(self, world) -> None:
        """``Permission.__str__`` reads ``content_type``: the old leaf shape ran it
        per permission. The length shape counts from the prefetched cache."""
        users = list(_users().prefetch_related("user_permissions"))
        with CaptureQueriesContext(connection) as queries:
            out = _rust.serialize_queryset(users, ["user_permissions.all.__len__"])
        assert queries.captured_queries == []
        assert [len(row["user_permissions"]["all"]) for row in out] == [MANY, 1, 0]
        # The control: the shape every length read had before #3862.
        users = list(_users().prefetch_related("user_permissions"))
        with CaptureQueriesContext(connection) as queries:
            _rust.serialize_queryset(users, ["user_permissions.all"])
        assert len(queries.captured_queries) >= MANY, len(queries.captured_queries)

    def test_the_jit_runs_no_query_per_permission(self, world) -> None:
        with CaptureQueriesContext(connection) as queries:
            _jit_rows(LENGTH["perms"])
        # The users and the one prefetch of their permissions.
        assert len(queries.captured_queries) == 2, [q["sql"] for q in queries]

    def test_an_unprefetched_relation_is_counted_not_fetched(self, world) -> None:
        users = list(_users())
        with CaptureQueriesContext(connection) as queries:
            out = _rust.serialize_queryset(users, ["groups.all.__len__"])
        sql = [q["sql"] for q in queries.captured_queries]
        assert len(sql) == 3 and all("COUNT(" in s for s in sql), sql
        assert [len(row["groups"]["all"]) for row in out] == [MANY, 1, 0]

    def test_the_codegen_twin_agrees(self, world) -> None:
        paths = ["groups.all.__len__", "user_permissions.all.__len__", "username"]
        users = list(_users().prefetch_related("groups", "user_permissions"))
        codegen = _codegen(paths, "ser_length_3862")
        with CaptureQueriesContext(connection) as queries:
            twin = [codegen(u) for u in users]
        assert queries.captured_queries == []
        assert twin == _rust.serialize_queryset(users, paths)

    def test_the_row_cap_bounds_the_placeholders(self, world, caplog) -> None:
        from djust.config import config

        users = list(_users())
        codegen = _codegen(["groups.all.__len__"], "ser_length_cap_3862")
        previous = config.get("queryset_all_row_cap")
        config.set("queryset_all_row_cap", 5)
        try:
            twin = [codegen(u) for u in users]
        finally:
            config.set("queryset_all_row_cap", previous)
        rust = _rust.serialize_queryset(users, ["groups.all.__len__"], 5)
        assert [len(row["groups"]["all"]) for row in rust] == [5, 1, 0]
        assert twin == rust
        assert any("more than 5 rows" in r.getMessage() for r in caplog.records)
