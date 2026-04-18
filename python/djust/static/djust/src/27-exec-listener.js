// ============================================================================
// djust:exec auto-executor (ADR-002 Phase 1a — server-initiated JS Commands)
// ============================================================================
//
// Listens for server-pushed `djust:exec` events and runs the JS Command chain
// they carry via `window.djust.js._executeOps(ops, null)`. Framework-provided;
// users never write or register a hook for this — it's bound once when
// client.js loads and handles every `push_commands()` call from the server.
//
// Transport: the server calls `self.push_commands(chain)` → the mixin calls
// `push_event("djust:exec", {"ops": chain.ops})` → the WebSocket consumer
// flushes the push-event queue → client.js dispatches a global
// `djust:push_event` CustomEvent on `window` with `detail: {event, payload}`
// (see 03-websocket.js case 'push_event'). We filter for `event === 'djust:exec'`
// and interpret the `payload.ops` array using the same `_executeOps` function
// used by inline `dj-click="[[...]]"` JSON chains and fluent-API `.exec()` calls
// from dj-hook code.
//
// There is no HTML markup, no hook registration, and no user setup required.
// Every djust page that loads client.js gets the auto-executor for free.
// ============================================================================

(function() {
    if (!window.djust) window.djust = {};

    function handleDjustExec(event) {
        const detail = event.detail || {};
        const eventName = detail.event;
        const payload = detail.payload;

        if (eventName !== 'djust:exec') return;

        if (!window.djust.js || !window.djust.js._executeOps) {
            // JS Commands module hasn't loaded yet (shouldn't happen since the
            // module ordering puts 26-js-commands.js before this file in the
            // build, but be defensive).
            if (globalThis.djustDebug) {
                djLog('[djust:exec] js commands module not loaded; skipping exec chain');
            }
            return;
        }

        if (!payload || !Array.isArray(payload.ops)) {
            if (globalThis.djustDebug) {
                djLog('[djust:exec] malformed payload (expected {ops: [...]}):', payload);
            }
            return;
        }

        try {
            // Execute the chain against the document body as the origin element.
            // Scoped targets (inner, closest) without an explicit to= selector
            // don't make sense for server-pushed chains, so we pass the body as
            // a stable default origin — chains that care about a specific
            // origin element should always use `to=` selectors.
            window.djust.js._executeOps(payload.ops, document.body);
        } catch (err) {
            // Don't let one bad op break the whole event pipeline. Log in debug
            // mode and swallow — downstream chains keep working.
            if (globalThis.djustDebug) {
                djLog('[djust:exec] op execution failed:', err);
            }
        }
    }

    // Register the listener once. The CustomEvent is fired on `window` by the
    // WebSocket consumer in 03-websocket.js — matches existing push_event
    // delivery semantics used by dj-hook's handleEvent() registrations.
    window.addEventListener('djust:push_event', handleDjustExec);

    // Expose the handler for tests and for debug-panel inspection.
    window.djust._execListener = { handleDjustExec: handleDjustExec };
})();
