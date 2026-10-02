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
            <div dj-root>
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

    // _sendModelUpdate tries window.djust.liveViewInstance.sendEvent() first (fast
    // synchronous path), then falls back to handleEvent() for HTTP-only mode.
    // Mock liveViewInstance to capture model update calls.
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

    // #3334: bindModelElements() ran only at init. A dj-model input inserted
    // afterwards (a patch, a live_redirect morph, a lazily hydrated view) was
    // never bound and sent nothing.
    describe('after init (reinitAfterDOMUpdate)', () => {
        const type = (window, input, value) => {
            input.value = value;
            input.dispatchEvent(new window.Event('input', { bubbles: true }));
        };

        // Let djustInit (queued as a microtask by client.js) run, then put the
        // capturing instance back: init installs a real one.
        async function initialized(bodyHtml) {
            const env = createEnv(bodyHtml);
            await new Promise((resolve) => setTimeout(resolve, 20));
            env.window.djust.liveViewInstance = {
                sendEvent: vi.fn((eventName, params) => {
                    if (eventName === 'update_model') {
                        env.modelUpdates.push({ field: params.field, value: params.value });
                    }
                    return true;
                }),
            };
            return env;
        }

        it('binds a dj-model input inserted after init', async () => {
            const { window, document, modelUpdates } = await initialized('<p id="slot"></p>');
            const input = document.createElement('input');
            input.type = 'text';
            input.setAttribute('dj-model', 'late');
            document.getElementById('slot').appendChild(input);

            window.djust.reinitAfterDOMUpdate();

            type(window, input, 'hello');
            expect(modelUpdates).toEqual([{ field: 'late', value: 'hello' }]);
        });

        it('binds modifier forms (.lazy, .debounce-N) inserted after init', async () => {
            const { window, document, modelUpdates } = await initialized('<p id="slot"></p>');
            document.getElementById('slot').innerHTML =
                '<input id="lz" type="text" dj-model.lazy="lazy_field">' +
                '<input id="db" type="text" dj-model.debounce-30="deb_field">';

            window.djust.reinitAfterDOMUpdate();

            const lazy = document.getElementById('lz');
            type(window, lazy, 'x');
            expect(modelUpdates).toEqual([]); // lazy waits for change
            lazy.dispatchEvent(new window.Event('change', { bubbles: true }));
            expect(modelUpdates).toEqual([{ field: 'lazy_field', value: 'x' }]);

            type(window, document.getElementById('db'), 'y');
            await new Promise((resolve) => setTimeout(resolve, 80));
            expect(modelUpdates).toContainEqual({ field: 'deb_field', value: 'y' });
        });

        it('binds inside the scope it is given (a lazily hydrated view)', async () => {
            const { window, document, modelUpdates } = await initialized(
                '<div id="lazy" dj-view="app.W"></div>');
            const lazy = document.getElementById('lazy');
            lazy.innerHTML = '<input id="in" type="text" dj-model="scoped">';

            window.djust.reinitAfterDOMUpdate(lazy);

            type(window, document.getElementById('in'), 'v');
            expect(modelUpdates).toEqual([{ field: 'scoped', value: 'v' }]);
        });

        it('binds each input once however many times it re-initializes', async () => {
            const { window, document, modelUpdates } = await initialized(
                '<input id="a" type="text" dj-model="a">' +
                '<input id="b" type="checkbox" dj-model="b">');
            const late = document.createElement('input');
            late.id = 'c';
            late.setAttribute('dj-model', 'c');
            document.body.appendChild(late);

            for (let i = 0; i < 5; i++) window.djust.reinitAfterDOMUpdate();

            // One listener per input: the input event fires one update, not five.
            type(window, document.getElementById('a'), '1');
            type(window, late, '2');
            const checkbox = document.getElementById('b');
            checkbox.checked = true;
            checkbox.dispatchEvent(new window.Event('change', { bubbles: true }));
            expect(modelUpdates.map((u) => u.field)).toEqual(['a', 'c', 'b']);
        });

        it('keeps the guard off the DOM, so a morph that strips attributes cannot double-bind', async () => {
            const { window, document, modelUpdates } = await initialized(
                '<input id="a" type="text" dj-model="a" data-x="1">');
            const input = document.getElementById('a');
            window.djust.reinitAfterDOMUpdate();
            // A morph rewrites the element's attributes to the server's markup.
            for (const attr of Array.from(input.attributes)) {
                if (attr.name !== 'dj-model' && attr.name !== 'id' && attr.name !== 'type') {
                    input.removeAttribute(attr.name);
                }
            }
            expect(Array.from(input.attributes).some((a) => /bound/i.test(a.name))).toBe(false);
            window.djust.reinitAfterDOMUpdate();
            type(window, input, 'once');
            expect(modelUpdates).toEqual([{ field: 'a', value: 'once' }]);
        });

        it('rebinds when a patch changes the field of a surviving input', async () => {
            const { window, document, modelUpdates } = await initialized(
                '<input id="a" type="text" dj-model="old_field">');
            const input = document.getElementById('a');
            input.setAttribute('dj-model', 'new_field');

            window.djust.reinitAfterDOMUpdate();

            type(window, input, 'z');
            expect(modelUpdates).toEqual([{ field: 'new_field', value: 'z' }]);
        });

        it('sends nothing when a server patch only sets the value', async () => {
            const { window, document, modelUpdates } = await initialized(
                '<input id="a" type="text" dj-model="a" value="">');
            const input = document.getElementById('a');
            input.value = 'from the server'; // a patch writes the value; no input event
            window.djust.reinitAfterDOMUpdate();
            window.djust.reinitAfterDOMUpdate();
            expect(modelUpdates).toEqual([]);
            expect(input.value).toBe('from the server');
        });
    });

    describe('exports', () => {
        it('exposes bindModelElements', () => {
            const { window } = createEnv();
            expect(typeof window.djust.bindModelElements).toBe('function');
        });
    });
});
