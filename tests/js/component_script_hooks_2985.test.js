/**
 * #2985 — the four component scripts that initialise themselves also answer
 * their dj-hook, so the hook runtime no longer logs a false
 * `No hook registered for "…"` for a component that works.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');
const DIR = './python/djust/components/static/djust_components/';

const CASES = [
    {
        hook: 'Countdown',
        file: 'countdown.js',
        markup: '<div dj-hook="Countdown" data-target="2999-01-01T00:00:00Z"><span data-unit="days"></span></div>',
        initFlag: '_djCountdownInit',
    },
    {
        hook: 'InfiniteScroll',
        file: 'infinite-scroll.js',
        markup: '<div dj-hook="InfiniteScroll" data-event="load_more"></div>',
        initFlag: '_djInfiniteScrollInit',
    },
    {
        hook: 'ScrollSpy',
        file: 'scroll-spy.js',
        markup: '<nav dj-hook="ScrollSpy" data-sections=\'["a"]\'></nav><section id="a"></section>',
        initFlag: '_djScrollSpyInit',
    },
    {
        hook: 'MarkdownTextarea',
        file: 'markdown-textarea.js',
        markup: '<div dj-hook="MarkdownTextarea"><div class="dj-md-textarea__preview" data-raw="x"></div></div>',
        initFlag: '_djMdTextareaInit',
    },
];

function createEnv(bodyHtml, { preRegister } = {}) {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><body><div dj-root>${bodyHtml}</div></body></html>`,
        { url: 'http://localhost:8000/test/', runScripts: 'dangerously', pretendToBeVisual: true },
    );
    const { window } = dom;
    const warnings = [];
    window.console = {
        log: () => {}, error: () => {}, debug: () => {}, info: () => {},
        warn: (...args) => warnings.push(args.join(' ')),
    };
    // IntersectionObserver is not in jsdom; the scripts only construct it.
    window.IntersectionObserver = class { observe() {} disconnect() {} };
    try {
        window.eval(clientCode);
    } catch (_e) {
        // client.js may throw on DOM APIs jsdom lacks; hooks still load.
    }
    if (preRegister) preRegister(window);
    return { window, warnings };
}

describe('component scripts answer their dj-hook (#2985)', () => {
    for (const c of CASES) {
        describe(c.hook, () => {
            // eslint-disable-next-line security/detect-non-literal-fs-filename -- fixed list above
            const source = fs.readFileSync(DIR + c.file, 'utf-8');

            it('registers a hook, so mounting logs no "No hook registered"', () => {
                const { window, warnings } = createEnv(c.markup);
                window.eval(source);
                expect(typeof window.djust.hooks[c.hook].mounted).toBe('function');
                window.djust.mountHooks();
                expect(warnings.filter((w) => w.includes('No hook registered'))).toEqual([]);
            });

            it('without the script the warning is logged (gate-off)', () => {
                const { window, warnings } = createEnv(c.markup);
                window.djust.mountHooks();
                expect(warnings.some((w) => w.includes(`No hook registered for "${c.hook}"`))).toBe(true);
            });

            it('mounted() runs the same guarded init as the self-initialisation', () => {
                const { window } = createEnv(c.markup);
                window.eval(source);
                const el = window.document.querySelector(`[dj-hook="${c.hook}"]`);
                el[c.initFlag] = undefined; // as if self-init had not reached it
                window.djust.mountHooks();
                expect(el[c.initFlag]).toBe(true);
            });

            it("keeps an app's own hook of the same name", () => {
                const mine = { mounted() {} };
                const { window } = createEnv(c.markup, {
                    preRegister: (w) => { w.djust.hooks = { [c.hook]: mine }; },
                });
                window.eval(source);
                expect(window.djust.hooks[c.hook]).toBe(mine);
            });
        });
    }
});
