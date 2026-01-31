/**
 * Unit tests for debug panel event filtering (Issue #176)
 *
 * Since the debug panel is an IIFE, we test the filtering logic
 * by replicating the filter function used in renderEventsTab().
 */

import { describe, it, expect } from 'vitest';

// Replicate the filter logic from 03-tab-events.js
function filterEvents(events, nameFilter, statusFilter) {
    const name = (nameFilter || '').toLowerCase();
    const status = statusFilter || 'all';
    return events.filter(event => {
        const eventName = (event.handler || event.name || 'unknown').toLowerCase();
        if (name && !eventName.includes(name)) return false;
        if (status === 'errors' && !event.error) return false;
        if (status === 'success' && event.error) return false;
        return true;
    });
}

const sampleEvents = [
    { handler: 'increment', timestamp: Date.now(), params: { amount: 1 } },
    { handler: 'decrement', timestamp: Date.now() },
    { handler: 'search', timestamp: Date.now(), error: 'Not found' },
    { handler: 'fetch_data', timestamp: Date.now(), params: { id: 5 } },
    { name: 'click_handler', timestamp: Date.now(), error: 'Timeout' },
];

describe('Event Filter Logic', () => {
    it('returns all events when no filters active', () => {
        const result = filterEvents(sampleEvents, '', 'all');
        expect(result).toHaveLength(5);
    });

    it('filters by name substring (case-insensitive)', () => {
        const result = filterEvents(sampleEvents, 'inc', 'all');
        expect(result).toHaveLength(1);
        expect(result[0].handler).toBe('increment');
    });

    it('filters by name substring matching multiple events', () => {
        const result = filterEvents(sampleEvents, 'cre', 'all');
        // increment, decrement both contain 'cre'
        expect(result).toHaveLength(2);
    });

    it('filters errors only', () => {
        const result = filterEvents(sampleEvents, '', 'errors');
        expect(result).toHaveLength(2);
        result.forEach(e => expect(e.error).toBeTruthy());
    });

    it('filters success only', () => {
        const result = filterEvents(sampleEvents, '', 'success');
        expect(result).toHaveLength(3);
        result.forEach(e => expect(e.error).toBeFalsy());
    });

    it('combines name and status filters', () => {
        const result = filterEvents(sampleEvents, 'search', 'errors');
        expect(result).toHaveLength(1);
        expect(result[0].handler).toBe('search');
    });

    it('returns empty when no match', () => {
        const result = filterEvents(sampleEvents, 'nonexistent', 'all');
        expect(result).toHaveLength(0);
    });

    it('falls back to "unknown" for events without handler or name', () => {
        const events = [{ timestamp: Date.now() }];
        const result = filterEvents(events, 'unknown', 'all');
        expect(result).toHaveLength(1);
    });

    it('handles empty event list', () => {
        const result = filterEvents([], 'test', 'errors');
        expect(result).toHaveLength(0);
    });
});
