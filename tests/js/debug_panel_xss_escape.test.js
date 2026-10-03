/**
 * Debug panel + client-dev.js output escaping.
 *
 * The panel builds tab HTML as strings and assigns them to innerHTML, so every
 * interpolated value that originates from application data (another user's
 * input, a repr() of view state, a server error message, an imported session
 * file) must be escaped. Each test pushes a markup payload and a
 * quote-breaking attribute payload through the real tab render + the real
 * innerHTML sink (switchTab -> renderTabContent) and asserts:
 *   - no element was created from the payload (no <img>, no on* attribute),
 *   - no handler ran (window.__pwned stays unset),
 *   - the payload is still visible as literal text.
 */

import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { createPanel } from './helpers/debug-panel-harness.js';

const IMG = '<img src=x onerror=window.__pwned=1>';
const ATTR = '" onmouseover="window.__pwned=1';

function content(panel) {
    return panel.panel.querySelector('.djust-tab-content');
}

function assertInert(root, ...literals) {
    // Fire the events a browser would, so a live handler would run.
    root.querySelectorAll('img').forEach((i) => i.dispatchEvent(new window.Event('error')));
    expect(root.querySelectorAll('img')).toHaveLength(0);
    expect(root.querySelectorAll('[onerror],[onmouseover],[onclick*="__pwned"]')).toHaveLength(0);
    // Nothing besides the panel's own markup got an injected attribute.
    for (const el of root.querySelectorAll('*')) {
        for (const attr of el.attributes) {
            expect(attr.name.startsWith('on') && String(attr.value).includes('__pwned')).toBe(false);
        }
    }
    expect(window.__pwned).toBeUndefined();
    for (const lit of literals) {
        expect(root.textContent).toContain(lit);
    }
}

describe('debug panel escapes interpolated values', () => {
    let panel;

    beforeEach(() => {
        delete window.__pwned;
        panel = createPanel();
        panel.open?.();
    });

    afterEach(() => {
        delete window.__pwned;
        vi.restoreAllMocks();
    });

    it('patches tab escapes patch type, path and value', () => {
        panel.patchHistory = [{
            count: IMG,
            timestamp: Date.now(),
            patches: [
                { type: IMG, path: ATTR + IMG, value: IMG },
                { type: 'SetText', path: '0', value: { k: IMG } },
            ],
        }];
        panel.switchTab('patches');
        assertInert(content(panel), IMG, ATTR);
    });

    it('events tab escapes handler, params, result, error and element fields', () => {
        panel.eventHistory = [{
            handler: IMG,
            timestamp: Date.now(),
            params: { a: IMG, b: ATTR },
            result: { r: IMG },
            error: IMG + ATTR,
            element: {
                tagName: IMG,
                id: ATTR + IMG,
                className: ATTR + IMG,
                text: IMG,
                attributes: { [ATTR]: IMG, 'data-x': ATTR },
            },
        }];
        panel.switchTab('events');
        assertInert(content(panel), IMG, ATTR);
    });

    it('network tab escapes message type, direction and payload JSON', () => {
        panel.networkHistory = [{
            direction: ATTR + IMG,
            type: IMG,
            payload: { type: IMG, text: IMG, other: ATTR },
            size: 10,
            timestamp: Date.now(),
        }];
        panel.switchTab('network');
        assertInert(content(panel), IMG);
    });

    it('components tab escapes name, type and state', () => {
        panel.components = {
            name: IMG,
            type: ATTR + IMG,
            state: { a: IMG },
            children: [{ name: IMG, type: IMG, state: { b: IMG } }],
        };
        panel.switchTab('components');
        assertInert(content(panel), IMG, ATTR);
        // renderComponentTree (list form) too
        const box = document.createElement('div');
        box.innerHTML = panel.renderComponentTree([{ name: IMG, type: ATTR + IMG }]);
        assertInert(box, IMG, ATTR);
    });

    it('handlers tab escapes name, description, params and source_file', () => {
        panel.handlers = [{
            name: IMG,
            description: IMG,
            decorators: [IMG],
            parameters: [{ name: IMG, type: ATTR, default: IMG, required: true }],
            source_file: IMG,
            source_line: IMG,
        }];
        panel.switchTab('handlers');
        assertInert(content(panel), IMG, ATTR);
    });

    it('variables tab escapes name, value and type', () => {
        panel.variables = {
            [IMG]: { type: IMG, value: IMG, size_bytes: 10 },
            safe: { type: ATTR, value: ATTR + IMG, size_bytes: 5 },
        };
        panel.switchTab('variables');
        assertInert(content(panel), IMG, ATTR);
    });

    it('warnings (patches tab summary + performance tree) escape message, node, recommendations, docs_url', () => {
        panel.patchHistory = [{
            count: 1,
            timestamp: Date.now(),
            patches: [{ type: 'SetText', path: '0', value: 'x' }],
            performance: {
                timing: {
                    name: IMG,
                    duration_ms: 1,
                    metadata: { query_count: IMG, memory: { delta_mb: IMG } },
                    warnings: [{
                        type: IMG,
                        message: IMG,
                        recommendation: IMG,
                        docs_url: 'javascript:window.__pwned=1',
                        recommendations: [{ title: IMG, description: IMG, priority: ATTR, code_example: IMG }],
                        query_count: IMG,
                    }],
                    children: [{ name: IMG, warnings: [{ type: 'slow_handler', message: ATTR + IMG, docs_url: ATTR }] }],
                },
            },
        }];
        panel.switchTab('patches');
        const root = content(panel);
        assertInert(root, IMG);
        for (const a of root.querySelectorAll('a[href]')) {
            expect(a.getAttribute('href').toLowerCase().startsWith('javascript:')).toBe(false);
        }
    });

    it('element badge escapes id, className and text in title and label', () => {
        const box = document.createElement('div');
        box.innerHTML = panel.renderElementBadge({
            tagName: 'DIV',
            id: ATTR + IMG,
            className: ATTR + IMG,
            text: ATTR + IMG,
        });
        const badge = box.querySelector('.element-badge');
        expect(badge).toBeTruthy();
        expect(badge.getAttribute('title')).toContain(ATTR);
        expect(badge.getAttribute('title')).toContain(IMG);
        assertInert(box, IMG);

        const box2 = document.createElement('div');
        box2.innerHTML = panel.renderElementBadge({ tagName: 'DIV', id: '', className: ATTR + ' x', text: '' });
        assertInert(box2);
        expect(box2.querySelector('.element-badge').getAttribute('title')).toContain(ATTR);
    });

    it('escapeHtml escapes quotes and does not double-escape raw input', () => {
        expect(panel.escapeHtml(`<a href="x" t='y'>&</a>`)).toBe(
            '&lt;a href=&quot;x&quot; t=&#39;y&#39;&gt;&amp;&lt;/a&gt;'
        );
        expect(panel.escapeHtml(null)).toBe('');
        expect(panel.escapeHtml(5)).toBe('5');
    });

    it('imported session file flows into the same escaped sinks', async () => {
        const session = {
            events: [{ handler: IMG, timestamp: 1, params: { p: IMG }, error: IMG,
                       element: { tagName: 'DIV', id: ATTR, className: 'c', text: IMG, attributes: {} } }],
            network: [{ direction: 'sent', payload: { type: IMG }, size: 1, timestamp: 1 }],
            patches: [{ count: 1, timestamp: 1, patches: [{ type: IMG, path: ATTR, value: IMG }] }],
        };
        vi.spyOn(window.HTMLInputElement.prototype, 'click').mockImplementation(function () {
            this.onchange({ target: { files: [new window.File([JSON.stringify(session)], 's.json')] } });
        });
        panel.import();
        await vi.waitFor(() => expect(panel.patchHistory).toHaveLength(1));
        for (const tab of ['patches', 'events', 'network']) {
            panel.switchTab(tab);
            assertInert(content(panel), IMG);
        }
    });
});

describe('client-dev.js toast escapes the message', () => {
    const src = readFileSync(
        resolve(dirname(fileURLToPath(import.meta.url)), '../../python/djust/static/djust/client-dev.js'),
        'utf-8'
    );

    afterEach(() => {
        delete window.__pwned;
        document.getElementById('djust-toast-container')?.remove();
    });

    it('renders a djust:error message as text, not markup', () => {
        vi.stubGlobal('requestAnimationFrame', (fn) => fn());
        new Function(src).call(window);
        for (const msg of [IMG, ATTR + IMG]) {
            window.dispatchEvent(new window.CustomEvent('djust:error', { detail: { error: msg, event: 'e' } }));
        }
        const container = document.getElementById('djust-toast-container');
        expect(container).toBeTruthy();
        container.querySelectorAll('img').forEach((i) => i.dispatchEvent(new window.Event('error')));
        expect(container.querySelectorAll('img')).toHaveLength(0);
        expect(container.querySelectorAll('[onerror],[onmouseover]')).toHaveLength(0);
        expect(window.__pwned).toBeUndefined();
        expect(container.textContent).toContain(IMG);
        expect(container.textContent).toContain(ATTR);
    });
});
