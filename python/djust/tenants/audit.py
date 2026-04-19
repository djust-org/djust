from __future__ import annotations

import functools
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from importlib import import_module
from typing import Any, Callable

from django.conf import settings

logger = logging.getLogger("djust.tenants.audit")

_backend_cache: AuditBackend | None = None


@dataclass
class AuditEvent:
    timestamp: float
    event_type: str
    action: str
    tenant_id: str | None = None
    user_id: str | None = None
    resource: str | None = None
    detail: str | None = None
    ip_address: str | None = None
    severity: str = "info"


class AuditBackend(ABC):
    @abstractmethod
    def emit(self, event: AuditEvent) -> None: ...


class LoggingAuditBackend(AuditBackend):
    def emit(self, event: AuditEvent) -> None:
        level_map = {
            "debug": logging.DEBUG,
            "info": logging.INFO,
            "warning": logging.WARNING,
            "error": logging.ERROR,
        }
        level = level_map.get(event.severity, logging.INFO)
        logger.log(
            level,
            "audit %s action=%s tenant=%s user=%s resource=%s",
            event.event_type,
            event.action,
            event.tenant_id,
            event.user_id,
            event.resource,
        )


class DatabaseAuditBackend(AuditBackend):
    def emit(self, event: AuditEvent) -> None:
        from django.apps import apps

        model = apps.get_model("djust_tenants", "AuditLog")
        model.objects.create(
            timestamp=event.timestamp,
            event_type=event.event_type,
            action=event.action,
            tenant_id=event.tenant_id,
            user_id=event.user_id,
            resource=event.resource,
            detail=event.detail,
            ip_address=event.ip_address,
            severity=event.severity,
        )


class CallbackAuditBackend(AuditBackend):
    def __init__(self, callback: Callable | str) -> None:
        if isinstance(callback, str):
            module_path, _, attr = callback.rpartition(".")
            module = import_module(module_path)
            callback = getattr(module, attr)
        self._callback: Callable = callback

    def emit(self, event: AuditEvent) -> None:
        self._callback(event)


def _import_backend(dotted_path: str) -> AuditBackend:
    module_path, _, attr = dotted_path.rpartition(".")
    module = import_module(module_path)
    cls = getattr(module, attr)
    return cls()


def get_audit_backend() -> AuditBackend:
    global _backend_cache
    if _backend_cache is not None:
        return _backend_cache

    conf = getattr(settings, "DJUST_TENANTS", {})
    backend_name = conf.get("AUDIT_BACKEND", "logging")

    if backend_name == "logging":
        _backend_cache = LoggingAuditBackend()
    elif backend_name == "database":
        _backend_cache = DatabaseAuditBackend()
    elif backend_name == "callback":
        callback_path = conf.get("AUDIT_CALLBACK", "")
        if not callback_path:
            raise ValueError(
                "DJUST_TENANTS['AUDIT_CALLBACK'] required when AUDIT_BACKEND='callback'"
            )
        _backend_cache = CallbackAuditBackend(callback_path)
    else:
        _backend_cache = _import_backend(backend_name)

    return _backend_cache


def emit_audit(
    event_type: str,
    *,
    tenant_id: str | None = None,
    user_id: str | None = None,
    action: str = "",
    resource: str | None = None,
    detail: str | None = None,
    ip_address: str | None = None,
    severity: str = "info",
) -> None:
    event = AuditEvent(
        timestamp=time.time(),
        event_type=event_type,
        action=action,
        tenant_id=tenant_id,
        user_id=user_id,
        resource=resource,
        detail=detail,
        ip_address=ip_address,
        severity=severity,
    )
    get_audit_backend().emit(event)


def audit_action(
    action: str = "",
    resource: str | None = None,
    severity: str = "info",
) -> Callable:
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            result = fn(*args, **kwargs)
            emit_audit(
                event_type="action",
                action=action or fn.__name__,
                resource=resource,
                severity=severity,
            )
            return result

        return wrapper

    return decorator
