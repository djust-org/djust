"""Bulk-action progress widget for djust admin.

Provides a LiveView-based progress widget plus a decorator
(``@admin_action_with_progress``) that turns any ``DjustModelAdmin``
action into a background job with a live progress page.

Usage
-----

Define a ModelAdmin action::

    from djust.admin_ext import DjustModelAdmin, site
    from djust.admin_ext.progress import admin_action_with_progress

    @site.register(Article)
    class ArticleAdmin(DjustModelAdmin):
        actions = ["republish_selected"]

        @admin_action_with_progress(description="Republish selected articles")
        def republish_selected(self, request, queryset, progress):
            total = queryset.count()
            for i, article in enumerate(queryset.iterator(), start=1):
                article.publish()
                progress.update(current=i, total=total,
                                message=f"Republished {article.title}")

Known limitation
----------------

The ``_JOBS`` registry is process-local. Under a multi-worker deploy
(Gunicorn/Uvicorn with ``workers>1``) the redirect may land on a
different worker than the one running the background thread, producing
a "Job not found" error on the progress page.  For v0.7.0, run a single
worker (``--workers 1``) or use sticky sessions. The v0.7.1 follow-up
will back ``_JOBS`` with a channel layer so multi-worker deploys work
out of the box.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from functools import wraps
from typing import Callable, List, Optional

from django.core.exceptions import PermissionDenied
from django.http import HttpResponseRedirect
from django.urls import reverse

from djust import LiveView
from djust.decorators import event_handler, state

logger = logging.getLogger(__name__)

# Process-local registry. v0.7.1 will back this with a channel layer for
# multi-worker safety. See module docstring.
#
# LRU-capped OrderedDict so a long-running server that runs many bulk
# actions doesn't grow _JOBS without bound. Oldest entries are evicted
# when the cap is exceeded.
_MAX_JOBS = 500
_MAX_MESSAGE_CHARS = 4096  # per-message cap for Job.message / Job.error
_JOBS: "OrderedDict[str, Job]" = OrderedDict()

# User-facing error message when the action body raises. The raw
# exception text is intentionally NOT surfaced to the browser — it may
# carry sensitive internals (SQL, credentials). The raw trace is logged
# via ``logger.exception`` at ERROR level for operators.
_GENERIC_ERROR_USER_MESSAGE = "Action failed — see server logs for details."


def _truncate(text: str, limit: int = _MAX_MESSAGE_CHARS) -> str:
    """Clamp a string to ``limit`` chars, appending ``...`` if truncated."""
    if text is None:
        return text
    s = str(text)
    if len(s) <= limit:
        return s
    return s[: max(0, limit - 3)] + "..."


def _store_job(job_id: str, job: "Job") -> None:
    """Insert/refresh a job in the LRU registry and evict overflow.

    Called from the decorator. Uses ``move_to_end`` for defensive
    re-insertion (shouldn't happen with uuid4 keys but harmless) and
    trims the dict down to ``_MAX_JOBS`` entries.
    """
    if job_id in _JOBS:
        _JOBS.move_to_end(job_id)
    _JOBS[job_id] = job
    while len(_JOBS) > _MAX_JOBS:
        _JOBS.popitem(last=False)  # FIFO eviction of the oldest


@dataclass
class Job:
    """In-memory state for a single bulk-action run.

    All fields are readable by the ``BulkActionProgressWidget`` LiveView
    polling loop and writable by the decorated action body via
    :meth:`update`.
    """

    job_id: str
    action_label: str
    user_id: Optional[int]
    admin_site_name: str
    redirect_url: str
    current: int = 0
    total: int = 0
    message: str = ""
    log: List[str] = field(default_factory=list)
    done: bool = False
    cancelled: bool = False
    error: Optional[str] = None

    # Private slot holding the raw exception message for server-side
    # diagnostics. ``error`` (public) carries the user-safe message.
    _error_raw: Optional[str] = None

    def update(
        self,
        *,
        current: Optional[int] = None,
        total: Optional[int] = None,
        message: str = "",
    ) -> None:
        """Advance the job's progress state.

        Any non-None argument replaces the corresponding field. A
        non-empty ``message`` is both set as the current message and
        appended to ``log`` (trimmed to the last 50 entries). Messages
        longer than ``_MAX_MESSAGE_CHARS`` are truncated with ``...``
        to bound memory growth from error-happy actions.
        """
        if current is not None:
            self.current = current
        if total is not None:
            self.total = total
        if message:
            safe = _truncate(message)
            self.message = safe
            self.log.append(safe)
            if len(self.log) > 50:
                self.log = self.log[-50:]

    def set_error(
        self,
        exc: BaseException,
        *,
        user_message: Optional[str] = None,
    ) -> None:
        """Record an exception for the progress page.

        ``self.error`` is set to a **generic, user-safe** message so the
        raw exception text (which may include sensitive DB / credential
        details) never reaches the browser. The raw text is retained in
        ``self._error_raw`` for server-side introspection in tests.
        Callers that want a specific user-facing string (e.g.
        ``"Invalid order ID"`` for an expected validation error) can
        pass ``user_message``.
        """
        raw = str(exc) if exc is not None else ""
        self._error_raw = _truncate(raw)
        self.error = _truncate(user_message or _GENERIC_ERROR_USER_MESSAGE)


class BulkActionProgressWidget(LiveView):
    """LiveView that reports progress of a background admin action.

    Mounted at ``/<admin>/djust-progress/<job_id>/``. Authenticated
    staff users only (re-checked in :meth:`mount`). Only the user who
    launched the job can see it — any other user, even a staff user,
    gets a 403.
    """

    template_name = "djust_admin/bulk_progress.html"
    login_required = True

    job_id = state(default="")
    current = state(default=0)
    total = state(default=0)
    message = state(default="")
    log_lines = state(default=[])
    done = state(default=False)
    cancelled = state(default=False)
    error = state(default=None)
    action_label = state(default="")
    redirect_url = state(default="")

    def mount(self, request, job_id: str = "", **kwargs) -> None:
        """Verify auth and attach to the job, starting a polling loop.

        Re-checks ``is_staff`` on top of ``login_required=True`` (the
        mount attribute covers authentication; we also require staff)
        and validates that the authenticated user is the job's owner.
        """
        self.request = request
        if not getattr(request.user, "is_staff", False):
            raise PermissionDenied("Admin-only progress view.")
        self.job_id = job_id
        job = _JOBS.get(job_id)
        if job is None:
            self.error = "Job not found or expired."
            self.done = True
            return
        if job.user_id is not None and job.user_id != getattr(request.user, "pk", None):
            raise PermissionDenied("Not your job.")
        self._refresh_from_job(job)
        self.start_async(self._poll, name="progress_poll")

    def _poll(self) -> None:
        """Background polling loop that pulls updates from ``_JOBS``.

        Sleeps 30 seconds after the job finishes (done / cancelled /
        error) before clearing the job from the registry, giving the
        user a chance to read the final state before it disappears.
        """
        while True:
            job = _JOBS.get(self.job_id)
            if job is None:
                return
            self._refresh_from_job(job)
            if job.done or job.cancelled or job.error:
                time.sleep(30)
                _JOBS.pop(self.job_id, None)
                return
            time.sleep(0.5)

    def _refresh_from_job(self, job: Job) -> None:
        """Copy the job's fields onto self so they render."""
        self.current = job.current
        self.total = job.total
        self.message = job.message
        self.log_lines = list(job.log)
        self.done = job.done
        self.cancelled = job.cancelled
        self.error = job.error
        self.action_label = job.action_label
        self.redirect_url = job.redirect_url

    @event_handler
    def cancel(self, **kwargs) -> None:
        """Cancel the running job — both flags flip atomically.

        Terminal: sets ``cancelled=True`` AND ``done=True`` so the
        polling loop exits on the next tick.
        """
        job = _JOBS.get(self.job_id)
        if job and not job.done:
            job.cancelled = True
            job.done = True
            self._refresh_from_job(job)

    def get_context_data(self, **kwargs):
        """Expose template-friendly fields."""
        percent = 0
        if self.total:
            try:
                percent = int(min(100, max(0, (self.current / self.total) * 100)))
            except (ZeroDivisionError, TypeError):
                percent = 0
        return {
            "job_id": self.job_id,
            "action_label": self.action_label,
            "current": self.current,
            "total": self.total,
            "percent": percent,
            "message": self.message,
            "log_lines": self.log_lines,
            "done": self.done,
            "cancelled": self.cancelled,
            "error": self.error,
            "redirect_url": self.redirect_url,
        }


def admin_action_with_progress(
    description: Optional[str] = None,
    permissions: Optional[List[str]] = None,
) -> Callable:
    """Decorator: turn a ModelAdmin action into a background job + progress page.

    The decorated method is called in a background daemon thread with
    the signature ``(self, request, queryset, progress)`` where
    ``progress`` is a :class:`Job` instance. The method may call
    ``progress.update(current=..., total=..., message=...)`` at any
    time; the progress page polls this state and re-renders.

    On invocation, the wrapped action returns an
    :class:`~django.http.HttpResponseRedirect` to the progress URL.

    Args:
        description: Human-readable short_description for the admin
            dropdown. Defaults to the method name, Title-Cased.
        permissions: Permission strings required to run this action.
            Stored as ``allowed_permissions`` for future integration
            with admin's permission machinery (v0.7.1+).

    Thread safety: the queryset is eagerly pinned to a list of primary
    keys before the thread starts, so late-evaluated filters on the
    request/session cannot affect the background run.
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(self, request, queryset):
            job_id = uuid.uuid4().hex
            admin_site = self.admin_site
            try:
                progress_url = reverse(
                    f"{admin_site.name}:djust_progress",
                    kwargs={"job_id": job_id},
                )
            except Exception:
                logger.debug(
                    "Failed to reverse djust_progress URL for %s", admin_site.name, exc_info=True
                )
                progress_url = f"/{admin_site.name}/djust-progress/{job_id}/"

            try:
                redirect_back = reverse(
                    f"{admin_site.name}:"
                    f"{self.model._meta.app_label}_"
                    f"{self.model._meta.model_name}_changelist",
                )
            except Exception:
                logger.debug("Failed to reverse changelist URL", exc_info=True)
                redirect_back = "/"

            job = Job(
                job_id=job_id,
                action_label=getattr(wrapper, "short_description", func.__name__),
                user_id=getattr(request.user, "pk", None),
                admin_site_name=admin_site.name,
                redirect_url=redirect_back,
            )
            _store_job(job_id, job)

            # Pin queryset to PKs so the thread doesn't re-evaluate the
            # lazy queryset against a stale session / request.
            try:
                pks = list(queryset.values_list("pk", flat=True))
            except Exception:
                logger.debug("Failed to pin queryset PKs", exc_info=True)
                pks = []

            model = self.model
            admin_instance = self

            def _run():
                try:
                    pinned = model._default_manager.filter(pk__in=pks)
                    func(admin_instance, request, pinned, job)
                except Exception as exc:
                    # Full traceback goes to the server log at ERROR
                    # level via logger.exception. The user-facing
                    # ``job.error`` is intentionally generic to avoid
                    # leaking sensitive exception details to the admin
                    # page.
                    logger.exception("admin_action_with_progress: %s failed", func.__name__)
                    job.set_error(exc)
                finally:
                    job.done = True

            threading.Thread(
                target=_run,
                daemon=True,
                name=f"djust-admin-action-{job_id}",
            ).start()
            return HttpResponseRedirect(progress_url)

        wrapper.short_description = description or func.__name__.replace("_", " ").title()
        wrapper.allowed_permissions = permissions or []
        wrapper._djust_admin_action_with_progress = True
        return wrapper

    return decorator


def _make_bulk_action_progress_view():
    """Build ``BulkActionProgressView`` class.

    Done lazily so we don't import ``AdminBaseMixin`` (from views.py) at
    module load time, which would set up a circular import: views.py
    imports from progress.py via sites.py indirectly.
    """
    from .views import AdminBaseMixin

    class BulkActionProgressView(AdminBaseMixin, BulkActionProgressWidget):
        """URL-routed admin view: the progress widget with full admin chrome.

        This is what ``DjustAdminSite.get_urls`` wires to
        ``djust-progress/<job_id>/``. It mixes in ``AdminBaseMixin`` so
        the rendered page has the sidebar, breadcrumbs, and plugin nav
        that the rest of the admin shares, and the mixin's ``as_view``
        installs ``admin_login_required``.
        """

        # Declared as a class attr so ``as_view(_view_registry_id=...)``
        # is accepted by Django's View.as_view signature check.
        _view_registry_id = None

        def mount(self, request, job_id: str = "", **kwargs) -> None:
            # AdminBaseMixin.as_view installs admin_login_required; we
            # still call the widget's mount for is_staff + owner checks.
            BulkActionProgressWidget.mount(self, request, job_id=job_id, **kwargs)

        def get_context_data(self, **kwargs):
            base = BulkActionProgressWidget.get_context_data(self, **kwargs)
            admin_ctx = self.get_admin_context()
            return {
                **admin_ctx,
                **base,
                "title": base.get("action_label") or "Progress",
            }

    return BulkActionProgressView


def __getattr__(name):
    """Lazy-construct ``BulkActionProgressView`` on first access.

    This avoids the circular import (progress.py -> views.py -> sites.py
    -> progress.py) that would happen at module load time.
    """
    if name == "BulkActionProgressView":
        cls = _make_bulk_action_progress_view()
        globals()["BulkActionProgressView"] = cls
        return cls
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "BulkActionProgressWidget",
    "BulkActionProgressView",  # noqa: F822 — lazy-constructed via __getattr__
    "Job",
    "admin_action_with_progress",
]
