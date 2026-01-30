"""
TemplateMixin - Template loading, rendering, and HTML extraction for LiveView.
"""

import json
import logging
import os
import re
import sys
from typing import Optional

from ..utils import get_template_dirs

logger = logging.getLogger(__name__)


class TemplateMixin:
    """Template-related methods: get_template, render, render_full_template, render_with_diff,
    and various HTML extraction/stripping helpers."""

    def get_template(self) -> str:
        """
        Get the Rust template source for this view.

        Supports template inheritance via {% extends %} and {% block %} tags.
        Templates are resolved using Rust template inheritance for performance.

        For templates with inheritance, extracts only [data-djust-root] content
        for VDOM tracking to avoid tracking the entire document.
        """
        if self.template:
            return self.template
        elif self.template_name:
            # Load the raw template source
            from django.template import loader
            from django.conf import settings

            template = loader.get_template(self.template_name)
            template_source = template.template.source

            # Check if template uses {% extends %} - if so, resolve inheritance in Rust
            if "{% extends" in template_source or "{%extends" in template_source:
                # Get template directories from Django settings in the EXACT same order Django searches
                template_dirs = []

                # Step 1: Add DIRS from all TEMPLATES configs
                for template_config in settings.TEMPLATES:
                    if "DIRS" in template_config:
                        template_dirs.extend(template_config["DIRS"])

                # Step 2: Add app template directories (only for DjangoTemplates with APP_DIRS=True)
                for template_config in settings.TEMPLATES:
                    if (
                        template_config["BACKEND"]
                        == "django.template.backends.django.DjangoTemplates"
                    ):
                        if template_config.get("APP_DIRS", False):
                            from django.apps import apps
                            from pathlib import Path

                            for app_config in apps.get_app_configs():
                                templates_dir = Path(app_config.path) / "templates"
                                if templates_dir.exists():
                                    template_dirs.append(str(templates_dir))

                # Convert to strings
                template_dirs_str = [str(d) for d in template_dirs]

                # Get the actual path Django resolved for verification
                django_resolved_path = (
                    template.origin.name
                    if hasattr(template, "origin") and template.origin
                    else None
                )

                # Use Rust template inheritance resolution
                try:
                    from djust._rust import resolve_template_inheritance

                    resolved = resolve_template_inheritance(self.template_name, template_dirs_str)

                    # Verify Rust found the same template as Django
                    if django_resolved_path:
                        rust_would_find = None
                        for template_dir in template_dirs_str:
                            candidate = os.path.join(template_dir, self.template_name)
                            if os.path.exists(candidate):
                                rust_would_find = os.path.abspath(candidate)
                                break

                        if (
                            rust_would_find
                            and os.path.abspath(django_resolved_path) != rust_would_find
                        ):
                            print(
                                f"[WARNING] Template resolution mismatch!\n"
                                f"  Django found: {django_resolved_path}\n"
                                f"  Rust found:   {rust_would_find}\n"
                                f"  Template dirs order: {template_dirs_str[:3]}...",
                                file=sys.stderr,
                            )

                    # Store full template for initial GET rendering
                    self._full_template = resolved

                    # For VDOM tracking, extract liveview-root from the RESOLVED template
                    vdom_template = self._extract_liveview_root_with_wrapper(resolved)

                    # CRITICAL: Strip comments and whitespace from template BEFORE Rust VDOM sees it
                    vdom_template = self._strip_comments_and_whitespace(vdom_template)

                    print(
                        f"[LiveView] Template inheritance resolved ({len(resolved)} chars), extracted liveview-root for VDOM ({len(vdom_template)} chars)",
                        file=sys.stderr,
                    )
                    return vdom_template

                except Exception as e:
                    # Fallback to raw template if Rust resolution fails
                    print(f"[LiveView] Template inheritance resolution failed: {e}")
                    print("[LiveView] Falling back to raw template source")
                    self._full_template = template_source
                    extracted = self._extract_liveview_root_with_wrapper(template_source)
                    extracted = self._strip_comments_and_whitespace(extracted)

                    print(
                        f"[LiveView] Extracted and stripped liveview-root: {len(extracted)} chars (from {len(template_source)} chars)",
                        file=sys.stderr,
                    )
                    return extracted

            # No template inheritance - store full template and extract liveview-root for VDOM
            self._full_template = template_source
            extracted = self._extract_liveview_root_with_wrapper(template_source)
            extracted = self._strip_comments_and_whitespace(extracted)

            print(
                f"[LiveView] No inheritance - extracted and stripped liveview-root: {len(extracted)} chars (from {len(template_source)} chars)",
                file=sys.stderr,
            )
            return extracted
        else:
            raise ValueError("Either template_name or template must be set")

    def render(self, request=None) -> str:
        """
        Render the view to HTML.

        Returns the rendered HTML from the template. For WebSocket updates,
        caller should use _extract_liveview_content() to get innerHTML only.

        After rendering, temporary_assigns and streams are reset to free memory.

        Args:
            request: The request object

        Returns:
            Rendered HTML with embedded handler metadata
        """
        self._initialize_rust_view(request)
        self._sync_state_to_rust()
        html = self._rust_view.render()

        # Post-process to hydrate React components
        html = self._hydrate_react_components(html)

        # Inject handler metadata for client-side decorators
        html = self._inject_handler_metadata(html)

        # Reset temporary assigns and streams to free memory after rendering
        self._reset_temporary_assigns()

        return html

    def _inject_handler_metadata(self, html: str) -> str:
        """
        Inject handler metadata script into HTML.

        Adds a <script> tag that sets window.handlerMetadata with
        decorator metadata for all handlers.
        """
        # Extract metadata
        metadata = self._extract_handler_metadata()

        # Skip injection if no metadata
        if not metadata:
            logger.debug("[LiveView] No handler metadata to inject, skipping script injection")
            return html

        logger.debug(f"[LiveView] Injecting handler metadata script for {len(metadata)} handlers")

        # Build script tag
        script = f"""
<script>
// Handler metadata for client-side decorators
window.handlerMetadata = window.handlerMetadata || {{}};
Object.assign(window.handlerMetadata, {json.dumps(metadata)});
</script>"""

        # Try to inject before </body>
        if "</body>" in html:
            html = html.replace("</body>", f"{script}\n</body>")
            logger.debug("[LiveView] Injected metadata script before </body>")
        elif "</html>" in html:
            html = html.replace("</html>", f"{script}\n</html>")
            logger.debug("[LiveView] Injected metadata script before </html>")
        else:
            html = html + script
            logger.debug("[LiveView] Appended metadata script to end of HTML")

        return html

    def _strip_comments_and_whitespace(self, html: str) -> str:
        """
        Strip HTML comments and normalize whitespace to match Rust VDOM parser behavior.

        IMPORTANT: Preserve whitespace inside <pre> and <code> tags.
        """
        # Remove HTML comments
        html = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)

        # Preserve whitespace inside <pre> and <code> tags
        preserved_blocks = []

        def preserve_block(match):
            preserved_blocks.append(match.group(0))
            return f"__PRESERVED_BLOCK_{len(preserved_blocks) - 1}__"

        html = re.sub(r"<pre[^>]*>.*?</pre>", preserve_block, html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(
            r"<code[^>]*>.*?</code>", preserve_block, html, flags=re.DOTALL | re.IGNORECASE
        )

        # Normalize whitespace
        html = re.sub(r"\s+", " ", html)
        html = re.sub(r">\s+<", "><", html)

        # Restore preserved blocks
        for i, block in enumerate(preserved_blocks):
            html = html.replace(f"__PRESERVED_BLOCK_{i}__", block)

        return html

    def _extract_liveview_content(self, html: str) -> str:
        """
        Extract the inner content of [data-djust-root] from full HTML.

        This ensures the HTML sent over WebSocket matches what the client expects:
        just the content to insert into the existing [data-djust-root] container.
        """
        # Find the opening tag for [data-djust-root]
        opening_match = re.search(r"<div\s+[^>]*data-djust-root[^>]*>", html, re.IGNORECASE)

        if not opening_match:
            return html

        start_pos = opening_match.end()

        # Count nested divs to find the matching closing tag
        depth = 1
        pos = start_pos

        while depth > 0 and pos < len(html):
            open_match = re.search(r"<div\b", html[pos:], re.IGNORECASE)
            close_match = re.search(r"</div>", html[pos:], re.IGNORECASE)

            if close_match is None:
                break

            close_pos = pos + close_match.start()
            open_pos = pos + open_match.start() if open_match else float("inf")

            if open_pos < close_pos:
                depth += 1
                pos = open_pos + 4
            else:
                depth -= 1
                if depth == 0:
                    return html[start_pos:close_pos]
                pos = close_pos + 6

        return html

    def _extract_liveview_root_with_wrapper(self, template: str) -> str:
        """
        Extract the <div data-djust-root>...</div> section from a template (WITH the wrapper div).
        """
        opening_match = re.search(r"<div\s+[^>]*data-djust-root[^>]*>", template, re.IGNORECASE)

        if not opening_match:
            return template

        start_pos = opening_match.start()
        inner_start_pos = opening_match.end()

        depth = 1
        pos = inner_start_pos

        while depth > 0 and pos < len(template):
            open_match = re.search(r"<div\b", template[pos:], re.IGNORECASE)
            close_match = re.search(r"</div>", template[pos:], re.IGNORECASE)

            if close_match is None:
                break

            close_pos = pos + close_match.start()
            open_pos = pos + open_match.start() if open_match else float("inf")

            if open_pos < close_pos:
                depth += 1
                pos = open_pos + 4
            else:
                depth -= 1
                if depth == 0:
                    end_pos = pos + close_match.end()
                    return template[start_pos:end_pos]
                pos = close_pos + 6

        return template

    def _extract_liveview_template_content(self, template: str) -> str:
        """
        Extract the innerHTML of [data-djust-root] from a TEMPLATE (not rendered HTML).
        """
        opening_match = re.search(r"<div\s+[^>]*data-djust-root[^>]*>", template, re.IGNORECASE)

        if not opening_match:
            return template

        start_pos = opening_match.end()

        depth = 1
        pos = start_pos

        while depth > 0 and pos < len(template):
            open_match = re.search(r"<div\b", template[pos:], re.IGNORECASE)
            close_match = re.search(r"</div>", template[pos:], re.IGNORECASE)

            if close_match is None:
                break

            close_pos = pos + close_match.start()
            open_pos = pos + open_match.start() if open_match else float("inf")

            if open_pos < close_pos:
                depth += 1
                pos = open_pos + 4
            else:
                depth -= 1
                if depth == 0:
                    return template[start_pos:close_pos]
                pos = close_pos + 6

        return template

    def _strip_liveview_root_in_html(self, html: str) -> str:
        """
        Strip comments and whitespace from [data-djust-root] div in full HTML page.
        """
        opening_match = re.search(r"<div\s+[^>]*data-djust-root[^>]*>", html, re.IGNORECASE)

        if not opening_match:
            return html

        start_pos = opening_match.start()
        inner_start_pos = opening_match.end()

        depth = 1
        pos = inner_start_pos

        while depth > 0 and pos < len(html):
            open_match = re.search(r"<div\b", html[pos:], re.IGNORECASE)
            close_match = re.search(r"</div>", html[pos:], re.IGNORECASE)

            if close_match is None:
                break

            close_pos = pos + close_match.start()
            open_pos = pos + open_match.start() if open_match else float("inf")

            if open_pos < close_pos:
                depth += 1
                pos = open_pos + 4
            else:
                depth -= 1
                if depth == 0:
                    end_pos = pos + close_match.end()
                    liveview_div = html[start_pos:end_pos]
                    stripped_div = self._strip_comments_and_whitespace(liveview_div)
                    return html[:start_pos] + stripped_div + html[end_pos:]
                pos = close_pos + 6

        return html

    def render_full_template(self, request=None, serialized_context=None) -> str:
        """
        Render the full template including base template inheritance.
        Used for initial GET requests when using template inheritance.

        Args:
            request: HTTP request object
            serialized_context: Optional pre-serialized context dict

        Returns the complete HTML document (DOCTYPE, html, head, body, etc.)
        """
        # Check if we have a full template from template inheritance
        if hasattr(self, "_full_template") and self._full_template:
            from djust._rust import RustLiveView

            template_dirs = get_template_dirs()
            temp_rust = RustLiveView(self._full_template, template_dirs)

            if serialized_context is not None:
                json_compatible_context = serialized_context
            else:
                from ..components.base import Component, LiveComponent

                context = self.get_context_data()
                context = self._apply_context_processors(context, request)

                rendered_context = {}
                for key, value in context.items():
                    if isinstance(value, (Component, LiveComponent)):
                        rendered_context[key] = {"render": str(value.render())}
                    else:
                        rendered_context[key] = value

                from ..serialization import DjangoJSONEncoder

                json_str = json.dumps(rendered_context, cls=DjangoJSONEncoder)
                json_compatible_context = json.loads(json_str)

            temp_rust.update_state(json_compatible_context)
            html = temp_rust.render()

            html = self._hydrate_react_components(html)
            html = self._inject_handler_metadata(html)

            return html
        else:
            return self.render(request)

    def render_with_diff(
        self, request=None, extract_liveview_root=False
    ) -> tuple[str, Optional[str], int]:
        """
        Render the view and compute diff from last render.

        Args:
            extract_liveview_root: If True, extract innerHTML of [data-djust-root]

        Returns:
            Tuple of (html, patches_json, version)
        """
        print(
            f"[LiveView] render_with_diff() called (extract_liveview_root={extract_liveview_root})",
            file=sys.stderr,
        )
        print(f"[LiveView] _rust_view before init: {self._rust_view}", file=sys.stderr)

        self._initialize_rust_view(request)

        # If template is a property (dynamic), update the template
        if hasattr(self.__class__, "template") and isinstance(
            getattr(self.__class__, "template"), property
        ):
            print("[LiveView] template is a property - updating template", file=sys.stderr)
            new_template = self.get_template()
            self._rust_view.update_template(new_template)

        print(f"[LiveView] _rust_view after init: {self._rust_view}", file=sys.stderr)

        self._sync_state_to_rust()

        result = self._rust_view.render_with_diff()
        html, patches_json, version = result

        print(
            f"[LiveView] Rendered HTML length: {len(html)} chars, starts with: {html[:100]}...",
            file=sys.stderr,
        )

        if extract_liveview_root:
            html = self._extract_liveview_content(html)
            print(
                f"[LiveView] Extracted [data-djust-root] content ({len(html)} chars)",
                file=sys.stderr,
            )

        print(
            f"[LiveView] Rust returned: version={version}, patches={'YES' if patches_json else 'NO'}",
            file=sys.stderr,
        )
        if not patches_json:
            print("[LiveView] NO PATCHES GENERATED!", file=sys.stderr)
        else:
            from djust.config import config

            if config.get("debug_vdom", False):
                import json as json_module

                patches_list = json_module.loads(patches_json) if patches_json else []
                print(f"[LiveView] Generated {len(patches_list)} patches:", file=sys.stderr)
                for i, patch in enumerate(patches_list[:5]):
                    patch_type = patch.get("type", "Unknown")
                    path = patch.get("path", [])

                    if patch_type == "SetAttr":
                        print(
                            f"[LiveView]   Patch {i}: {patch_type} '{patch.get('key')}' = '{patch.get('value')}' at path {path}",
                            file=sys.stderr,
                        )
                    elif patch_type == "RemoveAttr":
                        print(
                            f"[LiveView]   Patch {i}: {patch_type} '{patch.get('key')}' at path {path}",
                            file=sys.stderr,
                        )
                    elif patch_type == "SetText":
                        text_preview = patch.get("text", "")[:50]
                        print(
                            f"[LiveView]   Patch {i}: {patch_type} to '{text_preview}' at path {path}",
                            file=sys.stderr,
                        )
                    else:
                        print(f"[LiveView]   Patch {i}: {patch}", file=sys.stderr)

        # Reset temporary assigns and streams to free memory after rendering
        self._reset_temporary_assigns()

        return (html, patches_json, version)
