
// Form polish — v0.5.1 batch (dj-no-submit, dj-trigger-action)
//
// dj-no-submit="enter"
//   Prevent form submission when the user presses Enter in a text-ish input.
//   Stops the #1 form UX annoyance: users press Enter to confirm a field and
//   accidentally submit the entire form. Multi-line inputs (textarea) and
//   submit buttons are exempt — pressing Enter in a textarea still inserts a
//   newline, and clicking a submit button still submits.
//
// dj-trigger-action
//   After a successful djust validation round-trip (no server error), bridge
//   to a standard HTML form POST. Essential for OAuth redirects, payment
//   gateway handoffs, and anywhere the final step needs a real browser POST.
//   Usage: the server calls ``self.trigger_submit(selector)`` (or pushes a
//   ``dj:trigger-submit`` event with ``{"selector": "#form-id"}``); the
//   client finds the matching form and calls its native ``.submit()``.

const _TEXT_INPUT_TYPES = new Set([
    'text', 'search', 'email', 'url', 'tel', 'password', 'number'
]);

function _isEnterKey(event) {
    return event.key === 'Enter' && !event.shiftKey && !event.ctrlKey && !event.metaKey && !event.altKey;
}

function _isEligibleEnterTarget(target) {
    if (!target || target.tagName !== 'INPUT') return false;
    const type = (target.type || 'text').toLowerCase();
    return _TEXT_INPUT_TYPES.has(type);
}

function _installFormPolishListeners() {
    // dj-no-submit="enter" — swallow Enter-key submits from text inputs.
    document.addEventListener('keydown', function (e) {
        if (!_isEnterKey(e)) return;
        const target = e.target;
        if (!_isEligibleEnterTarget(target)) return;
        const form = target.closest('form[dj-no-submit]');
        if (!form) return;
        const modes = (form.getAttribute('dj-no-submit') || '')
            .split(/[,\s]+/)
            .map(s => s.trim())
            .filter(Boolean);
        if (modes.length === 0 || modes.includes('enter')) {
            e.preventDefault();
            if (globalThis.djustDebug) {
                djLog('[form-polish] suppressed Enter-key submit on', form);
            }
        }
    }, { capture: true });

    // dj-trigger-action — native POST bridge. Listen for a push event from
    // the server carrying ``{"selector": "..."}`` (or "form_id"). Find the
    // form, ensure it carries ``dj-trigger-action``, and submit it natively.
    const handleTriggerAction = function (detail) {
        if (!detail) return;
        const selector = detail.selector || (detail.form_id ? '#' + detail.form_id : null);
        if (!selector) return;
        let form;
        try {
            form = document.querySelector(selector);
        } catch (err) {
            if (globalThis.djustDebug) {
                djLog('[form-polish] invalid dj:trigger-submit selector:', selector, err);
            }
            return;
        }
        if (!form || form.tagName !== 'FORM') {
            if (globalThis.djustDebug) {
                djLog('[form-polish] dj:trigger-submit: no matching <form> for', selector);
            }
            return;
        }
        if (!form.hasAttribute('dj-trigger-action')) {
            if (globalThis.djustDebug) {
                djLog('[form-polish] refusing to submit form without dj-trigger-action:', form);
            }
            return;
        }
        // Native submit — triggers the browser's normal POST flow, bypassing
        // djust's WS handler for this final step.
        form.submit();
    };

    // Server-pushed events arrive as a ``djust:push_event`` CustomEvent on
    // ``window`` with ``detail = {event, payload}``. Filter to the
    // ``dj:trigger-submit`` subtype and forward the payload.
    window.addEventListener('djust:push_event', function (e) {
        if (!e || !e.detail || e.detail.event !== 'dj:trigger-submit') return;
        handleTriggerAction(e.detail.payload);
    });
}

// Install on initial load and keep it simple — the keydown listener attaches
// to ``document`` so DOM morphs don't need to re-register.
if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', _installFormPolishListeners);
    } else {
        _installFormPolishListeners();
    }
}

globalThis.djust = globalThis.djust || {};
globalThis.djust.formPolish = {
    _isEnterKey,
    _isEligibleEnterTarget,
};
