"""ADR-038 E1: the log sites with no LiveView owner follow the current turn.

djust's Django template backend (``DjustTemplate``) and the PWA sync endpoint
(``pwa/sync.py``) serve ordinary Django requests; neither has a view to read an
exposure policy from. Outside a LiveView turn ADR-038 does not apply and the
log is unchanged. Inside one (a handler calling ``render_to_string``, say) the
turn decides: a nonlegacy owner makes the line value-free (#2951).
"""

import json
import logging
from contextlib import contextmanager

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory

from djust._exposure_diagnostics import diagnostic_scope, restrict_diagnostics
from djust.pwa import sync as pwa_sync
from djust.template import rendering

SENTINEL = "OWNERLESS_SENTINEL"


def _boom(*args, **kwargs):
    raise ValueError(SENTINEL)


class _Owner:
    def __init__(self, policy):
        self.exposure_policy = policy


@contextmanager
def _turn(where):
    if where == "no-turn":
        yield
        return
    with diagnostic_scope():
        restrict_diagnostics(_Owner(where))
        yield


def _jit_queryset(monkeypatch):
    monkeypatch.setattr(rendering, "JIT_AVAILABLE", True)
    monkeypatch.setattr(rendering, "extract_template_variables", _boom, raising=False)
    template = object.__new__(rendering.DjustTemplate)
    template.template_string = "{{ rows }}"
    assert template._jit_serialize_queryset([], "rows") == []


def _jit_model(monkeypatch):
    monkeypatch.setattr(rendering, "JIT_AVAILABLE", True)
    monkeypatch.setattr(rendering, "DjangoJSONEncoder", object)
    monkeypatch.setattr(rendering, "normalize_django_value", _boom)
    template = object.__new__(rendering.DjustTemplate)
    row = type("Row", (), {"pk": 1})()
    assert template._jit_serialize_model(row, "row")["pk"] == 1


def _resolver(monkeypatch):
    resolver = pwa_sync.ConflictResolver()
    resolver.register_resolver("Note", _boom)
    resolver.resolve_conflict("Note", {"a": 1}, {"a": 2})


def _batch(monkeypatch):
    manager = pwa_sync.SyncManager()
    monkeypatch.setattr(manager, "_group_actions", lambda actions: {("create", "Note"): actions})
    monkeypatch.setattr(manager, "_create_batches", lambda actions: [actions])
    monkeypatch.setattr(manager, "_sync_batch", _boom)
    manager._perform_sync([object()])


def _endpoint(monkeypatch):
    monkeypatch.setattr(pwa_sync, "SyncManager", _boom)
    request = RequestFactory().post(
        "/sync/", data=json.dumps({"actions": []}), content_type="application/json"
    )
    request.user = type("U", (AnonymousUser,), {"is_authenticated": True})()
    response = pwa_sync.sync_endpoint_view(request)
    assert response.status_code == 500
    assert SENTINEL not in response.content.decode()


SITES = {
    "jit-queryset": _jit_queryset,
    "jit-model": _jit_model,
    "custom-resolver": _resolver,
    "batch-sync": _batch,
    "sync-endpoint": _endpoint,
}


@pytest.mark.parametrize("where", ["no-turn", "legacy", "explicit"])
@pytest.mark.parametrize("site", sorted(SITES))
def test_ownerless_log_sites_follow_the_turn(monkeypatch, caplog, site, where):
    with caplog.at_level(logging.DEBUG), _turn(where):
        SITES[site](monkeypatch)
    if where == "explicit":
        assert SENTINEL not in caplog.text
        assert "Protected view operation failed" in caplog.text
    else:
        # Unchanged output outside a nonlegacy turn; also proves the catch ran.
        assert SENTINEL in caplog.text
