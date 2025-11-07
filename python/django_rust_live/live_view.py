"""
LiveView base class and decorator for reactive Django views
"""

import json
import asyncio
import hashlib
import sys
from typing import Any, Dict, Optional, Callable
from django.http import HttpResponse, JsonResponse
from django.views import View
from django.template.loader import render_to_string

try:
    from ._rust import RustLiveView
except ImportError:
    RustLiveView = None

# Global cache for RustLiveView instances (keyed by session_id + view_key)
_rust_view_cache = {}


class LiveView(View):
    """
    Base class for reactive LiveView components.

    Usage:
        class CounterView(LiveView):
            template_name = 'counter.html'

            def mount(self, request, **kwargs):
                self.count = 0

            def increment(self):
                self.count += 1

            def decrement(self):
                self.count -= 1
    """

    template_name: Optional[str] = None
    template_string: Optional[str] = None

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._rust_view: Optional[RustLiveView] = None
        self._session_id: Optional[str] = None
        self._cache_key: Optional[str] = None

    def get_template(self) -> str:
        """Get the template source for this view"""
        if self.template_string:
            return self.template_string
        elif self.template_name:
            return render_to_string(self.template_name, {})
        else:
            raise ValueError("Either template_name or template_string must be set")

    def mount(self, request, **kwargs):
        """
        Called when the view is mounted. Override to set initial state.

        Args:
            request: The Django request object
            **kwargs: URL parameters
        """
        pass

    def get_context_data(self, **kwargs) -> Dict[str, Any]:
        """
        Get the context data for rendering. Override to customize context.

        Returns:
            Dictionary of context variables
        """
        import json
        context = {}

        # Add all non-private attributes as context
        for key in dir(self):
            if not key.startswith('_'):
                try:
                    value = getattr(self, key)
                    if not callable(value):
                        # Only include JSON-serializable values
                        try:
                            json.dumps(value)
                            context[key] = value
                        except (TypeError, ValueError):
                            # Skip non-serializable objects (like request, etc)
                            pass
                except (AttributeError, TypeError):
                    # Skip class-only methods and other inaccessible attributes
                    continue

        return context

    def _initialize_rust_view(self, request=None):
        """Initialize the Rust LiveView backend"""
        if self._rust_view is None:
            # Try to get from cache if we have a session
            if request and hasattr(request, 'session'):
                view_key = f'liveview_{request.path}'
                session_key = request.session.session_key
                if not session_key:
                    request.session.create()
                    session_key = request.session.session_key

                self._cache_key = f'{session_key}_{view_key}'

                # Try to get cached RustLiveView
                if self._cache_key in _rust_view_cache:
                    self._rust_view = _rust_view_cache[self._cache_key]
                    return

            # Create new RustLiveView
            template_source = self.get_template()
            self._rust_view = RustLiveView(template_source)

            # Cache it if we have a cache key
            if self._cache_key:
                _rust_view_cache[self._cache_key] = self._rust_view

    def _sync_state_to_rust(self):
        """Sync Python state to Rust backend"""
        if self._rust_view:
            context = self.get_context_data()
            self._rust_view.update_state(context)

    def render(self, request=None) -> str:
        """Render the view to HTML"""
        self._initialize_rust_view(request)
        self._sync_state_to_rust()
        html = self._rust_view.render()
        # Post-process to hydrate React components
        html = self._hydrate_react_components(html)
        return html

    def render_with_diff(self, request=None) -> tuple[str, Optional[str], int]:
        """
        Render the view and compute diff from last render.

        Returns:
            Tuple of (html, patches_json, version)
        """
        self._initialize_rust_view(request)
        self._sync_state_to_rust()
        return self._rust_view.render_with_diff()

    def get(self, request, *args, **kwargs):
        """Handle GET requests - initial page load"""
        # IMPORTANT: mount() must be called first to initialize clean state
        self.mount(request, **kwargs)

        # Debug: Check field_errors state after mount
        field_errors = getattr(self, 'field_errors', None)
        print(f"[LiveView] GET request - field_errors after mount: {field_errors}", file=sys.stderr)

        # Store initial state in session
        view_key = f'liveview_{request.path}'
        request.session[view_key] = self.get_context_data()

        # Clear any cached RustLiveView for this session/view to ensure fresh start
        session_key = request.session.session_key
        if not session_key:
            request.session.create()
            session_key = request.session.session_key
        cache_key = f'{session_key}_{view_key}'

        print(f"[LiveView] GET request - cache_key: {cache_key}, exists: {cache_key in _rust_view_cache}", file=sys.stderr)

        if cache_key in _rust_view_cache:
            print(f"[LiveView] Clearing cached RustLiveView for fresh session", file=sys.stderr)
            del _rust_view_cache[cache_key]
            # Also clear our reference so a new one will be created
            self._rust_view = None
            self._cache_key = None

        # Initialize and render to establish baseline VDOM
        # The render() call will create a new RustLiveView with the initial HTML,
        # establishing the correct baseline VDOM that matches what the browser will have.
        html = self.render(request)

        # Debug: Save the rendered HTML to a file for inspection
        if 'registration' in request.path:
            form_start = html.find('<form')
            if form_start != -1:
                form_end = html.find('</form>', form_start) + 7
                form_html = html[form_start:form_end]
                import tempfile
                with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.html', prefix='registration_form_') as f:
                    f.write(form_html)
                    print(f"[LiveView] Saved form HTML to {f.name}", file=sys.stderr)

        # Inject LiveView client script
        html = self._inject_client_script(html)

        return HttpResponse(html)

    def post(self, request, *args, **kwargs):
        """Handle POST requests - event handling"""
        try:
            data = json.loads(request.body)
            event_name = data.get('event')
            params = data.get('params', {})

            # Restore state from session
            view_key = f'liveview_{request.path}'
            saved_state = request.session.get(view_key, {})

            # Restore state to self (skip read-only properties)
            for key, value in saved_state.items():
                if not key.startswith('_') and not callable(value):
                    try:
                        setattr(self, key, value)
                    except AttributeError:
                        # Skip read-only properties
                        pass

            # Call the event handler
            handler = getattr(self, event_name, None)
            if handler and callable(handler):
                if params:
                    handler(**params)
                else:
                    handler()

            # Save updated state back to session
            request.session[view_key] = self.get_context_data()

            # Render with diff to get patches
            html, patches_json, version = self.render_with_diff(request)

            # Debug: log patch count and version
            import sys
            import json as json_module

            print(f"[LiveView] VDOM version: {version}", file=sys.stderr)

            # Threshold for when to use patches vs full HTML
            PATCH_THRESHOLD = 100

            if patches_json:
                patches = json_module.loads(patches_json)
                patch_count = len(patches)
                print(f"[LiveView] Generated {patch_count} patches", file=sys.stderr)

                # Log ALL patches for debugging
                for i, patch in enumerate(patches):
                    patch_type = patch.get('type', 'Unknown')
                    path = patch.get('path', [])
                    index = patch.get('index', 'N/A')

                    # Highlight patches that might target form children
                    if len(path) >= 6 and path[4] == 2:  # Path suggests form element
                        form_child_idx = path[5]
                        print(f"  [{i}] {patch_type:12} path={path} index={index} <- FORM CHILD {form_child_idx}", file=sys.stderr)
                    else:
                        print(f"  [{i}] {patch_type:12} path={path} index={index}", file=sys.stderr)

                # If patches are reasonable size, send patches with HTML fallback
                # Otherwise send full HTML for efficiency
                if patch_count <= PATCH_THRESHOLD:
                    print(f"[LiveView] Sending patches with HTML fallback ({patch_count} patches)", file=sys.stderr)
                    # Include HTML as fallback in case patches fail on client
                    # Note: We include a flag so client can tell us if it used the fallback
                    return JsonResponse({'patches': patches_json, 'html': html, 'version': version, 'reset_on_fallback': True})
                else:
                    print(f"[LiveView] Too many patches ({patch_count}), sending full HTML and resetting VDOM cache", file=sys.stderr)
                    # Reset VDOM cache since we're sending full HTML
                    # This ensures next patches are calculated from the browser's normalized DOM
                    self._rust_view.reset()
                    return JsonResponse({'html': html, 'version': version})
            else:
                # No changes, just send HTML
                return JsonResponse({'html': html, 'version': version})

        except Exception as e:
            import traceback
            traceback.print_exc()
            return JsonResponse({'error': str(e)}, status=500)

    def _hydrate_react_components(self, html: str) -> str:
        """
        Post-process HTML to hydrate React component placeholders with server-rendered content.

        The Rust renderer creates <div data-react-component="Name" data-react-props='{...}'>children</div>
        We need to call the Python renderer functions and inject their output.
        """
        import re
        from .react import react_components
        import json as json_module

        # Pattern to match React component divs
        pattern = r'<div data-react-component="([^"]+)" data-react-props=\'([^\']+)\'>(.*?)</div>'

        def replace_component(match):
            component_name = match.group(1)
            props_json = match.group(2)
            children = match.group(3)

            # Parse props
            try:
                props = json_module.loads(props_json)
            except json_module.JSONDecodeError:
                props = {}

            # Resolve any Django template variables in props (like {{ client_count }})
            context = self.get_context_data()
            resolved_props = {}
            for key, value in props.items():
                if isinstance(value, str) and '{{' in value and '}}' in value:
                    # Extract variable name from {{ var_name }}
                    var_match = re.search(r'\{\{\s*(\w+)\s*\}\}', value)
                    if var_match:
                        var_name = var_match.group(1)
                        if var_name in context:
                            resolved_props[key] = context[var_name]
                        else:
                            resolved_props[key] = value
                    else:
                        resolved_props[key] = value
                else:
                    resolved_props[key] = value

            # Get the renderer for this component
            renderer = react_components.get(component_name)

            if renderer:
                # Call the server-side renderer with resolved props
                rendered_content = renderer(resolved_props, children)
                # Create updated props JSON for client-side hydration
                resolved_props_json = json_module.dumps(resolved_props).replace('"', '&quot;')
                # Wrap with data attributes for client-side hydration
                return f'<div data-react-component="{component_name}" data-react-props=\'{resolved_props_json}\'>{rendered_content}</div>'
            else:
                # No renderer found, return placeholder
                return match.group(0)

        # Replace all React component placeholders
        html = re.sub(pattern, replace_component, html, flags=re.DOTALL)

        return html

    def _inject_client_script(self, html: str) -> str:
        """Inject the LiveView client JavaScript into the HTML"""
        # For now, use a simple HTTP-based reactive approach
        # TODO: Implement full WebSocket integration
        script = """
        <script>
            // Simple HTTP-based LiveView (fallback until WebSocket integration is complete)

            // Track VDOM version for synchronization
            let clientVdomVersion = null;

            // Client-side React Counter component (vanilla JS implementation)
            function initReactCounters() {
                document.querySelectorAll('[data-react-component="Counter"]').forEach(container => {
                    const propsJson = container.dataset.reactProps;
                    let props = {};
                    try {
                        props = JSON.parse(propsJson.replace(/&quot;/g, '"'));
                    } catch(e) {}

                    let count = props.initialCount || 0;
                    const display = container.querySelector('.counter-display');
                    const minusBtn = container.querySelectorAll('.btn-sm')[0];
                    const plusBtn = container.querySelectorAll('.btn-sm')[1];

                    if (display && minusBtn && plusBtn) {
                        minusBtn.addEventListener('click', () => {
                            count--;
                            display.textContent = count;
                        });
                        plusBtn.addEventListener('click', () => {
                            count++;
                            display.textContent = count;
                        });
                    }
                });
            }

            function bindLiveViewEvents() {
                // Find all interactive elements
                const allElements = document.querySelectorAll('*');
                allElements.forEach(element => {
                    // Handle @click events
                    const clickHandler = element.getAttribute('@click');
                    if (clickHandler && !element.dataset.liveviewClickBound) {
                        element.dataset.liveviewClickBound = 'true';
                        element.addEventListener('click', async (e) => {
                            e.preventDefault();
                            const params = {};
                            if (element.dataset.id) {
                                params.id = element.dataset.id;
                            }
                            await handleEvent(clickHandler, params);
                        });
                    }

                    // Handle @submit events on forms
                    const submitHandler = element.getAttribute('@submit');
                    if (submitHandler && !element.dataset.liveviewSubmitBound) {
                        element.dataset.liveviewSubmitBound = 'true';
                        element.addEventListener('submit', async (e) => {
                            e.preventDefault();
                            const formData = new FormData(e.target);
                            const params = Object.fromEntries(formData.entries());
                            await handleEvent(submitHandler, params);
                            e.target.reset();
                        });
                    }

                    // Handle @change events
                    const changeHandler = element.getAttribute('@change');
                    if (changeHandler && !element.dataset.liveviewChangeBound) {
                        element.dataset.liveviewChangeBound = 'true';
                        element.addEventListener('change', async (e) => {
                            const params = {};
                            // For checkboxes, send the checked state
                            if (e.target.type === 'checkbox') {
                                params.value = e.target.checked;
                            } else {
                                // For other inputs, send the value
                                params.value = e.target.value;
                            }
                            // Include data-field if present (for form field validation)
                            if (e.target.dataset.field) {
                                params.field_name = e.target.dataset.field;
                                console.log('[LiveView] Sending field_name:', params.field_name, 'value:', params.value);
                            }
                            // Include data-id if present (use generic 'id' param name)
                            if (e.target.dataset.id) {
                                params.id = e.target.dataset.id;
                            }
                            await handleEvent(changeHandler, params);
                        });
                    }
                });
            }

            // DOM patching utilities
            // Find the root LiveView container (first content element, skipping <link>, <style>, <script>)
            function getLiveViewRoot() {
                const NON_CONTENT_TAGS = ['LINK', 'STYLE', 'SCRIPT'];
                for (const child of document.body.childNodes) {
                    if (child.nodeType === Node.ELEMENT_NODE && !NON_CONTENT_TAGS.includes(child.tagName)) {
                        return child;
                    }
                }
                return document.body;
            }

            function getNodeByPath(path) {
                // The Rust VDOM root matches getLiveViewRoot() (first content element)
                // Path [0] = first child of root, [0,0] = first child of that, etc.
                let node = getLiveViewRoot();

                // DEBUG: Log root info
                if (path.length > 0) {
                    console.log(`[DEBUG-ROOT] Starting traversal from <${node.tagName}>, path length=${path.length}, path=${JSON.stringify(path)}`);
                }

                // Empty path means the root itself
                if (path.length === 0) {
                    return node;
                }

                // Traverse the path directly - no adjustment needed since roots are aligned
                for (let i = 0; i < path.length; i++) {
                    const index = path[i];

                    // Get children - Rust DOES filter whitespace-only text nodes
                    // The parser.rs code filters with: if !text.trim().is_empty()
                    // So we should do the same here
                    const children = Array.from(node.childNodes).filter(child => {
                        // Keep element nodes
                        if (child.nodeType === Node.ELEMENT_NODE) return true;
                        // Keep text nodes that have non-whitespace content
                        if (child.nodeType === Node.TEXT_NODE) {
                            return child.textContent.trim().length > 0;
                        }
                        return false;
                    });

                    // DEBUG: Log at first few iterations
                    if (i < 3) {
                        console.log(`[DEBUG-PATH] Step ${i}: index=${index}, children=${children.length}, node=<${node.tagName || node.nodeName}>`);
                        if (children.length === 0) {
                            console.log(`[DEBUG-PATH] ZERO CHILDREN! node.childNodes.length=${node.childNodes.length}`);
                            for (let j = 0; j < node.childNodes.length; j++) {
                                const child = node.childNodes[j];
                                console.log(`  Raw child[${j}]: type=${child.nodeType}, tag=${child.tagName || child.nodeName}`);
                            }
                        }
                    }

                    // DEBUG: Log when accessing the form's parent (card-body)
                    if (i === 4 && path[0] === 0 && path[1] === 0 && path[2] === 0 && path[3] === 1 && path[4] === 2 && path.length > 5) {
                        console.log(`[DEBUG] About to descend into child[${index}] of card-body`);
                        console.log(`[DEBUG] card-body has ${children.length} children, about to access index ${index}`);
                        if (index < children.length && children[index].nodeType === Node.ELEMENT_NODE) {
                            const target = children[index];
                            console.log(`[DEBUG] Target is <${target.tagName.toLowerCase()}>`);
                            // Now show what THIS element's children are
                            const targetChildren = Array.from(target.childNodes).filter(child => {
                                if (child.nodeType === Node.ELEMENT_NODE) return true;
                                if (child.nodeType === Node.TEXT_NODE) {
                                    return child.textContent.trim().length > 0;
                                }
                                return false;
                            });
                            console.log(`[DEBUG] This element has ${targetChildren.length} filtered children`);
                            targetChildren.forEach((child, idx) => {
                                if (child.nodeType === Node.ELEMENT_NODE) {
                                    console.log(`  [${idx}] <${child.tagName.toLowerCase()} class="${child.className || ''}">`);
                                }
                            });
                        }
                    }

                    if (index >= children.length) {
                        console.warn(`[LiveView] Index ${index} out of bounds, only ${children.length} children at path`, path.slice(0, i+1));
                        return null;
                    }

                    node = children[index];
                }
                return node;
            }

            function createNodeFromVNode(vnode) {
                // VNode structure from Rust: { tag, text?, attrs, children, key? }
                // Text nodes have tag === "#text" and a text field
                if (vnode.tag === '#text') {
                    return document.createTextNode(vnode.text || '');
                }

                // Element nodes
                const elem = document.createElement(vnode.tag);

                // Set attributes and handle events
                if (vnode.attrs) {
                    for (const [key, value] of Object.entries(vnode.attrs)) {
                        // Handle LiveView event attributes (@click, @change, etc.)
                        if (key.startsWith('@')) {
                            const eventName = key.substring(1);
                            elem.addEventListener(eventName, (e) => {
                                handleEvent(value, e);
                            });
                        } else {
                            // Special handling for input value property
                            if (key === 'value' && (elem.tagName === 'INPUT' || elem.tagName === 'TEXTAREA')) {
                                elem.value = value;
                            }
                            // Regular HTML attributes
                            elem.setAttribute(key, value);
                        }
                    }
                }

                // Add children recursively
                if (vnode.children && vnode.children.length > 0) {
                    for (const child of vnode.children) {
                        const childNode = createNodeFromVNode(child);
                        if (childNode) {
                            elem.appendChild(childNode);
                        }
                    }
                }

                return elem;
            }

            // Debug helper: log DOM structure
            function debugNode(node, prefix = '') {
                const children = Array.from(node.childNodes).filter(child => {
                    if (child.nodeType === Node.ELEMENT_NODE) return true;
                    if (child.nodeType === Node.TEXT_NODE) {
                        return child.textContent.trim().length > 0;
                    }
                    return false;
                });

                return {
                    tag: node.tagName || 'TEXT',
                    text: node.nodeType === Node.TEXT_NODE ? node.textContent.substring(0, 20) : null,
                    childCount: children.length,
                    children: children.slice(0, 3).map((c, i) => `[${i}]: ${c.tagName || 'TEXT'}`)
                };
            }

            function applyPatches(patches) {
                const parsedPatches = JSON.parse(patches);
                console.log('[LiveView] Applying', parsedPatches.length, 'patches');

                // Sort patches to ensure RemoveChild operations are applied in descending index order
                // within the same path. This prevents index invalidation when removing multiple children.
                parsedPatches.sort((a, b) => {
                    // RemoveChild patches should come before other types at the same path
                    if (a.type === 'RemoveChild' && b.type === 'RemoveChild') {
                        // Same path? Sort by index descending (higher indices first)
                        const pathA = JSON.stringify(a.path);
                        const pathB = JSON.stringify(b.path);
                        if (pathA === pathB) {
                            return b.index - a.index; // Descending order
                        }
                    }
                    return 0; // Maintain relative order for other patches
                });

                // Debug: log patch types for small patch sets
                if (parsedPatches.length > 0 && parsedPatches.length <= 20) {
                    console.log('[LiveView] Patch count:', parsedPatches.length);
                    for (let i = 0; i < Math.min(5, parsedPatches.length); i++) {
                        console.log('  Patch', i, ':', parsedPatches[i].type, 'at', parsedPatches[i].path);
                    }
                }

                let failedCount = 0;
                let successCount = 0;
                for (const patch of parsedPatches) {
                    const node = getNodeByPath(patch.path);
                    if (!node) {
                        failedCount++;
                        if (failedCount <= 3) {
                            // Log first 3 failures in detail
                            const patchType = Object.keys(patch).filter(k => k !== 'path')[0];
                            console.warn(`[LiveView] Failed patch #${failedCount} at path:`, patch.path);
                            console.warn('Patch type:', patchType, patch[patchType]);

                            // Try to traverse as far as we can to see where it breaks
                            let debugNode = getLiveViewRoot();

                            for (let i = 0; i < patch.path.length; i++) {
                                const children = Array.from(debugNode.childNodes).filter(child => {
                                    if (child.nodeType === Node.ELEMENT_NODE) return true;
                                    if (child.nodeType === Node.TEXT_NODE) return child.textContent.trim().length > 0;
                                    return false;
                                });

                                console.warn(`  Path[${i}] = ${patch.path[i]}, available children:`, children.length,
                                    children.map((c,idx) => `[${idx}]=${c.tagName||'TEXT'}`).join(', '));

                                if (patch.path[i] >= children.length) {
                                    console.warn(`  FAILED: Index ${patch.path[i]} out of bounds (only ${children.length} children)`);
                                    break;
                                }

                                debugNode = children[patch.path[i]];
                            }
                        }
                        continue;
                    }

                    successCount++;

                    if (patch.type === 'Replace') {
                        const newNode = createNodeFromVNode(patch.node);
                        node.parentNode.replaceChild(newNode, node);
                    } else if (patch.type === 'SetText') {
                        node.textContent = patch.text;
                    } else if (patch.type === 'SetAttr') {
                        // Special handling for input value to preserve user input
                        if (patch.key === 'value' && (node.tagName === 'INPUT' || node.tagName === 'TEXTAREA')) {
                            // Only update if the element is not currently focused (user is typing)
                            if (document.activeElement !== node) {
                                node.value = patch.value;
                            }
                            // Always update the attribute for consistency
                            node.setAttribute(patch.key, patch.value);
                        } else {
                            node.setAttribute(patch.key, patch.value);
                        }
                    } else if (patch.type === 'RemoveAttr') {
                        node.removeAttribute(patch.key);
                    } else if (patch.type === 'InsertChild') {
                        const newChild = createNodeFromVNode(patch.node);
                        // Use filtered children to match path traversal
                        const children = Array.from(node.childNodes).filter(child => {
                            if (child.nodeType === Node.ELEMENT_NODE) return true;
                            if (child.nodeType === Node.TEXT_NODE) {
                                return child.textContent.trim().length > 0;
                            }
                            return false;
                        });
                        const refChild = children[patch.index];
                        if (refChild) {
                            node.insertBefore(newChild, refChild);
                        } else {
                            node.appendChild(newChild);
                        }
                    } else if (patch.type === 'RemoveChild') {
                        // Use filtered children to match path traversal
                        const children = Array.from(node.childNodes).filter(child => {
                            if (child.nodeType === Node.ELEMENT_NODE) return true;
                            if (child.nodeType === Node.TEXT_NODE) {
                                return child.textContent.trim().length > 0;
                            }
                            return false;
                        });
                        const child = children[patch.index];
                        if (child) {
                            node.removeChild(child);
                        }
                    } else if (patch.type === 'MoveChild') {
                        // Use filtered children to match path traversal
                        const children = Array.from(node.childNodes).filter(child => {
                            if (child.nodeType === Node.ELEMENT_NODE) return true;
                            if (child.nodeType === Node.TEXT_NODE) {
                                return child.textContent.trim().length > 0;
                            }
                            return false;
                        });
                        const child = children[patch.from];
                        if (child) {
                            const refChild = children[patch.to];
                            if (refChild) {
                                node.insertBefore(child, refChild);
                            } else {
                                node.appendChild(child);
                            }
                        }
                    }
                }

                console.log(`[LiveView] Patch summary: ${successCount} succeeded, ${failedCount} failed`);

                // If any patches failed, return false to trigger full HTML fallback
                if (failedCount > 0) {
                    console.warn(`[LiveView] ${failedCount} patches failed, will fall back to full HTML`);
                    return false;
                }

                return true; // All patches applied successfully
            }

            async function handleEvent(eventName, params) {
                console.log('[LiveView] Event:', eventName, params);

                try {
                    const response = await fetch(window.location.href.split('?')[0], {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-CSRFToken': getCookie('csrftoken'),
                        },
                        body: JSON.stringify({
                            event: eventName,
                            params: params
                        })
                    });

                    if (response.ok) {
                        const data = await response.json();

                        // Check for version mismatch
                        if (data.version !== undefined) {
                            if (clientVdomVersion === null) {
                                // First response, initialize version
                                clientVdomVersion = data.version;
                                console.log('[LiveView] Initialized VDOM version:', clientVdomVersion);
                            } else if (clientVdomVersion !== data.version - 1) {
                                // Version mismatch detected! Client and server are out of sync
                                console.warn('[LiveView] VDOM version mismatch detected!');
                                console.warn(`  Client expected version ${clientVdomVersion + 1}, but server is at ${data.version}`);
                                console.warn('  Clearing client VDOM cache and using full HTML');

                                // Force full HTML reload to resync
                                if (data.html) {
                                    const parser = new DOMParser();
                                    const doc = parser.parseFromString(data.html, 'text/html');
                                    document.body.innerHTML = doc.body.innerHTML;
                                    clientVdomVersion = data.version;
                                    initReactCounters();
                                    initTodoItems();
                                    bindLiveViewEvents();
                                }
                                return;
                            }
                            // Update client version
                            clientVdomVersion = data.version;
                        }

                        if (data.patches) {
                            // Try to apply DOM patches (efficient!)
                            const success = applyPatches(data.patches);
                            if (success === false && data.html) {
                                // Patches failed, fall back to HTML
                                console.warn('[LiveView] Patches failed, falling back to full HTML');
                                const parser = new DOMParser();
                                const doc = parser.parseFromString(data.html, 'text/html');
                                document.body.innerHTML = doc.body.innerHTML;
                            }
                            // Re-bind event handlers to new/modified elements
                            initReactCounters();
                            initTodoItems();
                            bindLiveViewEvents();
                        } else if (data.html) {
                            // Replace full HTML
                            const parser = new DOMParser();
                            const doc = parser.parseFromString(data.html, 'text/html');
                            document.body.innerHTML = doc.body.innerHTML;
                            // Re-bind event handlers to new elements
                            initReactCounters();
                            initTodoItems();
                            bindLiveViewEvents();
                        }
                    }
                } catch (error) {
                    console.error('[LiveView] Error:', error);
                }
            }

            function getCookie(name) {
                let cookieValue = null;
                if (document.cookie && document.cookie !== '') {
                    const cookies = document.cookie.split(';');
                    for (let i = 0; i < cookies.length; i++) {
                        const cookie = cookies[i].trim();
                        if (cookie.substring(0, name.length + 1) === (name + '=')) {
                            cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                            break;
                        }
                    }
                }
                return cookieValue;
            }

            // Client-side TodoItem interactions (only for React demo TodoItems)
            function initTodoItems() {
                // Handle checkbox changes for .todo-checkbox (React demo TodoItems)
                document.querySelectorAll('.todo-checkbox').forEach(checkbox => {
                    if (!checkbox.dataset.reactInitialized) {
                        checkbox.dataset.reactInitialized = 'true';
                        checkbox.addEventListener('change', function() {
                            const todoItem = this.closest('.todo-item');
                            if (this.checked) {
                                todoItem.classList.add('completed');
                            } else {
                                todoItem.classList.remove('completed');
                            }
                        });
                    }
                });

                // Handle delete button clicks for React demo TodoItems
                document.querySelectorAll('.todo-delete').forEach(button => {
                    if (!button.dataset.reactInitialized) {
                        button.dataset.reactInitialized = 'true';
                        button.addEventListener('click', async function() {
                            const todoText = this.dataset.todoText;
                            await handleEvent('delete_todo_item', { text: todoText });
                        });
                    }
                });
            }

            document.addEventListener('DOMContentLoaded', function() {
                console.log('[LiveView] Initialized with Rust-powered rendering');
                initReactCounters();  // Initialize client-side React components
                initTodoItems();      // Initialize todo item checkboxes
                bindLiveViewEvents();
            });
        </script>
        """

        if '</body>' in html:
            html = html.replace('</body>', f'{script}</body>')
        else:
            html += script

        return html


def live_view(template_name: Optional[str] = None,
              template_string: Optional[str] = None):
    """
    Decorator to convert a function-based view into a LiveView.

    Usage:
        @live_view(template_name='counter.html')
        def counter_view(request):
            count = 0

            def increment():
                nonlocal count
                count += 1

            def decrement():
                nonlocal count
                count -= 1

            return locals()

    Args:
        template_name: Path to Django template
        template_string: Inline template string

    Returns:
        View function
    """
    def decorator(func: Callable) -> Callable:
        def wrapper(request, *args, **kwargs):
            # Create a dynamic LiveView class
            class DynamicLiveView(LiveView):
                pass

            if template_name:
                DynamicLiveView.template_name = template_name
            if template_string:
                DynamicLiveView.template_string = template_string

            view = DynamicLiveView()

            # Execute the function to get initial state
            result = func(request, *args, **kwargs)
            if isinstance(result, dict):
                for key, value in result.items():
                    if not callable(value):
                        setattr(view, key, value)
                    else:
                        setattr(view, key, value)

            # Handle the request
            if request.method == 'GET':
                return view.get(request, *args, **kwargs)
            elif request.method == 'POST':
                return view.post(request, *args, **kwargs)

        return wrapper

    return decorator
