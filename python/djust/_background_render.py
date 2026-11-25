"""Render-bound public metadata for consumer-owned background updates.

The caller owns the render lock for this entire operation. Cancellation cannot
stop a sync worker: retain that lock until it settles, then discard its baseline.
"""

import asyncio
import json
from dataclasses import dataclass
from typing import Any

from asgiref.sync import sync_to_async
from ._render_operation import settle_render_operation


@dataclass(frozen=True)
class BackgroundRender:
    html: str
    patches: Any
    content: str | None
    send_fields: dict[str, Any]
    #: Nothing changed since the last render, so nothing was rendered and
    #: there is no frame to send (#4123). Only with ``skip_unchanged``.
    unchanged: bool = False


#: The result of a background render that found nothing to render (#4123).
UNCHANGED = BackgroundRender("", None, None, {}, unchanged=True)


def snapshot_delivers_nothing(snapshot_fields: dict[str, Any] | None, serial_before: Any) -> bool:
    """Whether a result frame's snapshot fields tell the page nothing new (#4123).

    A server-originated turn's frame carries the refreshed signed snapshot of
    an explicit root (ADR-038 E3). Withholding that frame is safe only when
    the page already holds what it says: no client-persisted token (a token
    can change while nothing the template reads did) and the same turn serial
    as before the turn's commit (a save that wrote a new state advances it).
    ``serial_before`` is ``turn_serial(view)`` read before the commit.
    """
    if not snapshot_fields:
        return True
    if snapshot_fields.get("state_snapshot_signed") is not None:
        return False
    return snapshot_fields.get("state_serial") == serial_before


def sync_reports_render_needed(view: Any) -> bool:
    """Sync ``view`` to Rust and say whether a render has anything to do (#4123).

    ``_sync_state_to_rust()`` returns False only when no key changed or was
    removed since the previous sync and no render was requested. Rust would
    then render the whole template for an empty patch list, so a background
    result skips the render. The sync's "already synced" mark is cleared then:
    the next ``render_with_diff()`` must sync for itself, not skip its sync.
    Anything but an explicit False (no Rust bridge, an override returning
    None) renders, as before.
    """
    sync = getattr(view, "_sync_state_to_rust", None)
    if sync is None or sync() is not False:
        return True
    view._sync_done_this_cycle = False
    return False


def _discard_baseline(view: Any) -> None:
    # Rust reset discards only the diff baseline, not application assigns.
    rust_view = getattr(view, "_rust_view", None)
    if rust_view is not None:
        rust_view.reset()
    view._force_full_html = True


async def render_background(
    view: Any, runtime: Any, *, skip_unchanged: bool = False
) -> BackgroundRender | None:
    """Capture HTML, fallback content and contracts in one synchronous operation.

    None is a redacted contract-discovery failure, not an application callback
    failure. The caller must not deliver this render or invoke an error callback.
    With ``skip_unchanged``, :data:`UNCHANGED` when the sync found nothing to
    render (#4123): nothing was rendered and no frame is due.
    """
    try:
        return await settle_render_operation(
            sync_to_async(render_background_sync)(view, runtime, skip_unchanged=skip_unchanged)
        )
    except asyncio.CancelledError:
        _discard_baseline(view)
        raise


def render_contract_fields(view: Any, runtime: Any) -> dict[str, Any] | None:
    """``_send_update`` fields carrying the just-rendered tree's public contracts.

    Call in the same synchronous operation as the render. ``{}`` for a
    tree with no handlers; None when discovery fails, since invalid metadata must
    never accompany a DOM frame (the caller withholds or replaces the frame).
    """
    try:
        from ._parameter_metadata import parameter_contract_manifest

        manifest = parameter_contract_manifest(view)
        if manifest is None and not getattr(runtime, "_parameter_contracts_active", False):
            return {}
        path = getattr(runtime, "_parameter_contract_view", None)
        if (
            not isinstance(path, str)
            or not path
            or getattr(runtime, "view_instance", None) is not view
        ):
            raise ValueError("Contract owner unavailable")
        # Detach before returning from the render worker. Only bounded public
        # declarations from parameter_contract_manifest are copied.
        return {
            "parameter_contract_snapshot": json.loads(
                json.dumps({"parameter_contracts": manifest, "parameter_contract_view": path})
            )
        }
    except Exception:  # noqa: BLE001 — reported by withholding the frame
        return None


def direct_render_contract_fields(view: Any, runtime: Any) -> dict[str, Any] | None:
    """``render_contract_fields`` for producers outside the background worker.

    A session that never advertised strict contracts keeps its legacy frame
    shape even if discovery fails, as it did before these producers attached
    snapshots; a strict session withholds or replaces the frame.
    """
    fields = render_contract_fields(view, runtime)
    if fields is None and not getattr(runtime, "_parameter_contracts_active", False):
        return {}
    return fields


def render_background_sync(
    view: Any, runtime: Any, *, skip_unchanged: bool = False
) -> BackgroundRender | None:
    """The synchronous body of :func:`render_background`.

    Runs on the session's worker thread. The offloaded server-push turn
    (#3074) calls it inside the same hop as the push hooks; the caller then
    owns the ``settle_render_operation`` + ``_discard_baseline`` handling.

    ``skip_unchanged`` (async results, #4123): when the sync reports that
    nothing changed since the last render, return :data:`UNCHANGED` without
    rendering. A forced render (``_force_full_html``) always renders.
    """
    from .sync_turn import run_sync_step

    def _sync_and_render() -> Any:
        if skip_unchanged:
            if not sync_reports_render_needed(view):
                return None
        elif hasattr(view, "_sync_state_to_rust"):
            view._sync_state_to_rust()
        return view.render_with_diff()

    # One render step for the pair (#4276): one wrapper entry and one query
    # budget pass; the two methods' own render steps nest in it.
    rendered = run_sync_step(view, "render", _sync_and_render)
    if rendered is None:
        return UNCHANGED
    html, patches, _version = rendered
    fields = render_contract_fields(view, runtime)
    if fields is None:  # invalid metadata must never accompany a DOM frame
        _discard_baseline(view)
        return None
    content = None
    if patches is None:
        content = view._extract_liveview_content(view._strip_comments_and_whitespace(html))
    # Consume a forced render once, including async success/error paths.
    # Failure and cancellation re-arm it when discarding the unseen baseline.
    if getattr(view, "_force_full_html", False):
        view._force_full_html = False
    return BackgroundRender(html, patches, content, fields)
