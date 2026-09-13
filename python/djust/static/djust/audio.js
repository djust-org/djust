/* djust declarative short audio player — ADR-028. Loaded only by opted-in views. */
(function () {

    if (window.djustAudio) return;
    const roots = new Map();
    let context = null;
    const NAME = /^[a-zA-Z0-9_-]{1,64}$/;
    const MAX_BYTES = 1024 * 1024, MAX_DECODED = 16 * 1024 * 1024;
    function owns(obj, key) { return Object.prototype.hasOwnProperty.call(obj, key); }
    function number(value) { return typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1; }
    function parse(marker) {
        const cfg = JSON.parse(marker.getAttribute('dj-audio'));
        if (cfg.version !== 1 || !/^[a-f0-9]{32}$/.test(cfg.scope) ||
            !cfg.banks || typeof cfg.banks !== 'object' || Array.isArray(cfg.banks) ||
            Object.keys(cfg.banks).length > 4 || !Array.isArray(cfg.origins) || cfg.origins.length > 8) throw Error('Invalid audio manifest');
        const origins = new Set([location.origin]);
        cfg.origins.forEach(function (origin) {
            const url = new URL(origin);
            if (!['http:', 'https:'].includes(url.protocol) || url.origin !== origin) throw Error('Invalid static origin');
            origins.add(origin);
        });
        Object.entries(cfg.banks).forEach(function (pair) {
            const name = pair[0], bank = pair[1];
            if (!NAME.test(name) || !bank || !Number.isInteger(bank.maxVoices) || bank.maxVoices < 1 || bank.maxVoices > 8 ||
                !bank.sounds || Object.keys(bank.sounds).length < 1 || Object.keys(bank.sounds).length > 32) throw Error('Invalid bank');
            Object.entries(bank.sounds).forEach(function (item) {
                const sound = item[1], url = new URL(sound.url, location.href);
                if (!NAME.test(item[0]) || !number(sound.volume) ||
                    !['http:', 'https:'].includes(url.protocol) || url.username || url.password || !origins.has(url.origin)) throw Error('Invalid sound URL');
                sound.url = url.href;
            });
        });
        return cfg;
    }
    function text(el, value) { if (el && el.textContent !== value) el.textContent = value; }
    function controls(state) {
        const label = state.loading ? 'Loading sound…' : state.error ? 'Retry sound' :
            !state.enabled ? 'Enable sound' : context && context.state !== 'running' ? 'Resume sound' : state.muted ? 'Unmute sound' : 'Mute sound';
        state.markers.forEach(function (marker) {
            const button = marker.querySelector('[data-audio-toggle]');
            if (button) {
                text(button.querySelector('[data-audio-label]') || button, label);
                button.disabled = state.loading;
                button.setAttribute('aria-pressed', String(state.enabled && !state.muted && context && context.state === 'running'));
            }
            const volume = marker.querySelector('[data-audio-volume]');
            if (volume && volume.value !== String(state.volume)) volume.value = String(state.volume);
            text(marker.querySelector('[data-audio-level]'), Math.round(state.volume * 100) + '%');
            const container = marker.querySelector('[data-audio-controls]');
            if (container) container.setAttribute('data-audio-state', state.error ? 'error' :
                state.loading ? 'loading' : state.enabled && !state.muted && context && context.state === 'running' ? 'on' : 'off');
            text(marker.querySelector('[data-audio-status]'), state.error || (state.loading ? 'Loading sound' :
                !state.enabled ? 'Sound off' : state.muted ? 'Sound muted' : context && context.state === 'running' ? 'Sound on' : 'Sound suspended'));
        });
    }
    function stop(state, bank) {
        state.voices.forEach(function (voice) {
            if (bank && voice.bank !== bank) return;
            try { voice.source.stop(); } catch (_) { /* Already ended. */ }
            voice.source.disconnect(); voice.gain.disconnect(); state.voices.delete(voice);
        });
    }
    function destroy(state) {
        state.disposed = true;
        state.abort.abort(); stop(state); state.buffers.clear(); state.seen.clear();
    }
    function sync() {
        const found = new Map();
        document.querySelectorAll('[dj-audio]').forEach(function (marker) {
            const root = marker.closest('[dj-root]');
            if (!root) return;
            try {
                const cfg = parse(marker), scope = cfg.scope;
                if (!found.has(scope)) found.set(scope, { cfg: cfg, root: root, markers: [] });
                const entry = found.get(scope);
                // Same token cannot identify two different roots, including nested ones.
                if (entry.root !== root) { entry.invalid = true; return; }
                entry.markers.push(marker);
            } catch (_) { text(marker.querySelector('[data-audio-status]'), 'Invalid sound configuration'); }
        });
        roots.forEach(function (state, scope) {
            const next = found.get(scope);
            if (!next || next.invalid || next.root !== state.root || JSON.stringify(next.cfg) !== state.signature) {
                destroy(state); roots.delete(scope);
            }
        });
        found.forEach(function (entry, scope) {
            if (entry.invalid) return;
            let state = roots.get(scope);
            if (!state) {
                state = { cfg: entry.cfg, signature: JSON.stringify(entry.cfg), root: entry.root,
                    buffers: new Map(), voices: new Set(), seen: new Map(), volume: 0.5,
                    enabled: false, muted: false, loading: false, disposed: false,
                    error: '', played: 0, abort: new AbortController() };
                roots.set(scope, state);
            }
            state.markers = entry.markers; controls(state);
        });
        if (!roots.size && context) {
            const old = context; context = null;
            old.onstatechange = null;
            old.close().catch(function () {});
        }
    }
    async function bytes(url, signal) {
        const response = await fetch(url, { signal: signal, redirect: 'error', credentials: 'same-origin' });
        if (!response.ok || Number(response.headers.get('Content-Length')) > MAX_BYTES || !response.body) throw Error('Sound download failed');
        const reader = response.body.getReader(), parts = [];
        let size = 0;
        try {
            while (true) {
                const part = await reader.read();
                if (part.done) break;
                size += part.value.byteLength;
                if (size > MAX_BYTES) throw Error('Sound file is too large');
                parts.push(part.value);
            }
        } catch (error) { await reader.cancel(); throw error; }
        const result = new Uint8Array(size);
        let offset = 0;
        parts.forEach(function (part) { result.set(part, offset); offset += part.byteLength; });
        return result.buffer;
    }
    async function enable(state) {
        if (state.loading || state.disposed) return;
        state.error = '';
        try {
            // Create/resume synchronously in the trusted click handler, before network awaits.
            if (!context) {
                const Audio = window.AudioContext || window.webkitAudioContext;
                if (!Audio) throw Error('Audio is unsupported in this browser');
                context = new Audio();
                context.onstatechange = function () { roots.forEach(controls); };
            }
            const current = context, resumed = current.resume();
            state.loading = true; controls(state);
            await resumed;
            if (state.disposed) return;
            if (current.state !== 'running') throw Error('Use Enable sound to allow playback');
            let total = 0, transferred = 0;
            for (const pair of Object.entries(state.cfg.banks)) {
                for (const item of Object.entries(pair[1].sounds)) {
                    const key = pair[0] + ':' + item[0];
                    let buffer = state.buffers.get(key);
                    if (!buffer) {
                        const data = await bytes(item[1].url, state.abort.signal);
                        transferred += data.byteLength;
                        if (transferred > 8 * MAX_BYTES) throw Error('Sound bank is too large');
                        buffer = await current.decodeAudioData(data);
                    }
                    if (state.disposed) return;
                    total += buffer.length * buffer.numberOfChannels * 4;
                    if (buffer.duration > 10 || total > MAX_DECODED) throw Error('Decoded sound bank is too large');
                    state.buffers.set(key, buffer);
                }
            }
            state.enabled = true; state.muted = false;
        } catch (_) {
            state.error = 'Sound unavailable. Try enabling again.';
            state.enabled = false; state.buffers.clear(); stop(state);
        } finally { state.loading = false; if (!state.disposed) controls(state); }
    }
    function stateFor(element) {
        const marker = element.closest('[dj-audio]');
        if (!marker) return null;
        for (const state of roots.values()) if (state.markers.includes(marker)) return state;
        return null;
    }
    function click(event) {
        const button = event.target.closest && event.target.closest('[data-audio-toggle]');
        if (!button || !event.isTrusted) return;
        sync(); const state = stateFor(button);
        if (!state) return;
        if (!state.enabled || !context || context.state !== 'running') { enable(state); return; }
        state.muted = !state.muted;
        if (state.muted) stop(state);
        controls(state);
    }
    function input(event) {
        if (!event.target.matches('[data-audio-volume]')) return;
        const state = stateFor(event.target), value = Number(event.target.value);
        if (!state || !number(value)) return;
        state.volume = value;
        state.voices.forEach(function (voice) { voice.gain.gain.value = value * voice.volume; });
        controls(state);
    }
    function receive(event) {
        if (!event.detail || event.detail.event !== 'djust:audio') return;
        sync(); const payload = event.detail.payload;
        if (!payload || payload.version !== 1) return;
        const state = roots.get(payload.scope);
        if (!state || !owns(state.cfg.banks, payload.bank)) return;
        const bank = state.cfg.banks[payload.bank];
        if (payload.op === 'stop') { stop(state, payload.bank); return; }
        if (payload.op !== 'play' || !Array.isArray(payload.events) || payload.events.length > 32) return;
        payload.events.forEach(function (cue) {
            if (!cue || typeof cue.id !== 'string' || !cue.id.length || cue.id.length > 128 || !owns(bank.sounds, cue.sound)) return;
            const id = payload.bank + ':' + cue.id;
            if (state.seen.has(id)) return;
            state.seen.set(id, true);
            if (state.seen.size > 512) state.seen.delete(state.seen.keys().next().value);
            const buffer = state.buffers.get(payload.bank + ':' + cue.sound);
            // Consume while unavailable: never queue a backlog to play later.
            if (!state.enabled || state.muted || state.loading || document.hidden || !context || context.state !== 'running' || !buffer) return;
            const active = Array.from(state.voices).filter(function (voice) { return voice.bank === payload.bank; }).length;
            if (active >= bank.maxVoices) return;
            const source = context.createBufferSource(), gain = context.createGain();
            const volume = bank.sounds[cue.sound].volume;
            const voice = { source: source, gain: gain, volume: volume, bank: payload.bank };
            try {
                source.buffer = buffer; gain.gain.value = state.volume * volume;
                source.connect(gain); gain.connect(context.destination);
                source.onended = function () { source.disconnect(); gain.disconnect(); state.voices.delete(voice); };
                state.voices.add(voice); source.start();
                state.played++;
                state.markers.forEach(function (marker) {
                    const owner = marker.querySelector('[data-audio-controls]');
                    if (owner) owner.setAttribute('data-audio-played', String(state.played));
                });
            } catch (_) { state.voices.delete(voice); source.disconnect(); gain.disconnect(); }
        });
    }
    document.addEventListener('click', click);
    document.addEventListener('input', input);
    document.addEventListener('visibilitychange', function () { if (document.hidden) roots.forEach(function (s) { stop(s); }); });
    document.addEventListener('djust:ws-reconnected', function () { roots.forEach(function (s) { stop(s); }); });
    window.addEventListener('djust:push_event', receive);
    window.addEventListener('pagehide', function () {
        roots.forEach(destroy); roots.clear();
        if (context) { context.onstatechange = null; context.close().catch(function () {}); context = null; }
    });
    window.addEventListener('pageshow', sync);
    window.djustAudio = { sync: sync };
    sync();
})();
