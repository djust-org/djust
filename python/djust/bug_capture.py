"""Sharable bug-capture artifacts for djust LiveViews (B7 iter A).

A :class:`BugCapture` is the minimum payload a developer needs to share
a reproducible report of a broken djust transition: the public state
*before* the broken event, the public state *after*, and the VDOM
patches the framework generated in between. Recipient pastes the
encoded blob into a viewer (iter B) and sees exactly what the framework
did with what state.

Iter A ships the data shape + encode/decode round-trip. Iter B (tracked
separately) adds a read-only replay view at ``/__djust__/replay/<blob>``.
Iter C (tracked separately) adds a Redis store for payloads too large
to fit inline, the ``djust replay`` CLI, and a ``time_travel_excluded_fields``
class attribute for PII scrubbing at the framework level.

Security model (READ THIS BEFORE USING)
---------------------------------------

**Captured state may contain user PII.** ``state_before`` /
``state_after`` are the view's *public* state at the moment of an
event — that includes whatever the developer assigned to public
attributes: form values, model field contents, user IDs, search
queries, multi-tenant context, etc. The encoded blob is the same data,
URL-safely transcoded. Treat an encoded :class:`BugCapture` URL as
sensitive data: do not paste it into shared bug trackers, Slack
channels, or email without reviewing what's inside.

**The encoded blob is NOT authenticated.** Anyone can construct a
syntactically-valid ``djbug1.<base64>`` payload. Consumers that decode
a :class:`BugCapture` and render it MUST treat the resulting state as
untrusted input — escape on render, do not dispatch handlers against
it, do not let it cross multi-tenant boundaries.

**Default-off in production.** :func:`BugCapture.encode` and
:func:`encode_view_state` raise when ``settings.DEBUG`` is falsy unless
the deployer explicitly opts in via
``DJUST_BUG_CAPTURE_PROD_OPT_IN = True``. The opt-in is deliberately
ugly so deployers think about whether they actually want users
exporting captured state from prod.

**Always use the** ``scrub`` **hook for known-sensitive fields.** Pass
a callable to :meth:`BugCapture.encode` that returns a redacted
``BugCapture``. The encoded artifact records the names of fields that
were scrubbed (not the values) so reviewers can verify what was
removed before sharing further.

Wire format
-----------

::

    djbug1.<base64-urlsafe of compact-JSON>

The ``djbug1.`` prefix is versioned; the decoder rejects unknown
versions. The JSON shape is::

    {
        "v": "djbug1",
        "event_name": str,
        "state_before": object,
        "state_after": object,
        "vdom_patches": list,
        "scrubbed_fields": list[str],
    }
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

#: Wire format identifier. Bump when changing the JSON shape on the wire.
WIRE_VERSION = "djbug1"


@dataclass
class BugCapture:
    """A minimal, sharable record of a broken djust event transition.

    Attributes:
        state_before: The view's public state at the moment the event
            handler started. Coerced to a JSON-safe dict on encode.
        state_after: The view's public state at the moment the event
            handler returned (or raised).
        vdom_patches: The VDOM patches the framework generated for this
            event, as a list of dicts (already JSON-decoded from the
            wire-format string ``render_with_diff`` returns).
        event_name: The handler name that produced this transition —
            context for the reviewer; optional but recommended.
        scrubbed_fields: Names of fields the ``scrub`` callable removed
            from state during encoding. The names are wire-visible (so
            reviewers know what was held back); the values are not.
    """

    state_before: Dict[str, Any]
    state_after: Dict[str, Any]
    vdom_patches: List[Dict[str, Any]]
    event_name: str = ""
    scrubbed_fields: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Return the JSON-safe wire-shape dict (without the version envelope)."""
        return {
            "event_name": self.event_name,
            "state_before": self.state_before,
            "state_after": self.state_after,
            "vdom_patches": self.vdom_patches,
            "scrubbed_fields": list(self.scrubbed_fields),
        }

    def encode(
        self,
        scrub: Optional[Callable[["BugCapture"], "BugCapture"]] = None,
    ) -> str:
        """Encode this capture into a sharable URL fragment.

        Args:
            scrub: Optional callable that receives this :class:`BugCapture`
                and returns a redacted copy. The callable is responsible
                for removing PII from ``state_before`` / ``state_after``
                and recording the removed field names on
                ``scrubbed_fields``. See :func:`scrub_fields` for a
                ready-made implementation.

        Returns:
            A URL-safe string of the form ``djbug1.<base64url>``.

        Raises:
            RuntimeError: when ``settings.DEBUG`` is falsy and
                ``DJUST_BUG_CAPTURE_PROD_OPT_IN`` is not explicitly set
                to ``True``. The default-off-in-prod gate is deliberate —
                see the module docstring.
            TypeError: when the capture (post-scrub) contains values that
                are not JSON-serializable.
        """
        _enforce_prod_gate()

        capture = scrub(self) if scrub is not None else self
        wire = {"v": WIRE_VERSION, **capture.to_dict()}
        # Compact JSON: no whitespace, sorted keys for byte-stable output.
        payload = json.dumps(wire, separators=(",", ":"), sort_keys=True).encode("utf-8")
        b64 = base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")
        return f"{WIRE_VERSION}.{b64}"

    @classmethod
    def decode(cls, blob: str) -> "BugCapture":
        """Decode a ``djbug1.<base64>`` blob back into a :class:`BugCapture`.

        Defensive against untrusted input: an attacker-supplied blob can
        produce any of the documented :class:`ValueError` shapes below,
        but never an uncaught exception or a partially-constructed
        object.

        Args:
            blob: The encoded string produced by :meth:`encode`.

        Raises:
            ValueError: on non-string input, unknown version prefix,
                malformed base64, malformed JSON, version-envelope
                mismatch, or missing required fields.
        """
        if not isinstance(blob, str):
            raise ValueError("encoded BugCapture must be a string, got %s" % type(blob).__name__)

        parts = blob.split(".", 1)
        if len(parts) != 2:
            raise ValueError("malformed BugCapture blob: missing version prefix")
        version, b64 = parts
        if version != WIRE_VERSION:
            raise ValueError(
                "unsupported BugCapture version %r (this build understands %r)"
                % (version, WIRE_VERSION)
            )

        pad = "=" * ((4 - len(b64) % 4) % 4)
        # validate=True rejects bytes outside the (urlsafe) base64 alphabet.
        # Without it, b64decode silently strips non-alphabet chars and
        # the failure surfaces as a UnicodeDecodeError downstream — a
        # less actionable error for the recipient of an URL.
        # urlsafe_b64decode doesn't accept validate=, so we call b64decode
        # with altchars="-_" to spell out the same urlsafe alphabet.
        try:
            raw = base64.b64decode((b64 + pad).encode("ascii"), altchars=b"-_", validate=True)
        except (ValueError, TypeError, UnicodeEncodeError) as exc:
            raise ValueError("malformed base64 in BugCapture blob: %s" % exc) from exc

        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("malformed JSON in BugCapture blob: %s" % exc) from exc

        if not isinstance(data, dict):
            raise ValueError("BugCapture wire payload must be a JSON object")
        if data.get("v") != WIRE_VERSION:
            raise ValueError(
                "BugCapture inner version mismatch: got %r, expected %r"
                % (data.get("v"), WIRE_VERSION)
            )

        required = {"state_before", "state_after", "vdom_patches"}
        missing = required - set(data.keys())
        if missing:
            raise ValueError("BugCapture missing required fields: %s" % sorted(missing))

        for key, expected_type, type_name in (
            ("state_before", dict, "object"),
            ("state_after", dict, "object"),
            ("vdom_patches", list, "array"),
        ):
            if not isinstance(data[key], expected_type):
                raise ValueError(
                    "BugCapture field %r must be a JSON %s, got %s"
                    % (key, type_name, type(data[key]).__name__)
                )

        return cls(
            state_before=data["state_before"],
            state_after=data["state_after"],
            vdom_patches=data["vdom_patches"],
            event_name=data.get("event_name", "") or "",
            scrubbed_fields=list(data.get("scrubbed_fields") or []),
        )


def encode_view_state(
    view: Any,
    event_name: str = "",
    scrub: Optional[Callable[[BugCapture], BugCapture]] = None,
) -> str:
    """Build a :class:`BugCapture` from the latest event on *view* and encode it.

    Reads the most recent :class:`~djust.time_travel.EventSnapshot` from
    the view's time-travel buffer (the source of ``state_before`` /
    ``state_after``) and the view's most recent VDOM patch list (the
    source of ``vdom_patches``). Raises if either is unavailable.

    Args:
        view: A :class:`~djust.live_view.LiveView` instance with
            ``time_travel_enabled = True`` and at least one event
            captured.
        event_name: If provided, locates the most recent snapshot for
            that event name. Default is the latest snapshot regardless
            of event.
        scrub: Forwarded to :meth:`BugCapture.encode`.

    Raises:
        RuntimeError: see :meth:`BugCapture.encode`.
        ValueError: if the view has no time-travel buffer, no recorded
            events, or no recent VDOM patches.
    """
    buffer = getattr(view, "_time_travel_buffer", None)
    if buffer is None or len(buffer) == 0:
        raise ValueError(
            "view has no time-travel buffer or no recorded events; "
            "set `time_travel_enabled = True` on the LiveView and "
            "dispatch at least one event before capturing"
        )

    snapshot = None
    history = list(buffer.history())
    if event_name:
        for entry in reversed(history):
            if entry.get("event_name") == event_name:
                snapshot = entry
                break
        if snapshot is None:
            raise ValueError("no recorded event named %r in time-travel buffer" % event_name)
    else:
        snapshot = history[-1]

    patches = _extract_last_patches(view)
    capture = BugCapture(
        state_before=snapshot["state_before"],
        state_after=snapshot["state_after"],
        vdom_patches=patches,
        event_name=snapshot.get("event_name", "") or "",
    )
    return capture.encode(scrub=scrub)


def scrub_fields(*field_names: str) -> Callable[[BugCapture], BugCapture]:
    """Return a scrub callable that removes *field_names* from state.

    Usage::

        encoded = capture.encode(scrub=scrub_fields("password", "ssn"))

    The returned callable removes each named field from both
    ``state_before`` and ``state_after`` (if present) and appends the
    field name to ``scrubbed_fields`` so reviewers can verify what was
    held back. Field names not present in state are silently ignored
    (so the same scrub callable can be reused across views with
    different shapes).
    """
    fields = tuple(field_names)

    def _scrub(capture: BugCapture) -> BugCapture:
        scrubbed_before = dict(capture.state_before)
        scrubbed_after = dict(capture.state_after)
        actually_scrubbed: List[str] = list(capture.scrubbed_fields)
        for name in fields:
            removed = False
            if name in scrubbed_before:
                del scrubbed_before[name]
                removed = True
            if name in scrubbed_after:
                del scrubbed_after[name]
                removed = True
            if removed and name not in actually_scrubbed:
                actually_scrubbed.append(name)
        return BugCapture(
            state_before=scrubbed_before,
            state_after=scrubbed_after,
            vdom_patches=capture.vdom_patches,
            event_name=capture.event_name,
            scrubbed_fields=actually_scrubbed,
        )

    return _scrub


def _enforce_prod_gate() -> None:
    """Raise if encoding is attempted in production without the explicit opt-in."""
    try:
        from django.conf import settings
    except Exception:  # pragma: no cover — Django always available in djust
        return
    if getattr(settings, "DEBUG", False):
        return
    if getattr(settings, "DJUST_BUG_CAPTURE_PROD_OPT_IN", False) is True:
        return
    raise RuntimeError(
        "BugCapture.encode is disabled in production (DEBUG=False). "
        "Captured snapshots may contain user PII; sharing them from "
        "production is a deliberate decision. To enable, set "
        "DJUST_BUG_CAPTURE_PROD_OPT_IN = True in settings and ensure "
        "a `scrub` callable is passed to remove sensitive fields."
    )


def _extract_last_patches(view: Any) -> List[Dict[str, Any]]:
    """Pull the most recent VDOM patches from *view* as a list of dicts.

    djust's ``render_with_diff()`` returns patches as a JSON string. We
    look for the most recent rendered patch list on conventional view
    attributes; this list is what iter A captures. Raises
    :class:`ValueError` if no patches are available.
    """
    # The framework keeps the last rendered patches on the view via the
    # standard render path. Looked up in priority order so test/mock
    # views can pre-populate any one of these.
    for attr in ("_last_vdom_patches", "_last_patches"):
        raw = getattr(view, attr, None)
        if raw is None:
            continue
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError("view.%s is not valid JSON: %s" % (attr, exc)) from exc
            if not isinstance(parsed, list):
                raise ValueError(
                    "view.%s decoded to %s, expected JSON array" % (attr, type(parsed).__name__)
                )
            return parsed
        if isinstance(raw, list):
            return list(raw)
        raise ValueError(
            "view.%s must be a JSON string or list, got %s" % (attr, type(raw).__name__)
        )
    raise ValueError(
        "view has no recent VDOM patches recorded; ensure at least one "
        "event has rendered through render_with_diff() before capturing"
    )


__all__ = [
    "WIRE_VERSION",
    "BugCapture",
    "encode_view_state",
    "scrub_fields",
]
