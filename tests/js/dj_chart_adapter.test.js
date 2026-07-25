/**
 * dj-chart official adapter (issue #2063, ADR-025 milestone C).
 *
 * Contract pinned by these tests — the *observable* behavior a user gets
 * after setting DJUST_CONFIG['extensions'] = ['chart'] and putting a
 * <canvas dj-hook="Chart"> in a template:
 *
 *   - The adapter registers a `Chart` hook additively; it must NOT clobber
 *     a user's existing window.djust.hooks entries.
 *   - mounted() constructs a chart from dj-hook-value-* (ADR-025 typed
 *     values), using the user-supplied window.Chart library.
 *   - Missing library => one clear console.error, never a throw that would
 *     break the whole hook-mount sweep for other hooks on the page.
 *   - updated() mutates the EXISTING instance (the #1724 teardown class):
 *     no destroy()/re-construct on a server re-render.
 *   - destroyed() calls chart.destroy() exactly once (leak guard).
 *
 * This file is the Stage-4 artifact (#1210): written BEFORE the adapter
 * exists, so the plan reflects what users observe rather than what the
 * implementation happens to do.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync(
    './python/djust/static/djust/client.js',
    'utf-8'
);

const ADAPTER_PATH = './python/djust/static/djust/ext/dj-chart.js';

function readAdapter() {
    return fs.readFileSync(ADAPTER_PATH, 'utf-8');
}

/** Minimal Chart.js stand-in recording constructor/update/destroy calls. */
function makeFakeChartLib(calls) {
    return function FakeChart(ctx, config) {
        calls.push({ op: 'construct', ctx, config });
        this.config = config;
        this.data = config.data;
        this.update = function () {
            calls.push({ op: 'update', data: this.data });
        };
        this.destroy = function () {
            calls.push({ op: 'destroy' });
        };
    };
}

function createDom(canvasAttrs = '') {
    const dom = new JSDOM(
        '<!DOCTYPE html><html><body>' +
            '<div dj-root dj-liveview-root dj-view="test.View">' +
            `<canvas id="c1" dj-hook="Chart" ${canvasAttrs}></canvas>` +
            '</div></body></html>',
        { url: 'http://localhost', runScripts: 'dangerously' }
    );

    if (!dom.window.CSS) dom.window.CSS = {};
    if (!dom.window.CSS.escape) {
        dom.window.CSS.escape = (v) => String(v).replace(/([^\w-])/g, '\\$1');
    }
    // jsdom has no canvas backend; the adapter must only need getContext.
    dom.window.HTMLCanvasElement.prototype.getContext = function () {
        return { canvas: this };
    };

    const errors = [];
    dom.window.console = {
        log: () => {},
        warn: () => {},
        error: (...args) => errors.push(args),
    };

    dom.window.eval(clientCode);
    return { dom, errors };
}

function loadAdapter(dom) {
    const el = dom.window.document.createElement('script');
    el.textContent = readAdapter();
    dom.window.document.body.appendChild(el);
}

describe('dj-chart adapter — registration', () => {
    it('ships as a standalone file in the wheel static dir', () => {
        expect(fs.existsSync(ADAPTER_PATH)).toBe(true);
    });

    it('is NOT bundled into client.js (opt-in only, protects the size budget)', () => {
        expect(clientCode).not.toContain('dj-chart');
    });

    it('registers its JS.ext commands against the real registry', async () => {
        // The adapter guards command registration behind a feature test.
        // If that guard ever silently fails, chart_update/chart_set_data
        // become dead code with no error — pin that they really landed, and
        // that they run through the real executeOps dispatch path (#1196).
        const { dom } = createDom();
        const calls = [];
        dom.window.Chart = makeFakeChartLib(calls);
        loadAdapter(dom);

        // _registry is a Map (26-js-commands.js) — not a plain object.
        expect(dom.window.djust.commands._registry.has('chart_update')).toBe(true);
        expect(dom.window.djust.commands._registry.has('chart_set_data')).toBe(true);

        dom.window.djust.mountHooks(dom.window.document);
        const el = dom.window.document.getElementById('c1');
        const before = calls.filter((c) => c.op === 'update').length;

        await dom.window.djust.js._executeOps([['ext.chart_update', {}]], el);
        expect(calls.filter((c) => c.op === 'update').length).toBe(before + 1);

        await dom.window.djust.js._executeOps(
            [['ext.chart_set_data', { data: { labels: ['z'], datasets: [{ data: [9] }] } }]],
            el
        );
        const last = calls.filter((c) => c.op === 'update').pop();
        expect(last.data.datasets[0].data).toEqual([9]);
    });

    it('registers the Chart hook without clobbering user hooks', () => {
        const { dom } = createDom();
        dom.window.djust.hooks = dom.window.djust.hooks || {};
        dom.window.djust.hooks.UserHook = { mounted() {} };

        loadAdapter(dom);

        expect(typeof dom.window.djust.hooks.Chart).toBe('object');
        expect(typeof dom.window.djust.hooks.UserHook).toBe('object');
    });
});

/**
 * Lifecycle is driven through djust's REAL hook dispatcher
 * (window.djust.mountHooks / updateHooks / destroyAllHooks), not by
 * hand-constructing an instance. Reproduction fidelity: a hand-rolled
 * instance would not exercise _createHookInstance's ADR-025 `values`
 * Proxy, the already-mounted guard, or the destroy sweep — the wiring is
 * most of what can break (#1196).
 */
describe('dj-chart adapter — lifecycle', () => {
    let dom, calls;

    beforeEach(() => {
        const made = createDom(
            `dj-hook-value-type='"bar"' ` +
            `dj-hook-value-data='{"labels":["a"],"datasets":[{"data":[1]}]}'`
        );
        dom = made.dom;
        calls = [];
        dom.window.Chart = makeFakeChartLib(calls);
        loadAdapter(dom);
        // Ignore any mounting that client.js init did on its own; each test
        // asserts on what its own driver call produced.
        calls.length = 0;
    });

    function mount() {
        dom.window.djust.mountHooks(dom.window.document);
    }

    function setData(json) {
        dom.window.document.getElementById('c1').setAttribute(
            'dj-hook-value-data',
            json
        );
    }

    it('mounted() constructs a chart from typed values', () => {
        mount();
        const c = calls.filter((c) => c.op === 'construct');
        expect(c).toHaveLength(1);
        expect(c[0].config.type).toBe('bar');
        expect(c[0].config.data.datasets[0].data).toEqual([1]);
    });

    it('updated() mutates in place — never destroy+reconstruct (#1724)', () => {
        mount();
        setData('{"labels":["a","b"],"datasets":[{"data":[1,2]}]}');
        dom.window.djust.updateHooks(dom.window.document);

        expect(calls.filter((c) => c.op === 'construct')).toHaveLength(1);
        expect(calls.filter((c) => c.op === 'destroy')).toHaveLength(0);
        const updates = calls.filter((c) => c.op === 'update');
        expect(updates.length).toBeGreaterThan(0);
        // The live-Proxy re-read must have reached the instance.
        expect(updates[updates.length - 1].data.datasets[0].data).toEqual([1, 2]);
    });

    it('destroyed() destroys the instance exactly once', () => {
        mount();
        dom.window.djust.destroyAllHooks();
        expect(calls.filter((c) => c.op === 'destroy')).toHaveLength(1);
    });

    it('missing library => clear console.error, no throw', () => {
        // Fresh DOM so the library is absent at mount time.
        const made = createDom(`dj-hook-value-type='"bar"'`);
        loadAdapter(made.dom);
        expect(() => made.dom.window.djust.mountHooks(made.dom.window.document)).not.toThrow();
        const msg = made.errors.flat().join(' ');
        expect(msg).toMatch(/chart/i);
    });

    it('missing library warns ONCE per element, not once per re-render', () => {
        // updated() retries the mount, so without a guard every server
        // re-render would emit another copy of the same error.
        const made = createDom(`dj-hook-value-type='"bar"'`);
        loadAdapter(made.dom);
        made.dom.window.djust.mountHooks(made.dom.window.document);
        const after1 = made.errors.length;
        made.dom.window.djust.updateHooks(made.dom.window.document);
        made.dom.window.djust.updateHooks(made.dom.window.document);
        expect(made.errors.length).toBe(after1);
    });

    it('never sets dj-update="ignore" on the canvas — that would freeze values', () => {
        // dj-update="ignore" makes the patcher return BEFORE attribute sync
        // (12-vdom-patch.js), so dj-hook-value-* could never change and
        // updated() would read stale data forever. The adapter must not add
        // it, and must work with it absent — which the update test above
        // demonstrates, since no test here sets it.
        mount();
        const el = dom.window.document.getElementById('c1');
        expect(el.getAttribute('dj-update')).not.toBe('ignore');

        // And a value change still reaches the instance with it absent.
        setData('{"labels":["q"],"datasets":[{"data":[7]}]}');
        dom.window.djust.updateHooks(dom.window.document);
        const last = calls.filter((c) => c.op === 'update').pop();
        expect(last.data.datasets[0].data).toEqual([7]);
    });
});
