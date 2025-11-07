"""
LiveView base class and decorator for reactive Django views
"""

import json
import asyncio
import hashlib
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

    def render_with_diff(self, request=None) -> tuple[str, Optional[str]]:
        """
        Render the view and compute diff from last render.

        Returns:
            Tuple of (html, patches_json)
        """
        self._initialize_rust_view(request)
        self._sync_state_to_rust()
        return self._rust_view.render_with_diff()

    def get(self, request, *args, **kwargs):
        """Handle GET requests - initial page load"""
        self.mount(request, **kwargs)

        # Store initial state in session
        view_key = f'liveview_{request.path}'
        request.session[view_key] = self.get_context_data()

        # Initialize and render to establish baseline VDOM
        html = self.render(request)

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
            html, patches_json = self.render_with_diff(request)

            # Always send both patches and HTML for fallback
            response = {'html': html}
            if patches_json:
                response['patches'] = patches_json

            return JsonResponse(response)

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
                            // For non-checkbox inputs, include the value
                            if (e.target.type !== 'checkbox') {
                                params.value = e.target.value;
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
            // Find the root LiveView container (first child of body that's not a script)
            function getLiveViewRoot() {
                for (const child of document.body.childNodes) {
                    if (child.nodeType === Node.ELEMENT_NODE && child.tagName !== 'SCRIPT') {
                        return child;
                    }
                }
                return document.body;
            }

            function getNodeByPath(path) {
                // The Rust VDOM starts at the first element child of <body>
                // Path [0] = root element, [0,0] = first child of root, etc.
                let node = getLiveViewRoot();

                // Empty path means the root itself
                if (path.length === 0) {
                    return node;
                }

                // Path starts at root (which we already have), so skip first index if it's 0
                const adjustedPath = path[0] === 0 && path.length > 1 ? path.slice(1) : path;

                // Traverse the path, filtering out whitespace-only text nodes
                // to match how the Rust HTML parser handles whitespace
                for (let i = 0; i < adjustedPath.length; i++) {
                    const index = adjustedPath[i];

                    // Get non-whitespace children (matching Rust parser behavior)
                    const children = Array.from(node.childNodes).filter(child => {
                        // Keep element nodes
                        if (child.nodeType === Node.ELEMENT_NODE) return true;
                        // Keep non-empty text nodes (matching lines 82-87 in parser.rs)
                        if (child.nodeType === Node.TEXT_NODE) {
                            return child.textContent.trim().length > 0;
                        }
                        return false;
                    });

                    if (index >= children.length) {
                        console.warn(`[LiveView] Index ${index} out of bounds, only ${children.length} children at path`, path.slice(0, i+1));
                        return null;
                    }

                    node = children[index];
                }
                return node;
            }

            function createNodeFromVNode(vnode) {
                if (vnode.type === 'Text') {
                    return document.createTextNode(vnode.text || '');
                } else if (vnode.type === 'Element') {
                    const elem = document.createElement(vnode.tag);
                    // Set attributes
                    if (vnode.attrs) {
                        for (const [key, value] of Object.entries(vnode.attrs)) {
                            elem.setAttribute(key, value);
                        }
                    }
                    // Add children
                    if (vnode.children) {
                        for (const child of vnode.children) {
                            elem.appendChild(createNodeFromVNode(child));
                        }
                    }
                    return elem;
                }
                return null;
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

                // If there are too many patches (like adding 1000 items), it might be
                // more efficient to just replace the whole container. This threshold
                // can be tuned based on performance testing.
                const PATCH_THRESHOLD = 100;
                if (parsedPatches.length > PATCH_THRESHOLD) {
                    console.log(`[LiveView] Too many patches (${parsedPatches.length}), falling back to full HTML replacement`);
                    return false; // Signal to caller to use full HTML
                }

                let failedCount = 0;
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
                            const adjustedPath = patch.path[0] === 0 && patch.path.length > 1 ? patch.path.slice(1) : patch.path;

                            for (let i = 0; i < adjustedPath.length; i++) {
                                const children = Array.from(debugNode.childNodes).filter(child => {
                                    if (child.nodeType === Node.ELEMENT_NODE) return true;
                                    if (child.nodeType === Node.TEXT_NODE) return child.textContent.trim().length > 0;
                                    return false;
                                });

                                console.warn(`  Path[${i}] = ${adjustedPath[i]}, available children:`, children.length,
                                    children.map((c,idx) => `[${idx}]=${c.tagName||'TEXT'}`).join(', '));

                                if (adjustedPath[i] >= children.length) {
                                    console.warn(`  FAILED: Index ${adjustedPath[i]} out of bounds (only ${children.length} children)`);
                                    break;
                                }

                                debugNode = children[adjustedPath[i]];
                            }
                        }
                        continue;
                    }

                    if (patch.Replace) {
                        const newNode = createNodeFromVNode(patch.Replace.node);
                        node.parentNode.replaceChild(newNode, node);
                    } else if (patch.SetText) {
                        node.textContent = patch.SetText.text;
                    } else if (patch.SetAttr) {
                        node.setAttribute(patch.SetAttr.key, patch.SetAttr.value);
                    } else if (patch.RemoveAttr) {
                        node.removeAttribute(patch.RemoveAttr.key);
                    } else if (patch.InsertChild) {
                        const newChild = createNodeFromVNode(patch.InsertChild.node);
                        const refChild = node.childNodes[patch.InsertChild.index];
                        if (refChild) {
                            node.insertBefore(newChild, refChild);
                        } else {
                            node.appendChild(newChild);
                        }
                    } else if (patch.RemoveChild) {
                        const child = node.childNodes[patch.RemoveChild.index];
                        if (child) {
                            node.removeChild(child);
                        }
                    } else if (patch.MoveChild) {
                        const child = node.childNodes[patch.MoveChild.from];
                        if (child) {
                            const refChild = node.childNodes[patch.MoveChild.to];
                            if (refChild) {
                                node.insertBefore(child, refChild);
                            } else {
                                node.appendChild(child);
                            }
                        }
                    }
                }
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
                        if (data.patches) {
                            // Try to apply DOM patches (efficient!)
                            const success = applyPatches(data.patches);
                            if (success === false && data.html) {
                                // Patches failed/too many, fall back to HTML
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
