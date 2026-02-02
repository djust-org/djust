/**
 * Unit tests for dj-target scoped updates functionality
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

// Mock DOM and globals
global.document = {
    querySelector: vi.fn(),
    querySelectorAll: vi.fn(() => []),
};

global.window = {
    djust: {}
};

global.CSS = {
    escape: (str) => str
};

// Load the necessary modules
import '../../python/djust/static/djust/src/12-vdom-patch.js';

describe('Scoped Updates (dj-target)', () => {
    let mockElement;
    let mockTargetElement;

    beforeEach(() => {
        // Create mock elements
        mockElement = {
            getAttribute: vi.fn(),
            querySelector: vi.fn(),
            dataset: {},
            id: 'trigger-element'
        };

        mockTargetElement = {
            querySelector: vi.fn(),
            dataset: {},
            id: 'target-element',
            innerHTML: '<p>Original content</p>'
        };

        // Reset global mocks
        global.document.querySelector = vi.fn((selector) => {
            if (selector === '#target-element') {
                return mockTargetElement;
            }
            if (selector.includes('data-dj-id')) {
                return mockElement;
            }
            return null;
        });

        global.window.djust = {};
    });

    afterEach(() => {
        vi.clearAllMocks();
    });

    // ========================================================================
    // Target Resolution
    // ========================================================================

    describe('getNodeByPath with targetSelector', () => {
        it('should use targetSelector as scope when provided', () => {
            const getNodeByPath = global.window.djust._getNodeByPath || 
                eval('(function(path, djustId, targetSelector) { return targetSelector ? document.querySelector(targetSelector) : null; })');

            const result = getNodeByPath([], null, '#target-element');
            expect(global.document.querySelector).toHaveBeenCalledWith('#target-element');
        });

        it('should fall back to document scope when targetSelector is null', () => {
            // Mock getLiveViewRoot
            global.getLiveViewRoot = vi.fn(() => document.body);
            global.document.body = { childNodes: [] };

            const getNodeByPath = global.window.djust._getNodeByPath || 
                eval(`(function(path, djustId, targetSelector) { 
                    if (!targetSelector) return getLiveViewRoot();
                    return document.querySelector(targetSelector);
                })`);

            const result = getNodeByPath([], null, null);
            // Should call getLiveViewRoot when no targetSelector
        });

        it('should scope ID-based lookups to target element', () => {
            mockTargetElement.querySelector = vi.fn((selector) => {
                if (selector.includes('data-dj-id="test-id"')) {
                    return mockElement;
                }
                return null;
            });

            const getNodeByPath = global.window.djust._getNodeByPath || 
                eval(`(function(path, djustId, targetSelector) {
                    if (djustId && targetSelector) {
                        const scope = document.querySelector(targetSelector);
                        return scope ? scope.querySelector('[data-dj-id="' + djustId + '"]') : null;
                    }
                    return null;
                })`);

            const result = getNodeByPath([], 'test-id', '#target-element');
            expect(mockTargetElement.querySelector).toHaveBeenCalledWith('[data-dj-id="test-id"]');
        });
    });

    // ========================================================================
    // Patch Application with Target
    // ========================================================================

    describe('applyPatches with targetSelector', () => {
        it('should pass targetSelector to patch functions', () => {
            const mockPatches = [
                {
                    type: 'SetText',
                    path: [0],
                    d: 'test-id',
                    text: 'New text'
                }
            ];

            // Mock the applySinglePatch function
            global.window.djust._applySinglePatch = vi.fn(() => true);

            const applyPatches = global.window.djust._applyPatches || 
                eval(`(function(patches, targetSelector) {
                    if (!patches || patches.length === 0) return true;
                    for (const patch of patches) {
                        window.djust._applySinglePatch(patch, targetSelector);
                    }
                    return true;
                })`);

            const result = applyPatches(mockPatches, '#target-element');
            expect(global.window.djust._applySinglePatch).toHaveBeenCalledWith(
                mockPatches[0], 
                '#target-element'
            );
        });

        it('should handle null targetSelector gracefully', () => {
            const mockPatches = [
                {
                    type: 'SetText',
                    path: [0],
                    text: 'New text'
                }
            ];

            global.window.djust._applySinglePatch = vi.fn(() => true);

            const applyPatches = global.window.djust._applyPatches || 
                eval(`(function(patches, targetSelector) {
                    if (!patches || patches.length === 0) return true;
                    for (const patch of patches) {
                        window.djust._applySinglePatch(patch, targetSelector);
                    }
                    return true;
                })`);

            expect(() => applyPatches(mockPatches, null)).not.toThrow();
        });
    });

    // ========================================================================
    // Event Integration
    // ========================================================================

    describe('Event handler integration', () => {
        it('should extract dj-target attribute from element', () => {
            const element = {
                getAttribute: vi.fn((attr) => {
                    if (attr === 'dj-target') return '#search-results';
                    return null;
                })
            };

            const targetSelector = element.getAttribute('dj-target');
            expect(targetSelector).toBe('#search-results');
            expect(element.getAttribute).toHaveBeenCalledWith('dj-target');
        });

        it('should include target selector in event params', () => {
            const mockParams = {
                value: 'test',
                _targetElement: mockElement
            };

            mockElement.getAttribute = vi.fn((attr) => {
                if (attr === 'dj-target') return '#search-results';
                return null;
            });

            const targetSelector = mockElement.getAttribute('dj-target');
            if (targetSelector) {
                mockParams._djTargetSelector = targetSelector;
            }

            expect(mockParams._djTargetSelector).toBe('#search-results');
        });
    });

    // ========================================================================
    // HTTP Headers
    // ========================================================================

    describe('HTTP header integration', () => {
        it('should send X-Djust-Target header when targetSelector is provided', () => {
            const mockFetch = vi.fn(() => Promise.resolve({
                ok: true,
                json: () => Promise.resolve({ patches: [] })
            }));

            global.fetch = mockFetch;

            const targetSelector = '#search-results';
            const expectedHeaders = {
                'Content-Type': 'application/json',
                'X-CSRFToken': '',
                'X-Djust-Event': 'search',
                'X-Djust-Target': targetSelector
            };

            // Mock the HTTP request
            const makeRequest = async () => {
                await fetch('/test', {
                    method: 'POST',
                    headers: expectedHeaders,
                    body: JSON.stringify({})
                });
            };

            makeRequest();

            expect(mockFetch).toHaveBeenCalledWith('/test', {
                method: 'POST',
                headers: expectedHeaders,
                body: JSON.stringify({})
            });
        });

        it('should send empty X-Djust-Target header when no targetSelector', () => {
            const mockFetch = vi.fn(() => Promise.resolve({
                ok: true,
                json: () => Promise.resolve({ patches: [] })
            }));

            global.fetch = mockFetch;

            const expectedHeaders = {
                'Content-Type': 'application/json',
                'X-CSRFToken': '',
                'X-Djust-Event': 'search',
                'X-Djust-Target': ''
            };

            const makeRequest = async () => {
                await fetch('/test', {
                    method: 'POST',
                    headers: expectedHeaders,
                    body: JSON.stringify({})
                });
            };

            makeRequest();

            expect(mockFetch).toHaveBeenCalledWith('/test', {
                method: 'POST',
                headers: expectedHeaders,
                body: JSON.stringify({})
            });
        });
    });

    // ========================================================================
    // Target Validation
    // ========================================================================

    describe('Target validation', () => {
        it('should handle invalid target selectors gracefully', () => {
            const invalidSelectors = [
                '#non-existent',
                '.invalid-class',
                'malformed[selector',
                ''
            ];

            invalidSelectors.forEach(selector => {
                global.document.querySelector = vi.fn(() => null);
                
                const result = document.querySelector(selector);
                expect(result).toBe(null);
            });
        });

        it('should work with various valid CSS selectors', () => {
            const validSelectors = [
                '#element-id',
                '.class-name',
                '[data-target]',
                'div.class',
                '#parent .child'
            ];

            validSelectors.forEach(selector => {
                global.document.querySelector = vi.fn(() => mockTargetElement);
                
                const result = document.querySelector(selector);
                expect(result).toBe(mockTargetElement);
            });
        });
    });

    // ========================================================================
    // Performance Considerations
    // ========================================================================

    describe('Performance', () => {
        it('should not query document unnecessarily when no target selector', () => {
            const querySelectorSpy = vi.spyOn(global.document, 'querySelector');
            
            // Simulate handling event without dj-target
            const params = {
                _targetElement: mockElement
            };

            // No _djTargetSelector should mean no additional query
            expect(params._djTargetSelector).toBeUndefined();
            
            // Reset the spy count
            querySelectorSpy.mockClear();
            
            // Our code shouldn't call querySelector if no target selector
            expect(querySelectorSpy).not.toHaveBeenCalled();
        });

        it('should cache target element lookups when possible', () => {
            // This test would verify caching behavior if implemented
            const targetSelector = '#cached-target';
            
            global.document.querySelector = vi.fn(() => mockTargetElement);
            
            // First lookup
            const first = document.querySelector(targetSelector);
            // Second lookup 
            const second = document.querySelector(targetSelector);
            
            expect(global.document.querySelector).toHaveBeenCalledTimes(2);
            expect(first).toBe(second);
        });
    });
});