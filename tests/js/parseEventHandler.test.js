/**
 * Tests for parseEventHandler function
 *
 * Tests the client-side parsing of event handler strings like @click="handler('arg')"
 * into function name and typed arguments.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import { readFileSync } from 'fs';

// Load the client module code
// Note: We extract just the parsing functions to test in isolation
const clientCode = readFileSync('./python/djust/static/djust/client.js', 'utf-8');

// Create a minimal DOM environment
const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', {
    runScripts: 'dangerously',
});

// Execute the client code to get parseEventHandler
// This exposes window.djust.parseEventHandler
dom.window.eval(clientCode);
const { parseEventHandler } = dom.window.djust;

describe('parseEventHandler', () => {
    describe('Simple handler names', () => {
        it('should handle simple handler name without parentheses', () => {
            const result = parseEventHandler('handleClick');
            expect(result.name).toBe('handleClick');
            expect(result.args).toEqual([]);
        });

        it('should handle handler name with empty parentheses', () => {
            const result = parseEventHandler('submit()');
            expect(result.name).toBe('submit');
            expect(result.args).toEqual([]);
        });

        it('should trim whitespace from handler name', () => {
            const result = parseEventHandler('  doSomething  ');
            expect(result.name).toBe('doSomething');
            expect(result.args).toEqual([]);
        });

        it('should handle whitespace around parentheses', () => {
            const result = parseEventHandler('  save(  )  ');
            expect(result.name).toBe('save');
            expect(result.args).toEqual([]);
        });
    });

    describe('String arguments', () => {
        it('should parse single single-quoted string argument', () => {
            const result = parseEventHandler("set_period('month')");
            expect(result.name).toBe('set_period');
            expect(result.args).toEqual(['month']);
        });

        it('should parse single double-quoted string argument', () => {
            const result = parseEventHandler('set_value("hello")');
            expect(result.name).toBe('set_value');
            expect(result.args).toEqual(['hello']);
        });

        it('should handle escaped quotes in strings', () => {
            const result = parseEventHandler("say('it\\'s')");
            expect(result.name).toBe('say');
            expect(result.args).toEqual(["it's"]);
        });

        it('should handle escaped double quotes in strings', () => {
            const result = parseEventHandler('say("he said \\"hi\\"")');
            expect(result.name).toBe('say');
            expect(result.args).toEqual(['he said "hi"']);
        });

        it('should handle escaped backslash in strings', () => {
            const result = parseEventHandler("path('c:\\\\temp')");
            expect(result.name).toBe('path');
            expect(result.args).toEqual(['c:\\temp']);
        });

        it('should handle escaped newline in strings', () => {
            const result = parseEventHandler("text('line1\\nline2')");
            expect(result.name).toBe('text');
            expect(result.args).toEqual(['line1\nline2']);
        });

        it('should handle escaped tab in strings', () => {
            const result = parseEventHandler("text('col1\\tcol2')");
            expect(result.name).toBe('text');
            expect(result.args).toEqual(['col1\tcol2']);
        });
    });

    describe('Number arguments', () => {
        it('should parse integer argument', () => {
            const result = parseEventHandler('select_tab(2)');
            expect(result.name).toBe('select_tab');
            expect(result.args).toEqual([2]);
            expect(typeof result.args[0]).toBe('number');
        });

        it('should parse negative integer argument', () => {
            const result = parseEventHandler('adjust(-5)');
            expect(result.name).toBe('adjust');
            expect(result.args).toEqual([-5]);
        });

        it('should parse float argument', () => {
            const result = parseEventHandler('set_price(19.99)');
            expect(result.name).toBe('set_price');
            expect(result.args).toEqual([19.99]);
        });

        it('should parse negative float argument', () => {
            const result = parseEventHandler('set_temp(-40.5)');
            expect(result.name).toBe('set_temp');
            expect(result.args).toEqual([-40.5]);
        });

        it('should parse float starting with dot', () => {
            const result = parseEventHandler('set_rate(.5)');
            expect(result.name).toBe('set_rate');
            expect(result.args).toEqual([0.5]);
        });

        it('should parse float ending with dot', () => {
            const result = parseEventHandler('set_count(5.)');
            expect(result.name).toBe('set_count');
            expect(result.args).toEqual([5.0]);
        });
    });

    describe('Boolean arguments', () => {
        it('should parse true argument', () => {
            const result = parseEventHandler('toggle(true)');
            expect(result.name).toBe('toggle');
            expect(result.args).toEqual([true]);
            expect(typeof result.args[0]).toBe('boolean');
        });

        it('should parse false argument', () => {
            const result = parseEventHandler('toggle(false)');
            expect(result.name).toBe('toggle');
            expect(result.args).toEqual([false]);
            expect(typeof result.args[0]).toBe('boolean');
        });
    });

    describe('Null argument', () => {
        it('should parse null argument', () => {
            const result = parseEventHandler('reset(null)');
            expect(result.name).toBe('reset');
            expect(result.args).toEqual([null]);
        });
    });

    describe('Multiple arguments', () => {
        it('should parse two string arguments', () => {
            const result = parseEventHandler("navigate('users', 'list')");
            expect(result.name).toBe('navigate');
            expect(result.args).toEqual(['users', 'list']);
        });

        it('should parse mixed type arguments', () => {
            const result = parseEventHandler("update('item', 123, true)");
            expect(result.name).toBe('update');
            expect(result.args).toEqual(['item', 123, true]);
        });

        it('should handle whitespace between arguments', () => {
            const result = parseEventHandler("call('a',  'b'  ,  'c')");
            expect(result.name).toBe('call');
            expect(result.args).toEqual(['a', 'b', 'c']);
        });

        it('should parse many arguments', () => {
            const result = parseEventHandler("multi(1, 'two', 3.0, true, null)");
            expect(result.name).toBe('multi');
            expect(result.args).toEqual([1, 'two', 3.0, true, null]);
        });
    });

    describe('Edge cases', () => {
        it('should handle empty string', () => {
            const result = parseEventHandler('');
            expect(result.name).toBe('');
            expect(result.args).toEqual([]);
        });

        it('should handle handler with unmatched open paren', () => {
            const result = parseEventHandler('broken(');
            expect(result.name).toBe('broken(');
            expect(result.args).toEqual([]);
        });

        it('should handle handler with commas in quoted strings', () => {
            const result = parseEventHandler("tag('red,green,blue')");
            expect(result.name).toBe('tag');
            expect(result.args).toEqual(['red,green,blue']);
        });

        it('should handle handler with parens in quoted strings', () => {
            const result = parseEventHandler("show('hello (world)')");
            expect(result.name).toBe('show');
            expect(result.args).toEqual(['hello (world)']);
        });

        it('should handle empty string argument', () => {
            const result = parseEventHandler("clear('')");
            expect(result.name).toBe('clear');
            expect(result.args).toEqual(['']);
        });

        it('should handle just whitespace in argument', () => {
            const result = parseEventHandler("space('  ')");
            expect(result.name).toBe('space');
            expect(result.args).toEqual(['  ']);
        });
    });

    describe('Real-world scenarios', () => {
        it('should handle set_period scenario from Issue #62', () => {
            const result = parseEventHandler("set_period('month')");
            expect(result.name).toBe('set_period');
            expect(result.args).toEqual(['month']);
        });

        it('should handle tab selection', () => {
            const result = parseEventHandler('select_tab(0)');
            expect(result.name).toBe('select_tab');
            expect(result.args).toEqual([0]);
        });

        it('should handle sort direction', () => {
            const result = parseEventHandler("sort_by('name', true)");
            expect(result.name).toBe('sort_by');
            expect(result.args).toEqual(['name', true]);
        });

        it('should handle delete with id', () => {
            const result = parseEventHandler('delete_item(42)');
            expect(result.name).toBe('delete_item');
            expect(result.args).toEqual([42]);
        });

        it('should handle filter with multiple values', () => {
            const result = parseEventHandler("filter('status', 'active', 1)");
            expect(result.name).toBe('filter');
            expect(result.args).toEqual(['status', 'active', 1]);
        });
    });
});
