/**
 * #2659 — a test must not import a `static/djust/` file that never ships.
 *
 * `scripts/build-client.sh` concatenates `src/[0-9]*.js` only. A JS file
 * that lives elsewhere under `static/djust/` and is imported by a test is
 * "tested but not shipped": edits to it turn tests green and change nothing
 * in the browser. `decorators.js` (~34 KB, seven test suites) was exactly
 * that until it was deleted; this guard keeps the class closed.
 *
 * Every `static/djust/…/*.js` path referenced from `tests/js/` must be one
 * of:
 *   - a bundle INPUT under `static/djust/src/`,
 *   - a bundle OUTPUT (`client.js`, `client.min.js`, `debug-panel*.js`), or
 *   - a documented standalone asset with its own loader (listed below with
 *     the loader that pulls it, so the entry is auditable).
 */
import { describe, it, expect } from 'vitest';
import fs from 'fs';
import path from 'path';

const TESTS_DIR = path.resolve('./tests/js');
const STATIC_DIR = path.resolve('./python/djust/static/djust');

const BUNDLE_OUTPUTS = new Set([
    'client.js',
    'client.min.js',
    'debug-panel.js',
    'debug-panel.min.js',
]);

// Standalone assets: NOT in the bundle, but each has a real loader.
const STANDALONE_WITH_LOADER = {
    // Registered as a service worker by the PWA views (python/djust/pwa/).
    'service-worker.js': 'python/djust/pwa',
    // Extension asset resolved by python/djust/extensions.py ("chart").
    'ext/dj-chart.js': 'python/djust/extensions.py',
    // Loaded by the bug-capture replay template.
    'bug_capture_replay.js': 'python/djust/templates/djust/bug_capture/replay.html',
    // DEBUG-only helper injected by mixins/post_processing.py.
    'client-dev.js': 'python/djust/mixins/post_processing.py',
    // Client-side sanitizer helpers (classic script; see eslint.config.js).
    'security.js': 'eslint.config.js (classic-script group)',
    // React integration shim (classic script; see eslint.config.js).
    'react-client.js': 'eslint.config.js (classic-script group)',
};

const PATH_RE = /static\/djust\/([A-Za-z0-9_./-]+\.js)/g;

function referencedStaticPaths() {
    const refs = new Map(); // relPath -> [test files]
    for (const name of fs.readdirSync(TESTS_DIR)) {
        if (!name.endsWith('.js') || name === path.basename(new URL(import.meta.url).pathname)) continue;
        const text = fs.readFileSync(path.join(TESTS_DIR, name), 'utf-8');
        for (const m of text.matchAll(PATH_RE)) {
            const rel = m[1];
            if (!refs.has(rel)) refs.set(rel, []);
            refs.get(rel).push(name);
        }
    }
    return refs;
}

describe('no test imports a static/djust JS file that is not shipped (#2659)', () => {
    const refs = referencedStaticPaths();

    it('scans something (the harness is not vacuous)', () => {
        expect(refs.size).toBeGreaterThan(0);
        expect(refs.has('client.js')).toBe(true);
    });

    for (const [rel, files] of refs) {
        it(`${rel} is a src/ module, a bundle output, or a documented standalone asset`, () => {
            const ok =
                rel.startsWith('src/') ||
                BUNDLE_OUTPUTS.has(rel) ||
                Object.prototype.hasOwnProperty.call(STANDALONE_WITH_LOADER, rel);
            expect(ok, `${rel} is imported by ${files.join(', ')} but is not in the bundle and has no documented loader — bundle it under src/ or delete it (#2659)`).toBe(true);
        });
    }

    it('the two files #2659 removed are gone', () => {
        expect(fs.existsSync(path.join(STATIC_DIR, 'decorators.js'))).toBe(false);
        expect(fs.existsSync(path.join(STATIC_DIR, 'js', 'pwa.js'))).toBe(false);
    });

    it('every documented standalone asset still exists (no stale allowlist rows)', () => {
        for (const rel of Object.keys(STANDALONE_WITH_LOADER)) {
            expect(fs.existsSync(path.join(STATIC_DIR, rel)), `${rel} listed but missing`).toBe(true);
        }
    });
});
