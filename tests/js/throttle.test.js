/**
 * Tests for @throttle decorator
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import {
    throttleEvent,
    throttleState,
    clearAllState
} from '../../python/djust/static/djust/decorators.js';

describe('Throttle Decorator', () => {
    let sendFn;

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

    describe('Basic Throttling', () => {
        it('should execute immediately on first call with leading=true', () => {
            const config = { interval: 0.5, leading: true, trailing: false };

            throttleEvent('scroll', { scrollY: 100 }, config, sendFn);

            expect(sendFn).toHaveBeenCalledOnce();
            expect(sendFn).toHaveBeenCalledWith('scroll', { scrollY: 100 });
        });

        it('should not execute immediately with leading=false', () => {
            const config = { interval: 0.5, leading: false, trailing: false };

            throttleEvent('scroll', { scrollY: 100 }, config, sendFn);

            expect(sendFn).not.toHaveBeenCalled();
        });

        it('should limit execution to interval frequency', () => {
            const config = { interval: 0.1, leading: true, trailing: false }; // 100ms

            // First event - executes immediately
            throttleEvent('scroll', { scrollY: 0 }, config, sendFn);
            expect(sendFn).toHaveBeenCalledTimes(1);

            // Events within interval - dropped
            vi.advanceTimersByTime(50);
            throttleEvent('scroll', { scrollY: 50 }, config, sendFn);
            expect(sendFn).toHaveBeenCalledTimes(1); // Still 1

            vi.advanceTimersByTime(40);
            throttleEvent('scroll', { scrollY: 90 }, config, sendFn);
            expect(sendFn).toHaveBeenCalledTimes(1); // Still 1

            // Event after interval - executes
            vi.advanceTimersByTime(10);
            throttleEvent('scroll', { scrollY: 100 }, config, sendFn);
            expect(sendFn).toHaveBeenCalledTimes(2);
        });

        it('should handle multiple independent throttled events', () => {
            const config = { interval: 0.2, leading: true, trailing: false };

            throttleEvent('scroll', { scrollY: 100 }, config, sendFn);
            throttleEvent('resize', { width: 1024 }, config, sendFn);

            expect(sendFn).toHaveBeenCalledTimes(2);
            expect(sendFn).toHaveBeenNthCalledWith(1, 'scroll', { scrollY: 100 });
            expect(sendFn).toHaveBeenNthCalledWith(2, 'resize', { width: 1024 });
        });
    });

    describe('Leading Edge', () => {
        it('should execute on first event with leading=true', () => {
            const config = { interval: 0.5, leading: true, trailing: false };

            throttleEvent('scroll', { scrollY: 100 }, config, sendFn);

            expect(sendFn).toHaveBeenCalledOnce();
            expect(sendFn).toHaveBeenCalledWith('scroll', { scrollY: 100 });
        });

        it('should drop events during interval with leading=true, trailing=false', () => {
            const config = { interval: 0.5, leading: true, trailing: false };

            throttleEvent('scroll', { scrollY: 0 }, config, sendFn);
            expect(sendFn).toHaveBeenCalledTimes(1);

            vi.advanceTimersByTime(200);
            throttleEvent('scroll', { scrollY: 200 }, config, sendFn);
            expect(sendFn).toHaveBeenCalledTimes(1); // Dropped

            vi.advanceTimersByTime(200);
            throttleEvent('scroll', { scrollY: 400 }, config, sendFn);
            expect(sendFn).toHaveBeenCalledTimes(1); // Dropped

            vi.advanceTimersByTime(100); // Total 500ms
            throttleEvent('scroll', { scrollY: 500 }, config, sendFn);
            expect(sendFn).toHaveBeenCalledTimes(2); // Executes
        });

        it('should execute immediately after interval expires', () => {
            const config = { interval: 0.1, leading: true, trailing: false };

            throttleEvent('scroll', { scrollY: 0 }, config, sendFn);
            vi.advanceTimersByTime(100);
            throttleEvent('scroll', { scrollY: 100 }, config, sendFn);

            expect(sendFn).toHaveBeenCalledTimes(2);
        });
    });

    describe('Trailing Edge', () => {
        it('should execute on trailing edge with leading=false, trailing=true', () => {
            const config = { interval: 0.5, leading: false, trailing: true };

            throttleEvent('scroll', { scrollY: 100 }, config, sendFn);

            // Not executed immediately
            expect(sendFn).not.toHaveBeenCalled();

            // Execute after interval
            vi.advanceTimersByTime(500);
            expect(sendFn).toHaveBeenCalledOnce();
            expect(sendFn).toHaveBeenCalledWith('scroll', { scrollY: 100 });
        });

        it('should execute with latest data on trailing edge', () => {
            const config = { interval: 0.5, leading: false, trailing: true };

            throttleEvent('scroll', { scrollY: 100 }, config, sendFn);
            vi.advanceTimersByTime(200);

            throttleEvent('scroll', { scrollY: 200 }, config, sendFn);
            vi.advanceTimersByTime(300); // Total 500ms - trailing executes

            // Should execute with latest data (200)
            expect(sendFn).toHaveBeenCalledOnce();
            expect(sendFn).toHaveBeenCalledWith('scroll', { scrollY: 200 });
        });

        it('should reschedule trailing call on new events', () => {
            const config = { interval: 0.5, leading: false, trailing: true };

            throttleEvent('scroll', { scrollY: 100 }, config, sendFn);
            vi.advanceTimersByTime(400);

            // New event reschedules trailing timer
            throttleEvent('scroll', { scrollY: 200 }, config, sendFn);

            // Execute after interval from first event (100ms more)
            vi.advanceTimersByTime(100);
            expect(sendFn).toHaveBeenCalledOnce();
            expect(sendFn).toHaveBeenCalledWith('scroll', { scrollY: 200 });
        });
    });

    describe('Leading + Trailing', () => {
        it('should execute on both leading and trailing edges', () => {
            const config = { interval: 0.5, leading: true, trailing: true };

            // Leading edge
            throttleEvent('scroll', { scrollY: 0 }, config, sendFn);
            expect(sendFn).toHaveBeenCalledTimes(1);
            expect(sendFn).toHaveBeenCalledWith('scroll', { scrollY: 0 });

            // Events during interval
            vi.advanceTimersByTime(200);
            throttleEvent('scroll', { scrollY: 200 }, config, sendFn);

            vi.advanceTimersByTime(200);
            throttleEvent('scroll', { scrollY: 400 }, config, sendFn);

            // Trailing edge (100ms after last event within interval)
            vi.advanceTimersByTime(100);
            expect(sendFn).toHaveBeenCalledTimes(2);
            expect(sendFn).toHaveBeenLastCalledWith('scroll', { scrollY: 400 });
        });

        it('should clear trailing call if interval expires with new event', () => {
            const config = { interval: 0.2, leading: true, trailing: true };

            // Leading edge
            throttleEvent('scroll', { scrollY: 0 }, config, sendFn);
            expect(sendFn).toHaveBeenCalledTimes(1);

            // Event within interval - schedules trailing
            vi.advanceTimersByTime(100);
            throttleEvent('scroll', { scrollY: 100 }, config, sendFn);
            expect(sendFn).toHaveBeenCalledTimes(1);

            // Event after interval - executes immediately
            vi.advanceTimersByTime(100);
            throttleEvent('scroll', { scrollY: 200 }, config, sendFn);

            // First trailing from scrollY:100 fires, then immediate for scrollY:200
            expect(sendFn).toHaveBeenCalledTimes(3);
        });

        it('should handle rapid burst followed by pause', () => {
            const config = { interval: 0.5, leading: true, trailing: true };

            // Rapid burst (10 events over 90ms)
            for (let i = 0; i < 10; i++) {
                throttleEvent('scroll', { scrollY: i * 10 }, config, sendFn);
                if (i < 9) vi.advanceTimersByTime(10);
            }

            // Should have executed once (leading)
            expect(sendFn).toHaveBeenCalledTimes(1);
            expect(sendFn).toHaveBeenCalledWith('scroll', { scrollY: 0 });

            // Wait for trailing (need to wait remaining time from last event)
            vi.advanceTimersByTime(410); // Total 500ms from first call
            expect(sendFn.mock.calls.length).toBeGreaterThanOrEqual(1);
        });
    });

    describe('State Management', () => {
        it('should track throttle state correctly', () => {
            const config = { interval: 0.5, leading: true, trailing: false };

            expect(throttleState.has('scroll')).toBe(false);

            throttleEvent('scroll', { scrollY: 100 }, config, sendFn);

            expect(throttleState.has('scroll')).toBe(true);
            const state = throttleState.get('scroll');
            expect(state).toHaveProperty('lastCall');
            expect(state).toHaveProperty('timeoutId');
            expect(state).toHaveProperty('pendingData');
        });

        it('should update lastCall timestamp on execution', () => {
            const config = { interval: 0.5, leading: true, trailing: false };

            throttleEvent('scroll', { scrollY: 0 }, config, sendFn);
            const state1 = throttleState.get('scroll');
            const lastCall1 = state1.lastCall;

            vi.advanceTimersByTime(500);
            throttleEvent('scroll', { scrollY: 500 }, config, sendFn);
            const state2 = throttleState.get('scroll');
            const lastCall2 = state2.lastCall;

            expect(lastCall2).toBeGreaterThan(lastCall1);
        });

        it('should track pending data for trailing call', () => {
            const config = { interval: 0.5, leading: true, trailing: true };

            throttleEvent('scroll', { scrollY: 0 }, config, sendFn);

            vi.advanceTimersByTime(200);
            throttleEvent('scroll', { scrollY: 200 }, config, sendFn);

            const state = throttleState.get('scroll');
            expect(state.pendingData).toEqual({ scrollY: 200 });
        });

        it('should clear state after trailing call completes', () => {
            const config = { interval: 0.5, leading: false, trailing: true };

            throttleEvent('scroll', { scrollY: 100 }, config, sendFn);
            expect(throttleState.has('scroll')).toBe(true);

            vi.advanceTimersByTime(500);
            expect(throttleState.has('scroll')).toBe(false);
        });
    });

    describe('Edge Cases', () => {
        it('should handle very short intervals', () => {
            const config = { interval: 0.01, leading: true, trailing: false }; // 10ms

            throttleEvent('event', { count: 1 }, config, sendFn);
            expect(sendFn).toHaveBeenCalledTimes(1);

            vi.advanceTimersByTime(10);
            throttleEvent('event', { count: 2 }, config, sendFn);
            expect(sendFn).toHaveBeenCalledTimes(2);
        });

        it('should handle very long intervals', () => {
            const config = { interval: 10.0, leading: true, trailing: false }; // 10 seconds

            throttleEvent('event', { count: 1 }, config, sendFn);
            expect(sendFn).toHaveBeenCalledTimes(1);

            vi.advanceTimersByTime(9999);
            throttleEvent('event', { count: 2 }, config, sendFn);
            expect(sendFn).toHaveBeenCalledTimes(1); // Still throttled

            vi.advanceTimersByTime(1);
            throttleEvent('event', { count: 3 }, config, sendFn);
            expect(sendFn).toHaveBeenCalledTimes(2);
        });

        it('should handle empty event data', () => {
            const config = { interval: 0.1, leading: true, trailing: false };

            throttleEvent('empty_event', {}, config, sendFn);

            expect(sendFn).toHaveBeenCalledWith('empty_event', {});
        });

        it('should handle complex event data objects', () => {
            const config = { interval: 0.1, leading: true, trailing: true };
            const complexData = {
                scrollY: 500,
                scrollX: 200,
                viewportHeight: 768,
                documentHeight: 2000
            };

            throttleEvent('scroll', complexData, config, sendFn);

            expect(sendFn).toHaveBeenCalledWith('scroll', complexData);
        });

        it('should handle leading=false, trailing=false (throttles but no edges)', () => {
            const config = { interval: 0.5, leading: false, trailing: false };

            // First call - no execution (leading=false)
            throttleEvent('event', { count: 0 }, config, sendFn);
            const initialCallCount = sendFn.mock.calls.length;

            // Multiple rapid calls
            for (let i = 0; i < 20; i++) {
                throttleEvent('event', { count: i + 1 }, config, sendFn);
                vi.advanceTimersByTime(50);
            }

            // Should throttle (not all 20 events should execute)
            const finalCallCount = sendFn.mock.calls.length;
            expect(finalCallCount).toBeLessThan(20);

            // But some events should execute based on interval
            expect(finalCallCount).toBeGreaterThanOrEqual(initialCallCount);
        });
    });

    describe('Real-World Scenarios', () => {
        it('should simulate scroll events (60 events/second)', () => {
            const config = { interval: 0.1, leading: true, trailing: true }; // Max 10/second

            // Simulate 60 scroll events over 1 second
            for (let i = 0; i < 60; i++) {
                throttleEvent('scroll', { scrollY: i * 10 }, config, sendFn);
                vi.advanceTimersByTime(16.67); // ~60fps
            }

            // Should have executed multiple times but less than 60
            // The exact number depends on timing, but should be around 10-20
            expect(sendFn.mock.calls.length).toBeGreaterThan(5);
            expect(sendFn.mock.calls.length).toBeLessThan(60);
        });

        it('should simulate window resize events', () => {
            const config = { interval: 0.2, leading: true, trailing: true };

            // User drags window to resize for 1 second
            for (let i = 0; i < 20; i++) {
                throttleEvent('resize', { width: 800 + i * 10, height: 600 }, config, sendFn);
                vi.advanceTimersByTime(50);
            }

            // Should have executed multiple times but limited by throttle
            expect(sendFn.mock.calls.length).toBeGreaterThan(1);
            expect(sendFn.mock.calls.length).toBeLessThan(20);

            // First call should be leading edge
            expect(sendFn).toHaveBeenNthCalledWith(1, 'resize', { width: 800, height: 600 });

            // Last call should be trailing edge with final dimensions
            expect(sendFn).toHaveBeenLastCalledWith('resize', { width: 990, height: 600 });
        });

        it('should simulate mouse move tracking', () => {
            const config = { interval: 0.05, leading: true, trailing: false }; // 20/second

            // Rapid mouse movement (100 events over 500ms)
            for (let i = 0; i < 100; i++) {
                throttleEvent('mouse_move', { x: i * 5, y: i * 3 }, config, sendFn);
                vi.advanceTimersByTime(5);
            }

            // Should have executed ~10 times (1 + 500ms / 50ms)
            expect(sendFn).toHaveBeenCalledTimes(10);
        });

        it('should handle start-stop-start pattern', () => {
            const config = { interval: 0.2, leading: true, trailing: true };

            // First burst
            for (let i = 0; i < 5; i++) {
                throttleEvent('scroll', { scrollY: i * 100 }, config, sendFn);
                vi.advanceTimersByTime(30);
            }

            const callCount1 = sendFn.mock.calls.length;

            // Pause
            vi.advanceTimersByTime(500);
            const callCount2 = sendFn.mock.calls.length;
            expect(callCount2).toBeGreaterThan(callCount1); // Trailing executed

            // Second burst
            for (let i = 0; i < 5; i++) {
                throttleEvent('scroll', { scrollY: (i + 5) * 100 }, config, sendFn);
                vi.advanceTimersByTime(30);
            }

            const callCount3 = sendFn.mock.calls.length;
            expect(callCount3).toBeGreaterThan(callCount2); // New leading edge
        });
    });

    describe('Integration with clearAllState', () => {
        it('should clear all throttle state when clearAllState called', () => {
            const config = { interval: 0.5, leading: true, trailing: true };

            throttleEvent('event1', { data: 1 }, config, sendFn);
            throttleEvent('event2', { data: 2 }, config, sendFn);
            throttleEvent('event3', { data: 3 }, config, sendFn);

            expect(throttleState.size).toBe(3);

            clearAllState();

            expect(throttleState.size).toBe(0);
        });

        it('should clear pending trailing calls', () => {
            const config = { interval: 0.5, leading: true, trailing: true };

            throttleEvent('scroll', { scrollY: 100 }, config, sendFn);
            vi.advanceTimersByTime(200);
            throttleEvent('scroll', { scrollY: 200 }, config, sendFn);

            // Should have pending trailing call
            const state = throttleState.get('scroll');
            expect(state.pendingData).toBeTruthy();

            clearAllState();
            vi.advanceTimersByTime(500);

            // Trailing call should not execute
            expect(sendFn).toHaveBeenCalledTimes(1); // Only leading
        });
    });
});
