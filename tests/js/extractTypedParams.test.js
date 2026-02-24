/**
 * Tests for extractTypedParams function
 *
 * Tests the client-side data-attribute→parameter extraction with type coercion.
 * Covers kebab→snake conversion, dj_ prefix stripping, type hints, internal
 * attribute skipping, and prototype pollution protection.
 *
 * Issue: https://github.com/djust-org/djust/issues/268
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import { readFileSync } from 'fs';

// Load the client module code
const clientCode = readFileSync('./python/djust/static/djust/client.js', 'utf-8');

// Create a minimal DOM environment
const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', {
    runScripts: 'dangerously',
});

// Execute the client code to get extractTypedParams
dom.window.eval(clientCode);
const { extractTypedParams } = dom.window.djust;

/** Helper: create a DOM element with given attributes */
function createElement(attrs = {}) {
    const el = dom.window.document.createElement('div');
    for (const [name, value] of Object.entries(attrs)) {
        el.setAttribute(name, value);
    }
    return el;
}

describe('extractTypedParams', () => {
    describe('Kebab-case to snake_case conversion', () => {
        it('should convert data-sender-id to sender_id', () => {
            const el = createElement({ 'data-sender-id': '42' });
            const params = extractTypedParams(el);
            expect(params.sender_id).toBe('42');
        });

        it('should convert data-first-name to first_name', () => {
            const el = createElement({ 'data-first-name': 'Alice' });
            const params = extractTypedParams(el);
            expect(params.first_name).toBe('Alice');
        });
    });

    describe('dj_ prefix stripping', () => {
        it('should strip dj_ prefix: data-dj-preset → preset', () => {
            const el = createElement({ 'data-dj-preset': 'dark' });
            const params = extractTypedParams(el);
            expect(params.preset).toBe('dark');
            expect(params.dj_preset).toBeUndefined();
        });
    });

    describe('Combined kebab + dj_ prefix', () => {
        it('should convert data-dj-property-id to property_id', () => {
            const el = createElement({ 'data-dj-property-id': '99' });
            const params = extractTypedParams(el);
            expect(params.property_id).toBe('99');
        });
    });

    describe(':int coercion', () => {
        it('should coerce data-count:int="42" to number 42', () => {
            const el = createElement({ 'data-count:int': '42' });
            const params = extractTypedParams(el);
            expect(params.count).toBe(42);
            expect(typeof params.count).toBe('number');
        });

        it('should return null for invalid int data-count:int="abc"', () => {
            const el = createElement({ 'data-count:int': 'abc' });
            const params = extractTypedParams(el);
            expect(params.count).toBeNull();
        });

        it('should return 0 for empty int data-count:int=""', () => {
            const el = createElement({ 'data-count:int': '' });
            const params = extractTypedParams(el);
            expect(params.count).toBe(0);
        });
    });

    describe(':bool coercion', () => {
        it('should coerce "true" to true', () => {
            const el = createElement({ 'data-enabled:bool': 'true' });
            const params = extractTypedParams(el);
            expect(params.enabled).toBe(true);
        });

        it('should coerce "1" to true', () => {
            const el = createElement({ 'data-enabled:bool': '1' });
            const params = extractTypedParams(el);
            expect(params.enabled).toBe(true);
        });

        it('should coerce "yes" to true', () => {
            const el = createElement({ 'data-enabled:bool': 'yes' });
            const params = extractTypedParams(el);
            expect(params.enabled).toBe(true);
        });

        it('should coerce "false" to false', () => {
            const el = createElement({ 'data-enabled:bool': 'false' });
            const params = extractTypedParams(el);
            expect(params.enabled).toBe(false);
        });

        it('should coerce "no" to false', () => {
            const el = createElement({ 'data-enabled:bool': 'no' });
            const params = extractTypedParams(el);
            expect(params.enabled).toBe(false);
        });
    });

    describe(':float coercion', () => {
        it('should coerce data-price:float="19.99" to 19.99', () => {
            const el = createElement({ 'data-price:float': '19.99' });
            const params = extractTypedParams(el);
            expect(params.price).toBe(19.99);
            expect(typeof params.price).toBe('number');
        });

        it('should return null for invalid float', () => {
            const el = createElement({ 'data-price:float': 'xyz' });
            const params = extractTypedParams(el);
            expect(params.price).toBeNull();
        });
    });

    describe(':json coercion', () => {
        it('should parse JSON array data-tags:json=\'["a","b"]\'', () => {
            const el = createElement({ 'data-tags:json': '["a","b"]' });
            const params = extractTypedParams(el);
            expect(params.tags).toEqual(['a', 'b']);
        });

        it('should parse JSON object', () => {
            const el = createElement({ 'data-config:json': '{"key":"val"}' });
            const params = extractTypedParams(el);
            expect(params.config).toEqual({ key: 'val' });
        });

        it('should keep as string on invalid JSON', () => {
            const el = createElement({ 'data-bad:json': 'not-json' });
            const params = extractTypedParams(el);
            expect(params.bad).toBe('not-json');
        });
    });

    describe(':list coercion', () => {
        it('should split data-items:list="a,b,c" into ["a","b","c"]', () => {
            const el = createElement({ 'data-items:list': 'a,b,c' });
            const params = extractTypedParams(el);
            expect(params.items).toEqual(['a', 'b', 'c']);
        });

        it('should trim whitespace in list items', () => {
            const el = createElement({ 'data-items:list': ' a , b , c ' });
            const params = extractTypedParams(el);
            expect(params.items).toEqual(['a', 'b', 'c']);
        });

        it('should return empty array for empty list', () => {
            const el = createElement({ 'data-items:list': '' });
            const params = extractTypedParams(el);
            expect(params.items).toEqual([]);
        });
    });

    describe('Default string (no type hint)', () => {
        it('should return string value by default', () => {
            const el = createElement({ 'data-name': 'John' });
            const params = extractTypedParams(el);
            expect(params.name).toBe('John');
            expect(typeof params.name).toBe('string');
        });
    });

    describe('Internal attribute skipping', () => {
        it('should skip data-liveview* attributes (defensive filter)', () => {
            // These attrs are no longer set by the client (WeakMap is used instead),
            // but the filter remains as a defensive measure.
            const el = createElement({
                'data-liveview-click-bound': 'true',
                'data-name': 'visible',
            });
            const params = extractTypedParams(el);
            expect(params.name).toBe('visible');
            expect(Object.keys(params)).not.toContain('liveview_click_bound');
            expect(Object.keys(params)).not.toContain('liveviewClickBound');
        });

        it('should skip data-djust* attributes', () => {
            const el = createElement({
                'dj-view': 'myapp.MyView',
                'dj-root': '',
                'data-name': 'visible',
            });
            const params = extractTypedParams(el);
            expect(Object.keys(params)).toEqual(['name']);
        });

        it('should skip dj-id', () => {
            const el = createElement({
                'dj-id': 'comp-1',
                'data-name': 'visible',
            });
            const params = extractTypedParams(el);
            expect(Object.keys(params)).toEqual(['name']);
        });

        it('should skip data-component-id', () => {
            const el = createElement({
                'data-component-id': 'comp-1',
                'data-name': 'visible',
            });
            const params = extractTypedParams(el);
            expect(Object.keys(params)).toEqual(['name']);
        });
    });

    describe('Multiple attributes combined', () => {
        it('should extract multiple params into one object', () => {
            const el = createElement({
                'data-sender-id:int': '7',
                'data-dj-preset': 'dark',
                'data-enabled:bool': 'true',
            });
            const params = extractTypedParams(el);
            expect(params).toEqual({
                sender_id: 7,
                preset: 'dark',
                enabled: true,
            });
        });
    });

    describe('Prototype pollution protection', () => {
        it('should skip data-__proto__ attribute', () => {
            const el = createElement({ 'data-__proto__': '{"polluted":true}' });
            const params = extractTypedParams(el);
            expect(params.__proto__).toBeUndefined();
            expect(params.polluted).toBeUndefined();
        });

        it('should skip data-constructor attribute', () => {
            const el = createElement({ 'data-constructor': 'evil' });
            const params = extractTypedParams(el);
            expect(params.constructor).toBeUndefined();
        });

        it('should skip data-prototype attribute', () => {
            const el = createElement({ 'data-prototype': 'evil' });
            const params = extractTypedParams(el);
            expect(params.prototype).toBeUndefined();
        });
    });

    describe('Non-data attributes ignored', () => {
        it('should not extract class, id, or other non-data attributes', () => {
            const el = createElement({
                'class': 'btn',
                'id': 'my-btn',
                'data-value': 'yes',
            });
            const params = extractTypedParams(el);
            expect(Object.keys(params)).toEqual(['value']);
        });
    });
});
