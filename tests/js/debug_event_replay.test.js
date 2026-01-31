/**
 * Unit tests for debug panel event replay (Issue #177)
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';

// Mock the replay logic extracted from 03-tab-events.js
function replayEvent(eventHistory, index, liveView) {
    const event = eventHistory[index];
    if (!event) return { status: 'error', message: 'No event' };

    const handlerName = event.handler || event.name;
    if (!handlerName) return { status: 'error', message: 'No handler' };

    if (!liveView || !liveView.sendEvent) {
        return { status: 'error', message: 'No connection' };
    }

    const sent = liveView.sendEvent(handlerName, event.params || {});
    if (sent) {
        return { status: 'success' };
    } else {
        return { status: 'error', message: 'Send failed' };
    }
}

const sampleEvents = [
    { handler: 'increment', timestamp: Date.now(), params: { amount: 1 } },
    { handler: 'decrement', timestamp: Date.now() },
    { name: 'search', timestamp: Date.now(), params: { query: 'test' } },
    { timestamp: Date.now() }, // no handler or name
];

describe('Event Replay Logic', () => {
    let mockLiveView;

    beforeEach(() => {
        mockLiveView = {
            sendEvent: vi.fn().mockReturnValue(true)
        };
    });

    it('replays event with handler and params', () => {
        const result = replayEvent(sampleEvents, 0, mockLiveView);
        expect(result.status).toBe('success');
        expect(mockLiveView.sendEvent).toHaveBeenCalledWith('increment', { amount: 1 });
    });

    it('replays event with handler but no params', () => {
        const result = replayEvent(sampleEvents, 1, mockLiveView);
        expect(result.status).toBe('success');
        expect(mockLiveView.sendEvent).toHaveBeenCalledWith('decrement', {});
    });

    it('replays event using name field fallback', () => {
        const result = replayEvent(sampleEvents, 2, mockLiveView);
        expect(result.status).toBe('success');
        expect(mockLiveView.sendEvent).toHaveBeenCalledWith('search', { query: 'test' });
    });

    it('returns error for event without handler or name', () => {
        const result = replayEvent(sampleEvents, 3, mockLiveView);
        expect(result.status).toBe('error');
        expect(result.message).toBe('No handler');
        expect(mockLiveView.sendEvent).not.toHaveBeenCalled();
    });

    it('returns error for invalid index', () => {
        const result = replayEvent(sampleEvents, 99, mockLiveView);
        expect(result.status).toBe('error');
    });

    it('returns error when no liveView connection', () => {
        const result = replayEvent(sampleEvents, 0, null);
        expect(result.status).toBe('error');
        expect(result.message).toBe('No connection');
    });

    it('returns error when sendEvent fails', () => {
        mockLiveView.sendEvent.mockReturnValue(false);
        const result = replayEvent(sampleEvents, 0, mockLiveView);
        expect(result.status).toBe('error');
        expect(result.message).toBe('Send failed');
    });
});
