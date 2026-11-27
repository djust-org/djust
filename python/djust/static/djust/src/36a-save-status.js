// ============================================================================
// Deferred-save status (#4164): a nonblocking notice, in every mode
// ============================================================================
//
// A `djust:error` frame that is a deferred state save (code `state_error` or
// `state_not_restored` with the server's literal `transient: true`) shows a
// small polite status in the corner instead of an error; a successful render
// of the same view/target clears it, and so does any other error for it.
//
// Split out of 36-error-overlay.js (#4320): this part is core, in every mode,
// while the DEBUG error overlay is the `error-overlay` client chunk, loaded
// only when `window.DEBUG_MODE` is true.

(function initSaveStatus() {
    const STATUS_ID = 'djust-save-status';
    const saving = new Map();

    function _owner(detail) {
        return JSON.stringify([
            typeof detail.view === 'string' ? detail.view : null,
            typeof detail.target_id === 'string' ? detail.target_id : null,
        ]);
    }

    function _clearSaving(detail) {
        const key = _owner(detail);
        const entry = saving.get(key);
        if (!entry) return;
        entry.remove();
        saving.delete(key);
        if (!saving.size) document.getElementById(STATUS_ID)?.remove();
    }

    function _showSaving(detail) {
        let status = document.getElementById(STATUS_ID);
        if (!status) {
            status = document.createElement('div');
            status.id = STATUS_ID;
            status.setAttribute('role', 'status');
            status.setAttribute('aria-live', 'polite');
            status.style.cssText = 'position:fixed;bottom:16px;right:16px;z-index:99998;' +
                'pointer-events:none;padding:12px;background:#1e293b;color:#fff;border-radius:8px;';
            document.body.appendChild(status);
        }
        const key = _owner(detail);
        let entry = saving.get(key);
        if (!entry) {
            entry = document.createElement('div');
            saving.set(key, entry);
            status.appendChild(entry);
        }
        // Only the message, never traceback or HTML. The server's literal
        // boolean distinguishes a deferred save from a persistent error.
        entry.textContent = typeof detail.error === 'string' ? detail.error : 'Saving your change…';
    }

    function _onError(e) {
        const detail = (e && e.detail) || {};
        const notice = detail.code === 'state_error' || detail.code === 'state_not_restored';
        if (notice && detail.transient === true) _showSaving(detail);
        else _clearSaving(detail);
    }

    if (typeof window !== 'undefined') {
        window.addEventListener('djust:error', _onError);
        window.addEventListener('djust:rendered', e => _clearSaving(e.detail || {}));
        for (const event of ['djust:before-navigate', 'pagehide']) {
            window.addEventListener(event, () => {
                saving.clear();
                document.getElementById(STATUS_ID)?.remove();
            });
        }
    }
})();
