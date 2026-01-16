/**
 * Unit tests for @client_state decorator and StateBus
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import {
    StateBus,
    globalStateBus,
    clientStateEvent,
    clearAllState
} from '../../python/djust/static/djust/decorators.js';

describe('StateBus', () => {
    let bus;

    beforeEach(() => {
        bus = new StateBus();
    });

    // ========================================================================
    // Basic Get/Set
    // ========================================================================

    describe('get/set', () => {
        it('should set and get state values', () => {
            bus.set('count', 5);
            expect(bus.get('count')).toBe(5);
        });

        it('should return undefined for non-existent keys', () => {
            expect(bus.get('nonexistent')).toBeUndefined();
        });

        it('should overwrite existing values', () => {
            bus.set('count', 5);
            bus.set('count', 10);
            expect(bus.get('count')).toBe(10);
        });

        it('should handle different value types', () => {
            bus.set('string', 'hello');
            bus.set('number', 42);
            bus.set('boolean', true);
            bus.set('object', { foo: 'bar' });
            bus.set('array', [1, 2, 3]);
            bus.set('null', null);

            expect(bus.get('string')).toBe('hello');
            expect(bus.get('number')).toBe(42);
            expect(bus.get('boolean')).toBe(true);
            expect(bus.get('object')).toEqual({ foo: 'bar' });
            expect(bus.get('array')).toEqual([1, 2, 3]);
            expect(bus.get('null')).toBe(null);
        });
    });

    // ========================================================================
    // Subscribe/Notify
    // ========================================================================

    describe('subscribe', () => {
        it('should notify subscriber when state changes', () => {
            const callback = vi.fn();
            bus.subscribe('count', callback);

            bus.set('count', 5);

            expect(callback).toHaveBeenCalledWith(5, undefined);
        });

        it('should pass old value to subscribers', () => {
            const callback = vi.fn();
            bus.set('count', 5);
            bus.subscribe('count', callback);

            bus.set('count', 10);

            expect(callback).toHaveBeenCalledWith(10, 5);
        });

        it('should notify multiple subscribers', () => {
            const callback1 = vi.fn();
            const callback2 = vi.fn();
            const callback3 = vi.fn();

            bus.subscribe('count', callback1);
            bus.subscribe('count', callback2);
            bus.subscribe('count', callback3);

            bus.set('count', 5);

            expect(callback1).toHaveBeenCalledWith(5, undefined);
            expect(callback2).toHaveBeenCalledWith(5, undefined);
            expect(callback3).toHaveBeenCalledWith(5, undefined);
        });

        it('should only notify subscribers of specific key', () => {
            const callback1 = vi.fn();
            const callback2 = vi.fn();

            bus.subscribe('key1', callback1);
            bus.subscribe('key2', callback2);

            bus.set('key1', 'value1');

            expect(callback1).toHaveBeenCalledWith('value1', undefined);
            expect(callback2).not.toHaveBeenCalled();
        });

        it('should return unsubscribe function', () => {
            const callback = vi.fn();
            const unsubscribe = bus.subscribe('count', callback);

            bus.set('count', 5);
            expect(callback).toHaveBeenCalledTimes(1);

            unsubscribe();

            bus.set('count', 10);
            expect(callback).toHaveBeenCalledTimes(1); // Not called again
        });

        it('should handle errors in subscriber callbacks', () => {
            const errorCallback = vi.fn(() => {
                throw new Error('Subscriber error');
            });
            const goodCallback = vi.fn();

            const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

            bus.subscribe('count', errorCallback);
            bus.subscribe('count', goodCallback);

            bus.set('count', 5);

            expect(errorCallback).toHaveBeenCalled();
            expect(goodCallback).toHaveBeenCalled();
            expect(consoleSpy).toHaveBeenCalled();

            consoleSpy.mockRestore();
        });
    });

    // ========================================================================
    // Clear
    // ========================================================================

    describe('clear', () => {
        it('should clear all state', () => {
            bus.set('key1', 'value1');
            bus.set('key2', 'value2');

            bus.clear();

            expect(bus.get('key1')).toBeUndefined();
            expect(bus.get('key2')).toBeUndefined();
        });

        it('should clear all subscribers', () => {
            const callback = vi.fn();
            bus.subscribe('count', callback);

            bus.clear();

            bus.set('count', 5);

            expect(callback).not.toHaveBeenCalled();
        });
    });

    // ========================================================================
    // GetAll (debugging)
    // ========================================================================

    describe('getAll', () => {
        it('should return all state as plain object', () => {
            bus.set('key1', 'value1');
            bus.set('key2', 42);
            bus.set('key3', true);

            const state = bus.getAll();

            expect(state).toEqual({
                key1: 'value1',
                key2: 42,
                key3: true
            });
        });

        it('should return empty object when no state', () => {
            const state = bus.getAll();
            expect(state).toEqual({});
        });
    });

    // ========================================================================
    // Multiple Subscriptions
    // ========================================================================

    describe('multiple subscriptions', () => {
        it('should allow same callback to subscribe to multiple keys', () => {
            const callback = vi.fn();

            bus.subscribe('key1', callback);
            bus.subscribe('key2', callback);

            bus.set('key1', 'value1');
            bus.set('key2', 'value2');

            expect(callback).toHaveBeenCalledWith('value1', undefined);
            expect(callback).toHaveBeenCalledWith('value2', undefined);
            expect(callback).toHaveBeenCalledTimes(2);
        });

        it('should unsubscribe independently', () => {
            const callback = vi.fn();

            const unsub1 = bus.subscribe('key1', callback);
            const unsub2 = bus.subscribe('key2', callback);

            unsub1();

            bus.set('key1', 'value1');
            bus.set('key2', 'value2');

            expect(callback).toHaveBeenCalledTimes(1);
            expect(callback).toHaveBeenCalledWith('value2', undefined);
        });
    });
});

describe('globalStateBus', () => {
    beforeEach(() => {
        clearAllState();
    });

    it('should be a singleton instance', () => {
        expect(globalStateBus).toBeInstanceOf(StateBus);
    });

    it('should be cleared by clearAllState', () => {
        globalStateBus.set('test', 'value');
        expect(globalStateBus.get('test')).toBe('value');

        clearAllState();

        expect(globalStateBus.get('test')).toBeUndefined();
    });
});

describe('@client_state decorator', () => {
    beforeEach(() => {
        clearAllState();
    });

    // ========================================================================
    // Basic Functionality
    // ========================================================================

    describe('clientStateEvent', () => {
        it('should update StateBus and call server', async () => {
            const sendFn = vi.fn().mockResolvedValue({ success: true });
            const config = { state_key: 'search_query' };
            const eventData = { value: 'test query' };

            await clientStateEvent('search', eventData, config, sendFn);

            expect(globalStateBus.get('search_query')).toBe('test query');
            expect(sendFn).toHaveBeenCalledWith('search', eventData);
        });

        it('should extract value from event data', async () => {
            const sendFn = vi.fn().mockResolvedValue({});
            const config = { state_key: 'query' };

            await clientStateEvent('search', { value: 'hello' }, config, sendFn);

            expect(globalStateBus.get('query')).toBe('hello');
        });

        it('should extract checked from checkbox events', async () => {
            const sendFn = vi.fn().mockResolvedValue({});
            const config = { state_key: 'active' };

            await clientStateEvent('toggle', { checked: true }, config, sendFn);

            expect(globalStateBus.get('active')).toBe(true);
        });

        it('should use entire event data if no value/checked', async () => {
            const sendFn = vi.fn().mockResolvedValue({});
            const config = { state_key: 'data' };
            const eventData = { foo: 'bar', baz: 123 };

            await clientStateEvent('update', eventData, config, sendFn);

            expect(globalStateBus.get('data')).toEqual(eventData);
        });

        it('should require state_key in config', async () => {
            const sendFn = vi.fn().mockResolvedValue({});
            const config = {}; // Missing state_key
            const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

            await clientStateEvent('search', { value: 'test' }, config, sendFn);

            expect(consoleSpy).toHaveBeenCalledWith('[LiveView:client_state] Missing state_key in config');
            expect(sendFn).toHaveBeenCalled();

            consoleSpy.mockRestore();
        });

        it('should still call server if state_key missing', async () => {
            const sendFn = vi.fn().mockResolvedValue({});
            const config = {}; // Missing state_key
            vi.spyOn(console, 'error').mockImplementation(() => {});

            await clientStateEvent('search', { value: 'test' }, config, sendFn);

            expect(sendFn).toHaveBeenCalledWith('search', { value: 'test' });
        });
    });

    // ========================================================================
    // Subscriber Coordination
    // ========================================================================

    describe('subscriber coordination', () => {
        it('should notify subscribers when state changes', async () => {
            const callback = vi.fn();
            const sendFn = vi.fn().mockResolvedValue({});
            const config = { state_key: 'cart_count' };

            globalStateBus.subscribe('cart_count', callback);

            await clientStateEvent('add_to_cart', { value: 3 }, config, sendFn);

            expect(callback).toHaveBeenCalledWith(3, undefined);
        });

        it('should coordinate between multiple components', async () => {
            const headerCallback = vi.fn();
            const sidebarCallback = vi.fn();
            const sendFn = vi.fn().mockResolvedValue({});
            const config = { state_key: 'cart_count' };

            // Two components subscribe to same state
            globalStateBus.subscribe('cart_count', headerCallback);
            globalStateBus.subscribe('cart_count', sidebarCallback);

            // One component triggers update
            await clientStateEvent('add_to_cart', { value: 5 }, config, sendFn);

            // Both components are notified
            expect(headerCallback).toHaveBeenCalledWith(5, undefined);
            expect(sidebarCallback).toHaveBeenCalledWith(5, undefined);
        });

        it('should pass old and new values to subscribers', async () => {
            const callback = vi.fn();
            const sendFn = vi.fn().mockResolvedValue({});
            const config = { state_key: 'count' };

            globalStateBus.subscribe('count', callback);

            // Set initial value
            await clientStateEvent('update', { value: 5 }, config, sendFn);
            expect(callback).toHaveBeenCalledWith(5, undefined);

            // Update value
            await clientStateEvent('update', { value: 10 }, config, sendFn);
            expect(callback).toHaveBeenCalledWith(10, 5);
        });
    });

    // ========================================================================
    // Multiple State Keys
    // ========================================================================

    describe('multiple state keys', () => {
        it('should handle different state keys independently', async () => {
            const callback1 = vi.fn();
            const callback2 = vi.fn();
            const sendFn = vi.fn().mockResolvedValue({});

            globalStateBus.subscribe('key1', callback1);
            globalStateBus.subscribe('key2', callback2);

            await clientStateEvent('update1', { value: 'value1' }, { state_key: 'key1' }, sendFn);

            expect(callback1).toHaveBeenCalledWith('value1', undefined);
            expect(callback2).not.toHaveBeenCalled();
        });

        it('should maintain separate state for each key', async () => {
            const sendFn = vi.fn().mockResolvedValue({});

            await clientStateEvent('update', { value: 'query1' }, { state_key: 'search_query' }, sendFn);
            await clientStateEvent('update', { value: 'filter1' }, { state_key: 'filter' }, sendFn);

            expect(globalStateBus.get('search_query')).toBe('query1');
            expect(globalStateBus.get('filter')).toBe('filter1');
        });
    });

    // ========================================================================
    // Error Handling
    // ========================================================================

    describe('error handling', () => {
        it('should propagate server errors to caller', async () => {
            const sendFn = vi.fn().mockRejectedValue(new Error('Server error'));
            const config = { state_key: 'query' };

            await expect(clientStateEvent('search', { value: 'test' }, config, sendFn))
                .rejects.toThrow('Server error');

            // State should still be updated despite server error
            expect(globalStateBus.get('query')).toBe('test');
        });

        it('should update StateBus even if server call fails', async () => {
            const sendFn = vi.fn().mockRejectedValue(new Error('Network error'));
            const config = { state_key: 'count' };

            try {
                await clientStateEvent('update', { value: 5 }, config, sendFn);
            } catch (e) {
                // Expected error
            }

            // StateBus should be updated immediately
            expect(globalStateBus.get('count')).toBe(5);
        });
    });
});
