/**
 * ADR-025 Feature B: user-registered custom JS commands.
 *
 * - window.djust.commands.register(): validation + collision rules
 * - executeOps dispatch of "ext.<name>" ops (targets resolution, async order)
 * - unknown-op reporting: console.error always; djust:error event only
 *   when window.DEBUG_MODE (did-you-mean suggestion included)
 * - chain surfaces: djust.js.ext(...) and JSChain.prototype.ext(...)
 * - real-DOM integration: dj-click carrying an ext op executes on click (#1196)
 */

import { describe, it, expect, vi } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createEnv(bodyHtml = '') {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><body>
            <div dj-root dj-view="test.View">
                ${bodyHtml}
            </div>
        </body></html>`,
        { url: 'http://localhost:8000/test/', runScripts: 'dangerously', pretendToBeVisual: true }
    );
    const { window } = dom;
    const errorSpy = vi.fn();
    window.console = { log: () => {}, error: errorSpy, warn: vi.fn(), debug: () => {}, info: () => {} };
    try {
        window.eval(clientCode);
    } catch (e) { /* client.js may throw on missing DOM APIs */ }
    return { window, document: window.document, errorSpy };
}

describe('djust.commands.register', () => {
    it('registers and is invoked by executeOps with (targets, args, originEl)', async () => {
        const { window, document } = createEnv('<div id="origin"></div>');
        const fn = vi.fn();
        window.djust.commands.register('blink', fn);
        const origin = document.getElementById('origin');
        await window.djust.js._executeOps([['ext.blink', { speed: 3 }]], origin);
        expect(fn).toHaveBeenCalledTimes(1);
        const [targets, args, originEl] = fn.mock.calls[0];
        expect(targets).toEqual([origin]);       // default target = origin
        expect(args).toEqual({ speed: 3 });
        expect(originEl).toBe(origin);
    });

    it('resolves to= targets before invoking', async () => {
        const { window, document } = createEnv('<div id="origin"></div><p class="x"></p><p class="x"></p>');
        const fn = vi.fn();
        window.djust.commands.register('mark', fn);
        await window.djust.js._executeOps([['ext.mark', { to: '.x' }]], document.getElementById('origin'));
        expect(fn.mock.calls[0][0]).toHaveLength(2);
        expect(fn.mock.calls[0][0][0].tagName).toBe('P');
    });

    it('throws on built-in name collision (snake_case and camelCase aliases)', () => {
        const { window } = createEnv();
        expect(() => window.djust.commands.register('show', () => {})).toThrow(/built-in/);
        expect(() => window.djust.commands.register('addClass', () => {})).toThrow(/built-in/);
    });

    it('throws on dotted names and non-function implementations', () => {
        const { window } = createEnv();
        expect(() => window.djust.commands.register('a.b', () => {})).toThrow(/must not contain/);
        expect(() => window.djust.commands.register('ok', 'not-a-fn')).toThrow(/function/);
        expect(() => window.djust.commands.register('', () => {})).toThrow(/non-empty/);
    });

    it('re-registering overwrites (last wins)', async () => {
        const { window, document } = createEnv('<div id="o"></div>');
        const first = vi.fn();
        const second = vi.fn();
        window.djust.commands.register('thing', first);
        window.djust.commands.register('thing', second);
        await window.djust.js._executeOps([['ext.thing', {}]], document.getElementById('o'));
        expect(first).not.toHaveBeenCalled();
        expect(second).toHaveBeenCalledTimes(1);
    });
});

describe('async chain ordering', () => {
    it('awaits an async ext command before running the next op', async () => {
        const { window, document } = createEnv('<div id="o"></div>');
        const order = [];
        window.djust.commands.register('slow', async () => {
            await new Promise((r) => setTimeout(r, 10));
            order.push('slow');
        });
        window.djust.commands.register('fast', () => order.push('fast'));
        await window.djust.js._executeOps(
            [['ext.slow', {}], ['ext.fast', {}]],
            document.getElementById('o')
        );
        expect(order).toEqual(['slow', 'fast']);
    });

    it('an ext command that throws does not abort the rest of the chain', async () => {
        const { window, document } = createEnv('<div id="o"></div>');
        const after = vi.fn();
        window.djust.commands.register('boom', () => { throw new Error('kaboom'); });
        window.djust.commands.register('after', after);
        await window.djust.js._executeOps(
            [['ext.boom', {}], ['ext.after', {}]],
            document.getElementById('o')
        );
        expect(after).toHaveBeenCalledTimes(1);
    });
});

describe('unknown ext op reporting', () => {
    it('console.errors with a did-you-mean suggestion; no fn is called (control)', async () => {
        const { window, document, errorSpy } = createEnv('<div id="o"></div>');
        const fn = vi.fn();
        window.djust.commands.register('scroll_to', fn);
        await window.djust.js._executeOps([['ext.scrol_to', {}]], document.getElementById('o'));
        expect(fn).not.toHaveBeenCalled();
        const joined = errorSpy.mock.calls.map((c) => c.join(' ')).join('\n');
        expect(joined).toContain('Unknown custom JS command');
        expect(joined).toContain('scrol_to');
        expect(joined).toContain('scroll_to'); // the suggestion
    });

    it('dispatches djust:error only when DEBUG_MODE', async () => {
        const { window, document } = createEnv('<div id="o"></div>');
        const seen = [];
        window.addEventListener('djust:error', (e) => seen.push(e.detail));
        await window.djust.js._executeOps([['ext.nope', {}]], document.getElementById('o'));
        expect(seen).toHaveLength(0); // DEBUG_MODE unset -> no overlay event
        window.DEBUG_MODE = true;
        await window.djust.js._executeOps([['ext.nope', {}]], document.getElementById('o'));
        expect(seen).toHaveLength(1);
        expect(seen[0].error).toContain('Unknown custom JS command');
    });
});

describe('chain surfaces', () => {
    it('djust.js.ext(name, args) serializes with the ext. prefix', () => {
        const { window } = createEnv();
        const chain = window.djust.js.ext('scroll_to', { to: '#top' }).addClass('flash');
        expect(JSON.parse(chain.toString())).toEqual([
            ['ext.scroll_to', { to: '#top' }],
            ['add_class', { names: 'flash' }],
        ]);
    });

    it('mid-chain .ext() works from a built-in start', () => {
        const { window } = createEnv();
        const chain = window.djust.js.hide('#m').ext('confetti', {});
        expect(JSON.parse(chain.toString())).toEqual([
            ['hide', { to: '#m' }],
            ['ext.confetti', {}],
        ]);
    });
});

describe('real-DOM integration (#1196)', () => {
    it('a click on dj-click carrying an ext op invokes the registered command', async () => {
        const { window, document } = createEnv(
            `<button id="btn" dj-click='[["ext.blink",{"speed":2}]]'>go</button>`
        );
        const fn = vi.fn();
        window.djust.commands.register('blink', fn);
        // Mirror the proven pattern from js-commands.test.js:295 ("attribute
        // dispatcher") — bind, native .click(), settle the async handler.
        window.djust.bindLiveViewEvents();
        document.getElementById('btn').click();
        await new Promise((r) => setTimeout(r, 20));
        expect(fn).toHaveBeenCalledTimes(1);
        expect(fn.mock.calls[0][1]).toEqual({ speed: 2 });
    });
});
