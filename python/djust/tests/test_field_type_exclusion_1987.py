"""#1987 — TYPE-based serialization floor.

The name/method floor (finding #19 / #1868) drops fields by NAME. #1987 adds a
complementary TYPE floor so a field whose *type* should never reach the client
is dropped regardless of its name:

- ``BinaryField`` — raw bytes, always dropped.
- Best-effort encrypted-field detection (class name contains ``Encrypted`` /
  ``Fernet``) — so encrypted columns don't leak when django-encrypted-fields /
  django-fernet-fields are used.
- Any class named in ``LIVEVIEW_CONFIG['sensitive_field_types']``.

``FileField``/``ImageField`` are explicitly NOT excluded (they serialize a URL).

Both client-bound paths share ONE authority (``_field_type_is_excluded``), so
the eager encoder and the lazy sidecar proxy can't drift (#1646):

- EAGER: ``DjangoJSONEncoder._serialize_model_safely`` (via
  ``normalize_django_value``).
- SIDECAR: ``_SidecarModelProxy.__getattr__`` (the template getattr walk).

Gate-off (#1468): removing either wired check makes
``test_eager_drops_binaryfield`` / ``test_sidecar_refuses_binaryfield`` FAIL —
they are the sentinels.
"""

import pytest
from django.db import models

from djust.config import get_config
from djust.serialization import (
    _field_type_excluded_for,
    _field_type_is_excluded,
    _protect_sidecar_value,
    normalize_django_value,
)

# A CharField subclass whose CLASS NAME contains "Encrypted" — exercises the
# best-effort encrypted-field detection with no real crypto dependency.
EncryptedCharField = type("EncryptedCharField", (models.CharField,), {"__module__": __name__})

_TypeFloorModel = type(
    "F1987TypeFloorModel",
    (models.Model,),
    {
        "__module__": __name__,
        "name": models.CharField(max_length=50, default=""),
        "blob": models.BinaryField(default=b""),
        "avatar": models.FileField(upload_to="uploads/", blank=True, default=""),
        "token": EncryptedCharField(max_length=64, default=""),
        "__str__": lambda self: f"tf({self.pk})",
        "Meta": type("Meta", (), {"app_label": "tests"}),
    },
)


def _fields():
    return {f.name: f for f in _TypeFloorModel._meta.get_fields() if hasattr(f, "name")}


def _inst():
    obj = _TypeFloorModel(name="visible", blob=b"\x00\x01\x02", token="s3cret-token")
    obj.pk = 7
    obj.id = 7
    return obj


class TestFieldTypeAuthority:
    """Unit tests on the single shared authority."""

    def test_binaryfield_excluded(self):
        assert _field_type_is_excluded(_fields()["blob"]) is True

    def test_charfield_not_excluded(self):
        assert _field_type_is_excluded(_fields()["name"]) is False

    def test_filefield_not_excluded(self):
        # FileField (and its ImageField subclass) serialize a URL — never dropped.
        assert _field_type_is_excluded(_fields()["avatar"]) is False

    def test_encrypted_named_type_excluded(self):
        assert _field_type_is_excluded(_fields()["token"]) is True

    def test_excluded_for_resolves_field_by_name(self):
        assert _field_type_excluded_for(_TypeFloorModel, "blob") is True
        assert _field_type_excluded_for(_TypeFloorModel, "name") is False

    def test_excluded_for_non_field_is_false(self):
        # A property / method / unknown name is not type-excluded (the name /
        # method floor governs those); resolution failure → not excluded.
        assert _field_type_excluded_for(_TypeFloorModel, "does_not_exist") is False


class TestConfiguredTypes:
    def test_configured_type_name_excludes(self):
        cfg = get_config()
        orig = cfg._config.get("sensitive_field_types")
        cfg._config["sensitive_field_types"] = ["CharField"]
        try:
            # CharField is now configured-sensitive → even `name` is excluded.
            assert _field_type_is_excluded(_fields()["name"]) is True
        finally:
            cfg._config["sensitive_field_types"] = orig

    def test_default_config_excludes_nothing_extra(self):
        # With the default empty list, a plain CharField stays serializable.
        assert _field_type_is_excluded(_fields()["name"]) is False


class TestEagerPath:
    def test_eager_drops_binaryfield(self):
        """SENTINEL (#1468): removing the eager wired check leaks `blob`."""
        d = normalize_django_value(_inst())
        assert "blob" not in d
        assert d["name"] == "visible"

    def test_eager_drops_encrypted_named(self):
        d = normalize_django_value(_inst())
        assert "token" not in d

    def test_eager_keeps_filefield(self):
        # FileField is NOT type-excluded — its key survives the floor.
        d = normalize_django_value(_inst())
        assert "avatar" in d


class TestSidecarPath:
    def test_sidecar_refuses_binaryfield(self):
        """SENTINEL (#1468): removing the sidecar wired check leaks `blob`."""
        proxy = _protect_sidecar_value(_inst())
        with pytest.raises(AttributeError):
            getattr(proxy, "blob")

    def test_sidecar_refuses_encrypted_named(self):
        proxy = _protect_sidecar_value(_inst())
        with pytest.raises(AttributeError):
            getattr(proxy, "token")

    def test_sidecar_allows_plain_charfield(self):
        proxy = _protect_sidecar_value(_inst())
        assert getattr(proxy, "name") == "visible"
