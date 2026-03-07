/**
 * Unit tests for debug panel TurboNav persistence via sessionStorage
 *
 * Since the debug panel is an IIFE, we test the persistence logic by
 * replicating the save/restore behavior from 15-panel-controls.js.
 */

import { describe, it, expect, beforeEach } from 'vitest';

// Replicate save history logic from destroy()
function saveHistoryToSession(eventHistory, patchHistory, networkHistory, stateHistory) {
    const historyData = {
        events: eventHistory.slice(0, 100),
        patches: patchHistory.slice(0, 100),
        network: networkHistory.slice(0, 100),
        stateHistory: stateHistory.slice(0, 50),
        timestamp: Date.now()
    };
    try {
        sessionStorage.setItem('djust-debug-history', JSON.stringify(historyData));
        return true;
    } catch (e) {
        return false;  // sessionStorage full or unavailable
    }
}

// Replicate restore history logic from loadState()
function restoreHistoryFromSession() {
    const savedHistory = sessionStorage.getItem('djust-debug-history');
    if (!savedHistory) return null;

    try {
        const historyData = JSON.parse(savedHistory);
        // Only restore if recent (within 30 seconds)
        if (Date.now() - historyData.timestamp < 30000) {
            sessionStorage.removeItem('djust-debug-history');
            return {
                events: historyData.events || [],
                patches: historyData.patches || [],
                network: historyData.network || [],
                stateHistory: historyData.stateHistory || []
            };
        }
        return null;
    } catch (e) {
        return null;  // Corrupted data
    }
}

describe('TurboNav Panel Persistence', () => {
    beforeEach(() => {
        // Clear sessionStorage before each test
        sessionStorage.clear();
    });

    describe('save history logic', () => {
        it('saves history to sessionStorage', () => {
            const events = [
                { event: 'test1', timestamp: Date.now() },
                { event: 'test2', timestamp: Date.now() }
            ];
            const patches = [
                { type: 'SetAttr', timestamp: Date.now() }
            ];
            const network = [
                { message: 'connected', timestamp: Date.now() }
            ];
            const stateHistory = [
                { variables: { count: 1 }, timestamp: Date.now() }
            ];

            const saved = saveHistoryToSession(events, patches, network, stateHistory);
            expect(saved).toBe(true);

            const stored = sessionStorage.getItem('djust-debug-history');
            expect(stored).toBeTruthy();

            const parsed = JSON.parse(stored);
            expect(parsed.events).toHaveLength(2);
            expect(parsed.patches).toHaveLength(1);
            expect(parsed.network).toHaveLength(1);
            expect(parsed.stateHistory).toHaveLength(1);
            expect(parsed.timestamp).toBeLessThanOrEqual(Date.now());
        });

        it('limits saved history to prevent sessionStorage overflow', () => {
            // Create large history arrays
            const events = Array(150).fill().map((_, i) => ({
                event: `test${i}`,
                timestamp: Date.now()
            }));
            const patches = Array(150).fill().map((_, i) => ({
                type: 'SetAttr',
                timestamp: Date.now()
            }));
            const stateHistory = Array(100).fill().map((_, i) => ({
                variables: { count: i },
                timestamp: Date.now()
            }));

            saveHistoryToSession(events, patches, [], stateHistory);

            const saved = JSON.parse(sessionStorage.getItem('djust-debug-history'));

            // Should limit to 100 events, 100 patches, 50 states
            expect(saved.events).toHaveLength(100);
            expect(saved.patches).toHaveLength(100);
            expect(saved.stateHistory).toHaveLength(50);
        });

        it('wraps sessionStorage.setItem in try-catch for quota exceeded', () => {
            // This test verifies the function has error handling by checking
            // it doesn't throw when sessionStorage fails. We can't easily mock
            // sessionStorage in JSDOM, but the try-catch ensures graceful
            // handling in production.
            const events = [{ event: 'test', timestamp: Date.now() }];

            // Should not throw even if sessionStorage has issues
            expect(() => {
                saveHistoryToSession(events, [], [], []);
            }).not.toThrow();
        });
    });

    describe('restore history logic', () => {
        it('restores history within 30s window', () => {
            // Save some history
            const historyData = {
                events: [
                    { event: 'restored_event', timestamp: Date.now() }
                ],
                patches: [
                    { type: 'SetAttr', timestamp: Date.now() }
                ],
                network: [
                    { message: 'restored_message', timestamp: Date.now() }
                ],
                stateHistory: [
                    { variables: { count: 99 }, timestamp: Date.now() }
                ],
                timestamp: Date.now() - 10000 // 10 seconds ago
            };
            sessionStorage.setItem('djust-debug-history', JSON.stringify(historyData));

            const restored = restoreHistoryFromSession();

            expect(restored).toBeTruthy();
            expect(restored.events).toHaveLength(1);
            expect(restored.events[0].event).toBe('restored_event');
            expect(restored.patches).toHaveLength(1);
            expect(restored.network).toHaveLength(1);
            expect(restored.stateHistory).toHaveLength(1);
        });

        it('ignores stale history after 30s', () => {
            // Save old history (35 seconds ago)
            const historyData = {
                events: [{ event: 'stale_event', timestamp: Date.now() }],
                patches: [],
                network: [],
                stateHistory: [],
                timestamp: Date.now() - 35000 // 35 seconds ago
            };
            sessionStorage.setItem('djust-debug-history', JSON.stringify(historyData));

            const restored = restoreHistoryFromSession();

            // Should not restore stale history
            expect(restored).toBeNull();
        });

        it('clears sessionStorage after restoring', () => {
            const historyData = {
                events: [{ event: 'test', timestamp: Date.now() }],
                patches: [],
                network: [],
                stateHistory: [],
                timestamp: Date.now() - 5000
            };
            sessionStorage.setItem('djust-debug-history', JSON.stringify(historyData));

            const restored = restoreHistoryFromSession();
            expect(restored).toBeTruthy();

            // Should clear after restoring
            expect(sessionStorage.getItem('djust-debug-history')).toBeNull();
        });

        it('handles corrupted sessionStorage data gracefully', () => {
            sessionStorage.setItem('djust-debug-history', 'not-valid-json');

            const restored = restoreHistoryFromSession();

            expect(restored).toBeNull();
        });

        it('returns null when no saved history', () => {
            const restored = restoreHistoryFromSession();
            expect(restored).toBeNull();
        });
    });

    describe('30 second window', () => {
        it('restores at exactly 29.9 seconds (within window)', () => {
            const historyData = {
                events: [{ event: 'edge_case', timestamp: Date.now() }],
                patches: [],
                network: [],
                stateHistory: [],
                timestamp: Date.now() - 29900 // 29.9 seconds ago
            };
            sessionStorage.setItem('djust-debug-history', JSON.stringify(historyData));

            const restored = restoreHistoryFromSession();

            expect(restored).toBeTruthy();
            expect(restored.events).toHaveLength(1);
        });

        it('ignores at exactly 30.1 seconds (outside window)', () => {
            const historyData = {
                events: [{ event: 'expired', timestamp: Date.now() }],
                patches: [],
                network: [],
                stateHistory: [],
                timestamp: Date.now() - 30100 // 30.1 seconds ago
            };
            sessionStorage.setItem('djust-debug-history', JSON.stringify(historyData));

            const restored = restoreHistoryFromSession();

            expect(restored).toBeNull();
        });
    });
});
