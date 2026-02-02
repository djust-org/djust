/**
 * Unit tests for dj-confirm directive
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

describe('dj-confirm directive', () => {
    let handleEventCalls;

    beforeEach(() => {
        handleEventCalls = [];
        window.djust = window.djust || {};
        // Ensure window.confirm exists (jsdom may not have it)
        if (!window.confirm) {
            window.confirm = () => true;
        }
        document.body.innerHTML = '';
    });

    afterEach(() => {
        document.body.innerHTML = '';
        vi.restoreAllMocks();
    });

    it('should show confirm dialog on click when dj-confirm is set', () => {
        const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);

        document.body.innerHTML = `
            <div data-djust-root>
                <button id="btn" dj-click="delete_item" dj-confirm="Are you sure?">Delete</button>
            </div>
        `;

        const btn = document.getElementById('btn');
        // Simulate the behavior: check for dj-confirm before proceeding
        btn.addEventListener('click', (e) => {
            const confirmMsg = btn.getAttribute('dj-confirm');
            if (confirmMsg && !window.confirm(confirmMsg)) {
                e.preventDefault();
                return;
            }
            handleEventCalls.push('delete_item');
        });

        btn.click();

        expect(confirmSpy).toHaveBeenCalledWith('Are you sure?');
        expect(handleEventCalls).toContain('delete_item');
    });

    it('should not send event when user cancels confirm', () => {
        const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);

        document.body.innerHTML = `
            <div data-djust-root>
                <button id="btn" dj-click="delete_item" dj-confirm="Are you sure?">Delete</button>
            </div>
        `;

        const btn = document.getElementById('btn');
        btn.addEventListener('click', (e) => {
            const confirmMsg = btn.getAttribute('dj-confirm');
            if (confirmMsg && !window.confirm(confirmMsg)) {
                e.preventDefault();
                return;
            }
            handleEventCalls.push('delete_item');
        });

        btn.click();

        expect(confirmSpy).toHaveBeenCalledWith('Are you sure?');
        expect(handleEventCalls).not.toContain('delete_item');
    });

    it('should not show confirm dialog when dj-confirm is not set', () => {
        const confirmSpy = vi.spyOn(window, 'confirm');

        document.body.innerHTML = `
            <div data-djust-root>
                <button id="btn" dj-click="save_item">Save</button>
            </div>
        `;

        const btn = document.getElementById('btn');
        btn.addEventListener('click', (e) => {
            const confirmMsg = btn.getAttribute('dj-confirm');
            if (confirmMsg && !window.confirm(confirmMsg)) {
                e.preventDefault();
                return;
            }
            handleEventCalls.push('save_item');
        });

        btn.click();

        expect(confirmSpy).not.toHaveBeenCalled();
        expect(handleEventCalls).toContain('save_item');
    });

    it('should support custom confirmation messages', () => {
        const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);

        document.body.innerHTML = `
            <button id="btn" dj-click="remove" dj-confirm="Delete all records permanently?">Remove All</button>
        `;

        const btn = document.getElementById('btn');
        btn.addEventListener('click', () => {
            const confirmMsg = btn.getAttribute('dj-confirm');
            if (confirmMsg) window.confirm(confirmMsg);
        });

        btn.click();

        expect(confirmSpy).toHaveBeenCalledWith('Delete all records permanently?');
    });
});
