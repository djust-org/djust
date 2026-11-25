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

// Export for global access and testing. Explicit exports are required now
// that the bundle is IIFE-wrapped (#1635) — top-level functions are no longer
// implicit globals, so anything callable from outside the bundle (tests,
// re-init hooks) must be attached to the namespace here.
window.djust = window.djust || {};
window.djust.parseEventHandler = parseEventHandler;

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

        // Apply type coercion
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

        // eslint-disable-next-line security/detect-object-injection
        values[key] = value;
    }

    return values;
}

// A mount owns its manifest. Never merge contracts by handler name, or retain
// a previous mount's contracts when a response omits this field. Wire v2
// (#4307): distinct contracts and handler tables are listed once, and each
// owner row names its table; owners share the (read-only) table Maps.
function _installParameterContracts(transport, manifest, viewPath, resetPrimary = true, receiptOrder = 0) {
    if (!transport._parameterContracts || (resetPrimary && viewPath === transport.primaryViewPath)) {
        transport._parameterContracts = new Map();
        transport._parameterContractApplied = new Map();
        transport._parameterContractDigest = new Map();
    }
    if (resetPrimary) transport._parameterContractApplied.set(viewPath, receiptOrder);
    transport._parameterContracts.set(viewPath, null);
    (transport._parameterContractDigest ??= new Map()).delete(viewPath);
    if (manifest === undefined || manifest === null) return;
    const reject = () => { throw new Error('Invalid public parameter contracts'); };
    // Keep invalid metadata distinguishable from a tree with no handlers.
    transport._parameterContracts.set(viewPath, false);
    if (typeof viewPath !== 'string' || !viewPath) reject();
    const n = Number.isInteger, ok = id => id === null || (typeof id === 'string' && id.length > 0);
    if (manifest.version !== 2 || typeof manifest.digest !== 'string' || !Array.isArray(manifest.contracts) ||
        !Array.isArray(manifest.handlers) || !Array.isArray(manifest.owners) || manifest.contracts.length > 1024 ||
        manifest.handlers.length > 1024 || manifest.owners.length > 10000 ||
        JSON.stringify([manifest.contracts, manifest.handlers]).length > 65536) reject();
    const contracts = manifest.contracts.map(contract => {
        if (!contract || typeof contract.coerce_types !== 'boolean' || !Array.isArray(contract.parameters) || contract.parameters.length > 1024) reject();
        const seen = new Set();
        const parameters = contract.parameters.map(parameter => {
            if (!parameter || typeof parameter.name !== 'string' || seen.has(parameter.name) || typeof parameter.type !== 'string' ||
                !['positional_only', 'positional_or_keyword', 'keyword_only', 'var_positional', 'var_keyword'].includes(parameter.kind) ||
                typeof parameter.required !== 'boolean' || typeof parameter.reduced_checking !== 'boolean') reject();
            seen.add(parameter.name);
            return Object.freeze({name: parameter.name, type: parameter.type, kind: parameter.kind,
                required: parameter.required, reduced_checking: parameter.reduced_checking});
        });
        return Object.freeze({coerce_types: contract.coerce_types, parameters: Object.freeze(parameters)});
    });
    let count = 0;
    const tables = manifest.handlers.map(table => {
        if (!table || typeof table !== 'object' || Array.isArray(table)) reject();
        const handlers = new Map();
        for (const [name, index] of Object.entries(table)) {
            if (++count > 10000 || !/^[a-zA-Z][a-zA-Z0-9_]*$/.test(name) || !n(index) || !contracts[index]) reject();
            handlers.set(name, contracts[index]);
        }
        return handlers;
    });
    const owners = new Map();
    for (const row of manifest.owners) {
        if (!Array.isArray(row) || row.length !== 3 || !ok(row[0]) || !ok(row[1]) || !n(row[2]) || !tables[row[2]]) reject();
        const key = JSON.stringify([row[0], row[1]]);
        if (owners.has(key)) reject();
        owners.set(key, tables[row[2]]);
    }
    if (!owners.has('[null,null]')) reject();
    transport._parameterContracts.set(viewPath, owners);
    transport._parameterContractDigest.set(viewPath, manifest.digest);
}

// Receipt order is client-owned, not a wire field or the VDOM version (child
// replies have no parent VDOM version). Weak keys cannot retain consumed frames.
function _recordParameterContractFrame(transport, data) {
    if (!data || typeof data !== 'object') return;
    transport._parameterContractFrames ??= new WeakMap();
    transport._parameterContractSequence = (transport._parameterContractSequence || 0) + 1;
    transport._parameterContractFrames.set(data, transport._parameterContractSequence);
}

// The mount whose contracts a render frame refreshes: the page view's, or the
// slot's while a slot's frame is applied (#3252).
function _contractScopePath(transport) {
    return _activeSlot ? _activeSlot.viewPath : transport?.primaryViewPath;
}

// A failed/partial DOM application cannot keep advertising the last successful
// strict snapshot. Invalidate only this transport's primary scope, never peers.
function _invalidateRenderParameterContracts(transport, data) {
    const path = _contractScopePath(transport);
    const mounts = transport?._parameterContracts;
    if (mounts?.has(path) && (mounts.get(path) !== null || Object.hasOwn(data, 'parameter_contracts'))) {
        mounts.set(path, false);
        transport._parameterContractDigest?.delete(path);
        const order = transport._parameterContractFrames?.get(data);
        if (order !== undefined) transport._parameterContractApplied.set(path,
            Math.max(order, transport._parameterContractApplied.get(path) || 0));
    }
}

// Called after DOM application (including empty patches), before dj-mounted
// or other bindings can run. Omission is safe only for a scope whose tree declares no handlers.
function _refreshRenderParameterContracts(transport, data) {
    const supplied = Object.hasOwn(data, 'parameter_contracts');
    if (!transport) {
        if (supplied && globalThis.djustDebug) console.warn('[LiveView] Missing parameter contract transport');
        return;
    }
    const order = transport._parameterContractFrames?.get(data);
    const scopePath = _contractScopePath(transport);
    const applied = transport._parameterContractApplied?.get(scopePath);
    // A newer applied response already supplied a whole-tree snapshot. Replaying
    // an older buffered delta must not replace it, even with a missing snapshot.
    if (order !== undefined && applied !== undefined && order <= applied) return;
    if (!supplied) {
        _invalidateRenderParameterContracts(transport, data);
        if (order !== undefined && applied !== undefined) {
            transport._parameterContractApplied.set(scopePath, order);
        }
        return;
    }
    const path = data.parameter_contract_view;
    const root = getLiveViewRoot();
    if (order === undefined || typeof path !== 'string' || path !== scopePath ||
        root.getAttribute('dj-view') !== path || !transport._parameterContracts?.has(path)) {
        _invalidateRenderParameterContracts(transport, data);
        if (globalThis.djustDebug) console.warn('[LiveView] Unknown render parameter contract mount');
        return;
    }
    transport._parameterContractApplied.set(path, order);
    try {
        _installParameterContracts(transport, data.parameter_contracts, path, false);
    } catch {
        // Metadata cannot prevent the originating response from acknowledging
        // its request. Strict lookups fail closed on the invalid scope instead.
        _invalidateRenderParameterContracts(transport, data);
        if (globalThis.djustDebug) console.warn('[LiveView] Invalid render parameter contracts');
    }
}

function _lookupParameterContract(transport, viewPath, viewId, componentId, eventName) {
    const mounts = transport._parameterContracts;
    if (!mounts || !mounts.has(viewPath)) throw new Error('Unknown parameter contract mount');
    const owners = mounts.get(viewPath);
    if (owners === null) return null;
    if (owners === false) throw new Error('Invalid public parameter contracts');
    const handlers = owners.get(JSON.stringify([viewId, componentId]));
    if (!handlers || !handlers.has(eventName)) throw new Error('Unknown parameter contract owner or handler');
    return handlers.get(eventName);
}

// ADR-036 staged collector. The
// owner-scoped binder must supply only documented generated application values;
// routing context is attached afterwards, never collected from markup here.
// `accept(key, hint)` lets the binder veto a dj-value-* argument (contract checks);
// it returns null to skip one silently (a framework-marked optional key, #4285).
function _collectStrictEventParams(element, generated = {}, positional = [], accept = () => true) {
    const reject = () => { throw new Error('Invalid strict event arguments'); };
    const values = Object.create(null);
    // Routing names; every "_" name (_args, client bookkeeping) fails the
    // identifier rule in put() below.
    const reserved = new Set([...UNSAFE_KEYS, 'component_id', 'view_id']);
    let nodes = 0;
    let textSize = 0;
    const active = new Set();
    const plainObject = value => {
        const proto = Object.getPrototypeOf(value);
        return proto === null || Object.getPrototypeOf(proto) === null;
    };
    const ownValue = (object, key) => {
        const descriptor = Object.getOwnPropertyDescriptor(object, key);
        if (!descriptor || !Object.hasOwn(descriptor, 'value')) reject();
        return descriptor.value;
    };
    const visit = (value, depth = 0) => {
        if (++nodes > 10000 || depth > 32) reject();
        if (typeof value === 'string') {
            textSize += value.length;
            if (textSize > 65536) reject();
        } else if (typeof value === 'number') {
            if (!Number.isFinite(value)) reject();
        } else if (value !== null && typeof value === 'object') {
            if (active.has(value)) reject();
            const array = Array.isArray(value);
            if (array && value.length > 1024) reject();
            if (!array && !plainObject(value)) reject();
            const keys = array ? null : Object.keys(value);
            if (keys && keys.length > 1024) reject();
            active.add(value);
            const snapshot = array ? [] : Object.create(null);
            if (array) {
                for (let i = 0; i < value.length; i++) {
                    snapshot.push(visit(ownValue(value, String(i)), depth + 1));
                }
            } else {
                for (const key of keys) {
                    visit(key, depth + 1);
                    // eslint-disable-next-line security/detect-object-injection
                    snapshot[key] = visit(ownValue(value, key), depth + 1);
                }
            }
            active.delete(value);
            return snapshot;
        } else if (value !== null && typeof value !== 'boolean') reject();
        return value;
    };
    const put = (key, value) => {
        // "_" names are framework-reserved on the server too (ADR-036 D5).
        if (!/^[a-zA-Z][a-zA-Z0-9_]*$/.test(key) || reserved.has(key) || Object.hasOwn(values, key)) reject();
        // eslint-disable-next-line security/detect-object-injection
        values[key] = value;
    };
    if (!generated || typeof generated !== 'object' || Array.isArray(generated) || !plainObject(generated) || !Array.isArray(positional)) reject();
    for (const key of Object.keys(generated)) put(key, ownValue(generated, key));
    let literalSize = 0;
    for (const attr of element.attributes) {
        if (!attr.name.startsWith('dj-value-')) continue;
        literalSize += attr.value.length + attr.name.length;
        if (literalSize > 65536) reject();
        const parts = attr.name.slice(9).split(':');
        if (parts.length > 2 || (parts.length === 2 && !parts[1])) reject();
        const key = parts[0].replace(/-/g, '_');
        const hint = parts[1];
        const verdict = accept(key, hint);
        if (verdict === null) continue;
        if (!verdict) reject();
        let value = attr.value;
        const text = value.replace(/^[ \t\n\r\v\f]+|[ \t\n\r\v\f]+$/g, '');
        if (hint) {
            switch (hint) {
                case 'int': case 'integer':
                    if (text.length > 1024 || !/^[+-]?[0-9]+(?![\s\S])/.test(text)) reject();
                    value = Number(text);
                    if (!Number.isSafeInteger(value)) reject();
                    break;
                case 'float': case 'number':
                    // Bounded input and disjoint decimal/exponent delimiters.
                    // eslint-disable-next-line security/detect-unsafe-regex
                    if (text.length > 1024 || !/^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?(?![\s\S])/.test(text)) reject();
                    value = Number(text);
                    break;
                case 'bool': case 'boolean': {
                    const lower = text.toLowerCase();
                    if (!['true', 'false', '1', '0', 'yes', 'no', 'on', 'off'].includes(lower)) reject();
                    value = ['true', '1', 'yes', 'on'].includes(lower);
                    break;
                }
                case 'json': case 'array': case 'list': case 'object':
                    try { value = JSON.parse(value); } catch { reject(); }
                    // Inspect number tokens before their integer spelling is
                    // lost. Strings are matched as whole tokens, including
                    // escapes, so their digits are never treated as numbers.
                    // JSON.parse has already validated this bounded literal;
                    // the alternatives have disjoint starting characters.
                    // eslint-disable-next-line security/detect-unsafe-regex
                    for (const token of attr.value.match(/"(?:\\[\s\S]|[^"\\])*"|-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?/g) || []) {
                        if (!token.startsWith('"') && !/[.eE]/.test(token) && !Number.isSafeInteger(Number(token))) reject();
                    }
                    if ((hint === 'array' || hint === 'list') && !Array.isArray(value)) reject();
                    if (hint === 'object' && (value === null || typeof value !== 'object' || Array.isArray(value))) reject();
                    break;
                default: reject();
            }
        }
        put(key, value);
    }
    const snapshot = visit(values);
    const args = visit(positional);
    if (args.length) snapshot._args = args;
    return snapshot;
}

// Export for global access
window.djust = window.djust || {};
window.djust.collectDjValues = collectDjValues;
