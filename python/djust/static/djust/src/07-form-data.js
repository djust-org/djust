
/**
 * The values of the named form controls in `fields`, keyed by name (#3978).
 *
 * A control that shares its name with others is one value, as the browser
 * submits it: a group of same-named checkboxes is the list of the checked
 * boxes' values (`[]` when none is), a radio group is the checked radio's
 * value (no entry when none is), and a `<select multiple>` is the list of its
 * selected options. A lone checkbox stays a boolean. Reading one control at a
 * time flattened a group to its last box.
 *
 * Shared by the draft manager, `_collectFormData` and the `dj-auto-recover`
 * envelope, so they read a group the same way.
 */
function _readControls(fields) {
    const data = {};
    const checkboxCount = new Map();
    fields.forEach(field => {
        if (field.type === 'checkbox' && field.name) {
            checkboxCount.set(field.name, (checkboxCount.get(field.name) || 0) + 1);
        }
    });
    fields.forEach(field => {
        const name = field.name;
        if (!name || UNSAFE_KEYS.includes(name)) return;
        if (field.type === 'checkbox') {
            if (checkboxCount.get(name) > 1) {
                // eslint-disable-next-line security/detect-object-injection
                if (!Array.isArray(data[name])) data[name] = [];
                // eslint-disable-next-line security/detect-object-injection
                if (field.checked) data[name].push(field.value);
            } else {
                // eslint-disable-next-line security/detect-object-injection
                data[name] = field.checked;
            }
        } else if (field.type === 'radio') {
            // eslint-disable-next-line security/detect-object-injection
            if (field.checked) data[name] = field.value;
        } else if (field.tagName === 'SELECT' && field.multiple) {
            // eslint-disable-next-line security/detect-object-injection
            data[name] = Array.from(field.selectedOptions, option => option.value);
        } else {
            // eslint-disable-next-line security/detect-object-injection
            data[name] = field.value;
        }
    });
    return data;
}

/**
 * Put a value `_readControls` produced back into one control of its group.
 *
 * A list ticks the checkboxes / selects the options whose value is in it; a
 * boolean (a lone checkbox, or a group saved before #3978) sets `checked`; a
 * radio is checked when its value is the saved one.
 */
function _writeControl(field, value) {
    if (field.type === 'checkbox') {
        field.checked = Array.isArray(value) ? value.includes(field.value) : !!value;
    } else if (field.type === 'radio') {
        if (value !== null && value !== undefined) field.checked = String(value) === field.value;
    } else if (field.tagName === 'SELECT' && field.multiple && Array.isArray(value)) {
        for (const option of field.options) option.selected = value.includes(option.value);
    } else {
        field.value = value;
    }
}

function _collectFormData(container) {
    const data = _readControls(Array.from(container.querySelectorAll('input, textarea, select')));

    const editables = container.querySelectorAll('[contenteditable="true"]');
    editables.forEach(editable => {
        const name = editable.getAttribute('name') || editable.id;
        // Prevent prototype pollution attacks
        if (name && !UNSAFE_KEYS.includes(name)) {
            // eslint-disable-next-line security/detect-object-injection
            data[name] = editable.innerHTML;
        }
    });

    return data;
}

function _restoreFormData(container, data) {
    if (!data) return;
    const has = name => Object.prototype.hasOwnProperty.call(data, name) && !UNSAFE_KEYS.includes(name);

    // Every control of a group, not only the first one with the name.
    container.querySelectorAll('input, textarea, select').forEach(field => {
        if (field.name && has(field.name)) _writeControl(field, data[field.name]);
    });

    container.querySelectorAll('[contenteditable="true"]').forEach(editable => {
        const name = editable.getAttribute('name') || editable.id;
        // eslint-disable-next-line security/detect-object-injection
        if (name && has(name)) editable.innerHTML = data[name];
    });

    if (globalThis.djustDebug) {
        djLog('[DraftMode] Restored form data:', data);
    }
}
