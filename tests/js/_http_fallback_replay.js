/**
 * Shared harness for the page-POST fallback replay tests (#4108): a jsdom page
 * parsed from a real GET document, with the built `client.js` evaluated in it,
 * and one fallback request answered at a time.
 */
import {JSDOM, VirtualConsole} from 'jsdom';
import {readFileSync} from 'node:fs';

const client = readFileSync('./python/djust/static/djust/client.js', 'utf8');

export const DIGEST_HEADER = 'X-Djust-Render-Digest';

/** The once-per-session notice that events are going over HTTP (#1674). */
const FALLBACK_NOTICE = /falling back to full-page HTTP re-renders/;

/** Resolves once the client initialised (it does so on DOMContentLoaded). */
export async function loadPage(documentHtml, path = '/') {
    const navigations = [];
    const logs = [];
    const virtualConsole = new VirtualConsole();
    virtualConsole.on('jsdomError', e => {
        if (String(e.message).includes('navigation')) navigations.push(e.message);
        else logs.push(['jsdomError', String(e.message)]);
    });
    for (const level of ['warn', 'error']) {
        virtualConsole.on(level, (...args) => logs.push([level, args.map(String).join(' ')]));
    }
    // Parsed by the HTML parser, so inter-element whitespace text nodes exist.
    // The page's external <script src> is not fetched (no `resources` option).
    const dom = new JSDOM(documentHtml, {
        url: 'http://localhost' + path, runScripts: 'dangerously', virtualConsole,
    });
    const win = dom.window;
    win.eval(client);
    let reloaded = 0;
    try { win.location.reload = () => { reloaded += 1; }; } catch (_e) { /* non-writable */ }
    if (win.document.readyState === 'loading') {
        await new Promise(resolve => win.document.addEventListener('DOMContentLoaded', resolve));
    }
    const requests = [];
    return {
        dom, win, requests,
        /**
         * Send one event over the fallback; `answer` is the POST's JSON body,
         * or a function `(request) => body`. Returns the request the client made.
         */
        async send(event, params, answer, target = null) {
            const before = requests.length;
            win.fetch = async (url, init = {}) => {
                const headers = init.headers || {};
                const request = {
                    url: String(url), method: init.method || 'GET',
                    digest: headers[DIGEST_HEADER] ?? null,
                    event: headers['X-Djust-Event'] ?? null,
                    body: init.body ?? null,
                };
                requests.push(request);
                const body = typeof answer === 'function' ? await answer(request) : answer;
                if (typeof body === 'string') {
                    return {ok: true, status: 200, text: async () => body, json: async () => JSON.parse(body)};
                }
                return {ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(body))};
            };
            await win.djust.handleEvent(event, {...params, _targetElement: target});
            return requests[before] || null;
        },
        reloads: () => reloaded + navigations.length,
        problems: () => logs.filter(l => !FALLBACK_NOTICE.test(l[1])).map(l => l[1].slice(0, 300)),
        close: () => win.close(),
    };
}

/** The element an event of the matrix is sent from. */
export function eventTarget(win, event, params) {
    const doc = win.document;
    if (params && params.field_name) return doc.querySelector(`[name="${params.field_name}"]`);
    return doc.querySelector(`[dj-click="${event}"],[dj-change="${event}"]`);
}

// Generated dj-id values, attribute order, whitespace-only text and comments
// other than dj-if markers are not part of the comparison.
function normalize(node) {
    if (node.nodeType === 1) {
        const attrs = {};
        for (const name of node.getAttributeNames().sort()) {
            if (name === 'dj-id') continue;
            let value = node.getAttribute(name);
            // classList round-trips trim the value and can leave class="" behind.
            if (name === 'class') {
                value = value.trim().split(/\s+/).join(' ');
                if (value === '') continue;
            }
            attrs[name] = value;
        }
        return {tag: node.tagName.toLowerCase(), attrs,
            children: [...node.childNodes].map(normalize).filter(Boolean)};
    }
    if (node.nodeType === 3) {
        const text = node.textContent.replace(/\s+/g, ' ').trim();
        return text === '' ? null : {text};
    }
    if (node.nodeType === 8 && /^\/?dj-if/.test(node.textContent)) {
        return {comment: node.textContent.startsWith('/') ? '/dj-if' : 'dj-if'};
    }
    return null;
}

function firstDifference(a, b, path) {
    if (!a || !b) return `${path}: ${JSON.stringify(a)?.slice(0, 160)} vs ${JSON.stringify(b)?.slice(0, 160)}`;
    if (a.text !== undefined || b.text !== undefined) {
        return a.text === b.text ? null : `${path}: text ${JSON.stringify(a.text)} vs ${JSON.stringify(b.text)}`;
    }
    if (a.comment !== undefined || b.comment !== undefined) {
        return a.comment === b.comment ? null : `${path}: comment ${a.comment} vs ${b.comment}`;
    }
    if (a.tag !== b.tag) return `${path}: tag ${a.tag} vs ${b.tag}`;
    if (JSON.stringify(a.attrs) !== JSON.stringify(b.attrs)) {
        return `${path}<${a.tag}>: attrs client=${JSON.stringify(a.attrs)} server=${JSON.stringify(b.attrs)}`;
    }
    if (a.children.length !== b.children.length) {
        return `${path}<${a.tag}>: ${a.children.length} children (client) vs ${b.children.length} (server)`;
    }
    for (let i = 0; i < a.children.length; i++) {
        const found = firstDifference(a.children[i], b.children[i], `${path}/${a.tag}[${i}]`);
        if (found) return found;
    }
    return null;
}

/**
 * The first difference between the page's root and `serverRootHtml` (the
 * server's render of the root element), or null. The root's own attributes
 * are left out: the page GET stamps `dj-view` on it, a render does not.
 */
export function domDifference(win, serverRootHtml) {
    const root = win.document.querySelector('[dj-root]');
    const template = win.document.createElement('template');
    template.innerHTML = serverRootHtml;
    const actual = normalize(root);
    const expected = normalize(template.content.firstElementChild);
    actual.attrs = expected.attrs = {};
    return firstDifference(actual, expected, 'root');
}
