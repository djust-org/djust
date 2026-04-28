import { defineConfig } from 'vitest/config';

/**
 * Test-runtime unhandled-error policy (v0.9.3 — issues #1186 / #1152)
 * ------------------------------------------------------------------
 * Two unhandled-error patterns surface intermittently during the full
 * `make test` run even though every test asserts pass and the suite
 * is otherwise clean. Both are test-runtime cross-pollination noise,
 * not application bugs:
 *
 *   1. happy-dom + undici WebSocket dispatchEvent (#1186, P1):
 *      undici's WebSocket calls `fireEvent` constructing a Node-side
 *      `Event` object; happy-dom's `EventTarget.dispatchEvent` runtime
 *      check rejects it because the two runtimes don't share the
 *      Web-platform Event prototype. The error fires from
 *      `WebSocket.dispatchEvent` deep in node_modules and never
 *      propagates into a test assertion.
 *
 *   2. view-transitions teardown (#1152, P2):
 *      Non-deterministic `EnvironmentTeardownError: Closing rpc while
 *      "onUserConsoleLog" was pending` during teardown of
 *      `tests/js/view-transitions.test.js`. The test stubs already
 *      yield a microtask before invoking the inner callback (per CLAUDE.md
 *      retro #1113), so this is RPC-timing teardown noise, not a stub
 *      regression.
 *
 * Both are filtered here rather than worked around in test files so the
 * suppression is centralized and reviewable. Each pattern matches on
 * narrow `message` + `stack` substrings — anything else still re-throws.
 *
 * TODO(#1186 v0.9.4+): replace WebSocket filter with a proper happy-dom
 * WebSocket stub or upstream-fixed happy-dom version.
 */
export default defineConfig({
  test: {
    environment: 'happy-dom',
    globals: true,
    onUnhandledError(error) {
      const msg = String(error && error.message ? error.message : '');
      const stack = String(error && error.stack ? error.stack : '');

      // Pattern 1: happy-dom + undici WebSocket dispatchEvent (#1186)
      if (
        msg.includes("Failed to execute 'dispatchEvent' on 'EventTarget'") &&
        (stack.includes('undici/lib/web/websocket') ||
          stack.includes('happy-dom/lib/event/EventTarget'))
      ) {
        return false;
      }

      // Pattern 2: view-transitions teardown (#1152)
      // Match only the diagnosed cause: vitest's `Closing rpc` teardown
      // signature paired with the specific console-log RPC handler that
      // had a pending call when the environment was torn down. The
      // earlier broader `stack.includes('view-transitions')` disjunct
      // (PR #1187) widened the net beyond the diagnosed shape — closes
      // #1188 🟡 #1.
      if (
        msg.includes('Closing rpc') &&
        (msg.includes('onUserConsoleLog') || msg.includes('onConsoleLog'))
      ) {
        return false;
      }

      // Anything else — let vitest surface it.
      return true;
    },
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json', 'html'],
      include: ['python/djust/static/djust/decorators.js'],
      exclude: ['node_modules', 'tests', '**/*.test.js'],
      // Coverage thresholds
      thresholds: {
        lines: 85,
        functions: 85,
        branches: 85,
        statements: 85,
      },
      // Report uncovered lines
      all: true,
      // Skip full coverage check (we want to see what's missing)
      skipFull: false,
    },
  },
});
