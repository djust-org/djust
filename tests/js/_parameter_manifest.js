/**
 * Test helper (#4307): pack a per-owner parameter-contract manifest into the
 * wire v2 shape the server sends.
 *
 * Suites describe a manifest the readable way, one owner per entry with its
 * handlers inline (`{version: 1, owners: [{view_id, component_id, handlers}]}`);
 * `packManifest` lists each distinct contract and handler table once, gives each
 * owner a `[view_id, component_id, table]` row, and adds a content digest, as
 * `_parameter_metadata._encode_manifest` does. The digest is a test stand-in
 * (FNV-1a over the canonical body), not the server's blake2b: equal contents
 * get equal digests, which is all the client relies on.
 */

function canonical(value) {
    if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
    if (value && typeof value === 'object') {
        return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`;
    }
    return JSON.stringify(value);
}

export function fakeDigest(text) {
    let a = 0x811c9dc5, b = 0x01000193;
    for (let i = 0; i < text.length; i++) {
        a = Math.imul(a ^ text.charCodeAt(i), 0x01000193) >>> 0;
        b = Math.imul(b ^ text.charCodeAt(i), 0x811c9dc5) >>> 0;
    }
    return a.toString(16).padStart(8, '0') + b.toString(16).padStart(8, '0');
}

/** @returns {object} the v2 manifest for a v1-shaped `{owners: [...]}` description. */
export function packManifest(manifest) {
    const contracts = [], contractIndex = new Map(), handlers = [], tableIndex = new Map();
    const owners = manifest.owners.map(owner => {
        const table = {};
        for (const name of Object.keys(owner.handlers || {}).sort()) {
            const {coerce_types, parameters} = owner.handlers[name];
            const record = {coerce_types, parameters};
            const key = canonical(record);
            if (!contractIndex.has(key)) { contractIndex.set(key, contracts.length); contracts.push(record); }
            table[name] = contractIndex.get(key);
        }
        const key = canonical(table);
        if (!tableIndex.has(key)) { tableIndex.set(key, handlers.length); handlers.push(table); }
        return [owner.view_id, owner.component_id, tableIndex.get(key)];
    });
    const body = {version: 2, contracts, handlers, owners};
    return {...body, digest: fakeDigest(canonical(body))};
}
