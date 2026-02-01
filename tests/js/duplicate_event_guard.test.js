/**
 * Tests for duplicate event guard in handleEvent.
 *
 * The in-flight guard uses a Map<string, timestamp> to suppress duplicate
 * events fired within 300ms of each other (same event name + params).
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';

describe('Duplicate Event Guard', () => {
    let inFlightEvents;

    beforeEach(() => {
        inFlightEvents = new Map();
    });

    function buildInFlightKey(eventName, params) {
        const guardParams = {};
        for (const [k, v] of Object.entries(params)) {
            if (k !== '_targetElement') guardParams[k] = v;
        }
        return eventName + ':' + JSON.stringify(guardParams);
    }

    function shouldSuppress(eventName, params, now) {
        const key = buildInFlightKey(eventName, params);
        const lastSent = inFlightEvents.get(key);
        if (lastSent && (now - lastSent) < 300) {
            return true;
        }
        inFlightEvents.set(key, now);
        return false;
    }

    it('should allow first event through', () => {
        expect(shouldSuppress('delete_todo', { id: 1 }, 1000)).toBe(false);
    });

    it('should suppress duplicate event within 300ms', () => {
        shouldSuppress('delete_todo', { id: 1 }, 1000);
        expect(shouldSuppress('delete_todo', { id: 1 }, 1100)).toBe(true);
        expect(shouldSuppress('delete_todo', { id: 1 }, 1299)).toBe(true);
    });

    it('should allow same event after 300ms', () => {
        shouldSuppress('delete_todo', { id: 1 }, 1000);
        expect(shouldSuppress('delete_todo', { id: 1 }, 1300)).toBe(false);
    });

    it('should allow different event names concurrently', () => {
        shouldSuppress('delete_todo', { id: 1 }, 1000);
        expect(shouldSuppress('add_todo', { id: 1 }, 1050)).toBe(false);
    });

    it('should allow same event with different params', () => {
        shouldSuppress('delete_todo', { id: 1 }, 1000);
        expect(shouldSuppress('delete_todo', { id: 2 }, 1050)).toBe(false);
    });

    it('should exclude _targetElement from dedup key', () => {
        const elem1 = { tagName: 'BUTTON' };
        const elem2 = { tagName: 'DIV' };
        shouldSuppress('delete_todo', { id: 1, _targetElement: elem1 }, 1000);
        // Same event + params but different _targetElement should still be suppressed
        expect(shouldSuppress('delete_todo', { id: 1, _targetElement: elem2 }, 1050)).toBe(true);
    });

    it('should handle rapid triple-click correctly', () => {
        expect(shouldSuppress('delete_todo', { id: 1 }, 1000)).toBe(false);
        expect(shouldSuppress('delete_todo', { id: 1 }, 1010)).toBe(true);
        expect(shouldSuppress('delete_todo', { id: 1 }, 1020)).toBe(true);
    });
});
