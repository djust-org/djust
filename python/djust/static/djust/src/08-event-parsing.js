
// Track which Counter containers have been initialized to prevent duplicate listeners
// on each server response. WeakSet entries are GC'd when the container is removed.
const _initializedCounters = new WeakSet();

// Client-side React Counter component (vanilla JS implementation)
function initReactCounters() {
    document.querySelectorAll('[data-react-component="Counter"]').forEach(container => {
        // Skip containers already initialized — prevents N listeners after N server responses
        if (_initializedCounters.has(container)) return;
        _initializedCounters.add(container);

        const propsJson = container.dataset.reactProps;
        let props = {};
        try {
            props = JSON.parse(propsJson.replace(/&quot;/g, '"'));
        } catch { }

        let count = props.initialCount || 0;
        const display = container.querySelector('.counter-display');
        const minusBtn = container.querySelectorAll('.btn-sm')[0];
        const plusBtn = container.querySelectorAll('.btn-sm')[1];

        if (display && minusBtn && plusBtn) {
            minusBtn.addEventListener('click', () => {
                count--;
                display.textContent = count;
            });
            plusBtn.addEventListener('click', () => {
                count++;
                display.textContent = count;
            });
        }
    });
}

// Stub function for todo items initialization (reserved for future use)
function initTodoItems() {
    // Currently no-op - todo functionality handled via LiveView events
}

// Smart default rate limiting by input type
// Prevents VDOM version mismatches from high-frequency events.
// Click-fired widgets (radio/checkbox/select) commit one value per user
// interaction, so there's no event stream to batch — 'passthrough' skips
// the rate-limit wrapper entirely.
const DEFAULT_RATE_LIMITS = {
    'range': { type: 'throttle', ms: 150 },      // Sliders
    'number': { type: 'throttle', ms: 100 },     // Number spinners
    'color': { type: 'throttle', ms: 150 },      // Color pickers
    'text': { type: 'debounce', ms: 300 },       // Text inputs
    'search': { type: 'debounce', ms: 300 },     // Search boxes
    'email': { type: 'debounce', ms: 300 },      // Email inputs
    'url': { type: 'debounce', ms: 300 },        // URL inputs
    'tel': { type: 'debounce', ms: 300 },        // Phone inputs
    'password': { type: 'debounce', ms: 300 },   // Password inputs
    'textarea': { type: 'debounce', ms: 300 },   // Multi-line text
    'radio': { type: 'passthrough' },            // Click-fired, one value per click
    'checkbox': { type: 'passthrough' },         // Click-fired, one value per click
    'select-one': { type: 'passthrough' },       // Click-fired, one value per click
    'select-multiple': { type: 'passthrough' }   // Click-fired, committed per option click
};

/**
 * Parse an event handler string to extract function name and arguments.
 *
 * Supports syntax like:
 *   "handler"              -> { name: "handler", args: [] }
 *   "handler()"            -> { name: "handler", args: [] }
 *   "handler('arg')"       -> { name: "handler", args: ["arg"] }
 *   "handler(123)"         -> { name: "handler", args: [123] }
 *   "handler(true)"        -> { name: "handler", args: [true] }
 *   "handler('a', 123)"    -> { name: "handler", args: ["a", 123] }
 *
 * @param {string} handlerString - The handler attribute value
 * @returns {Object} - { name: string, args: any[] }
 */
function parseEventHandler(handlerString) {
    const str = handlerString.trim();
    const parenIndex = str.indexOf('(');

    // No parentheses - simple handler name
    if (parenIndex === -1) {
        return { name: str, args: [] };
    }

    const name = str.slice(0, parenIndex).trim();

    // Validate handler name is a valid Python identifier
    if (!/^[a-zA-Z_][a-zA-Z0-9_]*$/.test(name)) {
        console.warn(`[LiveView] Invalid handler name: "${name}", treating as literal`);
        return { name: str, args: [] };
    }

    const closeParen = str.lastIndexOf(')');

    // Invalid syntax - missing close paren, treat as simple name
    if (closeParen === -1 || closeParen < parenIndex) {
        return { name: str, args: [] };
    }

    const argsStr = str.slice(parenIndex + 1, closeParen).trim();

    // Empty parentheses
    if (!argsStr) {
        return { name, args: [] };
    }

    return { name, args: parseArguments(argsStr) };
}

/**
 * Parse comma-separated arguments into typed values.
 * Handles quoted strings, numbers, booleans, and null.
 *
 * @param {string} argsStr - Arguments string (e.g., "'hello', 123, true")
 * @returns {any[]} - Array of parsed argument values
 */
function parseArguments(argsStr) {
    const args = [];
    let current = '';
    let inString = false;
    let stringChar = null;
    let i = 0;

    while (i < argsStr.length) {
        const char = argsStr.charAt(i);

        if (inString) {
            if (char === '\\' && i + 1 < argsStr.length) {
                // Handle escaped characters
                current += char + argsStr[i + 1];
                i += 2;
                continue;
            }
            if (char === stringChar) {
                // End of string
                inString = false;
                current += char;
            } else {
                current += char;
            }
        } else {
            if (char === '"' || char === "'") {
                // Start of string
                inString = true;
                stringChar = char;
                current += char;
            } else if (char === ',') {
                // Argument separator
                const parsed = parseSingleArgument(current.trim());
                if (parsed !== undefined) {
                    args.push(parsed);
                }
                current = '';
            } else {
                current += char;
            }
        }
        i++;
    }

    // Handle the last argument
    if (current.trim()) {
        const parsed = parseSingleArgument(current.trim());
        if (parsed !== undefined) {
            args.push(parsed);
        }
    }

    return args;
}

/**
 * Parse a single argument value into its typed representation.
 *
 * @param {string} value - Single argument value string
 * @returns {any} - Parsed value (string, number, boolean, or null)
 */
function parseSingleArgument(value) {
    if (!value) return undefined;

    // Quoted string - remove quotes and handle escapes
    if ((value.startsWith("'") && value.endsWith("'")) ||
        (value.startsWith('"') && value.endsWith('"'))) {
        const inner = value.slice(1, -1);
        // Handle escape sequences in a single pass to avoid double-processing
        // e.g., \\t should become \t (backslash-t), not tab character
        return inner.replace(/\\(.)/g, (match, char) => {
            switch (char) {
                case 'n': return '\n';
                case 't': return '\t';
                case 'r': return '\r';
                case '\\': return '\\';
                case "'": return "'";
                case '"': return '"';
                default: return char; // Unknown escape, just return the char
            }
        });
    }

    // Boolean
    if (value === 'true') return true;
    if (value === 'false') return false;

    // Null
    if (value === 'null') return null;

    // Number (integer or float)
    if (/^-?\d+$/.test(value)) {
        return parseInt(value, 10);
    }
    if (/^-?\d*\.\d+$/.test(value) || /^-?\d+\.\d*$/.test(value)) {
        return parseFloat(value);
    }

    // Unknown - return as string (without quotes)
    return value;
}

// Export for global access and testing
window.djust = window.djust || {};
window.djust.parseEventHandler = parseEventHandler;

/**
 * Extract parameters from element data-* attributes with optional type coercion.
 *
 * Supports typed attributes via suffix notation:
 *   data-sender-id:int="42"     -> { sender_id: 42 }
 *   data-enabled:bool="true"    -> { enabled: true }
 *   data-price:float="19.99"    -> { price: 19.99 }
 *   data-tags:json='["a","b"]'  -> { tags: ["a", "b"] }
 *   data-items:list="a,b,c"     -> { items: ["a", "b", "c"] }
 *   data-name="John"            -> { name: "John" } (default: string)
 *
 * Backward compatibility: Also reads dj-params='{"key": value}' JSON blob
 * for 0.3.2 → 0.3.6+ migration. The dj-params attribute is deprecated;
 * use individual data-* attributes instead. data-* attributes take
 * precedence over dj-params keys with the same name.
 *
 * @param {HTMLElement} element - Element to extract params from
 * @returns {Object} - Parameters with coerced types
 */
function extractTypedParams(element) {
    const params = Object.create(null); // null prototype prevents prototype-pollution

    for (const attr of element.attributes) {
        if (!attr.name.startsWith('data-')) continue;

        // Skip djust internal attributes
        if (attr.name.startsWith('data-liveview') ||
            attr.name.startsWith('data-live-') ||
            attr.name.startsWith('data-djust') ||
            attr.name === 'dj-id' ||
            attr.name === 'data-loading' ||
            attr.name === 'data-component-id') {
            continue;
        }

        // Parse attribute name: data-sender-id:int -> key="sender_id", type="int"
        const nameWithoutPrefix = attr.name.slice(5); // Remove "data-"
        const colonIndex = nameWithoutPrefix.lastIndexOf(':');
        let rawKey, typeHint;

        if (colonIndex !== -1) {
            rawKey = nameWithoutPrefix.slice(0, colonIndex);
            typeHint = nameWithoutPrefix.slice(colonIndex + 1);
        } else {
            rawKey = nameWithoutPrefix;
            typeHint = null;
        }

        // Convert kebab-case to snake_case, then strip dj_ namespace prefix
        // so data-dj-preset="x" becomes {preset: "x"}, not {dj_preset: "x"}
        let key = rawKey.replace(/-/g, '_');
        if (key.startsWith('dj_')) {
            key = key.slice(3);
        }

        // Prevent prototype pollution attacks
        if (UNSAFE_KEYS.includes(key)) {
            continue;
        }
        let value = attr.value;

        // Apply type coercion based on suffix
        if (typeHint) {
            // Sanitize attribute name for logging (truncate, alphanumeric only)
            const safeAttrName = String(attr.name).slice(0, 50).replace(/[^a-z0-9-:]/gi, '_');

            switch (typeHint) {
                case 'int':
                case 'integer': {
                    if (value === '') {
                        value = 0;
                    } else {
                        const parsed = parseInt(value, 10);
                        if (isNaN(parsed)) {
                            console.warn(`[LiveView] Invalid int value for ${safeAttrName}: "${value}", using null`);
                            value = null;  // Let server-side validation handle invalid input
                        } else {
                            value = parsed;
                        }
                    }
                    break;
                }

                case 'float':
                case 'number': {
                    if (value === '') {
                        value = 0.0;
                    } else {
                        const parsed = parseFloat(value);
                        if (isNaN(parsed)) {
                            console.warn(`[LiveView] Invalid float value for ${safeAttrName}: "${value}", using null`);
                            value = null;  // Let server-side validation handle invalid input
                        } else {
                            value = parsed;
                        }
                    }
                    break;
                }

                case 'bool':
                case 'boolean':
                    value = ['true', '1', 'yes', 'on', 'checked'].includes(value.toLowerCase());
                    break;

                case 'json':
                case 'object':
                case 'array':
                    try {
                        value = JSON.parse(value);
                    } catch {
                        console.warn(`[LiveView] Failed to parse JSON for ${safeAttrName}: "${value}"`);
                        // Keep as string if JSON parse fails - server will validate
                    }
                    break;

                case 'list':
                    // Comma-separated list
                    value = value ? value.split(',').map(v => v.trim()).filter(v => v) : [];
                    break;

                // Unknown type hint - keep as string
                default:
                    console.warn(`[LiveView] Unknown type hint "${typeHint}" for ${safeAttrName}, keeping as string`);
                    break;
            }
        }

        params[key] = value;
    }

    // dj-params backward compatibility: merge JSON blob into params.
    // data-* attributes take precedence over dj-params keys.
    const djParamsAttr = element.getAttribute('dj-params');
    if (djParamsAttr !== null) {
        if (globalThis.djustDebug) {
            console.warn(
                '[LiveView] dj-params is deprecated and will be removed in a future release. ' +
                'Replace with individual data-* attributes, e.g. data-todo-id:int="{{ todo.id }}". ' +
                'See the 0.3.2 → 0.3.6 migration guide in CHANGELOG.md.'
            );
        }
        if (djParamsAttr !== '') {
            try {
                const parsed = JSON.parse(djParamsAttr);
                if (parsed !== null && typeof parsed === 'object' && !Array.isArray(parsed)) {
                    for (const [k, v] of Object.entries(parsed)) {
                        // Prevent prototype pollution
                        if (UNSAFE_KEYS.includes(k)) continue;
                        // data-* attributes win; only fill in missing keys
                        if (!(k in params)) {
                            params[k] = v;
                        }
                    }
                }
            } catch {
                if (globalThis.djustDebug) console.warn('[LiveView] Failed to parse dj-params JSON: "' + djParamsAttr + '"');
            }
        }
    }

    // Merge dj-value-* attributes. dj-value-* takes precedence over data-*
    // and dj-params, matching Phoenix's phx-value-* semantics.
    const djValues = collectDjValues(element);
    for (const [k, v] of Object.entries(djValues)) {
        params[k] = v;
    }

    return params;
}

/**
 * Collect dj-value-* attributes from an element and return as a params object.
 *
 * dj-value-* is the standard way to pass static context alongside events
 * (Phoenix LiveView's phx-value-* equivalent). Supports the same type-hint
 * suffixes as data-* attributes.
 *
 * Examples:
 *   dj-value-id="42"             -> { id: "42" }
 *   dj-value-id:int="42"        -> { id: 42 }
 *   dj-value-item-type="soft"   -> { item_type: "soft" }
 *   dj-value-active:bool="true" -> { active: true }
 *   dj-value-tags:list="a,b,c"  -> { tags: ["a", "b", "c"] }
 *
 * @param {HTMLElement} element - Element to extract dj-value-* from
 * @returns {Object} - Collected params with coerced types
 */
function collectDjValues(element) {
    const values = Object.create(null);

    for (const attr of element.attributes) {
        if (!attr.name.startsWith('dj-value-')) continue;

        // Parse: dj-value-item-id:int -> key="item_id", type="int"
        const nameWithoutPrefix = attr.name.slice(9); // Remove "dj-value-"
        const colonIndex = nameWithoutPrefix.lastIndexOf(':');
        let rawKey, typeHint;

        if (colonIndex !== -1) {
            rawKey = nameWithoutPrefix.slice(0, colonIndex);
            typeHint = nameWithoutPrefix.slice(colonIndex + 1);
        } else {
            rawKey = nameWithoutPrefix;
            typeHint = null;
        }

        // Convert kebab-case to snake_case
        const key = rawKey.replace(/-/g, '_');

        // Prevent prototype pollution
        if (UNSAFE_KEYS.includes(key)) continue;

        let value = attr.value;

        // Apply type coercion (same logic as extractTypedParams)
        if (typeHint) {
            switch (typeHint) {
                case 'int':
                case 'integer': {
                    if (value === '') { value = 0; }
                    else {
                        const parsed = parseInt(value, 10);
                        value = isNaN(parsed) ? null : parsed;
                    }
                    break;
                }
                case 'float':
                case 'number': {
                    if (value === '') { value = 0.0; }
                    else {
                        const parsed = parseFloat(value);
                        value = isNaN(parsed) ? null : parsed;
                    }
                    break;
                }
                case 'bool':
                case 'boolean':
                    value = ['true', '1', 'yes', 'on', 'checked'].includes(value.toLowerCase());
                    break;
                case 'json':
                case 'object':
                case 'array':
                    try { value = JSON.parse(value); }
                    catch { /* keep as string */ }
                    break;
                case 'list':
                    value = value ? value.split(',').map(v => v.trim()).filter(v => v) : [];
                    break;
                default:
                    break;
            }
        }

        values[key] = value;
    }

    return values;
}

// Export for global access
window.djust = window.djust || {};
window.djust.extractTypedParams = extractTypedParams;
window.djust.collectDjValues = collectDjValues;
