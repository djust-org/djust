"""Per-model serialization rules apply to a lazily wrapped model.

``request.user`` is a ``SimpleLazyObject`` (what the auth middleware and the ``auth`` context
processor hand to a template). The per-model rules -- ``djust_exclude_fields``,
``djust_serializable_fields`` and the ``BinaryField`` type exclusion -- were read from
``type(obj)``, which for a lazy wrapper is the wrapper's class, not the model's. The rules must
hold for a wrapped model exactly as they do for the bare instance, through every path that
reads a model: the eager serializer, the template sidecar proxy, and a real render with a
context processor.

The rule: a lazy wrapper is resolved ONCE and the model it holds is what the rules are read from.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.contrib.sessions.backends.db import SessionStore
from django.db import models
from django.test import RequestFactory, override_settings
from django.utils.functional import SimpleLazyObject

from djust import LiveView
from djust.mixins.context import _context_processors_cache, _resolved_processors_cache
from djust.serialization import (
    DjangoJSONEncoder,
    _SidecarModelProxy,
    _protect_sidecar_value,
    normalize_django_value,
)
from djust.utils import clear_template_dirs_cache

# ---------------------------------------------------------------------------
# Models (in memory; no database)
# ---------------------------------------------------------------------------

_ExcludeModel = type(
    "LazyRulesExcludeModel",
    (models.Model,),
    {
        "__module__": __name__,
        "name": models.CharField(max_length=50, default=""),
        "note": models.CharField(max_length=50, default=""),
        "__str__": lambda self: f"excl({self.pk})",
        "Meta": type("Meta", (), {"app_label": "tests"}),
        "djust_exclude_fields": ["note"],
    },
)

_AllowModel = type(
    "LazyRulesAllowModel",
    (models.Model,),
    {
        "__module__": __name__,
        "name": models.CharField(max_length=50, default=""),
        "note": models.CharField(max_length=50, default=""),
        "__str__": lambda self: f"allow({self.pk})",
        "Meta": type("Meta", (), {"app_label": "tests"}),
        "djust_serializable_fields": ["name"],
    },
)

_BlobModel = type(
    "LazyRulesBlobModel",
    (models.Model,),
    {
        "__module__": __name__,
        "name": models.CharField(max_length=50, default=""),
        "blob": models.BinaryField(default=b""),
        "__str__": lambda self: f"blob({self.pk})",
        "Meta": type("Meta", (), {"app_label": "tests"}),
    },
)

_PlainModel = type(
    "LazyRulesPlainModel",
    (models.Model,),
    {
        "__module__": __name__,
        "name": models.CharField(max_length=50, default=""),
        "__str__": lambda self: f"plain({self.pk})",
        "Meta": type("Meta", (), {"app_label": "tests"}),
    },
)

_OptOutModel = type(
    "LazyRulesOptOutModel",
    (models.Model,),
    {
        "__module__": __name__,
        "name": models.CharField(max_length=50, default=""),
        "password": models.CharField(max_length=50, default=""),
        "__str__": lambda self: f"optout({self.pk})",
        "Meta": type("Meta", (), {"app_label": "tests"}),
        "djust_serialize_sensitive_fields": ["password"],
    },
)

_ToDictModel = type(
    "LazyRulesToDictModel",
    (models.Model,),
    {
        "__module__": __name__,
        "name": models.CharField(max_length=50, default=""),
        "note": models.CharField(max_length=50, default=""),
        "to_dict": lambda self: {"custom": "payload", "pk": self.pk},
        "__str__": lambda self: f"todict({self.pk})",
        "Meta": type("Meta", (), {"app_label": "tests"}),
    },
)

MARK = "VALUE-7c1e"


def _instance(cls, **kw):
    obj = cls(name="visible", **kw)
    obj.pk = 7
    obj.id = 7
    return obj


def _lazy(obj, calls=None):
    def factory():
        if calls is not None:
            calls.append(1)
        return obj

    return SimpleLazyObject(factory)


def _cases():
    return {
        "exclude": (_instance(_ExcludeModel, note=MARK), "note"),
        "allowlist": (_instance(_AllowModel, note=MARK), "note"),
        "binary": (_instance(_BlobModel, blob=MARK.encode()), "blob"),
    }


CASE_IDS = ["exclude", "allowlist", "binary"]


# ---------------------------------------------------------------------------
# Eager serializer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASE_IDS)
class TestEagerSerializer:
    def test_bare_instance_is_the_reference(self, case):
        obj, field = _cases()[case]
        assert field not in normalize_django_value(obj)

    def test_normalize_matches_the_bare_instance(self, case):
        obj, field = _cases()[case]
        lazy = _lazy(obj)
        out = normalize_django_value(lazy)
        assert field not in out, out
        assert out == normalize_django_value(obj)
        assert out["name"] == "visible"

    def test_json_encoder_matches_the_bare_instance(self, case):
        obj, field = _cases()[case]
        text = json.dumps({"u": _lazy(obj)}, cls=DjangoJSONEncoder)
        assert MARK not in text, text
        assert (
            json.loads(text)["u"] == json.loads(json.dumps({"u": obj}, cls=DjangoJSONEncoder))["u"]
        )

    def test_nested_in_a_container(self, case):
        obj, field = _cases()[case]
        out = normalize_django_value({"rows": [_lazy(obj)], "one": (_lazy(obj),)})
        assert MARK not in json.dumps(out, cls=DjangoJSONEncoder), out


# ---------------------------------------------------------------------------
# Template sidecar proxy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASE_IDS)
class TestSidecarProxy:
    def test_the_refused_field_is_refused_for_a_wrapped_model(self, case):
        obj, field = _cases()[case]
        proxy = _protect_sidecar_value(_lazy(obj))
        with pytest.raises(AttributeError):
            getattr(proxy, field)

    def test_the_refused_field_is_refused_for_the_bare_instance(self, case):
        obj, field = _cases()[case]
        with pytest.raises(AttributeError):
            getattr(_protect_sidecar_value(obj), field)

    def test_terminal_conversion_omits_the_field(self, case):
        obj, field = _cases()[case]
        proxy = _protect_sidecar_value(_lazy(obj))
        assert field not in proxy.__djust_serialize__()

    def test_an_allowed_field_and_the_bare_read_still_work(self, case):
        obj, field = _cases()[case]
        proxy = _protect_sidecar_value(_lazy(obj))
        assert proxy.name == "visible"
        assert str(proxy) == str(obj)

    def test_the_wrapper_internals_are_not_reachable(self, case):
        obj, field = _cases()[case]
        proxy = _protect_sidecar_value(_lazy(obj))
        for name in ("_wrapped", "_setup"):
            with pytest.raises(AttributeError):
                getattr(proxy, name)


class _TransparentProxy:
    """A minimal object proxy of the ``wrapt`` / ``lazy-object-proxy`` kind: not a Django
    ``LazyObject``, it reports the wrapped object's class through ``__class__``."""

    def __init__(self, wrapped):
        object.__setattr__(self, "_proxied", wrapped)

    @property
    def __class__(self):
        return object.__getattribute__(self, "_proxied").__class__

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_proxied"), name)


@pytest.mark.parametrize("case", CASE_IDS)
class TestTransparentObjectProxy:
    def test_the_sidecar_refuses_the_field_for_a_foreign_proxy(self, case):
        obj, field = _cases()[case]
        proxy = _protect_sidecar_value(_TransparentProxy(obj))
        with pytest.raises(AttributeError):
            getattr(proxy, field)
        assert proxy.name == "visible"

    def test_the_eager_serializer_omits_the_field_for_a_foreign_proxy(self, case):
        obj, field = _cases()[case]
        assert field not in normalize_django_value(_TransparentProxy(obj))


class TestResolvedOnce:
    def test_a_double_wrapper_through_the_sidecar_holds_the_model(self):
        obj, field = _cases()["binary"]
        proxy = _protect_sidecar_value(SimpleLazyObject(lambda: _lazy(obj)))
        assert object.__getattribute__(proxy, "_obj") is obj
        with pytest.raises(AttributeError):
            getattr(proxy, field)

    def test_the_proxy_built_directly_over_an_unresolved_wrapper_holds_the_model(self):
        obj, field = _cases()["binary"]
        calls: list[int] = []
        proxy = _SidecarModelProxy(_lazy(obj, calls))
        assert object.__getattribute__(proxy, "_obj") is obj
        assert len(calls) == 1
        with pytest.raises(AttributeError):
            getattr(proxy, field)

    def test_the_wrapper_is_evaluated_once_across_every_read(self):
        calls: list[int] = []
        lazy = _lazy(_instance(_ExcludeModel, note=MARK), calls)
        proxy = _protect_sidecar_value(lazy)
        normalize_django_value(lazy)
        json.dumps(lazy, cls=DjangoJSONEncoder)
        assert proxy.name == "visible"
        assert len(calls) == 1

    def test_a_wrapper_around_a_non_model_is_left_alone(self):
        lazy = SimpleLazyObject(lambda: {"a": 1})
        assert normalize_django_value(lazy) == {"a": 1}
        assert _protect_sidecar_value(lazy) is lazy

    def test_a_model_without_rules_is_unchanged(self):
        obj = _instance(_PlainModel)
        assert normalize_django_value(_lazy(obj)) == normalize_django_value(obj)

    def test_a_per_model_opt_in_applies_to_a_wrapped_model(self):
        obj = _instance(_OptOutModel, password="declared-visible")
        assert normalize_django_value(obj)["password"] == "declared-visible"
        assert normalize_django_value(_lazy(obj)) == normalize_django_value(obj)
        assert _protect_sidecar_value(_lazy(obj)).password == "declared-visible"

    def test_a_model_level_to_dict_applies_to_a_wrapped_model(self):
        obj = _instance(_ToDictModel, note=MARK)
        assert normalize_django_value(obj) == {"custom": "payload", "pk": 7}
        assert normalize_django_value(_lazy(obj)) == {"custom": "payload", "pk": 7}

    def test_the_sidecar_proxy_holds_the_model_not_the_wrapper(self):
        obj = _instance(_ExcludeModel, note=MARK)
        proxy = _protect_sidecar_value(_lazy(obj))
        assert object.__getattribute__(proxy, "_obj") is obj

    def test_a_wrapper_around_a_wrapper(self):
        obj, field = _cases()["exclude"]
        assert field not in normalize_django_value(SimpleLazyObject(lambda: _lazy(obj)))


# ---------------------------------------------------------------------------
# A real render: request.user through the ``auth`` context processor, and a custom
# processor that hands a lazily wrapped model of another class to the template
# ---------------------------------------------------------------------------


def lazy_profile_processor(request):
    return {"profile": getattr(request, "profile", None)}


_PROCESSORS = [
    "django.template.context_processors.request",
    "django.contrib.auth.context_processors.auth",
    f"{__name__}.lazy_profile_processor",
]

_TEMPLATE = (
    "<div dj-root>"
    "user=[{{ user.username }}]|email=[{{ user.email }}]|first=[{{ user.first_name }}]"
    "|name=[{{ profile.name }}]|note=[{{ profile.note }}]|blob=[{{ profile.blob }}]"
    "</div>"
)


@pytest.fixture
def template_dir():
    tmp = Path(tempfile.mkdtemp())
    (tmp / "lazy_rules_page.html").write_text(_TEMPLATE)
    templates = [
        {
            "BACKEND": "djust.template_backend.DjustTemplateBackend",
            "DIRS": [str(tmp)],
            "APP_DIRS": False,
            "OPTIONS": {"context_processors": _PROCESSORS},
        }
    ]
    with override_settings(TEMPLATES=templates):
        clear_template_dirs_cache()
        _resolved_processors_cache.clear()
        _context_processors_cache.clear()
        yield tmp
    clear_template_dirs_cache()
    _resolved_processors_cache.clear()
    _context_processors_cache.clear()
    shutil.rmtree(tmp, ignore_errors=True)


class _PageView(LiveView):
    template_name = "lazy_rules_page.html"

    def mount(self, request, **kwargs):
        self.count = 1


def _request(user=None, profile=None):
    request = RequestFactory().get("/lazy-rules/")
    request.user = user
    request.profile = profile
    request.session = SessionStore()
    request.session.create()
    return request


def _get(user=None, profile=None):
    response = _PageView.as_view()(_request(user, profile))
    return response.content.decode()


def _diff_render(user=None, profile=None):
    request = _request(user, profile)
    view = _PageView()
    view.setup(request)
    view._initialize_temporary_assigns()
    view.mount(request)
    view.request = request
    view.get_template()
    html, _patches, _version = view.render_with_diff(request)
    return html


def _user():
    u = User(username="alice", email=f"{MARK}@example.com", first_name=MARK)
    u.pk = 11
    u.id = 11
    return u


RENDERERS = [_get, _diff_render]


@pytest.mark.django_db
@pytest.mark.parametrize("render", RENDERERS, ids=["http_get", "render_with_diff"])
class TestRealRenderWithContextProcessors:
    def test_request_user_honours_a_per_model_exclusion(self, template_dir, monkeypatch, render):
        monkeypatch.setattr(User, "djust_exclude_fields", ["email"], raising=False)
        html = render(user=_lazy(_user()))
        assert "user=[alice]" in html, html
        assert MARK + "@example.com" not in html, html
        assert "email=[]" in html, html

    def test_request_user_honours_a_per_model_allowlist(self, template_dir, monkeypatch, render):
        monkeypatch.setattr(User, "djust_serializable_fields", ["username"], raising=False)
        html = render(user=_lazy(_user()))
        assert "user=[alice]" in html, html
        assert "first=[]" in html, html
        assert MARK not in html, html

    def test_a_wrapped_binary_column_is_not_drawn(self, template_dir, render):
        html = render(user=_lazy(_user()), profile=_lazy(_instance(_BlobModel, blob=MARK.encode())))
        assert "name=[visible]" in html, html
        assert "blob=[]" in html, html
        assert MARK not in html.replace(MARK + "@example.com", "").replace(f"first=[{MARK}]", ""), (
            html
        )

    def test_a_wrapped_model_with_a_per_model_exclusion_via_a_processor(self, template_dir, render):
        html = render(user=_lazy(_user()), profile=_lazy(_instance(_ExcludeModel, note=MARK)))
        assert "name=[visible]" in html, html
        assert "note=[]" in html, html

    def test_the_bare_instance_is_the_reference(self, template_dir, monkeypatch, render):
        monkeypatch.setattr(User, "djust_exclude_fields", ["email"], raising=False)
        html = render(user=_user())
        assert "email=[]" in html, html
        assert "user=[alice]" in html, html

    def test_ordinary_fields_still_render(self, template_dir, render):
        html = render(user=_lazy(_user()))
        assert "user=[alice]" in html, html
        assert f"email=[{MARK}@example.com]" in html, html
