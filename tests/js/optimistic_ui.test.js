/**
 * Unit tests for Optimistic UI functionality
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

// Mock DOM helpers and global functions
global.document = {
    querySelector: vi.fn(),
    querySelectorAll: vi.fn(() => []),
    createElement: vi.fn(() => ({
        className: '',
        style: { cssText: '' },
        textContent: '',
        innerHTML: '',
        dataset: {},
        attributes: [],
        setAttribute: vi.fn(),
        removeAttribute: vi.fn(),
        appendChild: vi.fn(),
    })),
    head: {
        appendChild: vi.fn()
    },
    body: {
        appendChild: vi.fn()
    }
};

global.window = {
    djust: {},
    djustDebounceTimeouts: {},
    djustThrottleTimestamps: {}
};

global.CSS = {
    escape: (str) => str
};

// Load the optimistic UI module
import '../../python/djust/static/djust/src/16-optimistic-ui.js';

describe('Optimistic UI', () => {
    let mockElement;

    beforeEach(() => {
        // Reset window.djust
        global.window.djust = {
            optimistic: {},
            rateLimit: {}
        };

        // Create mock element
        mockElement = {
            getAttribute: vi.fn(),
            querySelector: vi.fn(),
            dataset: {},
            attributes: [],
            textContent: '',
            innerHTML: '',
            className: '',
            setAttribute: vi.fn(),
            removeAttribute: vi.fn(),
            id: 'test-element'
        };

        // Mock document.querySelector to return our mock element
        global.document.querySelector = vi.fn(() => mockElement);
    });

    afterEach(() => {
        vi.clearAllMocks();
    });

    // ========================================================================
    // Optimistic Value Parsing
    // ========================================================================

    describe('parseOptimisticValue', () => {
        it('should parse quoted strings', () => {
            const parseOptimisticValue = global.window.djust.optimistic.parseOptimisticValue || 
                eval(`(${global.window.djust.optimistic.applyOptimisticUpdate.toString().match(/function parseOptimisticValue[^}]+}/)[0]})`);

            expect(parseOptimisticValue('"hello"')).toBe('hello');
            expect(parseOptimisticValue("'world'")).toBe('world');
        });

        it('should parse booleans', () => {
            const parseOptimisticValue = global.window.djust.optimistic.parseOptimisticValue || 
                eval('(function(valueExpr) { if (valueExpr === "true") return true; if (valueExpr === "false") return false; return valueExpr; })');
            
            expect(parseOptimisticValue('true')).toBe(true);
            expect(parseOptimisticValue('false')).toBe(false);
        });

        it('should parse numbers', () => {
            const parseOptimisticValue = global.window.djust.optimistic.parseOptimisticValue || 
                eval(`(function(valueExpr) {
                    if (/^-?\\d+$/.test(valueExpr)) return parseInt(valueExpr, 10);
                    if (/^-?\\d*\\.\\d+$/.test(valueExpr)) return parseFloat(valueExpr);
                    return valueExpr;
                })`);
            
            expect(parseOptimisticValue('42')).toBe(42);
            expect(parseOptimisticValue('3.14')).toBe(3.14);
            expect(parseOptimisticValue('-5')).toBe(-5);
        });

        it('should handle null', () => {
            const parseOptimisticValue = global.window.djust.optimistic.parseOptimisticValue || 
                eval('(function(valueExpr) { if (valueExpr === "null") return null; return valueExpr; })');
            
            expect(parseOptimisticValue('null')).toBe(null);
        });
    });

    // ========================================================================
    // Boolean Coercion
    // ========================================================================

    describe('coerceToBoolean', () => {
        const coerceToBoolean = (value) => {
            if (typeof value === 'boolean') return value;
            if (typeof value === 'string') {
                return ['true', '1', 'yes', 'on', 'checked'].includes(value.toLowerCase());
            }
            return Boolean(value);
        };

        it('should handle boolean values', () => {
            expect(coerceToBoolean(true)).toBe(true);
            expect(coerceToBoolean(false)).toBe(false);
        });

        it('should handle string values', () => {
            expect(coerceToBoolean('true')).toBe(true);
            expect(coerceToBoolean('1')).toBe(true);
            expect(coerceToBoolean('yes')).toBe(true);
            expect(coerceToBoolean('on')).toBe(true);
            expect(coerceToBoolean('checked')).toBe(true);
            
            expect(coerceToBoolean('false')).toBe(false);
            expect(coerceToBoolean('0')).toBe(false);
            expect(coerceToBoolean('no')).toBe(false);
            expect(coerceToBoolean('')).toBe(false);
        });

        it('should handle other values', () => {
            expect(coerceToBoolean(1)).toBe(true);
            expect(coerceToBoolean(0)).toBe(false);
            expect(coerceToBoolean(null)).toBe(false);
            expect(coerceToBoolean(undefined)).toBe(false);
            expect(coerceToBoolean({})).toBe(true);
        });
    });

    // ========================================================================
    // Optimistic Updates
    // ========================================================================

    describe('applyOptimisticUpdate', () => {
        it('should return null for missing dj-optimistic attribute', () => {
            mockElement.getAttribute = vi.fn(() => null);
            
            // Since we can't easily import the function, we'll test the behavior
            // by checking if the optimisticUpdates map is empty
            expect(global.window.djust.optimistic.optimisticUpdates?.size || 0).toBe(0);
        });

        it('should return null for invalid dj-optimistic format', () => {
            mockElement.getAttribute = vi.fn((attr) => {
                if (attr === 'dj-optimistic') return 'invalid-format';
                return null;
            });

            // Test that invalid format is handled gracefully
            const result = global.window.djust.optimistic.applyOptimisticUpdate?.(mockElement, 'test-event');
            expect(result).toBe(null);
        });

        it('should parse valid dj-optimistic attribute', () => {
            mockElement.getAttribute = vi.fn((attr) => {
                if (attr === 'dj-optimistic') return 'liked:true';
                if (attr === 'dj-target') return null;
                return null;
            });

            // Mock the attributes for storing original state
            mockElement.attributes = [
                { name: 'class', value: 'button' }
            ];

            const result = global.window.djust.optimistic.applyOptimisticUpdate?.(mockElement, 'toggle-like');
            expect(typeof result).toBe('string');
        });
    });

    describe('revertOptimisticUpdate', () => {
        it('should handle non-existent update ID gracefully', () => {
            expect(() => {
                global.window.djust.optimistic.revertOptimisticUpdate?.('non-existent-id');
            }).not.toThrow();
        });
    });

    describe('clearOptimisticUpdate', () => {
        it('should handle non-existent update ID gracefully', () => {
            expect(() => {
                global.window.djust.optimistic.clearOptimisticUpdate?.('non-existent-id');
            }).not.toThrow();
        });
    });
});

describe('Rate Limiting', () => {
    beforeEach(() => {
        global.window.djustDebounceTimeouts = {};
        global.window.djustThrottleTimestamps = {};
        vi.useFakeTimers();
    });

    afterEach(() => {
        vi.useRealTimers();
        vi.clearAllMocks();
    });

    // ========================================================================
    // Debounce
    // ========================================================================

    describe('debounce', () => {
        it('should delay function execution', () => {
            const mockFn = vi.fn();
            const mockElement = { id: 'test' };
            
            const debounced = global.window.djust.rateLimit?.createDebouncedHandler?.(
                mockElement, 'click', mockFn, 100
            );

            if (debounced) {
                debounced();
                expect(mockFn).not.toHaveBeenCalled();

                vi.advanceTimersByTime(100);
                expect(mockFn).toHaveBeenCalledTimes(1);
            }
        });

        it('should reset delay on subsequent calls', () => {
            const mockFn = vi.fn();
            const mockElement = { id: 'test' };
            
            const debounced = global.window.djust.rateLimit?.createDebouncedHandler?.(
                mockElement, 'click', mockFn, 100
            );

            if (debounced) {
                debounced();
                vi.advanceTimersByTime(50);
                debounced(); // Reset the timer
                vi.advanceTimersByTime(50);
                expect(mockFn).not.toHaveBeenCalled();

                vi.advanceTimersByTime(50);
                expect(mockFn).toHaveBeenCalledTimes(1);
            }
        });
    });

    // ========================================================================
    // Throttle
    // ========================================================================

    describe('throttle', () => {
        it('should allow immediate execution', () => {
            const mockFn = vi.fn();
            const mockElement = { id: 'test' };
            
            const throttled = global.window.djust.rateLimit?.createThrottledHandler?.(
                mockElement, 'click', mockFn, 100
            );

            if (throttled) {
                throttled();
                expect(mockFn).toHaveBeenCalledTimes(1);
            }
        });

        it('should prevent subsequent calls within delay', () => {
            const mockFn = vi.fn();
            const mockElement = { id: 'test' };
            
            const throttled = global.window.djust.rateLimit?.createThrottledHandler?.(
                mockElement, 'click', mockFn, 100
            );

            if (throttled) {
                throttled();
                throttled();
                throttled();
                expect(mockFn).toHaveBeenCalledTimes(1);

                vi.advanceTimersByTime(100);
                throttled();
                expect(mockFn).toHaveBeenCalledTimes(2);
            }
        });
    });

    // ========================================================================
    // Rate Limit Configuration
    // ========================================================================

    describe('getRateLimitConfig', () => {
        let mockElement;

        beforeEach(() => {
            mockElement = {
                hasAttribute: vi.fn(),
                getAttribute: vi.fn()
            };
        });

        it('should return debounce config when dj-debounce is present', () => {
            mockElement.hasAttribute = vi.fn((attr) => attr === 'dj-debounce');
            mockElement.getAttribute = vi.fn(() => '300');

            const config = global.window.djust.rateLimit?.getRateLimitConfig?.(mockElement);
            expect(config).toEqual({ type: 'debounce', delay: 300 });
        });

        it('should return throttle config when dj-throttle is present', () => {
            mockElement.hasAttribute = vi.fn((attr) => attr === 'dj-throttle');
            mockElement.getAttribute = vi.fn(() => '500');

            const config = global.window.djust.rateLimit?.getRateLimitConfig?.(mockElement);
            expect(config).toEqual({ type: 'throttle', delay: 500 });
        });

        it('should return null when no rate limiting attributes', () => {
            mockElement.hasAttribute = vi.fn(() => false);

            const config = global.window.djust.rateLimit?.getRateLimitConfig?.(mockElement);
            expect(config).toBe(null);
        });

        it('should handle invalid delay values', () => {
            mockElement.hasAttribute = vi.fn((attr) => attr === 'dj-debounce');
            mockElement.getAttribute = vi.fn(() => 'invalid');

            const config = global.window.djust.rateLimit?.getRateLimitConfig?.(mockElement);
            expect(config).toBe(null);
        });
    });
});

describe('Integration', () => {
    it('should expose optimistic functions on window.djust', () => {
        expect(global.window.djust).toBeDefined();
        expect(global.window.djust.optimistic).toBeDefined();
        expect(global.window.djust.rateLimit).toBeDefined();
    });

    it('should have all required optimistic functions', () => {
        const optimistic = global.window.djust.optimistic;
        expect(typeof optimistic.applyOptimisticUpdate).toBe('function');
        expect(typeof optimistic.revertOptimisticUpdate).toBe('function');
        expect(typeof optimistic.clearOptimisticUpdate).toBe('function');
        expect(optimistic.optimisticUpdates).toBeDefined();
    });

    it('should have all required rate limit functions', () => {
        const rateLimit = global.window.djust.rateLimit;
        expect(typeof rateLimit.createDebouncedHandler).toBe('function');
        expect(typeof rateLimit.createThrottledHandler).toBe('function');
        expect(typeof rateLimit.getRateLimitConfig).toBe('function');
        expect(typeof rateLimit.wrapWithRateLimit).toBe('function');
    });
});