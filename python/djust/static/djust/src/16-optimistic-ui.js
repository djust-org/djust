// ============================================================================
// Optimistic UI and Enhanced Debounce
// ============================================================================

// Global storage for optimistic updates
const optimisticUpdates = new Map();
const optimisticCounters = new Map();

/**
 * Apply optimistic update to DOM element based on dj-optimistic attribute
 * @param {HTMLElement} element - Target element
 * @param {string} eventName - Name of the event being triggered
 */
function applyOptimisticUpdate(element, eventName) {
    const optimisticAttr = element.getAttribute('dj-optimistic');
    if (!optimisticAttr) {
        return null;
    }

    // Parse optimistic attribute: "field:value" or "field:!field"
    const [fieldPath, valueExpr] = optimisticAttr.split(':', 2);
    if (!fieldPath || valueExpr === undefined) {
        console.warn('[djust:optimistic] Invalid dj-optimistic format, expected "field:value"');
        return null;
    }

    // Find target element for the update (could be the element itself or a data-target)
    const targetSelector = element.getAttribute('dj-target');
    const targetElement = targetSelector 
        ? document.querySelector(targetSelector)
        : element;

    if (!targetElement) {
        console.warn('[djust:optimistic] Target element not found for optimistic update');
        return null;
    }

    // Store original state before applying optimistic update
    const updateId = `${eventName}-${Date.now()}-${Math.random()}`;
    const originalState = {
        attributes: new Map(),
        textContent: targetElement.textContent,
        innerHTML: targetElement.innerHTML,
        dataset: { ...targetElement.dataset }
    };

    // Store all current attributes
    for (let attr of targetElement.attributes) {
        originalState.attributes.set(attr.name, attr.value);
    }

    optimisticUpdates.set(updateId, {
        element: targetElement,
        originalState,
        fieldPath,
        valueExpr
    });

    // Apply the optimistic update
    applyOptimisticValue(targetElement, fieldPath, valueExpr, originalState);

    return updateId;
}

/**
 * Apply optimistic value to the target element
 * @param {HTMLElement} targetElement - Element to update
 * @param {string} fieldPath - Field path (supports dot notation)
 * @param {string} valueExpr - Value expression (literal value or !field for toggle)
 * @param {Object} originalState - Original state for reference
 */
function applyOptimisticValue(targetElement, fieldPath, valueExpr, originalState) {
    // Handle toggle expressions like "!liked"
    if (valueExpr.startsWith('!')) {
        const toggleField = valueExpr.slice(1);
        const currentValue = getFieldValue(targetElement, toggleField, originalState);
        const newValue = !coerceToBoolean(currentValue);
        setFieldValue(targetElement, fieldPath, newValue);
        return;
    }

    // Handle literal values
    const newValue = parseOptimisticValue(valueExpr);
    setFieldValue(targetElement, fieldPath, newValue);
}

/**
 * Get current field value from element
 * @param {HTMLElement} element - Target element
 * @param {string} fieldPath - Field path
 * @param {Object} originalState - Original state for fallback
 * @returns {any} Current field value
 */
function getFieldValue(element, fieldPath, originalState) {
    // Handle data attributes
    if (fieldPath.startsWith('data-')) {
        const dataKey = fieldPath.slice(5).replace(/-/g, '');
        return element.dataset[dataKey] || originalState.dataset[dataKey];
    }

    // Handle class-related fields
    if (fieldPath === 'class') {
        return element.className;
    }

    // Handle text content
    if (fieldPath === 'textContent' || fieldPath === 'text') {
        return element.textContent;
    }

    // Handle innerHTML
    if (fieldPath === 'innerHTML' || fieldPath === 'html') {
        return element.innerHTML;
    }

    // Handle other attributes
    return element.getAttribute(fieldPath) || originalState.attributes.get(fieldPath);
}

/**
 * Set field value on element
 * @param {HTMLElement} element - Target element
 * @param {string} fieldPath - Field path
 * @param {any} value - New value
 */
function setFieldValue(element, fieldPath, value) {
    // Handle data attributes
    if (fieldPath.startsWith('data-')) {
        const dataKey = fieldPath.slice(5).replace(/-/g, '');
        element.dataset[dataKey] = String(value);
        return;
    }

    // Handle class-related fields
    if (fieldPath === 'class') {
        element.className = String(value);
        return;
    }

    // Handle text content
    if (fieldPath === 'textContent' || fieldPath === 'text') {
        element.textContent = String(value);
        return;
    }

    // Handle innerHTML
    if (fieldPath === 'innerHTML' || fieldPath === 'html') {
        element.innerHTML = String(value);
        return;
    }

    // Handle boolean attributes
    if (typeof value === 'boolean') {
        if (value) {
            element.setAttribute(fieldPath, '');
        } else {
            element.removeAttribute(fieldPath);
        }
        return;
    }

    // Handle other attributes
    element.setAttribute(fieldPath, String(value));
}

/**
 * Parse optimistic value from string
 * @param {string} valueExpr - Value expression
 * @returns {any} Parsed value
 */
function parseOptimisticValue(valueExpr) {
    // Handle quoted strings
    if ((valueExpr.startsWith('"') && valueExpr.endsWith('"')) ||
        (valueExpr.startsWith("'") && valueExpr.endsWith("'"))) {
        return valueExpr.slice(1, -1);
    }

    // Handle booleans
    if (valueExpr === 'true') return true;
    if (valueExpr === 'false') return false;

    // Handle null
    if (valueExpr === 'null') return null;

    // Handle numbers
    if (/^-?\d+$/.test(valueExpr)) {
        return parseInt(valueExpr, 10);
    }
    if (/^-?\d*\.\d+$/.test(valueExpr)) {
        return parseFloat(valueExpr);
    }

    // Return as string
    return valueExpr;
}

/**
 * Coerce value to boolean
 * @param {any} value - Value to coerce
 * @returns {boolean} Boolean value
 */
function coerceToBoolean(value) {
    if (typeof value === 'boolean') return value;
    if (typeof value === 'string') {
        return ['true', '1', 'yes', 'on', 'checked'].includes(value.toLowerCase());
    }
    return Boolean(value);
}

/**
 * Revert optimistic update
 * @param {string} updateId - Update ID to revert
 */
function revertOptimisticUpdate(updateId) {
    const update = optimisticUpdates.get(updateId);
    if (!update) {
        return;
    }

    const { element, originalState } = update;

    // Restore original attributes
    // First, remove all current attributes
    const currentAttrs = [...element.attributes];
    currentAttrs.forEach(attr => {
        if (!originalState.attributes.has(attr.name)) {
            element.removeAttribute(attr.name);
        }
    });

    // Then restore original attributes
    for (let [name, value] of originalState.attributes) {
        element.setAttribute(name, value);
    }

    // Restore dataset
    Object.keys(element.dataset).forEach(key => {
        delete element.dataset[key];
    });
    Object.assign(element.dataset, originalState.dataset);

    // Restore content
    element.innerHTML = originalState.innerHTML;

    optimisticUpdates.delete(updateId);
}

/**
 * Clear optimistic update without reverting (called on successful server response)
 * @param {string} updateId - Update ID to clear
 */
function clearOptimisticUpdate(updateId) {
    optimisticUpdates.delete(updateId);
}

/**
 * Enhanced debounce function that works with any event type
 * @param {HTMLElement} element - Target element
 * @param {string} eventType - Event type (click, input, change, etc.)
 * @param {Function} handler - Event handler function
 * @param {number} delay - Delay in milliseconds
 * @returns {Function} Debounced event handler
 */
function createDebouncedHandler(element, eventType, handler, delay) {
    const debounceKey = `${eventType}-${element.id || Math.random()}`;
    
    return function debouncedHandler(...args) {
        // Clear existing timeout for this element/event combination
        if (window.djustDebounceTimeouts) {
            clearTimeout(window.djustDebounceTimeouts[debounceKey]);
        } else {
            window.djustDebounceTimeouts = {};
        }

        // Set new timeout
        window.djustDebounceTimeouts[debounceKey] = setTimeout(() => {
            handler.apply(this, args);
            delete window.djustDebounceTimeouts[debounceKey];
        }, delay);
    };
}

/**
 * Enhanced throttle function that works with any event type
 * @param {HTMLElement} element - Target element
 * @param {string} eventType - Event type (click, input, change, etc.)
 * @param {Function} handler - Event handler function
 * @param {number} delay - Delay in milliseconds
 * @returns {Function} Throttled event handler
 */
function createThrottledHandler(element, eventType, handler, delay) {
    const throttleKey = `${eventType}-${element.id || Math.random()}`;
    
    return function throttledHandler(...args) {
        if (!window.djustThrottleTimestamps) {
            window.djustThrottleTimestamps = {};
        }

        const now = Date.now();
        const lastCall = window.djustThrottleTimestamps[throttleKey] || 0;

        if (now - lastCall >= delay) {
            window.djustThrottleTimestamps[throttleKey] = now;
            handler.apply(this, args);
        }
    };
}

/**
 * Get debounce/throttle configuration for an element
 * @param {HTMLElement} element - Target element
 * @returns {Object|null} Configuration object with type and delay, or null
 */
function getRateLimitConfig(element) {
    // Check for explicit dj-debounce
    if (element.hasAttribute('dj-debounce')) {
        const delay = parseInt(element.getAttribute('dj-debounce'), 10);
        return delay > 0 ? { type: 'debounce', delay } : null;
    }

    // Check for explicit dj-throttle
    if (element.hasAttribute('dj-throttle')) {
        const delay = parseInt(element.getAttribute('dj-throttle'), 10);
        return delay > 0 ? { type: 'throttle', delay } : null;
    }

    return null;
}

/**
 * Wrap event handler with rate limiting if configured
 * @param {HTMLElement} element - Target element
 * @param {string} eventType - Event type
 * @param {Function} handler - Original handler
 * @returns {Function} Wrapped handler (or original if no rate limiting)
 */
function wrapWithRateLimit(element, eventType, handler) {
    const config = getRateLimitConfig(element);
    if (!config) {
        return handler;
    }

    if (config.type === 'debounce') {
        return createDebouncedHandler(element, eventType, handler, config.delay);
    } else if (config.type === 'throttle') {
        return createThrottledHandler(element, eventType, handler, config.delay);
    }

    return handler;
}

// Export to global namespace
window.djust = window.djust || {};
window.djust.optimistic = {
    applyOptimisticUpdate,
    revertOptimisticUpdate,
    clearOptimisticUpdate,
    optimisticUpdates
};

window.djust.rateLimit = {
    createDebouncedHandler,
    createThrottledHandler,
    getRateLimitConfig,
    wrapWithRateLimit
};