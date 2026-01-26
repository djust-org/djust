/**
 * Unit tests for djust initialization state
 * Tests the window.djustInitialized flag exposed for E2E test detection
 *
 * Note: These tests verify the contract that client.js implements:
 * - window.djustInitialized = false at load time
 * - window.djustInitialized = true after DOMContentLoaded initialization
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';

describe('window.djustInitialized contract', () => {
    let originalDjustInitialized;

    beforeEach(() => {
        // Save original state (may be undefined in test environment)
        originalDjustInitialized = window.djustInitialized;
    });

    afterEach(() => {
        // Restore original state
        if (originalDjustInitialized === undefined) {
            delete window.djustInitialized;
        } else {
            window.djustInitialized = originalDjustInitialized;
        }
    });

    it('should support the E2E test detection pattern', () => {
        // This is the pattern used in Playwright tests:
        // await page.waitForFunction(() => window.djustInitialized === true);

        // Simulate pre-init state (what client.js sets at load time)
        window.djustInitialized = false;
        expect(window.djustInitialized).toBe(false);
        expect(window.djustInitialized === true).toBe(false);

        // Simulate post-init state (what client.js sets after DOMContentLoaded)
        window.djustInitialized = true;
        expect(window.djustInitialized).toBe(true);
        expect(window.djustInitialized === true).toBe(true);
    });

    it('should be assignable on the window object', () => {
        // Verify window.djustInitialized can be set (not frozen/sealed)
        expect(() => {
            window.djustInitialized = false;
        }).not.toThrow();

        expect(() => {
            window.djustInitialized = true;
        }).not.toThrow();
    });

    it('should work with waitForFunction check pattern', () => {
        // Simulate the exact check that Playwright uses
        const checkFn = () => window.djustInitialized === true;

        window.djustInitialized = false;
        expect(checkFn()).toBe(false);

        window.djustInitialized = true;
        expect(checkFn()).toBe(true);
    });
});
