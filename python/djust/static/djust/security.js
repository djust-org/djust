/**
 * djust Security Utilities
 *
 * This module provides client-side security utilities for preventing common
 * web vulnerabilities:
 *
 * - XSS Prevention: Use safeSetInnerHTML() instead of raw innerHTML assignment
 *   when setting content from potentially untrusted sources.
 *
 * - Prototype Pollution: Use safeObjectAssign() instead of Object.assign()
 *   when merging objects from untrusted sources.
 *
 * - Log Injection: Use sanitizeForLog() to clean data before console logging.
 *
 * Usage:
 *   // Safe innerHTML (parses and re-serializes to strip scripts)
 *   djustSecurity.safeSetInnerHTML(element, htmlString);
 *
 *   // Safe object assignment (blocks __proto__, constructor, etc.)
 *   const merged = djustSecurity.safeObjectAssign({}, untrustedData);
 *
 *   // Safe logging (strips control chars)
 *   console.log(djustSecurity.sanitizeForLog(userInput));
 */

// Expose as global for non-module usage (inline scripts)
// Using var + window assignment to ensure it's accessible as window.djustSecurity
var djustSecurity = (function() {
    'use strict';

    /**
     * Dangerous keys that should never be set from untrusted input.
     * These can be used for prototype pollution attacks.
     */
    const DANGEROUS_KEYS = new Set([
        '__proto__',
        'prototype',
        'constructor',
        '__defineGetter__',
        '__defineSetter__',
        '__lookupGetter__',
        '__lookupSetter__',
        '__parent__',
        '__noSuchMethod__',
        // Common property descriptors
        'get',
        'set',
    ]);

    /**
     * Check if a key is safe to use for object property assignment.
     *
     * @param {string} key - The key to check
     * @returns {boolean} - True if the key is safe
     */
    function isSafeKey(key) {
        if (typeof key !== 'string') {
            return false;
        }

        // Block dangerous keys
        if (DANGEROUS_KEYS.has(key)) {
            return false;
        }

        // Block dunder keys (__anything__)
        if (key.startsWith('__') && key.endsWith('__')) {
            return false;
        }

        return true;
    }

    /**
     * Safely set innerHTML on an element, stripping potentially dangerous content.
     *
     * This function uses DOMParser to parse HTML and then extracts safe content,
     * which helps prevent certain XSS attacks by ensuring the HTML is properly
     * parsed rather than directly executed.
     *
     * NOTE: This provides defense-in-depth but is NOT a complete XSS solution.
     * The primary XSS defense should be server-side sanitization and CSP headers.
     * This utility helps ensure that even if malformed HTML slips through, it's
     * re-parsed in a controlled manner.
     *
     * @param {HTMLElement} element - The target element
     * @param {string} htmlString - The HTML string to set
     * @param {Object} options - Optional settings
     * @param {boolean} options.allowScripts - Whether to allow script tags (default: false)
     * @returns {HTMLElement} - The modified element
     */
    function safeSetInnerHTML(element, htmlString, options = {}) {
        if (!element || !(element instanceof HTMLElement)) {
            console.warn('[djust:security] safeSetInnerHTML: Invalid element');
            return element;
        }

        if (typeof htmlString !== 'string') {
            console.warn('[djust:security] safeSetInnerHTML: htmlString must be a string');
            return element;
        }

        const { allowScripts = false } = options;

        // Parse HTML using DOMParser (safe context, doesn't execute scripts)
        const parser = new DOMParser();
        const doc = parser.parseFromString(htmlString, 'text/html');

        // Remove script tags unless explicitly allowed
        if (!allowScripts) {
            const scripts = doc.querySelectorAll('script');
            scripts.forEach(script => script.remove());

            // Also remove event handlers that could execute code
            const allElements = doc.querySelectorAll('*');
            allElements.forEach(el => {
                // Get all attributes
                const attrs = [...el.attributes];
                attrs.forEach(attr => {
                    // Remove inline event handlers (onclick, onerror, etc.)
                    if (attr.name.toLowerCase().startsWith('on')) {
                        el.removeAttribute(attr.name);
                    }
                    // Remove dangerous URL schemes (javascript:, data:, vbscript:)
                    if (attr.value) {
                        const val = attr.value.toLowerCase().trim();
                        if (val.startsWith('javascript:') ||
                            val.startsWith('data:') ||
                            val.startsWith('vbscript:')) {
                            el.removeAttribute(attr.name);
                        }
                    }
                });
            });
        }

        // Set the sanitized content
        element.innerHTML = doc.body.innerHTML;

        return element;
    }

    /**
     * Safely assign properties from source objects to a target, blocking
     * prototype pollution attacks.
     *
     * @param {Object} target - The target object
     * @param {...Object} sources - Source objects to merge
     * @returns {Object} - The modified target object
     */
    function safeObjectAssign(target, ...sources) {
        if (target === null || target === undefined) {
            throw new TypeError('Cannot convert undefined or null to object');
        }

        const to = Object(target);

        for (const source of sources) {
            if (source === null || source === undefined) {
                continue;
            }

            // Use Object.keys to only get own enumerable properties
            for (const key of Object.keys(source)) {
                if (!isSafeKey(key)) {
                    if (globalThis.djustDebug) {
                        console.warn(`[djust:security] Blocked unsafe key: ${key}`);
                    }
                    continue;
                }

                to[key] = source[key];
            }
        }

        return to;
    }

    /**
     * Deep merge objects safely, blocking prototype pollution at all levels.
     *
     * @param {Object} target - The target object
     * @param {Object} source - The source object to merge
     * @returns {Object} - The merged object
     */
    function safeDeepMerge(target, source) {
        if (target === null || target === undefined) {
            target = {};
        }

        if (source === null || source === undefined) {
            return target;
        }

        const output = { ...target };

        for (const key of Object.keys(source)) {
            if (!isSafeKey(key)) {
                if (globalThis.djustDebug) {
                    console.warn(`[djust:security] Blocked unsafe key in deep merge: ${key}`);
                }
                continue;
            }

            const sourceValue = source[key];
            const targetValue = output[key];

            // Recursively merge nested objects
            if (
                sourceValue !== null &&
                typeof sourceValue === 'object' &&
                !Array.isArray(sourceValue) &&
                targetValue !== null &&
                typeof targetValue === 'object' &&
                !Array.isArray(targetValue)
            ) {
                output[key] = safeDeepMerge(targetValue, sourceValue);
            } else {
                output[key] = sourceValue;
            }
        }

        return output;
    }

    /**
     * Sanitize a string for safe logging (strips control characters and
     * limits length).
     *
     * @param {*} value - The value to sanitize
     * @param {number} maxLength - Maximum length (default: 500)
     * @returns {string} - Sanitized string
     */
    function sanitizeForLog(value, maxLength = 500) {
        if (value === null) {
            return '[null]';
        }
        if (value === undefined) {
            return '[undefined]';
        }

        let str;
        try {
            if (typeof value === 'string') {
                str = value;
            } else if (typeof value === 'object') {
                // Use JSON.stringify for objects to get meaningful output
                str = JSON.stringify(value);
            } else {
                str = String(value);
            }
        } catch {
            return '[unstringifiable]';
        }

        // Remove ANSI escape sequences
        str = str.replace(/\x1b\[[0-9;]*[a-zA-Z]|\x1b\][^\x07]*\x07/g, '');

        // Remove control characters (except space, tab)
        str = str.replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g, ' ');

        // Replace newlines with visible markers
        str = str.replace(/\r\n|\r|\n/g, ' ');

        // Collapse multiple spaces
        str = str.replace(/ +/g, ' ').trim();

        // Truncate
        if (str.length > maxLength) {
            str = str.substring(0, maxLength - 20) + `...[truncated]`;
        }

        return str;
    }

    /**
     * Sanitize an object for safe logging (recursively sanitizes values,
     * redacts sensitive keys).
     *
     * @param {Object} obj - The object to sanitize
     * @param {Set} sensitiveKeys - Keys to redact (default: password, token, etc.)
     * @param {number} maxValueLength - Max length per value
     * @returns {Object} - Sanitized object
     */
    function sanitizeObjectForLog(obj, sensitiveKeys = null, maxValueLength = 100) {
        if (sensitiveKeys === null) {
            sensitiveKeys = new Set([
                'password', 'passwd', 'secret', 'token', 'api_key', 'apikey',
                'auth', 'authorization', 'credential', 'credentials',
                'private_key', 'privatekey', 'access_token', 'refresh_token',
                'session_id', 'sessionid', 'csrf', 'csrftoken', 'cookie'
            ]);
        }

        if (obj === null || obj === undefined) {
            return obj;
        }

        if (typeof obj !== 'object') {
            return sanitizeForLog(obj, maxValueLength);
        }

        if (Array.isArray(obj)) {
            return obj.slice(0, 10).map(item =>
                typeof item === 'object'
                    ? sanitizeObjectForLog(item, sensitiveKeys, maxValueLength)
                    : sanitizeForLog(item, maxValueLength)
            );
        }

        const result = {};
        for (const key of Object.keys(obj)) {
            const safeKey = sanitizeForLog(key, 50);
            const lowerKey = safeKey.toLowerCase();

            if (sensitiveKeys.has(lowerKey)) {
                result[safeKey] = '[REDACTED]';
            } else if (typeof obj[key] === 'object' && obj[key] !== null) {
                result[safeKey] = sanitizeObjectForLog(obj[key], sensitiveKeys, maxValueLength);
            } else {
                result[safeKey] = sanitizeForLog(obj[key], maxValueLength);
            }
        }

        return result;
    }

    // Public API
    return {
        // Key validation
        isSafeKey,
        DANGEROUS_KEYS,

        // DOM security
        safeSetInnerHTML,

        // Object security
        safeObjectAssign,
        safeDeepMerge,

        // Logging security
        sanitizeForLog,
        sanitizeObjectForLog,
    };
})();

// Attach to window for browser usage (including JSDOM)
if (typeof window !== 'undefined') {
    window.djustSecurity = djustSecurity;
}

// Export for module systems (ES modules, CommonJS)
if (typeof module !== 'undefined' && module.exports) {
    module.exports = djustSecurity;
}
if (typeof exports !== 'undefined') {
    exports.djustSecurity = djustSecurity;
}
