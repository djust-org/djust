/**
 * djLog must survive a page-installed console.log hook that throws.
 *
 * Strict event params are null-prototype objects; a hook doing
 * `args.join(' ')` (tests/cache page in the demo project) throws on them.
 * Before the fix that rejection escaped handleEvent and no event was sent.
 */
import { describe, it, expect, vi } from 'vitest';
import { createDom } from './_helpers.js';

describe('djLog with a throwing console.log', () => {
    it('does not throw when the page hook cannot stringify a null-prototype arg', () => {
        const dom = createDom('<div dj-root dj-view="x.V"></div>');
        const w = dom.window;
        w.djustDebug = true;
        const seen = [];
        w.console.log = (...args) => {
            seen.push(1);
            return args.join(' ');
        };
        const sent = Object.create(null);
        sent.value = 'laptop';
        expect(() => w.djLog('[LiveView] Handling event: search', sent)).not.toThrow();
        expect(seen.length).toBe(1);
    });

    it('still logs normally when the hook is fine and stays silent without debug', () => {
        const dom = createDom('<div dj-root dj-view="x.V"></div>');
        const w = dom.window;
        const spy = vi.fn();
        w.console.log = spy;
        w.djustDebug = false;
        w.djLog('quiet');
        expect(spy).not.toHaveBeenCalled();
        w.djustDebug = true;
        w.djLog('loud', 1);
        expect(spy).toHaveBeenCalledWith('loud', 1);
    });
});
