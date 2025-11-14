/**
 * Unit tests for @cache decorator
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import {
    resultCache,
    cacheEvent,
    generateCacheKey,
    clearCache,
    cleanupExpiredCache,
    clearAllState
} from '../../python/djust/static/djust/decorators.js';

describe('@cache decorator', () => {
    beforeEach(() => {
        clearAllState();
        vi.clearAllTimers();
    });

    // ========================================================================
    // Cache Key Generation
    // ========================================================================

    describe('generateCacheKey', () => {
        it('should generate simple key without params', () => {
            const key = generateCacheKey('search', {}, []);
            expect(key).toBe('search');
        });

        it('should generate key with single param', () => {
            const key = generateCacheKey('search', { query: 'test' }, ['query']);
            expect(key).toBe('search:test');
        });

        it('should generate key with multiple params', () => {
            const key = generateCacheKey('search', { query: 'test', page: 2 }, ['query', 'page']);
            expect(key).toBe('search:test:2');
        });

        it('should handle missing params', () => {
            const key = generateCacheKey('search', { query: 'test' }, ['query', 'page']);
            expect(key).toBe('search:test:');
        });

        it('should handle null/undefined params', () => {
            const key = generateCacheKey('search', { query: null, page: undefined }, ['query', 'page']);
            expect(key).toBe('search::');
        });

        it('should convert non-string values to strings', () => {
            const key = generateCacheKey('search', { page: 2, active: true }, ['page', 'active']);
            expect(key).toBe('search:2:true');
        });
    });

    // ========================================================================
    // Cache Hit/Miss
    // ========================================================================

    describe('cacheEvent', () => {
        it('should call server on cache miss', async () => {
            const sendFn = vi.fn().mockResolvedValue({ data: 'result' });
            const config = { ttl: 60, key_params: [] };

            const result = await cacheEvent('search', { query: 'test' }, config, sendFn);

            expect(sendFn).toHaveBeenCalledWith('search', { query: 'test' });
            expect(result).toEqual({ data: 'result' });
        });

        it('should return cached result on cache hit', async () => {
            const sendFn = vi.fn().mockResolvedValue({ data: 'result' });
            const config = { ttl: 60, key_params: [] };

            // First call - cache miss
            await cacheEvent('search', { query: 'test' }, config, sendFn);
            expect(sendFn).toHaveBeenCalledTimes(1);

            // Second call - cache hit
            const result = await cacheEvent('search', { query: 'test' }, config, sendFn);
            expect(sendFn).toHaveBeenCalledTimes(1); // Not called again
            expect(result).toEqual({ data: 'result' });
        });

        it('should store result in cache', async () => {
            const sendFn = vi.fn().mockResolvedValue({ data: 'result' });
            const config = { ttl: 60, key_params: [] };

            await cacheEvent('search', { query: 'test' }, config, sendFn);

            expect(resultCache.has('search')).toBe(true);
            const cached = resultCache.get('search');
            expect(cached.result).toEqual({ data: 'result' });
            expect(cached.expiresAt).toBeGreaterThan(Date.now());
        });

        it('should respect TTL', async () => {
            const sendFn = vi.fn().mockResolvedValue({ data: 'result' });
            const config = { ttl: 60, key_params: [] };

            await cacheEvent('search', { query: 'test' }, config, sendFn);

            const cached = resultCache.get('search');
            const expectedExpiry = Date.now() + (60 * 1000);
            expect(cached.expiresAt).toBeGreaterThanOrEqual(expectedExpiry - 100);
            expect(cached.expiresAt).toBeLessThanOrEqual(expectedExpiry + 100);
        });

        it('should use custom TTL', async () => {
            const sendFn = vi.fn().mockResolvedValue({ data: 'result' });
            const config = { ttl: 120, key_params: [] };

            await cacheEvent('search', { query: 'test' }, config, sendFn);

            const cached = resultCache.get('search');
            const expectedExpiry = Date.now() + (120 * 1000);
            expect(cached.expiresAt).toBeGreaterThanOrEqual(expectedExpiry - 100);
            expect(cached.expiresAt).toBeLessThanOrEqual(expectedExpiry + 100);
        });

        it('should use default TTL of 60 seconds', async () => {
            const sendFn = vi.fn().mockResolvedValue({ data: 'result' });
            const config = { key_params: [] }; // No TTL specified

            await cacheEvent('search', { query: 'test' }, config, sendFn);

            const cached = resultCache.get('search');
            const expectedExpiry = Date.now() + (60 * 1000);
            expect(cached.expiresAt).toBeGreaterThanOrEqual(expectedExpiry - 100);
            expect(cached.expiresAt).toBeLessThanOrEqual(expectedExpiry + 100);
        });
    });

    // ========================================================================
    // Cache with Key Parameters
    // ========================================================================

    describe('cacheEvent with key_params', () => {
        it('should cache separately for different params', async () => {
            const sendFn = vi.fn()
                .mockResolvedValueOnce({ data: 'result1' })
                .mockResolvedValueOnce({ data: 'result2' });
            const config = { ttl: 60, key_params: ['query'] };

            // First query
            const result1 = await cacheEvent('search', { query: 'test1' }, config, sendFn);
            expect(result1).toEqual({ data: 'result1' });

            // Second query (different param)
            const result2 = await cacheEvent('search', { query: 'test2' }, config, sendFn);
            expect(result2).toEqual({ data: 'result2' });

            // Both should be called
            expect(sendFn).toHaveBeenCalledTimes(2);
        });

        it('should use cached result for same params', async () => {
            const sendFn = vi.fn().mockResolvedValue({ data: 'result' });
            const config = { ttl: 60, key_params: ['query'] };

            // First call
            await cacheEvent('search', { query: 'test' }, config, sendFn);

            // Second call with same param
            const result = await cacheEvent('search', { query: 'test' }, config, sendFn);
            expect(result).toEqual({ data: 'result' });
            expect(sendFn).toHaveBeenCalledTimes(1);
        });

        it('should cache with multiple key params', async () => {
            const sendFn = vi.fn()
                .mockResolvedValueOnce({ data: 'result1' })
                .mockResolvedValueOnce({ data: 'result2' });
            const config = { ttl: 60, key_params: ['query', 'page'] };

            // First query
            await cacheEvent('search', { query: 'test', page: 1 }, config, sendFn);

            // Second query (different page)
            await cacheEvent('search', { query: 'test', page: 2 }, config, sendFn);

            expect(sendFn).toHaveBeenCalledTimes(2);
        });

        it('should hit cache with matching multi-param key', async () => {
            const sendFn = vi.fn().mockResolvedValue({ data: 'result' });
            const config = { ttl: 60, key_params: ['query', 'page'] };

            // First call
            await cacheEvent('search', { query: 'test', page: 1 }, config, sendFn);

            // Second call with same params
            await cacheEvent('search', { query: 'test', page: 1 }, config, sendFn);

            expect(sendFn).toHaveBeenCalledTimes(1);
        });
    });

    // ========================================================================
    // Cache Expiration
    // ========================================================================

    describe('cache expiration', () => {
        beforeEach(() => {
            vi.useFakeTimers();
        });

        it('should call server again after TTL expires', async () => {
            const sendFn = vi.fn()
                .mockResolvedValueOnce({ data: 'result1' })
                .mockResolvedValueOnce({ data: 'result2' });
            const config = { ttl: 60, key_params: [] };

            // First call
            await cacheEvent('search', { query: 'test' }, config, sendFn);
            expect(sendFn).toHaveBeenCalledTimes(1);

            // Advance time past TTL
            vi.advanceTimersByTime(61 * 1000);

            // Second call - should hit server
            await cacheEvent('search', { query: 'test' }, config, sendFn);
            expect(sendFn).toHaveBeenCalledTimes(2);
        });

        it('should use cache before TTL expires', async () => {
            const sendFn = vi.fn().mockResolvedValue({ data: 'result' });
            const config = { ttl: 60, key_params: [] };

            // First call
            await cacheEvent('search', { query: 'test' }, config, sendFn);

            // Advance time but stay within TTL
            vi.advanceTimersByTime(30 * 1000);

            // Second call - should use cache
            await cacheEvent('search', { query: 'test' }, config, sendFn);
            expect(sendFn).toHaveBeenCalledTimes(1);
        });
    });

    // ========================================================================
    // Cache Cleanup
    // ========================================================================

    describe('cleanupExpiredCache', () => {
        beforeEach(() => {
            vi.useFakeTimers();
        });

        it('should remove expired entries', async () => {
            const sendFn = vi.fn().mockResolvedValue({ data: 'result' });
            const config = { ttl: 60, key_params: [] };

            // Create cached entries
            await cacheEvent('search1', {}, config, sendFn);
            await cacheEvent('search2', {}, config, sendFn);

            // Advance time past TTL
            vi.advanceTimersByTime(61 * 1000);

            // Clean up
            const cleaned = cleanupExpiredCache();

            expect(cleaned).toBe(2);
            expect(resultCache.size).toBe(0);
        });

        it('should keep non-expired entries', async () => {
            const sendFn = vi.fn().mockResolvedValue({ data: 'result' });

            // Create entry with short TTL
            await cacheEvent('search1', {}, { ttl: 10, key_params: [] }, sendFn);

            // Create entry with long TTL
            await cacheEvent('search2', {}, { ttl: 120, key_params: [] }, sendFn);

            // Advance time to expire first entry only
            vi.advanceTimersByTime(11 * 1000);

            // Clean up
            const cleaned = cleanupExpiredCache();

            expect(cleaned).toBe(1);
            expect(resultCache.size).toBe(1);
            expect(resultCache.has('search2')).toBe(true);
        });

        it('should return 0 if no expired entries', async () => {
            const sendFn = vi.fn().mockResolvedValue({ data: 'result' });
            await cacheEvent('search', {}, { ttl: 60, key_params: [] }, sendFn);

            const cleaned = cleanupExpiredCache();

            expect(cleaned).toBe(0);
            expect(resultCache.size).toBe(1);
        });
    });

    // ========================================================================
    // Cache Invalidation
    // ========================================================================

    describe('clearCache', () => {
        it('should clear all cache when no event specified', async () => {
            const sendFn = vi.fn().mockResolvedValue({ data: 'result' });
            const config = { ttl: 60, key_params: [] };

            // Create multiple cached entries
            await cacheEvent('search', {}, config, sendFn);
            await cacheEvent('filter', {}, config, sendFn);

            clearCache();

            expect(resultCache.size).toBe(0);
        });

        it('should clear specific event cache', async () => {
            const sendFn = vi.fn().mockResolvedValue({ data: 'result' });
            const config = { ttl: 60, key_params: [] };

            // Create multiple cached entries
            await cacheEvent('search', {}, config, sendFn);
            await cacheEvent('filter', {}, config, sendFn);

            clearCache('search');

            expect(resultCache.size).toBe(1);
            expect(resultCache.has('search')).toBe(false);
            expect(resultCache.has('filter')).toBe(true);
        });

        it('should clear all entries with matching prefix', async () => {
            const sendFn = vi.fn().mockResolvedValue({ data: 'result' });
            const config = { ttl: 60, key_params: ['query'] };

            // Create multiple cached entries with params
            await cacheEvent('search', { query: 'test1' }, config, sendFn);
            await cacheEvent('search', { query: 'test2' }, config, sendFn);
            await cacheEvent('filter', { query: 'test' }, config, sendFn);

            clearCache('search');

            expect(resultCache.size).toBe(1);
            expect(resultCache.has('search:test1')).toBe(false);
            expect(resultCache.has('search:test2')).toBe(false);
            expect(resultCache.has('filter:test')).toBe(true);
        });
    });

    // ========================================================================
    // Error Handling
    // ========================================================================

    describe('error handling', () => {
        it('should not cache errors', async () => {
            const sendFn = vi.fn().mockRejectedValue(new Error('Server error'));
            const config = { ttl: 60, key_params: [] };

            try {
                await cacheEvent('search', { query: 'test' }, config, sendFn);
            } catch (e) {
                // Expected error
            }

            expect(resultCache.has('search')).toBe(false);
        });

        it('should propagate errors to caller', async () => {
            const sendFn = vi.fn().mockRejectedValue(new Error('Server error'));
            const config = { ttl: 60, key_params: [] };

            await expect(cacheEvent('search', {}, config, sendFn))
                .rejects.toThrow('Server error');
        });
    });

    // ========================================================================
    // Integration with clearAllState
    // ========================================================================

    describe('clearAllState', () => {
        it('should clear cache', async () => {
            const sendFn = vi.fn().mockResolvedValue({ data: 'result' });
            const config = { ttl: 60, key_params: [] };

            // Create cached entries
            await cacheEvent('search', {}, config, sendFn);

            clearAllState();

            expect(resultCache.size).toBe(0);
        });
    });
});
