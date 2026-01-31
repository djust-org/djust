/**
 * Unit tests for debug panel network message inspection (Issue #179)
 */

import { describe, it, expect } from 'vitest';

// Test the message rendering logic
function getPayloadJson(msg) {
    const payload = msg.data || msg.payload;
    if (!payload) return null;
    if (typeof payload === 'object' && Object.keys(payload).length === 0) return null;
    return JSON.stringify(payload, null, 2);
}

function getMessageType(msg) {
    const payload = msg.data || msg.payload;
    return msg.type || (payload ? (payload.type || payload.event || 'data') : 'unknown');
}

const sampleMessages = [
    { direction: 'sent', payload: { type: 'event', event: 'increment', params: { amount: 1 } }, size: 64, timestamp: Date.now() },
    { direction: 'received', payload: { type: 'response', patches: [{ op: 'replace' }], _debug: {} }, size: 128, timestamp: Date.now() },
    { direction: 'sent', payload: {}, size: 2, timestamp: Date.now() },
    { direction: 'received', data: 'binary-data', size: 32, timestamp: Date.now() },
];

describe('Network Message Inspection', () => {
    it('extracts payload JSON from message with payload object', () => {
        const json = getPayloadJson(sampleMessages[0]);
        expect(json).toContain('"event": "increment"');
        expect(json).toContain('"amount": 1');
    });

    it('returns null for empty payload', () => {
        const json = getPayloadJson(sampleMessages[2]);
        expect(json).toBeNull();
    });

    it('uses data field when payload is absent', () => {
        const json = getPayloadJson(sampleMessages[3]);
        expect(json).toBe('"binary-data"');
    });

    it('detects message type from payload.type', () => {
        expect(getMessageType(sampleMessages[0])).toBe('event');
        expect(getMessageType(sampleMessages[1])).toBe('response');
    });

    it('falls back to unknown for typeless messages', () => {
        expect(getMessageType({ direction: 'sent' })).toBe('unknown');
    });

    it('identifies sent vs received direction', () => {
        expect(sampleMessages[0].direction).toBe('sent');
        expect(sampleMessages[1].direction).toBe('received');
    });

    it('detects debug info in payload', () => {
        expect(sampleMessages[1].payload._debug).toBeTruthy();
        expect(sampleMessages[0].payload._debug).toBeUndefined();
    });
});
