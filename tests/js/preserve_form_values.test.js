/**
 * Tests for preserveFormValues() — saves/restores form element values
 * across innerHTML replacement (which destroys .value properties).
 *
 * Regression: textarea content was lost on html_update because innerHTML
 * only sets the DOM attribute, not the JS .value property.
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { JSDOM } from 'jsdom';

// Load the client module
const fs = await import('fs');
const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

// Execute in JSDOM environment
const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', {
    runScripts: 'dangerously',
});

// Polyfill CSS.escape (not available in JSDOM)
if (!dom.window.CSS) {
    dom.window.CSS = {};
}
if (!dom.window.CSS.escape) {
    dom.window.CSS.escape = function(value) {
        return String(value).replace(/([^\w-])/g, '\\$1');
    };
}

// Execute the client code
dom.window.eval(clientCode);

const { preserveFormValues } = dom.window.djust;

describe('preserveFormValues', () => {
    let document;

    beforeEach(() => {
        document = dom.window.document;
    });

    afterEach(() => {
        document.body.innerHTML = '';
    });

    describe('textarea .value syncing from textContent', () => {
        it('should sync textarea .value from textContent after innerHTML replacement', () => {
            const container = document.createElement('div');
            container.innerHTML = '<textarea id="editor">initial</textarea>';
            document.body.appendChild(container);

            // Simulate innerHTML replacement with new content
            preserveFormValues(container, () => {
                container.innerHTML = '<textarea id="editor">updated\ncontent</textarea>';
            });

            const textarea = container.querySelector('textarea');
            // .value should be synced from textContent
            expect(textarea.value).toBe('updated\ncontent');
        });

        it('should preserve newlines in textarea content through innerHTML', () => {
            const container = document.createElement('div');
            container.innerHTML = '<textarea name="doc">line1\nline2\nline3</textarea>';
            document.body.appendChild(container);

            const newContent = 'first\n\nsecond\n\n\nthird';
            preserveFormValues(container, () => {
                container.innerHTML = `<textarea name="doc">${newContent}</textarea>`;
            });

            const textarea = container.querySelector('textarea');
            expect(textarea.value).toBe(newContent);
            expect(textarea.value).toContain('\n');
            expect((textarea.value.match(/\n/g) || []).length).toBe(5);
        });

        it('should handle multiple textareas', () => {
            const container = document.createElement('div');
            container.innerHTML = '<textarea id="a">aaa</textarea><textarea id="b">bbb</textarea>';
            document.body.appendChild(container);

            preserveFormValues(container, () => {
                container.innerHTML = '<textarea id="a">new-a\nline</textarea><textarea id="b">new-b\nline</textarea>';
            });

            expect(container.querySelector('#a').value).toBe('new-a\nline');
            expect(container.querySelector('#b').value).toBe('new-b\nline');
        });
    });

    describe('focused textarea value preservation', () => {
        it('should restore focused textarea value over textContent sync', () => {
            const container = document.createElement('div');
            container.innerHTML = '<textarea id="editor">server content</textarea>';
            document.body.appendChild(container);

            const textarea = container.querySelector('textarea');
            // User has typed something different from server state
            textarea.value = 'user is typing this\nwith newlines';
            textarea.focus();

            preserveFormValues(container, () => {
                // Server sends back slightly different content
                container.innerHTML = '<textarea id="editor">server content updated</textarea>';
            });

            const restored = container.querySelector('#editor');
            // Should have the USER's value, not the server's
            expect(restored.value).toBe('user is typing this\nwith newlines');
        });

        it('should match by name attribute when no id', () => {
            const container = document.createElement('div');
            container.innerHTML = '<textarea name="content">old</textarea>';
            document.body.appendChild(container);

            const textarea = container.querySelector('textarea');
            textarea.value = 'user value';
            textarea.focus();

            preserveFormValues(container, () => {
                container.innerHTML = '<textarea name="content">server</textarea>';
            });

            expect(container.querySelector('[name="content"]').value).toBe('user value');
        });

        it('should match by positional index when no id or name', () => {
            const container = document.createElement('div');
            container.innerHTML = '<textarea>first</textarea><textarea>second</textarea>';
            document.body.appendChild(container);

            const textareas = container.querySelectorAll('textarea');
            textareas[1].value = 'user edited second';
            textareas[1].focus();

            preserveFormValues(container, () => {
                container.innerHTML = '<textarea>first-new</textarea><textarea>second-new</textarea>';
            });

            const newTextareas = container.querySelectorAll('textarea');
            // First should have server value (not focused)
            expect(newTextareas[0].value).toBe('first-new');
            // Second should have user value (was focused)
            expect(newTextareas[1].value).toBe('user edited second');
        });
    });

    describe('input element handling', () => {
        it('should preserve focused text input value', () => {
            const container = document.createElement('div');
            container.innerHTML = '<input id="search" type="text" value="old">';
            document.body.appendChild(container);

            const input = container.querySelector('#search');
            input.value = 'user query';
            input.focus();

            preserveFormValues(container, () => {
                container.innerHTML = '<input id="search" type="text" value="server">';
            });

            expect(container.querySelector('#search').value).toBe('user query');
        });

        it('should preserve focused checkbox state', () => {
            const container = document.createElement('div');
            container.innerHTML = '<input id="agree" type="checkbox">';
            document.body.appendChild(container);

            const checkbox = container.querySelector('#agree');
            checkbox.checked = true;
            checkbox.focus();

            preserveFormValues(container, () => {
                container.innerHTML = '<input id="agree" type="checkbox">';
            });

            expect(container.querySelector('#agree').checked).toBe(true);
        });
    });

    describe('no focused element', () => {
        it('should still sync textarea .value from textContent', () => {
            const container = document.createElement('div');
            container.innerHTML = '<textarea id="t">old</textarea>';
            document.body.appendChild(container);

            // Don't focus anything — blur
            document.body.focus();

            preserveFormValues(container, () => {
                container.innerHTML = '<textarea id="t">new\nlines</textarea>';
            });

            // Even without focus, textarea .value should be synced from textContent
            expect(container.querySelector('#t').value).toBe('new\nlines');
        });
    });

    describe('edge cases', () => {
        it('should handle empty container', () => {
            const container = document.createElement('div');
            document.body.appendChild(container);

            expect(() => {
                preserveFormValues(container, () => {
                    container.innerHTML = '<textarea>new</textarea>';
                });
            }).not.toThrow();
        });

        it('should handle textarea with empty content', () => {
            const container = document.createElement('div');
            container.innerHTML = '<textarea id="t"></textarea>';
            document.body.appendChild(container);

            preserveFormValues(container, () => {
                container.innerHTML = '<textarea id="t"></textarea>';
            });

            expect(container.querySelector('#t').value).toBe('');
        });

        it('should handle textarea removed after replacement', () => {
            const container = document.createElement('div');
            container.innerHTML = '<textarea id="t">old</textarea>';
            document.body.appendChild(container);

            const textarea = container.querySelector('textarea');
            textarea.value = 'user typed';
            textarea.focus();

            // Server removes the textarea entirely
            preserveFormValues(container, () => {
                container.innerHTML = '<div>no textarea anymore</div>';
            });

            // Should not throw, textarea is just gone
            expect(container.querySelector('textarea')).toBeNull();
        });
    });
});
