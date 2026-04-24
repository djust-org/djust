// ============================================================================
// Server Functions — djust.call(viewSlug, funcName, params)
// ============================================================================
// Same-origin RPC from the browser to an @server_function method on a
// LiveView. No re-render, no assigns diff — pure request/response. Rejects
// with an Error carrying {code, status, details} on non-2xx responses.
//
// CSRF: reads the hidden input (preferred) then falls back to the cookie.
// Mirrors the resolver in src/11-event-handler.js for consistency.

(function () {
    function _csrf() {
        try {
            const input = document.querySelector('[name=csrfmiddlewaretoken]');
            if (input && input.value) return input.value;
        } catch (_) { /* SSR / detached DOM */ }
        const m = (document.cookie || '').match(/(?:^|;\s*)csrftoken=([^;]+)/);
        return m ? m[1] : '';
    }

    async function call(viewSlug, funcName, params) {
        if (!viewSlug || !funcName) {
            throw new Error('djust.call requires (viewSlug, funcName)');
        }
        // #987: resolve the URL prefix through djust.apiUrl() so
        // FORCE_SCRIPT_NAME / api_patterns(prefix=...) sub-path deploys
        // work. djust.apiPrefix is set at bootstrap from the
        // {% djust_client_config %} meta tag (see 00-namespace.js).
        const url = window.djust.apiUrl(
            'call/' + encodeURIComponent(viewSlug) + '/' + encodeURIComponent(funcName) + '/'
        );
        const resp = await fetch(url, {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': _csrf(),
                'X-Requested-With': 'XMLHttpRequest',
            },
            body: JSON.stringify({ params: params || {} }),
        });
        let data = {};
        try { data = await resp.json(); } catch (_) { /* empty / non-json */ }
        if (!resp.ok) {
            const err = new Error(data.message || ('djust.call failed: ' + resp.status));
            err.code = data.error || 'http_error';
            err.status = resp.status;
            err.details = data.details;
            throw err;
        }
        return data.result;
    }

    window.djust = window.djust || {};
    window.djust.call = call;
})();
