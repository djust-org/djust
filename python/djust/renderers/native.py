"""NativeRenderer — emits widget-shaped VNodes for native clients.

LVN-II POC (djust-org/djust#1578 follow-up): the structural scaffold
from LVN-II PR-2 now has a working render path. Renders the resolved
``.swiftui.html`` / ``.compose.html`` template variant through Django,
parses the result with the stdlib HTML parser into a VNode tree, and
emits a single ``Replace`` patch (no diffing — full re-render per
tick is fine for POC; real diffing reuses ``djust_vdom`` later).

The output format is JSON (matches djust's WS default
``use_binary = False`` at ``websocket.py:393``). Wire shape matches
``HtmlRenderer``'s `(html, patches_json, version)` triple so the
transport layer is unchanged.

Why the stdlib HTMLParser and not ``crates/djust_vdom``: the Rust
parser is HTML-tag-aware in subtle ways (`<br/>` self-close, attr
escaping). For a POC that emits widget tags (`<Stack>`, `<Text>`),
stdlib is enough and avoids touching the Rust extension. Later
optimization can route the widget templates through the Rust parser
with no client-visible change.
"""

from __future__ import annotations

import json
from html.parser import HTMLParser
from typing import Any, Optional, Tuple

from django.template.loader import render_to_string

__all__ = ["NativeRenderer", "SwiftUIRenderer", "ComposeRenderer"]


# Base62 djust-id generator — matches the format the Rust counter
# produces (``crates/djust_vdom/src/lib.rs:55-114``) just enough for
# the native clients' identity-tracking; the actual integer space is
# different but the alphabet matches so the wire shape is consistent.
_BASE62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


def _to_base62(n: int) -> str:
    if n == 0:
        return "0"
    out = []
    while n:
        n, r = divmod(n, 62)
        out.append(_BASE62[r])
    return "".join(reversed(out))


class _VNodeBuilder(HTMLParser):
    """Build a VNode dict tree from native-template HTML output."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root: Optional[dict] = None
        self.stack: list[dict] = []
        self.id_counter = 0

    def _next_id(self) -> str:
        self.id_counter += 1
        return _to_base62(self.id_counter)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        node = {
            "id": self._next_id(),
            "tag": tag,
            "attrs": {k: (v if v is not None else "") for k, v in attrs},
            "text": "",
            "children": [],
        }
        if self.stack:
            self.stack[-1]["children"].append(node)
        elif self.root is None:
            self.root = node
        self.stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        # Tolerant close: pop until we find a matching tag (handles
        # malformed templates without crashing the render path).
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i]["tag"] == tag:
                del self.stack[i:]
                return

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        # Self-closing tag like <Spacer/> — no children, no need to push.
        node = {
            "id": self._next_id(),
            "tag": tag,
            "attrs": {k: (v if v is not None else "") for k, v in attrs},
            "text": "",
            "children": [],
        }
        if self.stack:
            self.stack[-1]["children"].append(node)
        elif self.root is None:
            self.root = node

    def handle_data(self, data: str) -> None:
        stripped = data.strip()
        if not stripped:
            return
        if self.stack:
            # Append to the current node's text. Multiple text runs get
            # joined by a space — fine for widget templates that don't
            # mix structure and prose like web HTML does.
            cur = self.stack[-1]
            cur["text"] = (cur["text"] + " " + stripped).strip() if cur["text"] else stripped


class NativeRenderer:
    """POC native widget renderer for djust LiveView.

    Conforms to :class:`djust.renderers.Renderer` Protocol. Renders the
    per-platform template variant resolved by :func:`resolve_template`,
    parses the result into a VNode tree, and emits a single ``Replace``
    patch covering the whole tree (no incremental diffing in this
    iteration — added later).

    Subclasses :class:`SwiftUIRenderer` and :class:`ComposeRenderer`
    fix the ``output_format`` per platform and are registered in
    ``djust.renderers.RENDERERS``.
    """

    output_format: str = "native"

    def __init__(self, view: Any) -> None:
        self.view = view
        # Monotonic version per renderer instance. Native clients
        # enforce this in their PatchApplicator (the version-regression
        # gate from djust-native-{ios,android} PR-3).
        self._version = 0

    def resolve_template(self, base: str) -> str:
        from .template_resolver import resolve_variant

        return resolve_variant(base, self.output_format)

    def render_with_diff(
        self,
        request: Any = None,
        extract_liveview_root: bool = False,
        preloaded_context: Optional[dict] = None,
    ) -> Tuple[str, Optional[str], int]:
        """Render the platform variant and emit a Replace-only patch.

        Returns ``(html, patches_json, version)``. The ``html`` field is
        empty for the native path (no HTML payload — the wire is
        patches-only). The ``patches_json`` is a single-element list
        containing a Replace patch with the full VNode tree, JSON-encoded.
        """
        base_template = getattr(self.view, "template_name", None)
        if base_template is None:
            raise RuntimeError(
                "NativeRenderer: view has no template_name. The native "
                "render path requires a Django template (and a per-platform "
                "variant like foo.swiftui.html alongside foo.html). See "
                "docs/native-author-guide.md."
            )

        template_name = self.resolve_template(base_template)

        # Use the LiveView's preloaded_context if the runtime supplied it;
        # otherwise pull it from get_context_data() like the HTML path.
        if preloaded_context is not None:
            context = preloaded_context
        else:
            context = self.view.get_context_data()

        # Hoisted-import call site so tests can patch
        # djust.renderers.native.render_to_string.
        html_text = render_to_string(template_name, context)

        builder = _VNodeBuilder()
        builder.feed(html_text)
        builder.close()

        if builder.root is None:
            raise RuntimeError(
                f"NativeRenderer: template {template_name!r} produced no "
                f"VNode root. Make sure the template starts with a widget "
                f"tag like <Stack>...</Stack>."
            )

        patches = [
            {
                "type": "replace",
                "path": [],
                "djId": None,
                "node": builder.root,
            }
        ]
        patches_json = json.dumps(patches)
        self._version += 1
        return "", patches_json, self._version


class SwiftUIRenderer(NativeRenderer):
    """``?platform=swiftui`` — used by ``djust-org/djust-native-ios``."""

    output_format: str = "swiftui"


class ComposeRenderer(NativeRenderer):
    """``?platform=compose`` — used by ``djust-org/djust-native-android``."""

    output_format: str = "compose"
