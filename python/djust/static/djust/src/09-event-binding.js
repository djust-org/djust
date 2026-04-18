/**
 * Check if element has dj-confirm and show confirmation dialog.
 * @param {HTMLElement} element - Element with potential dj-confirm attribute
 * @returns {boolean} - true if confirmed or no dialog needed, false if cancelled
 */
function checkDjConfirm(element) {
    const confirmMsg = element.getAttribute('dj-confirm');
    if (confirmMsg && !window.confirm(confirmMsg)) {
        return false; // User cancelled
    }
    return true; // Proceed
}

// Track which DOM nodes have actually had event handlers attached.
// WeakMap<Element, Set<string>> — keys are live DOM nodes, values are
// sets of handler types already bound (e.g. 'click', 'submit').
// Unlike data attributes, WeakMap entries are automatically invalidated
// when a DOM node is replaced (cloned/morphed) because the new node
// is a different object.  This prevents stale binding flags from
// blocking re-binding after VDOM patches.
const _boundHandlers = new WeakMap();

function _isHandlerBound(element, type) {
    const set = _boundHandlers.get(element);
    return set ? set.has(type) : false;
}

function _markHandlerBound(element, type) {
    let set = _boundHandlers.get(element);
    if (!set) {
        set = new Set();
        _boundHandlers.set(element, set);
    }
    set.add(type);
}

// ============================================================================
// Scoped Listener Helpers (window/document event binding)
// ============================================================================

// Track all elements that have scoped (window/document) listeners so we can
// sweep and clean up listeners for elements removed from the DOM.
const _scopedListenerElements = new Set();

/**
 * Clean up all scoped (window/document) listeners stored on an element.
 * Each listener is stored as { target, eventType, handler, capture } in
 * element._djustScopedListeners.
 * @param {HTMLElement} element
 */
function _cleanupScopedListeners(element) {
    if (!element._djustScopedListeners) return;
    for (const entry of element._djustScopedListeners) {
        entry.target.removeEventListener(entry.eventType, entry.handler, entry.capture || false);
    }
    element._djustScopedListeners = [];
    _scopedListenerElements.delete(element);
}

/**
 * Register a scoped listener on an element. Stores the reference for cleanup.
 * @param {HTMLElement} element - Declaring element (anchor)
 * @param {EventTarget} target - window or document
 * @param {string} eventType - DOM event type (e.g. 'keydown')
 * @param {Function} handler - Event handler function
 * @param {boolean} [capture=false] - Use capture phase
 */
function _addScopedListener(element, target, eventType, handler, capture) {
    if (!element._djustScopedListeners) element._djustScopedListeners = [];
    const useCapture = capture || false;
    element._djustScopedListeners.push({ target, eventType, handler, capture: useCapture });
    target.addEventListener(eventType, handler, useCapture);
    _scopedListenerElements.add(element);
}

/**
 * Sweep all tracked scoped-listener elements and remove listeners for any
 * elements that are no longer in the DOM. Called before binding new scoped
 * listeners to prevent accumulation from conditional rendering.
 */
function _sweepOrphanedScopedListeners() {
    for (const element of _scopedListenerElements) {
        if (!document.contains(element)) {
            _cleanupScopedListeners(element);
        }
    }
}

/**
 * Normalize a key name to match KeyboardEvent.key values.
 * @param {string} name - Key name from attribute (e.g. 'escape', 'enter', 'k')
 * @returns {string} - Normalized key name
 */
function _normalizeKeyName(name) {
    const lower = name.toLowerCase();
    const keyMap = {
        'escape': 'Escape',
        'enter': 'Enter',
        'tab': 'Tab',
        'space': ' ',
        'backspace': 'Backspace',
        'delete': 'Delete',
        'arrowup': 'ArrowUp',
        'arrowdown': 'ArrowDown',
        'arrowleft': 'ArrowLeft',
        'arrowright': 'ArrowRight',
    };
    return keyMap[lower] || name;
}

// ============================================================================
// Scoped Event Delegation (dj-window-*, dj-document-*)
// ============================================================================
// Install ONE listener per event type on window/document. When the event fires,
// dispatch to registered declaring elements. Elements are registered by scanning
// the DOM once at install time. Re-scanning happens on TurboNav navigation or
// when bindLiveViewEvents() is called after DOM changes.

// Registry: Map<"prefix:evtType", Set<{element, attrName, handler, requiredKey}>>
var _scopedRegistry = new Map();
var _scopedDelegationInstalled = false;

/**
 * Scan the DOM for elements with dj-window-[event] / dj-document-[event] attributes
 * and register them in the scoped registry for delegated dispatch.
 */
function _scanScopedElements() {
    var scopedPrefixes = ['dj-window-', 'dj-document-'];
    var scopedEventTypes = ['keydown', 'keyup', 'click', 'scroll', 'resize'];
    var root = document.querySelector('[dj-view]') || document.querySelector('[dj-root]') || document;

    // Clear stale entries (elements removed from DOM)
    _scopedRegistry.forEach(function(entries, key) {
        entries.forEach(function(entry) {
            if (!document.contains(entry.element)) {
                entries.delete(entry);
            }
        });
    });

    // Scan ALL elements in the LiveView root (only way to find dotted attr names).
    // This runs once at mount and on TurboNav — NOT after every patch.
    var allElements = root.querySelectorAll('*');
    allElements.forEach(function(element) {
        for (var ai = 0; ai < element.attributes.length; ai++) {
            var attrName = element.attributes[ai].name;
            for (var pi = 0; pi < scopedPrefixes.length; pi++) {
                var prefix = scopedPrefixes[pi];
                if (!attrName.startsWith(prefix)) continue;

                // Find which event type this is (e.g. 'keydown' from 'dj-window-keydown.escape')
                var rest = attrName.slice(prefix.length); // 'keydown' or 'keydown.escape'
                var evtType = null;
                for (var ei = 0; ei < scopedEventTypes.length; ei++) {
                    if (rest === scopedEventTypes[ei] || rest.startsWith(scopedEventTypes[ei] + '.')) {
                        evtType = scopedEventTypes[ei];
                        break;
                    }
                }
                if (!evtType) continue;

                var registryKey = prefix + evtType;
                if (!_scopedRegistry.has(registryKey)) {
                    _scopedRegistry.set(registryKey, new Set());
                }

                var suffix = rest.slice(evtType.length); // '' or '.escape'
                var requiredKey = suffix.startsWith('.') ? _normalizeKeyName(suffix.slice(1)) : null;
                var parsed = parseEventHandler(element.attributes[ai].value);

                // Check if already registered (prevent duplicates)
                var entries = _scopedRegistry.get(registryKey);
                var alreadyRegistered = false;
                entries.forEach(function(entry) {
                    if (entry.element === element && entry.attrName === attrName) {
                        alreadyRegistered = true;
                    }
                });
                if (alreadyRegistered) continue;

                entries.add({
                    element: element,
                    attrName: attrName,
                    parsed: parsed,
                    requiredKey: requiredKey,
                });
            }
        }
    });
}

/**
 * Install delegated listeners on window/document for scoped events.
 * Called once — listeners dispatch to the registry.
 */
function _installScopedDelegation() {
    if (_scopedDelegationInstalled) return;
    _scopedDelegationInstalled = true;

    // Scan DOM once at install time to populate the registry
    _scanScopedElements();

    var scopedTargets = [
        { prefix: 'dj-window-', target: window },
        { prefix: 'dj-document-', target: document },
    ];
    var scopedEventTypes = ['keydown', 'keyup', 'click', 'scroll', 'resize'];

    for (var ti = 0; ti < scopedTargets.length; ti++) {
        var prefix = scopedTargets[ti].prefix;
        var target = scopedTargets[ti].target;

        for (var ei = 0; ei < scopedEventTypes.length; ei++) {
            var evtType = scopedEventTypes[ei];
            if (target === document && (evtType === 'scroll' || evtType === 'resize')) continue;

            (function(prefix, target, evtType) {
                var registryKey = prefix + evtType;

                target.addEventListener(evtType, function(e) {
                    var entries = _scopedRegistry.get(registryKey);
                    if (!entries || entries.size === 0) return;

                    entries.forEach(function(entry) {
                        // Skip elements removed from DOM
                        if (!document.contains(entry.element)) return;

                        // Key filtering
                        if (entry.requiredKey && e.key !== entry.requiredKey) return;

                        var params = extractTypedParams(entry.element);
                        addEventContext(params, entry.element);

                        if (evtType === 'keydown' || evtType === 'keyup') {
                            params.key = e.key;
                            params.code = e.code;
                        } else if (evtType === 'click') {
                            params.clientX = e.clientX;
                            params.clientY = e.clientY;
                        } else if (evtType === 'scroll') {
                            params.scrollY = window.scrollY;
                            params.scrollX = window.scrollX;
                        } else if (evtType === 'resize') {
                            params.innerWidth = window.innerWidth;
                            params.innerHeight = window.innerHeight;
                        }

                        if (entry.parsed.args.length > 0) {
                            params._args = entry.parsed.args;
                        }

                        handleEvent(entry.parsed.name, params);
                    });
                }, evtType === 'scroll' || evtType === 'resize' ? { passive: true } : false);
            })(prefix, target, evtType);
        }
    }
}

/**
 * Add component and embedded view context to event params.
 * Extracts component_id and view_id from the element's ancestry.
 * @param {Object} params - Event params object to augment
 * @param {HTMLElement} element - Element that triggered the event
 */
function addEventContext(params, element) {
    const componentId = getComponentId(element);
    if (componentId) params.component_id = componentId;
    const embeddedViewId = getEmbeddedViewId(element);
    if (embeddedViewId) params.view_id = embeddedViewId;
}

// WeakSet to track elements whose dj-mounted handler has already fired.
// Using WeakSet means entries are GC'd when the DOM node is replaced,
// allowing the handler to fire again for genuinely new elements.
const _mountedElements = new WeakSet();

/**
 * Check if element is a form element that supports the disabled property.
 * @param {HTMLElement} element
 * @returns {boolean}
 */
function _isFormElement(element) {
    const tag = element.tagName;
    return tag === 'BUTTON' || tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA';
}

/**
 * Lock an element: set data-djust-locked marker and disable/style it.
 * For form elements (button, input, select, textarea): sets disabled = true.
 * For non-form elements: adds CSS class 'djust-locked'.
 * @param {HTMLElement} element
 */
function _lockElement(element) {
    element.setAttribute('data-djust-locked', '');
    if (_isFormElement(element)) {
        element.disabled = true;
    } else {
        element.classList.add('djust-locked');
    }
}

/**
 * Check if element has dj-lock and is already locked. If locked, return true
 * to signal the caller to skip the event. If not locked but has dj-lock, lock it.
 * @param {HTMLElement} element
 * @returns {boolean} true if event should be skipped (already locked)
 */
function _checkAndLock(element) {
    if (!element.hasAttribute('dj-lock')) return false;
    if (element.hasAttribute('data-djust-locked')) return true; // Already locked, skip
    _lockElement(element);
    return false;
}

/**
 * Apply dj-disable-with: save original text and replace with loading text.
 * Only saves original text if not already saved (prevents overwrite on double-submit).
 * @param {HTMLElement} element - Element with dj-disable-with attribute
 */
function _applyDisableWith(element) {
    const disableText = element.getAttribute('dj-disable-with');
    if (!disableText) return;
    if (!element.hasAttribute('data-djust-original-text')) {
        element.setAttribute('data-djust-original-text', element.textContent);
    }
    element.textContent = disableText;
    element.disabled = true;
}

// ============================================================================
// Delegated Event Handlers
// ============================================================================

// WeakMaps to store per-element rate limit state for delegated events.
// Since delegation means we don't have a closure per element, we use
// WeakMaps to associate rate-limited wrappers with their elements.
const _inputRateLimitState = new WeakMap();
const _changeRateLimitState = new WeakMap();
const _clickRateLimitState = new WeakMap();
const _keydownRateLimitState = new WeakMap();
const _keyupRateLimitState = new WeakMap();

// Helper: Extract field name from element attributes
// Priority: data-field (explicit) > name (standard) > id (fallback)
function getFieldName(element) {
    if (element.dataset && element.dataset.field) {
        return element.dataset.field;
    }
    if (element.name) {
        return element.name;
    }
    if (element.id) {
        // Strip common prefixes like 'id_' (Django convention)
        return element.id.replace(/^id_/, '');
    }
    return null;
}

/**
 * Build standard form event params with component context.
 * Used by change, input, blur, focus event handlers.
 * @param {HTMLElement} element - Form element that triggered the event
 * @param {any} value - Current value of the field
 * @returns {Object} - Params object with value, field, and optional component_id
 */
function buildFormEventParams(element, value) {
    const fieldName = getFieldName(element);
    const params = { value, field: fieldName };
    // Merge dj-value-* attributes from the triggering element
    Object.assign(params, collectDjValues(element));
    addEventContext(params, element);
    return params;
}

/**
 * Get or create a rate-limited handler wrapper for an element.
 * @param {WeakMap} stateMap - WeakMap storing per-element rate limit state
 * @param {HTMLElement} element - Element to get/create wrapper for
 * @param {string} eventType - Event type (for server rate limit lookup)
 * @param {Function} rawHandler - The raw (unwrapped) handler function
 * @returns {Function} - Rate-limited wrapper or raw handler
 */
function _getOrCreateRateLimitedHandler(stateMap, element, eventType, rawHandler) {
    let state = stateMap.get(element);
    if (state) return state.wrapped;

    // Create rate-limited wrapper for this element
    let wrapped = _applyRateLimitAttrs(element, rawHandler);
    if (wrapped === rawHandler && window.djust.rateLimit) {
        wrapped = window.djust.rateLimit.wrapWithRateLimit(element, eventType, rawHandler);
    }
    stateMap.set(element, { wrapped });
    return wrapped;
}

/**
 * Handle dj-click events via delegation.
 * @param {HTMLElement} element - Element with dj-click attribute
 * @param {Event} e - The original click event
 */
async function _handleDjClick(element, e) {
    e.preventDefault();

    // dj-lock: skip if already locked
    if (_checkAndLock(element)) return;

    // Read attribute at fire time so morphElement attribute updates take effect
    const rawClickValue = element.getAttribute('dj-click') || '';

    // dj-confirm: show confirmation dialog before executing commands/events
    if (!checkDjConfirm(element)) {
        return; // User cancelled
    }

    // JS Commands: synchronously check whether the attribute is a
    // JSON command chain. If so, fire-and-forget the chain (push
    // ops still round-trip, but we don't block the rest of this
    // handler on them). A plain event name falls through to the
    // normal dj-click path without adding an `await` boundary,
    // so synchronous expectations on dj-disable-with and friends
    // continue to hold.
    if (window.djust.js) {
        const _ops = window.djust.js._parseCommandValue(rawClickValue);
        if (_ops) {
            window.djust.js._executeOps(_ops, element);
            return;
        }
    }

    const parsed = parseEventHandler(rawClickValue);

    // dj-disable-with: disable and show loading text
    _applyDisableWith(element);

    // Apply optimistic update if specified
    let optimisticUpdateId = null;
    if (window.djust.optimistic) {
        optimisticUpdateId = window.djust.optimistic.applyOptimisticUpdate(element, parsed.name);
    }

    // Extract all data-* attributes with type coercion support
    const params = extractTypedParams(element);

    // Add positional arguments from handler syntax if present
    // e.g., dj-click="set_period('month')" -> params._args = ['month']
    if (parsed.args.length > 0) {
        params._args = parsed.args;
    }

    addEventContext(params, element);

    // Pass target element and optimistic update ID
    params._targetElement = element;
    params._optimisticUpdateId = optimisticUpdateId;

    // Handle dj-target for scoped updates
    const targetSelector = element.getAttribute('dj-target');
    if (targetSelector) {
        params._djTargetSelector = targetSelector;
    }

    await handleEvent(parsed.name, params);
}

/**
 * Handle dj-copy — client-side clipboard copy (no server round-trip).
 * @param {HTMLElement} element - Element with dj-copy attribute
 * @param {Event} e - The original click event
 */
function _handleDjCopy(element, e) {
    e.preventDefault();
    // Read attribute at click time (not bind time) so morph updates take effect
    var currentValue = element.getAttribute('dj-copy');
    if (!currentValue) return;

    // Selector-based copy: if value starts with #, . or [, try querySelector
    var textToCopy = currentValue;
    if (currentValue.charAt(0) === '#' || currentValue.charAt(0) === '.' || currentValue.charAt(0) === '[') {
        try {
            var target = document.querySelector(currentValue);
            if (target) {
                textToCopy = target.textContent;
            }
        } catch (err) {
            // Invalid selector — fall back to literal copy
        }
    }

    navigator.clipboard.writeText(textToCopy).then(function() {
        // CSS class feedback: add class and remove after 2s
        var cssClass = element.getAttribute('dj-copy-class') || 'dj-copied';
        element.classList.add(cssClass);
        setTimeout(function() { element.classList.remove(cssClass); }, 2000);

        // Text feedback: custom or default "Copied!"
        var feedbackText = element.getAttribute('dj-copy-feedback') || 'Copied!';
        var original = element.textContent;
        element.textContent = feedbackText;
        setTimeout(function() { element.textContent = original; }, 1500);

        // Optional server event for analytics
        var copyEvent = element.getAttribute('dj-copy-event');
        if (copyEvent) {
            handleEvent(copyEvent, { text: textToCopy });
        }
    });
}

/**
 * Handle dj-submit events on forms via delegation.
 * @param {HTMLElement} element - Form element with dj-submit attribute
 * @param {Event} e - The original submit event
 */
async function _handleDjSubmit(element, e) {
    e.preventDefault();

    // dj-lock: skip if already locked
    if (_checkAndLock(element)) return;

    // dj-confirm: show confirmation dialog before sending event
    if (!checkDjConfirm(element)) {
        return; // User cancelled
    }

    // Read attribute at fire time so morphElement attribute updates take effect
    const submitHandler = element.getAttribute('dj-submit');

    // dj-disable-with: disable submit buttons within the form
    const submitBtns = element.querySelectorAll('button[type="submit"][dj-disable-with]');
    submitBtns.forEach(btn => _applyDisableWith(btn));
    // Also check the submitter if it has dj-disable-with
    if (e.submitter && e.submitter.hasAttribute('dj-disable-with')) {
        _applyDisableWith(e.submitter);
    }

    const formData = new FormData(element);
    const params = Object.fromEntries(formData.entries());

    // Merge dj-value-* attributes from the form element
    Object.assign(params, collectDjValues(element));

    addEventContext(params, element);

    // _target: include submitter name if available
    params._target = (e.submitter && (e.submitter.name || e.submitter.id)) || null;

    // Pass target element for optimistic updates (Phase 3)
    params._targetElement = element;

    await handleEvent(submitHandler, params);
}

/**
 * Handle dj-change events via delegation.
 * @param {HTMLElement} element - Element with dj-change attribute
 * @param {Event} e - The original change event
 */
async function _handleDjChange(element, e) {
    // dj-lock: skip if already locked
    if (_checkAndLock(element)) return;

    // dj-confirm: show confirmation dialog before sending event
    if (!checkDjConfirm(element)) {
        return; // User cancelled
    }

    // Read and parse attribute at fire time
    const changeHandler = element.getAttribute('dj-change');
    const parsedChange = parseEventHandler(changeHandler);

    const value = e.target.type === 'checkbox' ? e.target.checked : e.target.value;
    const params = buildFormEventParams(e.target, value);

    // Add positional arguments from handler syntax if present
    // e.g., dj-change="toggle_todo(3)" -> params._args = [3]
    if (parsedChange.args.length > 0) {
        params._args = parsedChange.args;
    }

    // _target: include triggering field's name (or id, or null)
    params._target = e.target.name || e.target.id || null;

    // Add target element for loading state (consistent with other handlers)
    params._targetElement = e.target;

    // Handle dj-target for scoped updates
    const targetSelector = element.getAttribute('dj-target');
    if (targetSelector) {
        params._djTargetSelector = targetSelector;
    }

    if (globalThis.djustDebug) {
        djLog(`[LiveView] dj-change handler: value="${value}", params=`, params);
    }
    await handleEvent(parsedChange.name, params);
}

/**
 * Handle dj-input events via delegation.
 * @param {HTMLElement} element - Element with dj-input attribute
 * @param {Event} e - The original input event
 */
async function _handleDjInput(element, e) {
    // dj-lock: skip if already locked
    if (_checkAndLock(element)) return;

    // dj-confirm: show confirmation dialog before sending event
    if (!checkDjConfirm(element)) {
        return; // User cancelled
    }

    // Read and parse attribute at fire time
    const inputHandler = element.getAttribute('dj-input');
    const parsedInput = parseEventHandler(inputHandler);

    const params = buildFormEventParams(e.target, e.target.value);
    if (parsedInput.args.length > 0) {
        params._args = parsedInput.args;
    }

    // _target: include triggering field's name (or id, or null)
    params._target = e.target.name || e.target.id || null;

    await handleEvent(parsedInput.name, params);
}

/**
 * Handle dj-blur events via delegation (using focusout which bubbles).
 * @param {HTMLElement} element - Element with dj-blur attribute
 * @param {Event} e - The original focusout event
 */
async function _handleDjBlur(element, e) {
    // dj-lock: skip if already locked
    if (_checkAndLock(element)) return;

    // dj-confirm: show confirmation dialog before sending event
    if (!checkDjConfirm(element)) {
        return; // User cancelled
    }

    // Read and parse attribute at fire time
    const blurHandler = element.getAttribute('dj-blur');
    const parsedBlur = parseEventHandler(blurHandler);

    const params = buildFormEventParams(e.target, e.target.value);
    if (parsedBlur.args.length > 0) {
        params._args = parsedBlur.args;
    }
    await handleEvent(parsedBlur.name, params);
}

/**
 * Handle dj-focus events via delegation (using focusin which bubbles).
 * @param {HTMLElement} element - Element with dj-focus attribute
 * @param {Event} e - The original focusin event
 */
async function _handleDjFocus(element, e) {
    // dj-lock: skip if already locked
    if (_checkAndLock(element)) return;

    // dj-confirm: show confirmation dialog before sending event
    if (!checkDjConfirm(element)) {
        return; // User cancelled
    }

    // Read and parse attribute at fire time
    const focusHandler = element.getAttribute('dj-focus');
    const parsedFocus = parseEventHandler(focusHandler);

    const params = buildFormEventParams(e.target, e.target.value);
    if (parsedFocus.args.length > 0) {
        params._args = parsedFocus.args;
    }
    await handleEvent(parsedFocus.name, params);
}

/**
 * Handle dj-paste events via delegation.
 * Extracts structured clipboard payload (plain text, rich HTML, files)
 * and sends it to the server as a single event call.
 * @param {HTMLElement} element - Element with dj-paste attribute
 * @param {Event} e - The original paste event
 */
async function _handleDjPaste(element, e) {
    // dj-lock: skip if already locked
    if (_checkAndLock(element)) return;

    // dj-confirm: show confirmation dialog before sending event
    if (!checkDjConfirm(element)) {
        return; // User cancelled
    }

    // Read and parse attribute at fire time
    const pasteHandler = element.getAttribute('dj-paste');
    const parsedPaste = parseEventHandler(pasteHandler);

    const clipboardData = e.clipboardData || window.clipboardData;
    if (!clipboardData) {
        // No clipboard data available — let the default paste happen
        return;
    }

    // Build structured payload: text, html, and file metadata.
    // The actual file bytes are NOT sent in this event — that would
    // blow the WS frame budget. Instead, set a dj-upload slot on
    // the element and UploadMixin will pick up any files from the
    // clipboard files list via the existing upload pipeline.
    let text = '';
    let html = '';
    const files = [];
    try {
        text = clipboardData.getData('text/plain') || '';
    } catch (err) { /* older browsers */ }
    try {
        html = clipboardData.getData('text/html') || '';
    } catch (err) { /* older browsers */ }
    if (clipboardData.files) {
        for (let i = 0; i < clipboardData.files.length; i++) {
            const f = clipboardData.files[i];
            files.push({
                name: f.name || 'clipboard-paste',
                type: f.type || '',
                size: f.size || 0,
            });
        }
    }

    // If the element has an upload slot configured, route pasted
    // files through the upload pipeline (image paste → chat, etc).
    // We route BEFORE sending the server event so the handler can
    // react to both the metadata and the pending upload in one tick.
    if (files.length > 0 && window.djust && window.djust.uploads && element.getAttribute('dj-upload')) {
        try {
            await window.djust.uploads.queueClipboardFiles(element, clipboardData.files);
        } catch (err) {
            if (globalThis.djustDebug) console.log('[LiveView] dj-paste: upload route failed', err);
        }
    }

    const params = {
        text: text,
        html: html,
        has_files: files.length > 0,
        files: files,
    };
    if (parsedPaste.args.length > 0) {
        params._args = parsedPaste.args;
    }

    // Suppress the default paste only when the element opts in
    // with dj-paste-suppress. Otherwise let the browser also
    // insert into the input so hybrid UIs still feel natural.
    if (element.hasAttribute('dj-paste-suppress')) {
        e.preventDefault();
    }

    await handleEvent(parsedPaste.name, params);
}

/**
 * Handle dj-keydown / dj-keyup events via delegation.
 * @param {HTMLElement} element - Element with dj-keydown or dj-keyup attribute
 * @param {Event} e - The original keyboard event
 * @param {string} eventType - 'keydown' or 'keyup'
 */
async function _handleDjKeyboard(element, e, eventType) {
    // Read attribute at fire time
    const keyHandler = element.getAttribute('dj-' + eventType);
    if (!keyHandler) return;

    // Check for key modifiers (e.g. dj-keydown.enter)
    const modifiers = keyHandler.split('.');
    const handlerName = modifiers[0];
    const requiredKey = modifiers.length > 1 ? modifiers[1] : null;

    if (requiredKey) {
        if (requiredKey === 'enter' && e.key !== 'Enter') return;
        if (requiredKey === 'escape' && e.key !== 'Escape') return;
        if (requiredKey === 'space' && e.key !== ' ') return;
        // Add more key mappings as needed
    }

    // dj-lock: skip if already locked
    if (_checkAndLock(element)) return;

    // dj-confirm: show confirmation dialog before sending event
    if (!checkDjConfirm(element)) {
        return; // User cancelled
    }

    const fieldName = getFieldName(e.target);
    const params = {
        key: e.key,
        code: e.code,
        value: e.target.value,
        field: fieldName
    };

    // Merge dj-value-* attributes from the element
    Object.assign(params, collectDjValues(element));

    addEventContext(params, e.target);

    // Add target element and handle dj-target
    params._targetElement = e.target;
    const targetSelector = element.getAttribute('dj-target');
    if (targetSelector) {
        params._djTargetSelector = targetSelector;
    }

    await handleEvent(handlerName, params);
}

// ============================================================================
// Event Delegation
// ============================================================================

/**
 * Install ONE delegated listener per DOM event type on the given root element.
 * Idempotent — uses AbortController to tear down old listeners before
 * installing new ones, preventing double-firing after TurboNav navigation.
 * @param {HTMLElement} root - The LiveView root element to delegate from
 */
function installDelegatedListeners(root) {
    // Tear down previous delegated listeners (if any) to prevent double-firing
    // after TurboNav morphs the page content while preserving the root element.
    if (root._djustDelegateAbort) {
        root._djustDelegateAbort.abort();
    }
    var controller = new AbortController();
    root._djustDelegateAbort = controller;
    var opts = { signal: controller.signal };

    // Helper: addEventListener with abort signal for clean teardown
    function on(event, handler) {
        root.addEventListener(event, handler, opts);
    }

    // click → dj-copy (client-only) first, then dj-click
    on('click', function(e) {
        var copyEl = e.target.closest('[dj-copy]');
        if (copyEl) {
            _handleDjCopy(copyEl, e);
            return;
        }
        var clickEl = e.target.closest('[dj-click]');
        if (clickEl) {
            // Rate-limit per element using WeakMap
            var rawHandler = function(ev) { return _handleDjClick(clickEl, ev); };
            var wrapped = _getOrCreateRateLimitedHandler(_clickRateLimitState, clickEl, 'click', rawHandler);
            wrapped(e);
        }
    });

    // submit → dj-submit
    on('submit', function(e) {
        var submitEl = e.target.closest('[dj-submit]');
        if (submitEl) {
            _handleDjSubmit(submitEl, e);
        }
    });

    // change → dj-change
    on('change', function(e) {
        var changeEl = e.target.closest('[dj-change]');
        if (changeEl) {
            // Rate-limit per element using WeakMap
            var rawHandler = function(ev) { return _handleDjChange(changeEl, ev); };
            var wrapped = _getOrCreateRateLimitedHandler(_changeRateLimitState, changeEl, 'change', rawHandler);
            wrapped(e);
        }
    });

    // input → dj-input (with smart rate limiting)
    on('input', function(e) {
        var inputEl = e.target.closest('[dj-input]');
        if (inputEl) {
            // Get or create rate-limited wrapper for this element
            var state = _inputRateLimitState.get(inputEl);
            if (!state) {
                // Build the raw handler
                var rawHandler = function(ev) { return _handleDjInput(inputEl, ev); };

                // Determine rate limit strategy
                var inputType = inputEl.type || inputEl.tagName.toLowerCase();
                var rateLimit = Object.prototype.hasOwnProperty.call(DEFAULT_RATE_LIMITS, inputType) ? DEFAULT_RATE_LIMITS[inputType] : { type: 'debounce', ms: 300 };

                // Check for explicit overrides: dj-* attributes take precedence
                if (inputEl.hasAttribute('dj-debounce')) {
                    var djVal = inputEl.getAttribute('dj-debounce');
                    if (djVal === 'blur') {
                        rateLimit.type = 'blur';
                        rateLimit.ms = 0;
                    } else {
                        rateLimit.type = 'debounce';
                        rateLimit.ms = parseInt(djVal, 10);
                    }
                } else if (inputEl.hasAttribute('dj-throttle')) {
                    rateLimit.type = 'throttle';
                    rateLimit.ms = parseInt(inputEl.getAttribute('dj-throttle'), 10);
                } else if (inputEl.hasAttribute('data-debounce')) {
                    rateLimit.type = 'debounce';
                    rateLimit.ms = parseInt(inputEl.getAttribute('data-debounce'));
                } else if (inputEl.hasAttribute('data-throttle')) {
                    rateLimit.type = 'throttle';
                    rateLimit.ms = parseInt(inputEl.getAttribute('data-throttle'));
                }

                // Apply rate limiting wrapper
                var wrapped;
                if (rateLimit.type === 'blur') {
                    // dj-debounce="blur": defer until element loses focus
                    var latestArgs = null;
                    wrapped = function() {
                        latestArgs = arguments;
                    };
                    inputEl.addEventListener('blur', function() {
                        if (latestArgs !== null) {
                            rawHandler.apply(null, latestArgs);
                            latestArgs = null;
                        }
                    });
                } else if (rateLimit.type === 'throttle') {
                    wrapped = throttle(rawHandler, rateLimit.ms);
                } else {
                    wrapped = debounce(rawHandler, rateLimit.ms);
                }

                state = { wrapped: wrapped };
                _inputRateLimitState.set(inputEl, state);
            }
            state.wrapped(e);
        }
    });

    // keydown → dj-keydown
    on('keydown', function(e) {
        var keyEl = e.target.closest('[dj-keydown]');
        if (keyEl) {
            var rawHandler = function(ev) { return _handleDjKeyboard(keyEl, ev, 'keydown'); };
            var wrapped = _getOrCreateRateLimitedHandler(_keydownRateLimitState, keyEl, 'keydown', rawHandler);
            wrapped(e);
        }
    });

    // keyup → dj-keyup (separate WeakMap from keydown to avoid handler collision)
    on('keyup', function(e) {
        var keyEl = e.target.closest('[dj-keyup]');
        if (keyEl) {
            var rawHandler = function(ev) { return _handleDjKeyboard(keyEl, ev, 'keyup'); };
            var wrapped = _getOrCreateRateLimitedHandler(_keyupRateLimitState, keyEl, 'keyup', rawHandler);
            wrapped(e);
        }
    });

    // paste → dj-paste
    on('paste', function(e) {
        var pasteEl = e.target.closest('[dj-paste]');
        if (pasteEl) {
            _handleDjPaste(pasteEl, e);
        }
    });

    // focusin → dj-focus (focusin bubbles, focus doesn't)
    on('focusin', function(e) {
        var focusEl = e.target.closest('[dj-focus]');
        if (focusEl) {
            _handleDjFocus(focusEl, e);
        }
    });

    // focusout → dj-blur (focusout bubbles, blur doesn't)
    on('focusout', function(e) {
        var blurEl = e.target.closest('[dj-blur]');
        if (blurEl) {
            _handleDjBlur(blurEl, e);
        }
    });
}

function bindLiveViewEvents(scope) {
    const root = scope || getLiveViewRoot() || document;

    // Install delegated listeners on the LiveView root element.
    // Only install on actual [dj-view]/[dj-root] elements, NOT on document.body
    // fallback — body persists across TurboNav page swaps, causing duplicate
    // events when navigating away from a LiveView page and back.
    const liveRoot = document.querySelector('[dj-view]') || document.querySelector('[dj-root]');
    if (liveRoot) installDelegatedListeners(liveRoot);

    // Bind upload handlers (dj-upload, dj-upload-drop, dj-upload-preview)
    if (window.djust.uploads) {
        window.djust.uploads.bindHandlers(scope);
    }

    // Bind navigation directives (dj-patch, dj-navigate)
    if (window.djust.navigation) {
        window.djust.navigation.bindDirectives(scope);
    }

    // === Per-element scanning section (only for non-delegable events) ===

    // dj-poll needs per-element interval setup
    const pollSelector = '[dj-poll]';
    const pollElements = root.querySelectorAll(pollSelector);
    pollElements.forEach(element => {
        const pollHandler = element.getAttribute('dj-poll');
        if (pollHandler && !_isHandlerBound(element, 'poll')) {
            _markHandlerBound(element, 'poll');
            const parsed = parseEventHandler(pollHandler);
            const interval = parseInt(element.getAttribute('dj-poll-interval'), 10) || 5000;
            const pollParams = extractTypedParams(element);

            const intervalId = setInterval(() => {
                if (document.hidden) return;
                handleEvent(parsed.name, Object.assign({}, pollParams, { _skipLoading: true }));
            }, interval);

            element._djustPollIntervalId = intervalId;

            // Pause/resume on visibility change
            const visHandler = () => {
                if (!document.hidden) {
                    handleEvent(parsed.name, Object.assign({}, pollParams, { _skipLoading: true }));
                }
            };
            document.addEventListener('visibilitychange', visHandler);
            element._djustPollVisibilityHandler = visHandler;
        }
    });

    // ================================================================
    // Scoped listeners: dj-window-*, dj-document-*, dj-click-away, dj-shortcut
    // ================================================================

    // --- Feature 1: dj-window-* and dj-document-* event scoping ---
    // Delegated approach: install ONE listener per event type on window/document.
    // When the event fires, find declaring elements via querySelectorAll('[dj-window-keydown]')
    // and dispatch to matching handlers. This avoids querySelectorAll('*') entirely.
    _installScopedDelegation();

    // Sweep orphaned scoped listeners (click-away, shortcut) for elements
    // removed from DOM by conditional rendering
    _sweepOrphanedScopedListeners();

    // --- Feature 2: dj-click-away ---
    document.querySelectorAll('[dj-click-away]').forEach(element => {
        if (_isHandlerBound(element, 'click-away')) return;
        _markHandlerBound(element, 'click-away');

        const handlerName = element.getAttribute('dj-click-away');

        const clickAwayHandler = async (e) => {
            // Only fire if click is outside the element
            if (element.contains(e.target)) return;

            // dj-confirm support
            if (!checkDjConfirm(element)) return;

            const params = extractTypedParams(element);
            addEventContext(params, element);

            await handleEvent(handlerName, params);
        };

        // Use capture phase so stopPropagation inside doesn't prevent detection
        _addScopedListener(element, document, 'click', clickAwayHandler, true);
    });

    // --- Feature 3: dj-shortcut ---
    document.querySelectorAll('[dj-shortcut]').forEach(element => {
        if (_isHandlerBound(element, 'shortcut')) return;
        _markHandlerBound(element, 'shortcut');

        const attrValue = element.getAttribute('dj-shortcut');
        const allowInInput = element.hasAttribute('dj-shortcut-in-input');

        // Parse comma-separated bindings
        // Each binding: [modifier+...]key:handler[:prevent]
        const bindings = attrValue.split(',').map(b => b.trim()).filter(b => b).map(binding => {
            const parts = binding.split(':');
            const keyCombo = parts[0].trim(); // e.g. 'ctrl+k' or 'escape'
            const handler = parts[1] ? parts[1].trim() : '';
            const preventDefault = parts[2] ? parts[2].trim() === 'prevent' : false;

            // Parse key combo into modifiers + key
            const comboParts = keyCombo.split('+');
            const key = _normalizeKeyName(comboParts[comboParts.length - 1]);
            const modifiers = new Set();
            for (let i = 0; i < comboParts.length - 1; i++) {
                modifiers.add(comboParts[i].toLowerCase());
            }

            return { key, modifiers, handler, preventDefault, comboString: keyCombo };
        });

        const shortcutHandler = async (e) => {
            // Skip if active element is a form input (unless opt-out)
            if (!allowInInput) {
                const active = document.activeElement;
                if (active) {
                    const tag = active.tagName;
                    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || active.isContentEditable) {
                        return;
                    }
                }
            }

            // Skip if element is not visible (hidden modals etc.)
            // Check hidden attribute and display:none; offsetParent is used as
            // a secondary check when available (not in JSDOM/SSR environments).
            if (element.hidden || element.style.display === 'none') return;
            if (!document.contains(element)) return;

            for (const binding of bindings) {
                if (e.key !== binding.key) continue;

                // Check modifiers
                const ctrlMatch = binding.modifiers.has('ctrl') === e.ctrlKey;
                const altMatch = binding.modifiers.has('alt') === e.altKey;
                const shiftMatch = binding.modifiers.has('shift') === e.shiftKey;
                const metaMatch = binding.modifiers.has('meta') === e.metaKey;

                if (!ctrlMatch || !altMatch || !shiftMatch || !metaMatch) continue;

                // Match found
                if (binding.preventDefault) {
                    e.preventDefault();
                }

                const params = extractTypedParams(element);
                addEventContext(params, element);
                params.key = e.key;
                params.code = e.code;
                params.shortcut = binding.comboString;

                await handleEvent(binding.handler, params);
                return; // Fire first match only
            }
        };

        _addScopedListener(element, document, 'keydown', shortcutHandler, false);
    });
    // Re-scan dj-loading attributes after DOM updates so dynamically
    // added elements (e.g. inside modals) get registered.
    globalLoadingManager.scanAndRegister();

    // dj-mounted: fire event for elements that have entered the DOM after
    // initial mount. Uses WeakSet to track already-fired elements so each
    // DOM node only triggers once (replaced nodes are new objects and will fire again).
    if (window.djust._mountReady) {
        document.querySelectorAll('[dj-mounted]').forEach(el => {
            if (_mountedElements.has(el)) return;
            _mountedElements.add(el);

            const handlerName = el.getAttribute('dj-mounted');
            if (!handlerName) return;

            // Collect dj-value-* and data-* params from the mounted element
            const params = extractTypedParams(el);
            addEventContext(params, el);

            handleEvent(handlerName, params);
        });
    }
}

/**
 * Apply dj-debounce / dj-throttle HTML attributes to an event handler.
 * If the element has dj-debounce or dj-throttle, wraps the handler accordingly.
 * dj-debounce="blur" is a special value that defers until the element loses focus.
 * Returns the (potentially wrapped) handler.
 * @param {HTMLElement} element - Element with potential dj-debounce/dj-throttle
 * @param {Function} handler - Original event handler
 * @returns {Function} - Wrapped or original handler
 */
function _applyRateLimitAttrs(element, handler) {
    if (element.hasAttribute('dj-debounce')) {
        const val = element.getAttribute('dj-debounce');
        if (val === 'blur') {
            // Special: defer event until element loses focus
            let latestArgs = null;
            const blurWrapper = function (...args) {
                latestArgs = args;
            };
            element.addEventListener('blur', function () {
                if (latestArgs !== null) {
                    handler(...latestArgs);
                    latestArgs = null;
                }
            });
            return blurWrapper;
        }
        const ms = parseInt(val, 10);
        if (ms === 0) {
            return handler; // dj-debounce="0" means no debounce
        }
        return debounce(handler, ms);
    }
    if (element.hasAttribute('dj-throttle')) {
        const ms = parseInt(element.getAttribute('dj-throttle'), 10);
        return throttle(handler, ms);
    }
    return handler;
}

// Helper: Debounce function
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

// Helper: Throttle function
function throttle(func, limit) {
    let inThrottle;
    return function (...args) {
        if (!inThrottle) {
            func(...args);
            inThrottle = true;
            setTimeout(() => inThrottle = false, limit);
        }
    }
}

// Helper: Get LiveView root element
function getLiveViewRoot() {
    return document.querySelector('[dj-view]') || document.querySelector('[dj-root]') || document.body;
}

// Helper: Clear optimistic state
function clearOptimisticState(eventName) {
    if (eventName && optimisticUpdates.has(eventName)) {
        const { element: _element, originalState: _originalState } = optimisticUpdates.get(eventName);
        // TODO: Restore original state on error (e.g. _element, _originalState)
        optimisticUpdates.delete(eventName);
    }
}

/**
 * Reinitialize all dynamic content after a DOM replacement.
 *
 * Call this after any operation that replaces or morphs DOM content
 * (html_update, html_recovery, TurboNav, embedded view update, etc.)
 * instead of manually calling initReactCounters + initTodoItems +
 * bindLiveViewEvents + updateHooks individually.
 */
// WeakSet to track elements that have already been scrolled into view.
// Fresh DOM nodes (from VDOM replacement) won't be in the set, so they
// will scroll again — correct behavior for newly inserted content.
const _scrolledElements = new WeakSet();

function reinitAfterDOMUpdate(scope) {
    initReactCounters();
    initTodoItems();
    bindLiveViewEvents(scope);
    if (window.djust.mountHooks) {
        // updateHooks scans for [dj-hook] — scope it too
        const hookRoot = scope || getLiveViewRoot();
        hookRoot.querySelectorAll('[dj-hook]').forEach(el => {
            // Delegate to the hook system's per-element logic
            if (window.djust.mountHooks) window.djust.mountHooks(el);
        });
    }
    updateHooks();

    // dj-scroll-into-view: auto-scroll elements into view after DOM updates
    const scrollRoot = scope || document;
    scrollRoot.querySelectorAll('[dj-scroll-into-view]').forEach(el => {
        if (_scrolledElements.has(el)) return;
        _scrolledElements.add(el);

        const value = el.getAttribute('dj-scroll-into-view') || '';
        let options;
        switch (value) {
            case 'instant':
                options = { behavior: 'instant', block: 'nearest' };
                break;
            case 'center':
                options = { behavior: 'smooth', block: 'center' };
                break;
            case 'start':
                options = { behavior: 'smooth', block: 'start' };
                break;
            case 'end':
                options = { behavior: 'smooth', block: 'end' };
                break;
            default:
                options = { behavior: 'smooth', block: 'nearest' };
                break;
        }
        el.scrollIntoView(options);
    });
}

/**
 * Process dj-auto-recover elements after a WebSocket reconnect.
 * Scans for [dj-auto-recover] elements, serializes their DOM state
 * (form values + data-* attributes), and fires the named event.
 * Only fires when _isReconnect flag is set; clears the flag after processing.
 */
function _processAutoRecover() {
    if (!window.djust._isReconnect) return;
    window.djust._isReconnect = false;

    document.querySelectorAll('[dj-auto-recover]').forEach(function(container) {
        var handlerName = container.getAttribute('dj-auto-recover');
        if (!handlerName) return;

        // Serialize form field values within the container
        var formValues = {};
        container.querySelectorAll('input, textarea, select').forEach(function(field) {
            var name = field.name;
            if (!name) return;
            if (field.type === 'checkbox') {
                formValues[name] = field.checked;
            } else if (field.type === 'radio') {
                if (field.checked) formValues[name] = field.value;
            } else {
                formValues[name] = field.value;
            }
        });

        // Collect data-* attributes from the container element
        var dataAttrs = {};
        for (var i = 0; i < container.attributes.length; i++) {
            var attr = container.attributes[i];
            if (attr.name.startsWith('data-')) {
                var key = attr.name.slice(5); // Strip 'data-' prefix
                dataAttrs[key] = attr.value;
            }
        }

        var params = {
            _form_values: formValues,
            _data_attrs: dataAttrs
        };

        handleEvent(handlerName, params);
    });
}

/**
 * Process automatic form recovery after a WebSocket reconnect.
 * Scans all form fields with dj-change or dj-input inside [dj-view] and
 * fires synthetic change events when the DOM value differs from the
 * server-rendered default, restoring server state transparently.
 *
 * Skips fields with dj-no-recover or fields inside dj-auto-recover
 * containers (custom handlers take precedence).
 *
 * Fires events sequentially (batched) via handleEvent() to avoid
 * race conditions on the server.
 */
function _processFormRecovery() {
    if (!window.djust._isReconnect) return;

    var root = document.querySelector('[dj-view]');
    if (!root) root = document.querySelector('[dj-root]');
    if (!root) return;

    // Collect fields to recover
    var fields = root.querySelectorAll('input[dj-change], textarea[dj-change], select[dj-change], input[dj-input], textarea[dj-input], select[dj-input]');
    var pendingEvents = [];

    for (var i = 0; i < fields.length; i++) {
        var field = fields[i];

        // Skip fields with dj-no-recover
        if (field.hasAttribute('dj-no-recover')) continue;

        // Skip fields inside dj-auto-recover containers (custom handler takes precedence)
        if (field.closest('[dj-auto-recover]')) continue;

        // Determine handler name — prefer dj-change, fall back to dj-input
        var handlerAttr = field.hasAttribute('dj-change') ? 'dj-change' : 'dj-input';
        var handlerString = field.getAttribute(handlerAttr);
        if (!handlerString) continue;

        // Parse handler string to extract function name
        var parsed = parseEventHandler(handlerString);
        var handlerName = parsed.name;

        // Determine current DOM value and server default
        var tagName = field.tagName.toLowerCase();
        var fieldType = (field.type || '').toLowerCase();
        var domValue;
        var serverDefault;

        if (fieldType === 'checkbox') {
            domValue = field.checked;
            serverDefault = field.hasAttribute('checked');
        } else if (fieldType === 'radio') {
            domValue = field.checked;
            serverDefault = field.hasAttribute('checked');
        } else if (tagName === 'select') {
            domValue = field.value;
            // Server default: the option with 'selected' attribute, or the first option
            var selectedOption = field.querySelector('option[selected]');
            serverDefault = selectedOption ? selectedOption.value : (field.options.length > 0 ? field.options[0].value : '');
        } else {
            // text, textarea, number, email, etc.
            domValue = field.value;
            serverDefault = field.getAttribute('value') || (tagName === 'textarea' ? field.defaultValue : '');
        }

        // Skip if DOM value matches server default (avoid unnecessary server work)
        if (domValue === serverDefault) continue;

        // Build event params matching dj-change param structure
        var value = (fieldType === 'checkbox' || fieldType === 'radio') ? domValue : domValue;
        var fieldName = field.name || field.id || null;
        var params = { value: value, field: fieldName };

        // Add positional arguments from handler syntax if present
        if (parsed.args.length > 0) {
            params._args = parsed.args;
        }

        // _target: include triggering field's name
        params._target = fieldName;

        pendingEvents.push({ handlerName: handlerName, params: params });
    }

    // Fire events sequentially to avoid server race conditions
    if (pendingEvents.length > 0) {
        if (globalThis.djustDebug) console.log('[LiveView] Form recovery: restoring ' + pendingEvents.length + ' field(s)');
        var fireSequentially = function(index) {
            if (index >= pendingEvents.length) return;
            var evt = pendingEvents[index];
            void handleEvent(evt.handlerName, evt.params).then(function() { fireSequentially(index + 1); });
        };
        fireSequentially(0);
    }
}

// Export for testing and for createNodeFromVNode to mark VDOM-created elements as bound
window.djust.bindLiveViewEvents = bindLiveViewEvents;
window.djust.reinitAfterDOMUpdate = reinitAfterDOMUpdate;
window.djust.installDelegatedListeners = installDelegatedListeners;
window.djust._isHandlerBound = _isHandlerBound;
window.djust._markHandlerBound = _markHandlerBound;
window.djust._processAutoRecover = _processAutoRecover;
window.djust._processFormRecovery = _processFormRecovery;
window.djust._isReconnect = false;
