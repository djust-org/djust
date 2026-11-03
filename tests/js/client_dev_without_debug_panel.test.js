/**
 * client-dev.js with the debug panel turned off (DJUST_CONFIG["debug_panel"]
 * = False). The server then serves client-dev.js without debug-panel.js and
 * without window.DJUST_DEBUG_INFO; client-dev.js must still run its hot-reload
 * features and skip initDebugPanel() without throwing.
 */

import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const src = readFileSync(
    resolve(dirname(fileURLToPath(import.meta.url)), '../../python/djust/static/djust/client-dev.js'),
    'utf-8'
);

describe('client-dev.js without debug-panel.js', () => {
    beforeEach(() => {
        vi.useFakeTimers();
        delete window.DjustDebugPanel;
        delete window.DJUST_DEBUG_INFO;
        delete window.djustDebugPanel;
    });

    afterEach(() => {
        document.getElementById('djust-toast-container')?.remove();
        vi.clearAllTimers();
        vi.unstubAllGlobals();
        vi.useRealTimers();
    });

    it('initialises without creating a panel', () => {
        vi.stubGlobal('requestAnimationFrame', (fn) => fn());
        expect(() => new Function(src).call(window)).not.toThrow();
        expect(window.djustDebugPanel).toBeUndefined();
    });

    it('still shows hot-reload toasts', () => {
        vi.stubGlobal('requestAnimationFrame', (fn) => fn());
        new Function(src).call(window);
        window.dispatchEvent(new window.CustomEvent('djust:error', { detail: { error: 'boom', event: 'e' } }));
        const container = document.getElementById('djust-toast-container');
        expect(container).toBeTruthy();
        expect(container.textContent).toContain('boom');
    });

    it('skips the panel when DJUST_DEBUG_INFO is set but DjustDebugPanel is not', () => {
        vi.stubGlobal('requestAnimationFrame', (fn) => fn());
        window.DJUST_DEBUG_INFO = { view_class: 'V', handlers: {}, variables: {} };
        expect(() => new Function(src).call(window)).not.toThrow();
        expect(window.djustDebugPanel).toBeUndefined();
    });
});
