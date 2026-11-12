"""#3788 -- the follow-ups the review of #3785 (the #3777 fix) left.

1. A QuerySet reached THROUGH a container (``{"d": {"users": qs}}``, a list of
   dicts) was serialized as a flat field dict, so ``{% for g in x.groups.all %}``
   rendered nothing. ``get_context_data`` only read the template when a
   TOP-LEVEL value was a Model / QuerySet, and the dotted name (``d.users``)
   found no entry in the extraction map (which keys by ROOT name).
2. ``{{ u.groups.all.count }}``, ``.all.first.name`` and ``.all.exists`` were
   empty on a LiveView: the serializer walked the QuerySet's METHODS as if they
   were row fields. Shipped now as ``{"count": n, ...}`` when the template reads
   ``all`` only through those methods. The Python codegen twin (the serializer
   for a ``list[Model]``) does the same, and the two are compared here.
3. A called ``all`` was read without a bound. ``queryset_all_row_cap`` bounds
   it; a method read (``.count`` / ``.first`` / ``.exists``) is never a row read.

Reproduction fidelity (#1650 / #1638): REAL ``User`` / ``Group`` rows and the
REAL related managers through each entry a QuerySet reaches a template by --
``DjustTemplateBackend``, a LiveView mount, an event re-render, an HTTP GET and
a WebSocket mount -- with Django's own engine as the control. Expectations are
DERIVED from Django, not typed in (#2727), except where djust deliberately
differs (the serialization floor).
"""

# ruff: noqa: F811

from __future__ import annotations

import html as html_lib
import logging
import re
import sys

import pytest
from django.apps import AppConfig
from django.apps.registry import Apps
from django.contrib.auth.models import Group, User
from django.db import OperationalError, connection, models
from django.test.utils import CaptureQueriesContext

from djust import _rust
from djust.live_view import LiveView
from djust.mixins.context import _holds_db_value
from djust.mixins.jit import _paths_for_variable, _serializer_function_name
from djust.optimization.codegen import (
    ALL_WHOLE_KEY,
    _build_path_tree,
    all_row_cap,
    compile_serializer,
    generate_serializer_code,
)
from djust.testing import LiveViewTestClient
from python.tests.test_queryset_all_floor_and_errors_3777 import (  # noqa: F401
    QSShelf,
    QSVolume,
    shelves,
)
from python.tests.test_queryset_all_traversal_3777 import (  # noqa: F401
    _backend_render,
    _django_render,
    _liveview_render,
    world,
)

RENDERS = [
    pytest.param(_backend_render, id="DjustTemplateBackend"),
    pytest.param(_liveview_render, id="LiveView"),
]


def _users():
    return User.objects.all().order_by("pk")


def _groups_of(var: str) -> str:
    return "[{% for g in " + var + ".groups.all %}{{ g.name }},{% endfor %}]"


# ===========================================================================
# 1. A QuerySet reached through a container
# ===========================================================================
NESTED = [
    pytest.param(
        "{% for x in d.users %}{{ x.username }}" + _groups_of("x") + ";{% endfor %}",
        lambda: {"d": {"users": _users()}},
        id="dict",
    ),
    pytest.param(
        "{% for r in rows %}{% for x in r.users %}{{ x.username }}"
        + _groups_of("x")
        + ";{% endfor %}{% endfor %}",
        lambda: {"rows": [{"label": "a", "users": _users()}]},
        id="list-of-dict",
    ),
    pytest.param(
        "{% for r in d.rows %}{% for x in r.users %}{{ x.username }}"
        + _groups_of("x")
        + ";{% endfor %}{% endfor %}",
        lambda: {"d": {"rows": [{"users": _users()}]}},
        id="dict-of-list-of-dict",
    ),
    pytest.param(
        "{% for x in d.users %}{{ x.username }}"
        + _groups_of("x")
        + ";{% endfor %}{% for u in top %}{{ u.username }}{% endfor %}",
        lambda: {"d": {"users": _users()}, "top": _users()},
        id="dict-beside-a-top-level-queryset",
    ),
    pytest.param(
        "{{ d.users.0.username }}[{{ d.users.0.groups.all|length }}]",
        lambda: {"d": {"users": _users()}},
        id="indexed",
    ),
    pytest.param(
        "{% for x in d.users %}{{ x.username }}[{{ x.groups.all.count }}];{% endfor %}",
        lambda: {"d": {"users": _users()}},
        id="method-read-through-a-dict",
    ),
    # The same defect for a MODEL and a model list: they looked the dotted name
    # up in the same map.
    pytest.param(
        "{{ d.user.username }}|{{ d.user.groups.count }}"
        "{% for x in top %}{{ x.username }}{% endfor %}",
        lambda: {"d": {"user": User.objects.get(username="u0")}, "top": _users()},
        id="model-in-dict-beside-a-queryset",
    ),
    pytest.param(
        "{% for x in d.us %}{{ x.username }}"
        + _groups_of("x")
        + ";{% endfor %}{% for u in top %}{{ u.username }}{% endfor %}",
        lambda: {"d": {"us": list(_users())}, "top": _users()},
        id="model-list-in-dict",
    ),
]


@pytest.mark.django_db
class TestAQuerySetReachedThroughAContainer3788:
    @pytest.mark.parametrize("render", RENDERS)
    @pytest.mark.parametrize(("source", "make_ctx"), NESTED)
    def test_renders_what_django_renders(self, world, render, source, make_ctx) -> None:
        expected = _django_render(source, make_ctx())
        assert re.search(r"staff|ops|[1-9]", expected), expected  # non-vacuous control
        assert html_lib.unescape(render(source, make_ctx())) == expected

    def test_the_view_keeps_the_raw_value(self, world) -> None:
        """The serialized copy is the renderer's; the view's own attribute is
        still the QuerySet (a render must not replace state)."""
        holder: dict = {}

        class _V(LiveView):
            exposure_policy = "legacy"

            def mount(self, request, **kwargs):
                self.d = {"users": _users()}
                holder["view"] = self

        _V.template = "<div dj-root>{% for x in d.users %}" + _groups_of("x") + "{% endfor %}</div>"
        client = LiveViewTestClient(_V)
        client.mount()
        client.render()
        assert isinstance(holder["view"].d["users"], models.QuerySet)

    def test_an_event_rerender_keeps_the_rows(self, world) -> None:
        class _V(LiveView):
            exposure_policy = "legacy"

            def mount(self, request, **kwargs):
                self.d = {"users": _users()}
                self.n = 0

            def bump(self, **kwargs):
                self.n += 1

        _V.template = (
            "<div dj-root>{{ n }}{% for x in d.users %}" + _groups_of("x") + "{% endfor %}</div>"
        )
        client = LiveViewTestClient(_V)
        client.mount()
        expected = _django_render(
            "{% for x in d.users %}" + _groups_of("x") + "{% endfor %}", {"d": {"users": _users()}}
        )
        client.send_event("bump")
        out = client.render()
        assert expected in out and "staff" in out, out

    def test_the_floor_holds_through_a_container(self, world) -> None:
        """A QuerySet in a dict is held to the serialization floor like one in
        a top-level name: the hash never reaches the context that is sent."""

        class _V(LiveView):
            exposure_policy = "legacy"

            def mount(self, request, **kwargs):
                self.d = {"users": _users()}

        _V.template = "<div dj-root>{% for x in d.users %}[{{ x.password }}]{% endfor %}</div>"
        client = LiveViewTestClient(_V)
        client.mount()
        assert re.search(r"\[\]\[\]\[\]", client.render())
        shipped = client.view_instance.get_context_data()["d"]["users"]
        assert shipped and all("password" not in row for row in shipped)
        assert "pbkdf2" not in repr(shipped)

    @pytest.mark.parametrize("render", RENDERS)
    def test_the_floor_holds_through_a_container_and_a_relation(self, shelves, render) -> None:
        source = (
            "{% for d_s in d.shelves %}{% for v in d_s.volumes.all %}"
            "{{ v.title }}[{{ v.password }}][{{ v.is_staff }}][{{ v.secret }}];"
            "{% endfor %}{% endfor %}"
        )
        got = render(source, {"d": {"shelves": QSShelf.objects.order_by("pk")}})
        assert got == "alpha[][][];beta[][][];"


class TestThePathLookupForAContainer3788:
    MAP = {
        "d": ["users", "users.groups.all", "users.groups.all.name", "users.username"],
        "rows": ["users.username"],
        "users": ["username"],
    }

    def test_a_root_name_is_looked_up_directly(self) -> None:
        assert _paths_for_variable(self.MAP, "users") == ["username"]

    def test_a_dotted_name_is_its_roots_paths_without_the_leading_segments(self) -> None:
        assert _paths_for_variable(self.MAP, "d.users") == [
            "groups.all",
            "groups.all.name",
            "username",
        ]

    def test_the_container_read_whole_is_not_a_path_of_its_value(self) -> None:
        """``users`` alone (the dict key read whole) is not a path INTO the
        value, so it contributes nothing."""
        assert "users" not in _paths_for_variable(self.MAP, "d.users")

    @pytest.mark.parametrize("name", ["missing", "d.missing", "d.users.groups.nothing", ""])
    def test_an_unknown_name_has_no_paths(self, name) -> None:
        assert _paths_for_variable(self.MAP, name) == []

    def test_no_map_has_no_paths(self) -> None:
        assert _paths_for_variable(None, "d.users") == []

    def test_a_dotted_name_makes_a_compilable_serializer_function_name(self) -> None:
        name = _serializer_function_name("d.users", "ab12cd34")
        assert name.isidentifier()
        code = generate_serializer_code("User", ["groups.all.name"], name)
        assert compile_serializer(code, name)

    @pytest.mark.django_db
    def test_a_render_with_a_nested_queryset_changes_no_state(self, world) -> None:
        """Behaviour, not membership: rendering a view whose context holds a
        nested QuerySet writes the per-render template memo, and that write must
        not look like a state change -- else a no-op event re-renders."""
        from djust.websocket import _compute_changed_keys, _snapshot_assigns

        class _V(LiveView):
            exposure_policy = "legacy"

            def mount(self, request, **kwargs):
                self.d = {"users": _users()}

        _V.template = "<div dj-root>{% for x in d.users %}" + _groups_of("x") + "{% endfor %}</div>"
        client = LiveViewTestClient(_V)
        client.mount()
        view = client.view_instance
        before = _snapshot_assigns(view)
        client.render()
        view.get_context_data()
        assert _compute_changed_keys(before, _snapshot_assigns(view)) == set()
        assert view._nested_template_source  # the memo really was written

    def test_the_per_render_template_memo_is_framework_state(self) -> None:
        """It is written by every render of a view with a nested DB value; if
        it counted as user state, a render would look like a state change and a
        no-op event would re-render (the failure the interactive-observation
        tests caught)."""

        from djust.live_view import _FRAMEWORK_INTERNAL_ATTRS

        assert "_nested_template_source" in _FRAMEWORK_INTERNAL_ATTRS

    def test_plain_data_is_not_scanned_for_a_template(self, monkeypatch) -> None:
        """A context with no nested DB value never reads the template source
        for a nested value: the scan answers on a type lookup per value."""
        rows = [{"a": i, "b": "x", "c": None, "d": 1.5} for i in range(2000)]
        assert not _holds_db_value(rows)
        assert not _holds_db_value({"d": {"rows": rows}})

    @pytest.mark.django_db
    def test_a_bounded_depth_finds_a_queryset_three_containers_down(self) -> None:
        assert _holds_db_value({"d": {"rows": [{"users": User.objects.none()}]}})

    @pytest.mark.django_db
    def test_the_depth_bound_is_exact(self) -> None:
        """Four containers hold a QuerySet; a fifth is left as the view built it."""
        qs = User.objects.none()
        assert _holds_db_value({"a": {"b": {"c": {"d": qs}}}})
        assert not _holds_db_value({"a": {"b": {"c": {"d": {"e": qs}}}}})

    def test_a_dict_subclass_with_its_own_spelling_is_left_alone(self) -> None:
        from collections import OrderedDict

        assert not _holds_db_value(OrderedDict(users=User.objects.none()))


# ===========================================================================
# 2. `all` read through QuerySet methods
# ===========================================================================
METHOD_SOURCES = {
    "count": "{% for u in users %}[{{ u.groups.all.count }}]{% endfor %}",
    "exists": "{% for u in users %}[{{ u.groups.all.exists }}]{% endfor %}",
    "exists-if": "{% for u in users %}[{% if u.groups.all.exists %}Y{% else %}N{% endif %}]{% endfor %}",
    "first-name": "{% for u in users %}[{{ u.groups.all.first.name }}]{% endfor %}",
    "last-name": "{% for u in users %}[{{ u.groups.all.last.name }}]{% endfor %}",
    "first-whole": "{% for u in users %}[{{ u.groups.all.first }}]{% endfor %}",
    "first-whole-and-field": "{% for u in users %}[{{ u.groups.all.first }}-{{ u.groups.all.first.name }}]{% endfor %}",
    "last-whole-and-field": "{% for u in users %}[{{ u.groups.all.last }}-{{ u.groups.all.last.name }}]{% endfor %}",
    "all-three": "{% for u in users %}[{{ u.groups.all.first.name }}|{{ u.groups.all.count }}"
    "|{{ u.groups.all.exists }}]{% endfor %}",
}

MAKE_CONTEXT = {
    "QuerySet": lambda: {"users": _users()},
    "list[Model]": lambda: {"users": list(_users())},
}


@pytest.mark.django_db
class TestAllReadThroughQuerySetMethods3788:
    @pytest.mark.parametrize("render", RENDERS)
    @pytest.mark.parametrize("shape", list(MAKE_CONTEXT))
    @pytest.mark.parametrize("name", list(METHOD_SOURCES))
    def test_agrees_with_django(self, world, render, shape, name) -> None:
        source, make = METHOD_SOURCES[name], MAKE_CONTEXT[shape]
        expected = _django_render(source, make())
        assert re.search(r"[1-9]|True|staff|ops|Y", expected), expected
        assert html_lib.unescape(render(source, make())) == expected

    def test_the_serializer_ships_the_methods_not_rows(self, world) -> None:
        out = _rust.serialize_queryset(
            list(_users()), ["groups.all.count", "groups.all.exists", "groups.all.first.name"]
        )
        assert [row["groups"]["all"] for row in out] == [
            {"count": 2, "exists": True, "first": {"name": "staff"}},
            {"count": 1, "exists": True, "first": {"name": "staff"}},
            {"count": 0, "exists": False, "first": None},
        ]

    def test_rust_and_codegen_ship_the_same_methods(self, world) -> None:
        """The #1646 pair: the Rust serializer (a QuerySet) and the codegen it
        twins (a list[Model]) agree on what a method-only ``all`` is."""
        paths = ["groups.all.count", "groups.all.exists", "groups.all.last.name"]
        code = generate_serializer_code("User", paths, "ser_3788")
        codegen = compile_serializer(code, "ser_3788")
        users = list(_users())
        assert _rust.serialize_queryset(users, paths) == [codegen(u) for u in users]

    def test_a_row_read_whole_and_by_field_carries_its_text(self, world) -> None:
        """A dict prints as its repr, so ``first`` / ``last`` read both ways carry
        the ``__str__`` a serialized model carries -- in Rust and in the codegen
        twin -- and only then (reading the field alone ships no text)."""
        both = ["groups.all.first", "groups.all.first.name"]
        field_only = ["groups.all.first.name"]
        code = generate_serializer_code("User", both, "ser_str_3788")
        codegen = compile_serializer(code, "ser_str_3788")
        users = list(_users())
        rust = _rust.serialize_queryset(users, both)
        assert rust == [codegen(u) for u in users]
        assert [r["groups"]["all"]["first"] for r in rust] == [
            {"name": "staff", "__str__": "staff"},
            {"name": "staff", "__str__": "staff"},
            None,
        ]
        alone = _rust.serialize_queryset(users, field_only)
        assert alone[0]["groups"]["all"]["first"] == {"name": "staff"}

    def test_reading_the_rows_too_keeps_the_rows(self, world) -> None:
        """The relation read whole (the loop's iterable), or any row field, is
        a list of rows -- the shape ``{% for %}`` needs."""
        for paths in (
            ["groups.all", "groups.all.count"],
            ["groups.all.name", "groups.all.count"],
            ["groups.all", "groups.all.first.name"],
        ):
            out = _rust.serialize_queryset(list(_users()), paths)
            assert isinstance(out[0]["groups"]["all"], list), paths

    def test_the_codegen_twin_keeps_rows_when_the_relation_is_read_whole(self, world) -> None:
        """Every child of ``all`` is a method name, but ``groups.all`` itself is
        read (the loop's iterable): rows, as in Rust. Without the whole mark the
        twin shipped ``{"count": n}`` and the loop iterated its keys."""
        paths = ["groups.all", "groups.all.count"]
        code = generate_serializer_code("User", paths, "ser_whole_3788")
        codegen = compile_serializer(code, "ser_whole_3788")
        users = list(_users())
        shipped = [codegen(u)["groups"]["all"] for u in users]
        assert all(isinstance(rows, list) for rows in shipped), shipped
        assert [len(rows) for rows in shipped] == [2, 1, 0]
        assert shipped == [r["groups"]["all"] for r in _rust.serialize_queryset(users, paths)]

    @pytest.mark.parametrize("render", RENDERS)
    @pytest.mark.parametrize("shape", list(MAKE_CONTEXT))
    def test_a_loop_beside_a_method_keeps_iterating_the_rows(self, world, render, shape) -> None:
        source = (
            "{% for u in users %}[{% for g in u.groups.all %}x{% endfor %}"
            "|{{ u.groups.all.count }}]{% endfor %}"
        )
        expected = re.sub(r"\|\d+\]", "|]", _django_render(source, MAKE_CONTEXT[shape]()))
        assert expected == "[xx|][x|][|]"
        got = html_lib.unescape(render(source, MAKE_CONTEXT[shape]()))
        assert re.sub(r"\|\d+\]", "|]", got) == expected

    @pytest.mark.parametrize("render", RENDERS)
    def test_a_loop_beside_a_method_still_renders_the_loop(self, world, render) -> None:
        source = (
            "{% for u in users %}[{% for g in u.groups.all %}{{ g.name }},{% endfor %}]{% endfor %}"
        )
        assert html_lib.unescape(render(source, MAKE_CONTEXT["QuerySet"]())) == _django_render(
            source, MAKE_CONTEXT["QuerySet"]()
        )

    def test_a_method_read_is_one_query_per_row_and_reads_no_rows(self, world) -> None:
        """``.count`` is a COUNT and ``.first`` a LIMIT 1 on the QuerySet
        ``all()`` returned -- the serializer never iterates the relation."""
        users = list(_users())
        with CaptureQueriesContext(connection) as queries:
            _rust.serialize_queryset(users, ["groups.all.count", "groups.all.first.name"])
        sql = [q["sql"] for q in queries.captured_queries]
        assert any("COUNT(" in s for s in sql), sql
        assert any("LIMIT 1" in s for s in sql), sql
        assert not any("LIMIT" not in s and "COUNT(" not in s and "auth_group" in s for s in sql), (
            sql
        )


# -- models that need their own registry: a row field named like a method ------
_REGISTRY = Apps(installed_apps=[AppConfig("queryset_all_followups_3788", sys.modules[__name__])])
CALLS: list[str] = []


def _build_models():
    class FlagQS(models.QuerySet):
        def first(self):
            CALLS.append("first")
            return super().first()

        first.alters_data = True  # type: ignore[attr-defined]

        def exists(self):
            CALLS.append("exists")
            return super().exists()

        exists.do_not_call_in_templates = True  # type: ignore[attr-defined]

        def count(self):
            CALLS.append("count")
            if getattr(FlagQS, "boom", None) is not None:
                raise FlagQS.boom
            return super().count()

    class QSBin(models.Model):
        name = models.CharField(max_length=20)

        class Meta:
            app_label = "queryset_all_followups_3788"
            apps = _REGISTRY

    class QSItem(models.Model):
        """Row fields named like the QuerySet methods a template can chain."""

        bin = models.ForeignKey(QSBin, on_delete=models.CASCADE, related_name="items")
        name = models.CharField(max_length=20)
        count = models.IntegerField(default=0)
        first = models.CharField(max_length=20, default="")

        class Meta:
            app_label = "queryset_all_followups_3788"
            apps = _REGISTRY

    class QSNote(models.Model):
        """A plain row whose QuerySet marks ``first`` ``alters_data`` and can fail."""

        bin = models.ForeignKey(QSBin, on_delete=models.CASCADE, related_name="notes")
        name = models.CharField(max_length=20)

        objects = FlagQS.as_manager()

        class Meta:
            app_label = "queryset_all_followups_3788"
            apps = _REGISTRY

    return FlagQS, QSBin, QSItem, QSNote


FlagQS, QSBin, QSItem, QSNote = _build_models()


@pytest.fixture
def bins(transactional_db):
    with connection.schema_editor() as editor:
        editor.create_model(QSBin)
        editor.create_model(QSItem)
        editor.create_model(QSNote)
    full = QSBin.objects.create(name="full")
    QSBin.objects.create(name="empty")
    QSItem.objects.create(bin=full, name="a", count=7, first="F-a")
    QSItem.objects.create(bin=full, name="b", count=9, first="F-b")
    QSNote.objects.create(bin=full, name="n1")
    QSNote.objects.create(bin=full, name="n2")
    CALLS.clear()
    FlagQS.boom = None  # type: ignore[attr-defined]
    try:
        yield
    finally:
        FlagQS.boom = None  # type: ignore[attr-defined]
        with connection.schema_editor() as editor:
            editor.delete_model(QSNote)
            editor.delete_model(QSItem)
            editor.delete_model(QSBin)


def _ser(paths):
    return _rust.serialize_queryset(list(QSBin.objects.order_by("pk")), paths)


class TestTheMethodReadingIsUnambiguous3788:
    def test_a_row_field_named_like_a_method_stays_a_row_field(self, bins) -> None:
        """``{% for i in b.items.all %}{{ i.count }}`` and ``b.items.all.count``
        extract to the same path. A row model that HAS a ``count`` field keeps
        ``all`` a list of rows, so the loop still reads the field."""
        out = _ser(["items.all.count", "items.all.first"])
        assert out[0]["items"]["all"] == [
            {"count": 7, "first": "F-a"},
            {"count": 9, "first": "F-b"},
        ]

    def test_the_method_reading_is_not_used_for_a_name_the_model_lacks_only_in_part(
        self, bins
    ) -> None:
        out = _ser(["items.all.count", "items.all.name"])
        assert isinstance(out[0]["items"]["all"], list)

    @pytest.mark.parametrize("render", RENDERS)
    def test_the_loop_over_such_a_field_renders_it(self, bins, render) -> None:
        source = "{% for b in bins %}{% for i in b.items.all %}{{ i.count }}/{{ i.first }};{% endfor %}{% endfor %}"
        make = lambda: {"bins": QSBin.objects.order_by("pk")}  # noqa: E731
        assert html_lib.unescape(render(source, make())) == _django_render(source, make())

    def test_a_marked_method_is_never_called(self, bins) -> None:
        """``first`` is stamped ``alters_data`` on this QuerySet: the one call
        guard ``all`` itself goes through refuses it, and it is not run."""
        out = _ser(["notes.all.first.name", "notes.all.count"])
        assert out[0]["notes"]["all"] == {"count": 2}
        assert "first" not in CALLS and "count" in CALLS

    def test_the_codegen_twin_never_calls_a_marked_method(self, bins) -> None:
        """The same guard on the Python serializer (a ``list[Model]``)."""
        code = generate_serializer_code("QSBin", ["notes.all.first.name", "notes.all.count"], "g")
        out = compile_serializer(code, "g")(QSBin.objects.get(name="full"))
        assert out == {"notes": {"all": {"count": 2}}}
        assert "first" not in CALLS and "count" in CALLS

    @pytest.mark.parametrize("marker", ["alters_data", "do_not_call_in_templates"])
    def test_the_codegen_all_step_never_calls_a_marked_all(self, marker) -> None:
        """The Python serializer's own ``all`` step goes through the same call
        guard as the Rust one (#3772): a marked ``all`` is not run."""
        from types import SimpleNamespace

        calls: list[str] = []

        def make(marked: bool):
            def all_():
                calls.append("all")
                return []

            if marked:
                setattr(all_, marker, True)
            return SimpleNamespace(groups=SimpleNamespace(all=all_))

        code = generate_serializer_code("Holder", ["groups.all.name"], "ser_guard_3788")
        serializer = compile_serializer(code, "ser_guard_3788")
        serializer(make(True))
        assert calls == []
        serializer(make(False))  # non-vacuity: an unmarked one IS run
        assert calls == ["all"]

    def test_a_do_not_call_in_templates_method_is_never_called(self, bins) -> None:
        """The second marker of the one call guard, in Rust and in the twin."""
        assert _ser(["notes.all.exists", "notes.all.count"])[0]["notes"]["all"] == {"count": 2}
        code = generate_serializer_code("QSBin", ["notes.all.exists", "notes.all.count"], "g")
        twin = compile_serializer(code, "g")(QSBin.objects.get(name="full"))
        assert twin == {"notes": {"all": {"count": 2}}}
        assert "exists" not in CALLS

    def test_the_codegen_twin_keeps_a_row_field_named_like_a_method(self, bins) -> None:
        code = generate_serializer_code("QSBin", ["items.all.count", "items.all.first"], "g")
        out = compile_serializer(code, "g")(QSBin.objects.get(name="full"))
        # Rows, not a method object, and the integer ``count`` field is kept
        # (the generated leaf branch used to call it as a method, #3801).
        rows = out["items"]["all"]
        assert isinstance(rows, list) and [r["first"] for r in rows] == ["F-a", "F-b"]
        assert [r["count"] for r in rows] == [7, 9]

    def test_a_database_error_in_a_method_propagates(self, bins) -> None:
        FlagQS.boom = OperationalError("db is down")  # type: ignore[attr-defined]
        with pytest.raises(OperationalError, match="db is down"):
            _ser(["notes.all.count"])

    @pytest.mark.parametrize("error", [TypeError("needs args"), AttributeError("no such")])
    def test_what_the_codegen_swallows_is_still_swallowed(self, bins, error) -> None:
        FlagQS.boom = error  # type: ignore[attr-defined]
        assert _ser(["notes.all.count"])[0]["notes"]["all"] == {}


@pytest.mark.django_db
class TestTheFloorHoldsOnAMethodRow3788:
    @pytest.mark.parametrize("render", RENDERS)
    def test_first_and_last_rows_are_gated(self, shelves, render) -> None:
        source = (
            "{% for s in shelves %}{{ s.volumes.all.first.title }}"
            "[{{ s.volumes.all.first.password }}][{{ s.volumes.all.last.is_staff }}]"
            "[{{ s.volumes.all.first.secret }}];{% endfor %}"
        )
        got = render(source, {"shelves": QSShelf.objects.order_by("pk")})
        assert got == "alpha[][][];[][][];", got

    def test_the_codegen_twin_gates_first_and_last_rows(self, shelves) -> None:
        paths = [
            "volumes.all.first.title",
            "volumes.all.first.password",
            "volumes.all.last.is_staff",
            "volumes.all.last.secret",
        ]
        code = generate_serializer_code("QSShelf", paths, "g")
        out = compile_serializer(code, "g")(QSShelf.objects.get(name="full"))
        assert out == {"volumes": {"all": {"first": {"title": "alpha"}, "last": {}}}}

    def test_nothing_secret_reaches_the_serialized_value(self, shelves) -> None:
        out = _rust.serialize_queryset(
            list(QSShelf.objects.order_by("pk")),
            [
                "volumes.all.first.title",
                "volumes.all.first.password",
                "volumes.all.last.is_staff",
                "volumes.all.first.secret",
            ],
        )
        shipped = repr(out)
        assert "pw-" not in shipped and "sec-" not in shipped
        assert out[0]["volumes"]["all"]["first"] == {"title": "alpha"}


# ===========================================================================
# 3. The bound on a called `all`
# ===========================================================================
class TestTheRowCap3788:
    def _rows(self, paths, cap):
        return _rust.serialize_queryset(list(_users()), paths, cap)

    @pytest.mark.django_db
    def test_the_object_arm_reads_at_most_cap_rows(self, world) -> None:
        out = self._rows(["groups.all", "groups.all.name"], 1)
        assert [[g["name"] for g in row["groups"]["all"]] for row in out] == [
            ["staff"],
            ["staff"],
            [],
        ]

    @pytest.mark.django_db
    def test_the_leaf_arm_reads_at_most_cap_rows(self, world) -> None:
        out = self._rows(["groups.all"], 1)
        assert [len(row["groups"]["all"]) for row in out] == [1, 1, 0]

    @pytest.mark.django_db
    def test_the_indexed_arm_reads_at_most_cap_rows(self, world) -> None:
        out = self._rows(["groups.all.0.name"], 1)
        assert [len(row["groups"]["all"]) for row in out] == [1, 1, 0]

    @pytest.mark.django_db
    def test_no_cap_reads_every_row(self, world) -> None:
        out = self._rows(["groups.all", "groups.all.name"], None)
        assert [len(row["groups"]["all"]) for row in out] == [2, 1, 0]

    @pytest.mark.django_db
    def test_a_relation_within_the_cap_is_complete_and_silent(self, world, caplog) -> None:
        with caplog.at_level(logging.WARNING, logger="djust.optimization.codegen"):
            out = self._rows(["groups.all", "groups.all.name"], 2)
        assert [len(row["groups"]["all"]) for row in out] == [2, 1, 0]
        assert not [r for r in caplog.records if "queryset_all_row_cap" in r.getMessage()]

    @pytest.mark.django_db
    def test_overflow_is_logged_not_silent(self, world, caplog) -> None:
        with caplog.at_level(logging.WARNING, logger="djust.optimization.codegen"):
            self._rows(["groups.all", "groups.all.name"], 1)
        messages = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any("queryset_all_row_cap" in m and "more than 1 rows" in m for m in messages), (
            messages
        )

    @pytest.mark.django_db
    def test_the_cap_is_a_sql_limit_on_an_unevaluated_relation(self, world) -> None:
        users = list(_users())
        with CaptureQueriesContext(connection) as queries:
            self._rows_for(users, ["groups.all", "groups.all.name"], 1)
        group_sql = [q["sql"] for q in queries.captured_queries if "auth_group" in q["sql"]]
        assert group_sql and all("LIMIT 2" in s for s in group_sql), group_sql

    def _rows_for(self, users, paths, cap):
        return _rust.serialize_queryset(users, paths, cap)

    @pytest.mark.django_db
    def test_a_method_read_is_never_capped(self, world) -> None:
        out = self._rows(["groups.all.count"], 1)
        assert [row["groups"]["all"]["count"] for row in out] == [2, 1, 0]

    @pytest.mark.django_db
    def test_the_codegen_twin_honours_the_same_cap(self, world) -> None:
        from djust.config import config

        paths = ["groups.all.name"]
        code = generate_serializer_code("User", paths, "ser_cap_3788")
        codegen = compile_serializer(code, "ser_cap_3788")
        users = list(_users())
        previous = config.get("queryset_all_row_cap")
        config.set("queryset_all_row_cap", 1)
        try:
            twin = [codegen(u) for u in users]
        finally:
            config.set("queryset_all_row_cap", previous)
        assert twin == self._rows(paths, 1)

    @pytest.mark.django_db
    def test_a_list_reached_through_take_by_tree_is_capped_and_chained(self, world) -> None:
        """``take_by_tree`` reads a list-valued attribute's items by the same
        tree (the codegen's third way into ``all``)."""
        from djust.config import config
        from djust.optimization.codegen import take_by_tree

        users = list(_users())
        previous = config.get("queryset_all_row_cap")
        config.set("queryset_all_row_cap", 1)
        try:
            rows = take_by_tree(users, {"groups": {"all": {"name": {}}}})
            counts = take_by_tree(users, {"groups": {"all": {"count": {}}}})
        finally:
            config.set("queryset_all_row_cap", previous)
        assert [len(row["groups"]["all"]) for row in rows] == [1, 1, 0]
        assert [row["groups"]["all"] for row in counts] == [
            {"count": 2},
            {"count": 1},
            {"count": 0},
        ]

    @pytest.mark.django_db
    def test_the_template_backend_applies_the_same_cap(self, world) -> None:
        """The backend's own QuerySet producer (``template/rendering.py``) is
        the third caller of ``serialize_queryset`` (#1646)."""
        from djust.config import config

        source = (
            "{% for u in users %}[{% for g in u.groups.all %}{{ g.name }},{% endfor %}]{% endfor %}"
        )
        previous = config.get("queryset_all_row_cap")
        config.set("queryset_all_row_cap", 1)
        try:
            capped = _backend_render(source, MAKE_CONTEXT["QuerySet"]())
        finally:
            config.set("queryset_all_row_cap", previous)
        assert capped == "[staff,][staff,][]"

    @pytest.mark.django_db
    def test_the_view_applies_the_configured_cap(self, world) -> None:
        from djust.config import config

        source = (
            "{% for u in users %}[{% for g in u.groups.all %}{{ g.name }},{% endfor %}]{% endfor %}"
        )
        previous = config.get("queryset_all_row_cap")
        config.set("queryset_all_row_cap", 1)
        try:
            capped = _liveview_render(source, MAKE_CONTEXT["QuerySet"]())
        finally:
            config.set("queryset_all_row_cap", previous)
        assert capped == "[staff,][staff,][]"
        assert _liveview_render(source, MAKE_CONTEXT["QuerySet"]()) == _django_render(
            source, MAKE_CONTEXT["QuerySet"]()
        )


class TestTheCapSetting3788:
    @pytest.mark.parametrize(
        ("configured", "expected"),
        [
            (0, None),
            (None, 100_000),
            (5, 5),
            (2**62 - 1, 2**62 - 1),
            (2**62, None),
            (2**63, None),
            (10**30, None),
            (-1, 100_000),
            ("many", 100_000),
            (True, 100_000),
            (2.5, 100_000),
        ],
    )
    def test_value_to_cap(self, configured, expected, caplog) -> None:
        from djust.config import config

        previous = config.get("queryset_all_row_cap")
        config.set("queryset_all_row_cap", configured)
        try:
            with caplog.at_level(logging.WARNING, logger="djust.optimization.codegen"):
                assert all_row_cap() == expected
        finally:
            config.set("queryset_all_row_cap", previous)

    @pytest.mark.django_db
    @pytest.mark.parametrize("cap", [10**30, 2**63, 2**62])
    def test_an_absurd_cap_is_no_cap_not_a_blank_page(self, world, cap) -> None:
        """10**30 reached Rust as a usize it cannot hold, the JIT swallowed the
        OverflowError, and every relation rendered empty."""
        from djust.config import config

        source = (
            "{% for u in users %}[{% for g in u.groups.all %}{{ g.name }},{% endfor %}]{% endfor %}"
        )
        previous = config.get("queryset_all_row_cap")
        config.set("queryset_all_row_cap", cap)
        try:
            got = _liveview_render(source, MAKE_CONTEXT["QuerySet"]())
        finally:
            config.set("queryset_all_row_cap", previous)
        assert got == _django_render(source, MAKE_CONTEXT["QuerySet"]()) == "[staff,ops,][staff,][]"

    @pytest.mark.django_db
    @pytest.mark.parametrize("cap", [2**62 - 1, 2**63 - 1])
    def test_a_direct_call_with_a_huge_cap_reads_every_row(self, world, cap) -> None:
        """The Rust slice bound is clamped, so even a caller that bypasses
        ``all_row_cap()`` gets the rows."""
        out = _rust.serialize_queryset(list(_users()), ["groups.all", "groups.all.name"], cap)
        assert [len(row["groups"]["all"]) for row in out] == [2, 1, 0]

    @pytest.mark.parametrize("cap", [2**62 - 1, 2**63 - 1])
    def test_the_slice_bound_is_clamped_for_a_plain_list_all(self, cap) -> None:
        """A QuerySet whose slice bound overflowed fell back to iterating the
        whole thing, which hid the overflow. A plain list does not: ``rows[:bound]``
        with a wrapped (negative) bound drops every row."""
        from types import SimpleNamespace

        holder = SimpleNamespace(
            groups=SimpleNamespace(
                all=lambda: [SimpleNamespace(name="a"), SimpleNamespace(name="b")]
            )
        )
        out = _rust.serialize_queryset([holder], ["groups.all", "groups.all.name"], cap)
        assert out == [{"groups": {"all": [{"name": "a"}, {"name": "b"}]}}]

    def test_the_default_is_a_ceiling_ordinary_pages_never_reach(self) -> None:
        from djust.config import config

        assert config.get("queryset_all_row_cap") == 100_000


# ===========================================================================
# The tree mark that tells a loop from a method read
# ===========================================================================
class TestTheWholeMark3788:
    def test_a_node_read_whole_and_walked_is_marked(self) -> None:
        tree = _build_path_tree(["groups.all", "groups.all.name"])
        assert ALL_WHOLE_KEY in tree["groups"]["all"]

    def test_a_node_only_walked_is_not_marked(self) -> None:
        tree = _build_path_tree(["groups.all.count"])
        assert ALL_WHOLE_KEY not in tree["groups"]["all"]

    def test_only_an_all_node_is_ever_marked(self) -> None:
        tree = _build_path_tree(["groups", "groups.all.name", "groups.all", "owner", "owner.name"])
        assert ALL_WHOLE_KEY not in tree["groups"]
        assert ALL_WHOLE_KEY not in tree["owner"]

    def test_the_mark_is_never_read_as_an_attribute(self) -> None:
        """It reaches ``_djust_all_methods`` inside the subtree literal, and
        nowhere else: no gate names it and no ``hasattr`` probes it."""
        code = generate_serializer_code("User", ["groups.all", "groups.all.name"], "ser_mark_3788")
        assert not re.search(r"_djust_gate\([^\n]*" + ALL_WHOLE_KEY, code), code
        assert f"hasattr(item_5, '{ALL_WHOLE_KEY}')" not in code
        carriers = [line for line in code.splitlines() if ALL_WHOLE_KEY in line]
        assert carriers and all(
            ("_djust_take(" in c or "_djust_all_methods(" in c) for c in carriers
        )


# ===========================================================================
# The entries that are not a template call: HTTP GET and WebSocket mount
# ===========================================================================
class NestedView(LiveView):
    exposure_policy = "legacy"
    login_required = False
    template = """<div dj-view="test_queryset_all_followups_3788.NestedView">
    {% for x in d.users %}<p>{{ x.username }}[{% for g in x.groups.all %}{{ g.name }},{% endfor %}]</p>{% endfor %}</div>"""

    def mount(self, request, **kwargs):
        self.d = {"users": User.objects.all().order_by("pk")}


class CountView(LiveView):
    exposure_policy = "legacy"
    login_required = False
    template = """<div dj-view="test_queryset_all_followups_3788.CountView">
    {% for u in users %}<p>{{ u.username }}:{{ u.groups.all.count }}/{{ u.groups.all.first.name }}</p>{% endfor %}</div>"""

    def mount(self, request, **kwargs):
        self.users = User.objects.all().order_by("pk")


def _paragraphs(html: str) -> list[str]:
    return re.findall(r"<p>.*?</p>", re.sub(r' dj-id="[^"]*"', "", html))


def _expected_nested() -> list[str]:
    return [
        f"<p>{u.username}[{''.join(g.name + ',' for g in u.groups.order_by('pk'))}]</p>"
        for u in User.objects.order_by("pk")
    ]


def _expected_counts() -> list[str]:
    return [
        f"<p>{u.username}:{u.groups.count()}/{getattr(u.groups.first(), 'name', '')}</p>"
        for u in User.objects.order_by("pk")
    ]


def _seed() -> None:
    staff = Group.objects.create(name="staff")
    User.objects.create(username="solo").groups.add(staff)
    User.objects.create(username="alone")


@pytest.mark.parametrize(
    ("view", "expected"),
    [(NestedView, _expected_nested), (CountView, _expected_counts)],
    ids=["nested", "count"],
)
def test_http_get_renders_the_rows(transactional_db, view, expected) -> None:
    from django.contrib.sessions.middleware import SessionMiddleware
    from django.test import RequestFactory

    _seed()
    request = RequestFactory().get("/x/")
    SessionMiddleware(lambda request: None).process_request(request)
    response = view.as_view()(request)
    assert response.status_code == 200
    got = _paragraphs(response.content.decode())
    assert got == expected()
    assert "staff" in "".join(got)  # non-vacuous: a relation really rendered


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("view_name", "expected"),
    [("NestedView", _expected_nested), ("CountView", _expected_counts)],
    ids=["nested", "count"],
)
async def test_websocket_mount_renders_the_rows(transactional_db, view_name, expected) -> None:
    from channels.db import database_sync_to_async
    from channels.testing import WebsocketCommunicator
    from django.test import override_settings

    from djust.websocket import LiveViewConsumer

    @database_sync_to_async
    def seed() -> list[str]:
        _seed()
        return expected()

    want = await seed()
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        try:
            connected, _ = await communicator.connect()
            assert connected
            await communicator.receive_json_from()
            await communicator.send_json_to({"type": "mount", "view": f"{__name__}.{view_name}"})
            message = await communicator.receive_json_from()
            assert message["type"] == "mount", message
            assert _paragraphs(message["html"]) == want
            assert "staff" in "".join(want)
        finally:
            await communicator.disconnect()
