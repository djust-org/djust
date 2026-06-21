"""Regression tests for finding #19 — model over-serialization of sensitive fields.

``DjangoJSONEncoder._serialize_model_safely`` used to dump *every* concrete
field of a Django Model, so a public LiveView attribute holding
``request.user`` shipped the ``password`` hash and privilege flags
(``is_superuser``/``is_staff``) to the browser.

This module pins the secure-by-default behavior across all client-bound paths:

- PRIMARY: the sensitive-field denylist inside ``_serialize_model_safely``
  (covers the full dump, the JIT exception/None fallbacks, snapshots,
  and ``get_state``), plus ``settings.DJUST_SENSITIVE_FIELDS`` and the
  per-model ``djust_exclude_fields`` / ``djust_serializable_fields`` controls
  and the ``to_dict()`` opt-out.
- SECONDARY: the JIT empty-paths fallback in
  ``mixins/jit.py:_jit_serialize_model`` emitting only the identity subset
  for a whole-object reference.

The ``TestGateOff`` class is the gate-off sentinel (#1468): it FAILS if the
denylist is removed from ``_serialize_model_safely``.
"""

from django.db import models
from django.db.models.base import ModelState
from django.test import override_settings

from djust.serialization import DjangoJSONEncoder, normalize_django_value


# ---------------------------------------------------------------------------
# Helpers — dynamic models built at import time to avoid Django re-registration
# warnings when the file is collected multiple times.
# ---------------------------------------------------------------------------


def _make_user(*, username="alice", raw_password="hunter2"):
    """Build an in-memory ``User`` (no DB) with a real hashed password."""
    from django.contrib.auth.models import User

    user = User(
        username=username,
        email=f"{username}@example.com",
        first_name="Al",
        last_name="Ice",
        is_staff=True,
        is_superuser=True,
    )
    user.pk = 42
    user.id = 42
    user.set_password(raw_password)  # populates user.password with the hash
    return user


_SecretModel = type(
    "F19SecretModel",
    (models.Model,),
    {
        "__module__": __name__,
        "name": models.CharField(max_length=50, default=""),
        "secret": models.CharField(max_length=50, default=""),
        "__str__": lambda self: f"secret({self.pk})",
        "Meta": type("Meta", (), {"app_label": "tests"}),
        "djust_exclude_fields": ["secret"],
    },
)

_AllowlistModel = type(
    "F19AllowlistModel",
    (models.Model,),
    {
        "__module__": __name__,
        "name": models.CharField(max_length=50, default=""),
        "secret": models.CharField(max_length=50, default=""),
        "extra": models.CharField(max_length=50, default=""),
        "__str__": lambda self: f"allow({self.pk})",
        "Meta": type("Meta", (), {"app_label": "tests"}),
        "djust_serializable_fields": ["name"],
    },
)

_ToDictModel = type(
    "F19ToDictModel",
    (models.Model,),
    {
        "__module__": __name__,
        "name": models.CharField(max_length=50, default=""),
        "password": models.CharField(max_length=128, default=""),
        "to_dict": lambda self: {"custom": "payload", "pk": self.pk},
        "__str__": lambda self: f"todict({self.pk})",
        "Meta": type("Meta", (), {"app_label": "tests"}),
    },
)


def _make(cls, pk=7, **field_values):
    """Instantiate a dynamic model without hitting the database."""
    obj = cls.__new__(cls)
    obj._state = ModelState()
    obj._djust_prop_cache = {}
    obj.pk = pk
    obj.id = pk
    for key, value in field_values.items():
        setattr(obj, key, value)
    return obj


# ---------------------------------------------------------------------------
# PRIMARY denylist — the reproduction + secure default
# ---------------------------------------------------------------------------


class TestSensitiveFieldDenylist:
    """The built-in denylist drops password + privilege flags by default."""

    def test_password_not_serialized(self):
        d = normalize_django_value(_make_user())
        assert "password" not in d, f"password leaked: {d.get('password')!r}"

    def test_privilege_flags_not_serialized(self):
        d = normalize_django_value(_make_user())
        assert "is_superuser" not in d
        assert "is_staff" not in d

    def test_safe_identity_and_display_fields_kept(self):
        d = normalize_django_value(_make_user())
        assert d["pk"] == 42
        assert d["id"] == 42
        assert d["__str__"] == "alice"
        assert d["__model__"] == "User"
        # Non-sensitive display fields still travel.
        assert d["email"] == "alice@example.com"
        assert d["username"] == "alice"
        assert d["first_name"] == "Al"

    def test_encoder_path_also_filtered(self):
        # _serialize_model_safely is reached by both normalize_django_value
        # and the json.dumps(..., cls=DjangoJSONEncoder) snapshot/get_state path.
        result = DjangoJSONEncoder()._serialize_model_safely(_make_user())
        assert "password" not in result
        assert "is_superuser" not in result


class TestSettingsOverride:
    """``DJUST_SENSITIVE_FIELDS`` adds project-wide denied field names."""

    @override_settings(DJUST_SENSITIVE_FIELDS={"email"})
    def test_named_field_dropped(self):
        d = normalize_django_value(_make_user())
        assert "email" not in d
        # Built-in floor still applies on top of the configured set.
        assert "password" not in d
        # An un-named safe field is unaffected.
        assert d["username"] == "alice"

    @override_settings(DJUST_SENSITIVE_FIELDS=("first_name", "last_name"))
    def test_accepts_non_set_iterable(self):
        # Contract is any iterable (#1108) — a tuple must work.
        d = normalize_django_value(_make_user())
        assert "first_name" not in d
        assert "last_name" not in d


class TestPerModelControls:
    """Per-model ``djust_exclude_fields`` / ``djust_serializable_fields``."""

    def test_exclude_fields_drops_listed(self):
        obj = _make(_SecretModel, name="visible", secret="topsecret")
        result = DjangoJSONEncoder()._serialize_model_safely(obj)
        assert "secret" not in result
        assert result["name"] == "visible"

    def test_allowlist_restricts_to_listed_plus_identity(self):
        obj = _make(_AllowlistModel, name="ok", secret="no", extra="no2")
        result = DjangoJSONEncoder()._serialize_model_safely(obj)
        assert set(result.keys()) <= {"name", "pk", "id", "__str__", "__model__"}
        assert result["name"] == "ok"
        assert "secret" not in result
        assert "extra" not in result


class TestToDictOverride:
    """A model-level ``to_dict()`` is the full opt-out (developer ownership)."""

    def test_to_dict_wins(self):
        obj = _make(_ToDictModel, name="x", password="should-not-matter")
        result = DjangoJSONEncoder()._serialize_model_safely(obj)
        assert result == {"custom": "payload", "pk": 7}


# ---------------------------------------------------------------------------
# SECONDARY — JIT empty-paths fallback (whole-object reference)
# ---------------------------------------------------------------------------


class TestJitEmptyPathsFallback:
    """``{{ user }}`` (no field paths) emits ONLY the identity subset."""

    def _serialize(self, obj, template, var):
        from djust.mixins.jit import JITMixin

        class _View(JITMixin):
            pass

        return _View()._jit_serialize_model(obj, template, var)

    def test_whole_object_reference_emits_identity_subset_only(self):
        result = self._serialize(_make_user(), "{{ user }}", "user")
        assert set(result.keys()) <= {"pk", "id", "__str__", "__model__"}
        assert result["pk"] == 42
        assert result["__str__"] == "alice"

    def test_no_sensitive_fields_in_empty_paths_fallback(self):
        result = self._serialize(_make_user(), "{{ user }}", "user")
        assert "password" not in result
        assert "is_superuser" not in result
        assert "email" not in result  # not referenced → not emitted


# ---------------------------------------------------------------------------
# GATE-OFF sentinel (#1468) — must FAIL if the denylist is removed.
# ---------------------------------------------------------------------------


class TestGateOff:
    """Sentinel: removing the sensitive-field skip makes this fail.

    If a future change drops the ``_field_is_serializable`` guard from
    ``_serialize_model_safely``, the password hash reappears in the output and
    this assertion fails — the canary for the whole denylist.
    """

    def test_password_absent_is_load_bearing(self):
        user = _make_user(raw_password="gate-off-canary")
        serialized = normalize_django_value(user)
        assert "password" not in serialized, (
            "Sensitive-field denylist (finding #19) appears to be disabled — "
            "the password hash is back in the serialized model output."
        )
