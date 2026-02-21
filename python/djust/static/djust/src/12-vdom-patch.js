
// === VDOM Patch Application ===

/**
 * Sanitize a djust ID for safe logging (defense-in-depth).
 * @param {*} id - The ID to sanitize
 * @returns {string} - Sanitized ID safe for logging
 */
function sanitizeIdForLog(id) {
    if (!id) return 'none';
    return String(id).slice(0, 20).replace(/[^\w-]/g, '');
}

/**
 * Resolve a DOM node using ID-based lookup (primary) or path traversal (fallback).
 *
 * Resolution strategy:
 * 1. If djustId is provided, try querySelector('[data-dj-id="..."]') - O(1), reliable
 * 2. Fall back to index-based path traversal
 *
 * @param {Array<number>} path - Index-based path (fallback)
 * @param {string|null} djustId - Compact djust ID for direct lookup (e.g., "1a")
 * @returns {Node|null} - Found node or null
 */
function getNodeByPath(path, djustId = null) {
    // Strategy 1: ID-based resolution (fast, reliable)
    if (djustId) {
        const byId = document.querySelector(`[data-dj-id="${CSS.escape(djustId)}"]`);
        if (byId) {
            return byId;
        }
        // ID not found - fall through to path-based
        if (globalThis.djustDebug) {
            // Log without user data to avoid log injection
            console.log('[LiveView] ID lookup failed, trying path fallback');
        }
    }

    // Strategy 2: Index-based path traversal (fallback)
    let node = getLiveViewRoot();

    if (path.length === 0) {
        return node;
    }

    for (let i = 0; i < path.length; i++) {
        const index = path[i]; // eslint-disable-line security/detect-object-injection -- path is a server-provided integer array
        const children = Array.from(node.childNodes).filter(child => {
            if (child.nodeType === Node.ELEMENT_NODE) return true;
            if (child.nodeType === Node.TEXT_NODE) {
                // Preserve non-breaking spaces (\u00A0) as significant, matching Rust VDOM parser.
                // Only filter out ASCII whitespace-only text nodes (space, tab, newline, CR).
                // JS \s includes \u00A0, so we use an explicit ASCII whitespace pattern instead.
                return (/[^ \t\n\r\f]/.test(child.textContent));
            }
            return false;
        });

        if (index >= children.length) {
            if (globalThis.djustDebug) {
                // Explicit number coercion for safe logging
                const safeIndex = Number(index) || 0;
                const safeLen = Number(children.length) || 0;
                console.warn(`[LiveView] Path traversal failed at index ${safeIndex}, only ${safeLen} children`);
            }
            return null;
        }

        node = children[index];
    }

    return node;
}

// SVG namespace and tags for proper element creation
const SVG_NAMESPACE = 'http://www.w3.org/2000/svg';
const SVG_TAGS = new Set([
    'svg', 'path', 'circle', 'rect', 'line', 'polyline', 'polygon',
    'ellipse', 'g', 'defs', 'use', 'text', 'tspan', 'textPath',
    'clipPath', 'mask', 'pattern', 'marker', 'symbol', 'linearGradient',
    'radialGradient', 'stop', 'image', 'foreignObject', 'switch',
    'desc', 'title', 'metadata'
]);

// Allowed HTML tags for VDOM element creation (security: prevents script injection)
// This whitelist covers standard HTML elements; extend as needed
const ALLOWED_HTML_TAGS = new Set([
    // Document structure
    'html', 'head', 'body', 'div', 'span', 'main', 'section', 'article',
    'aside', 'header', 'footer', 'nav', 'figure', 'figcaption',
    // Text content
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'pre', 'code', 'blockquote',
    'hr', 'br', 'wbr', 'address',
    // Inline text
    'a', 'abbr', 'b', 'bdi', 'bdo', 'cite', 'data', 'dfn', 'em', 'i',
    'kbd', 'mark', 'q', 's', 'samp', 'small', 'strong', 'sub', 'sup',
    'time', 'u', 'var', 'del', 'ins',
    // Lists
    'ul', 'ol', 'li', 'dl', 'dt', 'dd', 'menu',
    // Tables
    'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'caption',
    'colgroup', 'col',
    // Forms
    'form', 'fieldset', 'legend', 'label', 'input', 'textarea', 'select',
    'option', 'optgroup', 'button', 'datalist', 'output', 'progress', 'meter',
    // Media
    'img', 'audio', 'video', 'source', 'track', 'picture', 'canvas',
    'iframe', 'embed', 'object', 'param', 'map', 'area',
    // Interactive
    'details', 'summary', 'dialog',
    // Other
    'template', 'slot', 'noscript'
]);

/**
 * Check if a DOM element is within an SVG context.
 * Used when creating new elements during patch application.
 */
function isInSvgContext(element) {
    if (!element) return false;
    // Check if element itself or any ancestor is an SVG element
    let current = element;
    while (current && current !== document.body) {
        if (current.namespaceURI === SVG_NAMESPACE) {
            return true;
        }
        current = current.parentElement;
    }
    return false;
}

/**
 * Create an SVG element by tag name (security: only creates whitelisted tags)
 * Uses a lookup object with factory functions to ensure only string literals
 * are passed to createElementNS.
 */
const SVG_ELEMENT_FACTORIES = {
    'svg': () => document.createElementNS(SVG_NAMESPACE, 'svg'),
    'path': () => document.createElementNS(SVG_NAMESPACE, 'path'),
    'circle': () => document.createElementNS(SVG_NAMESPACE, 'circle'),
    'rect': () => document.createElementNS(SVG_NAMESPACE, 'rect'),
    'line': () => document.createElementNS(SVG_NAMESPACE, 'line'),
    'polyline': () => document.createElementNS(SVG_NAMESPACE, 'polyline'),
    'polygon': () => document.createElementNS(SVG_NAMESPACE, 'polygon'),
    'ellipse': () => document.createElementNS(SVG_NAMESPACE, 'ellipse'),
    'g': () => document.createElementNS(SVG_NAMESPACE, 'g'),
    'defs': () => document.createElementNS(SVG_NAMESPACE, 'defs'),
    'use': () => document.createElementNS(SVG_NAMESPACE, 'use'),
    'text': () => document.createElementNS(SVG_NAMESPACE, 'text'),
    'tspan': () => document.createElementNS(SVG_NAMESPACE, 'tspan'),
    'textPath': () => document.createElementNS(SVG_NAMESPACE, 'textPath'),
    'clipPath': () => document.createElementNS(SVG_NAMESPACE, 'clipPath'),
    'mask': () => document.createElementNS(SVG_NAMESPACE, 'mask'),
    'pattern': () => document.createElementNS(SVG_NAMESPACE, 'pattern'),
    'marker': () => document.createElementNS(SVG_NAMESPACE, 'marker'),
    'symbol': () => document.createElementNS(SVG_NAMESPACE, 'symbol'),
    'linearGradient': () => document.createElementNS(SVG_NAMESPACE, 'linearGradient'),
    'radialGradient': () => document.createElementNS(SVG_NAMESPACE, 'radialGradient'),
    'stop': () => document.createElementNS(SVG_NAMESPACE, 'stop'),
    'image': () => document.createElementNS(SVG_NAMESPACE, 'image'),
    'foreignObject': () => document.createElementNS(SVG_NAMESPACE, 'foreignObject'),
    'switch': () => document.createElementNS(SVG_NAMESPACE, 'switch'),
    'desc': () => document.createElementNS(SVG_NAMESPACE, 'desc'),
    'title': () => document.createElementNS(SVG_NAMESPACE, 'title'),
    'metadata': () => document.createElementNS(SVG_NAMESPACE, 'metadata'),
};

function createSvgElement(tagLower) {
    const factory = SVG_ELEMENT_FACTORIES[tagLower];
    return factory ? factory() : document.createElement('span');
}

/**
 * Create an HTML element by tag name (security: only creates whitelisted tags)
 * Uses a lookup object with factory functions to ensure only string literals
 * are passed to createElement.
 */
const HTML_ELEMENT_FACTORIES = {
    // Document structure
    'html': () => document.createElement('html'),
    'head': () => document.createElement('head'),
    'body': () => document.createElement('body'),
    'div': () => document.createElement('div'),
    'span': () => document.createElement('span'),
    'main': () => document.createElement('main'),
    'section': () => document.createElement('section'),
    'article': () => document.createElement('article'),
    'aside': () => document.createElement('aside'),
    'header': () => document.createElement('header'),
    'footer': () => document.createElement('footer'),
    'nav': () => document.createElement('nav'),
    'figure': () => document.createElement('figure'),
    'figcaption': () => document.createElement('figcaption'),
    // Text content
    'h1': () => document.createElement('h1'),
    'h2': () => document.createElement('h2'),
    'h3': () => document.createElement('h3'),
    'h4': () => document.createElement('h4'),
    'h5': () => document.createElement('h5'),
    'h6': () => document.createElement('h6'),
    'p': () => document.createElement('p'),
    'pre': () => document.createElement('pre'),
    'code': () => document.createElement('code'),
    'blockquote': () => document.createElement('blockquote'),
    'hr': () => document.createElement('hr'),
    'br': () => document.createElement('br'),
    'wbr': () => document.createElement('wbr'),
    'address': () => document.createElement('address'),
    // Inline text
    'a': () => document.createElement('a'),
    'abbr': () => document.createElement('abbr'),
    'b': () => document.createElement('b'),
    'bdi': () => document.createElement('bdi'),
    'bdo': () => document.createElement('bdo'),
    'cite': () => document.createElement('cite'),
    'data': () => document.createElement('data'),
    'dfn': () => document.createElement('dfn'),
    'em': () => document.createElement('em'),
    'i': () => document.createElement('i'),
    'kbd': () => document.createElement('kbd'),
    'mark': () => document.createElement('mark'),
    'q': () => document.createElement('q'),
    's': () => document.createElement('s'),
    'samp': () => document.createElement('samp'),
    'small': () => document.createElement('small'),
    'strong': () => document.createElement('strong'),
    'sub': () => document.createElement('sub'),
    'sup': () => document.createElement('sup'),
    'time': () => document.createElement('time'),
    'u': () => document.createElement('u'),
    'var': () => document.createElement('var'),
    'del': () => document.createElement('del'),
    'ins': () => document.createElement('ins'),
    // Lists
    'ul': () => document.createElement('ul'),
    'ol': () => document.createElement('ol'),
    'li': () => document.createElement('li'),
    'dl': () => document.createElement('dl'),
    'dt': () => document.createElement('dt'),
    'dd': () => document.createElement('dd'),
    'menu': () => document.createElement('menu'),
    // Tables
    'table': () => document.createElement('table'),
    'thead': () => document.createElement('thead'),
    'tbody': () => document.createElement('tbody'),
    'tfoot': () => document.createElement('tfoot'),
    'tr': () => document.createElement('tr'),
    'th': () => document.createElement('th'),
    'td': () => document.createElement('td'),
    'caption': () => document.createElement('caption'),
    'colgroup': () => document.createElement('colgroup'),
    'col': () => document.createElement('col'),
    // Forms
    'form': () => document.createElement('form'),
    'fieldset': () => document.createElement('fieldset'),
    'legend': () => document.createElement('legend'),
    'label': () => document.createElement('label'),
    'input': () => document.createElement('input'),
    'textarea': () => document.createElement('textarea'),
    'select': () => document.createElement('select'),
    'option': () => document.createElement('option'),
    'optgroup': () => document.createElement('optgroup'),
    'button': () => document.createElement('button'),
    'datalist': () => document.createElement('datalist'),
    'output': () => document.createElement('output'),
    'progress': () => document.createElement('progress'),
    'meter': () => document.createElement('meter'),
    // Media
    'img': () => document.createElement('img'),
    'audio': () => document.createElement('audio'),
    'video': () => document.createElement('video'),
    'source': () => document.createElement('source'),
    'track': () => document.createElement('track'),
    'picture': () => document.createElement('picture'),
    'canvas': () => document.createElement('canvas'),
    'iframe': () => document.createElement('iframe'),
    'embed': () => document.createElement('embed'),
    'object': () => document.createElement('object'),
    'param': () => document.createElement('param'),
    'map': () => document.createElement('map'),
    'area': () => document.createElement('area'),
    // Interactive
    'details': () => document.createElement('details'),
    'summary': () => document.createElement('summary'),
    'dialog': () => document.createElement('dialog'),
    // Other
    'template': () => document.createElement('template'),
    'slot': () => document.createElement('slot'),
    'noscript': () => document.createElement('noscript'),
};

function createHtmlElement(tagLower) {
    const factory = HTML_ELEMENT_FACTORIES[tagLower];
    return factory ? factory() : document.createElement('span');
}

/**
 * Create a DOM node from a virtual node (VDOM).
 * SECURITY NOTE: vnode data comes from the trusted server (Django templates
 * rendered server-side). This is the standard LiveView pattern where the
 * server controls all HTML structure via VDOM patches.
 */
function createNodeFromVNode(vnode, inSvgContext = false) {
    if (vnode.tag === '#text') {
        return document.createTextNode(vnode.text || '');
    }

    // Validate tag name against whitelist (security: prevents script injection)
    // Convert to lowercase for consistent matching
    const tagLower = String(vnode.tag || '').toLowerCase();

    // Check if tag is in our whitelists
    const isSvgTag = SVG_TAGS.has(tagLower);
    const isAllowedHtml = ALLOWED_HTML_TAGS.has(tagLower);

    // Determine SVG context for child element creation
    const useSvgNamespace = isSvgTag || inSvgContext;

    // Security: Only pass whitelisted string literals to createElement
    // If not in whitelist, use 'span' as a safe fallback
    let elem;
    if (isSvgTag) {
        // SVG tag: use switch for known values only
        elem = createSvgElement(tagLower);
    } else if (isAllowedHtml) {
        // HTML tag: use switch for known values only
        elem = createHtmlElement(tagLower);
    } else {
        // Unknown tag - use safe span placeholder
        if (globalThis.djustDebug) {
            console.warn('[LiveView] Blocked unknown tag, using span placeholder');
        }
        elem = document.createElement('span');
    }

    if (vnode.attrs) {
        for (const [key, value] of Object.entries(vnode.attrs)) {
            // Set all attributes on the element (including dj-* attributes).
            // Event listeners for dj-* attributes are attached by bindLiveViewEvents()
            // after patches are applied, which already uses _markHandlerBound to
            // prevent double-binding on subsequent calls.
            if (key === 'value' && (elem.tagName === 'INPUT' || elem.tagName === 'TEXTAREA')) {
                elem.value = value;
            }
            elem.setAttribute(key, value);

            // Note: dj-* event listeners are attached by bindLiveViewEvents() after
            // patch application. Do NOT pre-mark elements here — that would prevent
            // bindLiveViewEvents() from ever attaching the listener.
        }
    }

    if (vnode.children) {
        // Pass SVG context to children so nested SVG elements are created correctly
        for (const child of vnode.children) {
            elem.appendChild(createNodeFromVNode(child, useSvgNamespace));
        }
    }

    // For textareas, set .value from text content (textContent alone doesn't set displayed value)
    if (elem.tagName === 'TEXTAREA') {
        elem.value = elem.textContent || '';
    }

    return elem;
}

/**
 * Handle dj-update attribute for efficient list updates with temporary_assigns.
 *
 * When using temporary_assigns in djust LiveViews, the server clears large collections
 * from memory after each render. This function ensures the client preserves existing
 * DOM elements and only adds new content.
 *
 * Supported dj-update values:
 *   - "append": Add new children to the end (e.g., chat messages, feed items)
 *   - "prepend": Add new children to the beginning (e.g., notifications)
 *   - "replace": Replace all content (default behavior)
 *   - "ignore": Don't update this element at all (for user-edited content)
 *
 * Example template usage:
 *   <ul dj-update="append" id="messages">
 *     {% for msg in messages %}
 *       <li id="msg-{{ msg.id }}">{{ msg.content }}</li>
 *     {% endfor %}
 *   </ul>
 *
 * @param {HTMLElement} existingRoot - The current DOM root
 * @param {HTMLElement} newRoot - The new content from server
 */
/**
 * Flag set by handleServerResponse when applying broadcast patches.
 * When true, preserveFormValues skips saving/restoring the focused
 * element so remote content (from other users) takes effect.
 */
let _isBroadcastUpdate = false;

/**
 * Preserve form values across innerHTML replacement.
 *
 * innerHTML destroys the DOM, creating new elements. For the focused
 * element we save and restore the user's in-progress value + cursor.
 * For all textareas, we sync .value from textContent after replacement
 * (innerHTML only sets the DOM attribute, not the JS property).
 *
 * Matching strategy: id → name → positional index within container.
 */
function preserveFormValues(container, updateFn) {
    const active = document.activeElement;
    let saved = null;

    // Skip saving focused element for broadcast (remote) updates —
    // the server content from another user should take effect.
    if (_isBroadcastUpdate) {
        updateFn();
        container.querySelectorAll('textarea').forEach(el => {
            el.value = el.textContent || '';
        });
        return;
    }

    // Only save the focused form element (user is actively editing)
    if (active && container.contains(active) &&
        (active.tagName === 'TEXTAREA' || active.tagName === 'INPUT' || active.tagName === 'SELECT')) {
        saved = { tag: active.tagName.toLowerCase() };
        // Build a matching key: prefer id, then name, then positional index
        if (active.id) {
            saved.findBy = 'id';
            saved.key = active.id;
        } else if (active.name) {
            saved.findBy = 'name';
            saved.key = active.name;
        } else {
            // Positional: find index among same-tag siblings in container
            saved.findBy = 'index';
            const siblings = container.querySelectorAll(active.tagName.toLowerCase());
            saved.key = Array.from(siblings).indexOf(active);
        }
        if (active.tagName === 'TEXTAREA') {
            saved.value = active.value;
            saved.selStart = active.selectionStart;
            saved.selEnd = active.selectionEnd;
        } else if (active.type === 'checkbox' || active.type === 'radio') {
            saved.checked = active.checked;
        } else {
            saved.value = active.value;
        }
    }

    updateFn();

    // Sync all textarea .value from textContent (innerHTML doesn't set .value)
    container.querySelectorAll('textarea').forEach(el => {
        el.value = el.textContent || '';
    });

    // Restore the focused element's value
    if (saved) {
        let el = null;
        if (saved.findBy === 'id') {
            el = container.querySelector(`#${CSS.escape(saved.key)}`);
        } else if (saved.findBy === 'name') {
            el = container.querySelector(`[name="${CSS.escape(saved.key)}"]`);
        } else {
            // Positional fallback
            const candidates = container.querySelectorAll(saved.tag);
            el = candidates[saved.key] || null;
        }
        if (el) {
            if (saved.tag === 'textarea') {
                el.value = saved.value;
                try { el.setSelectionRange(saved.selStart, saved.selEnd); } catch (e) { /* */ }
                el.focus();
            } else if (el.type === 'checkbox' || el.type === 'radio') {
                el.checked = saved.checked;
            } else if (saved.value !== undefined) {
                el.value = saved.value;
            }
        }
    }
}

/**
 * Morph existing DOM children to match desired DOM children.
 * Preserves existing elements (and their event listeners) where possible.
 *
 * Matching per child:
 *   1. If desired child has an id → find existing child with same id (keyed)
 *   2. If current existing child has same tag and neither has an id → reuse
 *   3. Otherwise → clone desired child and insert
 *
 * Unmatched existing children are removed after the walk.
 *
 * @param {Element} existing - Current live DOM parent
 * @param {Element} desired  - Target DOM parent (parsed from server HTML)
 */
function morphChildren(existing, desired) {
    const existingNodes = Array.from(existing.childNodes);
    const desiredNodes = Array.from(desired.childNodes);

    // Index existing elements by id for O(1) keyed lookup
    const existingById = new Map();
    for (const node of existingNodes) {
        if (node.nodeType === Node.ELEMENT_NODE && node.id) {
            existingById.set(node.id, node);
        }
    }

    const matched = new Set();
    let eIdx = 0;

    for (const dNode of desiredNodes) {
        // Advance past already-matched existing nodes
        while (eIdx < existingNodes.length && matched.has(existingNodes[eIdx])) {
            eIdx++;
        }
        const eNode = eIdx < existingNodes.length ? existingNodes[eIdx] : null;

        // --- Text node ---
        if (dNode.nodeType === Node.TEXT_NODE) {
            if (eNode && eNode.nodeType === Node.TEXT_NODE && !matched.has(eNode)) {
                if (eNode.textContent !== dNode.textContent) {
                    eNode.textContent = dNode.textContent;
                }
                matched.add(eNode);
                eIdx++;
            } else {
                existing.insertBefore(document.createTextNode(dNode.textContent), eNode);
            }
            continue;
        }

        // --- Comment node ---
        if (dNode.nodeType === Node.COMMENT_NODE) {
            if (eNode && eNode.nodeType === Node.COMMENT_NODE && !matched.has(eNode)) {
                if (eNode.textContent !== dNode.textContent) {
                    eNode.textContent = dNode.textContent;
                }
                matched.add(eNode);
                eIdx++;
            } else {
                existing.insertBefore(document.createComment(dNode.textContent), eNode);
            }
            continue;
        }

        // --- Element node ---
        if (dNode.nodeType !== Node.ELEMENT_NODE) {
            continue;
        }

        const dId = dNode.id || null;

        // Strategy 1: Match by id (keyed element)
        if (dId && existingById.has(dId)) {
            const match = existingById.get(dId);
            existingById.delete(dId);
            matched.add(match);
            if (match !== eNode) {
                // Move keyed element into correct position
                existing.insertBefore(match, eNode);
            } else {
                eIdx++;
            }
            morphElement(match, dNode);
            continue;
        }

        // Strategy 2: Same tag, no ids on either side — reuse in place
        if (eNode && eNode.nodeType === Node.ELEMENT_NODE &&
            eNode.tagName === dNode.tagName &&
            !dId && !eNode.id && !matched.has(eNode)) {
            matched.add(eNode);
            morphElement(eNode, dNode);
            eIdx++;
            continue;
        }

        // Strategy 3: No match — clone desired child and insert
        existing.insertBefore(dNode.cloneNode(true), eNode);
    }

    // Remove unmatched existing children
    for (const node of existingNodes) {
        if (!matched.has(node) && node.parentNode === existing) {
            existing.removeChild(node);
        }
    }
}

/**
 * Morph a single element to match a desired element.
 * Updates attributes and recurses into children.
 * Preserves event listeners on the existing element.
 *
 * @param {Element} existing - Current live DOM element
 * @param {Element} desired  - Target element to match
 */
function morphElement(existing, desired) {
    // Tag mismatch — replace entirely
    if (existing.tagName !== desired.tagName) {
        // Clean up poll timers before replacing (prevents orphaned intervals)
        if (existing._djustPollIntervalId) {
            clearInterval(existing._djustPollIntervalId);
            if (existing._djustPollVisibilityHandler) {
                document.removeEventListener('visibilitychange', existing._djustPollVisibilityHandler);
            }
        }
        existing.parentNode.replaceChild(desired.cloneNode(true), existing);
        return;
    }

    // dj-update="ignore" — skip entirely
    if (existing.getAttribute('dj-update') === 'ignore') {
        return;
    }

    // --- Sync attributes ---
    // Remove attributes not present in desired
    for (let i = existing.attributes.length - 1; i >= 0; i--) {
        const name = existing.attributes[i].name;
        if (!desired.hasAttribute(name)) {
            existing.removeAttribute(name);
        }
    }
    // Set/update attributes from desired
    for (const attr of desired.attributes) {
        if (existing.getAttribute(attr.name) !== attr.value) {
            existing.setAttribute(attr.name, attr.value);
        }
    }

    // --- Form element value sync ---
    const isFocused = document.activeElement === existing;
    const skipValue = isFocused && !_isBroadcastUpdate;

    if (existing.tagName === 'INPUT' && !skipValue) {
        if (existing.type === 'checkbox' || existing.type === 'radio') {
            existing.checked = desired.checked;
        } else {
            const newVal = desired.value || desired.getAttribute('value') || '';
            if (existing.value !== newVal) {
                existing.value = newVal;
            }
        }
    } else if (existing.tagName === 'SELECT' && !skipValue) {
        const newVal = desired.value;
        if (existing.value !== newVal) {
            existing.value = newVal;
        }
    }

    // --- Recurse into children ---
    // dj-update="append"/"prepend" accumulate children server-side;
    // morphing would remove them, so skip child recursion
    const updateMode = existing.getAttribute('dj-update');
    if (updateMode === 'append' || updateMode === 'prepend') {
        return;
    }

    morphChildren(existing, desired);

    // Sync textarea .value from textContent after children are morphed
    // (.value and .textContent diverge after initial render)
    if (existing.tagName === 'TEXTAREA' && !skipValue) {
        existing.value = existing.textContent || '';
    }
}

function applyDjUpdateElements(existingRoot, newRoot) {
    // Find all elements with dj-update attribute in the new content
    const djUpdateElements = newRoot.querySelectorAll('[dj-update]');

    if (djUpdateElements.length === 0) {
        // No dj-update elements — morph to preserve event listeners
        morphChildren(existingRoot, newRoot);
        return;
    }

    // Track which elements we've handled specially
    const handledIds = new Set();

    // Process each dj-update element
    for (const newElement of djUpdateElements) {
        const updateMode = newElement.getAttribute('dj-update');
        const elementId = newElement.id;

        if (!elementId) {
            console.warn('[LiveView:dj-update] Element with dj-update must have an id:', newElement);
            continue;
        }

        const existingElement = existingRoot.querySelector(`#${CSS.escape(elementId)}`);
        if (!existingElement) {
            // Element doesn't exist yet, will be created by full update
            continue;
        }

        handledIds.add(elementId);

        switch (updateMode) {
            case 'append': {
                // Get new children that don't already exist
                const existingChildIds = new Set(
                    Array.from(existingElement.children)
                        .map(child => child.id)
                        .filter(id => id)
                );

                for (const newChild of Array.from(newElement.children)) {
                    if (newChild.id && !existingChildIds.has(newChild.id)) {
                        // Clone and append new child
                        existingElement.appendChild(newChild.cloneNode(true));
                        if (globalThis.djustDebug) {
                            console.log(`[LiveView:dj-update] Appended #${newChild.id} to #${elementId}`);
                        }
                    }
                }
                break;
            }

            case 'prepend': {
                // Get new children that don't already exist
                const existingChildIds = new Set(
                    Array.from(existingElement.children)
                        .map(child => child.id)
                        .filter(id => id)
                );

                const firstExisting = existingElement.firstChild;
                for (const newChild of Array.from(newElement.children).reverse()) {
                    if (newChild.id && !existingChildIds.has(newChild.id)) {
                        // Clone and prepend new child
                        existingElement.insertBefore(newChild.cloneNode(true), firstExisting);
                        if (globalThis.djustDebug) {
                            console.log(`[LiveView:dj-update] Prepended #${newChild.id} to #${elementId}`);
                        }
                    }
                }
                break;
            }

            case 'ignore':
                // Don't update this element at all
                if (globalThis.djustDebug) {
                    console.log(`[LiveView:dj-update] Ignoring #${elementId}`);
                }
                break;

            case 'replace':
            default:
                // Morph to preserve event listeners
                morphElement(existingElement, newElement);
                break;
        }
    }

    // For elements NOT handled by dj-update, do standard updates
    // This ensures non-dj-update parts of the page still get updated

    // Get all top-level elements in both roots
    const existingChildren = Array.from(existingRoot.children);
    const newChildren = Array.from(newRoot.children);

    // Create a map of new children by id for quick lookup
    const newChildMap = new Map();
    for (const child of newChildren) {
        if (child.id) {
            newChildMap.set(child.id, child);
        }
    }

    // Update or add elements
    for (const newChild of newChildren) {
        if (newChild.id && handledIds.has(newChild.id)) {
            // Already handled by dj-update, skip
            continue;
        }

        if (newChild.id) {
            const existing = existingRoot.querySelector(`#${CSS.escape(newChild.id)}`);
            if (existing) {
                // Check if this element contains dj-update children
                if (newChild.querySelector('[dj-update]')) {
                    // Recursively process
                    applyDjUpdateElements(existing, newChild);
                } else {
                    // Morph to preserve event listeners
                    morphElement(existing, newChild);
                }
            } else {
                // New element, append it
                existingRoot.appendChild(newChild.cloneNode(true));
            }
        }
    }

    // Handle elements that exist in old but not in new (remove them)
    // But preserve dj-update elements since their children are managed differently
    for (const existing of existingChildren) {
        if (existing.id && !handledIds.has(existing.id) && !newChildMap.has(existing.id)) {
            // Check if it's a dj-update element
            if (!existing.hasAttribute('dj-update')) {
                existing.remove();
            }
        }
    }
}

/**
 * Stamp data-dj-id attributes from server HTML onto existing pre-rendered DOM.
 * This avoids replacing innerHTML (which destroys whitespace in code blocks).
 * Walks both trees in parallel and copies data-dj-id from server elements to DOM elements.
 * Note: serverHtml is trusted (comes from our own WebSocket mount response).
 */
function _stampDjIds(serverHtml, container) {
    if (!container) {
        container = document.querySelector('[dj-view]') ||
                    document.querySelector('[dj-root]');
    }
    if (!container) return;

    const parser = new DOMParser();
    const doc = parser.parseFromString('<div>' + serverHtml + '</div>', 'text/html');
    const serverRoot = doc.body.firstChild;

    function stampRecursive(domNode, serverNode) {
        if (!domNode || !serverNode) return;
        if (serverNode.nodeType !== Node.ELEMENT_NODE || domNode.nodeType !== Node.ELEMENT_NODE) return;

        // Bail out if structure diverges (e.g. browser extension injected elements)
        if (domNode.tagName !== serverNode.tagName) return;

        const djId = serverNode.getAttribute('data-dj-id');
        if (djId) {
            domNode.setAttribute('data-dj-id', djId);
        }
        // Also stamp data-dj-src (template source mapping) if present
        const djSrc = serverNode.getAttribute('data-dj-src');
        if (djSrc) {
            domNode.setAttribute('data-dj-src', djSrc);
        }

        // Walk children in parallel (element nodes only)
        const domChildren = Array.from(domNode.children);
        const serverChildren = Array.from(serverNode.children);
        const len = Math.min(domChildren.length, serverChildren.length);
        for (let i = 0; i < len; i++) {
            stampRecursive(domChildren[i], serverChildren[i]);
        }
    }

    // Walk container children vs server root children
    const domChildren = Array.from(container.children);
    const serverChildren = Array.from(serverRoot.children);
    const len = Math.min(domChildren.length, serverChildren.length);
    for (let i = 0; i < len; i++) {
        stampRecursive(domChildren[i], serverChildren[i]);
    }
}

/**
 * Get significant children (elements and non-whitespace text nodes).
 * Preserves all whitespace inside <pre>, <code>, and <textarea> elements.
 */
function getSignificantChildren(node) {
    // Check if we're inside a whitespace-preserving element
    const preserveWhitespace = isWhitespacePreserving(node);

    return Array.from(node.childNodes).filter(child => {
        if (child.nodeType === Node.ELEMENT_NODE) return true;
        if (child.nodeType === Node.TEXT_NODE) {
            // Preserve all text nodes inside pre/code/textarea
            if (preserveWhitespace) return true;
            // Preserve non-breaking spaces (\u00A0) as significant, matching Rust VDOM parser.
            // Only filter out ASCII whitespace-only text nodes.
            return (/[^ \t\n\r\f]/.test(child.textContent));
        }
        return false;
    });
}

/**
 * Check if a node is a whitespace-preserving element or inside one.
 */
function isWhitespacePreserving(node) {
    const WHITESPACE_PRESERVING_TAGS = ['PRE', 'CODE', 'TEXTAREA', 'SCRIPT', 'STYLE'];
    let current = node;
    while (current) {
        if (current.nodeType === Node.ELEMENT_NODE &&
            WHITESPACE_PRESERVING_TAGS.includes(current.tagName)) {
            return true;
        }
        current = current.parentNode;
    }
    return false;
}

// Export for testing
window.djust.getSignificantChildren = getSignificantChildren;
window.djust._applySinglePatch = applySinglePatch;
window.djust._stampDjIds = _stampDjIds;
window.djust._getNodeByPath = getNodeByPath;
window.djust.createNodeFromVNode = createNodeFromVNode;
window.djust.preserveFormValues = preserveFormValues;
window.djust.morphChildren = morphChildren;
window.djust.morphElement = morphElement;

/**
 * Group patches by their parent path for batching.
 *
 * Child operations (InsertChild, RemoveChild, MoveChild) use the full path
 * as the parent key because the path points to the parent container.
 * Node-targeting operations (SetAttribute, SetText, etc.) use slice(0,-1)
 * because the path points to the node itself, and the parent is one level up.
 */
const CHILD_OPS = new Set(['InsertChild', 'RemoveChild', 'MoveChild']);
function groupPatchesByParent(patches) {
    const groups = new Map(); // Use Map to avoid prototype pollution
    for (const patch of patches) {
        const parentPath = CHILD_OPS.has(patch.type)
            ? patch.path.join('/')
            : patch.path.slice(0, -1).join('/');
        if (!groups.has(parentPath)) {
            groups.set(parentPath, []);
        }
        groups.get(parentPath).push(patch);
    }
    return groups;
}
window.djust._groupPatchesByParent = groupPatchesByParent;

/**
 * Group InsertChild patches with consecutive indices.
 * Only consecutive inserts can be batched with DocumentFragment.
 *
 * Example: [2, 3, 4, 7, 8] -> [[2,3,4], [7,8]]
 *
 * @param {Array} inserts - Array of InsertChild patches
 * @returns {Array<Array>} - Groups of consecutive inserts
 */
function groupConsecutiveInserts(inserts) {
    if (inserts.length === 0) return [];

    // Sort by index first
    inserts.sort((a, b) => a.index - b.index);

    const groups = [];
    let currentGroup = [inserts[0]];

    for (let i = 1; i < inserts.length; i++) {
        // Check if this insert is consecutive with the previous one AND targets same parent
        if (inserts[i].index === inserts[i - 1].index + 1 && inserts[i].d === inserts[i - 1].d) {
            currentGroup.push(inserts[i]);
        } else {
            // Start a new group
            groups.push(currentGroup);
            currentGroup = [inserts[i]];
        }
    }

    // Don't forget the last group
    groups.push(currentGroup);

    return groups;
}
window.djust._groupConsecutiveInserts = groupConsecutiveInserts;

/**
 * Sort patches in 4-phase order for correct DOM mutation sequencing:
 * Phase 0: RemoveChild (descending index within same parent)
 * Phase 1: MoveChild
 * Phase 2: InsertChild
 * Phase 3: SetText, SetAttribute, and other node-targeting patches
 */
function _sortPatches(patches) {
    function patchPhase(p) {
        switch (p.type) {
            case 'RemoveChild': return 0;
            case 'MoveChild':   return 1;
            case 'InsertChild': return 2;
            default:            return 3;
        }
    }
    patches.sort(function(a, b) {
        const phaseA = patchPhase(a);
        const phaseB = patchPhase(b);
        if (phaseA !== phaseB) return phaseA - phaseB;
        // Within RemoveChild phase, sort by descending index per parent
        if (phaseA === 0) {
            const pA = JSON.stringify(a.path);
            const pB = JSON.stringify(b.path);
            if (pA === pB) return b.index - a.index;
        }
        return 0;
    });
    return patches;
}
window.djust._sortPatches = _sortPatches;

/**
 * Apply a single patch operation.
 *
 * Patches include:
 * - `path`: Index-based path (fallback)
 * - `d`: Compact djust ID for O(1) querySelector lookup
 */
function applySinglePatch(patch) {
    // Use ID-based resolution (d field) with path as fallback
    const node = getNodeByPath(patch.path, patch.d);
    if (!node) {
        // Sanitize for logging (patches come from trusted server, but log defensively)
        const safePath = Array.isArray(patch.path) ? patch.path.map(Number).join('/') : 'invalid';
        console.warn(`[LiveView] Failed to find node: path=${safePath}, id=${sanitizeIdForLog(patch.d)}`);
        return false;
    }

    try {
        switch (patch.type) {
            case 'Replace':
                // Clean up poll timers before replacing (prevents orphaned intervals)
                if (node._djustPollIntervalId) {
                    clearInterval(node._djustPollIntervalId);
                    if (node._djustPollVisibilityHandler) {
                        document.removeEventListener('visibilitychange', node._djustPollVisibilityHandler);
                    }
                }
                const newNode = createNodeFromVNode(patch.node, isInSvgContext(node.parentNode));
                node.parentNode.replaceChild(newNode, node);
                break;

            case 'SetText':
                node.textContent = patch.text;
                // If this is a text node inside a textarea, also update the textarea's .value
                // (textContent alone doesn't update what's displayed in the textarea)
                if (node.parentNode && node.parentNode.tagName === 'TEXTAREA') {
                    if (document.activeElement !== node.parentNode) {
                        node.parentNode.value = patch.text;
                    }
                }
                break;

            case 'SetAttr':
                if (patch.key === 'value' && (node.tagName === 'INPUT' || node.tagName === 'TEXTAREA')) {
                    if (document.activeElement !== node) {
                        node.value = patch.value;
                    }
                    node.setAttribute(patch.key, patch.value);
                } else {
                    node.setAttribute(patch.key, patch.value);
                }
                break;

            case 'RemoveAttr':
                // Never remove dj-* event handler attributes — defense in depth
                // against VDOM path mismatches from conditional rendering.
                // Also preserve data-dj-src (template source mapping).
                if (patch.key && (patch.key.startsWith('dj-') || patch.key === 'data-dj-src')) {
                    break;
                }
                node.removeAttribute(patch.key);
                break;

            case 'InsertChild': {
                const newChild = createNodeFromVNode(patch.node, isInSvgContext(node));
                const children = getSignificantChildren(node);
                const refChild = children[patch.index];
                if (refChild) {
                    node.insertBefore(newChild, refChild);
                } else {
                    node.appendChild(newChild);
                }
                // If inserting a text node into a textarea, also update its .value
                if (newChild.nodeType === Node.TEXT_NODE && node.tagName === 'TEXTAREA') {
                    if (document.activeElement !== node) {
                        node.value = newChild.textContent || '';
                    }
                }
                break;
            }

            case 'RemoveChild': {
                const children = getSignificantChildren(node);
                const child = children[patch.index];
                if (child) {
                    const wasTextNode = child.nodeType === Node.TEXT_NODE;
                    const parentTag = node.tagName;
                    node.removeChild(child);
                    // If removing a text node from a textarea, also clear its .value
                    // (removing textContent alone doesn't update what's displayed)
                    if (wasTextNode && parentTag === 'TEXTAREA' && document.activeElement !== node) {
                        node.value = '';
                    }
                }
                break;
            }

            case 'MoveChild': {
                let child;
                if (patch.child_d) {
                    // ID-based resolution: find direct child by data-dj-id (resilient to index shifts)
                    const escaped = CSS.escape(patch.child_d);
                    child = node.querySelector(`:scope > [data-dj-id="${escaped}"]`);
                }
                if (!child) {
                    // Fallback: index-based
                    const fallbackChildren = getSignificantChildren(node);
                    child = fallbackChildren[patch.from];
                }
                if (child) {
                    const children = getSignificantChildren(node);
                    const refChild = children[patch.to];
                    if (refChild) {
                        node.insertBefore(child, refChild);
                    } else {
                        node.appendChild(child);
                    }
                }
                break;
            }

            default:
                // Sanitize type for logging
                const safeType = String(patch.type || 'undefined').slice(0, 50);
                console.warn('[LiveView] Unknown patch type:', safeType);
                return false;
        }

        return true;
    } catch (error) {
        // Log error without potentially sensitive patch data
        console.error('[LiveView] Error applying patch:', error.message || error);
        return false;
    }
}

/**
 * Apply VDOM patches with optimized batching.
 *
 * Improvements over sequential application:
 * - Groups patches by parent path for batch operations
 * - Uses DocumentFragment for consecutive InsertChild patches on same parent
 * - Skips batching overhead for small patch sets (<=10 patches)
 */
function applyPatches(patches) {
    if (!patches || patches.length === 0) {
        return true;
    }

    // Sort patches in 4-phase order for correct DOM mutation sequencing
    _sortPatches(patches);

    // For small patch sets, apply directly without batching overhead
    if (patches.length <= 10) {
        let failedCount = 0;
        for (const patch of patches) {
            if (!applySinglePatch(patch)) {
                failedCount++;
            }
        }
        if (failedCount > 0) {
            console.error(`[LiveView] ${failedCount}/${patches.length} patches failed`);
            return false;
        }
        // Update hooks and model bindings after DOM patches
        if (typeof updateHooks === 'function') { updateHooks(); }
        if (typeof bindModelElements === 'function') { bindModelElements(); }
        return true;
    }

    // For larger patch sets, use batching
    let failedCount = 0;
    let successCount = 0;

    // Group patches by parent for potential batching
    const patchGroups = groupPatchesByParent(patches);

    for (const [, group] of patchGroups) {
        // Optimization: Use DocumentFragment for consecutive InsertChild on same parent
        const insertPatches = group.filter(p => p.type === 'InsertChild');

        if (insertPatches.length >= 3) {
            // Group only consecutive inserts (can't batch non-consecutive indices)
            const consecutiveGroups = groupConsecutiveInserts(insertPatches);

            for (const consecutiveGroup of consecutiveGroups) {
                // Only batch if we have 3+ consecutive inserts
                if (consecutiveGroup.length < 3) continue;

                const firstPatch = consecutiveGroup[0];
                // Use ID-based resolution for parent node
                const parentNode = getNodeByPath(firstPatch.path, firstPatch.d);

                if (parentNode) {
                    try {
                        const fragment = document.createDocumentFragment();
                        const svgContext = isInSvgContext(parentNode);
                        for (const patch of consecutiveGroup) {
                            const newChild = createNodeFromVNode(patch.node, svgContext);
                            fragment.appendChild(newChild);
                            successCount++;
                        }

                        // Insert fragment at the first index position
                        const children = getSignificantChildren(parentNode);
                        const firstIndex = consecutiveGroup[0].index;
                        const refChild = children[firstIndex];

                        if (refChild) {
                            parentNode.insertBefore(fragment, refChild);
                        } else {
                            parentNode.appendChild(fragment);
                        }

                        // Mark these patches as processed
                        const processedSet = new Set(consecutiveGroup);
                        for (let i = group.length - 1; i >= 0; i--) {
                            if (processedSet.has(group[i])) {
                                group.splice(i, 1);
                            }
                        }
                    } catch (error) {
                        console.error('[LiveView] Batch insert failed, falling back to individual patches:', error.message);
                        // On failure, patches remain in group for individual processing
                        successCount -= consecutiveGroup.length;  // Undo count
                    }
                }
            }
        }

        // Apply remaining patches individually
        for (const patch of group) {
            if (applySinglePatch(patch)) {
                successCount++;
            } else {
                failedCount++;
            }
        }
    }

    if (failedCount > 0) {
        console.error(`[LiveView] ${failedCount}/${patches.length} patches failed`);
        return false;
    }

    // Update hooks and model bindings after DOM patches
    if (typeof updateHooks === 'function') { updateHooks(); }
    if (typeof bindModelElements === 'function') { bindModelElements(); }

    return true;
}
