// ============================================================================
// dj-model — Two-Way Data Binding
// ============================================================================
//
// Automatically syncs form input values with server-side view attributes.
//
// Usage in template:
//   <input type="text" dj-model="search_query" />
//   <textarea dj-model="description"></textarea>
//   <select dj-model="category">...</select>
//   <input type="checkbox" dj-model="is_active" />
//
// Options:
//   dj-model="field_name"              — sync on 'input' event (default)
//   dj-model.lazy="field_name"         — sync on 'change' event (blur)
//   dj-model.debounce-300="field_name" — debounce by 300ms
//
// The server-side ModelBindingMixin handles the 'update_model' event
// and sets the attribute on the view instance.
//
// ============================================================================

const _modelDebounceTimers = new Map();

/**
 * Parse dj-model attribute value and modifiers.
 * "field_name" → { field: "field_name", lazy: false, debounce: 0 }
 * With attribute dj-model.lazy="field_name" → { field: "field_name", lazy: true }
 */
function _parseModelAttr(el) {
    // Check for dj-model.lazy and dj-model.debounce-N
    const attrs = el.attributes;
    let field = null;
    let lazy = false;
    let debounce = 0;

    for (let i = 0; i < attrs.length; i++) {
        // eslint-disable-next-line security/detect-object-injection
        const name = attrs[i].name;
        if (name === 'dj-model') {
            // eslint-disable-next-line security/detect-object-injection
            field = attrs[i].value;
        } else if (name === 'dj-model.lazy') {
            // eslint-disable-next-line security/detect-object-injection
            field = attrs[i].value;
            lazy = true;
        } else if (name.startsWith('dj-model.debounce')) {
            // eslint-disable-next-line security/detect-object-injection
            field = attrs[i].value;
            const match = name.match(/debounce-?(\d+)/);
            debounce = match ? parseInt(match[1], 10) : 300;
        }
    }

    return { field, lazy, debounce };
}

/**
 * Get the current value from a form element.
 */
function _getElementValue(el) {
    if (el.type === 'checkbox') {
        return el.checked;
    }
    if (el.type === 'radio') {
        // For radio buttons, find the checked one in the same group
        const form = el.closest('form') || document;
        const checked = form.querySelector(`input[name="${el.name}"]:checked`);
        return checked ? checked.value : null;
    }
    if (el.tagName === 'SELECT' && el.multiple) {
        return Array.from(el.selectedOptions).map(o => o.value);
    }
    return el.value;
}

/**
 * Send update_model event to server.
 * Tries direct WebSocket first (synchronous, no loading states needed for model
 * binding), then falls back to handleEvent for HTTP-only scenarios.
 */
function _sendModelUpdate(field, value) {
    // Fast path: send directly via WebSocket (synchronous)
    const inst = window.djust.liveViewInstance;
    if (inst && inst.sendEvent && inst.sendEvent('update_model', { field, value })) {
        return;
    }
    // Fallback: handleEvent (includes HTTP fallback, loading states)
    handleEvent('update_model', { field, value });
}

/**
 * Bind dj-model to a single element.
 */
function _bindModel(el) {
    const { field, lazy, debounce } = _parseModelAttr(el);

    // #2858 — the handler closure captures field / lazy / debounce parsed
    // from the attribute NAME + VALUE at bind time, and an element that
    // survives a morphdom patch keeps BOTH its listener and the
    // `_djustModelBound` marker, so the marker alone cannot justify the
    // skip (#2845/#2855 shape): skip only when the whole parsed tuple is
    // unchanged; evict the old listeners and rebuild from the current
    // attrs otherwise. Re-mark unconditionally after the eviction branch.
    const boundKey = (field || '') + '\u0000' + (lazy ? 'lazy' : '') + '\u0000' + debounce;
    if (el._djustModelBound) {
        if (el._djustModelBoundKey === boundKey) return;
        if (el._djustModelHandler) {
            const staleTypes = el._djustModelEventTypes || [];
            for (const t of staleTypes) {
                el.removeEventListener(t, el._djustModelHandler);
            }
        }
    }
    el._djustModelBound = true;
    el._djustModelBoundKey = boundKey;
    if (!field) return;

    const eventType = lazy ? 'change' : 'input';

    const handler = () => {
        const value = _getElementValue(el);

        if (debounce > 0) {
            const timerKey = `model:${field}`;
            if (_modelDebounceTimers.has(timerKey)) {
                clearTimeout(_modelDebounceTimers.get(timerKey));
            }
            _modelDebounceTimers.set(timerKey, setTimeout(() => {
                _sendModelUpdate(field, value);
                _modelDebounceTimers.delete(timerKey);
            }, debounce));
        } else {
            _sendModelUpdate(field, value);
        }
    };

    el._djustModelHandler = handler;
    el._djustModelEventTypes = [eventType];
    el.addEventListener(eventType, handler);

    // For checkboxes and radios, also listen on change
    if (el.type === 'checkbox' || el.type === 'radio') {
        el.addEventListener('change', handler);
        el._djustModelEventTypes.push('change');
    }
}

/**
 * Scan and bind all dj-model elements.
 */
function bindModelElements(root) {
    root = root || document;
    const elements = root.querySelectorAll('[dj-model], [dj-model\\.lazy], [dj-model\\.debounce]');
    elements.forEach(_bindModel);

    // Also check for dj-model with modifiers via attribute prefix
    root.querySelectorAll('input, textarea, select').forEach(el => {
        for (let i = 0; i < el.attributes.length; i++) {
            // eslint-disable-next-line security/detect-object-injection
            if (el.attributes[i].name.startsWith('dj-model')) {
                _bindModel(el);
                break;
            }
        }
    });
}

// Export
window.djust.bindModelElements = bindModelElements;
