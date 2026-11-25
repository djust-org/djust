/**
 * Test helper (#4205): the parameter-contract block a server would render for a
 * page, with every handler the markup binds declared as `**kwargs: Any`.
 *
 * A binding whose handler the mount does not list sends only its `dj-value-*`
 * and positional arguments. A suite whose subject is something else (focus,
 * keyboard, polling, ...) and fires bindings in a hand-built page uses this so
 * the generated values (`value`, `field`, `key`, form fields, ...) still reach
 * the recorded request. It covers the root view plus every component id and
 * embedded view id the markup mentions.
 */

import { packManifest } from './_parameter_manifest.js';

const NAME = /^[a-zA-Z][a-zA-Z0-9_]*$/;

function handlerNames(html) {
    const names = new Set();
    for (const [, , value] of html.matchAll(/\b(dj-[a-z-]+(?:\.[a-z0-9._-]+)?|data-[a-z-]*event[a-z-]*|[a-z-]+-event)\s*=\s*"([^"]*)"/g)) {
        // `name`, `name(args)`, and dj-shortcut / keyboard `combo:name[:prevent]` entries.
        for (const part of value.split(',')) {
            for (const candidate of [part.trim().split('(')[0], ...part.split(':').map(s => s.trim())]) {
                if (NAME.test(candidate)) names.add(candidate);
            }
        }
    }
    return names;
}

/** @returns {string} a `<script data-djust-parameter-contracts>` element. */
export function openContracts(html, view = null, extraHandlers = []) {
    view ??= (/\bdj-view\s*=\s*"([^"]+)"/.exec(html) || [])[1] || 'test.V';
    const names = [...handlerNames(html), ...extraHandlers];
    const handlers = {};
    for (const name of names) {
        handlers[name] = {
            policy: 'strict', coerce_types: true,
            parameters: [{name: 'kwargs', type: 'Any', kind: 'var_keyword', required: false, reduced_checking: true}],
        };
    }
    const owners = [{view_id: null, component_id: null, handlers}];
    const seen = new Set();
    for (const [, id] of html.matchAll(/data-component-id\s*=\s*"([^"]+)"/g)) {
        if (!seen.has('c' + id)) { seen.add('c' + id); owners.push({view_id: null, component_id: id, handlers}); }
    }
    for (const [, id] of html.matchAll(/data-djust-embedded\s*=\s*"([^"]+)"/g)) {
        if (!seen.has('v' + id)) { seen.add('v' + id); owners.push({view_id: id, component_id: null, handlers}); }
    }
    const manifest = {view, contracts: packManifest({version: 1, owners})};
    return `<script type="application/json" data-djust-parameter-contracts>${JSON.stringify(manifest)}</script>`;
}

/**
 * `html` as a served page: a `dj-view` root (the `dj-root` element gets the
 * attribute, else the markup is wrapped in one) followed by the contract block.
 */
export function withContracts(html, view = null, extraHandlers = []) {
    let page = html;
    view ??= (/\bdj-view\s*=\s*"([^"]+)"/.exec(page) || [])[1] || 'test.V';
    if (!/\bdj-view\s*=/.test(page)) {
        page = /<div\b[^>]*\bdj-root\b/.test(page)
            ? page.replace(/<div\b([^>]*\bdj-root\b)/, `<div dj-view="${view}" $1`)
            : `<div dj-view="${view}" dj-root>${page}</div>`;
    }
    if (!/\bdj-root\b/.test(page)) page = page.replace(/\bdj-view\s*=/, 'dj-root dj-view=');
    return page + openContracts(page, view, extraHandlers);
}

/**
 * The fields a server's render response carries to restate the contracts. A mock
 * `fetch` spreads them into its answer: a response without them marks the
 * mount's contracts invalid, so a second event in the same page would fail closed.
 */
export function contractFields(html, view = null, extraHandlers = []) {
    const block = withContracts(html, view, extraHandlers);
    const json = /data-djust-parameter-contracts>(.*)<\/script>/s.exec(block)[1];
    const manifest = JSON.parse(json);
    return {parameter_contracts: manifest.contracts, parameter_contract_view: manifest.view};
}
