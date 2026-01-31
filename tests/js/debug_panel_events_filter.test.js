/**
 * Tests for debug panel Events tab filtering (Issue #170)
 *
 * Verifies event name filtering, status filtering, and filter clearing.
 */

import { describe, it, expect } from 'vitest';

// Simulate the filtering logic from renderEventsTab
function filterEvents(events, nameFilter, statusFilter) {
    const name = (nameFilter || '').toLowerCase();
    const status = statusFilter || 'all';

    return events.filter(event => {
        const eventName = (event.handler || event.name || '').toLowerCase();
        if (name && !eventName.includes(name)) return false;
        if (status === 'errors' && !event.error) return false;
        if (status === 'success' && event.error) return false;
        return true;
    });
}

const mockEvents = [
    { handler: 'increment', name: 'increment', error: null, params: { amount: 1 }, timestamp: 1 },
    { handler: 'decrement', name: 'decrement', error: null, params: {}, timestamp: 2 },
    { handler: 'submit_form', name: 'submit_form', error: 'Validation failed', params: { data: 'x' }, timestamp: 3 },
    { handler: 'increment', name: 'increment', error: 'Overflow', params: { amount: 999 }, timestamp: 4 },
    { handler: 'reset', name: 'reset', error: null, params: {}, timestamp: 5 },
];

describe('Event name filtering', () => {
    it('should show all events when filter is empty', () => {
        const result = filterEvents(mockEvents, '', 'all');
        expect(result.length).toBe(5);
    });

    it('should filter by substring match', () => {
        const result = filterEvents(mockEvents, 'incr', 'all');
        expect(result.length).toBe(2);
        expect(result.every(e => e.handler === 'increment')).toBe(true);
    });

    it('should be case-insensitive', () => {
        const result = filterEvents(mockEvents, 'INCREMENT', 'all');
        expect(result.length).toBe(2);
    });

    it('should return empty when no match', () => {
        const result = filterEvents(mockEvents, 'nonexistent', 'all');
        expect(result.length).toBe(0);
    });
});

describe('Event status filtering', () => {
    it('should show only errors', () => {
        const result = filterEvents(mockEvents, '', 'errors');
        expect(result.length).toBe(2);
        expect(result.every(e => e.error !== null)).toBe(true);
    });

    it('should show only successes', () => {
        const result = filterEvents(mockEvents, '', 'success');
        expect(result.length).toBe(3);
        expect(result.every(e => e.error === null)).toBe(true);
    });

    it('should show all with "all" filter', () => {
        const result = filterEvents(mockEvents, '', 'all');
        expect(result.length).toBe(5);
    });
});

describe('Combined filters', () => {
    it('should combine name and status filters', () => {
        const result = filterEvents(mockEvents, 'increment', 'errors');
        expect(result.length).toBe(1);
        expect(result[0].error).toBe('Overflow');
    });

    it('should return empty when combined filters eliminate all', () => {
        const result = filterEvents(mockEvents, 'reset', 'errors');
        expect(result.length).toBe(0);
    });
});

describe('Filter state detection', () => {
    it('should detect active filters', () => {
        const nameFilter = 'incr';
        const statusFilter = 'all';
        const hasActiveFilters = nameFilter || statusFilter !== 'all';
        expect(hasActiveFilters).toBe('incr'); // truthy
    });

    it('should detect no active filters', () => {
        const nameFilter = '';
        const statusFilter = 'all';
        const hasActiveFilters = nameFilter || statusFilter !== 'all';
        expect(hasActiveFilters).toBeFalsy();
    });
});
