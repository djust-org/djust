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
// The bound element is a form control (input, textarea, select) or a
// contenteditable element, whose text is the value. Any other element is bound
// by the plain and unnumbered forms only (dj-model, dj-model.lazy,
// dj-model.debounce).
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
    let source = null;
    let lazy = false;
    let debounce = 0;

    for (let i = 0; i < attrs.length; i++) {
        // eslint-disable-next-line security/detect-object-injection
        const name = attrs[i].name;
        if (name === 'dj-model') {
            // eslint-disable-next-line security/detect-object-injection
            field = attrs[i].value;
            source = name;
        } else if (name === 'dj-model.lazy') {
            // eslint-disable-next-line security/detect-object-injection
            field = attrs[i].value;
            source = name;
            lazy = true;
        } else if (name.startsWith('dj-model.debounce')) {
            // eslint-disable-next-line security/detect-object-injection
            field = attrs[i].value;
            source = name;
            const match = name.match(/debounce-?(\d+)/);
            debounce = match ? parseInt(match[1], 10) : 300;
        }
    }

    return { field, lazy, debounce, source };
}

/**
 * Get the current value from a form element.
 */
function _isContentEditable(el) {
    // A control inside an editable container reports isContentEditable too, but
    // its value is its own.
    if (el.matches('input, textarea, select')) return false;
    if (el.isContentEditable === true) return true;
    const attr = el.getAttribute('contenteditable');
    return attr !== null && attr.toLowerCase() !== 'false';
}

function _getElementValue(el) {
    if (_isContentEditable(el)) {
        // innerText keeps the line breaks the user typed; jsdom has none.
        return typeof el.innerText === 'string' ? el.innerText : el.textContent;
    }
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
function _sendModelUpdate(field, value, el) {
    // Fast path: send directly via WebSocket (synchronous)
    const inst = window.djust.liveViewInstance;
    // The element addresses the event to the view it lives in (#3252).
    if (inst && inst.sendEvent && inst.sendEvent('update_model', { field, value }, null, slotIdFor(el))) {
        return;
    }
    // Fallback: handleEvent (includes HTTP fallback, loading states)
    handleEvent('update_model', markSlotOf({ field, value }, el));
}

/**
 * True when `el` is bound and its dj-model attribute is as it was at bind time,
 * so re-parsing it would change nothing. Two property reads and one attribute
 * lookup instead of the attribute walk _bindModel does; anything that does not
 * match (a changed value, a renamed or removed attribute, an attribute added)
 * falls through to _bindModel, which rebuilds the handler (#2858).
 */
function _modelUnchanged(el) {
    const source = el._djustModelSource;
    return el._djustModelBound === true
        && typeof source === 'string'
        && el.attributes.length === el._djustModelAttrCount
        && el.getAttribute(source) === el._djustModelSourceValue;
}

/**
 * Bind dj-model to a single element.
 */
function _bindModel(el) {
    const { field, lazy, debounce, source } = _parseModelAttr(el);

    // #2858 — the handler closure captures field / lazy / debounce parsed
    // from the attribute NAME + VALUE at bind time, and an element that
    // survives a morphdom patch keeps BOTH its listener and the
    // `_djustModelBound` marker, so the marker alone cannot justify the
    // skip (#2845/#2855 shape): skip only when the whole parsed tuple is
    // unchanged; evict the old listeners and rebuild from the current
    // attrs otherwise. Re-mark unconditionally after the eviction branch.
    const boundKey = (field || '') + '\u0000' + (lazy ? 'lazy' : '') + '\u0000' + debounce;
    if (el._djustModelBound) {
        if (el._djustModelBoundKey === boundKey) {
            // Same binding; an unrelated attribute came or went.
            el._djustModelAttrCount = el.attributes.length;
            return;
        }
        if (el._djustModelHandler) {
            const staleTypes = el._djustModelEventTypes || [];
            for (const t of staleTypes) {
                el.removeEventListener(t, el._djustModelHandler);
            }
        }
    }
    el._djustModelBound = true;
    el._djustModelBoundKey = boundKey;
    // What _modelUnchanged() compares against.
    el._djustModelSource = source;
    el._djustModelSourceValue = field;
    el._djustModelAttrCount = el.attributes.length;
    if (!field) return;

    // A contenteditable element fires `input` as it is typed in but no
    // `change`; leaving it is its commit point.
    const eventType = lazy ? (_isContentEditable(el) ? 'focusout' : 'change') : 'input';

    const handler = () => {
        const value = _getElementValue(el);

        if (debounce > 0) {
            // Keyed by the view the element lives in as well as the field: two
            // views binding the same field name must not drop each other's
            // pending update (#3355).
            const timerKey = `model:${slotIdFor(el) || ''}:${field}`;
            if (_modelDebounceTimers.has(timerKey)) {
                clearTimeout(_modelDebounceTimers.get(timerKey));
            }
            _modelDebounceTimers.set(timerKey, setTimeout(() => {
                _sendModelUpdate(field, value, el);
                _modelDebounceTimers.delete(timerKey);
            }, debounce));
        } else {
            _sendModelUpdate(field, value, el);
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

// The spellings a selector can name outright. Queried one selector at a time:
// a comma group is several times slower than its parts in some selector engines
// (jsdom's), and this runs after every DOM update.
const _MODEL_EXACT = ['[dj-model]', '[dj-model\\.lazy]', '[dj-model\\.debounce]'];
// `dj-model.debounce-N` carries its N in the attribute NAME, which no selector
// can match, so these elements are searched by attribute name instead.
const _MODEL_NUMBERED_CANDIDATES = ['input', 'textarea', 'select', '[contenteditable]'];

/**
 * Scan and bind all dj-model elements.
 *
 * Runs after every DOM update (#3334), so the common case must be cheap: a page
 * of already-bound inputs costs the native selector matches and a few property
 * reads per element, not an attribute walk (#3355).
 */
function bindModelElements(root) {
    root = root || document;
    _MODEL_EXACT.forEach(selector => {
        root.querySelectorAll(selector).forEach(el => {
            if (!_modelUnchanged(el)) _bindModel(el);
        });
    });

    // dj-model.debounce-N on a control (or contenteditable element)
    _MODEL_NUMBERED_CANDIDATES.forEach(selector => {
        root.querySelectorAll(selector).forEach(el => {
            if (_modelUnchanged(el)) return;
            const names = el.getAttributeNames();
            for (let i = 0; i < names.length; i++) {
                // eslint-disable-next-line security/detect-object-injection
                if (names[i].startsWith('dj-model')) {
                    _bindModel(el);
                    break;
                }
            }
        });
    });
}

// Export
window.djust.bindModelElements = bindModelElements;
