
/**
 * A password field (or one the page marks as holding a password) is never
 * read into a draft, an auto-recover envelope or `_collectFormData`, and never
 * written back from one (1.3 backport of #4067).
 */
function _isSecretControl(field) {
    if (field.type === 'password') return true;
    const autocomplete = (field.getAttribute('autocomplete') || '').toLowerCase();
    return /(^|\s)(new|current)-password(\s|$)/.test(autocomplete);
}

function _collectFormData(container) {
    const data = {};

    const fields = container.querySelectorAll('input, textarea, select');
    fields.forEach(field => {
        if (field.name && !UNSAFE_KEYS.includes(field.name) && !_isSecretControl(field)) {
            if (field.type === 'checkbox') {
                data[field.name] = field.checked;
            } else if (field.type === 'radio') {
                if (field.checked) {
                    data[field.name] = field.value;
                }
            } else {
                data[field.name] = field.value;
            }
        }
    });

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

    Object.entries(data).forEach(([name, value]) => {
        let field = container.querySelector(`[name="${name}"]`);

        if (!field) {
            field = container.querySelector(`#${name}`);
        }

        if (!field || (field.tagName === 'INPUT' && _isSecretControl(field))) return;

        if (field.tagName === 'INPUT') {
            if (field.type === 'checkbox') {
                field.checked = value;
            } else if (field.type === 'radio') {
                if (field.value === value) {
                    field.checked = true;
                }
            } else {
                field.value = value;
            }
        } else if (field.tagName === 'TEXTAREA') {
            field.value = value;
        } else if (field.tagName === 'SELECT') {
            field.value = value;
        } else if (field.getAttribute('contenteditable') === 'true') {
            field.innerHTML = value;
        }
    });

    if (globalThis.djustDebug) {
        djLog('[DraftMode] Restored form data:', data);
    }
}
