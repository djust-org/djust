// The real browser client, bridged to a real djust server over stdio (#4178).
//
// A jsdom page parsed from the server's real GET document evaluates the built
// `client.js`. Its WebSocket, EventSource and fetch are stand-ins that only
// relay: every frame the client sends is written to stdout as one JSON line,
// and the Python test answers with frames the real server produced (a
// `WebsocketCommunicator`, the SSE stream and message views, the page POST).
// Nothing here builds a server frame or an event payload itself.
//
// stdin  (one JSON object per line, from the test):
//   {op: 'ws_open', id}                 the socket `id` is connected
//   {op: 'ws_message', id, data}        a server frame for socket `id`
//   {op: 'ws_close', id, code}          the server closed socket `id`
//   {op: 'sse_open'}                    the EventSource (re)connected
//   {op: 'sse_message', data}           a server frame on the stream
//   {op: 'sse_drop'}                    the stream dropped (the browser retries)
//   {op: 'fetch_reply', id, status, body}
//   {op: 'eval', code}                  run code in the page, reply 'eval_result'
//   {op: 'quit'}
// stdout:
//   {op: 'ready'} | {op: 'ws_connect', id, url} | {op: 'ws_send', id, data}
//   {op: 'sse_connect', url} | {op: 'fetch', id, url, method, headers, body}
//   {op: 'eval_result', value} | {op: 'log', level, text}
import fs from 'node:fs';
import readline from 'node:readline';
import { JSDOM, VirtualConsole } from 'jsdom';

// The page URL is optional (argv 3): the address the client mounts and posts to.
const [pagePath, transport, pageUrl] = process.argv.slice(2);
const out = message => process.stdout.write(JSON.stringify(message) + '\n');

const virtualConsole = new VirtualConsole();
for (const level of ['warn', 'error']) {
    virtualConsole.on(level, (...args) => out({op: 'log', level, text: args.map(String).join(' ')}));
}
virtualConsole.on('jsdomError', error => out({op: 'log', level: 'jsdomError', text: String(error.message)}));

const dom = new JSDOM(fs.readFileSync(pagePath, 'utf8'), {
    url: pageUrl || 'http://testserver/4178/', runScripts: 'dangerously', virtualConsole,
});
const win = dom.window;
if (!win.CSS) win.CSS = {};
if (!win.CSS.escape) win.CSS.escape = s => String(s).replace(/([^\w-])/g, '\\$1');
// No paint in jsdom: the post-mount work runs synchronously.
win.requestAnimationFrame = undefined;
try { win.location.reload = () => out({op: 'log', level: 'reload', text: 'reload'}); } catch (_e) { /* fixed */ }

const sockets = [];
class BridgedWebSocket {
    static CONNECTING = 0;
    static OPEN = 1;
    static CLOSING = 2;
    static CLOSED = 3;
    constructor(url) {
        this.url = String(url);
        this.readyState = 0;
        this.id = sockets.length;
        sockets.push(this);
        out({op: 'ws_connect', id: this.id, url: this.url});
    }
    send(data) { out({op: 'ws_send', id: this.id, data: String(data)}); }
    close(code = 1000) {
        if (this.readyState === 3) return;
        this.readyState = 3;
        if (this.onclose) this.onclose({code, reason: '', wasClean: true});
    }
}

let eventSource = null;
class BridgedEventSource {
    constructor(url) {
        this.url = String(url);
        this.readyState = 0;
        eventSource = this;
        out({op: 'sse_connect', url: this.url});
    }
    close() { this.readyState = 2; }
    addEventListener() {}
}

const pendingFetches = new Map();
let fetchId = 0;
function bridgedFetch(url, init = {}) {
    const id = fetchId++;
    out({op: 'fetch', id, url: String(url), method: init.method || 'GET',
        headers: init.headers || {}, body: init.body === undefined ? null : String(init.body)});
    return new Promise(resolve => pendingFetches.set(id, resolve));
}

if (transport === 'sse') {
    win.DJUST_USE_WEBSOCKET = false;
    win.EventSource = BridgedEventSource;
} else if (transport === 'http') {
    win.DJUST_USE_WEBSOCKET = false;
    delete win.EventSource;
} else {
    win.WebSocket = BridgedWebSocket;
}
win.fetch = bridgedFetch;

win.eval(fs.readFileSync('./python/djust/static/djust/client.js', 'utf8'));
if (win.document.readyState === 'loading') {
    await new Promise(resolve => win.document.addEventListener('DOMContentLoaded', resolve));
}
out({op: 'ready'});

const lines = readline.createInterface({input: process.stdin});
for await (const line of lines) {
    const command = JSON.parse(line);
    if (command.op === 'quit') break;
    if (command.op === 'ws_open') {
        const socket = sockets[command.id];
        socket.readyState = 1;
        socket.onopen && socket.onopen({});
    } else if (command.op === 'ws_message') {
        const socket = sockets[command.id];
        socket.onmessage && socket.onmessage({data: command.data});
    } else if (command.op === 'ws_close') {
        const socket = sockets[command.id];
        socket.readyState = 3;
        socket.onclose && socket.onclose({code: command.code || 1006, reason: '', wasClean: false});
    } else if (command.op === 'sse_open') {
        eventSource.readyState = 1;
        eventSource.onopen && eventSource.onopen({});
    } else if (command.op === 'sse_message') {
        eventSource.onmessage && eventSource.onmessage({data: command.data});
    } else if (command.op === 'sse_drop') {
        // The browser keeps the EventSource and re-requests the same URL.
        eventSource.readyState = 0;
        eventSource.onerror && eventSource.onerror({});
    } else if (command.op === 'fetch_reply') {
        const resolve = pendingFetches.get(command.id);
        pendingFetches.delete(command.id);
        const body = command.body;
        resolve({
            ok: command.status >= 200 && command.status < 300,
            status: command.status,
            headers: new win.Headers({'Content-Type': 'application/json'}),
            json: async () => JSON.parse(body),
            text: async () => body,
        });
    } else if (command.op === 'eval') {
        let value;
        try { value = win.eval(command.code); } catch (error) { value = {error: String(error)}; }
        out({op: 'eval_result', value: value === undefined ? null : value});
    }
    // Let the client's promise chains run before the next command.
    await new Promise(resolve => setTimeout(resolve, 0));
    out({op: 'idle'});
}
process.exit(0);
