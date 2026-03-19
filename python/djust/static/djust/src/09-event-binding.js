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

function bindLiveViewEvents() {
    // Bind upload handlers (dj-upload, dj-upload-drop, dj-upload-preview)
    if (window.djust.uploads) {
        window.djust.uploads.bindHandlers();
    }

    // Bind navigation directives (dj-patch, dj-navigate)
    if (window.djust.navigation) {
        window.djust.navigation.bindDirectives();
    }

    // Find all interactive elements
    const allElements = document.querySelectorAll('*');
    allElements.forEach(element => {
        // Handle dj-click events
        const clickHandler = element.getAttribute('dj-click');
        if (clickHandler && !_isHandlerBound(element, 'click')) {
            _markHandlerBound(element, 'click');

            const clickHandlerFn = async (e) => {
                e.preventDefault();

                // dj-lock: skip if already locked
                if (_checkAndLock(element)) return;

                // Read attribute at fire time so morphElement attribute updates take effect
                const parsed = parseEventHandler(element.getAttribute('dj-click') || '');

                // dj-confirm: show confirmation dialog before sending event
                if (!checkDjConfirm(element)) {
                    return; // User cancelled
                }

                // dj-disable-with: disable and show loading text
                _applyDisableWith(element);

                // Apply optimistic update if specified
                let optimisticUpdateId = null;
                if (window.djust.optimistic) {
                    optimisticUpdateId = window.djust.optimistic.applyOptimisticUpdate(e.currentTarget, parsed.name);
                }

                // Extract all data-* attributes with type coercion support
                const params = extractTypedParams(element);

                // Add positional arguments from handler syntax if present
                // e.g., dj-click="set_period('month')" -> params._args = ['month']
                if (parsed.args.length > 0) {
                    params._args = parsed.args;
                }

                addEventContext(params, e.currentTarget);

                // Pass target element and optimistic update ID
                params._targetElement = e.currentTarget;
                params._optimisticUpdateId = optimisticUpdateId;

                // Handle dj-target for scoped updates
                const targetSelector = element.getAttribute('dj-target');
                if (targetSelector) {
                    params._djTargetSelector = targetSelector;
                }

                await handleEvent(parsed.name, params);
            };

            // Apply rate limiting if specified
            const wrappedHandler = window.djust.rateLimit
                ? window.djust.rateLimit.wrapWithRateLimit(element, 'click', clickHandlerFn)
                : clickHandlerFn;

            element.addEventListener('click', wrappedHandler);
        }

        // Handle dj-copy — client-side clipboard copy (no server round-trip)
        if (element.getAttribute('dj-copy') && !_isHandlerBound(element, 'copy')) {
            _markHandlerBound(element, 'copy');
            element.addEventListener('click', function(e) {
                e.preventDefault();
                // Read attribute at click time (not bind time) so morph updates take effect
                var currentValue = element.getAttribute('dj-copy');
                if (!currentValue) return;
                navigator.clipboard.writeText(currentValue).then(function() {
                    var original = element.textContent;
                    element.textContent = 'Copied!';
                    setTimeout(function() { element.textContent = original; }, 1500);
                });
            });
        }

        // Handle dj-submit events on forms
        const submitHandler = element.getAttribute('dj-submit');
        if (submitHandler && !_isHandlerBound(element, 'submit')) {
            _markHandlerBound(element, 'submit');
            element.addEventListener('submit', async (e) => {
                e.preventDefault();

                // dj-lock: skip if already locked
                if (_checkAndLock(e.target)) return;

                // dj-confirm: show confirmation dialog before sending event
                if (!checkDjConfirm(e.target)) {
                    return; // User cancelled
                }

                // dj-disable-with: disable submit buttons within the form
                const submitBtns = e.target.querySelectorAll('button[type="submit"][dj-disable-with]');
                submitBtns.forEach(btn => _applyDisableWith(btn));
                // Also check the submitter if it has dj-disable-with
                if (e.submitter && e.submitter.hasAttribute('dj-disable-with')) {
                    _applyDisableWith(e.submitter);
                }

                const formData = new FormData(e.target);
                const params = Object.fromEntries(formData.entries());

                // Merge dj-value-* attributes from the form element
                Object.assign(params, collectDjValues(e.target));

                addEventContext(params, e.target);

                // _target: include submitter name if available
                params._target = (e.submitter && (e.submitter.name || e.submitter.id)) || null;

                // Pass target element for optimistic updates (Phase 3)
                params._targetElement = e.target;

                await handleEvent(submitHandler, params);
            });
        }

        // Helper: Extract field name from element attributes
        // Priority: data-field (explicit) > name (standard) > id (fallback)
        function getFieldName(element) {
            if (element.dataset.field) {
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

        // Handle dj-change events
        const changeHandler = element.getAttribute('dj-change');
        if (changeHandler && !_isHandlerBound(element, 'change')) {
            _markHandlerBound(element, 'change');
            // Parse handler string to extract function name and arguments
            const parsedChange = parseEventHandler(changeHandler);

            const changeHandlerFn = async (e) => {
                // dj-lock: skip if already locked
                if (_checkAndLock(element)) return;

                // dj-confirm: show confirmation dialog before sending event
                if (!checkDjConfirm(element)) {
                    return; // User cancelled
                }

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
                    console.log(`[LiveView] dj-change handler: value="${value}", params=`, params);
                }
                await handleEvent(parsedChange.name, params);
            };

            // Apply rate limiting if specified
            const wrappedHandler = window.djust.rateLimit
                ? window.djust.rateLimit.wrapWithRateLimit(element, 'change', changeHandlerFn)
                : changeHandlerFn;

            element.addEventListener('change', wrappedHandler);
        }

        // Handle dj-input events (with smart debouncing/throttling)
        const inputHandler = element.getAttribute('dj-input');
        if (inputHandler && !_isHandlerBound(element, 'input')) {
            _markHandlerBound(element, 'input');
            // Parse handler string to extract function name and arguments
            const parsedInput = parseEventHandler(inputHandler);

            // Determine rate limit strategy
            const inputType = element.type || element.tagName.toLowerCase();
            const rateLimit = Object.prototype.hasOwnProperty.call(DEFAULT_RATE_LIMITS, inputType) ? DEFAULT_RATE_LIMITS[inputType] : { type: 'debounce', ms: 300 };

            // Check for explicit overrides
            if (element.hasAttribute('data-debounce')) {
                rateLimit.type = 'debounce';
                rateLimit.ms = parseInt(element.getAttribute('data-debounce'));
            } else if (element.hasAttribute('data-throttle')) {
                rateLimit.type = 'throttle';
                rateLimit.ms = parseInt(element.getAttribute('data-throttle'));
            }

            const handler = async (e) => {
                // dj-lock: skip if already locked
                if (_checkAndLock(element)) return;

                // dj-confirm: show confirmation dialog before sending event
                if (!checkDjConfirm(element)) {
                    return; // User cancelled
                }

                const params = buildFormEventParams(e.target, e.target.value);
                if (parsedInput.args.length > 0) {
                    params._args = parsedInput.args;
                }

                // _target: include triggering field's name (or id, or null)
                params._target = e.target.name || e.target.id || null;

                await handleEvent(parsedInput.name, params);
            };

            // Apply rate limiting wrapper
            let wrappedHandler;
            if (rateLimit.type === 'throttle') {
                wrappedHandler = throttle(handler, rateLimit.ms);
            } else {
                wrappedHandler = debounce(handler, rateLimit.ms);
            }

            element.addEventListener('input', wrappedHandler);
        }

        // Handle dj-blur events
        const blurHandler = element.getAttribute('dj-blur');
        if (blurHandler && !_isHandlerBound(element, 'blur')) {
            _markHandlerBound(element, 'blur');
            const parsedBlur = parseEventHandler(blurHandler);
            element.addEventListener('blur', async (e) => {
                // dj-lock: skip if already locked
                if (_checkAndLock(element)) return;

                // dj-confirm: show confirmation dialog before sending event
                if (!checkDjConfirm(element)) {
                    return; // User cancelled
                }

                const params = buildFormEventParams(e.target, e.target.value);
                if (parsedBlur.args.length > 0) {
                    params._args = parsedBlur.args;
                }
                await handleEvent(parsedBlur.name, params);
            });
        }

        // Handle dj-focus events
        const focusHandler = element.getAttribute('dj-focus');
        if (focusHandler && !_isHandlerBound(element, 'focus')) {
            _markHandlerBound(element, 'focus');
            const parsedFocus = parseEventHandler(focusHandler);
            element.addEventListener('focus', async (e) => {
                // dj-lock: skip if already locked
                if (_checkAndLock(element)) return;

                // dj-confirm: show confirmation dialog before sending event
                if (!checkDjConfirm(element)) {
                    return; // User cancelled
                }

                const params = buildFormEventParams(e.target, e.target.value);
                if (parsedFocus.args.length > 0) {
                    params._args = parsedFocus.args;
                }
                await handleEvent(parsedFocus.name, params);
            });
        }

        // Handle dj-keydown / dj-keyup events
        ['keydown', 'keyup'].forEach(eventType => {
            const keyHandler = element.getAttribute(`dj-${eventType}`);
            if (keyHandler && !_isHandlerBound(element, eventType)) {
                _markHandlerBound(element, eventType);

                const keyHandlerFn = async (e) => {
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
                };

                // Apply rate limiting if specified
                const wrappedHandler = window.djust.rateLimit
                    ? window.djust.rateLimit.wrapWithRateLimit(element, eventType, keyHandlerFn)
                    : keyHandlerFn;

                element.addEventListener(eventType, wrappedHandler);
            }
        });

        // Handle dj-poll — declarative polling
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

    // Sweep orphaned scoped listeners (elements removed from DOM by conditional rendering)
    _sweepOrphanedScopedListeners();

    // --- Feature 1: dj-window-* and dj-document-* event scoping ---
    const scopedPrefixes = [
        { prefix: 'dj-window-', target: window },
        { prefix: 'dj-document-', target: document },
    ];
    const scopedEventTypes = ['keydown', 'keyup', 'click', 'scroll', 'resize'];

    // Collect all elements that have any dj-window-* or dj-document-* attributes
    // by scanning once through allElements (already queried above).
    for (const { prefix, target } of scopedPrefixes) {
        for (const evtType of scopedEventTypes) {
            // scroll and resize only make sense on window
            if (target === document && (evtType === 'scroll' || evtType === 'resize')) continue;

            const attrBase = prefix + evtType;
            // Scan all elements for attributes starting with this prefix+eventType
            // (covers both exact matches and key-modifier variants like dj-window-keydown.escape)
            allElements.forEach(element => {
                for (const attr of element.attributes) {
                    if (!attr.name.startsWith(attrBase)) continue;
                    const bindType = attr.name; // e.g. 'dj-window-keydown' or 'dj-window-keydown.escape'
                    if (_isHandlerBound(element, bindType)) continue;
                    _markHandlerBound(element, bindType);

                    // Parse handler name and optional key modifier
                    const attrValue = attr.value;
                    const suffix = attr.name.slice(attrBase.length); // '' or '.escape'
                    const requiredKey = suffix.startsWith('.') ? _normalizeKeyName(suffix.slice(1)) : null;

                    // Parse handler name (supports handler(args) syntax)
                    const parsed = parseEventHandler(attrValue);

                    const scopedHandler = async (e) => {
                        // Key filtering for keydown/keyup
                        if (requiredKey && e.key !== requiredKey) return;

                        // Build params from the declaring element
                        const params = extractTypedParams(element);
                        addEventContext(params, element);

                        // Add event-specific params
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

                        if (parsed.args.length > 0) {
                            params._args = parsed.args;
                        }

                        await handleEvent(parsed.name, params);
                    };

                    // Apply default throttle for scroll/resize
                    let finalHandler = scopedHandler;
                    if (evtType === 'scroll' || evtType === 'resize') {
                        const throttleMs = parseInt(element.getAttribute('data-throttle'), 10) || 150;
                        finalHandler = throttle(scopedHandler, throttleMs);
                    } else if (element.hasAttribute('data-debounce')) {
                        finalHandler = debounce(scopedHandler, parseInt(element.getAttribute('data-debounce'), 10));
                    } else if (element.hasAttribute('data-throttle')) {
                        finalHandler = throttle(scopedHandler, parseInt(element.getAttribute('data-throttle'), 10));
                    }

                    _addScopedListener(element, target, evtType, finalHandler, false);
                }
            });
        }
    }

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
function reinitAfterDOMUpdate() {
    initReactCounters();
    initTodoItems();
    bindLiveViewEvents();
    if (typeof updateHooks === 'function') { updateHooks(); }
}

// Export for testing and for createNodeFromVNode to mark VDOM-created elements as bound
window.djust.bindLiveViewEvents = bindLiveViewEvents;
window.djust.reinitAfterDOMUpdate = reinitAfterDOMUpdate;
window.djust._isHandlerBound = _isHandlerBound;
window.djust._markHandlerBound = _markHandlerBound;
