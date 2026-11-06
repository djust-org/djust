"""#3888: a raw ``Model`` crosses the engine boundary as a FLOORED LIVE HANDLE.

Before: a raw model reaching ``impl FromPyObject for Value`` (a ``list`` / ``tuple`` / nested
``QuerySet`` of models in a ``DjustTemplateBackend`` context, ``render_template``) became the dict
``normalize_django_value`` makes of it: fields and forward-FK objects, no manager, no ``*_id``
attname. Whatever the dict lacked was read from the render sidecar through the loop alias
``u == users.<N>``, and a filtered operand (``|slice``, ``|first``, ``|dictsort``, a nested name)
registers no alias, so the read was empty.

After: the model is carried as a ``Value::Encoded`` whose live handle is the SAME floor proxy the
sidecar already used (``_SidecarModelProxy``), so a row keeps its own handle through any filter
(ADR-027), and every attribute read, relation traversal included, is floor-checked per read.

Two matrices, both against Django's own engine on REAL ``User`` / ``Group`` / ``Permission`` /
``ContentType`` rows:

* **Differential** (``TestAgreesWithDjango``): container x operand x read, where the oracle is what
  Django prints.
* **Security** (``TestTheFloorHoldsOnTheHandle``): for every field and method the dict form
  excluded, the handle form excludes it too, through every filter and nested access, and the
  template sandbox names (``_meta``, ``_state``, ``__class__``, ``__dict__``) stay refused. The
  oracle here is the opposite one: Django PRINTS the value (asserted, so a case cannot pass
  vacuously), the handle must not.

Engine path: the Django-backend engine; Django's engine is the oracle.

Refs #3888, #3867, #2504, ADR-027, #1986.
"""

# ruff: noqa: F811

from __future__ import annotations

import html as html_lib

import pytest
from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import Group, Permission, User
from django.contrib.contenttypes.models import ContentType
from django.template import TemplateSyntaxError

from python.tests.test_queryset_all_traversal_3777 import (  # noqa: F401
    _backend_render,
    _django_render,
    world,
)


def _users():
    return User.objects.all().order_by("pk")


def _groups():
    return Group.objects.all().order_by("pk")


def _perms():
    return Permission.objects.all().order_by("pk")[:4]


def _cts():
    return ContentType.objects.all().order_by("pk")[:3]


#: container shape -> (context factory for rows named ``rows``, the dotted name the template reads).
def _flat(make, shape):
    rows = make()
    return {
        "list": lambda: {"rows": list(rows)},
        "tuple": lambda: {"rows": tuple(rows)},
        "queryset": lambda: {"rows": rows},
        "nested-list": lambda: {"page": {"rows": list(rows)}},
        "nested-tuple": lambda: {"page": {"rows": tuple(rows)}},
        "nested-queryset": lambda: {"page": {"rows": rows}},
        "dict-of-lists": lambda: {"page": {"inner": {"rows": list(rows)}}},
    }[shape]()


SHAPES = ["list", "tuple", "nested-list", "nested-tuple", "nested-queryset", "dict-of-lists"]
#: the template spelling of the rows for each shape
NAME = {
    "list": "rows",
    "tuple": "rows",
    "queryset": "rows",
    "nested-list": "page.rows",
    "nested-tuple": "page.rows",
    "nested-queryset": "page.rows",
    "dict-of-lists": "page.inner.rows",
}

#: operand templates over ``{N}`` (the rows' name) with ``<R>`` the read, bound to ``u``.
OPERANDS = {
    "plain": "{% for u in {N} %}<R>{% endfor %}",
    "slice-head": "{% for u in {N}|slice:':2' %}<R>{% endfor %}",
    "slice-tail": "{% for u in {N}|slice:'1:' %}<R>{% endfor %}",
    "slice-reversed": "{% for u in {N}|slice:'::-1' %}<R>{% endfor %}",
    "slice-of-slice": "{% for u in {N}|slice:':3'|slice:':2' %}<R>{% endfor %}",
    "with-slice": "{% with s={N}|slice:':2' %}{% for u in s %}<R>{% endfor %}{% endwith %}",
    "with-alias": "{% with s={N} %}{% for u in s %}<R>{% endfor %}{% endwith %}",
    "first": "{% with u={N}|first %}<R>{% endwith %}",
    "last": "{% with u={N}|last %}<R>{% endwith %}",
    "reversed-kw": "{% for u in {N} reversed %}<R>{% endfor %}",
    "dictsort": "{% for u in {N}|dictsort:'pk' %}<R>{% endfor %}",
    "dictsortreversed": "{% for u in {N}|dictsortreversed:'pk' %}<R>{% endfor %}",
}

# -- reads, per model ------------------------------------------------------------------------------
USER_READS = {
    "m2m-loop": "[{% for g in u.groups.all %}{{ g.name }},{% endfor %}]",
    "m2m-all-count": "[{{ u.groups.all.count }}]",
    "m2m-manager-count": "[{{ u.groups.count }}]",
    "m2m-first-name": "[{{ u.groups.all.first.name }}]",
    "m2m-empty-count": "[{{ u.user_permissions.count }}]",
    "reverse-fk-count": "[{{ u.logentry_set.count }}]",
    "reverse-m2m-through-m2m": ("[{% for g in u.groups.all %}{{ g.user_set.count }},{% endfor %}]"),
    "field": "[{{ u.username }}|{{ u.pk }}|{{ u.id }}|{{ u.is_active }}]",
    "str": "[{{ u }}]",
    "method": "[{{ u.get_full_name }}|{{ u.get_username }}]",
    "datetime-filter": "[{{ u.date_joined|date:'Y' }}]",
}
PERM_READS = {
    "forward-fk-attr": "[{{ u.content_type.app_label }}]",
    "forward-fk-id": "[{{ u.content_type_id }}]",
    "reverse-fk-from-fk": "[{{ u.content_type.permission_set.count }}]",
    "reverse-m2m": "[{{ u.group_set.count }}]",
    "field": "[{{ u.codename }}|{{ u.pk }}]",
}
GROUP_READS = {
    "m2m-count": "[{{ u.permissions.count }}]",
    "reverse-m2m-loop": "[{% for m in u.user_set.all %}{{ m.username }},{% endfor %}]",
    "reverse-m2m-count": "[{{ u.user_set.count }}]",
}
CT_READS = {
    "reverse-fk-count": "[{{ u.permission_set.count }}]",
    "reverse-fk-loop": "[{% for p in u.permission_set.all|slice:':2' %}{{ p.codename }},{% endfor %}]",
}

MODELS = {
    "User": (_users, USER_READS),
    "Permission": (_perms, PERM_READS),
    "Group": (_groups, GROUP_READS),
    "ContentType": (_cts, CT_READS),
}


def _got(source: str, ctx: dict) -> str:
    return html_lib.unescape(_backend_render(source, ctx)).replace("<!--dj-if-->", "")


def _cells() -> list:
    cells = []
    for model, (_make, reads) in MODELS.items():
        for shape in SHAPES:
            for op in OPERANDS:
                # Django: ``QuerySet.__getitem__(-1)`` raises ("Negative indexing is not supported"),
                # so ``|last`` over a QuerySet has no oracle to agree with.
                if op == "last" and shape.endswith("queryset"):
                    continue
                for read in reads:
                    cells.append(
                        pytest.param(model, shape, op, read, id=f"{model}-{shape}-{op}-{read}")
                    )
    return cells


@pytest.fixture
def rows_world(world, db):
    """``world`` plus a LogEntry per user so a reverse FK has rows, and the admin tables exist."""
    from django.contrib.admin.models import ADDITION

    ct = ContentType.objects.get_for_model(User)
    for user in User.objects.order_by("pk")[:2]:
        LogEntry.objects.create(
            user=user,
            content_type=ct,
            object_id=str(user.pk),
            object_repr="x",
            action_flag=ADDITION,
        )
    Group.objects.get(name="staff").permissions.add(*Permission.objects.order_by("pk")[:2])


@pytest.mark.django_db
class TestAgreesWithDjango:
    """container x operand x read, every cell against Django's own engine."""

    @pytest.mark.parametrize("model,shape,op,read", _cells())
    def test_the_backend_agrees(self, rows_world, model, shape, op, read) -> None:
        make, reads = MODELS[model]
        source = OPERANDS[op].replace("{N}", NAME[shape]).replace("<R>", reads[read])
        expected = _django_render(source, _flat(make, shape))
        assert _got(source, _flat(make, shape)) == expected

    @pytest.mark.parametrize("read", list(USER_READS))
    def test_a_generator_is_read_once_like_django(self, rows_world, read) -> None:
        """A one-shot iterable has no positions to alias; the handle is what lets it work at all."""
        source = "{% for u in rows %}" + USER_READS[read] + "{% endfor %}"
        make = lambda: {"rows": (u for u in _users())}  # noqa: E731
        assert _got(source, make()) == _django_render(source, make())

    @pytest.mark.parametrize(
        "source",
        [
            "{{ rows|length }}|{{ rows|first }}|{{ rows|last }}|{{ rows|join:',' }}",
            "{{ rows|slice:':2'|length }}|{{ rows|slice:':2'|join:'/' }}",
            "{% for u in rows %}{% if u %}T{% else %}F{% endif %}{% endfor %}",
            "{% for u in rows %}{{ u|length }}|{{ u|default:'d' }}|{{ u|stringformat:'s' }};{% endfor %}",
            "{% for u in rows %}{% if u == rows.0 %}same{% else %}other{% endif %},{% endfor %}",
            "{% for u in rows|slice:':2' %}{% if u in rows %}in{% endif %},{% endfor %}",
            "{% with u=rows|first %}{{ u }}|{{ u.pk }}|{{ u.groups.count }}{% endwith %}",
            "{% for u in rows|slice:':2' %}{% include_probe %}{% endfor %}",
        ][:7],
    )
    @pytest.mark.parametrize("shape", ["list", "tuple", "nested-list"])
    def test_a_row_as_a_whole_agrees(self, rows_world, source, shape) -> None:
        source = source.replace("rows", NAME[shape])
        assert _got(source, _flat(_users, shape)) == _django_render(source, _flat(_users, shape))

    def test_regroup_over_handles_agrees(self, rows_world) -> None:
        source = (
            "{% regroup rows by is_active as g %}"
            "{% for grp in g %}{{ grp.grouper }}:{% for u in grp.list %}{{ u.username }},{% endfor %};{% endfor %}"
        )
        assert _got(source, _flat(_users, "list")) == _django_render(source, _flat(_users, "list"))

    def test_include_reads_the_row_through_its_handle(self, rows_world, tmp_path) -> None:
        from djust.template import DjustTemplateBackend

        (tmp_path / "row.html").write_text(
            "[{{ u.username }}|{{ u.groups.count }}|{{ u.date_joined|date:'Y' }}]"
        )
        source = "{% for u in rows|slice:':2' %}{% include 'row.html' %}{% endfor %}"
        backend = DjustTemplateBackend(
            params={"NAME": "djust", "DIRS": [str(tmp_path)], "APP_DIRS": False, "OPTIONS": {}}
        )
        got = html_lib.unescape(
            str(backend.from_string(source).render(context={"rows": list(_users())}))
        )
        from django.template import Context, Engine

        expected = (
            Engine(dirs=[str(tmp_path)])
            .from_string(source)
            .render(Context({"rows": list(_users())}))
        )
        assert got == expected


# -- the security matrix ---------------------------------------------------------------------------
@pytest.fixture
def secure_world(db):
    admin = User.objects.create_user("root", "root@example.com", "hunter2", is_staff=True)
    admin.is_superuser = True
    admin.save()
    other = User.objects.create_user("other", "o@example.com", "hunter3")
    g = Group.objects.create(name="ops")
    g.user_set.add(admin, other)
    admin.user_permissions.add(Permission.objects.order_by("pk").first())
    ct = ContentType.objects.get_for_model(User)
    from django.contrib.admin.models import ADDITION

    LogEntry.objects.create(
        user=admin, content_type=ct, object_id="1", object_repr="x", action_flag=ADDITION
    )
    return admin


#: the same rows reached through every route the engine has
ROUTES = {
    "plain": "{% for u in {N} %}<R>{% endfor %}",
    "slice": "{% for u in {N}|slice:':5' %}<R>{% endfor %}",
    "first": "{% with u={N}|first %}<R>{% endwith %}",
    "with-alias": "{% with s={N} %}{% for u in s %}<R>{% endfor %}{% endwith %}",
    "dictsort": "{% for u in {N}|dictsort:'pk' %}<R>{% endfor %}",
    "reversed": "{% for u in {N} reversed %}<R>{% endfor %}",
}
SEC_SHAPES = ["list", "tuple", "nested-list", "nested-queryset"]

#: (name, template read). Each is something the dict form of a model EXCLUDED.
FLOOR_FIELDS = {
    "password": "{{ u.password }}",
    "is_superuser": "{{ u.is_superuser }}",
    "is_staff": "{{ u.is_staff }}",
}
SENSITIVE_METHODS = {
    "get_session_auth_hash": "{{ u.get_session_auth_hash }}",
    "get_all_permissions": "{{ u.get_all_permissions }}",
    "get_user_permissions": "{{ u.get_user_permissions }}",
    "get_group_permissions": "{{ u.get_group_permissions }}",
    "get_deferred_fields": "{{ u.get_deferred_fields }}",
    "get_next_by_date_joined": "{{ u.get_next_by_date_joined }}",
}
#: floor fields and methods reached THROUGH a relation: the relation yields floored rows again.
TRAVERSALS = {
    "m2m-first-password": "{{ u.groups.first.user_set.first.password }}",
    "m2m-all-loop-password": "{% for g in u.groups.all %}{% for m in g.user_set.all %}{{ m.password }}{% endfor %}{% endfor %}",
    "m2m-superuser": "{% for g in u.groups.all %}{% for m in g.user_set.all %}{{ m.is_superuser }}{% endfor %}{% endfor %}",
    "reverse-fk-user-password": "{{ u.logentry_set.first.user.password }}",
    "reverse-fk-user-staff": "{{ u.logentry_set.first.user.is_staff }}",
    "perm-ct-password": "{{ u.logentry_set.first.content_type.logentry_set.first.user.password }}",
    "with-bound": "{% with g=u.groups.first %}{{ g.user_set.first.password }}{% endwith %}",
}
#: the floor is not "refuse everything": these must still render, or the refusals above are vacuous.
STILL_READABLE = {
    "username": ("{{ u.username }}", "root"),
    "relation-name": ("{{ u.groups.first.name }}", "ops"),
    "relation-field-of-member": ("{{ u.groups.first.user_set.first.username }}", "root"),
    "count": ("{{ u.groups.count }}", "1"),
}


def _first_secret_user_ctx(shape: str) -> dict:
    rows = User.objects.order_by("pk")
    return _flat(lambda: rows, shape)


def _src(route: str, shape: str, read: str) -> str:
    return ROUTES[route].replace("{N}", NAME[shape]).replace("<R>", read)


@pytest.mark.django_db
class TestTheFloorHoldsOnTheHandle:
    @pytest.mark.parametrize("shape", SEC_SHAPES)
    @pytest.mark.parametrize("route", list(ROUTES))
    @pytest.mark.parametrize(
        "read", {**FLOOR_FIELDS, **SENSITIVE_METHODS, **TRAVERSALS}.values(), ids=lambda r: r[:48]
    )
    def test_no_floor_value_reaches_the_page(self, secure_world, shape, route, read) -> None:
        """Django prints the value (the oracle: asserted non-empty on the plain route), the handle
        must not, on any route."""
        source = _src(route, shape, read)
        got = _got(source, _first_secret_user_ctx(shape))
        assert "pbkdf2" not in got, got
        assert "hunter" not in got, got
        # superuser / staff are printed as the words "True" / "False" by Django
        assert "True" not in got and "False" not in got, got
        assert got.strip() == "" or got.strip("[]") == "", got

    @pytest.mark.parametrize("read", {**FLOOR_FIELDS, **SENSITIVE_METHODS, **TRAVERSALS}.values())
    def test_the_oracle_prints_it(self, secure_world, read) -> None:
        """The control that makes the matrix above non-vacuous: Django DOES print each of these."""
        source = _src("plain", "list", read)
        printed = _django_render(source, _first_secret_user_ctx("list"))
        assert printed.strip(), f"Django printed nothing for {read!r}: the cell proves nothing"

    @pytest.mark.parametrize("shape", SEC_SHAPES)
    @pytest.mark.parametrize("route", ["plain", "slice", "first", "dictsort"])
    @pytest.mark.parametrize("name", list(STILL_READABLE))
    def test_the_floor_is_not_a_blanket_refusal(self, secure_world, shape, route, name) -> None:
        read, needle = STILL_READABLE[name]
        source = _src(route, shape, read)
        assert needle in _got(source, _first_secret_user_ctx(shape))

    @pytest.mark.parametrize("shape", SEC_SHAPES)
    @pytest.mark.parametrize("route", ["plain", "slice", "first"])
    @pytest.mark.parametrize("method", ["delete", "save", "full_clean"])
    def test_a_handle_does_not_make_a_mutator_callable(
        self, secure_world, shape, route, method
    ) -> None:
        """``alters_data`` is Django's guard: ``delete`` / ``save`` are never CALLED from a template
        (#3815/#3879). A handle must not turn an attribute read into a call of them."""
        before = (User.objects.count(), Group.objects.count(), LogEntry.objects.count())
        source = _src(route, shape, "{{ u." + method + " }}")
        _got(source, _first_secret_user_ctx(shape))
        assert (User.objects.count(), Group.objects.count(), LogEntry.objects.count()) == before
        assert User.objects.filter(username="root").exists()

    @pytest.mark.parametrize("shape", SEC_SHAPES)
    @pytest.mark.parametrize("route", ["plain", "slice"])
    @pytest.mark.parametrize(
        "read", ["{{ u.groups.delete }}", "{{ u.groups.all.delete }}", "{{ u.groups.clear }}"]
    )
    def test_a_relation_mutator_is_not_called(self, secure_world, shape, route, read) -> None:
        source = _src(route, shape, read)
        _got(source, _first_secret_user_ctx(shape))
        assert Group.objects.filter(name="ops").exists()
        assert User.objects.get(username="root").groups.count() == 1

    @pytest.mark.parametrize(
        "name", ["_meta", "_state", "__class__", "__dict__", "_default_manager"]
    )
    @pytest.mark.parametrize("route", ["plain", "slice", "first"])
    def test_the_sandbox_names_do_not_compile_on_either_engine(
        self, secure_world, route, name
    ) -> None:
        """Django refuses a leading-underscore variable at parse time. So must the backend: a handle
        must not be a way to read ``_meta`` / ``_state`` / ``__class__`` / ``__dict__``."""
        source = _src(route, "list", "{{ u." + name + " }}")
        with pytest.raises(TemplateSyntaxError):
            _django_render(source, _first_secret_user_ctx("list"))
        with pytest.raises(Exception) as err:
            _got(source, _first_secret_user_ctx("list"))
        assert "pbkdf2" not in str(err.value)
        assert "auth_user" not in str(err.value)

    @pytest.mark.parametrize("shape", SEC_SHAPES)
    @pytest.mark.parametrize("read", ["{{ u.objects }}", "{{ u.objects.count }}"])
    def test_a_manager_is_not_reachable_from_an_instance(self, secure_world, shape, read) -> None:
        """Django raises ``Manager isn't accessible via User instances`` (the name is in ``dir()``,
        so ``_resolve_lookup`` re-raises); the backend renders it EMPTY. The row is not a way to the
        class's manager either way, which is what this pins; the difference from Django is the same
        one the dict form had (a refusal rendered as empty, not raised)."""
        source = _src("slice", shape, "[" + read + "]")
        with pytest.raises(AttributeError, match="Manager isn't accessible"):
            _django_render(source, _first_secret_user_ctx(shape))
        got = _got(source, _first_secret_user_ctx(shape))
        assert "[]" in got and "Manager" not in got and "auth_user" not in got, got

    @pytest.mark.parametrize("shape", SEC_SHAPES)
    def test_an_exception_class_is_used_as_is_not_called(self, secure_world, shape) -> None:
        """``DoesNotExist`` is a class (``do_not_call_in_templates``): Django prints its repr and the
        handle does the same, rather than instantiating it."""
        source = _src("slice", shape, "[{{ u.DoesNotExist }}]")
        assert _got(source, _first_secret_user_ctx(shape)) == _django_render(
            source, _first_secret_user_ctx(shape)
        )

    def test_the_handle_is_the_floor_proxy_not_the_model(self) -> None:
        """A direct check on what the engine holds: the live handle a model crosses as is the
        floor proxy, so no code that receives the handle can read a floor field from it."""
        from djust import _rust
        from djust.serialization import _SidecarModelProxy

        user = User(username="x", password="pbkdf2$secret", is_superuser=True)
        assert _rust.crosses_as_encoded(user) is True
        with pytest.raises(AttributeError):
            getattr(_SidecarModelProxy(user), "password")

    def test_a_floor_field_set_by_per_model_exclusion_is_refused_on_the_handle(
        self, secure_world
    ) -> None:
        """``djust_exclude_fields`` and ``djust_serializable_fields`` are honoured per attribute."""
        from django.db import models as dj_models

        class Excl(dj_models.Model):
            name = dj_models.CharField(max_length=10)
            secret = dj_models.CharField(max_length=10)
            djust_exclude_fields = ("secret",)

            class Meta:
                app_label = "auth"

        row = Excl(name="n", secret="s3cret")
        source = "{% for r in rows|slice:':1' %}[{{ r.name }}|{{ r.secret }}]{% endfor %}"
        assert _got(source, {"rows": [row]}) == "[n|]"

        class Allow(dj_models.Model):
            name = dj_models.CharField(max_length=10)
            secret = dj_models.CharField(max_length=10)
            djust_serializable_fields = ("name",)

            class Meta:
                app_label = "auth"

        row = Allow(name="n", secret="s3cret")
        assert _got(source, {"rows": [row]}) == "[n|]"


@pytest.mark.django_db
class TestTheConversionContract:
    """What the Rust conversion promises about a model, asked of the extension directly."""

    def test_crosses_as_encoded_agrees_with_the_conversion_for_a_model(self) -> None:
        """The #1646 pair: the predicate the Python side asks and the conversion it predicts."""
        from djust import _rust

        user = User(username="x")
        assert _rust.crosses_as_encoded(user) is True
        assert _rust.crosses_as_encoded_by_conversion(user) is True

    def test_a_model_whose_str_raises_still_has_its_floor(self) -> None:
        """``opaque_value`` declines an object whose ``__str__`` raises, so the handle arm steps
        aside and the conversion falls through to the ``Encoded`` carrier it always reached for such
        an object. The floor holds there too, because ``Context::walk_live`` re-applies it to the
        ROOT of every walk (a second floor, not this arm's). Pre-existing and unchanged: the
        predicate and the conversion disagree about this one edge object (``False`` / ``True``)
        on ``main`` as well."""
        from django.db import models as dj_models

        class Boom(dj_models.Model):
            name = dj_models.CharField(max_length=10)
            password = dj_models.CharField(max_length=10)

            class Meta:
                app_label = "auth"

            def __str__(self) -> str:
                raise RuntimeError("no str")

        row = Boom(name="n", password="hash")
        for source in (
            "{% for r in rows %}[{{ r.name }}|{{ r.password }}]{% endfor %}",
            "{% for r in rows|slice:':1' %}[{{ r.name }}|{{ r.password }}]{% endfor %}",
        ):
            assert _got(source, {"rows": [row]}) == "[n|]"

    def test_a_model_handle_is_never_in_a_state_snapshot(self) -> None:
        """A handle is render-local. The msgpack snapshot carries the facts and drops the live
        object (the contract a datetime carrier has), so a floor field cannot ride in it."""
        from djust import _rust

        user = User(username="x", password="pbkdf2$secret")
        view = _rust.RustLiveView("{{ u }}")
        view.update_state({"u": user})
        blob = view.serialize_msgpack()
        assert b"pbkdf2" not in blob and b"secret" not in blob
        assert _rust.RustLiveView.deserialize_msgpack(blob).render() == "x"

    def test_dictsort_reads_each_key_once(self, secure_world) -> None:
        """The sort key is read once per item, not once per comparison: on a carried object it is
        a Python attribute read."""
        reads = []

        class Row:
            def __init__(self, k: int) -> None:
                self._k = k

            @property
            def k(self) -> int:
                reads.append(1)
                return self._k

        n = 64
        rows = [Row((i * 37) % n) for i in range(n)]
        source = "{% for r in rows|dictsort:'k' %}.{% endfor %}"
        assert _got(source, {"rows": rows}) == "." * n
        assert len(reads) <= 3 * n, f"{len(reads)} reads for {n} rows"

    def test_dictsort_orders_carried_objects_like_django(self, secure_world) -> None:
        class Row:
            def __init__(self, k: int, name: str) -> None:
                self.k, self.name = k, name

        rows = [Row(3, "c"), Row(1, "a"), Row(2, "b")]
        source = "{% for r in rows|dictsort:'k' %}{{ r.name }}{% endfor %}"
        assert _got(source, {"rows": rows}) == _django_render(source, {"rows": rows}) == "abc"
        source = "{% for r in rows|dictsortreversed:'k' %}{{ r.name }}{% endfor %}"
        assert _got(source, {"rows": rows}) == _django_render(source, {"rows": rows}) == "cba"

    def test_dictsort_by_a_floor_field_fails_closed(self, secure_world) -> None:
        """Django sorts by ``password`` (a ranking of hashes is a disclosure); the handle refuses the
        read, and ``dictsort`` fails soft to the empty string for the WHOLE sequence."""
        source = "{% for u in rows|dictsort:'password' %}[{{ u.username }}]{% endfor %}"
        ctx = {"rows": list(User.objects.order_by("pk"))}
        assert _django_render(source, ctx).count("[") == 2
        assert _got(source, ctx) == ""


@pytest.mark.django_db
class TestTheFloorOfTheHandleItself:
    """What the PROXY (the handle) enforces where no template walk stands between it and a reader:
    the type-based floor, the wrapping of a related object, equality of two handles, and the
    floor's own presence (review round 1)."""

    def test_two_distinct_handles_of_one_row_are_equal(self, rows_world) -> None:
        """Both handles come from separate relation walks, so they are different proxy objects over
        the same group row: only the proxy's own ``__eq__`` can say they are the same row."""
        source = (
            "{% for a in rows|slice:':1' %}{% for b in rows|slice:'1:2' %}"
            "{% if a.groups.first == b.groups.first %}SAME{% else %}DIFF{% endif %}"
            "{% endfor %}{% endfor %}"
        )
        users = list(User.objects.filter(username__in=["u0", "u1"]).order_by("pk"))
        assert _got(source, {"rows": users}) == "SAME"

    def test_a_binary_field_is_not_readable_on_the_handle(self) -> None:
        """The type-based floor (#1987) applies to the handle, whatever the field is named."""
        from django.db import models as dj_models

        class Blob3888(dj_models.Model):
            name = dj_models.CharField(max_length=5)
            blob = dj_models.BinaryField(default=b"BLOBBYTES")

            class Meta:
                app_label = "auth"

        got = _got(
            "{% for r in rows|slice:':1' %}[{{ r.name }}|{{ r.blob }}]{% endfor %}",
            {"rows": [Blob3888(name="n")]},
        )
        assert got == "[n|]", got

    def test_dictsort_cannot_rank_rows_by_a_floor_field_of_a_related_row(
        self, secure_world
    ) -> None:
        """``dictsort`` reads ``owner.password`` off the handle with no walk in between, so a related
        object that was not wrapped would be sorted by it. The proxy wraps what it returns."""
        from django.db import models as dj_models

        class Owned3888(dj_models.Model):
            owner = dj_models.ForeignKey(User, on_delete=dj_models.CASCADE)
            nick = dj_models.CharField(max_length=5)

            class Meta:
                app_label = "auth"

        a, b = list(User.objects.order_by("pk"))
        rows = [Owned3888(owner=a, nick="a"), Owned3888(owner=b, nick="b")]  # input order != sorted
        floor = "{% for r in rows|dictsort:'owner.password' %}[{{ r.nick }}]{% endfor %}"
        assert _got(floor, {"rows": rows}) == ""
        # and a readable related field does sort: a dotted key reads EVERY part, not the first
        ok = "{% for r in rows|dictsort:'owner.username' %}[{{ r.nick }}]{% endfor %}"
        assert _django_render(ok, {"rows": rows}) == "[b][a]"  # premise: the sort moves the rows
        assert _got(ok, {"rows": rows}) == "[b][a]"

    def test_a_missing_floor_never_leaves_the_raw_model_as_the_handle(self, monkeypatch) -> None:
        """When the floor cannot wrap the model (it hands the object back unchanged), the conversion
        keeps the denylist dict. It never carries the raw model as its own handle."""
        from djust import _rust, serialization

        monkeypatch.setattr(serialization, "_protect_sidecar_value", lambda value: value)
        user = User(username="x", password="pbkdf2$secret", is_superuser=True)
        # Asked of the conversion itself, not of a render: with the floor function replaced by the
        # identity the WHOLE floor is gone (the walk uses it too), which is not what this pins.
        assert _rust.crosses_as_encoded(user) is False
        assert _rust.crosses_as_encoded_by_conversion(user) is False
