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


def _discard_baseline(view: Any) -> None:
    # Rust reset discards only the diff baseline, not application assigns.
    rust_view = getattr(view, "_rust_view", None)
    if rust_view is not None:
        rust_view.reset()
    view._force_full_html = True


def render_contract_fields(view: Any, runtime: Any) -> dict[str, Any] | None:
    """``_send_update`` fields carrying the just-rendered tree's public contracts.

    Call in the same synchronous operation as the render. ``{}`` for an
    all-legacy session; None when discovery fails, since invalid metadata must
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


async def render_background(view: Any, runtime: Any) -> BackgroundRender | None:
    """Capture HTML, fallback content and contracts in one synchronous operation.

    None is a redacted contract-discovery failure, not an application callback
    failure. The caller must not deliver this render or invoke an error callback.
    """

    def render() -> BackgroundRender | None:
        if hasattr(view, "_sync_state_to_rust"):
            view._sync_state_to_rust()
        html, patches, _version = view.render_with_diff()
        fields = render_contract_fields(view, runtime)
        if fields is None:
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

    try:
        return await settle_render_operation(sync_to_async(render)())
    except asyncio.CancelledError:
        _discard_baseline(view)
        raise
