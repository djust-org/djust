/**
 * Unit tests for @loading attribute support
 */

import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import {
    LoadingManager,
    globalLoadingManager,
    clearAllState
} from '../../python/djust/static/djust/decorators.js';

describe('LoadingManager', () => {
    let manager;
    let mockElement;

    beforeEach(() => {
        manager = new LoadingManager();

        // Create mock DOM element
        document.body.innerHTML = `
            <button id="save-btn" dj-loading.disable>Save</button>
            <button id="submit-btn" dj-loading.class="opacity-50">Submit</button>
            <div id="spinner" dj-loading.show style="display: none;">Loading...</div>
            <div id="content" dj-loading.hide>Form Content</div>
        `;

        mockElement = document.getElementById('save-btn');
    });

    afterEach(() => {
        document.body.innerHTML = '';
    });

    // ========================================================================
    // Registration
    // ========================================================================

    describe('register', () => {
        it('should register element with dj-loading.disable', () => {
            const btn = document.getElementById('save-btn');
            manager.register(btn, 'save');

            expect(manager.loadingElements.has(btn)).toBe(true);
            const config = manager.loadingElements.get(btn);
            expect(config.eventName).toBe('save');
            expect(config.modifiers).toContainEqual({ type: 'disable' });
        });

        it('should register element with dj-loading.class', () => {
            const btn = document.getElementById('submit-btn');
            btn.setAttribute('dj-loading.class', 'opacity-50');
            manager.register(btn, 'submit');

            const config = manager.loadingElements.get(btn);
            expect(config.modifiers).toContainEqual({ type: 'class', value: 'opacity-50' });
        });

        it('should register element with dj-loading.show', () => {
            const spinner = document.getElementById('spinner');
            manager.register(spinner, 'save');

            const config = manager.loadingElements.get(spinner);
            expect(config.modifiers).toContainEqual({ type: 'show' });
        });

        it('should register element with dj-loading.hide', () => {
            const content = document.getElementById('content');
            manager.register(content, 'save');

            const config = manager.loadingElements.get(content);
            expect(config.modifiers).toContainEqual({ type: 'hide' });
        });

        it('should save original state', () => {
            const btn = document.getElementById('save-btn');
            btn.disabled = true;
            manager.register(btn, 'save');

            const config = manager.loadingElements.get(btn);
            expect(config.originalState.disabled).toBe(true);
        });

        it('should not register element without dj-loading attributes', () => {
            const btn = document.createElement('button');
            manager.register(btn, 'save');

            expect(manager.loadingElements.has(btn)).toBe(false);
        });

        it('should register multiple modifiers on same element', () => {
            const btn = document.createElement('button');
            btn.setAttribute('dj-loading.disable', '');
            btn.setAttribute('dj-loading.class', 'loading');
            manager.register(btn, 'save');

            const config = manager.loadingElements.get(btn);
            expect(config.modifiers.length).toBeGreaterThan(1);
        });
    });

    // ========================================================================
    // Start/Stop Loading
    // ========================================================================

    describe('startLoading', () => {
        it('should mark event as pending', () => {
            manager.startLoading('save');

            expect(manager.pendingEvents.has('save')).toBe(true);
            expect(manager.isLoading('save')).toBe(true);
        });

        it('should apply loading state to registered elements', () => {
            const btn = document.getElementById('save-btn');
            manager.register(btn, 'save');

            expect(btn.disabled).toBe(false);

            manager.startLoading('save');

            expect(btn.disabled).toBe(true);
        });

        it('should not affect unrelated elements', () => {
            const btn1 = document.getElementById('save-btn');
            const btn2 = document.getElementById('submit-btn');

            manager.register(btn1, 'save');
            manager.register(btn2, 'submit');

            btn2.disabled = false;

            manager.startLoading('save');

            expect(btn1.disabled).toBe(true);
            expect(btn2.disabled).toBe(false);
        });

        it('should show element with dj-loading.show', () => {
            const spinner = document.getElementById('spinner');
            manager.register(spinner, 'save');

            spinner.style.display = 'none';

            manager.startLoading('save');

            expect(spinner.style.display).toBe('block');
        });

        it('should hide element with dj-loading.hide', () => {
            const content = document.getElementById('content');
            manager.register(content, 'save');

            content.style.display = 'block';

            manager.startLoading('save');

            expect(content.style.display).toBe('none');
        });

        it('should add class with dj-loading.class', () => {
            const btn = document.getElementById('submit-btn');
            btn.setAttribute('dj-loading.class', 'opacity-50');
            manager.register(btn, 'submit');

            expect(btn.classList.contains('opacity-50')).toBe(false);

            manager.startLoading('submit');

            expect(btn.classList.contains('opacity-50')).toBe(true);
        });
    });

    describe('stopLoading', () => {
        it('should remove event from pending', () => {
            manager.startLoading('save');
            expect(manager.isLoading('save')).toBe(true);

            manager.stopLoading('save');

            expect(manager.isLoading('save')).toBe(false);
            expect(manager.pendingEvents.has('save')).toBe(false);
        });

        it('should restore original disabled state', () => {
            const btn = document.getElementById('save-btn');
            btn.disabled = false;
            manager.register(btn, 'save');

            manager.startLoading('save');
            expect(btn.disabled).toBe(true);

            manager.stopLoading('save');
            expect(btn.disabled).toBe(false);
        });

        it('should restore original display state', () => {
            const spinner = document.getElementById('spinner');
            const originalDisplay = spinner.style.display;
            manager.register(spinner, 'save');

            manager.startLoading('save');
            expect(spinner.style.display).toBe('block');

            manager.stopLoading('save');
            expect(spinner.style.display).toBe(originalDisplay);
        });

        it('should remove class with dj-loading.class', () => {
            const btn = document.getElementById('submit-btn');
            btn.setAttribute('dj-loading.class', 'opacity-50');
            manager.register(btn, 'submit');

            manager.startLoading('submit');
            expect(btn.classList.contains('opacity-50')).toBe(true);

            manager.stopLoading('submit');
            expect(btn.classList.contains('opacity-50')).toBe(false);
        });

        it('should handle stopping non-started event gracefully', () => {
            expect(() => manager.stopLoading('nonexistent')).not.toThrow();
        });
    });

    // ========================================================================
    // Multiple Events
    // ========================================================================

    describe('multiple events', () => {
        it('should track multiple pending events independently', () => {
            manager.startLoading('save');
            manager.startLoading('submit');

            expect(manager.isLoading('save')).toBe(true);
            expect(manager.isLoading('submit')).toBe(true);

            manager.stopLoading('save');

            expect(manager.isLoading('save')).toBe(false);
            expect(manager.isLoading('submit')).toBe(true);
        });

        it('should apply loading to all elements watching same event', () => {
            const btn1 = document.getElementById('save-btn');
            const spinner = document.getElementById('spinner');

            manager.register(btn1, 'save');
            manager.register(spinner, 'save');

            manager.startLoading('save');

            expect(btn1.disabled).toBe(true);
            expect(spinner.style.display).toBe('block');

            manager.stopLoading('save');

            expect(btn1.disabled).toBe(false);
        });
    });

    // ========================================================================
    // Clear
    // ========================================================================

    describe('clear', () => {
        it('should clear all loading elements', () => {
            const btn = document.getElementById('save-btn');
            manager.register(btn, 'save');

            expect(manager.loadingElements.size).toBeGreaterThan(0);

            manager.clear();

            expect(manager.loadingElements.size).toBe(0);
        });

        it('should clear all pending events', () => {
            manager.startLoading('save');
            manager.startLoading('submit');

            expect(manager.pendingEvents.size).toBe(2);

            manager.clear();

            expect(manager.pendingEvents.size).toBe(0);
        });
    });

    // ========================================================================
    // Custom Display Value
    // ========================================================================

    describe('custom display value', () => {
        it('should use data-loading-display attribute if provided', () => {
            const spinner = document.getElementById('spinner');
            spinner.setAttribute('data-loading-display', 'inline-block');
            manager.register(spinner, 'save');

            manager.startLoading('save');

            expect(spinner.style.display).toBe('inline-block');
        });

        it('should default to "block" if no data-loading-display', () => {
            const spinner = document.getElementById('spinner');
            manager.register(spinner, 'save');

            manager.startLoading('save');

            expect(spinner.style.display).toBe('block');
        });
    });

    // ========================================================================
    // Error Handling
    // ========================================================================

    describe('error handling', () => {
        it('should handle element with no style gracefully', () => {
            const div = document.createElement('div');
            div.setAttribute('dj-loading.show', '');
            manager.register(div, 'save');

            expect(() => manager.startLoading('save')).not.toThrow();
            expect(() => manager.stopLoading('save')).not.toThrow();
        });

        it('should handle element removal gracefully', () => {
            const btn = document.getElementById('save-btn');
            manager.register(btn, 'save');

            btn.remove();

            // Should not throw when element is removed from DOM
            expect(() => manager.startLoading('save')).not.toThrow();
            expect(() => manager.stopLoading('save')).not.toThrow();
        });
    });
});

describe('globalLoadingManager', () => {
    beforeEach(() => {
        clearAllState();
    });

    it('should be a LoadingManager instance', () => {
        expect(globalLoadingManager).toBeInstanceOf(LoadingManager);
    });

    it('should be cleared by clearAllState', () => {
        const btn = document.createElement('button');
        btn.setAttribute('dj-loading.disable', '');
        globalLoadingManager.register(btn, 'save');
        globalLoadingManager.startLoading('save');

        expect(globalLoadingManager.loadingElements.size).toBeGreaterThan(0);
        expect(globalLoadingManager.pendingEvents.size).toBeGreaterThan(0);

        clearAllState();

        expect(globalLoadingManager.loadingElements.size).toBe(0);
        expect(globalLoadingManager.pendingEvents.size).toBe(0);
    });
});

describe('Integration scenarios', () => {
    beforeEach(() => {
        clearAllState();
        document.body.innerHTML = `
            <form>
                <button id="save" dj-loading.disable dj-loading.class="loading">Save</button>
                <button id="cancel" dj-loading.hide>Cancel</button>
                <div id="spinner" dj-loading.show style="display: none;">Saving...</div>
                <div id="form" dj-loading.hide>Form fields</div>
            </form>
        `;
    });

    afterEach(() => {
        document.body.innerHTML = '';
    });

    it('should handle complete save workflow', () => {
        const saveBtn = document.getElementById('save');
        const cancelBtn = document.getElementById('cancel');
        const spinner = document.getElementById('spinner');
        const form = document.getElementById('form');

        // Register all elements
        globalLoadingManager.register(saveBtn, 'save');
        globalLoadingManager.register(cancelBtn, 'save');
        globalLoadingManager.register(spinner, 'save');
        globalLoadingManager.register(form, 'save');

        // Initial state
        expect(saveBtn.disabled).toBe(false);
        expect(saveBtn.classList.contains('loading')).toBe(false);
        expect(spinner.style.display).toBe('none');
        expect(form.style.display).not.toBe('none');

        // Start save
        globalLoadingManager.startLoading('save');

        // Loading state
        expect(saveBtn.disabled).toBe(true);
        expect(saveBtn.classList.contains('loading')).toBe(true);
        expect(spinner.style.display).toBe('block');
        expect(form.style.display).toBe('none');
        expect(cancelBtn.style.display).toBe('none');

        // Complete save
        globalLoadingManager.stopLoading('save');

        // Restored state
        expect(saveBtn.disabled).toBe(false);
        expect(saveBtn.classList.contains('loading')).toBe(false);
        expect(spinner.style.display).toBe('none');
    });

    it('should handle multiple simultaneous operations', () => {
        const btn1 = document.getElementById('save');
        const btn2 = document.getElementById('cancel');

        btn1.setAttribute('dj-loading.disable', '');
        btn2.setAttribute('dj-loading.disable', '');

        globalLoadingManager.register(btn1, 'save');
        globalLoadingManager.register(btn2, 'delete');

        // Start both
        globalLoadingManager.startLoading('save');
        globalLoadingManager.startLoading('delete');

        expect(btn1.disabled).toBe(true);
        expect(btn2.disabled).toBe(true);

        // Stop one
        globalLoadingManager.stopLoading('save');

        expect(btn1.disabled).toBe(false);
        expect(btn2.disabled).toBe(true);

        // Stop other
        globalLoadingManager.stopLoading('delete');

        expect(btn2.disabled).toBe(false);
    });
});
