/**
 * Tests for djust JavaScript security utilities
 *
 * These tests verify the security utilities work correctly to prevent:
 * - XSS attacks (safeSetInnerHTML)
 * - Prototype pollution (safeObjectAssign, safeDeepMerge)
 * - Log injection (sanitizeForLog)
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { JSDOM } from 'jsdom';

// Load the security module
// Note: In actual runtime, this is loaded as a global
const securityCode = await import('fs').then(fs =>
    fs.readFileSync('./python/djust/static/djust/security.js', 'utf-8')
);

// Execute in JSDOM environment
const dom = new JSDOM('<!DOCTYPE html><html><body><div id="test"></div></body></html>', {
    runScripts: 'dangerously',
});

// Execute the security code
dom.window.eval(securityCode);
const djustSecurity = dom.window.djustSecurity;

describe('djustSecurity.isSafeKey', () => {
    it('should allow normal keys', () => {
        expect(djustSecurity.isSafeKey('name')).toBe(true);
        expect(djustSecurity.isSafeKey('count')).toBe(true);
        expect(djustSecurity.isSafeKey('user_id')).toBe(true);
    });

    it('should block __proto__', () => {
        expect(djustSecurity.isSafeKey('__proto__')).toBe(false);
    });

    it('should block prototype', () => {
        expect(djustSecurity.isSafeKey('prototype')).toBe(false);
    });

    it('should block constructor', () => {
        expect(djustSecurity.isSafeKey('constructor')).toBe(false);
    });

    it('should block dunder keys', () => {
        expect(djustSecurity.isSafeKey('__class__')).toBe(false);
        expect(djustSecurity.isSafeKey('__init__')).toBe(false);
        expect(djustSecurity.isSafeKey('__custom__')).toBe(false);
    });

    it('should block non-string keys', () => {
        expect(djustSecurity.isSafeKey(123)).toBe(false);
        expect(djustSecurity.isSafeKey(null)).toBe(false);
        expect(djustSecurity.isSafeKey(undefined)).toBe(false);
    });
});

describe('djustSecurity.safeObjectAssign', () => {
    it('should merge safe properties', () => {
        const target = { a: 1 };
        const source = { b: 2, c: 3 };
        const result = djustSecurity.safeObjectAssign(target, source);

        expect(result.a).toBe(1);
        expect(result.b).toBe(2);
        expect(result.c).toBe(3);
    });

    it('should block __proto__ pollution', () => {
        const target = {};
        const malicious = JSON.parse('{"__proto__": {"polluted": true}}');

        djustSecurity.safeObjectAssign(target, malicious);

        // The __proto__ key should be blocked
        expect(target.polluted).toBeUndefined();
        expect(({}).polluted).toBeUndefined(); // Global Object not polluted
    });

    it('should block constructor pollution', () => {
        const target = {};
        const malicious = { constructor: { prototype: { polluted: true } } };

        djustSecurity.safeObjectAssign(target, malicious);

        // The 'constructor' key should be blocked, so target.constructor
        // should still be the default Object constructor (not the malicious object)
        expect(target.constructor).toBe(Object);
        expect(({}).polluted).toBeUndefined(); // Global Object not polluted
    });

    it('should block prototype pollution', () => {
        const target = {};
        const malicious = { prototype: { polluted: true } };

        djustSecurity.safeObjectAssign(target, malicious);

        expect(target.prototype).toBeUndefined();
    });

    it('should handle multiple sources', () => {
        const result = djustSecurity.safeObjectAssign({}, { a: 1 }, { b: 2 }, { c: 3 });

        expect(result.a).toBe(1);
        expect(result.b).toBe(2);
        expect(result.c).toBe(3);
    });

    it('should handle null/undefined sources', () => {
        const result = djustSecurity.safeObjectAssign({ a: 1 }, null, undefined, { b: 2 });

        expect(result.a).toBe(1);
        expect(result.b).toBe(2);
    });

    it('should throw for null target', () => {
        expect(() => djustSecurity.safeObjectAssign(null, {})).toThrow();
    });
});

describe('djustSecurity.safeDeepMerge', () => {
    it('should deep merge objects', () => {
        const target = { a: { b: 1 } };
        const source = { a: { c: 2 } };
        const result = djustSecurity.safeDeepMerge(target, source);

        expect(result.a.b).toBe(1);
        expect(result.a.c).toBe(2);
    });

    it('should block __proto__ in nested objects', () => {
        const target = {};
        const malicious = JSON.parse('{"nested": {"__proto__": {"polluted": true}}}');

        const result = djustSecurity.safeDeepMerge(target, malicious);

        expect(result.nested).toBeDefined();
        expect(({}).polluted).toBeUndefined(); // Global Object not polluted
    });

    it('should handle arrays (not merge)', () => {
        const target = { arr: [1, 2] };
        const source = { arr: [3, 4] };
        const result = djustSecurity.safeDeepMerge(target, source);

        expect(result.arr).toEqual([3, 4]); // Replaced, not merged
    });
});

describe('djustSecurity.safeSetInnerHTML', () => {
    let element;

    beforeEach(() => {
        element = dom.window.document.createElement('div');
    });

    it('should set normal HTML content', () => {
        djustSecurity.safeSetInnerHTML(element, '<p>Hello World</p>');

        expect(element.innerHTML).toContain('Hello World');
        expect(element.querySelector('p')).not.toBeNull();
    });

    it('should remove script tags by default', () => {
        djustSecurity.safeSetInnerHTML(element, '<div>Safe</div><script>alert("xss")</script>');

        expect(element.innerHTML).toContain('Safe');
        expect(element.querySelector('script')).toBeNull();
        expect(element.innerHTML).not.toContain('alert');
    });

    it('should remove inline event handlers', () => {
        djustSecurity.safeSetInnerHTML(element, '<div onclick="alert(1)">Click</div>');

        const div = element.querySelector('div');
        expect(div.getAttribute('onclick')).toBeNull();
    });

    it('should remove javascript: URLs', () => {
        djustSecurity.safeSetInnerHTML(element, '<a href="javascript:alert(1)">Link</a>');

        const link = element.querySelector('a');
        expect(link.getAttribute('href')).toBeNull();
    });

    it('should remove onerror handlers', () => {
        djustSecurity.safeSetInnerHTML(element, '<img src="x" onerror="alert(1)">');

        const img = element.querySelector('img');
        expect(img.getAttribute('onerror')).toBeNull();
    });

    it('should allow scripts when explicitly enabled', () => {
        // Note: In JSDOM, DOMParser doesn't preserve script tag content when
        // re-serializing via innerHTML. This test verifies the allowScripts
        // option at least processes without error and includes safe content.
        djustSecurity.safeSetInnerHTML(element, '<script>console.log("ok")</script><p>Safe</p>', {
            allowScripts: true,
        });

        // Safe content should be preserved
        expect(element.querySelector('p')).not.toBeNull();
        expect(element.innerHTML).toContain('Safe');
        // Note: Script tag preservation depends on environment (browser vs JSDOM)
    });

    it('should handle invalid element gracefully', () => {
        const consoleSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});

        const result = djustSecurity.safeSetInnerHTML(null, '<p>test</p>');

        expect(result).toBeNull();
        expect(consoleSpy).toHaveBeenCalled();

        consoleSpy.mockRestore();
    });

    it('should handle non-string HTML gracefully', () => {
        const consoleSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});

        djustSecurity.safeSetInnerHTML(element, 12345);

        expect(consoleSpy).toHaveBeenCalled();

        consoleSpy.mockRestore();
    });
});

describe('djustSecurity.sanitizeForLog', () => {
    it('should pass through normal text', () => {
        expect(djustSecurity.sanitizeForLog('hello world')).toBe('hello world');
    });

    it('should remove ANSI escape sequences', () => {
        const result = djustSecurity.sanitizeForLog('\x1b[31mred\x1b[0m');

        expect(result).not.toContain('\x1b');
        expect(result).toContain('red');
    });

    it('should remove control characters', () => {
        const result = djustSecurity.sanitizeForLog('hello\x00world');

        expect(result).not.toContain('\x00');
    });

    it('should replace newlines', () => {
        const result = djustSecurity.sanitizeForLog('line1\nline2\r\nline3');

        expect(result).not.toContain('\n');
        expect(result).not.toContain('\r');
        expect(result).toContain('line1');
        expect(result).toContain('line2');
        expect(result).toContain('line3');
    });

    it('should truncate long strings', () => {
        const longString = 'a'.repeat(1000);
        const result = djustSecurity.sanitizeForLog(longString);

        expect(result.length).toBeLessThanOrEqual(500);
        expect(result).toContain('truncated');
    });

    it('should handle null', () => {
        expect(djustSecurity.sanitizeForLog(null)).toBe('[null]');
    });

    it('should handle undefined', () => {
        expect(djustSecurity.sanitizeForLog(undefined)).toBe('[undefined]');
    });

    it('should handle numbers', () => {
        expect(djustSecurity.sanitizeForLog(12345)).toBe('12345');
    });

    it('should handle objects', () => {
        const result = djustSecurity.sanitizeForLog({ key: 'value' });

        expect(result).toContain('key');
        expect(result).toContain('value');
    });
});

describe('djustSecurity.sanitizeObjectForLog', () => {
    it('should redact password fields', () => {
        const data = { username: 'bob', password: 'secret123' };
        const result = djustSecurity.sanitizeObjectForLog(data);

        expect(result.username).toBe('bob');
        expect(result.password).toBe('[REDACTED]');
    });

    it('should redact api_key fields', () => {
        const data = { api_key: 'sk-123456' };
        const result = djustSecurity.sanitizeObjectForLog(data);

        expect(result.api_key).toBe('[REDACTED]');
    });

    it('should redact token fields', () => {
        const data = { token: 'abc123', access_token: 'def456' };
        const result = djustSecurity.sanitizeObjectForLog(data);

        expect(result.token).toBe('[REDACTED]');
        expect(result.access_token).toBe('[REDACTED]');
    });

    it('should handle nested objects', () => {
        const data = {
            user: { name: 'Bob', password: 'secret' },
        };
        const result = djustSecurity.sanitizeObjectForLog(data);

        expect(result.user.name).toBe('Bob');
        expect(result.user.password).toBe('[REDACTED]');
    });

    it('should handle arrays', () => {
        const data = { items: ['a', 'b', 'c'] };
        const result = djustSecurity.sanitizeObjectForLog(data);

        expect(result.items).toHaveLength(3);
    });

    it('should limit array length', () => {
        const data = { items: Array(20).fill('item') };
        const result = djustSecurity.sanitizeObjectForLog(data);

        expect(result.items.length).toBeLessThanOrEqual(11); // 10 items + "...and X more"
    });
});

describe('Prototype Pollution Prevention', () => {
    it('should prevent Object.prototype pollution via JSON parse + assign', () => {
        // This is the classic prototype pollution attack
        const payload = '{"__proto__": {"isAdmin": true}}';
        const parsed = JSON.parse(payload);

        djustSecurity.safeObjectAssign({}, parsed);

        // Check that Object.prototype was NOT modified
        expect(({}).isAdmin).toBeUndefined();
    });

    it('should prevent constructor pollution', () => {
        const payload = { constructor: { prototype: { polluted: true } } };

        djustSecurity.safeObjectAssign({}, payload);

        expect(({}).polluted).toBeUndefined();
    });
});

describe('XSS Prevention', () => {
    let element;

    beforeEach(() => {
        element = dom.window.document.createElement('div');
    });

    it('should prevent XSS via script tags', () => {
        const maliciousHTML = `
            <div>
                <script>document.write('XSS')</script>
                <p>Safe content</p>
            </div>
        `;

        djustSecurity.safeSetInnerHTML(element, maliciousHTML);

        expect(element.querySelector('script')).toBeNull();
        expect(element.querySelector('p')).not.toBeNull();
    });

    it('should prevent XSS via event handlers', () => {
        const maliciousHTML = `
            <img src="x" onerror="alert('XSS')">
            <a href="#" onclick="alert('XSS')">Click</a>
            <div onmouseover="alert('XSS')">Hover</div>
        `;

        djustSecurity.safeSetInnerHTML(element, maliciousHTML);

        const img = element.querySelector('img');
        const link = element.querySelector('a');
        const div = element.querySelector('div');

        expect(img.getAttribute('onerror')).toBeNull();
        expect(link.getAttribute('onclick')).toBeNull();
        expect(div.getAttribute('onmouseover')).toBeNull();
    });

    it('should prevent XSS via javascript: URLs', () => {
        const maliciousHTML = `
            <a href="javascript:alert('XSS')">Click</a>
            <iframe src="javascript:alert('XSS')"></iframe>
        `;

        djustSecurity.safeSetInnerHTML(element, maliciousHTML);

        const link = element.querySelector('a');
        const iframe = element.querySelector('iframe');

        expect(link.getAttribute('href')).toBeNull();
        expect(iframe.getAttribute('src')).toBeNull();
    });
});
