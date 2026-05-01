"""
Regression tests for #1274.

``assign_async()`` returns ``AsyncResult`` instances. Templates expect to
read ``{{ users.loading }}``, ``{{ users.ok }}``, ``{{ users.failed }}``,
``{{ users.result }}``, and ``{{ users.error }}``. Before #1274, the JIT
serializer's ``normalize_django_value`` and ``DjangoJSONEncoder.default``
had no ``AsyncResult`` branch — the value fell through to ``str()``,
producing a string like ``"AsyncResult(loading=True, ...)"`` that
templates couldn't navigate. Result: ``{% if users.loading %}`` always
evaluated falsy and ``assign_async`` demos rendered blank.

This module locks in the new serializer contract.
"""

import json

from djust.async_result import AsyncResult
from djust.serialization import DjangoJSONEncoder, normalize_django_value


# ---------------------------------------------------------------------------
# AsyncResult.to_dict() — the conversion shape templates depend on.
# ---------------------------------------------------------------------------


class TestAsyncResultToDict:
    def test_pending_dict_shape(self):
        d = AsyncResult.pending().to_dict()
        assert d == {
            "loading": True,
            "ok": False,
            "failed": False,
            "result": None,
            "error": None,
        }

    def test_succeeded_preserves_result(self):
        d = AsyncResult.succeeded({"count": 42}).to_dict()
        assert d["loading"] is False
        assert d["ok"] is True
        assert d["failed"] is False
        assert d["result"] == {"count": 42}
        assert d["error"] is None

    def test_errored_stringifies_exception(self):
        exc = ValueError("upstream timed out")
        d = AsyncResult.errored(exc).to_dict()
        assert d["loading"] is False
        assert d["ok"] is False
        assert d["failed"] is True
        assert d["result"] is None
        assert d["error"] == "upstream timed out"


# ---------------------------------------------------------------------------
# normalize_django_value — the JIT path Template context goes through.
# ---------------------------------------------------------------------------


class TestNormalizeAsyncResult:
    def test_pending_normalizes_to_dict(self):
        result = normalize_django_value(AsyncResult.pending())
        assert result == {
            "loading": True,
            "ok": False,
            "failed": False,
            "result": None,
            "error": None,
        }

    def test_succeeded_normalizes_to_dict(self):
        result = normalize_django_value(AsyncResult.succeeded("hello"))
        assert result["ok"] is True
        assert result["result"] == "hello"

    def test_succeeded_recurses_into_result(self):
        """Inner result containing a non-primitive is normalized too."""
        from datetime import datetime

        # AsyncResult.succeeded with a datetime payload — the nested
        # datetime should be ISO-stringified by the recursive call.
        result = normalize_django_value(
            AsyncResult.succeeded({"ts": datetime(2026, 5, 1, 12, 0, 0)})
        )
        assert result["ok"] is True
        # datetime stringified by normalize_django_value's recursion.
        assert result["result"]["ts"] == "2026-05-01T12:00:00"

    def test_async_result_inside_dict(self):
        """AsyncResult as a value in a context dict: still serialized."""
        ctx = {"users": AsyncResult.pending(), "title": "Dashboard"}
        out = normalize_django_value(ctx)
        assert out["title"] == "Dashboard"
        assert out["users"]["loading"] is True
        assert out["users"]["ok"] is False


# ---------------------------------------------------------------------------
# DjangoJSONEncoder.default — the json.dumps path used by some flows.
# ---------------------------------------------------------------------------


class TestDjangoJSONEncoderAsyncResult:
    def test_json_dumps_pending(self):
        s = json.dumps(AsyncResult.pending(), cls=DjangoJSONEncoder)
        loaded = json.loads(s)
        assert loaded == {
            "loading": True,
            "ok": False,
            "failed": False,
            "result": None,
            "error": None,
        }

    def test_json_dumps_succeeded_with_nested(self):
        from decimal import Decimal

        result = AsyncResult.succeeded({"price": Decimal("9.99")})
        s = json.dumps(result, cls=DjangoJSONEncoder)
        loaded = json.loads(s)
        assert loaded["ok"] is True
        assert loaded["result"]["price"] == 9.99  # Decimal → float

    def test_json_dumps_errored(self):
        s = json.dumps(AsyncResult.errored(RuntimeError("kaboom")), cls=DjangoJSONEncoder)
        loaded = json.loads(s)
        assert loaded["failed"] is True
        assert loaded["error"] == "kaboom"


# ---------------------------------------------------------------------------
# Template-readability — proves {% if users.loading %} works after fix.
# ---------------------------------------------------------------------------


class TestTemplateAccessAfterSerialize:
    def test_loading_key_truthy_when_pending(self):
        """The serialized dict has a `loading` key Django templates can read.

        Before #1274, this key didn't exist (value was str()), so
        `{% if users.loading %}` evaluated falsy.
        """
        ctx = normalize_django_value({"users": AsyncResult.pending()})
        # Django templates resolve `users.loading` via dict.get('loading')
        # before falling back to attribute access.
        assert ctx["users"].get("loading") is True
        assert ctx["users"].get("ok") is False
