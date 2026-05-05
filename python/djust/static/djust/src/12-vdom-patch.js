
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
 * Returns true if a comment node's text content matches the dj-if family
 * preserved by the server's VDOM parser. Mirrors
 * `crates/djust_vdom/src/parser.rs:494-499` so client-side path-fallback
 * traversal counts the same comments the server emits.
 *
 * Accepts:
 *   - exact `dj-if` (legacy single-comment placeholder for false-no-else
 *     pure-text conditionals — issue #295)
 *   - `dj-if<space-or-tab>...` (boundary-marker opening, e.g.
 *     `dj-if id="if-0"` or `dj-if id="if-a3b1c2d4-0"` after the Stage 11
 *     prefix fix on PR #1363)
 *   - `/dj-if` (boundary-marker closing)
 *
 * Rejects lookalikes like `dj-iffy`, `dj-if-extra`, `dj-ifid="x"`, etc.
 *
 * @param {string} text - The comment's textContent (may contain leading/
 *   trailing whitespace; trim() is applied internally to match the
 *   server's `.trim()`).
 * @returns {boolean}
 */
function isDjIfComment(text) {
    if (typeof text !== 'string') return false;
    const trimmed = text.trim();
    if (trimmed === 'dj-if') return true;
    if (trimmed === '/dj-if') return true;
    // Boundary-open marker: `dj-if<space-or-tab>...`. Crucially must
    // NOT match `dj-iffy`, `dj-if-extra`, `dj-ifid=...` — only a literal
    // space or tab after `dj-if` qualifies, mirroring the server predicate.
    return trimmed.startsWith('dj-if ') || trimmed.startsWith('dj-if\t');
}

/**
 * Save the current focus state (active element, selection, scroll position).
 * Call before DOM mutations that may destroy focus. Pairs with restoreFocusState().
 *
 * @param {HTMLElement|null} [rootEl=null] — optional scoping root. When
 *   provided, the positional-fallback index is computed relative to this
 *   root (used by scoped sticky patch application so the focus index for
 *   a child view doesn't collide with the parent view's positional
 *   indices).
 * @returns {Object|null} Saved focus state, or null if no form element is focused.
 */
function saveFocusState(rootEl = null) {
    const active = document.activeElement;
    if (!active || active === document.body || active === document.documentElement) {
        return null;
    }

    // Only save state for form elements and contenteditable
    const isFormEl = (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA' || active.tagName === 'SELECT');
    const isEditable = active.isContentEditable;
    if (!isFormEl && !isEditable) {
        return null;
    }

    // Skip saving during broadcast updates — remote content should take effect.
    if (_isBroadcastUpdate) {
        return null;
    }

    const state = { tag: active.tagName };

    // Build a matching key: prefer id, then name, then dj-id, then positional index
    if (active.id) {
        state.findBy = 'id';
        state.key = active.id;
    } else if (active.name) {
        state.findBy = 'name';
        state.key = active.name;
    } else if (active.getAttribute && active.getAttribute('dj-id')) {
        state.findBy = 'dj-id';
        state.key = active.getAttribute('dj-id');
    } else {
        // Positional: index among same-tag siblings in the nearest dj-view.
        // Sticky patch applier passes rootEl so the index is scoped to the
        // sticky subtree, not the whole document.
        state.findBy = 'index';
        const root = rootEl || active.closest('[dj-view]') || document.body;
        const siblings = root.querySelectorAll(active.tagName.toLowerCase());
        state.key = Array.from(siblings).indexOf(active);
    }

    // Save value and selection state
    if (active.tagName === 'TEXTAREA' || (active.tagName === 'INPUT' && !['checkbox', 'radio'].includes(active.type))) {
        state.selStart = active.selectionStart;
        state.selEnd = active.selectionEnd;
        state.scrollTop = active.scrollTop;
        state.scrollLeft = active.scrollLeft;
    }

    return state;
}

/**
 * Restore focus state saved by saveFocusState().
 * Re-finds the element in the DOM (it may have been replaced) and restores
 * focus, selection range, and scroll position.
 *
 * @param {Object|null} state - Saved state from saveFocusState()
 * @param {HTMLElement|null} [rootEl=null] — optional scoping root. When
 *   provided, the lookup queries against ``rootEl`` instead of
 *   ``document``, so sticky/child patches don't resurrect a matching
 *   id-carrying element outside their own subtree.
 */
function restoreFocusState(state, rootEl = null) {
    if (!state) return;

    const scope = rootEl || document;
    let el = null;
    if (state.findBy === 'id') {
        el = (rootEl && rootEl.querySelector)
            ? rootEl.querySelector('#' + CSS.escape(state.key))
            : document.getElementById(state.key);
    } else if (state.findBy === 'name') {
        el = scope.querySelector(`[name="${CSS.escape(state.key)}"]`);
    } else if (state.findBy === 'dj-id') {
        el = scope.querySelector(`[dj-id="${CSS.escape(state.key)}"]`);
    } else {
        // Positional fallback — scoped to rootEl when provided.
        const root = rootEl || document.querySelector('[dj-view]') || document.body;
        const candidates = root.querySelectorAll(state.tag.toLowerCase());
        el = candidates[state.key] || null;
    }

    if (!el) return;

    // Re-focus the element (won't re-trigger focus event if already focused)
    if (document.activeElement !== el) {
        el.focus({ preventScroll: true });
    }

    // Restore selection range for text inputs/textareas
    if (state.selStart !== undefined && typeof el.setSelectionRange === 'function') {
        try {
            el.setSelectionRange(state.selStart, state.selEnd);
        } catch (_e) {
            // setSelectionRange throws on some input types (email, number)
        }
    }

    // Restore scroll position within the element
    if (state.scrollTop !== undefined) {
        el.scrollTop = state.scrollTop;
        el.scrollLeft = state.scrollLeft;
    }
}

/**
 * Resolve a DOM node using ID-based lookup (primary) or path traversal (fallback).
 *
 * Resolution strategy:
 * 1. If djustId is provided, try querySelector('[dj-id="..."]') - O(1), reliable
 * 2. Fall back to index-based path traversal
 *
 * @param {Array<number>} path - Index-based path (fallback)
 * @param {string|null} djustId - Compact djust ID for direct lookup (e.g., "1a")
 * @param {HTMLElement|null} [rootEl=null] — optional scoping root. When
 *   provided, both the dj-id lookup and the path traversal are scoped to
 *   ``rootEl``. Sticky LiveViews (Phase B) pass the sticky subtree here
 *   so a child's dj-id doesn't match the parent's.
 * @returns {Node|null} - Found node or null
 */
function getNodeByPath(path, djustId = null, rootEl = null) {
    // Strategy 1: ID-based resolution (fast, reliable)
    if (djustId) {
        const scope = rootEl || document;
        const byId = scope.querySelector(`[dj-id="${CSS.escape(djustId)}"]`);
        if (byId) {
            return byId;
        }
        // ID not found - fall through to path-based
        if (globalThis.djustDebug || window.DEBUG_MODE) {
            // Log without user data to avoid log injection
            if (globalThis.djustDebug) console.log('[LiveView] ID lookup failed, trying path fallback');
        }
    }

    // Strategy 2: Index-based path traversal (fallback)
    let node = rootEl || getLiveViewRoot();

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
            // Only include `dj-if`-family comments — the Rust VDOM
            // parser preserves these for diffing stability (#559) and for
            // boundary markers (#1358 Iter 1) but drops all other HTML
            // comments. Regular comments (<!-- Hero Section --> etc.) must
            // be excluded to keep path indices aligned. The predicate
            // mirrors `crates/djust_vdom/src/parser.rs:494-499`.
            if (child.nodeType === Node.COMMENT_NODE) {
                return isDjIfComment(child.textContent);
            }
            return false;
        });

        if (index >= children.length) {
            if (globalThis.djustDebug || window.DEBUG_MODE) {
                // Explicit number coercion for safe logging
                const safeIndex = Number(index) || 0;
                const safeLen = Number(children.length) || 0;
                const parentTag = node.tagName || '#text';
                const parentId = node.getAttribute ? (node.getAttribute('dj-id') || node.id || '') : '';
                const parentDesc = parentId ? `${parentTag}#${parentId}` : parentTag;
                console.warn(`[LiveView] Path traversal failed at index ${safeIndex}, only ${safeLen} children (parent: ${parentDesc}). The DOM may have been modified by third-party JS, or a {% if %} block changed the node count.`);
            }
            return null;
        }

        // eslint-disable-next-line security/detect-object-injection
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
    // eslint-disable-next-line security/detect-object-injection
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
    // eslint-disable-next-line security/detect-object-injection
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
    // Handle comment nodes — Rust emits <!--dj-if--> placeholders for
    // {% if %} blocks that evaluate to False (#559).
    if (vnode.tag === '#comment') {
        return document.createComment(vnode.text || '');
    }

    // Validate tag name against whitelist (security: prevents script injection)
    // Convert to lowercase for consistent matching
    const tagLower = String(vnode.tag || '').toLowerCase();

    // Check if tag is in our whitelists
    const isSvgTag = SVG_TAGS.has(tagLower);
    const isAllowedHtml = ALLOWED_HTML_TAGS.has(tagLower);
    // (#1255) Web Components: per the HTML spec, custom elements MUST contain
    // a hyphen in their tag name. The hyphen rule is a safe, spec-grounded
    // discriminator — `document.createElement` rejects malformed tag names
    // outright, and the server is the source of truth for emitted markup
    // (standard LiveView trust model). This unblocks Shoelace, Lit, Stencil,
    // model-viewer, etc. without weakening the allow-listed core tags.
    const isCustomElement = tagLower.includes('-');
    // (#1255) Optional opt-in extension hook for non-hyphenated proprietary
    // tags (rare). App code can populate `window.djustAllowedTags` with a
    // Set of additional tag names to allow.
    const isUserAllowed = typeof window !== 'undefined'
        && window.djustAllowedTags
        && typeof window.djustAllowedTags.has === 'function'
        && window.djustAllowedTags.has(tagLower);

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
    } else if (isCustomElement || isUserAllowed) {
        // (#1255) Web Component or user-allow-listed tag. The browser's
        // createElement validates the tag name format — invalid tag names
        // throw `InvalidCharacterError`, which is a hard failure rather
        // than a silent bypass. Wrap in try/catch so a malformed tag still
        // falls back to <span> safely.
        try {
            elem = document.createElement(tagLower);
        } catch (_e) {
            if (globalThis.djustDebug) {
                console.warn('[LiveView] createElement threw for tag %s; using span placeholder', tagLower);
            }
            elem = document.createElement('span');
        }
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
            } else if (key === 'checked' && elem.tagName === 'INPUT') {
                elem.checked = true;
            } else if (key === 'selected' && elem.tagName === 'OPTION') {
                elem.selected = true;
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
 *
 * `let` (NOT const) — 02-response-handler.js reassigns on broadcast
 * frames; ESLint's per-file analysis can't see the cross-module
 * reassignment (#1351).
 */
// eslint-disable-next-line prefer-const
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
        saved = { tag: active.tagName.toLowerCase(), originalName: active.name };
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
                try { el.setSelectionRange(saved.selStart, saved.selEnd); } catch (_e) { /* */ }
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
        // eslint-disable-next-line security/detect-object-injection
        while (eIdx < existingNodes.length && matched.has(existingNodes[eIdx])) {
            eIdx++;
        }
        // eslint-disable-next-line security/detect-object-injection
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
            if (node.nodeType === Node.ELEMENT_NODE
                && globalThis.djust
                && typeof globalThis.djust.maybeDeferRemoval === 'function'
                && globalThis.djust.maybeDeferRemoval(node)) {
                continue;
            }
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
        // Clean up scoped (window/document) listeners before replacing
        _cleanupScopedListeners(existing);
        if (globalThis.djust && typeof globalThis.djust.maybeDeferRemoval === 'function'
            && existing.nodeType === Node.ELEMENT_NODE
            && existing.hasAttribute('dj-remove')) {
            // If a removal is already pending for this element, the replacement
            // node was inserted by the prior patch — skip to avoid duplicates.
            const alreadyPending = globalThis.djust.djRemove
                && globalThis.djust.djRemove._pendingRemovals
                && globalThis.djust.djRemove._pendingRemovals.has(existing);
            if (alreadyPending) {
                return;
            }
            const newNode = desired.cloneNode(true);
            existing.parentNode.insertBefore(newNode, existing);
            if (globalThis.djust.maybeDeferRemoval(existing)) {
                return;
            }
            // Declined — fall through to the normal replace (newNode
            // already inserted, drop duplicate first).
            existing.parentNode.removeChild(newNode);
        }
        existing.parentNode.replaceChild(desired.cloneNode(true), existing);
        return;
    }

    // dj-update="ignore" — skip entirely
    if (existing.getAttribute('dj-update') === 'ignore') {
        return;
    }

    // --- Sync attributes ---
    // Remove attributes not present in desired.
    // Exception: canvas width/height are set by scripts (e.g. Chart.js) and must
    // not be removed — doing so resets the canvas context and clears drawn content.
    // Also: attributes listed in dj-ignore-attrs are client-owned — the morph
    // path must not remove or overwrite them (mirrors the SetAttr guard at
    // line ~1199, which only covered individual attribute patches).
    const isCanvas = existing.tagName === 'CANVAS';
    const _isIgnored = (globalThis.djust && typeof globalThis.djust.isIgnoredAttr === 'function')
        ? globalThis.djust.isIgnoredAttr
        : null;
    for (let i = existing.attributes.length - 1; i >= 0; i--) {
        // eslint-disable-next-line security/detect-object-injection
        const name = existing.attributes[i].name;
        if (!desired.hasAttribute(name)) {
            if (isCanvas && (name === 'width' || name === 'height')) continue;
            if (_isIgnored && _isIgnored(existing, name)) continue;
            existing.removeAttribute(name);
        }
    }
    // Set/update attributes from desired
    for (const attr of desired.attributes) {
        if (_isIgnored && _isIgnored(existing, attr.name)) continue;
        if (existing.getAttribute(attr.name) !== attr.value) {
            existing.setAttribute(attr.name, attr.value);
        }
    }

    // --- Form element value sync ---
    const isFocused = document.activeElement === existing;
    // Skip value sync for focused inputs to preserve what the user is typing,
    // UNLESS the input's identity changed (different name = different field).
    const nameChanged = existing.getAttribute('name') !== desired.getAttribute('name');
    const skipValue = isFocused && !_isBroadcastUpdate && !nameChanged;

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
                            djLog(`[LiveView:dj-update] Appended #${newChild.id} to #${elementId}`);
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
                            djLog(`[LiveView:dj-update] Prepended #${newChild.id} to #${elementId}`);
                        }
                    }
                }
                break;
            }

            case 'ignore':
                // Don't update this element at all
                if (globalThis.djustDebug) {
                    djLog(`[LiveView:dj-update] Ignoring #${elementId}`);
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
                if (globalThis.djust
                    && typeof globalThis.djust.maybeDeferRemoval === 'function'
                    && globalThis.djust.maybeDeferRemoval(existing)) {
                    continue;
                }
                existing.remove();
            }
        }
    }
}

/**
 * Stamp dj-id attributes from server HTML onto existing pre-rendered DOM.
 * This avoids replacing innerHTML (which destroys whitespace in code blocks).
 * Walks both trees in parallel and copies dj-id from server elements to DOM elements.
 * Note: serverHtml is trusted (comes from our own WebSocket mount response).
 */
function _stampDjIds(serverHtml, container) {
    if (!container) {
        container = document.querySelector('[dj-view]') ||
                    document.querySelector('[dj-root]');
    }
    if (!container) return;

    const parser = new DOMParser();
    // codeql[js/xss] -- serverHtml is rendered by the trusted Django/Rust template engine
    const doc = parser.parseFromString('<div>' + serverHtml + '</div>', 'text/html');
    const serverRoot = doc.body.firstChild;

    function stampRecursive(domNode, serverNode) {
        if (!domNode || !serverNode) return;
        if (serverNode.nodeType !== Node.ELEMENT_NODE || domNode.nodeType !== Node.ELEMENT_NODE) return;

        // Bail out if structure diverges (e.g. browser extension injected elements)
        if (domNode.tagName !== serverNode.tagName) return;

        const djId = serverNode.getAttribute('dj-id');
        if (djId) {
            domNode.setAttribute('dj-id', djId);
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
            // eslint-disable-next-line security/detect-object-injection
            stampRecursive(domChildren[i], serverChildren[i]);
        }
    }

    // Walk container children vs server root children
    const domChildren = Array.from(container.children);
    const serverChildren = Array.from(serverRoot.children);
    const len = Math.min(domChildren.length, serverChildren.length);
    for (let i = 0; i < len; i++) {
        // eslint-disable-next-line security/detect-object-injection
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
        // Include comment nodes — the Rust VDOM parser preserves <!--dj-if-->
        // placeholders and counts them in child indices (#559).
        if (child.nodeType === Node.COMMENT_NODE) return true;
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

// ============================================================================
// dj-if subtree patch helpers — Foundation 2 of #1358 (Iter 2)
// ----------------------------------------------------------------------------
// The server (Iter 1) emits `<!--dj-if id="if-<8hex>-N"-->...<!--/dj-if-->`
// boundary markers around `{% if %}` block contents. The differ (Iter 3)
// will emit `RemoveSubtree` / `InsertSubtree` patches when conditionals
// flip. The handlers below dispatch those patch types.
//
// Until Iter 3 lands, no live frame contains these patch types — so this
// is zero-observable-behavior. The handlers exist so the next milestone
// can wire the differ without a coordinated client+server release.
// ============================================================================

/**
 * Extract the `id="..."` value from a dj-if open-marker comment body.
 *
 * Mirrors the format emitted by Iter 1's parser: e.g.
 * `dj-if id="if-a3b1c2d4-0"`. Returns `null` if the comment doesn't
 * contain an id token (e.g. legacy bare `dj-if` placeholder, #295).
 *
 * @param {string} text — comment textContent (already trimmed, since
 *   the open marker family is matched via `isDjIfComment`).
 * @returns {string|null}
 */
function _extractDjIfMarkerId(text) {
    if (typeof text !== 'string') return null;
    // Match id="..."  (only the open marker carries it; the close is /dj-if).
    // Quoted only — server emits double-quotes per parser.rs.
    const match = /id="([^"]+)"/.exec(text);
    return match ? match[1] : null;
}

/**
 * Walk the DOM (or a scoped root) and find the open-marker comment node
 * whose embedded id matches `targetId`.
 *
 * Uses a TreeWalker filtered to comment nodes for cheap traversal.
 * Reuses `isDjIfComment` to ignore non-dj-if comments.
 *
 * @param {string} targetId — the id substring to match (e.g. `"if-abc-0"`).
 * @param {Node} [root=document.body] — scoping root for the search.
 * @returns {Comment|null}
 */
function _findDjIfOpenMarker(targetId, root) {
    const scopeRoot = root || document.body;
    if (!scopeRoot) return null;
    const walker = document.createTreeWalker(scopeRoot, NodeFilter.SHOW_COMMENT, null);
    let n = walker.nextNode();
    while (n) {
        const text = n.textContent || '';
        if (isDjIfComment(text)) {
            const id = _extractDjIfMarkerId(text.trim());
            if (id === targetId) return n;
        }
        n = walker.nextNode();
    }
    return null;
}

/**
 * Given an open-marker comment, find its matching close marker comment
 * by scanning forward through *sibling-order* DOM nodes and counting
 * `dj-if` opens / `/dj-if` closes.
 *
 * Handles nesting correctly: an inner open/close pair inside the
 * targeted subtree increments and decrements the depth counter without
 * ever returning to zero, so the outer close is the one returned.
 *
 * Uses a TreeWalker rooted at `document.body` (or the marker's
 * common ancestor if unconnected) and advances forward until depth
 * returns to 0.
 *
 * @param {Comment} openMarker — the matched open-marker comment node.
 * @returns {Comment|null} — the matching close marker, or null if
 *   none was found (malformed pairing — caller should warn + abort).
 */
function _findDjIfCloseMarker(openMarker) {
    if (!openMarker || !openMarker.parentNode) return null;
    // Walk only forward in document order — TreeWalker's currentNode
    // anchored at the open marker, then nextNode() until depth==0 on
    // a close.
    const root = openMarker.ownerDocument && openMarker.ownerDocument.body
        ? openMarker.ownerDocument.body
        : openMarker.parentNode;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_COMMENT, null);
    walker.currentNode = openMarker;
    let depth = 1;
    let n = walker.nextNode();
    while (n) {
        const trimmed = (n.textContent || '').trim();
        // Bare 'dj-if' is a legacy single-comment placeholder (#295) — it
        // does NOT participate in open/close pairing. Only the boundary
        // forms (`dj-if<space-or-tab>...` and `/dj-if`) bracket subtrees.
        if (trimmed === '/dj-if') {
            depth -= 1;
            if (depth === 0) return n;
        } else if (trimmed.startsWith('dj-if ') || trimmed.startsWith('dj-if\t')) {
            depth += 1;
        }
        n = walker.nextNode();
    }
    return null;
}

/**
 * Remove every node between (and including) the open and close marker
 * comments, in sibling order.
 *
 * The open and close are guaranteed to share the same parent, since the
 * Rust VDOM parser only emits boundary markers around `{% if %}` block
 * children inside a single parent context. Walks the open marker's
 * parent's children, collecting from `openMarker` through `closeMarker`
 * inclusive, and removes them.
 *
 * @param {Comment} openMarker
 * @param {Comment} closeMarker
 */
function _removeDjIfBracketedRange(openMarker, closeMarker) {
    const parent = openMarker.parentNode;
    if (!parent) return;
    const toRemove = [];
    let cursor = openMarker;
    while (cursor) {
        toRemove.push(cursor);
        if (cursor === closeMarker) break;
        cursor = cursor.nextSibling;
    }
    for (const node of toRemove) {
        if (node.parentNode === parent) {
            parent.removeChild(node);
        }
    }
}

/**
 * Apply a `RemoveSubtree` patch: locate the dj-if marker pair by id and
 * remove the bracketed range (markers + everything between).
 *
 * @param {Object} patch — `{type: 'RemoveSubtree', id: '...'}`.
 * @param {HTMLElement|null} rootEl — optional scoping root.
 * @returns {boolean} true on success, false if the marker wasn't found.
 */
function applyRemoveSubtree(patch, rootEl = null) {
    const targetId = String(patch.id || '');
    if (!targetId) {
        console.warn('[LiveView] RemoveSubtree patch missing id, skipping');
        return false;
    }
    const open = _findDjIfOpenMarker(targetId, rootEl);
    if (!open) {
        console.warn('[LiveView] RemoveSubtree: open marker not found id=%s', sanitizeIdForLog(targetId));
        return false;
    }
    const close = _findDjIfCloseMarker(open);
    if (!close) {
        console.warn('[LiveView] RemoveSubtree: close marker not found id=%s', sanitizeIdForLog(targetId));
        return false;
    }
    _removeDjIfBracketedRange(open, close);
    return true;
}

/**
 * Parse a server-emitted HTML fragment into a DocumentFragment using a
 * `<template>` element so any `<script>` tags inside are inert (not
 * executed). The fragment is the trust-boundary's responsibility — the
 * server is authoritative for HTML content; this just guarantees that
 * even if the server emits a script tag inadvertently, it doesn't run.
 *
 * @param {string} html
 * @returns {DocumentFragment}
 */
function _parseSubtreeHtml(html) {
    const tpl = document.createElement('template');
    tpl.innerHTML = String(html || '');
    return tpl.content;
}

/**
 * Apply an `InsertSubtree` patch: parse the server-emitted HTML
 * fragment (which carries its own `<!--dj-if id="..."-->...
 * <!--/dj-if-->` marker pair + content) and insert at `parent` /
 * `index`.
 *
 * Uses Shape A (server emits the full marker pair). Patch shape:
 *   {type: 'InsertSubtree', id: '...', html: '...',
 *    path: [parent path], index: N, d: <parent dj-id?>}
 *
 * @param {Object} patch
 * @param {HTMLElement|null} rootEl — optional scoping root.
 * @returns {boolean}
 */
function applyInsertSubtree(patch, rootEl = null) {
    if (typeof patch.html !== 'string' || !patch.html) {
        console.warn('[LiveView] InsertSubtree patch missing html, skipping');
        return false;
    }
    // Resolve the parent node via the same path/d resolution other
    // child-ops use.
    const parent = getNodeByPath(patch.path, patch.d, rootEl);
    if (!parent) {
        console.warn('[LiveView] InsertSubtree: parent not found path=%s id=%s',
            Array.isArray(patch.path) ? patch.path.map(Number).join('/') : 'invalid',
            sanitizeIdForLog(patch.id));
        return false;
    }
    if (parent.nodeType !== 1) {
        if (globalThis.djustDebug) {
            console.log('[LiveView] InsertSubtree: parent is non-element (nodeType=%d), skipping', parent.nodeType);
        }
        return false;
    }
    const fragment = _parseSubtreeHtml(patch.html);
    // Determine insert position: index counted against significant
    // children (matches InsertChild semantics).
    const children = getSignificantChildren(parent);
    const refChild = (typeof patch.index === 'number' ? children[patch.index] : null) || null;
    if (refChild) {
        parent.insertBefore(fragment, refChild);
    } else {
        parent.appendChild(fragment);
    }
    return true;
}

// Export for testing
window.djust._applyRemoveSubtree = applyRemoveSubtree;
window.djust._applyInsertSubtree = applyInsertSubtree;
window.djust._findDjIfOpenMarker = _findDjIfOpenMarker;
window.djust._findDjIfCloseMarker = _findDjIfCloseMarker;
window.djust._extractDjIfMarkerId = _extractDjIfMarkerId;

// Export for testing
window.djust.getSignificantChildren = getSignificantChildren;
window.djust._applySinglePatch = applySinglePatch;
window.djust._stampDjIds = _stampDjIds;
window.djust._getNodeByPath = getNodeByPath;
window.djust._isDjIfComment = isDjIfComment;
window.djust.createNodeFromVNode = createNodeFromVNode;
window.djust.preserveFormValues = preserveFormValues;
window.djust.saveFocusState = saveFocusState;
window.djust.restoreFocusState = restoreFocusState;
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
        // eslint-disable-next-line security/detect-object-injection
        if (inserts[i].index === inserts[i - 1].index + 1 && inserts[i].d === inserts[i - 1].d) {
            // eslint-disable-next-line security/detect-object-injection
            currentGroup.push(inserts[i]);
        } else {
            // Start a new group
            groups.push(currentGroup);
            // eslint-disable-next-line security/detect-object-injection
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
function applySinglePatch(patch, rootEl = null) {
    // dj-if subtree patches (Foundation 2 of #1358) are dispatched by
    // marker id, not by path/d resolution. Short-circuit before the
    // generic `getNodeByPath` call so the dispatcher doesn't try to
    // resolve a non-applicable path.
    if (patch && (patch.type === 'RemoveSubtree' || patch.type === 'InsertSubtree')) {
        try {
            if (patch.type === 'RemoveSubtree') {
                return applyRemoveSubtree(patch, rootEl);
            }
            return applyInsertSubtree(patch, rootEl);
        } catch (error) {
            console.error('[LiveView] Error applying subtree patch:', error.message || error);
            return false;
        }
    }
    // Use ID-based resolution (d field) with path as fallback.
    // rootEl is threaded in by the scoped applier (Sticky LiveViews
    // Phase B) so child / sticky patches don't resolve against the
    // parent view's dj-id namespace.
    const node = getNodeByPath(patch.path, patch.d, rootEl);
    // v0.7.0 — {% dj_activity %} gate. If the target node lives inside
    // a HIDDEN activity wrapper that is NOT eager, we intentionally
    // skip the subtree patch so local DOM state (form values, scroll
    // offsets, transient JS state) is preserved across show/hide
    // cycles. The server is the canonical source of visibility, so the
    // next render after the activity is shown will re-sync state.
    if (node && node.nodeType === 1 && node.closest) {
        const hiddenActivity = node.closest('[data-djust-activity][hidden]:not([data-djust-eager="true"])');
        if (hiddenActivity) {
            if (globalThis.djustDebug) {
                console.log('[LiveView:activity] skipping patch inside hidden activity:', hiddenActivity.getAttribute('data-djust-activity'), patch.type);
            }
            return true;
        }
    }
    if (!node) {
        // Sanitize for logging (patches come from trusted server, but log defensively)
        const safePath = Array.isArray(patch.path) ? patch.path.map(Number).join('/') : 'invalid';
        const patchType = String(patch.type || 'Unknown');
        console.warn('[LiveView] Patch failed (%s): node not found at path=%s, dj-id=%s', patchType, safePath, sanitizeIdForLog(patch.d));
        if (window.DEBUG_MODE) {
            console.groupCollapsed('[LiveView] Patch detail (%s)', patchType);
            if (globalThis.djustDebug) console.log('[LiveView] Full patch object:', JSON.stringify(patch));
            if (globalThis.djustDebug) console.log('[LiveView] Suggested causes:\n  - The DOM may have been modified by third-party JS\n  - A template {% if %} block may have changed the node count\n  - A conditional rendering path produced a different DOM structure');
            console.groupEnd();
        }
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
                if (node.nodeType === Node.ELEMENT_NODE
                    && globalThis.djust
                    && typeof globalThis.djust.maybeDeferRemoval === 'function'
                    && node.hasAttribute('dj-remove')) {
                    // If a removal is already pending for this element, the
                    // replacement node was inserted by the prior patch — skip
                    // to avoid duplicates.
                    const alreadyPending = globalThis.djust.djRemove
                        && globalThis.djust.djRemove._pendingRemovals
                        && globalThis.djust.djRemove._pendingRemovals.has(node);
                    if (alreadyPending) {
                        break;
                    }
                    node.parentNode.insertBefore(newNode, node);
                    if (globalThis.djust.maybeDeferRemoval(node)) {
                        break;
                    }
                    // Declined — drop the pre-inserted duplicate and fall
                    // through to the normal replace path.
                    node.parentNode.removeChild(newNode);
                }
                node.parentNode.replaceChild(newNode, node);
                break;

            case 'SetText': {
                const safeText = String(patch.text);
                node.textContent = safeText;
                // If this is a text node inside a textarea, also update the textarea's .value
                // (textContent alone doesn't update what's displayed in the textarea)
                if (node.parentNode && node.parentNode.tagName === 'TEXTAREA') {
                    if (document.activeElement !== node.parentNode) {
                        node.parentNode.value = safeText;
                    }
                }
                break;
            }

            case 'SetAttr': {
                // Guard: element-only methods (setAttribute) don't exist on text/comment nodes (#622)
                if (node.nodeType !== 1) {
                    if (globalThis.djustDebug) console.log('[LiveView] Patch %s targets non-element (nodeType=%d), skipping', patch.type, node.nodeType);
                    return false;
                }
                // Sanitize key to prevent prototype pollution
                const attrKey = String(patch.key);
                if (UNSAFE_KEYS.includes(attrKey)) break;
                // dj-ignore-attrs: element opts out of server updates for this key
                // (e.g. <dialog dj-ignore-attrs="open">).
                if (globalThis.djust && typeof globalThis.djust.isIgnoredAttr === 'function' &&
                    globalThis.djust.isIgnoredAttr(node, attrKey)) {
                    if (globalThis.djustDebug) {
                        console.debug('[LiveView] Skipped SetAttr on ignored attr %s', attrKey);
                    }
                    break;
                }
                const attrVal = String(patch.value != null ? patch.value : '');
                if (attrKey === 'value' && (node.tagName === 'INPUT' || node.tagName === 'TEXTAREA')) {
                    if (document.activeElement !== node) {
                        node.value = attrVal;
                    }
                    node.setAttribute(attrKey, attrVal);
                } else if (attrKey === 'name' && (node.tagName === 'INPUT' || node.tagName === 'TEXTAREA' || node.tagName === 'SELECT')) {
                    // Input name changed = different field. Clear the value
                    // so the old field's content doesn't leak into the new field.
                    node.setAttribute(attrKey, attrVal);
                    const serverValue = node.getAttribute('value') || '';
                    node.value = serverValue;
                } else if (attrKey === 'checked' && node.tagName === 'INPUT') {
                    node.checked = true;
                    node.setAttribute('checked', '');
                } else if (attrKey === 'selected' && node.tagName === 'OPTION') {
                    node.selected = true;
                    node.setAttribute('selected', '');
                } else {
                    node.setAttribute(attrKey, attrVal);
                }
                break;
            }

            case 'RemoveAttr': {
                // Guard: element-only methods (removeAttribute) don't exist on text/comment nodes (#622)
                if (node.nodeType !== 1) {
                    if (globalThis.djustDebug) console.log('[LiveView] Patch %s targets non-element (nodeType=%d), skipping', patch.type, node.nodeType);
                    return false;
                }
                const removeKey = String(patch.key);
                // Never remove dj-* event handler attributes — defense in depth
                // against VDOM path mismatches from conditional rendering.
                // Also preserve data-dj-src (template source mapping).
                if (removeKey.startsWith('dj-') || removeKey === 'data-dj-src') {
                    break;
                }
                if (UNSAFE_KEYS.includes(removeKey)) break;
                if (removeKey === 'checked' && node.tagName === 'INPUT') {
                    node.checked = false;
                } else if (removeKey === 'selected' && node.tagName === 'OPTION') {
                    node.selected = false;
                }
                node.removeAttribute(removeKey);
                break;
            }

            case 'InsertChild': {
                // Guard: element-only methods (querySelector, tagName) don't exist on text/comment nodes (#622)
                if (node.nodeType !== 1) {
                    if (globalThis.djustDebug) console.log('[LiveView] Patch %s targets non-element (nodeType=%d), skipping', patch.type, node.nodeType);
                    return false;
                }
                const newChild = createNodeFromVNode(patch.node, isInSvgContext(node));
                // Guard: <select> only accepts <option>/<optgroup> as direct children.
                // When an adjacent {% if %} block expands, the server may resolve the
                // parent path to the <select> element rather than the surrounding
                // container, causing new sibling nodes to be inserted inside the
                // <select>.  Detect this mismatch and redirect the insert to the
                // correct parent so the new node becomes a sibling of <select>.
                let insertTarget = node;
                let insertRefChild = null;
                const isSelectNode = node.nodeType === Node.ELEMENT_NODE && node.tagName === 'SELECT';
                const newChildIsOption = newChild.nodeType === Node.ELEMENT_NODE &&
                    (newChild.tagName === 'OPTION' || newChild.tagName === 'OPTGROUP');
                if (isSelectNode && !newChildIsOption) {
                    // Redirect: insert as sibling of <select> instead of inside it
                    insertTarget = node.parentNode;
                    insertRefChild = node.nextSibling;
                    if (globalThis.djustDebug) {
                        djLog('[LiveView] InsertChild redirected: non-option child into SELECT parent');
                    }
                } else {
                    if (patch.ref_d) {
                        // ID-based resolution: find sibling by dj-id (resilient to index shifts)
                        const escaped = CSS.escape(patch.ref_d);
                        insertRefChild = node.querySelector(`:scope > [dj-id="${escaped}"]`);
                    }
                    if (!insertRefChild) {
                        // Fallback: index-based
                        const children = getSignificantChildren(node);
                        insertRefChild = children[patch.index] || null;
                    }
                }
                if (insertRefChild) {
                    insertTarget.insertBefore(newChild, insertRefChild);
                } else {
                    insertTarget.appendChild(newChild);
                }
                // If inserting a text node into a textarea, also update its .value
                if (newChild.nodeType === Node.TEXT_NODE && node.tagName === 'TEXTAREA') {
                    if (document.activeElement !== node) {
                        node.value = String(newChild.textContent || '');
                    }
                }
                break;
            }

            case 'RemoveChild': {
                // Guard: element-only methods (querySelector, tagName) don't exist on text/comment nodes (#622)
                if (node.nodeType !== 1) {
                    if (globalThis.djustDebug) console.log('[LiveView] Patch %s targets non-element (nodeType=%d), skipping', patch.type, node.nodeType);
                    return false;
                }
                let child = null;
                if (patch.child_d) {
                    // ID-based resolution: find child by dj-id (resilient to index shifts)
                    const escaped = CSS.escape(patch.child_d);
                    child = node.querySelector(`:scope > [dj-id="${escaped}"]`);
                }
                if (!child) {
                    // Fallback: index-based
                    const children = getSignificantChildren(node);
                    child = children[patch.index] || null;
                }
                if (child) {
                    const wasTextNode = child.nodeType === Node.TEXT_NODE;
                    const parentTag = node.tagName;
                    if (!wasTextNode
                        && child.nodeType === Node.ELEMENT_NODE
                        && globalThis.djust
                        && typeof globalThis.djust.maybeDeferRemoval === 'function'
                        && globalThis.djust.maybeDeferRemoval(child)) {
                        break;
                    }
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
                // Guard: element-only methods (querySelector) don't exist on text/comment nodes (#622)
                if (node.nodeType !== 1) {
                    if (globalThis.djustDebug) console.log('[LiveView] Patch %s targets non-element (nodeType=%d), skipping', patch.type, node.nodeType);
                    return false;
                }
                let child;
                if (patch.child_d) {
                    // ID-based resolution: find direct child by dj-id (resilient to index shifts)
                    const escaped = CSS.escape(patch.child_d);
                    child = node.querySelector(`:scope > [dj-id="${escaped}"]`);
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
 *
 * @param {Array} patches - VDOM patch list (server-authoritative).
 * @param {HTMLElement|null} [rootEl=null] — optional scoping root for
 *   the patch application. When null (the default path used by every
 *   pre-Phase-B caller), the live view root is resolved via
 *   ``getLiveViewRoot()`` exactly as before — zero regressions for
 *   top-level patches. When non-null (used by Sticky LiveViews Phase B
 *   and the now-wired Phase A child_update path), node lookups,
 *   focus save/restore, and the autofocus query are all scoped to
 *   ``rootEl`` so they cannot spill into or from another view's
 *   subtree.
 */
/**
 * Should the next ``applyPatches`` call wrap its DOM mutations in
 * ``document.startViewTransition()``? All four conditions must hold:
 *
 *   1. ``document`` is defined (not in a worker / non-browser context)
 *   2. ``document.startViewTransition`` is a function (Chrome 111+,
 *      Edge 111+, Safari 18+; Firefox graceful degrade — returns false)
 *   3. ``document.body`` is not null (yes, it can be — early bootstrap,
 *      ``<head>``-only HTML responses, mid-navigation)
 *   4. ``<body dj-view-transitions>`` opt-in attribute is present
 *   5. The user has NOT requested ``prefers-reduced-motion: reduce``
 *      (accessibility — motion-sensitive users get instant patches)
 *
 * Re-evaluated on every patch so dynamic mid-session opt-in via
 * ``document.body.setAttribute('dj-view-transitions', '')`` works.
 */
function _shouldUseViewTransition() {
    if (typeof document === 'undefined') return false;
    if (typeof document.startViewTransition !== 'function') return false;
    if (!document.body) return false;
    if (!document.body.hasAttribute('dj-view-transitions')) return false;
    if (
        typeof window !== 'undefined' &&
        window.matchMedia &&
        window.matchMedia('(prefers-reduced-motion: reduce)').matches
    ) {
        return false;
    }
    return true;
}

/**
 * Apply VDOM patches to the DOM. Returns ``Promise<boolean>`` — ``true``
 * on full success, ``false`` if any patch failed (caller may trigger a
 * full re-render fallback).
 *
 * **Two paths**:
 *
 * - **Direct (default)**: just runs the inner patch loop and resolves
 *   with its boolean result.
 * - **Wrapped (opt-in via** ``<body dj-view-transitions>`` **)**: wraps
 *   the inner loop in ``document.startViewTransition()``. The browser
 *   captures a pre-state frame, runs our callback, captures the
 *   post-state, and animates between them (cross-fade by default;
 *   ``view-transition-name`` enables shared-element morphs). We
 *   ``await transition.updateCallbackDone`` so the returned promise
 *   correctly reflects whether the inner loop succeeded — the View
 *   Transitions spec runs the callback in a microtask, NOT
 *   synchronously, so a non-async wrap would lose the boolean
 *   (this was PR #1092's bug).
 *
 * If the View Transitions callback throws, we ``transition.skipTransition()``
 * to abandon the animation and return false so the caller can trigger
 * a full re-render fallback. ADR-013.
 */
async function applyPatches(patches, rootEl = null) {
    if (!patches || patches.length === 0) {
        return true;
    }

    if (_shouldUseViewTransition()) {
        let innerResult = true;
        const transition = document.startViewTransition(() => {
            innerResult = _applyPatchesInner(patches, rootEl);
        });
        try {
            await transition.updateCallbackDone;
        } catch (err) {
            console.error('[djust] applyPatches threw inside View Transition:', err);
            transition.skipTransition();
            return false;
        }
        return innerResult;
    }

    return _applyPatchesInner(patches, rootEl);
}

/**
 * Synchronous inner patch loop. Extracted from the original sync
 * ``applyPatches`` body — no behavior changes, just renamed. The View
 * Transitions wrap above invokes this from inside the
 * ``startViewTransition`` callback; the direct path invokes it
 * unwrapped. Either way, this is the same DOM-mutating loop that has
 * shipped for many releases.
 */
function _applyPatchesInner(patches, rootEl = null) {
    if (!patches || patches.length === 0) {
        return true;
    }

    // Save focus state before any DOM mutations (#559 follow-up: focus preservation)
    const focusState = saveFocusState(rootEl);
    const autofocusScope = rootEl || document;

    // Sort patches in 4-phase order for correct DOM mutation sequencing
    _sortPatches(patches);

    // For small patch sets, apply directly without batching overhead
    if (patches.length <= 10) {
        let failedCount = 0;
        const failedIndices = [];
        for (let _pi = 0; _pi < patches.length; _pi++) {
            // eslint-disable-next-line security/detect-object-injection
            if (!applySinglePatch(patches[_pi], rootEl)) {
                failedCount++;
                failedIndices.push(_pi);
            }
        }
        if (failedCount > 0) {
            console.error(`[LiveView] ${failedCount}/${patches.length} patches failed (indices: ${failedIndices.join(', ')})`);
            // Still handle autofocus even when some patches failed (#617)
            if (!focusState || !focusState.id) {
                const autoFocusEl = autofocusScope.querySelector('[autofocus]');
                if (autoFocusEl && document.activeElement !== autoFocusEl) {
                    autoFocusEl.focus();
                }
            }
            restoreFocusState(focusState, rootEl);
            return false;
        }
        // Note: updateHooks() and bindModelElements() are called by
        // reinitAfterDOMUpdate() in the response handler — not here,
        // to avoid double-scanning the DOM.
        // Handle autofocus on dynamically inserted elements (#617)
        // Browser only honors autofocus on initial page load, so we
        // manually focus the first element with autofocus after a patch.
        if (!focusState || !focusState.id) {
            const autoFocusEl = autofocusScope.querySelector('[autofocus]');
            if (autoFocusEl && document.activeElement !== autoFocusEl) {
                autoFocusEl.focus();
            }
        }
        restoreFocusState(focusState, rootEl);
        return true;
    }

    // For larger patch sets, use batching
    let failedCount = 0;
    let successCount = 0;

    // Group patches by parent for potential batching
    const patchGroups = groupPatchesByParent(patches);

    for (const [, group] of patchGroups) {
        // Phase order within a group MUST match the top-level phase order:
        // RemoveChild → MoveChild → InsertChild → other.
        //
        // Previously the batching code below ran InsertChild patches (via
        // DocumentFragment) BEFORE iterating `group` for the RemoveChild
        // patches — violating phase order. That breaks when a comment/text
        // child without a dj-id needs removal: the index-based fallback
        // resolves to the just-inserted content instead of the old child,
        // and the wrong node gets deleted.  See regression fixtures for
        // a downstream consumer tab switches (#641).
        //
        // Fix: apply all non-Insert patches individually FIRST, then batch
        // the consecutive inserts, then apply any remaining inserts that
        // were too small to batch.  _sortPatches has already sorted the
        // removes within the group by descending index.
        const nonInsertPatches = [];
        const insertPatches = [];
        for (const patch of group) {
            if (patch.type === 'InsertChild') insertPatches.push(patch);
            else nonInsertPatches.push(patch);
        }

        // 1. Apply non-insert patches (RemoveChild, MoveChild, SetAttr, etc.)
        //    in their existing sorted order.  RemoveChild patches are
        //    descending-index-sorted by _sortPatches, so they're safe to
        //    apply sequentially without index drift.
        for (const patch of nonInsertPatches) {
            if (applySinglePatch(patch, rootEl)) {
                successCount++;
            } else {
                failedCount++;
            }
        }

        // 2. Batch consecutive inserts via DocumentFragment where possible.
        //    At this point the DOM is in the "post-remove" state, so index
        //    fallback for ref_d=None inserts lines up with what the server
        //    computed against the new VDOM.
        const batchedInserts = new Set();
        if (insertPatches.length >= 3) {
            const consecutiveGroups = groupConsecutiveInserts(insertPatches);

            for (const consecutiveGroup of consecutiveGroups) {
                if (consecutiveGroup.length < 3) continue;

                const firstPatch = consecutiveGroup[0];
                const parentNode = getNodeByPath(firstPatch.path, firstPatch.d, rootEl);

                if (parentNode) {
                    try {
                        const fragment = document.createDocumentFragment();
                        const svgContext = isInSvgContext(parentNode);
                        for (const patch of consecutiveGroup) {
                            const newChild = createNodeFromVNode(patch.node, svgContext);
                            fragment.appendChild(newChild);
                            successCount++;
                            batchedInserts.add(patch);
                        }

                        const children = getSignificantChildren(parentNode);
                        const firstIndex = consecutiveGroup[0].index;
                        // eslint-disable-next-line security/detect-object-injection
                        const refChild = children[firstIndex];

                        if (refChild) {
                            parentNode.insertBefore(fragment, refChild);
                        } else {
                            parentNode.appendChild(fragment);
                        }
                    } catch (error) {
                        console.error('[LiveView] Batch insert failed, falling back to individual patches:', error.message);
                        successCount -= consecutiveGroup.length;  // undo count
                        for (const patch of consecutiveGroup) batchedInserts.delete(patch);
                    }
                }
            }
        }

        // 3. Apply any insert patches that weren't batched (non-consecutive
        //    groups or group size < 3) individually.
        for (const patch of insertPatches) {
            if (batchedInserts.has(patch)) continue;
            if (applySinglePatch(patch, rootEl)) {
                successCount++;
            } else {
                failedCount++;
            }
        }
    }

    if (failedCount > 0) {
        console.error(`[LiveView] ${failedCount}/${patches.length} patches failed (${successCount} succeeded)`);
        // Still handle autofocus even when some patches failed (#617)
        if (!focusState || !focusState.id) {
            const autoFocusEl = autofocusScope.querySelector('[autofocus]');
            if (autoFocusEl && document.activeElement !== autoFocusEl) {
                autoFocusEl.focus();
            }
        }
        restoreFocusState(focusState, rootEl);
        return false;
    }

    // Note: updateHooks() and bindModelElements() are called by
    // reinitAfterDOMUpdate() in the response handler — not here,
    // to avoid double-scanning the DOM.

    // Handle autofocus on dynamically inserted elements (#617)
    // Browser only honors autofocus on initial page load, so we
    // manually focus the first element with autofocus after a patch.
    if (!focusState || !focusState.id) {
        const autoFocusEl = autofocusScope.querySelector('[autofocus]');
        if (autoFocusEl && document.activeElement !== autoFocusEl) {
            autoFocusEl.focus();
        }
    }

    restoreFocusState(focusState, rootEl);
    return true;
}

// Expose applyPatches on the public namespace for test-side eval and
// third-party hook integration. ``async function`` declarations don't
// always hoist to the host scope under JSDOM's eval; without this
// explicit binding, ``dom.window.eval(clientCode + '...applyPatches...')``
// throws ReferenceError. Public-surface change documented in CHANGELOG.
if (typeof globalThis !== 'undefined') {
    globalThis.djust = globalThis.djust || {};
    globalThis.djust.applyPatches = applyPatches;
}
