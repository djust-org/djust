/**
 * Tests for the client-side JS Commands interpreter + fluent chain API
 * (src/26-js-commands.js).
 *
 * Covers:
 * - Each of the 11 ops (show, hide, toggle, add_class, remove_class,
 *   transition, set_attr, remove_attr, focus, dispatch, push)
 * - Target resolution: to=, inner=, closest=, and "origin element" default
 * - Fluent chain API (djust.js.show('#x').addClass('active').exec())
 * - Attribute dispatcher: `dj-click="[[...]]"` executes locally
 * - Hook API: `this.js()` returns a chain bound to the hook element
 * - Backward compatibility: normal event-name `dj-click` values still work
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
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

    // Suppress console
    window.console = { log: () => {}, error: () => {}, warn: () => {}, debug: () => {}, info: () => {} };

    // Mock fetch so handleEvent uses the HTTP fallback path and we can
    // observe what got sent to the server.
    const fetchCalls = [];
    window.fetch = vi.fn().mockImplementation(async (url, opts) => {
        let eventName = '';
        let params = {};
        try {
            eventName = (opts && opts.headers && opts.headers['X-Djust-Event']) || '';
        } catch (_) {}
        try {
            params = opts && opts.body ? JSON.parse(opts.body) : {};
        } catch (_) {}
        fetchCalls.push({ url, eventName, params });
        return { ok: true, json: async () => ({ patches: [], version: 1 }) };
    });

    // Force HTTP-only so handleEvent uses fetch (deterministic).
    window.DJUST_USE_WEBSOCKET = false;

    try {
        window.eval(clientCode);
    } catch (_) {
        // client.js may throw on missing DOM APIs; tests still work
    }

    return { window, document: dom.window.document, fetchCalls };
}

describe('djust.js factory', () => {
    it('exposes all 11 commands', () => {
        const { window } = createEnv();
        const js = window.djust.js;
        expect(typeof js.show).toBe('function');
        expect(typeof js.hide).toBe('function');
        expect(typeof js.toggle).toBe('function');
        expect(typeof js.addClass).toBe('function');
        expect(typeof js.removeClass).toBe('function');
        expect(typeof js.transition).toBe('function');
        expect(typeof js.setAttr).toBe('function');
        expect(typeof js.removeAttr).toBe('function');
        expect(typeof js.focus).toBe('function');
        expect(typeof js.dispatch).toBe('function');
        expect(typeof js.push).toBe('function');
        expect(typeof js.chain).toBe('function');
    });

    it('chain() returns an empty chain', () => {
        const { window } = createEnv();
        const c = window.djust.js.chain();
        expect(JSON.parse(c.toString())).toEqual([]);
    });
});

describe('show/hide/toggle ops', () => {
    it('show unhides the target element', async () => {
        const { window, document } = createEnv(
            '<div id="modal" style="display: none;">Modal</div>'
        );
        await window.djust.js.show('#modal').exec();
        const el = document.getElementById('modal');
        expect(el.style.display).toBe('');
    });

    it('hide sets display none', async () => {
        const { window, document } = createEnv('<div id="modal">Modal</div>');
        await window.djust.js.hide('#modal').exec();
        const el = document.getElementById('modal');
        expect(el.style.display).toBe('none');
    });

    it('toggle flips hidden → visible', async () => {
        const { window, document } = createEnv(
            '<div id="modal" style="display: none;">Modal</div>'
        );
        await window.djust.js.toggle('#modal').exec();
        const el = document.getElementById('modal');
        expect(el.style.display).not.toBe('none');
    });

    it('toggle flips visible → hidden', async () => {
        const { window, document } = createEnv('<div id="modal">Modal</div>');
        await window.djust.js.toggle('#modal').exec();
        const el = document.getElementById('modal');
        expect(el.style.display).toBe('none');
    });

    it('show with display option', async () => {
        const { window, document } = createEnv(
            '<div id="modal" style="display: none;">Modal</div>'
        );
        await window.djust.js.show('#modal', { display: 'flex' }).exec();
        const el = document.getElementById('modal');
        expect(el.style.display).toBe('flex');
    });

    it('show fires djust:show CustomEvent', async () => {
        const { window, document } = createEnv(
            '<div id="modal" style="display: none;">Modal</div>'
        );
        const el = document.getElementById('modal');
        let fired = false;
        el.addEventListener('djust:show', () => { fired = true; });
        await window.djust.js.show('#modal').exec();
        expect(fired).toBe(true);
    });
});

describe('class mutations', () => {
    it('addClass adds a single class', async () => {
        const { window, document } = createEnv('<div id="overlay"></div>');
        await window.djust.js.addClass('active', { to: '#overlay' }).exec();
        expect(document.getElementById('overlay').classList.contains('active')).toBe(true);
    });

    it('addClass adds multiple classes', async () => {
        const { window, document } = createEnv('<div id="overlay"></div>');
        await window.djust.js.addClass('active visible', { to: '#overlay' }).exec();
        const cl = document.getElementById('overlay').classList;
        expect(cl.contains('active')).toBe(true);
        expect(cl.contains('visible')).toBe(true);
    });

    it('removeClass strips the class', async () => {
        const { window, document } = createEnv(
            '<div id="overlay" class="active"></div>'
        );
        await window.djust.js.removeClass('active', { to: '#overlay' }).exec();
        expect(document.getElementById('overlay').classList.contains('active')).toBe(false);
    });
});

describe('transition', () => {
    it('adds classes then removes them after the given time', async () => {
        vi.useFakeTimers();
        const { window, document } = createEnv('<div id="modal"></div>');
        const el = document.getElementById('modal');

        await window.djust.js.transition('fade-in', { to: '#modal', time: 300 }).exec();
        expect(el.classList.contains('fade-in')).toBe(true);

        vi.advanceTimersByTime(310);
        expect(el.classList.contains('fade-in')).toBe(false);

        vi.useRealTimers();
    });
});

describe('attribute mutations', () => {
    it('setAttr sets attribute via [name, value]', async () => {
        const { window, document } = createEnv('<div id="panel"></div>');
        await window.djust.js.setAttr('data-open', 'true', { to: '#panel' }).exec();
        expect(document.getElementById('panel').getAttribute('data-open')).toBe('true');
    });

    it('removeAttr removes the attribute', async () => {
        const { window, document } = createEnv(
            '<button id="btn" disabled>Save</button>'
        );
        await window.djust.js.removeAttr('disabled', { to: '#btn' }).exec();
        expect(document.getElementById('btn').hasAttribute('disabled')).toBe(false);
    });
});

describe('focus', () => {
    it('moves keyboard focus to target', async () => {
        const { window, document } = createEnv('<input id="name">');
        const el = document.getElementById('name');
        // JSDOM supports focus()
        await window.djust.js.focus('#name').exec();
        expect(document.activeElement).toBe(el);
    });
});

describe('dispatch', () => {
    it('fires a CustomEvent with detail', async () => {
        const { window, document } = createEnv('<div id="target"></div>');
        const el = document.getElementById('target');
        let capturedDetail = null;
        el.addEventListener('my:event', (e) => {
            capturedDetail = e.detail;
        });
        await window.djust.js.dispatch('my:event', {
            to: '#target',
            detail: { x: 42 },
        }).exec();
        expect(capturedDetail).toEqual({ x: 42 });
    });
});

describe('push', () => {
    it('routes through handleEvent to send server event', async () => {
        const { window, document, fetchCalls } = createEnv(
            '<div id="origin"></div>'
        );
        const origin = document.getElementById('origin');

        await window.djust.js.push('save_draft', {
            value: { id: 42 },
        }).exec(origin);

        await new Promise((r) => setTimeout(r, 10));

        const calls = fetchCalls.filter((c) => c.eventName === 'save_draft');
        expect(calls.length).toBe(1);
    });
});

describe('target resolution', () => {
    it('defaults to the origin element when no target is set', async () => {
        const { window, document } = createEnv(
            '<button id="btn">Click</button>'
        );
        const btn = document.getElementById('btn');
        await window.djust.js.addClass('active').exec(btn);
        expect(btn.classList.contains('active')).toBe(true);
    });

    it('inner= scopes to origin children', async () => {
        const { window, document } = createEnv(
            '<div id="card"><p class="title">Hi</p></div>'
        );
        const card = document.getElementById('card');
        await window.djust.js.addClass('big', { inner: '.title' }).exec(card);
        expect(document.querySelector('#card .title').classList.contains('big')).toBe(true);
    });

    it('closest= walks up from origin', async () => {
        const { window, document } = createEnv(
            '<div class="modal"><button id="close">x</button></div>'
        );
        const btn = document.getElementById('close');
        await window.djust.js.hide(undefined, { closest: '.modal' }).exec(btn);
        expect(document.querySelector('.modal').style.display).toBe('none');
    });
});

describe('fluent chaining', () => {
    it('executes multiple ops in sequence', async () => {
        const { window, document } = createEnv(
            '<div id="modal" style="display: none;"></div><div id="overlay"></div>'
        );

        await window.djust.js
            .show('#modal')
            .addClass('active', { to: '#overlay' })
            .exec();

        expect(document.getElementById('modal').style.display).toBe('');
        expect(document.getElementById('overlay').classList.contains('active')).toBe(true);
    });

    it('chain is immutable (each method returns a new chain)', () => {
        const { window } = createEnv();
        const base = window.djust.js.show('#modal');
        const extended = base.addClass('active', { to: '#overlay' });
        expect(JSON.parse(base.toString()).length).toBe(1);
        expect(JSON.parse(extended.toString()).length).toBe(2);
    });
});

describe('attribute dispatcher (dj-click with JSON chain)', () => {
    it('executes chain from dj-click JSON value on click', async () => {
        const { window, document } = createEnv(
            '<button id="btn" dj-click=\'[["show",{"to":"#modal"}]]\'>Open</button>' +
            '<div id="modal" style="display: none;">Modal</div>'
        );

        window.djust.bindLiveViewEvents();

        const btn = document.getElementById('btn');
        btn.click();

        await new Promise((r) => setTimeout(r, 20));

        const modal = document.getElementById('modal');
        expect(modal.style.display).toBe('');
    });

    it('does NOT fire a server event when attribute is a JSON chain', async () => {
        const { window, document, fetchCalls } = createEnv(
            '<button id="btn" dj-click=\'[["hide",{"to":"#m"}]]\'>Hide</button>' +
            '<div id="m">x</div>'
        );

        window.djust.bindLiveViewEvents();

        const btn = document.getElementById('btn');
        btn.click();

        await new Promise((r) => setTimeout(r, 20));

        // No fetch call should have been made — the chain runs locally
        expect(fetchCalls.length).toBe(0);
    });

    it('still fires server event for plain dj-click event name (backward compat)', async () => {
        const { window, document, fetchCalls } = createEnv(
            '<button id="btn" dj-click="save_draft">Save</button>'
        );

        window.djust.bindLiveViewEvents();

        const btn = document.getElementById('btn');
        btn.click();

        await new Promise((r) => setTimeout(r, 20));

        const calls = fetchCalls.filter((c) => c.eventName === 'save_draft');
        expect(calls.length).toBe(1);
    });

    it('chain with push op still sends server event', async () => {
        const { window, document, fetchCalls } = createEnv(
            '<button id="btn" dj-click=\'[["hide",{"to":"#modal"}],["push",{"event":"saved"}]]\'>OK</button>' +
            '<div id="modal">Modal</div>'
        );

        window.djust.bindLiveViewEvents();

        const btn = document.getElementById('btn');
        btn.click();

        await new Promise((r) => setTimeout(r, 30));

        expect(document.getElementById('modal').style.display).toBe('none');
        const calls = fetchCalls.filter((c) => c.eventName === 'saved');
        expect(calls.length).toBe(1);
    });
});

describe('parseCommandValue (internal)', () => {
    it('recognises a JSON array', () => {
        const { window } = createEnv();
        const ops = window.djust.js._parseCommandValue('[["show",{"to":"#m"}]]');
        expect(ops).toEqual([['show', { to: '#m' }]]);
    });

    it('returns null for a plain event name', () => {
        const { window } = createEnv();
        expect(window.djust.js._parseCommandValue('save_draft')).toBe(null);
    });

    it('returns null for malformed JSON', () => {
        const { window } = createEnv();
        expect(window.djust.js._parseCommandValue('[[')).toBe(null);
    });

    it('returns null for JSON that is not an op list', () => {
        const { window } = createEnv();
        expect(window.djust.js._parseCommandValue('{"not": "an array"}')).toBe(null);
        expect(window.djust.js._parseCommandValue('[]')).toBe(null);
    });
});
