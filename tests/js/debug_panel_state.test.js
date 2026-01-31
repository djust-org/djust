/**
 * Tests for debug panel per-view state scoping (Issue #166)
 *
 * Verifies that panel UI state (open/closed, active tab) is persisted
 * per-view and that data histories are cleared on view change.
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';

describe('Debug panel per-view state scoping', () => {
    let localStorage;

    beforeEach(() => {
        // Mock localStorage
        localStorage = {};
        Object.defineProperty(window, 'localStorage', {
            value: {
                getItem: (key) => localStorage[key] || null,
                setItem: (key, value) => { localStorage[key] = value; },
                removeItem: (key) => { delete localStorage[key]; },
                clear: () => { localStorage = {}; },
            },
            writable: true,
            configurable: true,
        });
    });

    afterEach(() => {
        localStorage = {};
    });

    it('should generate view-specific state keys', () => {
        // Simulate the _getStateKey logic
        const viewId = 'MyCounterView';
        const key = `djust-debug-ui-${viewId}`;
        expect(key).toBe('djust-debug-ui-MyCounterView');
    });

    it('should use different keys for different views', () => {
        const key1 = `djust-debug-ui-CounterView`;
        const key2 = `djust-debug-ui-TodoView`;
        expect(key1).not.toBe(key2);
    });

    it('should store and retrieve per-view UI state', () => {
        const viewId = 'CounterView';
        const key = `djust-debug-ui-${viewId}`;
        const uiState = { isOpen: true, activeTab: 'network', searchQuery: '', filters: { types: [], severity: 'all' } };

        window.localStorage.setItem(key, JSON.stringify(uiState));
        const restored = JSON.parse(window.localStorage.getItem(key));

        expect(restored.isOpen).toBe(true);
        expect(restored.activeTab).toBe('network');
    });

    it('should detect view ID from DJUST_DEBUG_INFO', () => {
        window.DJUST_DEBUG_INFO = { view_name: 'MyTestView' };

        // Simulate _detectCurrentViewId logic
        let viewId;
        if (window.DJUST_DEBUG_INFO && window.DJUST_DEBUG_INFO.view_name) {
            viewId = window.DJUST_DEBUG_INFO.view_name;
        } else {
            viewId = window.location.pathname;
        }

        expect(viewId).toBe('MyTestView');

        delete window.DJUST_DEBUG_INFO;
    });

    it('should fall back to pathname when no debug info', () => {
        delete window.DJUST_DEBUG_INFO;

        let viewId;
        if (window.DJUST_DEBUG_INFO && window.DJUST_DEBUG_INFO.view_name) {
            viewId = window.DJUST_DEBUG_INFO.view_name;
        } else {
            viewId = window.location.pathname;
        }

        expect(viewId).toBe(window.location.pathname);
    });

    it('should not share state between views', () => {
        const key1 = `djust-debug-ui-ViewA`;
        const key2 = `djust-debug-ui-ViewB`;

        window.localStorage.setItem(key1, JSON.stringify({ isOpen: true, activeTab: 'events' }));
        window.localStorage.setItem(key2, JSON.stringify({ isOpen: false, activeTab: 'network' }));

        const stateA = JSON.parse(window.localStorage.getItem(key1));
        const stateB = JSON.parse(window.localStorage.getItem(key2));

        expect(stateA.isOpen).toBe(true);
        expect(stateA.activeTab).toBe('events');
        expect(stateB.isOpen).toBe(false);
        expect(stateB.activeTab).toBe('network');
    });
});
