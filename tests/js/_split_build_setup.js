/**
 * Setup for the `split-build` vitest project (#4007).
 *
 * The tests for the chunked modules (uploads, virtual list, cursor overlay,
 * tutorial bubble, service worker) read the FULL bundle, client.js. In this
 * project a read of client.js returns the CORE bundle followed by every chunk
 * instead, so the same assertions run against the split build: core first,
 * then each chunk evaluated in the same window, which is what the core's
 * loader does before djustInit() binds and connects.
 */
import fs from 'fs';
import path from 'path';
import { syncBuiltinESMExports } from 'module';

const STATIC = path.resolve('python/djust/static/djust');
const FULL = path.join(STATIC, 'client.js');
const CHUNKS = ['uploads', 'virtual-list', 'cursor-overlay', 'tutorial', 'service-worker',
    // #4320
    'component-behaviors', 'keyboard-nav', 'error-overlay', 'dj-style', 'flip', 'transitions',
    'infinite-scroll', 'prefetch', 'track-static', 'dialog'];

const realRead = fs.readFileSync;
let splitSource = null;

function splitBuild() {
    if (splitSource === null) {
        const parts = [realRead(path.join(STATIC, 'client-core.js'), 'utf-8')];
        for (const name of CHUNKS) {
            parts.push(realRead(path.join(STATIC, 'chunks', name + '.js'), 'utf-8'));
        }
        splitSource = parts.join('\n');
    }
    return splitSource;
}

fs.readFileSync = function (file, options) {
    if (typeof file === 'string' && path.resolve(file) === FULL) {
        const text = splitBuild();
        const encoding = typeof options === 'string' ? options : options && options.encoding;
        return encoding ? text : Buffer.from(text, 'utf-8');
    }
    return realRead.apply(this, arguments);
};
syncBuiltinESMExports();
