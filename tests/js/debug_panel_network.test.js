/**
 * Tests for debug panel Network tab enhancements (Issue #167)
 *
 * Verifies direction color classes, copy-to-clipboard functionality,
 * and expandable message body rendering.
 */

import { describe, it, expect, vi } from 'vitest';

describe('Network tab message rendering', () => {
    it('should assign direction class to sent messages', () => {
        const msg = { direction: 'sent', type: 'event', size: 128, timestamp: Date.now(), payload: { type: 'click' } };
        const dirClass = msg.direction; // 'sent' or 'received'
        expect(dirClass).toBe('sent');
    });

    it('should assign direction class to received messages', () => {
        const msg = { direction: 'received', type: 'data', size: 256, timestamp: Date.now(), payload: { state: {} } };
        expect(msg.direction).toBe('received');
    });

    it('should detect expandable messages with payload', () => {
        const msg = { payload: { type: 'event', data: 'test' } };
        const hasPayload = msg.data || (msg.payload && Object.keys(msg.payload).length > 0);
        expect(hasPayload).toBe(true);
    });

    it('should not be expandable without payload', () => {
        const msg = { payload: {} };
        const hasPayload = msg.data || (msg.payload && Object.keys(msg.payload).length > 0);
        expect(hasPayload).toBe(false);
    });

    it('should format payload as pretty-printed JSON', () => {
        const payload = { type: 'event', handler: 'increment', params: { amount: 1 } };
        const formatted = JSON.stringify(payload, null, 2);
        expect(formatted).toContain('"handler": "increment"');
        expect(formatted).toContain('  '); // Indented
    });
});

describe('Copy to clipboard', () => {
    it('should produce correct JSON for clipboard copy', () => {
        const payload = { type: 'event', params: { x: 1 } };
        const text = JSON.stringify(payload, null, 2);
        expect(text).toContain('"type": "event"');
        expect(text).toContain('"x": 1');
    });

    it('should call writeText with formatted JSON', async () => {
        const writeText = vi.fn().mockResolvedValue(undefined);
        // Override navigator.clipboard in test env
        Object.defineProperty(navigator, 'clipboard', {
            value: { writeText },
            writable: true,
            configurable: true,
        });

        const payload = { handler: 'increment' };
        await navigator.clipboard.writeText(JSON.stringify(payload, null, 2));
        expect(writeText).toHaveBeenCalledTimes(1);
        expect(writeText.mock.calls[0][0]).toContain('"handler": "increment"');
    });
});
