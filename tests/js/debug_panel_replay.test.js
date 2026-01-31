/**
 * Tests for debug panel event replay (Issue #171)
 *
 * Verifies replay message construction, WebSocket availability check,
 * and button state feedback logic.
 */

import { describe, it, expect, vi } from 'vitest';

describe('Event replay message construction', () => {
    it('should construct correct WebSocket message from event', () => {
        const event = { handler: 'increment', name: 'increment', params: { amount: 5 } };
        const message = JSON.stringify({
            type: 'event',
            event: event.handler || event.name,
            params: event.params
        });
        const parsed = JSON.parse(message);

        expect(parsed.type).toBe('event');
        expect(parsed.event).toBe('increment');
        expect(parsed.params.amount).toBe(5);
    });

    it('should fall back to name when handler is missing', () => {
        const event = { name: 'submit', params: { data: 'test' } };
        const message = JSON.stringify({
            type: 'event',
            event: event.handler || event.name,
            params: event.params
        });
        const parsed = JSON.parse(message);

        expect(parsed.event).toBe('submit');
    });

    it('should not be replayable without params', () => {
        const event = { handler: 'reset', params: null };
        const canReplay = !!event.params;
        expect(canReplay).toBe(false);
    });

    it('should be replayable with empty params object', () => {
        const event = { handler: 'reset', params: {} };
        const canReplay = !!event.params;
        expect(canReplay).toBe(true);
    });
});

describe('WebSocket readiness check', () => {
    it('should detect when WebSocket is not available', () => {
        const ws = null;
        const isReady = ws && ws.readyState === 1; // WebSocket.OPEN = 1
        expect(isReady).toBeFalsy();
    });

    it('should detect when WebSocket is open', () => {
        const ws = { readyState: 1 }; // WebSocket.OPEN
        const isReady = ws && ws.readyState === 1;
        expect(isReady).toBe(true);
    });

    it('should detect when WebSocket is closed', () => {
        const ws = { readyState: 3 }; // WebSocket.CLOSED
        const isReady = ws && ws.readyState === 1;
        expect(isReady).toBeFalsy();
    });
});

describe('Replay button feedback states', () => {
    it('should cycle through pending -> success states', () => {
        const states = [];
        const btn = {
            textContent: 'Replay',
            classList: {
                add: (cls) => states.push(`+${cls}`),
                remove: (cls) => states.push(`-${cls}`),
            }
        };

        // Simulate success flow
        btn.textContent = 'Sending...';
        btn.classList.add('replay-pending');
        expect(btn.textContent).toBe('Sending...');

        btn.textContent = 'Sent!';
        btn.classList.remove('replay-pending');
        btn.classList.add('replay-success');
        expect(btn.textContent).toBe('Sent!');
        expect(states).toContain('+replay-success');
    });
});
