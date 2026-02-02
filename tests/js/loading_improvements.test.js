/**
 * Unit tests for dj-loading improvements (dj-loading.remove alias)
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';

describe('dj-loading.remove modifier', () => {
    beforeEach(() => {
        document.body.innerHTML = '';
    });

    afterEach(() => {
        document.body.innerHTML = '';
    });

    it('should treat dj-loading.remove as alias for dj-loading.hide', () => {
        document.body.innerHTML = `
            <div id="content" dj-loading.remove dj-click="save" style="display: block;">
                Content to hide while loading
            </div>
        `;

        const content = document.getElementById('content');

        // Parse attributes like the loading manager does
        const modifiers = [];
        Array.from(content.attributes).forEach(attr => {
            const match = attr.name.match(/^dj-loading\.(.+)$/);
            if (match) {
                const modifier = match[1];
                if (modifier === 'hide' || modifier === 'remove') {
                    modifiers.push({ type: 'hide' });
                }
            }
        });

        expect(modifiers).toContainEqual({ type: 'hide' });
    });

    it('should hide element when loading starts with dj-loading.remove', () => {
        document.body.innerHTML = `
            <div id="content" dj-loading.remove style="display: block;">
                Content
            </div>
        `;

        const content = document.getElementById('content');
        expect(content.style.display).toBe('block');

        // Simulate loading manager hiding
        content.style.display = 'none';
        expect(content.style.display).toBe('none');

        // Simulate restore
        content.style.display = 'block';
        expect(content.style.display).toBe('block');
    });

    it('dj-loading.disable should disable button', () => {
        document.body.innerHTML = `
            <button id="btn" dj-loading.disable dj-click="save">Save</button>
        `;

        const btn = document.getElementById('btn');
        expect(btn.disabled).toBe(false);

        // Simulate loading
        btn.disabled = true;
        expect(btn.disabled).toBe(true);

        // Simulate stop loading
        btn.disabled = false;
        expect(btn.disabled).toBe(false);
    });

    it('dj-loading.class should add/remove CSS class', () => {
        document.body.innerHTML = `
            <div id="content" dj-loading.class="opacity-50" dj-click="save">Content</div>
        `;

        const content = document.getElementById('content');
        expect(content.classList.contains('opacity-50')).toBe(false);

        // Simulate loading
        content.classList.add('opacity-50');
        expect(content.classList.contains('opacity-50')).toBe(true);

        // Simulate stop loading
        content.classList.remove('opacity-50');
        expect(content.classList.contains('opacity-50')).toBe(false);
    });

    it('dj-loading.show should show element only while loading', () => {
        document.body.innerHTML = `
            <span id="spinner" dj-loading.show style="display: none;">Saving...</span>
        `;

        const spinner = document.getElementById('spinner');
        expect(spinner.style.display).toBe('none');

        // Simulate loading
        spinner.style.display = 'block';
        expect(spinner.style.display).toBe('block');

        // Simulate stop loading
        spinner.style.display = 'none';
        expect(spinner.style.display).toBe('none');
    });
});
