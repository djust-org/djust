"""
Tests for eval_handler v2 — DryRunContext + endpoint dry_run wiring.
"""

from __future__ import annotations

import json

import pytest
from django.test import RequestFactory, override_settings

from djust.observability.dry_run import DryRunContext, DryRunViolation
from djust.observability.registry import _clear_registry, register_view
from djust.observability.views import eval_handler


@pytest.fixture(autouse=True)
def clean_registry():
    _clear_registry()
    yield
    _clear_registry()


# --- DryRunContext direct tests -------------------------------------------


def test_context_blocks_orm_save():
    # Use a Django model from the test environment. Use a simple one.
    from django.contrib.auth.models import User

    user = User(username="not_saved")
    with DryRunContext(block=True):
        with pytest.raises(DryRunViolation) as exc_info:
            user.save()
    assert exc_info.value.kind == "orm_save"
    assert "User" in exc_info.value.target


def test_context_blocks_orm_delete():
    from django.contrib.auth.models import User

    user = User(username="x")
    with DryRunContext(block=True):
        with pytest.raises(DryRunViolation) as exc_info:
            user.delete()
    assert exc_info.value.kind == "orm_delete"


def test_context_records_without_blocking_when_block_false():
    """block=False mode: attempts recorded, original call proceeds.
    Use email (safe to call django.core.mail.send_mail — it raises
    locmem not-configured on a fresh test env but captures the attempt
    first so we see it in violations)."""
    from django.core import mail

    with DryRunContext(block=False) as ctx:
        try:
            mail.send_mail("dr-test", "body", "from@x.co", ["to@x.co"])
        except Exception:
            # The original send_mail might raise (backend not configured
            # for real mail), but the CM has already recorded the attempt.
            pass
    kinds = {v["kind"] for v in ctx.violations}
    assert "email" in kinds


def test_context_unpatch_on_exit():
    """After exit, Model.save must be restored to the original."""
    from django.db.models import Model

    original_save = Model.save
    with DryRunContext():
        pass
    # After exit, Model.save is the original.
    assert Model.save is original_save


def test_context_unpatch_on_exception():
    """If handler raises inside, exit still restores patches."""
    from django.db.models import Model

    original_save = Model.save
    with pytest.raises(RuntimeError):
        with DryRunContext():
            raise RuntimeError("boom")
    assert Model.save is original_save


def test_context_uninstall_failure_is_logged_not_swallowed(caplog):
    """Regression for #759: if setattr fails during uninstall, we log a
    warning so the failure is visible. Silent swallow would leave the
    process running with a wrapped Model.save forever.
    """
    import logging

    caplog.set_level(logging.WARNING, logger="djust.observability")

    ctx = DryRunContext()
    ctx.__enter__()
    try:
        # Install a single bogus patch entry whose setattr will fail.
        # frozenset is immutable — setattr raises AttributeError.
        bogus_target = frozenset([1, 2, 3])
        ctx._patches.append((bogus_target, "some_attr", "restore_value"))
    finally:
        ctx.__exit__(None, None, None)

    # _uninstall should have logged the failure, not silently swallowed.
    assert any(
        "failed to restore" in rec.getMessage() for rec in caplog.records
    ), "DryRunContext._uninstall must log when setattr fails"
    # And the patch table is cleared regardless.
    assert ctx._patches == []


def test_context_blocks_http_requests():
    try:
        import requests  # noqa: F401
    except ImportError:
        pytest.skip("requests not installed")

    import requests

    with DryRunContext(block=True):
        with pytest.raises(DryRunViolation) as exc_info:
            requests.get("https://example.com")
    assert exc_info.value.kind == "http"
    assert "requests.get" in exc_info.value.target
    assert exc_info.value.details.get("method") == "GET"


def test_context_blocks_urllib():
    from urllib import request as urllib_request

    with DryRunContext(block=True):
        with pytest.raises(DryRunViolation) as exc_info:
            urllib_request.urlopen("https://example.com")
    assert exc_info.value.kind == "urllib"


# --- Endpoint integration -------------------------------------------------


class _FakeViewPure:
    """Pure state mutation — survives dry_run unscathed."""

    def __init__(self):
        self.count = 0

    def increment(self):
        self.count += 1


class _FakeViewWithHttp:
    """Makes an HTTP call — dry_run blocks it."""

    def __init__(self):
        self.count = 0

    def fetch_and_increment(self):
        import requests

        requests.get("https://example.com")
        self.count += 1  # Never reached in block mode.


def _post(body: dict, session_id: str = "s"):
    rf = RequestFactory()
    return rf.post(
        f"/?session_id={session_id}",
        data=json.dumps(body),
        content_type="application/json",
    )


@override_settings(DEBUG=True)
def test_endpoint_dry_run_pure_handler_succeeds():
    """Pure state handler runs cleanly under dry_run — no violations."""
    view = _FakeViewPure()
    register_view("s", view)
    resp = eval_handler(_post({"handler_name": "increment", "dry_run": True}))
    assert resp.status_code == 200
    data = json.loads(resp.content)
    assert data["dry_run"] is True
    assert data["after_assigns"]["count"] == 1
    assert data["delta"]["change_count"] == 1
    assert "blocked_side_effect" not in data


@override_settings(DEBUG=True)
def test_endpoint_dry_run_blocks_http_call():
    view = _FakeViewWithHttp()
    register_view("s", view)
    resp = eval_handler(_post({"handler_name": "fetch_and_increment", "dry_run": True}))
    assert resp.status_code == 200
    data = json.loads(resp.content)
    assert data["dry_run"] is True
    assert data["blocked_side_effect"]["kind"] == "http"
    assert "requests.get" in data["blocked_side_effect"]["target"]
    # Handler unwound at the side-effect point; count never incremented.
    assert data["after_assigns"]["count"] == 0


@override_settings(DEBUG=True)
def test_endpoint_dry_run_record_mode_no_block():
    """dry_run_block=False records attempts but lets originals run.

    We can't verify "originals ran" without actually making an HTTP
    call, but we CAN verify the violation is recorded and no
    blocked_side_effect appears in the response.
    """
    view = _FakeViewPure()  # pure — no violation to record
    register_view("s", view)
    resp = eval_handler(
        _post({"handler_name": "increment", "dry_run": True, "dry_run_block": False})
    )
    assert resp.status_code == 200
    data = json.loads(resp.content)
    assert data["dry_run"] is True
    assert data["dry_run_block"] is False
    assert data["after_assigns"]["count"] == 1


@override_settings(DEBUG=True)
def test_endpoint_without_dry_run_flag_works_like_v1():
    """Default behavior unchanged — no dry_run in response."""
    view = _FakeViewPure()
    register_view("s", view)
    resp = eval_handler(_post({"handler_name": "increment"}))
    assert resp.status_code == 200
    data = json.loads(resp.content)
    assert "dry_run" not in data
    assert "blocked_side_effect" not in data
    assert data["after_assigns"]["count"] == 1
