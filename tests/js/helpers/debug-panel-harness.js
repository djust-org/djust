/**
 * Test harness for the debug panel IIFE (Issue #184)
 *
 * Evaluates the actual debug-panel.js source in a DOM environment
 * so tests exercise the real code paths instead of replicated logic.
 */

import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __harnessDir = dirname(fileURLToPath(import.meta.url));
export const PANEL_SOURCE_PATH = resolve(
    __harnessDir,
    '../../../python/djust/static/djust/debug-panel.js'
);

// Cached across all tests in a run (read-only — never mutated after first read)
let sourceCache = null;

function getSource() {
    if (!sourceCache) {
        sourceCache = readFileSync(PANEL_SOURCE_PATH, 'utf-8');
    }
    return sourceCache;
}

/**
 * Load the real DjustDebugPanel class into the current window.
 *
 * Sets up minimal globals the IIFE expects (DEBUG_MODE, navigator, etc.)
 * then evaluates the source. Returns the class from window.DjustDebugPanel.
 *
 * @param {object} [opts] - Options
 * @param {object} [opts.globals] - Extra window globals to set before eval
 * @returns {typeof DjustDebugPanel} The real panel class
 */
export function loadPanel(opts = {}) {
    // Ensure DEBUG_MODE is set so the IIFE doesn't bail out
    window.DEBUG_MODE = true;

    // Provide a minimal navigator if not present
    if (!window.navigator) {
        window.navigator = { platform: 'MacIntel', clipboard: { writeText: () => Promise.resolve() } };
    }
    if (!window.navigator.clipboard) {
        window.navigator.clipboard = { writeText: () => Promise.resolve() };
    }

    // Provide minimal localStorage if not present
    if (!window.localStorage) {
        const store = {};
        window.localStorage = {
            getItem: (k) => store[k] ?? null,
            setItem: (k, v) => { store[k] = String(v); },
            removeItem: (k) => { delete store[k]; },
            clear: () => { Object.keys(store).forEach(k => delete store[k]); },
        };
    }

    // Apply any extra globals
    if (opts.globals) {
        Object.assign(window, opts.globals);
    }

    // Clean up any previous panel instance
    delete window.DjustDebugPanel;
    delete window.djustDebugPanel;

    // Evaluate the IIFE source
    const source = getSource();
    // eslint-disable-next-line no-eval
    const fn = new Function(source);
    fn.call(window);

    if (!window.DjustDebugPanel) {
        throw new Error(
            'DjustDebugPanel was not exported to window. ' +
            'Ensure DEBUG_MODE is true and the IIFE source is valid.'
        );
    }

    return window.DjustDebugPanel;
}

/**
 * Create a DjustDebugPanel instance with minimal DOM stubs.
 *
 * The constructor calls init() which creates DOM elements. This helper
 * ensures the DOM environment is adequate for that.
 *
 * @param {object} [config] - Panel config overrides
 * @param {object} [opts] - loadPanel options
 * @returns {DjustDebugPanel} A live panel instance
 */
export function createPanel(config = {}, opts = {}) {
    const PanelClass = loadPanel(opts);
    return new PanelClass(config);
}
