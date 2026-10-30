// ============================================================================
// Colocated JS Hooks — Phoenix 1.1 parity
// ============================================================================
//
// Extract <script type="djust/hook"> tags emitted by the {% colocated_hook %}
// template tag and register them into window.djust.hooks for the dj-hook
// runtime to mount.
//
// Usage (template):
//   {% load live_tags %}
//   {% colocated_hook "Chart" %}
//       hook.mounted = function() { renderChart(this.el); };
//       hook.updated = function() { renderChart(this.el); };
//   {% endcolocated_hook %}
//   <canvas dj-hook="Chart"></canvas>
//
// With namespacing (DJUST_CONFIG = {"hook_namespacing": "strict"}) the server
// emits the tag's data-hook as e.g. "myapp.views.DashboardView.Chart"; the
// client registers that exact key, and dj-hook="myapp.views.DashboardView.Chart"
// in the template resolves correctly.
//
// SECURITY BOUNDARY
// -----------------
// The script body is template-author-controlled, not user-supplied, so
// running it is at the same trust level as any other JS inside a Django
// template. The {% colocated_hook %} tag escapes `</script>` in the body to
// prevent premature tag close.
//
// When the request has a CSP nonce the tag carries it, and the body runs in a
// script element created with that nonce: a strict nonce CSP allows it with
// neither 'unsafe-eval' nor 'unsafe-inline'. Without a nonce the body is
// evaluated with `new Function(...)`.
// ============================================================================

(function initColocatedHooks() {
    globalThis.djust = globalThis.djust || {};

    /**
     * Walk the given root and register every <script type="djust/hook">
     * definition it finds. Idempotent via the data-djust-hook-registered
     * sentinel — safe to call on every DOM morph.
     *
     * @param {ParentNode} [root=document] - where to search
     */
    function extractAndRegister(root) {
        root = root || document;
        const scripts = root.querySelectorAll('script[type="djust/hook"]');
        for (const scriptEl of scripts) {
            if (scriptEl.dataset.djustHookRegistered === '1') continue;
            const hookName = scriptEl.getAttribute('data-hook');
            if (!hookName) {
                // Mark as processed so we don't keep scanning it.
                scriptEl.dataset.djustHookRegistered = '1';
                continue;
            }
            const nonce = scriptEl.nonce || scriptEl.getAttribute('nonce');
            if (nonce) {
                _registerWithNonce(scriptEl, hookName, nonce);
                continue;
            }
            try {
                // Convention: the body assigns to a local `hook` object.
                // We wrap in an IIFE-factory so users don't have to return
                // the hook themselves.
                //
                // `new Function` is intentional here: the template body is
                // inert text (we set type="djust/hook" on the emitter so
                // the browser doesn't auto-execute it). Running it through
                // `new Function` is the registration step. The body is
                // authored by the same people who write the template —
                // the same trust boundary as any other inline template
                // script. The Python emitter escapes `</script>` in all
                // casings to prevent tag-breakout.
                // eslint-disable-next-line no-new-func
                const factory = new Function(
                    'return (function() { const hook = {}; ' +
                    scriptEl.textContent +
                    '; return hook; })()'
                );
                const definition = factory();
                globalThis.djust.hooks = globalThis.djust.hooks || {};
                // eslint-disable-next-line security/detect-object-injection
                globalThis.djust.hooks[hookName] = definition;
                scriptEl.dataset.djustHookRegistered = '1';
                if (globalThis.djustDebug) {
                    console.debug('[colocated-hook] Registered %s', hookName);
                }
            } catch (err) {
                if (globalThis.djustDebug) {
                    console.warn(
                        '[colocated-hook] Failed to register %s: %s',
                        hookName,
                        err && err.message
                    );
                }
                scriptEl.dataset.djustHookRegistered = 'error';
            }
        }
    }

    /**
     * Run a hook body under a strict CSP: a script element carrying the
     * page's nonce is allowed where `new Function` ('unsafe-eval') is not.
     * The inserted script runs synchronously and assigns the hook itself.
     */
    function _registerWithNonce(scriptEl, hookName, nonce) {
        const runner = document.createElement('script');
        runner.setAttribute('nonce', nonce);
        runner.nonce = nonce;
        runner.textContent =
            '(function(){const hook={};' + scriptEl.textContent +
            '\n;(globalThis.djust.hooks=globalThis.djust.hooks||{})[' +
            JSON.stringify(hookName) + ']=hook;})();';
        (document.head || document.documentElement).appendChild(runner);
        runner.remove();
        // eslint-disable-next-line security/detect-object-injection
        const registered = !!(globalThis.djust.hooks && globalThis.djust.hooks[hookName]);
        scriptEl.dataset.djustHookRegistered = registered ? '1' : 'error';
        if (globalThis.djustDebug) {
            console.debug('[colocated-hook] %s %s with the page nonce',
                registered ? 'Registered' : 'Could not register', hookName);
        }
    }

    globalThis.djust.extractColocatedHooks = extractAndRegister;
})();
