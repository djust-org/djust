import { defineConfig, configDefaults } from 'vitest/config';

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
// The tests of the chunked modules (#4007), also run against the split build.
const SPLIT_BUILD_TESTS = [
  'tests/js/uploads.test.js',
  'tests/js/upload_resume.test.js',
  'tests/js/upload_progress_ui.test.js',
  'tests/js/dj-paste.test.js',
  'tests/js/component_batch5_image_upload_2985.test.js',
  'tests/js/virtual_list.test.js',
  'tests/js/virtual-keyed-ops-2017.test.js',
  'tests/js/dj-virtual-teardown-2033.test.js',
  'tests/js/dj-virtual-path-fallback-2113.test.js',
  'tests/js/dj-virtual-patch-miss-diagnostic-2017.test.js',
  'tests/js/stream-ops-dj-virtual-2017.test.js',
  'tests/js/service_worker.test.js',
  'tests/js/sw_advanced.test.js',
  'tests/js/cursor-overlay.test.js',
  'tests/js/tutorial-bubble.test.js',
];

export default defineConfig({
  test: {
    environment: 'happy-dom',
    globals: true,
    // Don't discover tests inside agent git worktrees (`.claude/worktrees/*`)
    // or other `.claude/` internals — those are other branches' checkouts and
    // running their (possibly stale) test copies against this tree's built
    // client.js produces spurious failures. Preserve vitest's defaults.
    //
    // `.django-src/` is the same situation (#2637): the gitignored Django
    // checkout `make django-template-suite` creates carries Django's own
    // QUnit admin tests, which cannot run under vitest and are not ours.
    exclude: [...configDefaults.exclude, '**/.claude/**', '**/.django-src/**'],
    // #4007: the tests of the modules built as client chunks run twice: once
    // against the full bundle (client.js) and once, in `split-build`, against
    // client-core.js followed by the chunks (tests/js/_split_build_setup.js).
    projects: [
      { extends: true, test: { name: 'default' } },
      {
        extends: true,
        test: {
          name: 'split-build',
          setupFiles: ['tests/js/_split_build_setup.js'],
          include: SPLIT_BUILD_TESTS,
        },
      },
    ],
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

      // Pattern 3: undici socket `setTypeOfService` EINVAL (macOS).
      // Node's built-in fetch sets the IP type-of-service on each new
      // socket; on recent macOS the kernel rejects it with EINVAL for some
      // sockets. It fires from undici's HTTP/1 writer after a test's
      // relative fetch() resolves against happy-dom's localhost origin,
      // and vitest attributes it to whichever file is running then. It
      // is a host-socket error, never an assertion; nothing else is
      // filtered by it.
      if (
        error &&
        error.code === 'EINVAL' &&
        error.syscall === 'setTypeOfService' &&
        stack.includes('undici')
      ) {
        return false;
      }

      // Anything else — let vitest surface it.
      return true;
    },
  },
});
