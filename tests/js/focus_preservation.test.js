/**
 * Tests for focus preservation across VDOM patches.
 *
 * When VDOM patches modify the DOM, the focused element's focus state,
 * selection range, and scroll position must be preserved.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';

const fs = await import('fs');
const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', {
    runScripts: 'dangerously',
});

if (!dom.window.CSS) dom.window.CSS = {};
if (!dom.window.CSS.escape) {
    dom.window.CSS.escape = function(value) {
        return String(value).replace(/([^\w-])/g, '\\$1');
    };
}

dom.window.eval(clientCode);

const {
    saveFocusState,
    restoreFocusState,
} = dom.window.djust;

const document = dom.window.document;

describe('Focus preservation across VDOM patches', () => {

    beforeEach(() => {
        document.body.innerHTML = '';
    });

    describe('saveFocusState', () => {
        it('returns null when no element is focused', () => {
            document.body.focus();
            const state = saveFocusState();
            expect(state).toBeNull();
        });

        it('saves state for a focused input by id', () => {
            const input = document.createElement('input');
            input.id = 'search';
            input.type = 'text';
            input.value = 'hello world';
            document.body.appendChild(input);
            input.focus();

            const state = saveFocusState();

            expect(state).not.toBeNull();
            expect(state.tag).toBe('INPUT');
            expect(state.findBy).toBe('id');
            expect(state.key).toBe('search');
        });

        it('saves state for a focused input by name', () => {
            const input = document.createElement('input');
            input.name = 'email';
            input.type = 'text';
            document.body.appendChild(input);
            input.focus();

            const state = saveFocusState();

            expect(state.findBy).toBe('name');
            expect(state.key).toBe('email');
        });

        it('saves selection range for text inputs', () => {
            const input = document.createElement('input');
            input.id = 'name';
            input.type = 'text';
            input.value = 'hello world';
            document.body.appendChild(input);
            input.focus();
            input.setSelectionRange(3, 7);

            const state = saveFocusState();

            expect(state.selStart).toBe(3);
            expect(state.selEnd).toBe(7);
        });

        it('saves selection range for textareas', () => {
            const ta = document.createElement('textarea');
            ta.id = 'content';
            ta.value = 'line one\nline two\nline three';
            document.body.appendChild(ta);
            ta.focus();
            ta.setSelectionRange(5, 15);

            const state = saveFocusState();

            expect(state.tag).toBe('TEXTAREA');
            expect(state.selStart).toBe(5);
            expect(state.selEnd).toBe(15);
        });

        it('returns null for non-form elements', () => {
            const div = document.createElement('div');
            div.tabIndex = 0;
            document.body.appendChild(div);
            div.focus();

            const state = saveFocusState();
            expect(state).toBeNull();
        });

        it('saves state for select elements', () => {
            const select = document.createElement('select');
            select.id = 'color';
            const opt = document.createElement('option');
            opt.value = 'red';
            select.appendChild(opt);
            document.body.appendChild(select);
            select.focus();

            const state = saveFocusState();

            expect(state.tag).toBe('SELECT');
            expect(state.findBy).toBe('id');
        });

        it('falls back to dj-id when no id or name', () => {
            const input = document.createElement('input');
            input.setAttribute('dj-id', 'abc123');
            input.type = 'text';
            document.body.appendChild(input);
            input.focus();

            const state = saveFocusState();

            expect(state.findBy).toBe('dj-id');
            expect(state.key).toBe('abc123');
        });

        it('falls back to positional index as last resort', () => {
            const input1 = document.createElement('input');
            input1.type = 'text';
            const input2 = document.createElement('input');
            input2.type = 'text';
            document.body.appendChild(input1);
            document.body.appendChild(input2);
            input2.focus();

            const state = saveFocusState();

            expect(state.findBy).toBe('index');
            expect(state.key).toBe(1);
        });
    });

    describe('restoreFocusState', () => {
        it('does nothing when state is null', () => {
            restoreFocusState(null);
            // No error thrown
        });

        it('restores focus to element by id', () => {
            const input = document.createElement('input');
            input.id = 'search';
            input.type = 'text';
            document.body.appendChild(input);

            restoreFocusState({ tag: 'INPUT', findBy: 'id', key: 'search' });

            expect(document.activeElement).toBe(input);
        });

        it('restores focus to element by name', () => {
            const input = document.createElement('input');
            input.name = 'email';
            input.type = 'text';
            document.body.appendChild(input);

            restoreFocusState({ tag: 'INPUT', findBy: 'name', key: 'email' });

            expect(document.activeElement).toBe(input);
        });

        it('restores focus to element by dj-id', () => {
            const input = document.createElement('input');
            input.setAttribute('dj-id', 'xyz');
            input.type = 'text';
            document.body.appendChild(input);

            restoreFocusState({ tag: 'INPUT', findBy: 'dj-id', key: 'xyz' });

            expect(document.activeElement).toBe(input);
        });

        it('restores selection range', () => {
            const input = document.createElement('input');
            input.id = 'name';
            input.type = 'text';
            input.value = 'hello world';
            document.body.appendChild(input);

            restoreFocusState({
                tag: 'INPUT', findBy: 'id', key: 'name',
                selStart: 3, selEnd: 7,
            });

            expect(document.activeElement).toBe(input);
            expect(input.selectionStart).toBe(3);
            expect(input.selectionEnd).toBe(7);
        });

        it('restores scroll position for textareas', () => {
            const ta = document.createElement('textarea');
            ta.id = 'content';
            ta.style.height = '50px';
            ta.value = 'a\n'.repeat(100);
            document.body.appendChild(ta);

            restoreFocusState({
                tag: 'TEXTAREA', findBy: 'id', key: 'content',
                selStart: 10, selEnd: 10,
                scrollTop: 200, scrollLeft: 0,
            });

            expect(document.activeElement).toBe(ta);
            expect(ta.scrollTop).toBe(200);
        });

        it('does not throw when element is not found', () => {
            // Element was removed from DOM — restoreFocusState should silently no-op
            expect(() => {
                restoreFocusState({ tag: 'INPUT', findBy: 'id', key: 'nonexistent' });
            }).not.toThrow();
        });
    });

    describe('save + restore roundtrip', () => {
        it('preserves focus through a simulated Replace patch', () => {
            // Setup: input is focused with cursor at position 5
            const input = document.createElement('input');
            input.id = 'search';
            input.type = 'text';
            input.value = 'hello world';
            document.body.appendChild(input);
            input.focus();
            input.setSelectionRange(5, 5);

            // Save state before DOM mutation
            const state = saveFocusState();

            // Simulate Replace patch: old element removed, new one inserted
            const newInput = document.createElement('input');
            newInput.id = 'search';
            newInput.type = 'text';
            newInput.value = 'hello world';
            document.body.replaceChild(newInput, input);

            // Focus is now lost (old element was removed)
            expect(document.activeElement).not.toBe(newInput);

            // Restore focus
            restoreFocusState(state);

            // Focus and cursor position restored on the NEW element
            expect(document.activeElement).toBe(newInput);
            expect(newInput.selectionStart).toBe(5);
            expect(newInput.selectionEnd).toBe(5);
        });

        it('preserves textarea selection through sibling DOM changes', () => {
            const container = document.createElement('div');
            const ta = document.createElement('textarea');
            ta.id = 'editor';
            ta.value = 'function foo() {\n  return bar;\n}';
            container.appendChild(ta);
            document.body.appendChild(container);
            ta.focus();
            ta.setSelectionRange(17, 27); // selects "return bar"

            const state = saveFocusState();

            // Simulate a sibling being added (common VDOM patch scenario)
            const sibling = document.createElement('div');
            sibling.textContent = 'Status: saved';
            container.insertBefore(sibling, ta);

            // Textarea still in DOM but let's restore anyway
            restoreFocusState(state);

            expect(document.activeElement).toBe(ta);
            expect(ta.selectionStart).toBe(17);
            expect(ta.selectionEnd).toBe(27);
        });
    });
});
