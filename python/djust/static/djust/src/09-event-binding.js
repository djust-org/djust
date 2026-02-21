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

                // Read attribute at fire time so morphElement attribute updates take effect
                const parsed = parseEventHandler(element.getAttribute('dj-click') || '');

                // dj-confirm: show confirmation dialog before sending event
                if (!checkDjConfirm(element)) {
                    return; // User cancelled
                }

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

                // Phase 4: Check if event is from a component
                const componentId = getComponentId(e.currentTarget);
                if (componentId) {
                    params.component_id = componentId;
                }

                // Embedded LiveView: route event to correct child view
                const embeddedViewId = getEmbeddedViewId(e.currentTarget);
                if (embeddedViewId) {
                    params.view_id = embeddedViewId;
                }

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

                // dj-confirm: show confirmation dialog before sending event
                if (!checkDjConfirm(e.target)) {
                    return; // User cancelled
                }

                const formData = new FormData(e.target);
                const params = Object.fromEntries(formData.entries());

                // Phase 4: Check if event is from a component
                const componentId = getComponentId(e.target);
                if (componentId) {
                    params.component_id = componentId;
                }

                // Embedded LiveView: route event to correct child view
                const embeddedViewId = getEmbeddedViewId(e.target);
                if (embeddedViewId) {
                    params.view_id = embeddedViewId;
                }

                // Pass target element for optimistic updates (Phase 3)
                params._targetElement = e.target;

                await handleEvent(submitHandler, params);
                e.target.reset();
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
            const componentId = getComponentId(element);
            if (componentId) {
                params.component_id = componentId;
            }
            // Embedded LiveView: route event to correct child view
            const embeddedViewId = getEmbeddedViewId(element);
            if (embeddedViewId) {
                params.view_id = embeddedViewId;
            }
            return params;
        }

        // Handle dj-change events
        const changeHandler = element.getAttribute('dj-change');
        if (changeHandler && !_isHandlerBound(element, 'change')) {
            _markHandlerBound(element, 'change');
            // Parse handler string to extract function name and arguments
            const parsedChange = parseEventHandler(changeHandler);

            const changeHandlerFn = async (e) => {
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

                // Add target element for loading state (consistent with other handlers)
                params._targetElement = e.target;

                // Handle dj-target for scoped updates
                const targetSelector = element.getAttribute('dj-target');
                if (targetSelector) {
                    params._djTargetSelector = targetSelector;
                }

                if (globalThis.djustDebug) {
                    if (globalThis.djustDebug) console.log(`[LiveView] dj-change handler: value="${value}", params=`, params);
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
                // dj-confirm: show confirmation dialog before sending event
                if (!checkDjConfirm(element)) {
                    return; // User cancelled
                }

                const params = buildFormEventParams(e.target, e.target.value);
                if (parsedInput.args.length > 0) {
                    params._args = parsedInput.args;
                }
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

                    // Phase 4: Check if event is from a component
                    const componentId = getComponentId(e.target);
                    if (componentId) {
                        params.component_id = componentId;
                    }

                    // Embedded LiveView: route event to correct child view
                    const embeddedViewId = getEmbeddedViewId(e.target);
                    if (embeddedViewId) {
                        params.view_id = embeddedViewId;
                    }

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

    // Re-scan dj-loading attributes after DOM updates so dynamically
    // added elements (e.g. inside modals) get registered.
    globalLoadingManager.scanAndRegister();
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

// Export for testing and for createNodeFromVNode to mark VDOM-created elements as bound
window.djust.bindLiveViewEvents = bindLiveViewEvents;
window.djust._isHandlerBound = _isHandlerBound;
window.djust._markHandlerBound = _markHandlerBound;
