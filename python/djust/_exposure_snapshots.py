"""Bounded signed snapshots for ADR-038's staged explicit policy.

Signatures protect integrity, not confidentiality. Only persist="client" fields
may enter this independently salted envelope. Transport callers must establish
fresh authorization before applying restored values.
https://docs.djangoproject.com/en/5.2/topics/signing/
"""

import json
import logging
import time
from typing import Any

from django.core import signing

from ._exposure import ExposureContract, ExposureError, clone_json_state
from ._exposure_sessions import StateBinding, request_binding

logger = logging.getLogger(__name__)

_SALT = "djust.explicit.client-snapshot.v1"

#: Default lifetime of a signed client snapshot, in seconds.
DEFAULT_MAX_AGE = 3600
#: The longest lifetime :class:`ClientSnapshot` accepts.
MAX_MAX_AGE = 86400

_reported_max_ages: set[str] = set()


def get_max_age() -> int:
    """The snapshot lifetime in seconds, from ``DJUST_STATE_SNAPSHOT_MAX_AGE``.

    The one reading of the setting: the codec that signs and verifies the
    token and the service-worker metadata that tells the browser how long to
    keep it use this value, so they cannot disagree. A whole number of
    seconds from 1 to 86400 (an ``int``, or a string of digits as an
    environment variable supplies) is used as given. A larger value is capped
    at 86400; anything else falls back to :data:`DEFAULT_MAX_AGE`. Each
    correction is logged once per value, and never raises: a misconfigured
    lifetime must not break a mount.
    """
    from django.conf import settings

    raw = getattr(settings, "DJUST_STATE_SNAPSHOT_MAX_AGE", DEFAULT_MAX_AGE)
    value: int | None = None
    if type(raw) is int:
        value = raw
    elif type(raw) is str and raw.strip().isascii() and raw.strip().isdigit():
        value = int(raw.strip())
    if value is not None and 0 < value <= MAX_MAX_AGE:
        return value
    used = MAX_MAX_AGE if value is not None and value > MAX_MAX_AGE else DEFAULT_MAX_AGE
    shown = repr(raw)[:80]
    if shown not in _reported_max_ages:
        _reported_max_ages.add(shown)
        logger.warning(
            "DJUST_STATE_SNAPSHOT_MAX_AGE must be a whole number of seconds from 1 to %d "
            "(got %s); using %d",
            MAX_MAX_AGE,
            shown,
            used,
        )
    return used


def snapshot_codec(view: Any, request: Any) -> "ClientSnapshot | None":
    """Build a codec from declarations and an authorized transport request."""
    policy = getattr(view, "exposure_policy", None)
    if type(policy) is not str or policy != "explicit":
        raise ExposureError("Client snapshot requires explicit exposure policy")
    contract = ExposureContract.from_view_class(type(view))
    if not any(field.persist == "client" for field in contract.fields.values()):
        return None
    # Force lazy session loading before binding; a deleted handle cannot be
    # replaced by a shared empty anonymous-session binding.
    request.session.get("_auth_user_id")
    return ClientSnapshot(
        contract,
        request_binding(request),
        max_age=get_max_age(),
    )


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ExposureError("Duplicate snapshot key")
        result[key] = value
    return result


class ClientSnapshot:
    """Authenticate a client-only projection without restoring a view itself."""

    def __init__(
        self, contract: ExposureContract, binding: StateBinding, *, max_age: int = 3600
    ) -> None:
        if type(contract) is not ExposureContract or type(binding) is not StateBinding:
            raise ExposureError("Invalid client snapshot contract or binding")
        if type(max_age) is not int or not 0 < max_age <= 86400:
            raise ExposureError("Invalid client snapshot lifetime")
        self.contract = contract
        self.binding = binding
        self.max_age = max_age

    def capture(self, view: Any) -> str:
        """Sign declared snapshot values; never stringify unsupported objects."""
        try:
            values = self.contract.project_view(view, "snapshot")
            envelope = {
                "version": 1,
                "binding": self.binding.digest,
                "created": int(time.time()),
                "state": self.contract.capture(values, "snapshot"),
            }
            bounded = clone_json_state(envelope, limits=self.contract.limits)
            payload = json.dumps(bounded, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            token = signing.TimestampSigner(salt=_SALT).sign(payload)
            if len(token.encode("utf-8")) > self.contract.limits.max_bytes:
                raise ExposureError("Client snapshot exceeds wire budget")
            return token
        except Exception:
            # Factories and custom values can carry secrets in exception text.
            # Do not allow a caller's debug logger to expose those values.
            raise ExposureError("Client snapshot capture unavailable") from None

    def restore(self, token: Any) -> dict[str, Any] | None:
        """Return detached validated values, or None to request a fresh mount."""
        if type(token) is not str or len(token) > self.contract.limits.max_bytes:
            return None
        try:
            if len(token.encode("utf-8")) > self.contract.limits.max_bytes:
                return None
            payload = signing.TimestampSigner(salt=_SALT).unsign(token, max_age=self.max_age)
            raw = json.loads(payload, object_pairs_hook=_unique_object)
            envelope = clone_json_state(raw, limits=self.contract.limits)
            if type(envelope) is not dict or set(envelope) != {
                "version",
                "binding",
                "created",
                "state",
            }:
                return None
            if type(envelope["version"]) is not int or envelope["version"] != 1:
                return None
            if type(envelope["binding"]) is not str or envelope["binding"] != self.binding.digest:
                return None
            created = envelope["created"]
            now = int(time.time())
            if type(created) is not int or created < 0 or not 0 <= now - created < self.max_age:
                return None
            return self.contract.prepare_restore(envelope["state"], "snapshot")
        except (signing.BadSignature, ValueError, TypeError, RecursionError, OverflowError):
            return None
