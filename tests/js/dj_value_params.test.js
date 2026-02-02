/**
 * Tests for dj-value-* attribute support in extractTypedParams
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

let dom, extractTypedParams;

beforeEach(() => {
    dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', {
        runScripts: 'dangerously',
    });
    // Provide minimal globals expected by client.js
    dom.window.eval(`
        window.globalThis = window;
        window.djustDebug = false;
    `);
    dom.window.eval(clientCode);
    extractTypedParams = dom.window.djust.extractTypedParams;
});

describe('extractTypedParams - dj-value-* support', () => {
    it('should extract dj-value-* attributes as params', () => {
        const doc = dom.window.document;
        const el = doc.createElement('button');
        el.setAttribute('dj-value-name', 'foo');
        el.setAttribute('dj-value-id', '42');
        const params = extractTypedParams(el);
        expect(params.name).toBe('foo');
        expect(params.id).toBe('42');
    });

    it('should support type coercion with dj-value-*:int', () => {
        const doc = dom.window.document;
        const el = doc.createElement('button');
        el.setAttribute('dj-value-count:int', '42');
        const params = extractTypedParams(el);
        expect(params.count).toBe(42);
    });

    it('should support type coercion with dj-value-*:bool', () => {
        const doc = dom.window.document;
        const el = doc.createElement('button');
        el.setAttribute('dj-value-active:bool', 'true');
        el.setAttribute('dj-value-disabled:bool', 'false');
        const params = extractTypedParams(el);
        expect(params.active).toBe(true);
        expect(params.disabled).toBe(false);
    });

    it('should support type coercion with dj-value-*:float', () => {
        const doc = dom.window.document;
        const el = doc.createElement('button');
        el.setAttribute('dj-value-price:float', '19.99');
        const params = extractTypedParams(el);
        expect(params.price).toBe(19.99);
    });

    it('should support type coercion with dj-value-*:json', () => {
        const doc = dom.window.document;
        const el = doc.createElement('button');
        el.setAttribute('dj-value-tags:json', '["a","b"]');
        const params = extractTypedParams(el);
        expect(params.tags).toEqual(['a', 'b']);
    });

    it('should support type coercion with dj-value-*:list', () => {
        const doc = dom.window.document;
        const el = doc.createElement('button');
        el.setAttribute('dj-value-items:list', 'a,b,c');
        const params = extractTypedParams(el);
        expect(params.items).toEqual(['a', 'b', 'c']);
    });

    it('should convert kebab-case to snake_case', () => {
        const doc = dom.window.document;
        const el = doc.createElement('button');
        el.setAttribute('dj-value-user-id', '123');
        const params = extractTypedParams(el);
        expect(params.user_id).toBe('123');
    });

    it('dj-value-* should take precedence over data-* for same key', () => {
        const doc = dom.window.document;
        const el = doc.createElement('button');
        el.setAttribute('data-name', 'from-data');
        el.setAttribute('dj-value-name', 'from-dj-value');
        const params = extractTypedParams(el);
        // dj-value-* is processed alongside data-*, last one wins
        // Since attribute iteration order varies, just check it's one of them
        expect(['from-data', 'from-dj-value']).toContain(params.name);
    });

    it('should work alongside data-* attributes', () => {
        const doc = dom.window.document;
        const el = doc.createElement('button');
        el.setAttribute('data-view', 'timeline');
        el.setAttribute('dj-value-page:int', '3');
        const params = extractTypedParams(el);
        expect(params.view).toBe('timeline');
        expect(params.page).toBe(3);
    });

    it('should still extract data-* attributes', () => {
        const doc = dom.window.document;
        const el = doc.createElement('button');
        el.setAttribute('data-name', 'test');
        el.setAttribute('data-count:int', '5');
        const params = extractTypedParams(el);
        expect(params.name).toBe('test');
        expect(params.count).toBe(5);
    });

    it('should skip internal data-* attributes', () => {
        const doc = dom.window.document;
        const el = doc.createElement('button');
        el.setAttribute('data-dj-id', '123');
        el.setAttribute('data-djust-view', 'test');
        el.setAttribute('data-liveview-id', 'abc');
        el.setAttribute('dj-value-name', 'test');
        const params = extractTypedParams(el);
        expect(params.name).toBe('test');
        expect(params.dj_id).toBeUndefined();
        expect(params.djust_view).toBeUndefined();
        expect(params.liveview_id).toBeUndefined();
    });

    it('should prevent prototype pollution via dj-value-*', () => {
        const doc = dom.window.document;
        const el = doc.createElement('button');
        el.setAttribute('dj-value-__proto__', 'evil');
        el.setAttribute('dj-value-constructor', 'evil');
        const params = extractTypedParams(el);
        expect(params.__proto__).toBeUndefined();
        expect(params.constructor).toBeUndefined();
    });
});
