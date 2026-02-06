/**
 * Tests for dj-model -- two-way data binding (src/20-model-binding.js)
 */

import { describe, it, expect, vi } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createEnv(bodyHtml = '') {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><body>
            <div data-djust-root>
                ${bodyHtml}
            </div>
        </body></html>`,
        { url: 'http://localhost:8000/test/', runScripts: 'dangerously', pretendToBeVisual: true }
    );
    const { window } = dom;

    // Track model update calls
    const modelUpdates = [];

    // Suppress console
    window.console = { log: () => {}, error: () => {}, warn: () => {}, debug: () => {}, info: () => {} };

    try {
        window.eval(clientCode);
    } catch (e) {
        // client.js may throw on missing DOM APIs
    }

    // _sendModelUpdate (outside the double-load guard) uses window.djust.liveViewInstance.
    // It first checks `typeof handleEvent === 'function'` which is false (handleEvent is scoped
    // inside the guard), then falls back to inst.sendEvent(). Mock liveViewInstance to capture calls.
    window.djust.liveViewInstance = {
        sendEvent: vi.fn((eventName, params) => {
            if (eventName === 'update_model') {
                modelUpdates.push({ field: params.field, value: params.value });
            }
            return true;
        }),
    };

    return { window, dom, document: dom.window.document, modelUpdates };
}

describe('model_binding', () => {
    describe('bindModelElements', () => {
        it('binds text input on input event', () => {
            const { window, document, modelUpdates } = createEnv(
                '<input type="text" dj-model="search_query" value="" />'
            );

            window.djust.bindModelElements(document);

            const input = document.querySelector('input');
            input.value = 'hello';
            input.dispatchEvent(new window.Event('input', { bubbles: true }));

            expect(modelUpdates.length).toBe(1);
            expect(modelUpdates[0].field).toBe('search_query');
            expect(modelUpdates[0].value).toBe('hello');
        });

        it('binds textarea', () => {
            const { window, document, modelUpdates } = createEnv(
                '<textarea dj-model="description"></textarea>'
            );

            window.djust.bindModelElements(document);

            const textarea = document.querySelector('textarea');
            textarea.value = 'some text';
            textarea.dispatchEvent(new window.Event('input', { bubbles: true }));

            expect(modelUpdates.length).toBe(1);
            expect(modelUpdates[0].field).toBe('description');
            expect(modelUpdates[0].value).toBe('some text');
        });

        it('binds select', () => {
            const { window, document, modelUpdates } = createEnv(`
                <select dj-model="category">
                    <option value="a">A</option>
                    <option value="b">B</option>
                </select>
            `);

            window.djust.bindModelElements(document);

            const select = document.querySelector('select');
            select.value = 'b';
            select.dispatchEvent(new window.Event('input', { bubbles: true }));

            expect(modelUpdates.length).toBe(1);
            expect(modelUpdates[0].field).toBe('category');
            expect(modelUpdates[0].value).toBe('b');
        });

        it('binds checkbox using .checked', () => {
            const { window, document, modelUpdates } = createEnv(
                '<input type="checkbox" dj-model="is_active" />'
            );

            window.djust.bindModelElements(document);

            const checkbox = document.querySelector('input[type="checkbox"]');
            checkbox.checked = true;
            checkbox.dispatchEvent(new window.Event('change', { bubbles: true }));

            expect(modelUpdates.length).toBeGreaterThanOrEqual(1);
            const update = modelUpdates.find(u => u.field === 'is_active');
            expect(update).toBeDefined();
            expect(update.value).toBe(true);
        });

        it('binds radio buttons', () => {
            const { window, document, modelUpdates } = createEnv(`
                <input type="radio" name="color" value="red" dj-model="color" />
                <input type="radio" name="color" value="blue" dj-model="color" />
            `);

            window.djust.bindModelElements(document);

            const blue = document.querySelector('input[value="blue"]');
            blue.checked = true;
            blue.dispatchEvent(new window.Event('change', { bubbles: true }));

            expect(modelUpdates.length).toBeGreaterThanOrEqual(1);
            const update = modelUpdates.find(u => u.field === 'color');
            expect(update).toBeDefined();
            expect(update.value).toBe('blue');
        });

        it('.lazy modifier listens on change event', () => {
            const { window, document, modelUpdates } = createEnv(
                '<input type="text" dj-model.lazy="name" value="" />'
            );

            window.djust.bindModelElements(document);

            const input = document.querySelector('input');
            input.value = 'typed text';

            // 'input' event should NOT trigger update for lazy
            input.dispatchEvent(new window.Event('input', { bubbles: true }));
            expect(modelUpdates.length).toBe(0);

            // 'change' event SHOULD trigger
            input.dispatchEvent(new window.Event('change', { bubbles: true }));

            expect(modelUpdates.length).toBe(1);
            expect(modelUpdates[0].field).toBe('name');
        });

        it('.debounce modifier delays sending', () => {
            const { window, document, modelUpdates } = createEnv(
                '<input type="text" dj-model.debounce-100="query" value="" />'
            );

            vi.useFakeTimers();

            window.djust.bindModelElements(document);

            const input = document.querySelector('input');
            input.value = 'a';
            input.dispatchEvent(new window.Event('input', { bubbles: true }));

            // Should not have fired yet (debounced)
            expect(modelUpdates.length).toBe(0);

            // Advance past debounce timer
            vi.advanceTimersByTime(150);

            expect(modelUpdates.length).toBe(1);
            expect(modelUpdates[0].field).toBe('query');
            expect(modelUpdates[0].value).toBe('a');

            vi.useRealTimers();
        });

        it('skips already bound elements', () => {
            const { window, document, modelUpdates } = createEnv(
                '<input type="text" dj-model="field" value="" />'
            );

            window.djust.bindModelElements(document);
            window.djust.bindModelElements(document);

            const input = document.querySelector('input');
            input.value = 'test';
            input.dispatchEvent(new window.Event('input', { bubbles: true }));

            // Should fire only once even though bindModelElements was called twice
            expect(modelUpdates.length).toBe(1);
        });

        it('multiple inputs do not interfere', () => {
            const { window, document, modelUpdates } = createEnv(`
                <input type="text" dj-model="first" value="" />
                <input type="text" dj-model="second" value="" />
            `);

            window.djust.bindModelElements(document);

            const inputs = document.querySelectorAll('input');
            inputs[0].value = 'aaa';
            inputs[0].dispatchEvent(new window.Event('input', { bubbles: true }));

            inputs[1].value = 'bbb';
            inputs[1].dispatchEvent(new window.Event('input', { bubbles: true }));

            expect(modelUpdates.length).toBe(2);
            expect(modelUpdates[0].field).toBe('first');
            expect(modelUpdates[1].field).toBe('second');
        });
    });

    describe('exports', () => {
        it('exposes bindModelElements', () => {
            const { window } = createEnv();
            expect(typeof window.djust.bindModelElements).toBe('function');
        });
    });
});
