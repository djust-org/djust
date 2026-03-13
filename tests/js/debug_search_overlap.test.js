/**
 * Tests for overlapping search query and name/type filters in the debug panel.
 * Covers issue #532 (events AND semantics), #530 (network filtered count),
 * and #520 (state tab search).
 *
 * Since the debug panel is an IIFE, filter logic is replicated here
 * from the corresponding source files.
 */

import { describe, it, expect } from 'vitest';

// ---------------------------------------------------------------------------
// Replicated from 03-tab-events.js
// ---------------------------------------------------------------------------
function filterEvents(events, nameFilter, statusFilter, searchQuery) {
    const name = (nameFilter || '').toLowerCase();
    const status = statusFilter || 'all';
    const query = (searchQuery || '').toLowerCase();

    return events.filter(event => {
        const eventName = (event.handler || event.name || 'unknown').toLowerCase();
        if (name && !eventName.includes(name)) return false;
        if (status === 'errors' && !event.error) return false;
        if (status === 'success' && event.error) return false;
        if (query) {
            const errorStr = (event.error || '').toLowerCase();
            const paramsStr = event.params ? JSON.stringify(event.params).toLowerCase() : '';
            if (!eventName.includes(query) && !errorStr.includes(query) && !paramsStr.includes(query)) {
                return false;
            }
        }
        return true;
    });
}

// ---------------------------------------------------------------------------
// Replicated from 04-tab-network.js
// ---------------------------------------------------------------------------
function filterMessages(messages, searchQuery) {
    const query = (searchQuery || '').toLowerCase();
    if (!query) return messages;
    return messages.filter(msg => {
        const payload = msg.data || msg.payload;
        const type = msg.type || (payload ? (payload.type || payload.event || 'data') : 'unknown');
        const payloadStr = payload ? JSON.stringify(payload).toLowerCase() : '';
        return type.toLowerCase().includes(query) ||
               (msg.direction || '').toLowerCase().includes(query) ||
               payloadStr.includes(query);
    });
}

function networkCountLabel(messages, searchQuery) {
    const filtered = filterMessages(messages, searchQuery);
    const query = (searchQuery || '').toLowerCase();
    return (query && filtered.length !== messages.length)
        ? `${filtered.length} / ${messages.length}`
        : String(messages.length);
}

// ---------------------------------------------------------------------------
// Replicated from 07-tab-state.js
// ---------------------------------------------------------------------------
function filterStateHistory(stateHistory, searchQuery) {
    const query = (searchQuery || '').toLowerCase();
    if (!query) return stateHistory;
    return stateHistory.filter(entry => {
        const trigger = (entry.trigger || '').toLowerCase();
        const eventName = (entry.eventName || '').toLowerCase();
        const stateStr = JSON.stringify(entry.state || {}).toLowerCase();
        return trigger.includes(query) || eventName.includes(query) || stateStr.includes(query);
    });
}

// ---------------------------------------------------------------------------
// Test data
// ---------------------------------------------------------------------------
const events = [
    { handler: 'search_products', timestamp: 1, params: { query: 'shoes' } },
    { handler: 'search_products', timestamp: 2, error: 'timeout', params: { query: 'hats' } },
    { handler: 'add_to_cart', timestamp: 3, params: { product_id: 42 } },
    { handler: 'add_to_cart', timestamp: 4, error: 'out of stock' },
    { handler: 'checkout', timestamp: 5 },
];

const messages = [
    { direction: 'sent', type: 'event', payload: { event: 'click' } },
    { direction: 'received', type: 'patch', payload: { ops: [] } },
    { direction: 'sent', type: 'heartbeat', data: null },
    { direction: 'received', type: 'event', payload: { event: 'update', data: 'foobar' } },
];

const stateHistory = [
    { trigger: 'event', eventName: 'search_products', state: { query: 'shoes', results: [] }, timestamp: 1 },
    { trigger: 'event', eventName: 'add_to_cart', state: { cart: [42], total: 9.99 }, timestamp: 2 },
    { trigger: 'mount', eventName: null, state: { query: '', cart: [] }, timestamp: 3 },
];

// ---------------------------------------------------------------------------
// Issue #532: overlapping nameFilter + searchQuery (events tab)
// ---------------------------------------------------------------------------
describe('Events tab — overlapping nameFilter AND searchQuery (#532)', () => {
    it('nameFilter alone narrows by handler name', () => {
        const result = filterEvents(events, 'search', 'all', '');
        expect(result).toHaveLength(2);
        result.forEach(e => expect(e.handler).toContain('search'));
    });

    it('searchQuery alone narrows by params content', () => {
        const result = filterEvents(events, '', 'all', 'shoes');
        expect(result).toHaveLength(1);
        expect(result[0].params.query).toBe('shoes');
    });

    it('nameFilter AND searchQuery apply AND semantics', () => {
        // nameFilter='search' matches 2; searchQuery='shoes' further narrows to 1
        const result = filterEvents(events, 'search', 'all', 'shoes');
        expect(result).toHaveLength(1);
        expect(result[0].handler).toBe('search_products');
        expect(result[0].params.query).toBe('shoes');
    });

    it('nameFilter + statusFilter + searchQuery — all three active', () => {
        // handler contains 'search', errors only, error message contains 'timeout'
        const result = filterEvents(events, 'search', 'errors', 'timeout');
        expect(result).toHaveLength(1);
        expect(result[0].error).toBe('timeout');
    });

    it('returns empty when nameFilter matches but searchQuery does not', () => {
        const result = filterEvents(events, 'search', 'all', 'nonexistent_payload_value');
        expect(result).toHaveLength(0);
    });

    it('returns empty when searchQuery matches but nameFilter does not', () => {
        const result = filterEvents(events, 'checkout', 'all', 'shoes');
        expect(result).toHaveLength(0);
    });

    it('searchQuery matches error text when nameFilter also active', () => {
        const result = filterEvents(events, 'add_to_cart', 'all', 'out of stock');
        expect(result).toHaveLength(1);
        expect(result[0].error).toBe('out of stock');
    });
});

// ---------------------------------------------------------------------------
// Issue #530: network tab filtered count label
// ---------------------------------------------------------------------------
describe('Network tab — filtered count label (#530)', () => {
    it('shows total count when no search query', () => {
        expect(networkCountLabel(messages, '')).toBe('4');
    });

    it('shows total count when search matches all messages', () => {
        // 'sent' or 'received' matches direction; 'event' matches type for 2 messages
        // but '' query returns all
        expect(networkCountLabel(messages, '')).toBe('4');
    });

    it('shows "filtered / total" format when query narrows results', () => {
        // 'heartbeat' matches only 1 message
        expect(networkCountLabel(messages, 'heartbeat')).toBe('1 / 4');
    });

    it('shows "filtered / total" when partial match', () => {
        // 'patch' matches 1 message
        expect(networkCountLabel(messages, 'patch')).toBe('1 / 4');
    });

    it('shows total (no slash) when query matches all', () => {
        // every message has a direction, so 'sent' only matches 2; but 'e' matches all (event, received, etc.)
        const all = filterMessages(messages, 'e');
        expect(all.length).toBe(messages.length);
        expect(networkCountLabel(messages, 'e')).toBe('4');
    });

    it('shows "0 / total" when nothing matches', () => {
        expect(networkCountLabel(messages, 'zzznomatch')).toBe('0 / 4');
    });

    it('filters by direction', () => {
        const result = filterMessages(messages, 'sent');
        expect(result).toHaveLength(2);
    });

    it('filters by payload content', () => {
        const result = filterMessages(messages, 'foobar');
        expect(result).toHaveLength(1);
    });
});

// ---------------------------------------------------------------------------
// Issue #520: state tab search filtering
// ---------------------------------------------------------------------------
describe('State tab — search filtering (#520)', () => {
    it('returns all entries when no search query', () => {
        expect(filterStateHistory(stateHistory, '')).toHaveLength(3);
    });

    it('filters by eventName', () => {
        const result = filterStateHistory(stateHistory, 'search_products');
        expect(result).toHaveLength(1);
        expect(result[0].eventName).toBe('search_products');
    });

    it('filters by trigger', () => {
        const result = filterStateHistory(stateHistory, 'mount');
        expect(result).toHaveLength(1);
        expect(result[0].trigger).toBe('mount');
    });

    it('filters by state value content', () => {
        const result = filterStateHistory(stateHistory, '9.99');
        expect(result).toHaveLength(1);
        expect(result[0].eventName).toBe('add_to_cart');
    });

    it('is case-insensitive', () => {
        const result = filterStateHistory(stateHistory, 'ADD_TO_CART');
        expect(result).toHaveLength(1);
    });

    it('returns empty when nothing matches', () => {
        const result = filterStateHistory(stateHistory, 'zzznomatch');
        expect(result).toHaveLength(0);
    });

    it('partial match on state key name', () => {
        // 'cart' appears in state for add_to_cart AND mount entries
        const result = filterStateHistory(stateHistory, 'cart');
        expect(result.length).toBeGreaterThanOrEqual(2);
    });
});
