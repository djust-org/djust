"""#1994 — a private model attr survives the state round-trip AS A MODEL.

A Django model cached on a private (``_``-prefixed) LiveView attr (e.g.
``self._workspace = Workspace.objects.get(...)`` in ``mount()``) is persisted to
the session so it survives the HTTP-POST-fallback restore path (which does NOT
re-run ``mount()``). The old path ran ``normalize_django_value`` over private
state, turning the model into the lossy *client* dict — so on restore
``self._workspace`` came back a plain ``dict`` and ``self._workspace.memberships``
raised ``AttributeError`` (the reported traceback at ``mixins/request.py`` ``post()``).

The fix encodes each private model as a re-hydratable reference and re-fetches it
from the DB on restore, so the attr round-trips as a MODEL.

Gate-off (#1468): ``test_private_model_comes_back_as_model_not_dict`` IS the
sentinel — without the encode/decode the restored attr is a ``dict``.
"""

import json

import pytest

from djust import LiveView
from djust.serialization import DjangoJSONEncoder, normalize_django_value


class _WorkspaceView(LiveView):
    def mount(self, request, **kwargs):  # _private attrs set directly in tests
        pass


def _roundtrip_private_state(view):
    """Mirror the real persist → session (normalize + JSON) → restore path
    (``mixins/request.py``) onto a FRESH view instance; return the restored one."""
    view._snapshot_user_private_attrs()
    private = view._get_private_state()
    # Django's session is JSON-serialized; request.py normalizes first.
    stored = json.loads(json.dumps(normalize_django_value(private), cls=DjangoJSONEncoder))
    restored = _WorkspaceView()
    restored._restore_private_state(stored)
    return restored


@pytest.mark.django_db
class TestPrivateModelRoundtrip1994:
    def test_private_model_comes_back_as_model_not_dict(self):
        from django.contrib.auth.models import User

        u = User.objects.create_user("wsowner", email="w@ex.com")
        v = _WorkspaceView()
        v._workspace = u  # private model attr cached in mount()

        restored = _roundtrip_private_state(v)

        assert isinstance(restored._workspace, User), (
            f"private model came back as {type(restored._workspace).__name__}, "
            "not a model — the #1994 round-trip regressed"
        )
        assert restored._workspace.username == "wsowner"  # attribute access works
        assert restored._workspace.pk == u.pk

    def test_private_model_nested_in_dict_rehydrates(self):
        from django.contrib.auth.models import User

        u = User.objects.create_user("nested")
        v = _WorkspaceView()
        v._cache = {"owner": u, "n": 3}  # model nested inside a private dict

        restored = _roundtrip_private_state(v)

        assert isinstance(restored._cache["owner"], User)
        assert restored._cache["n"] == 3  # sibling scalar preserved

    def test_private_model_in_list_rehydrates(self):
        from django.contrib.auth.models import User

        a = User.objects.create_user("a")
        b = User.objects.create_user("b")
        v = _WorkspaceView()
        v._members = [a, b]

        restored = _roundtrip_private_state(v)

        assert all(isinstance(m, User) for m in restored._members)
        assert [m.username for m in restored._members] == ["a", "b"]

    def test_deleted_model_rehydrates_to_none(self):
        from django.contrib.auth.models import User

        u = User.objects.create_user("gone")
        v = _WorkspaceView()
        v._workspace = u
        v._snapshot_user_private_attrs()
        private = v._get_private_state()
        stored = json.loads(json.dumps(normalize_django_value(private), cls=DjangoJSONEncoder))

        u.delete()  # row disappears between save and restore

        restored = _WorkspaceView()
        restored._restore_private_state(stored)  # must not raise
        assert restored._workspace is None  # fail-safe

    def test_non_model_private_attrs_unaffected(self):
        v = _WorkspaceView()
        v._counter = 42
        v._label = "hi"
        v._flags = {"a": True}

        restored = _roundtrip_private_state(v)

        assert restored._counter == 42
        assert restored._label == "hi"
        assert restored._flags == {"a": True}
