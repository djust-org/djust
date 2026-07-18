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


class TestTypeFloorMemo:
    """The verdict is a pure function of (field CLASS, configured type names),
    and the eager path calls the authority once per field per serialized model
    — a chat-sized render is thousands of calls per event. The memo caches the
    verdict per (class, config) key; a config mutation lands on a NEW key, so
    stale verdicts can never be served after ``LIVEVIEW_CONFIG`` changes."""

    def test_verdict_memoized_per_class(self):
        from djust.serialization import (
            _FIELD_TYPE_EXCLUSION_MEMO,
            _sensitive_field_types,
        )

        _FIELD_TYPE_EXCLUSION_MEMO.clear()
        f = _fields()["name"]
        assert _field_type_is_excluded(f) is False
        key = (type(f), _sensitive_field_types())
        assert _FIELD_TYPE_EXCLUSION_MEMO[key] is False
        # A DIFFERENT instance of the same field class hits the memo (the
        # verdict never depends on instance state).
        f2 = models.CharField(max_length=5)
        assert _field_type_is_excluded(f2) is False

    def test_config_mutation_bypasses_stale_memo(self):
        from djust.serialization import _FIELD_TYPE_EXCLUSION_MEMO

        cfg = get_config()
        orig = cfg._config.get("sensitive_field_types")
        _FIELD_TYPE_EXCLUSION_MEMO.clear()
        f = _fields()["name"]
        assert _field_type_is_excluded(f) is False  # memo populated (excluded=False)
        cfg._config["sensitive_field_types"] = ["CharField"]
        try:
            # New config → new memo key → fresh verdict, no stale False served.
            assert _field_type_is_excluded(f) is True
        finally:
            cfg._config["sensitive_field_types"] = orig


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


class TestHeuristicRefinements:
    """#1987 review M1/M2 — case-insensitive heuristic + one-shot breadcrumb."""

    def test_lowercase_encrypted_name_excluded(self):
        # M2: case-insensitive — a lowercased/oddly-cased variant must not slip.
        lowered = type("encryptedloweredfield", (models.CharField,), {"__module__": __name__})
        assert _field_type_is_excluded(lowered(max_length=10, default="")) is True

    def test_fernet_name_excluded(self):
        fernet = type("FernetTextField", (models.TextField,), {"__module__": __name__})
        assert _field_type_is_excluded(fernet(default="")) is True

    def test_decrypted_name_not_a_false_positive(self):
        # "decrypted" does not contain "encrypted" — must NOT be dropped.
        dec = type("DecryptedViewField", (models.CharField,), {"__module__": __name__})
        assert _field_type_is_excluded(dec(max_length=10, default="")) is False

    def test_heuristic_drop_logs_once_and_only_for_the_heuristic(self):
        from djust.serialization import _HEURISTIC_TYPE_DROP_WARNED

        enc = type("EncryptedOnceField", (models.CharField,), {"__module__": __name__})
        _HEURISTIC_TYPE_DROP_WARNED.discard(enc)
        assert _field_type_is_excluded(enc(max_length=10, default="")) is True
        # M1: the heuristic recorded a one-shot breadcrumb for this class.
        assert enc in _HEURISTIC_TYPE_DROP_WARNED
        # The unconditional BinaryField rule must NOT touch the heuristic set.
        before = set(_HEURISTIC_TYPE_DROP_WARNED)
        assert _field_type_is_excluded(_fields()["blob"]) is True
        assert set(_HEURISTIC_TYPE_DROP_WARNED) == before
