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
// The script body is evaluated via `new Function(...)`. This is safe at the
// same trust level as any other JS inside a Django template: the body is
// template-author-controlled, not user-supplied. The {% colocated_hook %} tag
// escapes `</script>` in the body to prevent premature tag close. Users on
// strict CSP without 'unsafe-eval' should not use this feature — register
// hooks via a nonce-bearing <script>window.djust.hooks.X = {...}</script>
// instead.
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

    globalThis.djust.extractColocatedHooks = extractAndRegister;
})();
