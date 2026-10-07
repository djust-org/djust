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

    // #3355 follow-ups to #3334.
    describe('views and elements dj-model had missed (#3355)', () => {
        // Same capture as createEnv, but each update also records the view the
        // element addressed (sendEvent's fourth argument).
        function slotEnv(bodyHtml) {
            const env = createEnv(bodyHtml);
            const sent = [];
            env.window.djust.liveViewInstance = {
                sendEvent: vi.fn((eventName, params, _target, slotId) => {
                    if (eventName === 'update_model') {
                        sent.push({ field: params.field, value: params.value, slot: slotId });
                    }
                    return true;
                }),
            };
            return { ...env, sent };
        }
        const typeInto = (window, el, value) => {
            el.value = value;
            el.dispatchEvent(new window.Event('input', { bubbles: true }));
        };

        it('keeps a debounced update per view when two views bind the same field name', () => {
            const { window, document, sent } = slotEnv(
                '<div dj-view="app.A" data-djust-target="w1"><input id="a" dj-model.debounce-100="q"></div>' +
                '<div dj-view="app.B" data-djust-target="w2"><input id="b" dj-model.debounce-100="q"></div>');
            vi.useFakeTimers();
            try {
                window.djust.bindModelElements(document);
                typeInto(window, document.getElementById('a'), 'one');
                typeInto(window, document.getElementById('b'), 'two');
                vi.advanceTimersByTime(150);
            } finally {
                vi.useRealTimers();
            }
            expect(sent).toEqual([
                { field: 'q', value: 'one', slot: 'w1' },
                { field: 'q', value: 'two', slot: 'w2' },
            ]);
        });

        it('still collapses rapid edits of one field in one view into the last', () => {
            const { window, document, sent } = slotEnv(
                '<div dj-view="app.A" data-djust-target="w1"><input id="a" dj-model.debounce-100="q"></div>');
            vi.useFakeTimers();
            try {
                window.djust.bindModelElements(document);
                const a = document.getElementById('a');
                typeInto(window, a, 'o');
                typeInto(window, a, 'on');
                typeInto(window, a, 'one');
                vi.advanceTimersByTime(150);
            } finally {
                vi.useRealTimers();
            }
            expect(sent).toEqual([{ field: 'q', value: 'one', slot: 'w1' }]);
        });

        it('sends the text of a contenteditable element', () => {
            const { window, document, modelUpdates } = createEnv(
                '<div id="ed" contenteditable="true" dj-model="body"></div>');
            window.djust.bindModelElements(document);
            const ed = document.getElementById('ed');
            ed.textContent = 'hello world';
            ed.dispatchEvent(new window.Event('input', { bubbles: true }));
            expect(modelUpdates).toEqual([{ field: 'body', value: 'hello world' }]);
        });

        it('treats a bare contenteditable attribute as editable, and contenteditable="false" as not', () => {
            const { window, document, modelUpdates } = createEnv(
                '<div id="yes" contenteditable dj-model="y"></div>' +
                '<div id="no" contenteditable="false" dj-model="n"></div>');
            window.djust.bindModelElements(document);
            document.getElementById('yes').textContent = 'Y';
            document.getElementById('yes').dispatchEvent(new window.Event('input', { bubbles: true }));
            document.getElementById('no').textContent = 'N';
            document.getElementById('no').dispatchEvent(new window.Event('input', { bubbles: true }));
            expect(modelUpdates[0]).toEqual({ field: 'y', value: 'Y' });
            // not editable: not a text value (no contenteditable read)
            expect(modelUpdates[1].value).toBeUndefined();
        });

        it('syncs a .lazy contenteditable element when it loses focus', () => {
            const { window, document, modelUpdates } = createEnv(
                '<div id="ed" contenteditable="true" dj-model.lazy="body"></div>');
            window.djust.bindModelElements(document);
            const ed = document.getElementById('ed');
            ed.textContent = 'draft';
            ed.dispatchEvent(new window.Event('input', { bubbles: true }));
            expect(modelUpdates).toEqual([]);
            ed.dispatchEvent(new window.Event('focusout', { bubbles: true }));
            expect(modelUpdates).toEqual([{ field: 'body', value: 'draft' }]);
        });

        it('binds dj-model.debounce-N on a contenteditable element', () => {
            const { window, document, modelUpdates } = createEnv(
                '<div id="ed" contenteditable="true" dj-model.debounce-50="note"></div>');
            vi.useFakeTimers();
            try {
                window.djust.bindModelElements(document);
                const ed = document.getElementById('ed');
                ed.textContent = 'noted';
                ed.dispatchEvent(new window.Event('input', { bubbles: true }));
                expect(modelUpdates).toEqual([]);
                vi.advanceTimersByTime(80);
            } finally {
                vi.useRealTimers();
            }
            expect(modelUpdates).toEqual([{ field: 'note', value: 'noted' }]);
        });

        it('sends a form control inside a contenteditable container by its own value', () => {
            const { window, document, modelUpdates } = createEnv(
                '<div id="box" contenteditable="true"><input id="inner" type="text" dj-model="note2"></div>');
            window.djust.bindModelElements(document);
            // Chromium reports isContentEditable for a control inside an editable container.
            const inner = document.getElementById('inner');
            Object.defineProperty(inner, 'isContentEditable', { value: true });
            typeInto(window, inner, 'hello');
            expect(modelUpdates).toEqual([{ field: 'note2', value: 'hello' }]);
        });

        it('keeps .lazy on change for a control inside a contenteditable container', () => {
            const { window, document, modelUpdates } = createEnv(
                '<div contenteditable="true"><input id="inner" type="text" dj-model.lazy="n"></div>');
            const inner = document.getElementById('inner');
            Object.defineProperty(inner, 'isContentEditable', { value: true });
            window.djust.bindModelElements(document);
            typeInto(window, inner, 'x');
            expect(modelUpdates).toEqual([]);
            inner.dispatchEvent(new window.Event('change', { bubbles: true }));
            expect(modelUpdates).toEqual([{ field: 'n', value: 'x' }]);
        });

        it('rebinds when a patch turns dj-model into dj-model.debounce-N', async () => {
            const { window, document, modelUpdates } = createEnv(
                '<input id="a" type="text" dj-model="q">');
            window.djust.bindModelElements(document);
            const input = document.getElementById('a');
            input.removeAttribute('dj-model');
            input.setAttribute('dj-model.debounce-50', 'q');
            window.djust.bindModelElements(document);

            typeInto(window, input, 'x');
            expect(modelUpdates).toEqual([]); // now debounced
            await new Promise((resolve) => setTimeout(resolve, 120));
            expect(modelUpdates).toEqual([{ field: 'q', value: 'x' }]);
        });

        it('rebinds when a patch changes the debounce of a surviving input', async () => {
            const { window, document, modelUpdates } = createEnv(
                '<input id="a" type="text" dj-model.debounce-5000="q">');
            window.djust.bindModelElements(document);
            const input = document.getElementById('a');
            input.removeAttribute('dj-model.debounce-5000');
            input.setAttribute('dj-model.debounce-20', 'q');
            window.djust.bindModelElements(document);

            typeInto(window, input, 'y');
            await new Promise((resolve) => setTimeout(resolve, 100));
            expect(modelUpdates).toEqual([{ field: 'q', value: 'y' }]);
        });

        it('rebinds when an unrelated attribute change comes with a new modifier', () => {
            const { window, document, modelUpdates } = createEnv(
                '<input id="a" type="text" dj-model="q" class="x">');
            window.djust.bindModelElements(document);
            const input = document.getElementById('a');
            input.setAttribute('dj-model.lazy', 'q'); // an attribute was added
            window.djust.bindModelElements(document);

            typeInto(window, input, 'z');
            expect(modelUpdates).toEqual([]); // lazy now waits for change
            input.dispatchEvent(new window.Event('change', { bubbles: true }));
            expect(modelUpdates).toEqual([{ field: 'q', value: 'z' }]);
        });

        it('binds a numbered-debounce input that gains the attribute on a surviving element', async () => {
            const { window, document, modelUpdates } = createEnv('<input id="a" type="text">');
            window.djust.bindModelElements(document);
            const input = document.getElementById('a');
            input.setAttribute('dj-model.debounce-20', 'late');
            window.djust.bindModelElements(document);

            typeInto(window, input, 'v');
            await new Promise((resolve) => setTimeout(resolve, 100));
            expect(modelUpdates).toEqual([{ field: 'late', value: 'v' }]);
        });

        // The scan runs after every DOM update. A bound element whose binding
        // attribute is unchanged is skipped on that attribute alone; only an
        // element nothing has claimed is searched by attribute name. (The cost
        // itself is measured by scripts, not here: a timing assertion is flaky.)
        it('does not search a bound element for attribute names again', () => {
            const inputs = [];
            for (let i = 0; i < 40; i++) inputs.push(`<input id="i${i}" type="text" dj-model="f${i}" class="c">`);
            const { window, document } = createEnv(inputs.join(''));
            window.djust.bindModelElements(document);

            const real = window.Element.prototype.getAttributeNames;
            let searched = 0;
            window.Element.prototype.getAttributeNames = function () {
                // jsdom's selector engine calls it too; count the bundle's own calls.
                if (!new Error().stack.includes('node_modules')) searched++;
                return real.call(this);
            };
            try {
                window.djust.bindModelElements(document);
                window.djust.bindModelElements(document);
            } finally {
                window.Element.prototype.getAttributeNames = real;
            }
            expect(searched).toBe(0);
        });

        it('skips nothing it should bind: many inputs, one changed', () => {
            const html = [];
            for (let i = 0; i < 30; i++) html.push(`<input id="i${i}" type="text" dj-model="f${i}">`);
            const { window, document, modelUpdates } = createEnv(html.join(''));
            window.djust.bindModelElements(document);
            document.getElementById('i7').setAttribute('dj-model', 'renamed');
            window.djust.bindModelElements(document);
            typeInto(window, document.getElementById('i7'), 'q');
            typeInto(window, document.getElementById('i8'), 'r');
            expect(modelUpdates).toEqual([
                { field: 'renamed', value: 'q' },
                { field: 'f8', value: 'r' },
            ]);
        });
    });

    describe('exports', () => {
        it('exposes bindModelElements', () => {
            const { window } = createEnv();
            expect(typeof window.djust.bindModelElements).toBe('function');
        });
    });
});
