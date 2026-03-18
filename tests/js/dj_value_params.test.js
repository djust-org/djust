/**
 * Tests for dj-value-* static event parameters.
 *
 * dj-value-* attributes pass static context alongside events, matching
 * Phoenix LiveView's phx-value-* semantics.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';

const fs = await import('fs');
const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', {
    runScripts: 'dangerously',
});

if (!dom.window.CSS) dom.window.CSS = {};
if (!dom.window.CSS.escape) {
    dom.window.CSS.escape = function(value) {
        return String(value).replace(/([^\w-])/g, '\\$1');
    };
}

dom.window.eval(clientCode);

const {
    collectDjValues,
    extractTypedParams,
} = dom.window.djust;

const document = dom.window.document;

describe('dj-value-* static event parameters', () => {

    describe('collectDjValues', () => {
        it('extracts simple string values', () => {
            const el = document.createElement('button');
            el.setAttribute('dj-value-id', '42');
            el.setAttribute('dj-value-type', 'soft');

            const values = collectDjValues(el);

            expect(values.id).toBe('42');
            expect(values.type).toBe('soft');
        });

        it('converts kebab-case to snake_case', () => {
            const el = document.createElement('button');
            el.setAttribute('dj-value-item-id', '42');
            el.setAttribute('dj-value-item-type', 'post');

            const values = collectDjValues(el);

            expect(values.item_id).toBe('42');
            expect(values.item_type).toBe('post');
        });

        it('supports :int type hint', () => {
            const el = document.createElement('button');
            el.setAttribute('dj-value-id:int', '42');

            const values = collectDjValues(el);

            expect(values.id).toBe(42);
            expect(typeof values.id).toBe('number');
        });

        it('supports :float type hint', () => {
            const el = document.createElement('button');
            el.setAttribute('dj-value-price:float', '19.99');

            const values = collectDjValues(el);

            expect(values.price).toBe(19.99);
        });

        it('supports :bool type hint', () => {
            const el = document.createElement('button');
            el.setAttribute('dj-value-active:bool', 'true');
            el.setAttribute('dj-value-deleted:bool', 'false');

            const values = collectDjValues(el);

            expect(values.active).toBe(true);
            expect(values.deleted).toBe(false);
        });

        it('supports :json type hint', () => {
            const el = document.createElement('button');
            el.setAttribute('dj-value-config:json', '{"theme":"dark"}');

            const values = collectDjValues(el);

            expect(values.config).toEqual({ theme: 'dark' });
        });

        it('supports :list type hint', () => {
            const el = document.createElement('button');
            el.setAttribute('dj-value-tags:list', 'a,b,c');

            const values = collectDjValues(el);

            expect(values.tags).toEqual(['a', 'b', 'c']);
        });

        it('returns empty object when no dj-value-* attributes', () => {
            const el = document.createElement('button');
            el.setAttribute('dj-click', 'submit');
            el.setAttribute('data-id', '42');

            const values = collectDjValues(el);

            expect(Object.keys(values).length).toBe(0);
        });

        it('ignores non-dj-value attributes', () => {
            const el = document.createElement('button');
            el.setAttribute('dj-click', 'submit');
            el.setAttribute('dj-confirm', 'Are you sure?');
            el.setAttribute('data-id', '99');
            el.setAttribute('dj-value-id', '42');

            const values = collectDjValues(el);

            expect(Object.keys(values).length).toBe(1);
            expect(values.id).toBe('42');
        });

        it('prevents prototype pollution', () => {
            const el = document.createElement('button');
            el.setAttribute('dj-value-__proto__', 'polluted');
            el.setAttribute('dj-value-constructor', 'polluted');
            el.setAttribute('dj-value-safe-key', 'safe');

            const values = collectDjValues(el);

            expect(values.__proto__).toBeUndefined();
            expect(values.constructor).toBeUndefined();
            expect(values.safe_key).toBe('safe');
        });

        it('handles empty string values', () => {
            const el = document.createElement('button');
            el.setAttribute('dj-value-query', '');

            const values = collectDjValues(el);

            expect(values.query).toBe('');
        });

        it('handles :int with empty string', () => {
            const el = document.createElement('button');
            el.setAttribute('dj-value-count:int', '');

            const values = collectDjValues(el);

            expect(values.count).toBe(0);
        });
    });

    describe('extractTypedParams integration', () => {
        it('merges dj-value-* with data-* attributes', () => {
            const el = document.createElement('button');
            el.setAttribute('data-page:int', '1');
            el.setAttribute('dj-value-id:int', '42');

            const params = extractTypedParams(el);

            expect(params.page).toBe(1);
            expect(params.id).toBe(42);
        });

        it('dj-value-* takes precedence over data-*', () => {
            const el = document.createElement('button');
            el.setAttribute('data-id', 'from-data');
            el.setAttribute('dj-value-id', 'from-dj-value');

            const params = extractTypedParams(el);

            expect(params.id).toBe('from-dj-value');
        });

        it('dj-value-* takes precedence over dj-params', () => {
            const el = document.createElement('button');
            el.setAttribute('dj-params', '{"id": "from-dj-params"}');
            el.setAttribute('dj-value-id', 'from-dj-value');

            const params = extractTypedParams(el);

            expect(params.id).toBe('from-dj-value');
        });
    });

    describe('typical usage patterns', () => {
        it('delete button with item context', () => {
            // <button dj-click="delete" dj-value-id:int="42" dj-value-type="soft">
            const el = document.createElement('button');
            el.setAttribute('dj-click', 'delete');
            el.setAttribute('dj-value-id:int', '42');
            el.setAttribute('dj-value-type', 'soft');

            const params = extractTypedParams(el);

            expect(params.id).toBe(42);
            expect(params.type).toBe('soft');
        });

        it('list item with multiple values', () => {
            // <tr dj-click="select" dj-value-row-id:int="7" dj-value-category="users">
            const el = document.createElement('tr');
            el.setAttribute('dj-click', 'select');
            el.setAttribute('dj-value-row-id:int', '7');
            el.setAttribute('dj-value-category', 'users');

            const params = extractTypedParams(el);

            expect(params.row_id).toBe(7);
            expect(params.category).toBe('users');
        });

        it('toggle with boolean flag', () => {
            // <button dj-click="toggle" dj-value-expanded:bool="true">
            const el = document.createElement('button');
            el.setAttribute('dj-click', 'toggle');
            el.setAttribute('dj-value-expanded:bool', 'true');

            const params = extractTypedParams(el);

            expect(params.expanded).toBe(true);
        });
    });
});
