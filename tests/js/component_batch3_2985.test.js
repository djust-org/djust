/**
 * #2985 batch 3 — ActivityFeed, Terminal and Tour answer their dj-hook, and an
 * app's own hook of the same name still wins.
 *
 * The markup is what the components render (python/djust/tests/
 * test_component_batch3_2985.py pins it on every render path).
 */

import { describe, it, expect, vi } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');
const DIR = './python/djust/components/static/djust_components/';
// eslint-disable-next-line security/detect-non-literal-fs-filename -- fixed names
const read = (f) => fs.readFileSync(DIR + f, 'utf-8');

function createEnv(bodyHtml, { preRegister, matchMedia } = {}) {
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
    window.IntersectionObserver = class { observe() {} disconnect() {} };
    if (matchMedia) window.matchMedia = matchMedia;
    try {
        window.eval(clientCode);
    } catch (_e) {
        // client.js may throw on DOM APIs jsdom lacks; hooks still load.
    }
    if (preRegister) preRegister(window);
    return { window, warnings };
}

function boot(markup, scriptFile, opts) {
    const env = createEnv(markup, opts);
    env.window.eval(read(scriptFile));
    env.window.djust.mountHooks();
    env.$ = (sel) => env.window.document.querySelector(sel);
    env.$$ = (sel) => Array.from(env.window.document.querySelectorAll(sel));
    env.live = () => (env.window.document.getElementById('dj-component-live') || {}).textContent;
    env.push = (name, payload) => env.window.djust.dispatchPushEventToHooks(name, payload);
    return env;
}

const key = (window, el, k, extra = {}) => {
    const e = new window.KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true, ...extra });
    el.dispatchEvent(e);
    return e;
};
const frame = (env) => new Promise((resolve) => env.window.requestAnimationFrame(() => resolve()));

// ---------------------------------------------------------------------------
// Fixtures (as rendered)
// ---------------------------------------------------------------------------

const FEED_ITEM = (user, action, target, time) =>
    '<div class="dj-activity-feed__item" role="article"><span class="dj-activity-feed__avatar">' +
    `<span class="dj-activity-feed__avatar-initials">${user[0]}</span></span><div class="dj-activity-feed__body">` +
    `<span class="dj-activity-feed__text"><strong class="dj-activity-feed__user">${user}</strong> ${action}` +
    (target ? ` <span class="dj-activity-feed__target">${target}</span>` : '') + '</span>' +
    (time ? `<span class="dj-activity-feed__time">${time}</span>` : '') + '</div></div>';
const FEED = ({ max = 5, items = [['Alice', 'commented on', 'Issue #42', '2m ago'], ['Bob', 'merged', 'PR #17', '5m ago']] } = {}) =>
    `<div class="dj-activity-feed" role="feed" aria-label="Activity feed" data-stream-event="activity_update" dj-hook="ActivityFeed" data-max-items="${max}">` +
    items.map((i) => FEED_ITEM(...i)).join('') + '</div>';

const TERM = ({ attrs = ' data-stream-event="term_out"', lines = ['$ ls', 'total 2'], title = 'Build' } = {}) =>
    `<div class="dj-terminal" dj-hook="Terminal"${attrs}>` +
    (title ? `<div class="dj-terminal__titlebar"><span class="dj-terminal__title">${title}</span><span class="dj-terminal__dots"></span></div>` : '') +
    '<div class="dj-terminal__body">' +
    lines.map((l, i) => `<div class="dj-terminal__line">${attrs.includes('line-numbers') ? `<span class="dj-terminal__line-num">${i + 1}</span>` : ''}<span class="dj-terminal__text">${l}</span></div>`).join('') +
    '</div></div>';

const TOUR = ({ step = 0, total = 3, target = '#create', extra = '' } = {}) =>
    `<div class="dj-tour" dj-hook="Tour" data-target="${target}" data-step="${step}" data-total="${total}" data-event="tour" role="dialog" aria-modal="true"${extra}>` +
    '<div class="dj-tour__overlay"></div><div class="dj-tour__popover"><div class="dj-tour__header">' +
    `<h4 class="dj-tour__title">Step title ${step + 1}</h4><span class="dj-tour__step-label">Step ${step + 1} of ${total}</span></div>` +
    '<div class="dj-tour__body"><p class="dj-tour__content">Some text.</p></div>' +
    '<div class="dj-tour__footer">' +
    (step < total - 1 ? '<button class="dj-tour__skip" type="button" dj-click="tour" data-value="skip">Skip tour</button>' : '') +
    (step > 0 ? '<button class="dj-tour__prev" type="button" dj-click="tour" data-value="prev">Back</button>' : '') +
    `<button class="dj-tour__next" type="button" dj-click="tour" data-value="${step === total - 1 ? 'finish' : 'next'}">${step === total - 1 ? 'Finish' : 'Next'}</button>` +
    '</div></div></div>';

// ---------------------------------------------------------------------------
// An app's own hook always wins
// ---------------------------------------------------------------------------

const COMPONENTS = [
    { hook: 'ActivityFeed', file: 'activity-feed.js', markup: FEED(), probe: (el) => el.querySelector('.dj-activity-feed__item').hasAttribute('tabindex') },
    { hook: 'Terminal', file: 'terminal.js', markup: TERM(), probe: (el) => el.hasAttribute('role') },
    { hook: 'Tour', file: 'tour.js', markup: TOUR(), probe: (el) => el.hasAttribute('aria-labelledby') },
];

describe('the shipped hooks never replace an app hook (#2985 batch 3)', () => {
    for (const c of COMPONENTS) {
        describe(c.hook, () => {
            it('registers a hook, so mounting logs no "No hook registered"', () => {
                const env = boot(c.markup, c.file);
                expect(env.warnings.filter((w) => w.includes('No hook registered'))).toEqual([]);
                expect(c.probe(env.$(`[dj-hook="${c.hook}"]`))).toBe(true);
            });

            it('without the script the warning is logged (gate-off)', () => {
                const env = createEnv(c.markup);
                env.window.djust.mountHooks();
                expect(env.warnings.some((w) => w.includes(`No hook registered for "${c.hook}"`))).toBe(true);
            });

            it('an app hook in window.djust.hooks registered first is the one that runs', () => {
                const mounted = vi.fn();
                const env = boot(c.markup, c.file, { preRegister: (w) => { w.djust.hooks = { [c.hook]: { mounted } }; } });
                expect(mounted).toHaveBeenCalledTimes(1);
                expect(c.probe(env.$(`[dj-hook="${c.hook}"]`))).toBe(false);
            });

            it('an app hook in window.DjustHooks registered first is the one that runs', () => {
                const mounted = vi.fn();
                const mine = { mounted };
                const env = boot(c.markup, c.file, { preRegister: (w) => { w.DjustHooks = { [c.hook]: mine }; } });
                expect(env.window.DjustHooks[c.hook]).toBe(mine);
                expect(mounted).toHaveBeenCalledTimes(1);
                expect(c.probe(env.$(`[dj-hook="${c.hook}"]`))).toBe(false);
            });

            it('an app hook in window.djust.hooks registered AFTER the script still wins', () => {
                const mounted = vi.fn();
                const env = createEnv(c.markup);
                env.window.eval(read(c.file));
                env.window.djust.hooks = { [c.hook]: { mounted } };
                env.window.djust.mountHooks();
                expect(mounted).toHaveBeenCalledTimes(1);
                expect(c.probe(env.window.document.querySelector(`[dj-hook="${c.hook}"]`))).toBe(false);
            });

            it('an app hook assigned into window.DjustHooks AFTER the script still wins', () => {
                const mounted = vi.fn();
                const env = createEnv(c.markup);
                env.window.eval(read(c.file));
                env.window.DjustHooks[c.hook] = { mounted };
                env.window.djust.mountHooks();
                expect(mounted).toHaveBeenCalledTimes(1);
                expect(c.probe(env.window.document.querySelector(`[dj-hook="${c.hook}"]`))).toBe(false);
            });

            it("an app hook's pushEvent/handleEvent API and the element's attributes are untouched", () => {
                const seen = [];
                boot(c.markup, c.file, {
                    preRegister: (w) => {
                        w.djust.hooks = { [c.hook]: { mounted() { seen.push(typeof this.pushEvent, typeof this.handleEvent, this.el.getAttribute('dj-hook')); } } };
                    },
                });
                expect(seen).toEqual(['function', 'function', c.hook]);
            });
        });
    }
});

// ---------------------------------------------------------------------------
// ActivityFeed
// ---------------------------------------------------------------------------

describe('ActivityFeed', () => {
    const rows = (env) => env.$$('.dj-activity-feed__item');
    const who = (env) => rows(env).map((r) => r.querySelector('.dj-activity-feed__user').textContent);

    it('makes each article a numbered, focusable feed item', () => {
        const env = boot(FEED(), 'activity-feed.js');
        expect(rows(env).map((r) => r.getAttribute('tabindex'))).toEqual(['0', '0']);
        expect(rows(env).map((r) => r.getAttribute('aria-posinset'))).toEqual(['1', '2']);
        expect(rows(env).map((r) => r.getAttribute('aria-setsize'))).toEqual(['-1', '-1']);
    });

    it('puts streamed events at the top, newest first, in the structure the server renders', () => {
        const env = boot(FEED(), 'activity-feed.js');
        env.push('activity_update', { events: [
            { user: 'Carol Dee', action: 'opened', target: 'PR #18', time: 'now', icon: '*' },
            { user: 'Dan', action: 'pushed', time: '1m ago', avatar: 'https://example.com/d.png' },
        ] });
        expect(who(env)).toEqual(['Carol Dee', 'Dan', 'Alice', 'Bob']);
        const first = rows(env)[0];
        expect(first.getAttribute('role')).toBe('article');
        expect(first.querySelector('.dj-activity-feed__avatar-initials').textContent).toBe('CD');
        expect(first.querySelector('.dj-activity-feed__icon').textContent).toBe('*');
        expect(first.querySelector('.dj-activity-feed__target').textContent).toBe('PR #18');
        expect(first.querySelector('.dj-activity-feed__time').textContent).toBe('now');
        expect(first.querySelector('.dj-activity-feed__text').textContent).toBe('Carol Dee opened PR #18');
        const dan = rows(env)[1];
        expect(dan.querySelector('.dj-activity-feed__avatar-img').getAttribute('src')).toBe('https://example.com/d.png');
        expect(dan.querySelector('.dj-activity-feed__target')).toBeNull();
    });

    it('accepts {event}, a list, or one dict, and ignores anything else', () => {
        const env = boot(FEED({ max: 50 }), 'activity-feed.js');
        env.push('activity_update', { event: { user: 'A1', action: 'x' } });
        env.push('activity_update', [{ user: 'A2', action: 'x' }]);
        env.push('activity_update', { user: 'A3', action: 'x' });
        expect(who(env).slice(0, 3)).toEqual(['A3', 'A2', 'A1']);
        const n = rows(env).length;
        for (const junk of [null, 'text', 7, {}, { events: 'no' }, { events: [null, 3, 'x', []] }]) env.push('activity_update', junk);
        env.push('other_event', { user: 'Z', action: 'x' });
        expect(rows(env)).toHaveLength(n);
    });

    it('keeps at most data-max-items rows, dropping the oldest', () => {
        const env = boot(FEED({ max: 4 }), 'activity-feed.js');
        env.push('activity_update', { events: [1, 2, 3].map((i) => ({ user: `N${i}`, action: 'did' })) });
        expect(who(env)).toEqual(['N1', 'N2', 'N3', 'Alice']);
        env.push('activity_update', { event: { user: 'N4', action: 'did' } });
        expect(who(env)).toEqual(['N4', 'N1', 'N2', 'N3']);
    });

    it('a focused row that is trimmed away hands focus to the nearest row left (not the page)', async () => {
        const env = boot(FEED({ max: 3 }), 'activity-feed.js');
        const doc = env.window.document;
        rows(env)[1].focus(); // Bob, the oldest
        expect(doc.activeElement).toBe(rows(env)[1]);
        env.push('activity_update', { events: [1, 2].map((i) => ({ user: `N${i}`, action: 'did' })) }); // Bob falls off
        expect(who(env)).toEqual(['N1', 'N2', 'Alice']);
        expect(doc.activeElement).toBe(rows(env)[2]);
        // focus on a row that stays is left alone, and so is focus outside the feed
        await frame(env); // new rows become focusable on the next frame
        rows(env)[0].focus();
        expect(doc.activeElement).toBe(rows(env)[0]);
        env.push('activity_update', { event: { user: 'N3', action: 'did' } });
        expect(doc.activeElement.querySelector('strong').textContent).toBe('N1');
        doc.activeElement.blur();
        env.push('activity_update', { event: { user: 'N4', action: 'did' } });
        expect(doc.activeElement).toBe(doc.body);
    });

    it('focus moves to a row left even when every row left is new in that burst (not yet focusable)', () => {
        const env = boot(FEED({ max: 2 }), 'activity-feed.js');
        const doc = env.window.document;
        rows(env)[1].focus();
        env.push('activity_update', { events: [1, 2, 3].map((i) => ({ user: `N${i}`, action: 'did' })) });
        expect(who(env)).toEqual(['N1', 'N2']);
        expect(doc.activeElement).toBe(rows(env)[1]);
    });

    it('falls back to 50 without data-max-items', () => {
        const env = boot(FEED().replace(' data-max-items="5"', ''), 'activity-feed.js');
        env.push('activity_update', { events: Array.from({ length: 60 }, (_, i) => ({ user: `U${i}`, action: 'x' })) });
        expect(rows(env)).toHaveLength(50);
    });

    it('renumbers the articles on the next frame', async () => {
        const env = boot(FEED(), 'activity-feed.js');
        env.push('activity_update', { event: { user: 'New', action: 'x' } });
        await frame(env);
        expect(rows(env).map((r) => r.getAttribute('aria-posinset'))).toEqual(['1', '2', '3']);
    });

    it('announces a new activity, summarises a burst, and says nothing for junk', () => {
        const env = boot(FEED(), 'activity-feed.js');
        env.push('activity_update', { event: { user: 'Eve', action: 'starred', target: 'repo' } });
        expect(env.live()).toBe('Eve starred repo');
        env.push('activity_update', { events: [1, 2, 3, 4, 5].map((i) => ({ user: `U${i}`, action: 'x' })) });
        expect(env.live()).toBe('5 new activities');
        env.push('activity_update', null);
        expect(env.live()).toBe('5 new activities');
    });

    it('everything streamed is text, never markup', () => {
        const env = boot(FEED(), 'activity-feed.js');
        const evil = '<img src=x onerror=window.__pwn=1>';
        env.push('activity_update', { event: { user: evil, action: evil, target: evil, time: evil, icon: evil, avatar: 'javascript:window.__pwn=1' } });
        expect(env.$('.dj-activity-feed img')).toBeNull();
        expect(env.window.__pwn).toBeUndefined();
        expect(rows(env)[0].querySelector('.dj-activity-feed__user').textContent).toBe(evil);
        expect(rows(env)[0].querySelector('.dj-activity-feed__avatar-initials').textContent).toBe('<S'); // the first letters of "<img" and "src=x", as on the server
    });

    it('applies the server\'s image policy to avatars', () => {
        const env = boot(FEED({ max: 50 }), 'activity-feed.js');
        const avatars = [
            ['https://a/b.png', true], ['http://a/b.png', true], ['/img/a.png', true], ['rel/a.png', true],
            ['data:image/png;base64,AAAA', true], ['javascript:alert(1)', false], [' JaVa\tScRiPt:alert(1)', false],
            ['data:text/html;base64,AAAA', false], ['vbscript:x', false], ['ftp://x/y.png', false], ['', false], [null, false],
        ];
        avatars.forEach(([src], i) => env.push('activity_update', { event: { user: `U${i}`, action: 'x', avatar: src } }));
        const got = rows(env).slice(0, avatars.length).reverse().map((r) => !!r.querySelector('img'));
        expect(got).toEqual(avatars.map(([, ok]) => ok));
    });

    it('Page Down / Page Up move between articles, nothing else is intercepted', () => {
        const env = boot(FEED(), 'activity-feed.js');
        const [a, b] = rows(env);
        a.focus();
        expect(key(env.window, a, 'PageDown').defaultPrevented).toBe(true);
        expect(env.window.document.activeElement).toBe(b);
        expect(key(env.window, b, 'PageDown').defaultPrevented).toBe(false); // last
        key(env.window, b, 'PageUp');
        expect(env.window.document.activeElement).toBe(a);
        expect(key(env.window, a, 'PageUp').defaultPrevented).toBe(false); // first
        expect(key(env.window, a, 'ArrowDown').defaultPrevented).toBe(false);
        expect(key(env.window, a, 'PageDown', { ctrlKey: true }).defaultPrevented).toBe(false);
    });

    it('re-applies what a patch dropped and does not double-bind', () => {
        const env = boot(FEED(), 'activity-feed.js');
        rows(env).forEach((r) => { r.removeAttribute('tabindex'); r.removeAttribute('aria-posinset'); });
        for (let i = 0; i < 4; i++) env.window.djust.updateHooks();
        expect(rows(env)[1].getAttribute('aria-posinset')).toBe('2');
        key(env.window, rows(env)[0], 'PageDown');
        expect(env.window.document.activeElement).toBe(rows(env)[1]);
    });

    it('recounts from the re-rendered rows after a server update', () => {
        const env = boot(FEED({ max: 3 }), 'activity-feed.js');
        env.push('activity_update', { event: { user: 'S1', action: 'x' } }); // 3 rows
        env.$('.dj-activity-feed').innerHTML = FEED_ITEM('Only', 'one', '', '');
        env.window.djust.updateHooks();
        env.push('activity_update', { events: [{ user: 'S2', action: 'x' }, { user: 'S3', action: 'x' }] });
        expect(who(env)).toEqual(['S2', 'S3', 'Only']);
    });

    it('streaming does not rescan the rows it holds', () => {
        const env = boot(FEED({ max: 10000 }), 'activity-feed.js');
        const feed = env.$('.dj-activity-feed');
        let queries = 0;
        for (const m of ['querySelectorAll', 'querySelector']) {
            const real = feed[m].bind(feed);
            feed[m] = (...a) => { queries += 1; return real(...a); };
        }
        for (let i = 0; i < 500; i++) env.push('activity_update', { event: { user: `U${i}`, action: 'x' } });
        expect(queries).toBe(0);
        expect(rows(env)).toHaveLength(502);
    });
});

// ---------------------------------------------------------------------------
// Terminal
// ---------------------------------------------------------------------------

const ESC = '\u001b';
const textRows = (env) => env.$$('.dj-terminal__text');
const styled = (el, env) => {
    const norm = (c) => { const i = env.window.document.createElement('i'); i.style.color = c; return i.style.color; };
    return Array.from(el.childNodes).map((n) =>
        n.nodeType === 3 ? n.textContent : [n.textContent, n.style.fontWeight || '', n.style.color ? norm(n.style.color) : '']);
};
const rgb = (env, c) => { const i = env.window.document.createElement('i'); i.style.color = c; return i.style.color; };

describe('Terminal', () => {
    it('names the output, keeps it from flooding a screen reader, and makes the body scrollable by keyboard', () => {
        const env = boot(TERM(), 'terminal.js');
        const root = env.$('.dj-terminal');
        expect(root.getAttribute('role')).toBe('log');
        expect(root.getAttribute('aria-live')).toBe('off');
        expect(root.getAttribute('aria-label')).toBe('Build');
        expect(env.$('.dj-terminal__body').getAttribute('tabindex')).toBe('0');
    });

    it('falls back to a generic name without a title, and keeps one an app supplied', () => {
        expect(boot(TERM({ title: '' }), 'terminal.js').$('.dj-terminal').getAttribute('aria-label')).toBe('Terminal output');
        const env = boot(TERM().replace('dj-hook="Terminal"', 'dj-hook="Terminal" aria-label="Deploy" role="region"'), 'terminal.js');
        expect(env.$('.dj-terminal').getAttribute('aria-label')).toBe('Deploy');
        expect(env.$('.dj-terminal').getAttribute('role')).toBe('region');
    });

    it('appends streamed lines (lines, line, list, string) and ignores the rest', () => {
        const env = boot(TERM(), 'terminal.js');
        env.push('term_out', { lines: ['a', 'b'] });
        env.push('term_out', { line: 'c' });
        env.push('term_out', ['d']);
        env.push('term_out', 'e');
        expect(textRows(env).map((r) => r.textContent)).toEqual(['$ ls', 'total 2', 'a', 'b', 'c', 'd', 'e']);
        for (const junk of [null, 5, {}, { lines: 'no' }, { lines: [] }]) env.push('term_out', junk);
        env.push('other', { line: 'x' });
        expect(textRows(env)).toHaveLength(7);
    });

    it('turns ANSI colour and bold into spans (flat, with the server\'s palette)', () => {
        const env = boot(TERM(), 'terminal.js');
        env.push('term_out', { lines: [
            `plain ${ESC}[32mgreen${ESC}[0m ${ESC}[1mbold${ESC}[0m`,
            `${ESC}[1;31mboth${ESC}[0m end`,
            `${ESC}[1mb ${ESC}[34mblue-bold ${ESC}[92mbright`,
            `${ESC}[31mred ${ESC}[mreset`,
            `${ESC}[7mreverse${ESC}[38;5;12m 256 ${ESC}[39mdefault`,
        ] });
        const rows = textRows(env).slice(2);
        expect(styled(rows[0], env)).toEqual(['plain ', ['green', '', rgb(env, '#2ecc71')], ' ', ['bold', 'bold', '']]);
        expect(styled(rows[1], env)).toEqual([['both', 'bold', rgb(env, '#e74c3c')], ' end']);
        expect(styled(rows[2], env)).toEqual([['b ', 'bold', ''], ['blue-bold ', 'bold', rgb(env, '#3498db')], ['bright', 'bold', rgb(env, '#55efc4')]]);
        expect(styled(rows[3], env)).toEqual([['red ', '', rgb(env, '#e74c3c')], 'reset']);
        // codes the server ignores are consumed and ignored (here: reverse, 256-colour, default-fg)
        expect(rows[4].textContent).toBe('reverse 256 default');
    });

    it('leaves what is not an SGR sequence visible as text, like the server', () => {
        const env = boot(TERM(), 'terminal.js');
        env.push('term_out', { lines: [`${ESC}[2Jcleared${ESC}[`, `${ESC}[31`, `tail${ESC}`] });
        expect(textRows(env).slice(2).map((r) => r.textContent)).toEqual([`${ESC}[2Jcleared${ESC}[`, `${ESC}[31`, `tail${ESC}`]);
    });

    it('a sequence with a very long parameter list stays flat', () => {
        const env = boot(TERM(), 'terminal.js');
        env.push('term_out', { line: `${ESC}[${Array(100000).fill('1').join(';')}mtext` });
        const row = textRows(env)[2];
        expect(row.textContent).toBe('text');
        expect(row.querySelectorAll('span')).toHaveLength(1);
        expect(row.querySelector(':scope span span')).toBeNull();
    });

    it('reads at most 16 parameters per sequence', () => {
        const env = boot(TERM(), 'terminal.js');
        env.push('term_out', { lines: [
            `${ESC}[${'1;'.repeat(15)}31mwithin`, // the colour is the 16th parameter: applied
            `${ESC}[${'1;'.repeat(16)}31mbeyond`, // the 17th: ignored
        ] });
        const [within, beyond] = textRows(env).slice(2);
        expect(within.querySelector('span').style.color).not.toBe('');
        expect(beyond.querySelector('span').style.fontWeight).toBe('bold');
        expect(beyond.querySelector('span').style.color).toBe('');
    });

    it('many sequences in one line stay flat', () => {
        const env = boot(TERM(), 'terminal.js');
        const n = 3000;
        const line = Array.from({ length: n }, (_, i) => `${ESC}[${31 + (i % 6)}mx`).join('');
        env.push('term_out', { line });
        const row = textRows(env)[2];
        expect(row.textContent).toBe('x'.repeat(n));
        expect(row.querySelector(':scope span span')).toBeNull(); // flat: no nesting
        expect(row.querySelectorAll('span')).toHaveLength(n); // one span per run
    });

    it('oversized and degenerate lines produce bounded output', () => {
        const env = boot(TERM(), 'terminal.js');
        env.push('term_out', { line: 'y'.repeat(100_000) });
        env.push('term_out', { line: `${ESC}[31m` + 'z'.repeat(100_000) + `${ESC}[0m` });
        env.push('term_out', { line: `${ESC}[` + '1'.repeat(100_000) });
        env.push('term_out', { line: `${ESC}[;`.repeat(10_000) });
        const rows = textRows(env);
        expect(rows[2].textContent.length).toBe(100_000);
        expect(rows[3].textContent.length).toBe(100_000);
        expect(rows[3].querySelectorAll('span')).toHaveLength(1); // one colour run, not one per character
        for (const r of rows.slice(2)) expect(r.querySelectorAll('span').length).toBeLessThan(10_001);
    });

    it('everything streamed is text, never markup', () => {
        const env = boot(TERM(), 'terminal.js');
        const evil = '<img src=x onerror=window.__pwn=1><script>window.__pwn=2</script>';
        env.push('term_out', { lines: [evil, `${ESC}[31m${evil}${ESC}[0m`, `${ESC}[1;31m"><b>x</b>`] });
        expect(env.$('.dj-terminal img, .dj-terminal script, .dj-terminal b')).toBeNull();
        expect(env.window.__pwn).toBeUndefined();
        expect(textRows(env)[2].textContent).toBe(evil);
        expect(textRows(env)[3].textContent).toBe(evil);
    });

    it('honours data-line-numbers and data-max-lines, numbering from the last rendered line', () => {
        const env = boot(TERM({ attrs: ' data-stream-event="term_out" data-line-numbers="true" data-max-lines="4"' }), 'terminal.js');
        env.push('term_out', { lines: ['c', 'd', 'e'] });
        expect(textRows(env).map((r) => r.textContent)).toEqual(['total 2', 'c', 'd', 'e']);
        expect(env.$$('.dj-terminal__line-num').map((n) => n.textContent)).toEqual(['2', '3', '4', '5']);
    });

    it('without line numbers none are added', () => {
        const env = boot(TERM(), 'terminal.js');
        env.push('term_out', { line: 'x' });
        expect(env.$$('.dj-terminal__line-num')).toHaveLength(0);
    });

    it('does not rescan or measure per event, and recounts after a re-render', () => {
        const env = boot(TERM({ attrs: ' data-stream-event="term_out" data-max-lines="50"' }), 'terminal.js');
        const body = env.$('.dj-terminal__body');
        let queries = 0;
        const real = body.querySelectorAll.bind(body);
        body.querySelectorAll = (...a) => { queries += 1; return real(...a); };
        for (let i = 0; i < 400; i++) env.push('term_out', { lines: ['a' + i, 'b' + i] });
        expect(queries).toBe(0);
        expect(env.$$('.dj-terminal__line')).toHaveLength(50);
        expect(textRows(env).pop().textContent).toBe('b399');
    });

    describe('follow', () => {
        const stub = (body, st) => {
            Object.defineProperty(body, 'scrollHeight', { get: () => st.height, configurable: true });
            Object.defineProperty(body, 'clientHeight', { get: () => 100, configurable: true });
            Object.defineProperty(body, 'scrollTop', { get: () => st.top, set: (v) => { st.top = v; st.writes += 1; }, configurable: true });
        };
        const scrolled = (env, body, st, top) => {
            if (top < st.top) body.dispatchEvent(new env.window.Event('wheel', { bubbles: true })); // the reader moves up; a drop with no input is a browser clamp
            st.top = top; body.dispatchEvent(new env.window.Event('scroll'));
        };
        const setup = () => {
            const env = createEnv(TERM());
            const body = env.window.document.querySelector('.dj-terminal__body');
            const st = { height: 1000, top: 900, writes: 0 };
            stub(body, st);
            env.window.eval(read('terminal.js'));
            env.window.djust.mountHooks();
            env.$ = (s) => env.window.document.querySelector(s);
            env.push = (n, p) => env.window.djust.dispatchPushEventToHooks(n, p);
            return { env, body, st };
        };

        it('keeps following a fast stream whose own scroll events arrive late', async () => {
            const { env, body, st } = setup();
            scrolled(env, body, st, 900);
            for (let round = 0; round < 20; round++) {
                env.push('term_out', { line: 'x' + round });
                await frame(env);
                expect(st.top).toBe(st.height);
                st.height += 500;
                body.dispatchEvent(new env.window.Event('scroll')); // late: not at the new bottom, but not up either
            }
        });

        it('a reader who scrolled up stays up, and resuming at the bottom follows again', async () => {
            const { env, body, st } = setup();
            scrolled(env, body, st, 900);
            scrolled(env, body, st, 300);
            for (let round = 0; round < 10; round++) {
                env.push('term_out', { line: 'x' });
                st.height += 500;
                await frame(env);
                expect(st.top).toBe(300);
            }
            scrolled(env, body, st, st.height - 100);
            env.push('term_out', { line: 'y' });
            await frame(env);
            expect(st.top).toBe(st.height);
        });

        // A layout model for max_lines streams: a row whose text is T<n> is tall
        // (200 px), any other 50 px; reading the height lays out, which clamps
        // scrollTop to the new maximum like a browser does.
        const trimmed = () => {
            const env = createEnv(TERM({ attrs: ' data-stream-event="term_out" data-max-lines="4"' }));
            const body = env.window.document.querySelector('.dj-terminal__body');
            const st = { top: 0, writes: 0, reads: 0 };
            const height = () => Array.from(body.children).reduce((n, r) => n + (/T\d/.test(r.textContent) ? 200 : 50), 0);
            const clamp = () => { st.top = Math.min(st.top, Math.max(0, height() - 100)); };
            Object.defineProperty(body, 'scrollHeight', { get: () => { st.reads += 1; clamp(); return height(); }, configurable: true });
            Object.defineProperty(body, 'clientHeight', { get: () => 100, configurable: true });
            Object.defineProperty(body, 'scrollTop', { get: () => { clamp(); return st.top; }, set: (v) => { st.top = v; st.writes += 1; }, configurable: true });
            env.window.eval(read('terminal.js'));
            env.window.djust.mountHooks();
            const push = (lines) => env.window.djust.dispatchPushEventToHooks('term_out', { lines });
            const bottom = () => Math.max(0, height() - 100);
            const scroll = (top) => {
                if (top < body.scrollTop) body.dispatchEvent(new env.window.Event('wheel', { bubbles: true })); // the reader moves up
                st.top = top; body.dispatchEvent(new env.window.Event('scroll'));
            };
            const top = () => body.scrollTop;
            return { env, body, st, push, bottom, scroll, top };
        };

        it('keeps following when trimming old rows clamps scrollTop (the late scroll event is not the reader scrolling up)', async () => {
            const { env, body, push, bottom, scroll, top } = trimmed();
            push(['T1', 'T2', 'T3', 'T4']); // four tall rows
            await frame(env);
            scroll(bottom());
            for (let round = 0; round < 6; round++) {
                push(['a', 'b', 'c', 'd', 'e', 'f']); // trims the tall rows: the browser clamps scrollTop at the next layout
                await frame(env);
                push(['T5', 'T6']); // more (tall) rows arrive before the clamp's scroll event is delivered
                body.dispatchEvent(new env.window.Event('scroll'));
                await frame(env);
                expect(top()).toBe(bottom()); // still following
            }
        });

        it('a reader who scrolls up during a trimmed stream (appends every frame) stays up, and resuming at the bottom follows again', async () => {
            const { env, st, push, bottom, scroll, top } = trimmed();
            push(['T1', 'T2', 'T3', 'T4']);
            await frame(env);
            scroll(bottom());
            st.reads = 0;
            for (let i = 0; i < 20; i++) push(['a' + i, 'b' + i]); // trimming on every append...
            expect(st.reads).toBe(0); // ...without measuring per append
            scroll(0); // the reader scrolls up before the frame
            await frame(env);
            expect(top()).toBe(0);
            for (let round = 0; round < 5; round++) {
                push(['x' + round, 'y' + round, 'z' + round]); // still trimming
                await frame(env);
                expect(top()).toBe(0);
            }
            scroll(bottom());
            push(['T9']);
            await frame(env);
            expect(top()).toBe(bottom());
        });

        it('the same, when the clamp\'s own scroll event has been delivered first', async () => {
            const { env, push, bottom, scroll, top } = trimmed();
            push(['T1', 'T2', 'T3', 'T4']);
            await frame(env);
            scroll(bottom());
            push(['a', 'b', 'c']);
            await frame(env);
            scroll(top()); // the clamp's (late) scroll event
            scroll(10); // then the reader
            push(['d']);
            await frame(env);
            expect(top()).toBe(10);
        });

        describe('only the reader leaves the bottom (input, not the drop alone)', () => {
            const drop = (env, body, st, top) => { st.top = top; body.dispatchEvent(new env.window.Event('scroll')); }; // no input
            const clock = (env, t) => { env.window.Date.now = () => t.now; };
            const stillFollows = async (env, st) => {
                env.push('term_out', { line: 'more' });
                await frame(env);
                return st.top === st.height;
            };

            it('a drop with no reader input (a browser clamp) does not unpin', async () => {
                const { env, body, st } = setup();
                drop(env, body, st, 900); // at the bottom
                drop(env, body, st, 300);
                expect(await stillFollows(env, st)).toBe(true);
            });

            for (const [name, type] of [['wheel', 'wheel'], ['touch', 'touchmove'], ['key', 'keydown']]) {
                it(`a ${name} on the log, then a drop, unpins`, async () => {
                    const { env, body, st } = setup();
                    drop(env, body, st, 900); // at the bottom
                    body.dispatchEvent(new env.window.Event(type, { bubbles: true }));
                    drop(env, body, st, 300);
                    expect(await stillFollows(env, st)).toBe(false);
                    expect(st.top).toBe(300);
                });
            }

            it('a drop more than 600 ms after the last input does not unpin', async () => {
                const { env, body, st } = setup();
                const t = { now: 1000 };
                clock(env, t);
                drop(env, body, st, 900); // at the bottom
                body.dispatchEvent(new env.window.Event('wheel', { bubbles: true }));
                t.now += 700;
                drop(env, body, st, 300);
                expect(await stillFollows(env, st)).toBe(true);
            });

            it('a held pointer (a scrollbar drag) counts however long it is held, and ends on release', async () => {
                const { env, body, st } = setup();
                const t = { now: 1000 };
                clock(env, t);
                drop(env, body, st, 900); // at the bottom
                body.dispatchEvent(new env.window.Event('pointerdown', { bubbles: true }));
                t.now += 5000;
                drop(env, body, st, 300);
                expect(await stillFollows(env, st)).toBe(false);

                const second = setup();
                const t2 = { now: 1000 };
                clock(second.env, t2);
                drop(second.env, second.body, second.st, 900);
                second.body.dispatchEvent(new second.env.window.Event('pointerdown', { bubbles: true }));
                second.env.window.document.dispatchEvent(new second.env.window.Event('pointerup', { bubbles: true }));
                t2.now += 5000;
                drop(second.env, second.body, second.st, 300);
                expect(await stillFollows(second.env, second.st)).toBe(true);
            });

            it('input outside the log is not the reader scrolling it', async () => {
                const { env, body, st } = setup();
                drop(env, body, st, 900); // at the bottom
                env.window.document.body.dispatchEvent(new env.window.Event('wheel', { bubbles: true }));
                drop(env, body, st, 300);
                expect(await stillFollows(env, st)).toBe(true);
            });
        });

        it('scrolls once per frame, never measures per event, and not if the reader scrolled up in the same frame', async () => {
            const { env, body, st } = setup();
            scrolled(env, body, st, 900);
            let reads = 0;
            Object.defineProperty(body, 'scrollHeight', { get: () => { reads += 1; return st.height; }, configurable: true });
            st.writes = 0;
            for (let i = 0; i < 50; i++) env.push('term_out', { line: 'x' });
            expect(reads).toBe(0);
            expect(st.writes).toBe(0);
            await frame(env);
            expect(st.writes).toBe(1);
            env.push('term_out', { line: 'again' });
            scrolled(env, body, st, 200);
            await frame(env);
            expect(st.top).toBe(200);
        });
    });
});

// ---------------------------------------------------------------------------
// Tour
// ---------------------------------------------------------------------------

const rect = (el, r) => { el.getBoundingClientRect = () => ({ left: 0, top: 0, width: 0, height: 0, right: r.left + r.width, bottom: r.top + r.height, ...r }); };

function tourEnv(markup = TOUR(), { target = { left: 100, top: 100, width: 80, height: 30 }, extraBody = '' } = {}) {
    const env = createEnv('<button id="open">open</button><div id="create">create</div>' + extraBody + markup);
    env.$ = (s) => env.window.document.querySelector(s);
    env.$$ = (s) => Array.from(env.window.document.querySelectorAll(s));
    env.live = () => (env.window.document.getElementById('dj-component-live') || {}).textContent;
    const creator = env.$('#create');
    if (target) rect(creator, target);
    rect(env.$('.dj-tour__overlay'), { left: 0, top: 0, width: 1024, height: 768 });
    rect(env.$('.dj-tour__popover'), { left: 0, top: 0, width: 320, height: 160 });
    env.window.eval(read('tour.js'));
    env.window.document.getElementById('open').focus();
    env.window.djust.mountHooks();
    return env;
}

describe('Tour', () => {
    const pop = (env) => env.$('.dj-tour__popover');

    it('names and describes the dialog from the step, and takes focus into the popover', () => {
        const env = tourEnv();
        const root = env.$('.dj-tour');
        expect(root.getAttribute('aria-labelledby')).toBe(env.$('.dj-tour__title').id);
        expect(root.getAttribute('aria-describedby')).toBe(env.$('.dj-tour__content').id);
        expect(pop(env).getAttribute('tabindex')).toBe('-1');
        expect(env.window.document.activeElement).toBe(pop(env));
    });

    it('keeps a name an app supplied', () => {
        const env = tourEnv(TOUR({ extra: ' aria-label="Onboarding"' }));
        expect(env.$('.dj-tour').hasAttribute('aria-labelledby')).toBe(false);
    });

    it('cuts a hole in the overlay, rings the target, and puts the popover below it', () => {
        const env = tourEnv();
        const clip = env.$('.dj-tour__overlay').style.clipPath;
        expect(clip).toContain('evenodd');
        const ring = env.$('.dj-tour__ring');
        expect(ring.style.left).toBe('94px');
        expect(ring.style.top).toBe('94px');
        expect(ring.style.width).toBe('92px');
        expect(ring.style.height).toBe('42px');
        expect(ring.getAttribute('aria-hidden')).toBe('true');
        expect(ring.style.pointerEvents).toBe('none');
        expect(pop(env).style.transform).toBe('none');
        expect(pop(env).style.top).toBe('146px'); // below: y2 + gap
        expect(pop(env).style.left).toBe('0px'.replace('0', '8')); // centred on the target, clamped to the margin
    });

    it('goes above, beside or to the bottom edge when below does not fit', () => {
        let env = tourEnv(TOUR(), { target: { left: 400, top: 700, width: 80, height: 30 } });
        expect(pop(env).style.top).toBe(String(700 - 4 - 12 - 160) + 'px');
        env = tourEnv(TOUR(), { target: { left: 100, top: 10, width: 80, height: 780 } });
        // neither above nor below fits: to the right
        expect(pop(env).style.left).toBe(String(100 - 4 + 88 + 12) + 'px');
        env = tourEnv(TOUR(), { target: { left: 0, top: 0, width: 1000, height: 800 } });
        // nothing fits beside it: the bottom edge
        expect(pop(env).style.top).toBe('600px');
    });

    describe('inside a transformed ancestor (position: fixed is then relative to the ancestor)', () => {
        // The stylesheet's inset:0 gives the overlay the ancestor's box (here 600 x 0 at 50,20);
        // inline left/top/width/height are measured from that box's origin.
        const transformed = (target = { left: 150, top: 120, width: 80, height: 30 }) => {
            const env = tourEnv(TOUR(), { target });
            const overlay = env.$('.dj-tour__overlay');
            const num = (v, d) => (v === '' || v === 'auto' ? d : parseFloat(v));
            overlay.getBoundingClientRect = () => {
                const st = overlay.style;
                if (st.width === '') return { left: 50, top: 20, width: 600, height: 0, right: 650, bottom: 20 };
                const left = 50 + num(st.left, 0);
                const top = 20 + num(st.top, 0);
                return { left, top, width: num(st.width, 0), height: num(st.height, 0), right: left + num(st.width, 0), bottom: top + num(st.height, 0) };
            };
            env.window.djust.updateHooks();
            return env;
        };

        it('puts the overlay back over the whole viewport and aligns the ring with the target', () => {
            const env = transformed();
            const o = env.$('.dj-tour__overlay').style;
            expect([o.left, o.top, o.width, o.height]).toEqual(['-50px', '-20px', '1024px', '768px']);
            const ring = env.$('.dj-tour__ring').style;
            expect([ring.left, ring.top]).toEqual(['144px', '114px']); // viewport coordinates: (150 - 6, 120 - 6)
        });

        it('offsets the popover by the ancestor so it lands where it was measured', () => {
            const env = transformed();
            expect(pop(env).style.left).toBe('-20px'); // viewport 30, minus the ancestor's 50
            expect(pop(env).style.top).toBe('146px'); // viewport 166, minus the ancestor's 20
        });

        it('centres a popover with no target itself (the 50% would be measured from the ancestor)', () => {
            const env = transformed({ left: 150, top: 120, width: 80, height: 30 });
            env.$('#create').remove();
            env.window.djust.updateHooks();
            expect(pop(env).style.transform).toBe('none');
            expect(pop(env).style.left).toBe('302px'); // (1024 - 320) / 2 - 50
            expect(pop(env).style.top).toBe('284px'); // (768 - 160) / 2 - 20
        });

        it('leaves a normally placed tour alone', () => {
            const env = tourEnv();
            const o = env.$('.dj-tour__overlay').style;
            expect([o.left, o.top, o.width, o.height]).toEqual(['', '', '', '']);
        });
    });

    it('a target that is missing, invalid, hidden or inside the tour leaves the popover centred', () => {
        for (const target of ['#nope', 'div[', '', '.dj-tour__title']) {
            const env = tourEnv(TOUR({ target }));
            // even a shown element inside the tour is not spotlighted
            const title = env.$('.dj-tour__title');
            if (title) rect(title, { left: 10, top: 10, width: 50, height: 20 });
            env.window.djust.updateHooks();
            expect(env.$('.dj-tour__overlay').style.clipPath).toBe('');
            expect(pop(env).style.top).toBe('');
            expect(pop(env).style.transform).toBe('');
            expect(env.$('.dj-tour__ring')).toBeNull();
        }
        const hidden = tourEnv(TOUR(), { target: { left: 0, top: 0, width: 0, height: 0 } });
        expect(hidden.$('.dj-tour__ring')).toBeNull();
        expect(pop(hidden).style.top).toBe('');
    });

    it('a target removed by a server patch falls back to centred, and one that appears is found', () => {
        const env = tourEnv();
        expect(env.$('.dj-tour__ring').style.display).toBe('');
        env.$('#create').remove();
        env.window.djust.updateHooks();
        expect(env.$('.dj-tour__ring').style.display).toBe('none');
        expect(env.$('.dj-tour__overlay').style.clipPath).toBe('');
        expect(pop(env).style.top).toBe('');
        const back = env.window.document.createElement('div');
        back.id = 'create';
        rect(back, { left: 300, top: 200, width: 60, height: 20 });
        env.window.document.body.appendChild(back);
        env.window.djust.updateHooks();
        expect(env.$('.dj-tour__ring').style.display).toBe('');
        expect(env.$('.dj-tour__ring').style.left).toBe('294px');
    });

    it('scrolls the target into view once per step', () => {
        const env = createEnv('<div id="create">c</div>' + TOUR());
        const calls = [];
        env.window.document.getElementById('create').scrollIntoView = (o) => calls.push(o);
        rect(env.window.document.getElementById('create'), { left: 100, top: 100, width: 80, height: 30 });
        env.window.eval(read('tour.js'));
        env.window.djust.mountHooks();
        env.window.djust.updateHooks();
        env.window.djust.updateHooks();
        expect(calls).toHaveLength(1);
        expect(calls[0].block).toBe('center');
        expect(calls[0].behavior).toBe('smooth');
        // a new step scrolls again
        const root = env.window.document.querySelector('.dj-tour');
        root.setAttribute('data-step', '1');
        env.window.djust.updateHooks();
        expect(calls).toHaveLength(2);
    });

    it('does not animate the scroll under prefers-reduced-motion', () => {
        const env = createEnv('<div id="create">c</div>' + TOUR(), {
            matchMedia: (q) => ({ matches: q.includes('reduce'), media: q, addEventListener() {}, removeEventListener() {} }),
        });
        const calls = [];
        const t = env.window.document.getElementById('create');
        t.scrollIntoView = (o) => calls.push(o);
        rect(t, { left: 1, top: 1, width: 10, height: 10 });
        env.window.eval(read('tour.js'));
        env.window.djust.mountHooks();
        expect(calls[0].behavior).toBe('auto');
    });

    it('a new step is announced and takes focus; other patches do not', () => {
        const env = tourEnv();
        env.window.djust.updateHooks();
        expect(env.live()).toBeUndefined();
        env.$('.dj-tour__next').focus();
        const root = env.$('.dj-tour');
        root.setAttribute('data-step', '1');
        env.$('.dj-tour__title').textContent = 'Create';
        env.window.djust.updateHooks();
        expect(env.live()).toBe('Step 2 of 3: Create');
        expect(env.window.document.activeElement).toBe(pop(env));
    });

    it('puts focus back in the popover when a patch dropped it', () => {
        const env = tourEnv();
        env.$('.dj-tour__next').focus();
        env.$('.dj-tour__next').replaceWith(env.$('.dj-tour__next').cloneNode(true));
        expect(env.window.document.activeElement).toBe(env.window.document.body);
        env.window.djust.updateHooks();
        expect(env.window.document.activeElement).toBe(pop(env));
    });

    describe('keyboard', () => {
        const watch = (env) => {
            const log = [];
            for (const cls of ['skip', 'prev', 'next']) {
                env.$(`.dj-tour__${cls}`)?.addEventListener('click', () => log.push(cls));
            }
            return log;
        };

        it('ArrowRight / ArrowLeft / Escape press the rendered Next / Back / Skip', () => {
            const env = tourEnv(TOUR({ step: 1 }));
            const log = watch(env);
            key(env.window, pop(env), 'ArrowRight');
            key(env.window, pop(env), 'ArrowLeft');
            key(env.window, pop(env), 'Escape');
            expect(log).toEqual(['next', 'prev', 'skip']);
        });

        it('on the last step the right arrow does not finish, and Escape presses Finish', () => {
            const env = tourEnv(TOUR({ step: 2 }));
            const log = watch(env);
            expect(key(env.window, pop(env), 'ArrowRight').defaultPrevented).toBe(false);
            key(env.window, pop(env), 'Escape');
            expect(log).toEqual(['next']); // the Finish button carries the next class
            expect(env.$('.dj-tour__next').textContent).toBe('Finish');
        });

        it('with no Skip button (show_skip off) Escape does nothing before the last step', () => {
            const env = tourEnv(TOUR({ step: 0 }).replace(/<button class="dj-tour__skip".*?<\/button>/, ''));
            const log = watch(env);
            key(env.window, pop(env), 'Escape');
            expect(log).toEqual([]);
        });

        it('Escape does not reach djust\'s own modal handler (which would click the first dj-click control with no value)', () => {
            for (const markup of [TOUR({ step: 1 }), TOUR({ step: 0 }).replace(/<button class="dj-tour__skip".*?<\/button>/, '')]) {
                const env = tourEnv(markup);
                const reached = [];
                env.window.document.addEventListener('keydown', (e) => reached.push(e.key));
                key(env.window, pop(env), 'Escape');
                expect(reached).toEqual([]);
            }
        });

        it('no Back on the first step: ArrowLeft does nothing', () => {
            const env = tourEnv(TOUR({ step: 0 }));
            const log = watch(env);
            key(env.window, pop(env), 'ArrowLeft');
            expect(log).toEqual([]);
        });

        it('modified keys are left alone', () => {
            const env = tourEnv(TOUR({ step: 1 }));
            const log = watch(env);
            key(env.window, pop(env), 'ArrowRight', { altKey: true });
            key(env.window, pop(env), 'Escape', { ctrlKey: true });
            expect(log).toEqual([]);
        });

        it('works wherever focus has wandered to, and pulls focus back into the dialog', () => {
            const env = tourEnv(TOUR({ step: 1 }), { extraBody: '<input id="behind">' });
            const log = watch(env);
            env.$('#behind').focus();
            expect(env.window.document.activeElement).toBe(pop(env)); // focusin outside is brought back
            key(env.window, env.$('#behind'), 'Escape');
            expect(log).toEqual(['skip']);
        });

        it('a field in the spotlight keeps focus, and the arrow keys belong to it (Escape and Tab still act)', () => {
            const env = tourEnv(TOUR({ step: 1, target: '#field' }), { extraBody: '<input id="field">' });
            const field = env.$('#field');
            rect(field, { left: 100, top: 100, width: 120, height: 24 });
            env.window.djust.updateHooks();
            const log = watch(env);
            field.focus();
            expect(env.window.document.activeElement).toBe(field); // not pulled back to the popover
            expect(key(env.window, field, 'ArrowRight').defaultPrevented).toBe(false);
            expect(key(env.window, field, 'ArrowLeft').defaultPrevented).toBe(false);
            expect(log).toEqual([]);
            key(env.window, field, 'Escape');
            expect(log).toEqual(['skip']);
        });

        it('a field that is NOT the target still cannot take focus from the dialog', () => {
            const env = tourEnv(TOUR({ step: 1 }), { extraBody: '<input id="other">' });
            env.$('#other').focus();
            expect(env.window.document.activeElement).toBe(pop(env));
        });

        it('Tab and Shift+Tab stay inside the popover', () => {
            const env = tourEnv(TOUR({ step: 1 }));
            const buttons = env.$$('.dj-tour button');
            const first = buttons[0];
            const last = buttons[buttons.length - 1];
            last.focus();
            expect(key(env.window, last, 'Tab').defaultPrevented).toBe(true);
            expect(env.window.document.activeElement).toBe(first);
            expect(key(env.window, first, 'Tab', { shiftKey: true }).defaultPrevented).toBe(true);
            expect(env.window.document.activeElement).toBe(last);
            buttons[1].focus();
            expect(key(env.window, buttons[1], 'Tab').defaultPrevented).toBe(false);
        });
    });

    it('re-places on window resize and scroll, once per frame', async () => {
        const env = tourEnv();
        rect(env.$('#create'), { left: 300, top: 300, width: 80, height: 30 });
        env.window.dispatchEvent(new env.window.Event('resize'));
        env.window.dispatchEvent(new env.window.Event('resize'));
        await frame(env);
        expect(env.$('.dj-tour__ring').style.left).toBe('294px');
        rect(env.$('#create'), { left: 500, top: 300, width: 80, height: 30 });
        env.window.document.dispatchEvent(new env.window.Event('scroll'));
        await frame(env);
        expect(env.$('.dj-tour__ring').style.left).toBe('494px');
    });

    it('ending the tour removes its listeners and gives focus back to the opener', async () => {
        const env = tourEnv();
        const root = env.$('.dj-tour');
        root.remove();
        env.window.djust.updateHooks();
        expect(env.window.document.activeElement.id).toBe('open');
        rect(env.$('#create'), { left: 700, top: 300, width: 80, height: 30 });
        env.window.dispatchEvent(new env.window.Event('resize'));
        await frame(env);
        // nothing re-placed a ring for the dead tour
        expect(env.$('.dj-tour__ring')).toBeNull();
        // and its document keyboard handler is gone: focus is not pulled anywhere
        env.$('#open').focus();
        expect(env.window.document.activeElement.id).toBe('open');
    });

    it('does not double-bind after repeated patches', () => {
        const env = tourEnv(TOUR({ step: 1 }));
        let presses = 0;
        env.$('.dj-tour__next').addEventListener('click', () => { presses += 1; });
        for (let i = 0; i < 5; i++) env.window.djust.updateHooks();
        key(env.window, pop(env), 'ArrowRight');
        key(env.window, env.window.document.body, 'ArrowRight');
        expect(presses).toBe(2); // one from the dialog's own handler, one from the document handler (not five each)
    });
});
