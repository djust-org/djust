"""#3888, review round 1: what a Python reader, a tag argument and a state snapshot get from a carried model.

1. **The proxy keeps its model out of reach.** A custom filter or tag handler receives the floor
   proxy, and a generic one walks a dotted attribute path. The live handle refuses every
   underscore-prefixed name (the wrapped instance, the slots, ``__class__``, ``__dict__``,
   ``__reduce__``...) and so does the queryset proxy and the call wrapper it hands out.
2. **A tag argument is still the dict.** A model inside a list / tuple / nested container reaches a
   native or bridged tag as the same floored dict text it always did, so ``{% data_table rows=rows %}``
   over a list of models renders its rows. Proven as a channel contract (the argument text equals
   what the denylist dict serialises to, for every route a model can take) and as a differential
   over every registered native tag (a list of models renders what a list of its dicts renders).
3. **A snapshot never holds a handle.** A model inside a set / frozenset / dict view held in state
   survives the real state backend's round trip with its fields.
4. **A model whose ``__str__`` raises** is described by its own class.

Engine path: the Django-backend engine; the real in-memory state backend.

Refs #3888.
"""

# ruff: noqa: F811

from __future__ import annotations

import html as html_lib
import json

import pytest
from django.contrib.auth.models import Group, User

from python.tests._scratch_models_3888 import scratch_models  # noqa: F401
from python.tests.test_queryset_all_traversal_3777 import _django_render  # noqa: F401

LIBS = {"libraries": {"rv3888": "python.tests._reviewlib_3888"}}
SECRET = "pbkdf2-sentinel-hash-3888"


@pytest.fixture
def users(db):
    root = User.objects.create_user("root", "r@example.com", SECRET, is_staff=True)
    other = User.objects.create_user("other", "o@example.com", SECRET + "-o")
    ops = Group.objects.create(name="ops")
    ops.user_set.add(root, other)
    return [root, other]


def _backend(src: str, ctx: dict) -> str:
    from djust.template import DjustTemplateBackend

    backend = DjustTemplateBackend(
        # APP_DIRS: an inclusion tag (``data_table``) loads its template, under either engine.
        params={"NAME": "djust", "DIRS": [], "APP_DIRS": True, "OPTIONS": LIBS}
    )
    return html_lib.unescape(str(backend.from_string(src).render(context=dict(ctx), request=None)))


# -- 1. the proxy keeps its model out of reach ----------------------------------------------------
#: every underscore-prefixed name is refused for a reader that walks attributes: the wrapped instance
#: lives in a slot the class's own ``__getattribute__`` refuses, with every dunder (the two it
#: answers, ``__class__`` and the engine's hook, carry no instance and are tested separately)
PRIVATE_PATHS = [
    "_obj",
    "_obj.password",
    "_denied",
    "_allowed",
    "_optout",
    "_s",
    "_SidecarModelProxy__s",
    "__dict__",
    "__wrapped__",
    "__self__",
    "__slots__",
    "__reduce__",
    "__reduce_ex__",
    "__getstate__",
    "__init_subclass__",
    "__getattribute__",
    "__eq__",
    "__str__",
]
ROUTES = {
    "plain": "{% for u in rows %}<R>{% endfor %}",
    "slice": "{% for u in rows|slice:':2' %}<R>{% endfor %}",
    "first": "{% with u=rows|first %}<R>{% endwith %}",
    "relation-row": "{% with u=rows|first %}{% with g=u.groups.first %}<R2>{% endwith %}{% endwith %}",
}


@pytest.mark.django_db
class TestAPythonReaderCannotReachTheWrappedObject:
    @pytest.mark.parametrize("path", PRIVATE_PATHS)
    @pytest.mark.parametrize("route", list(ROUTES))
    def test_a_dotted_filter_is_refused(self, users, route, path) -> None:
        var = "g" if route == "relation-row" else "u"
        read = "{% load rv3888 %}[{{ " + var + "|dotted:'" + path + "' }}]"
        src = ROUTES[route].replace("<R>", read).replace("<R2>", read)
        got = _backend(src, {"rows": list(users)})
        assert SECRET not in got and "pbkdf2" not in got, got
        assert "ERR:AttributeError" in got, got

    @pytest.mark.parametrize("name", ["_obj", "_s", "__dict__", "_denied"])
    def test_a_simple_tag_is_refused(self, users, name) -> None:
        src = (
            "{% load rv3888 %}{% for u in rows|slice:':2' %}[{% attr_of u '" + name + "' %}]"
            "{% endfor %}"
        )
        got = _backend(src, {"rows": list(users)})
        assert got == "[ERR:AttributeError][ERR:AttributeError]", got

    @pytest.mark.parametrize("route", ["plain", "slice"])
    def test_class_is_a_class_of_no_methods_not_the_models(self, users, route) -> None:
        read = "{% load rv3888 %}[{{ u|dotted:'__class__.__name__' }}]"
        got = _backend(ROUTES[route].replace("<R>", read), {"rows": list(users)})
        # the class a proxy reports has no Python-level methods and is not the model's
        assert "_Opaque" in got and "User" not in got, got

    def test_plain_python_getattr_on_the_proxy_is_refused_and_the_floor_still_reads(
        self, users
    ) -> None:
        """The unit: the hidden names, by plain ``getattr``, whoever asks."""
        from djust.serialization import _SidecarModelProxy

        proxy = _SidecarModelProxy(users[0])
        for name in PRIVATE_PATHS:
            if "." in name:
                continue
            with pytest.raises(AttributeError):
                getattr(proxy, name)
        assert proxy.username == "root"
        # what the engine itself asks the instance for stays available, and carries no instance
        assert isinstance(proxy.__djust_serialize__(), dict)
        assert "password" not in proxy.__djust_serialize__()

    @pytest.mark.parametrize(
        "path",
        ["_qs", "_is_projection", "all._fn", "all.__self__", "all.__closure__", "all.__wrapped__"],
    )
    def test_the_manager_and_the_call_it_hands_out_hide_their_receiver(self, users, path) -> None:
        src = (
            "{% load rv3888 %}{% for u in rows|slice:':1' %}[{{ u.groups|dotted:'" + path + "' }}]"
            "{% endfor %}"
        )
        got = _backend(src, {"rows": list(users)})
        assert "ERR:AttributeError" in got, got

    @pytest.mark.parametrize("name", ["_qs", "_is_projection", "_s", "__dict__", "__wrapped__"])
    def test_the_queryset_proxy_refuses_underscore_names_for_a_plain_getattr(
        self, users, name
    ) -> None:
        """The wrapped queryset lives in a real slot, which a ``__getattr__`` is never asked about."""
        from djust.serialization import _SidecarModelProxy

        manager = _SidecarModelProxy(users[0]).groups
        with pytest.raises(AttributeError):
            getattr(manager, name)
        assert manager.count() == 1

    def test_a_manager_call_still_works_and_still_warns_as_an_orm_call(self, users) -> None:
        """The wrapper replaced a function: ``alters_data`` and the ORM-call marker survive."""
        from djust.serialization import _SidecarModelProxy

        manager = _SidecarModelProxy(users[0]).groups
        assert manager.count() == 1
        assert manager.all.djust_orm_call is True
        assert getattr(manager.clear, "alters_data", False) is True
        assert not hasattr(manager.all, "_fn")


# -- 2. a tag argument is still the dict -----------------------------------------------------------
def _expected_rows(rows) -> list:
    from djust.serialization import DjangoJSONEncoder, normalize_django_value

    # a datetime crosses as its str() (the engine's tag-argument spelling), as it always did
    return json.loads(
        json.dumps([normalize_django_value(r) for r in rows], cls=DjangoJSONEncoder, default=str)
    )


@pytest.fixture
def capture_tag():
    from djust.template_tags import TagHandler, register

    seen: list = []

    @register("cap3888")
    class _Cap(TagHandler):
        def render(self, args, context):
            seen.append(list(args))
            return "T"

    seen.clear()
    return seen


ARG_ROUTES = {
    "list": ("{% cap3888 rows=rows %}", lambda us: {"rows": list(us)}),
    "tuple": ("{% cap3888 rows=rows %}", lambda us: {"rows": tuple(us)}),
    "nested": ("{% cap3888 rows=page.rows %}", lambda us: {"page": {"rows": list(us)}}),
    "slice": ("{% cap3888 rows=rows|slice:':2' %}", lambda us: {"rows": list(us)}),
    "with": ("{% with r=rows %}{% cap3888 rows=r %}{% endwith %}", lambda us: {"rows": list(us)}),
}


@pytest.mark.django_db
class TestATagArgumentIsStillTheFlooredDict:
    @pytest.mark.parametrize("route", list(ARG_ROUTES))
    def test_the_argument_text_is_the_denylist_dict(self, users, capture_tag, route) -> None:
        src, make = ARG_ROUTES[route]
        _backend(src, make(users))
        (args,) = capture_tag
        (text,) = [a for a in args if a.startswith("rows=")]
        got = json.loads(text.split("=", 1)[1])
        assert got == _expected_rows(users), got
        assert SECRET not in text and "is_staff" not in text

    def test_a_loop_variable_argument_is_the_denylist_dict(self, users, capture_tag) -> None:
        _backend("{% for u in rows %}{% cap3888 user=u %}{% endfor %}", {"rows": list(users)})
        got = [
            json.loads(a.split("=", 1)[1])
            for args in capture_tag
            for a in args
            if a.startswith("user=")
        ]
        assert got == _expected_rows(users)

    def test_data_table_over_a_list_of_models_renders_as_django_renders_it(self, users) -> None:
        """The Rust engine renders the Python tag's own node (one render path per tag). Its
        ``table_cell`` reads a cell by key from a MAPPING, so a model row is an empty row under
        Django's engine and here alike (the native handler was handed JSON dicts and drew the
        fields; #3895 records the row shape as the reference's decision). A LiveView normalises a
        model in its state to the floored dict before render, so a table over view state still
        draws its rows. Nothing floored is printed either way."""
        from django.template import engines

        cols = [{"key": "username", "label": "U"}, {"key": "email", "label": "E"}]
        src = "{% data_table rows=rows columns=cols %}"
        got = _backend(src, {"rows": list(users), "cols": cols})
        django = html_lib.unescape(
            engines["django"]
            .from_string("{% load djust_components %}" + src)
            .render({"rows": list(users), "cols": cols})
        )
        assert got == django
        assert SECRET not in got
        dicts = [{"username": u.username, "email": u.email} for u in users]
        drawn = _backend(src, {"rows": dicts, "cols": cols})
        for needle in ("root", "r@example.com", "other", "o@example.com"):
            assert needle in drawn, drawn

    def test_no_inline_tag_prints_a_floored_field_of_a_list_of_models(self, users) -> None:
        """The sweep over EVERY inline component tag the Rust engine serves: for each list-like
        argument name, a list of models never puts a floored field (the password hash) in the
        output. The tag's own node renders (one render path per tag) and reads each model through
        the bridge floor, so what it prints of a model is what Django's tag prints of it, minus
        what the floor refuses. Derived from the bridge registration, not listed by hand."""
        from djust.components import rust_handlers
        from djust.components.templatetags.djust_components import register

        cols = [{"key": "username", "label": "U"}, {"key": "email", "label": "E"}]
        keys = ["rows", "items", "options", "cards", "data", "entries", "steps", "tabs", "list"]
        checked = rendered = 0
        inline = [
            name
            for name, (kind, _handler) in rust_handlers._bridged_handlers(register).items()
            if kind == "tag"
        ]
        for name in inline:
            for key in keys:
                src = "{% " + name + " " + key + "=rows columns=cols %}"
                checked += 1
                try:
                    out = _backend(src, {"rows": list(users), "cols": cols})
                except Exception:  # noqa: BLE001 - a tag that refuses the argument prints nothing
                    continue
                rendered += 1
                assert SECRET not in out, (name, key)
        assert checked > 200, checked
        assert rendered > 100, rendered


# -- 3. a snapshot never holds a handle ------------------------------------------------------------
@pytest.mark.django_db
class TestASnapshotKeepsTheFieldsOfAModelInAnUnconvertedContainer:
    @pytest.mark.parametrize("make", [set, frozenset, lambda it: {u.pk: u for u in it}.values()])
    def test_the_in_memory_backend_round_trip(self, users, make) -> None:
        from djust import _rust
        from djust.state_backends.memory import InMemoryStateBackend

        view = _rust.RustLiveView("{% for u in rows %}[{{ u.username }}]{% endfor %}")
        view.update_state({"rows": make(users)})
        before = view.render()
        assert "[root]" in before and "[other]" in before
        backend = InMemoryStateBackend()
        backend.set("k3888", view, warn_on_large_state=False)
        restored, _ts = backend.get("k3888")
        after = restored.render()
        assert sorted(after.replace("][", "] [").split()) == sorted(
            before.replace("][", "] [").split()
        )
        assert SECRET not in after

    def test_the_msgpack_bytes_carry_no_floor_value(self, users) -> None:
        from djust import _rust

        view = _rust.RustLiveView("{% for u in rows %}{{ u.username }}{% endfor %}")
        view.update_state({"rows": frozenset(users)})
        blob = view.serialize_msgpack()
        assert SECRET.encode() not in blob and b"is_staff" not in blob


# -- 4. a model whose __str__ raises is described by its own class -----------------------------------
@pytest.mark.django_db
class TestAModelWhoseStrRaisesIsNamedByItsClass:
    def test_an_error_message_names_the_model_class(self, users) -> None:
        """Iterating a model is Django's ``'User' object is not iterable``; a carried model must
        not say it is a ``_SidecarModelProxy``."""
        with pytest.raises(Exception, match=r"'User' object is not iterable"):
            _backend("{% for x in rows|first %}{{ x }}{% endfor %}", {"rows": list(users)})

    def test_the_container_spelling_names_the_model_not_the_proxy(self, scratch_models) -> None:
        from django.db import models as dj_models

        class Raises3888(dj_models.Model):
            name = dj_models.CharField(max_length=10)

            class Meta:
                app_label = "auth"

            def __str__(self) -> str:
                raise RuntimeError("no str")

        got = _backend("{{ rows }}|{{ rows|pprint }}", {"rows": [Raises3888(name="n")]})
        assert "_SidecarModelProxy" not in got, got
        assert "Raises3888" in got, got
