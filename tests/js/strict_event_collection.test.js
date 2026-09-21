/** ADR-036 staged collector; owner-scoped binder integration is a separate gate. */
import { afterAll, describe, expect, it } from 'vitest';
import { JSDOM } from 'jsdom';
import { readFileSync } from 'node:fs';

const source = readFileSync('./python/djust/static/djust/client.js', 'utf8');
const seam = 'window.djust.collectDjValues = collectDjValues;';
if (source.split(seam).length !== 2) throw new Error('Missing unique collector test seam');
const dom = new JSDOM('', { runScripts: 'dangerously' });
dom.window.eval(source.replace(seam, seam + '\nwindow.strictCollector = typeof _collectStrictEventParams === "function" ? _collectStrictEventParams : null;'));
afterAll(() => dom.window.close());

function collect(attributes, generated = {}, positional = []) {
    const element = dom.window.document.createElement('button');
    for (const [name, value] of Object.entries(attributes)) element.setAttribute(name, value);
    expect(dom.window.strictCollector).toBeTypeOf('function');
    return dom.window.strictCollector(element, generated, positional);
}

describe('strict application argument collection', () => {
    it('collects only canonical attributes and preserves untyped strings', () => {
        expect(collect({ 'dj-value-item-id': ' 42 ', 'data-bs-toggle': 'dropdown',
            'data-item-id': '99', 'dj-params': '{"legacy":1}' })).toEqual({ item_id: ' 42 ' });
    });

    it.each([
        ['int', '42', 42], ['integer', ' -7 ', -7], ['float', ' .25e2 ', 25],
        ['number', '-0.5', -0.5], ['bool', ' YES ', true], ['boolean', 'OFF', false],
        ['bool', 'true', true], ['bool', 'false', false], ['bool', '1', true],
        ['bool', '0', false], ['bool', 'on', true], ['bool', 'no', false],
        ['array', '[1,"x",null]', [1, 'x', null]], ['list', '[1,2]', [1, 2]],
        ['json', 'null', null], ['object', '{"x":1}', { x: 1 }],
        ['float', '1e20', 1e20], ['float', '9007199254740993', 9007199254740992],
        ['json', '{"cost":1e20,"text":"9007199254740993"}', { cost: 1e20, text: '9007199254740993' }],
    ])('parses valid :%s %s without lossy conversion', (type, text, expected) => {
        expect(collect({ [`dj-value-value:${type}`]: text }).value).toEqual(expected);
    });

    it.each([
        ['int', ''], ['int', '12garbage'], ['int', '1.5'], ['int', '1_000'],
        ['int', '9007199254740993'], ['int', '\u00a01'], ['int', '１２'],
        ['int', '1\u2028'], ['float', '1\u2029'],
        ['float', ''], ['float', '2px'], ['float', 'Infinity'], ['float', '1e999'],
        ['bool', 'maybe'], ['bool', 'checked'],
        ['json', '{broken'], ['json', '[9007199254740993]'], ['json', '[1e999]'],
        ['array', '{}'], ['object', '[]'], ['list', 'a,b'], ['unknown', 'secret'],
    ])('rejects malformed or unrepresentable :%s', (type, text) => {
        expect(() => collect({ [`dj-value-value:${type}`]: text })).toThrow('Invalid strict event arguments');
    });

    it('rejects normalized duplicates and generated-value collisions', () => {
        expect(() => collect({ 'dj-value-item-id': '1', 'dj-value-item_id': '2' })).toThrow('Invalid strict event arguments');
        expect(() => collect({ 'dj-value-value': 'old' }, { value: 'new' })).toThrow('Invalid strict event arguments');
    });

    it('keeps generated values and positional arguments distinct without mutating them', () => {
        const generated = { value: 'hello' };
        const positional = ['x'];
        const result = collect({ 'dj-value-item-id': '2' }, generated, positional);
        expect(result).toEqual({ value: 'hello', item_id: '2', _args: ['x'] });
        expect(generated).toEqual({ value: 'hello' });
        expect(result._args).not.toBe(positional);
    });

    it.each(['__proto__', 'constructor', 'prototype', '_args', 'component_id', 'view_id'])('rejects reserved application key %s', key => {
        expect(() => collect({ [`dj-value-${key}`]: 'secret' })).toThrow('Invalid strict event arguments');
    });

    it('does not echo secret values or keys in diagnostics', () => {
        try {
            collect({ 'dj-value-secret-key:int': 'SECRET_PAYLOAD' });
            throw new Error('Expected rejection');
        } catch (error) {
            expect(error.message).toBe('Invalid strict event arguments');
        }
    });

    it('rejects oversized, deep, cyclic and unsafe generated values', () => {
        let nested = null;
        for (let i = 0; i < 35; i++) nested = [nested];
        const cycle = []; cycle.push(cycle);
        for (const value of [nested, cycle, Array(1025).fill(0), 'x'.repeat(65537), NaN]) {
            expect(() => collect({}, { value })).toThrow('Invalid strict event arguments');
        }
    });

    it('snapshots generated containers without invoking accessors or toJSON', () => {
        const input = { items: [1, { label: 'before' }] };
        const result = collect({}, input);
        input.items[1].label = 'after';
        expect(result.items[1].label).toBe('before');
        const hostile = {};
        Object.defineProperty(hostile, 'value', { enumerable: true, get() { throw new Error('getter executed'); } });
        expect(() => collect({}, hostile)).toThrow('Invalid strict event arguments');
        const nested = { safe: true };
        Object.defineProperty(nested, 'toJSON', { value() { throw new Error('toJSON executed'); } });
        expect(JSON.stringify(collect({}, { nested }))).toBe('{"nested":{"safe":true}}');
    });

    it('rejects sparse arrays and non-object generated payloads', () => {
        expect(() => collect({}, { value: Array(3) })).toThrow('Invalid strict event arguments');
        expect(() => collect({}, new Date())).toThrow('Invalid strict event arguments');
    });

    it('snapshots positional values and rejects positional accessors', () => {
        const object = { value: 'before' };
        Object.defineProperty(object, 'toJSON', { value() { return 'x'.repeat(70000); } });
        const result = collect({}, {}, [object]);
        object.value = 'after';
        expect(JSON.stringify(result)).toBe('{"_args":[{"value":"before"}]}');
        const accessor = [];
        Object.defineProperty(accessor, '0', { enumerable: true, get() { throw new Error('SECRET_GETTER'); } });
        expect(() => collect({}, {}, accessor)).toThrow('Invalid strict event arguments');
    });

    it('does not interpret escaped JSON string content as numeric tokens', () => {
        for (const text of ['9007199254740993', '\\"9007199254740993', '" : 9007199254740993', '\\']) {
            expect(collect({ 'dj-value-value:json': JSON.stringify({ text }) }).value).toEqual({ text });
        }
    });

    it('checks integer precision at both safe boundaries using exact BigInt expectations', () => {
        const limit = 9007199254740991n;
        for (let delta = -40n; delta <= 40n; delta++) {
            for (const sign of [-1n, 1n]) {
                const value = (limit + delta) * sign;
                const attributes = { 'dj-value-value:json': `[${value}]` };
                if (value >= -limit && value <= limit) {
                    expect(collect(attributes).value).toEqual([Number(value)]);
                } else {
                    expect(() => collect(attributes)).toThrow('Invalid strict event arguments');
                }
            }
        }
    });
});
