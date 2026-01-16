/**
 * Tests for @debounce decorator
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import {
    debounceEvent,
    debounceTimers,
    clearAllState
} from '../../python/djust/static/djust/decorators.js';

describe('Debounce Decorator', () => {
    let sendFn;
    let clock;

    beforeEach(() => {
        // Clear state before each test
        clearAllState();

        // Mock send function
        sendFn = vi.fn();

        // Use fake timers
        vi.useFakeTimers();
    });

    afterEach(() => {
        vi.restoreAllMocks();
        vi.useRealTimers();
    });

    describe('Basic Debouncing', () => {
        it('should delay execution until wait time passes', () => {
            const config = { wait: 0.5 }; // 500ms

            debounceEvent('search', { query: 'test' }, config, sendFn);

            // Should not execute immediately
            expect(sendFn).not.toHaveBeenCalled();

            // Fast-forward 400ms (not enough)
            vi.advanceTimersByTime(400);
            expect(sendFn).not.toHaveBeenCalled();

            // Fast-forward another 100ms (total 500ms)
            vi.advanceTimersByTime(100);
            expect(sendFn).toHaveBeenCalledOnce();
            expect(sendFn).toHaveBeenCalledWith('search', { query: 'test' });
        });

        it('should restart timer on new events', () => {
            const config = { wait: 0.3 }; // 300ms

            // First event
            debounceEvent('search', { query: 'a' }, config, sendFn);
            vi.advanceTimersByTime(200);
            expect(sendFn).not.toHaveBeenCalled();

            // Second event (resets timer)
            debounceEvent('search', { query: 'ab' }, config, sendFn);
            vi.advanceTimersByTime(200);
            expect(sendFn).not.toHaveBeenCalled();

            // Third event (resets timer again)
            debounceEvent('search', { query: 'abc' }, config, sendFn);
            vi.advanceTimersByTime(300);

            // Should execute with latest data
            expect(sendFn).toHaveBeenCalledOnce();
            expect(sendFn).toHaveBeenCalledWith('search', { query: 'abc' });
        });

        it('should handle multiple independent debounced events', () => {
            const config = { wait: 0.2 }; // 200ms

            debounceEvent('search', { query: 'test' }, config, sendFn);
            debounceEvent('filter', { status: 'active' }, config, sendFn);

            vi.advanceTimersByTime(200);

            expect(sendFn).toHaveBeenCalledTimes(2);
            expect(sendFn).toHaveBeenCalledWith('search', { query: 'test' });
            expect(sendFn).toHaveBeenCalledWith('filter', { status: 'active' });
        });

        it('should clear timer state after execution', () => {
            const config = { wait: 0.1 }; // 100ms

            debounceEvent('search', { query: 'test' }, config, sendFn);

            // State should exist while waiting
            expect(debounceTimers.has('search')).toBe(true);

            vi.advanceTimersByTime(100);

            // State should be cleared after execution
            expect(debounceTimers.has('search')).toBe(false);
        });
    });

    describe('Max Wait (Force Execution)', () => {
        it('should force execution after max_wait even with continuous events', () => {
            const config = { wait: 0.5, max_wait: 2.0 }; // 500ms wait, 2s max

            // Continuous stream of events
            debounceEvent('search', { query: 'a' }, config, sendFn);
            vi.advanceTimersByTime(400);

            debounceEvent('search', { query: 'ab' }, config, sendFn);
            vi.advanceTimersByTime(400);

            debounceEvent('search', { query: 'abc' }, config, sendFn);
            vi.advanceTimersByTime(400);

            debounceEvent('search', { query: 'abcd' }, config, sendFn);
            vi.advanceTimersByTime(400);

            debounceEvent('search', { query: 'abcde' }, config, sendFn);
            vi.advanceTimersByTime(400); // Total: 2000ms

            // One more event after max_wait - this should trigger force execution
            debounceEvent('search', { query: 'abcdef' }, config, sendFn);

            // Should have executed due to max_wait
            expect(sendFn).toHaveBeenCalledOnce();
            expect(sendFn).toHaveBeenCalledWith('search', { query: 'abcdef' });
        });

        it('should not force execution if max_wait not exceeded', () => {
            const config = { wait: 0.5, max_wait: 2.0 };

            debounceEvent('search', { query: 'test' }, config, sendFn);

            // Wait for normal debounce time (should execute normally, not via max_wait)
            vi.advanceTimersByTime(500);
            expect(sendFn).toHaveBeenCalledOnce();
        });

        it('should clear timer state after max_wait execution', () => {
            const config = { wait: 0.5, max_wait: 1.0 };

            debounceEvent('search', { query: 'test' }, config, sendFn);

            // Trigger max_wait with continuous events
            for (let i = 0; i < 4; i++) {
                vi.advanceTimersByTime(300);
                debounceEvent('search', { query: `test${i}` }, config, sendFn);
            }

            // This call happens at 1200ms, which exceeds max_wait of 1000ms
            expect(sendFn).toHaveBeenCalled();
            expect(debounceTimers.has('search')).toBe(false);
        });

        it('should handle max_wait=0 (no max wait)', () => {
            const config = { wait: 0.5, max_wait: 0 };

            // Continuous events
            for (let i = 0; i < 10; i++) {
                debounceEvent('search', { query: `test${i}` }, config, sendFn);
                vi.advanceTimersByTime(400);
            }

            // Should never force execute (only normal debounce)
            expect(sendFn).not.toHaveBeenCalled();

            // Wait for final debounce
            vi.advanceTimersByTime(500);
            expect(sendFn).toHaveBeenCalledOnce();
        });
    });

    describe('Edge Cases', () => {
        it('should handle very short wait times', () => {
            const config = { wait: 0.01 }; // 10ms

            debounceEvent('search', { query: 'test' }, config, sendFn);
            vi.advanceTimersByTime(10);

            expect(sendFn).toHaveBeenCalledOnce();
        });

        it('should handle very long wait times', () => {
            const config = { wait: 10.0 }; // 10 seconds

            debounceEvent('search', { query: 'test' }, config, sendFn);
            vi.advanceTimersByTime(9999);
            expect(sendFn).not.toHaveBeenCalled();

            vi.advanceTimersByTime(1);
            expect(sendFn).toHaveBeenCalledOnce();
        });

        it('should handle empty event data', () => {
            const config = { wait: 0.1 };

            debounceEvent('empty_event', {}, config, sendFn);
            vi.advanceTimersByTime(100);

            expect(sendFn).toHaveBeenCalledWith('empty_event', {});
        });

        it('should handle complex event data objects', () => {
            const config = { wait: 0.1 };
            const complexData = {
                query: 'test',
                filters: {
                    status: 'active',
                    tags: ['urgent', 'review']
                },
                pagination: { page: 1, size: 20 }
            };

            debounceEvent('search', complexData, config, sendFn);
            vi.advanceTimersByTime(100);

            expect(sendFn).toHaveBeenCalledWith('search', complexData);
        });

        it('should handle null/undefined in event data', () => {
            const config = { wait: 0.1 };

            debounceEvent('event1', null, config, sendFn);
            vi.advanceTimersByTime(100);
            expect(sendFn).toHaveBeenCalledWith('event1', null);

            sendFn.mockClear();

            debounceEvent('event2', undefined, config, sendFn);
            vi.advanceTimersByTime(100);
            expect(sendFn).toHaveBeenCalledWith('event2', undefined);
        });
    });

    describe('State Management', () => {
        it('should track timer state correctly', () => {
            const config = { wait: 0.5 };

            expect(debounceTimers.has('search')).toBe(false);

            debounceEvent('search', { query: 'test' }, config, sendFn);

            expect(debounceTimers.has('search')).toBe(true);
            const state = debounceTimers.get('search');
            expect(state).toHaveProperty('timerId');
            expect(state).toHaveProperty('firstCallTime');
        });

        it('should update firstCallTime correctly', () => {
            const config = { wait: 0.3, max_wait: 1.0 };

            debounceEvent('search', { query: 'a' }, config, sendFn);
            const state1 = debounceTimers.get('search');
            const firstCall = state1.firstCallTime;

            vi.advanceTimersByTime(200);
            debounceEvent('search', { query: 'ab' }, config, sendFn);
            const state2 = debounceTimers.get('search');

            // firstCallTime should not change on subsequent calls
            expect(state2.firstCallTime).toBe(firstCall);
        });

        it('should reset firstCallTime after execution', () => {
            const config = { wait: 0.2 };

            debounceEvent('search', { query: 'first' }, config, sendFn);
            const firstCallTime1 = debounceTimers.get('search').firstCallTime;

            vi.advanceTimersByTime(200);
            expect(debounceTimers.has('search')).toBe(false);

            // New event after execution
            vi.advanceTimersByTime(100); // Advance time
            debounceEvent('search', { query: 'second' }, config, sendFn);
            const firstCallTime2 = debounceTimers.get('search').firstCallTime;

            expect(firstCallTime2).toBeGreaterThan(firstCallTime1);
        });
    });

    describe('Real-World Scenarios', () => {
        it('should simulate typing in search box (10 keystrokes)', () => {
            const config = { wait: 0.3, max_wait: 2.0 };
            const text = 'javascript';

            for (let i = 1; i <= text.length; i++) {
                debounceEvent('search', { query: text.slice(0, i) }, config, sendFn);
                vi.advanceTimersByTime(100); // 100ms between keystrokes
            }

            // Should not have executed yet (still typing)
            expect(sendFn).not.toHaveBeenCalled();

            // User stops typing
            vi.advanceTimersByTime(300);

            // Should execute with final query
            expect(sendFn).toHaveBeenCalledOnce();
            expect(sendFn).toHaveBeenCalledWith('search', { query: 'javascript' });
        });

        it('should simulate rapid typing with max_wait protection', () => {
            const config = { wait: 0.5, max_wait: 2.0 };

            // Rapid typing for 3 seconds (exceeds max_wait)
            for (let i = 0; i < 30; i++) {
                debounceEvent('search', { query: `char${i}` }, config, sendFn);
                vi.advanceTimersByTime(100);
            }

            // Should have executed at least once due to max_wait
            expect(sendFn).toHaveBeenCalled();
        });

        it('should handle window resize events', () => {
            const config = { wait: 0.2, max_wait: 1.0 };

            // Simulate multiple resize events (20 events * 50ms = 1000ms)
            for (let i = 0; i < 20; i++) {
                debounceEvent('on_resize', { width: 1024 + i, height: 768 }, config, sendFn);
                vi.advanceTimersByTime(50);
            }

            // Last call is at 950ms, advance to trigger one more call past max_wait
            debounceEvent('on_resize', { width: 1044, height: 768 }, config, sendFn);

            // Should have executed due to max_wait
            expect(sendFn).toHaveBeenCalled();
        });
    });

    describe('Integration with clearAllState', () => {
        it('should clear all debounce timers when clearAllState called', () => {
            const config = { wait: 0.5 };

            debounceEvent('event1', { data: 1 }, config, sendFn);
            debounceEvent('event2', { data: 2 }, config, sendFn);
            debounceEvent('event3', { data: 3 }, config, sendFn);

            expect(debounceTimers.size).toBe(3);

            clearAllState();

            expect(debounceTimers.size).toBe(0);
        });

        it('should clear timers without executing callbacks', () => {
            const config = { wait: 0.5 };

            debounceEvent('search', { query: 'test' }, config, sendFn);
            vi.advanceTimersByTime(300);

            clearAllState();
            vi.advanceTimersByTime(300);

            // Should not have executed
            expect(sendFn).not.toHaveBeenCalled();
        });
    });
});
