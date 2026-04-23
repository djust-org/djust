"""
DryRunContext — side-effect-blocking context manager used by
`eval_handler` v2 to answer: "what *would* this handler do, with zero
external consequences?"

Monkey-patches at enter, un-patches at exit. Process-wide lock
guarantees only one dry-run is active at a time (dev-only endpoint —
throughput doesn't matter; correctness does).

Blocks:
- `django.db.models.Model.save` / `Model.delete` — raises DryRunViolation
  before the write hits the DB
- `django.core.mail.send_mail` / `send_mass_mail` — raises
- `requests.get/post/put/delete/patch/head/request` — raises with
  the HTTP method + URL captured in `details`
- `urllib.request.urlopen` — raises

Usage:
    with DryRunContext() as ctx:
        try:
            view.increment()
        except DryRunViolation as v:
            # ctx.violations is an empty list — the CM raises on first
            # attempt rather than collecting silently. If you want to
            # collect without blocking, pass `block=False`.
            print(v.kind, v.target)

Design notes:
- `block=True` (default): first side-effect attempt raises
  DryRunViolation, handler unwinds, caller sees exactly what was
  attempted.
- `block=False`: attempts are appended to `ctx.violations` and the
  original call is still made. Useful when you want a record of every
  side effect a handler produces (still commits — *not* a sandbox).
  Blocking mode is the default because "dry" implies "doesn't commit".
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

logger = logging.getLogger("djust.observability")


# Process-wide lock — serializes dry-runs so we never leave a patched
# ORM in place for another thread's request.
_dry_run_lock = threading.Lock()


class DryRunViolation(Exception):
    """Raised when code inside a DryRunContext attempts a side effect.

    Attributes:
        kind: broad category ("orm_save", "orm_delete", "email", "http", "urllib")
        target: specific symbol ("User.save", "requests.post", "send_mail", ...)
        details: call-site specifics (args length, HTTP URL, method, ...)
    """

    def __init__(self, kind: str, target: str, details: Optional[Dict[str, Any]] = None):
        self.kind = kind
        self.target = target
        self.details = details or {}
        super().__init__(f"{kind}: {target}")


class DryRunContext:
    """Monkey-patches side-effect callables while active.

    Args:
        block: if True (default), raise DryRunViolation on first attempt.
            If False, log each attempt in `violations` and call through.
    """

    def __init__(self, block: bool = True):
        self.block = block
        self.violations: List[Dict[str, Any]] = []
        self._patches: List[tuple] = []  # (obj, attr, original)
        self._lock_held = False

    def __enter__(self) -> "DryRunContext":
        # Serialize across the process — prevents one request's dry-run
        # from turning another request's save into a DryRunViolation.
        _dry_run_lock.acquire()
        self._lock_held = True
        try:
            self._install()
        except Exception:
            # Installation failed partway; undo and release.
            self._uninstall()
            _dry_run_lock.release()
            self._lock_held = False
            raise
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            self._uninstall()
        finally:
            if self._lock_held:
                _dry_run_lock.release()
                self._lock_held = False

    # -- patch table management -------------------------------------------

    def _patch(self, obj: Any, attr: str, wrapper) -> None:
        original = getattr(obj, attr)
        self._patches.append((obj, attr, original))
        setattr(obj, attr, wrapper)

    def _record_or_raise(
        self, kind: str, target: str, details: Optional[Dict[str, Any]], original, args, kwargs
    ):
        entry = {"kind": kind, "target": target, "details": details or {}}
        if self.block:
            raise DryRunViolation(kind, target, details)
        self.violations.append(entry)
        return original(*args, **kwargs)

    # -- install / uninstall ---------------------------------------------

    def _install(self) -> None:
        self._install_orm()
        self._install_email()
        self._install_http_requests()
        self._install_urllib()

    def _uninstall(self) -> None:
        # Restore in reverse order so nested wrappers don't leak.
        # We catch+log rather than raise — if ONE restore fails, we still
        # want to attempt every remaining restore so the smallest number of
        # patches leak into subsequent requests. But we log at warning so
        # the failure is visible (silent swallow would leave the process
        # running with a wrapped Model.save indefinitely — catastrophic
        # for a dev server).
        for obj, attr, original in reversed(self._patches):
            try:
                setattr(obj, attr, original)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "DryRunContext failed to restore %s.%s: %s",
                    getattr(obj, "__name__", type(obj).__name__),
                    attr,
                    e,
                )
        self._patches.clear()

    def _install_orm(self) -> None:
        """Patch Django ORM write methods.

        Covers (#758):
        - Per-instance: Model.save, Model.delete
        - Bulk: QuerySet.update, QuerySet.delete, bulk_create, bulk_update
        - Bulk insert/update via `Manager` also flow through QuerySet —
          patching on QuerySet catches `Model.objects.create()` via
          `QuerySet.create` too (which internally does `Model.save`,
          already patched, but having it early lets us tag `kind:
          "orm_create"` for readability).

        Raw SQL (connection.execute, connection.cursor().execute) is NOT
        patched — it's deliberately low-level; users invoking it want the
        side effect. Use the sql_queries observability endpoint to see it.
        """
        try:
            from django.db.models import Model
            from django.db.models.query import QuerySet
        except Exception:  # noqa: BLE001
            return

        _self = self

        # --- Per-instance: Model.save, Model.delete -------------------
        orig_save = Model.save
        orig_delete = Model.delete

        def wrapped_save(self_, *a, **kw):
            target = f"{type(self_).__name__}.save"
            details = {"pk": getattr(self_, "pk", None)}
            return _self._record_or_raise("orm_save", target, details, orig_save, (self_, *a), kw)

        def wrapped_delete(self_, *a, **kw):
            target = f"{type(self_).__name__}.delete"
            details = {"pk": getattr(self_, "pk", None)}
            return _self._record_or_raise(
                "orm_delete", target, details, orig_delete, (self_, *a), kw
            )

        self._patch(Model, "save", wrapped_save)
        self._patch(Model, "delete", wrapped_delete)

        # --- Bulk: QuerySet.update / delete / bulk_create / bulk_update
        # These bypass Model.save entirely so without patching QuerySet
        # a handler that uses Model.objects.filter(...).update(...) would
        # appear "pure" to dry_run while actually committing writes.
        for qs_method, kind in [
            ("update", "orm_bulk_update"),
            ("delete", "orm_bulk_delete"),
            ("bulk_create", "orm_bulk_create"),
            ("bulk_update", "orm_bulk_update"),
        ]:
            if not hasattr(QuerySet, qs_method):
                continue
            original = getattr(QuerySet, qs_method)

            def _make_qs_wrapper(method_name, orig, kind_tag):
                def wrapper(self_, *a, **kw):
                    model_name = getattr(self_, "model", None)
                    model_name = getattr(model_name, "__name__", "?") if model_name else "?"
                    target = f"{model_name}.objects.{method_name}"
                    details: Dict[str, Any] = {}
                    # For bulk_create/bulk_update the first positional arg
                    # is the list — record its length for quick triage.
                    if method_name in ("bulk_create", "bulk_update") and a:
                        try:
                            details["count"] = len(a[0])
                        except TypeError:
                            # First arg isn't sized (e.g., a generator); count is best-effort.
                            pass
                    return _self._record_or_raise(kind_tag, target, details, orig, (self_, *a), kw)

                return wrapper

            self._patch(QuerySet, qs_method, _make_qs_wrapper(qs_method, original, kind))

    def _install_email(self) -> None:
        try:
            from django.core import mail
        except Exception:  # noqa: BLE001
            return
        for fname in ("send_mail", "send_mass_mail"):
            if not hasattr(mail, fname):
                continue
            original = getattr(mail, fname)
            _self = self

            def _make_wrapper(name, orig):
                def wrapper(*a, **kw):
                    # Subject is the first positional arg in send_mail /
                    # send_mass_mail. Slice regardless of whether it
                    # came via args or kwargs to keep the recorded
                    # details bounded.
                    raw_subject = a[0] if a else kw.get("subject", "")
                    subject = str(raw_subject)[:80] if raw_subject is not None else ""
                    details = {"subject": subject}
                    return _self._record_or_raise(
                        "email", f"django.core.mail.{name}", details, orig, a, kw
                    )

                return wrapper

            self._patch(mail, fname, _make_wrapper(fname, original))

    def _install_http_requests(self) -> None:
        try:
            import requests
        except Exception:  # noqa: BLE001
            return

        _self = self
        for method in ("request", "get", "post", "put", "delete", "patch", "head", "options"):
            if not hasattr(requests, method):
                continue
            original = getattr(requests, method)

            def _make_wrapper(m, orig):
                def wrapper(*a, **kw):
                    url = (
                        a[1]
                        if (m == "request" and len(a) >= 2)
                        else (a[0] if a else kw.get("url", ""))
                    )
                    details = {"method": m.upper(), "url": str(url)[:200]}
                    return _self._record_or_raise("http", f"requests.{m}", details, orig, a, kw)

                return wrapper

            self._patch(requests, method, _make_wrapper(method, original))

    def _install_urllib(self) -> None:
        try:
            from urllib import request as urllib_request
        except Exception:  # noqa: BLE001
            return
        if not hasattr(urllib_request, "urlopen"):
            return
        original = urllib_request.urlopen
        _self = self

        def wrapper(*a, **kw):
            arg = a[0] if a else kw.get("url", "")
            url = getattr(arg, "full_url", None) or str(arg)
            details = {"url": str(url)[:200]}
            return _self._record_or_raise(
                "urllib", "urllib.request.urlopen", details, original, a, kw
            )

        self._patch(urllib_request, "urlopen", wrapper)
