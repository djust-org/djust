/**
 * Unit tests for debug panel scoped state persistence (Issue #178)
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';

// Replicate the state key logic from 15-panel-controls.js
function stateKey(viewClass) {
    const viewName = viewClass || 'global';
    return `djust-debug-state:${viewName}`;
}

// Replicate saveState logic: only UI preferences
function saveState(state, viewClass) {
    const uiState = {
        isOpen: state.isOpen,
        activeTab: state.activeTab,
    };
    return { key: stateKey(viewClass), value: JSON.stringify(uiState) };
}

// Replicate loadState logic
function loadState(stored, currentState) {
    if (!stored) return currentState;
    try {
        const parsed = JSON.parse(stored);
        return {
            ...currentState,
            isOpen: parsed.isOpen || false,
            activeTab: parsed.activeTab || 'events',
        };
    } catch {
        return currentState;
    }
}

describe('Scoped State Persistence', () => {
    it('generates different keys for different views', () => {
        expect(stateKey('CounterView')).toBe('djust-debug-state:CounterView');
        expect(stateKey('DashboardView')).toBe('djust-debug-state:DashboardView');
    });

    it('falls back to global key when no view class', () => {
        expect(stateKey(null)).toBe('djust-debug-state:global');
        expect(stateKey(undefined)).toBe('djust-debug-state:global');
    });

    it('only saves UI preferences, not filters or data', () => {
        const state = {
            isOpen: true,
            activeTab: 'network',
            searchQuery: 'test',
            filters: { eventName: 'search', eventStatus: 'errors' }
        };
        const { value } = saveState(state, 'MyView');
        const saved = JSON.parse(value);

        expect(saved.isOpen).toBe(true);
        expect(saved.activeTab).toBe('network');
        expect(saved.searchQuery).toBeUndefined();
        expect(saved.filters).toBeUndefined();
    });

    it('restores UI preferences from saved state', () => {
        const stored = JSON.stringify({ isOpen: true, activeTab: 'patches' });
        const defaultState = {
            isOpen: false,
            activeTab: 'events',
            filters: { eventName: '', eventStatus: 'all' }
        };
        const result = loadState(stored, defaultState);

        expect(result.isOpen).toBe(true);
        expect(result.activeTab).toBe('patches');
        expect(result.filters).toEqual({ eventName: '', eventStatus: 'all' });
    });

    it('handles missing saved state gracefully', () => {
        const defaultState = { isOpen: false, activeTab: 'events' };
        const result = loadState(null, defaultState);
        expect(result).toEqual(defaultState);
    });

    it('handles corrupt saved state gracefully', () => {
        const defaultState = { isOpen: false, activeTab: 'events' };
        const result = loadState('not-json', defaultState);
        expect(result).toEqual(defaultState);
    });
});
