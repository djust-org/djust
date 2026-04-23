// ============================================================================
// File Upload Support
// ============================================================================
// Handles: file selection, chunked binary WebSocket upload, progress tracking,
// image previews, and drag-and-drop zones.
//
// Template directives:
//   dj-upload="name"         — file input bound to upload slot
//   dj-upload-drop="name"    — drop zone for drag-and-drop
//   dj-upload-preview="name" — container for image previews
//   dj-upload-progress="name"— container for progress bars

(function() {
    'use strict';

    // Frame types matching server protocol
    const FRAME_CHUNK    = 0x01;
    const FRAME_COMPLETE = 0x02;
    const FRAME_CANCEL   = 0x03;

    const DEFAULT_CHUNK_SIZE = 64 * 1024; // 64KB

    // Active uploads: ref -> { file, config, chunkIndex, resolve, reject }
    const activeUploads = new Map();

    // Upload configs from server mount response
    let uploadConfigs = {};

    // ========================================================================
    // Resumable upload persistence (ADR-010, #821)
    // ========================================================================
    //
    // When a slot is configured with resumable=True the client keeps a
    // small record in IndexedDB keyed by file "hint" (name + size +
    // lastModified — a poor-man's fingerprint that survives within a
    // single browser session without requiring full-file hashing on
    // upload start).
    //
    // On WS reconnect the client sends `upload_resume` with the saved
    // ref; server replies with bytes_received + chunks_received and
    // the client seeks past them. If IndexedDB is unavailable
    // (private mode, old browser) we fall back to in-memory only —
    // resume works within the same tab load but not across reloads.

    const RESUMABLE_DB_NAME = 'djust_uploads';
    const RESUMABLE_STORE_NAME = 'pending';
    const RESUMABLE_DB_VERSION = 1;
    let _resumableDbPromise = null;

    function _hasIndexedDB() {
        try {
            return typeof indexedDB !== 'undefined' && indexedDB !== null;
        } catch (_) {
            return false;
        }
    }

    /**
     * Open (or lazily create) the IndexedDB backing the resumable
     * upload cache. Returns a Promise<IDBDatabase|null> — null on
     * failure (private mode, SecurityError, etc.) so callers can
     * degrade gracefully to in-memory.
     */
    function openResumableDb() {
        if (!_hasIndexedDB()) return Promise.resolve(null);
        if (_resumableDbPromise) return _resumableDbPromise;
        _resumableDbPromise = new Promise((resolve) => {
            let req;
            try {
                req = indexedDB.open(RESUMABLE_DB_NAME, RESUMABLE_DB_VERSION);
            } catch (err) {
                resolve(null);
                return;
            }
            req.onupgradeneeded = (ev) => {
                const db = ev.target.result;
                if (!db.objectStoreNames.contains(RESUMABLE_STORE_NAME)) {
                    db.createObjectStore(RESUMABLE_STORE_NAME, { keyPath: 'fileHint' });
                }
            };
            req.onsuccess = (ev) => resolve(ev.target.result);
            req.onerror = () => resolve(null);
            req.onblocked = () => resolve(null);
        });
        return _resumableDbPromise;
    }

    /**
     * Build a file "hint" key — not a cryptographic hash, just a
     * best-effort fingerprint. Good enough to spot "is this the same
     * file the user selected last session?" without reading bytes.
     */
    function fileHintKey(file) {
        if (!file) return null;
        const name = file.name || '';
        const size = Number(file.size || 0);
        const lm = Number(file.lastModified || 0);
        return `${name}|${size}|${lm}`;
    }

    async function saveResumableRecord(record) {
        const db = await openResumableDb();
        if (!db) return false;
        return new Promise((resolve) => {
            try {
                const tx = db.transaction(RESUMABLE_STORE_NAME, 'readwrite');
                tx.objectStore(RESUMABLE_STORE_NAME).put(record);
                tx.oncomplete = () => resolve(true);
                tx.onerror = () => resolve(false);
                tx.onabort = () => resolve(false);
            } catch (_) {
                resolve(false);
            }
        });
    }

    async function loadResumableRecord(fileHint) {
        const db = await openResumableDb();
        if (!db) return null;
        return new Promise((resolve) => {
            try {
                const tx = db.transaction(RESUMABLE_STORE_NAME, 'readonly');
                const req = tx.objectStore(RESUMABLE_STORE_NAME).get(fileHint);
                req.onsuccess = () => resolve(req.result || null);
                req.onerror = () => resolve(null);
            } catch (_) {
                resolve(null);
            }
        });
    }

    async function deleteResumableRecord(fileHint) {
        const db = await openResumableDb();
        if (!db) return false;
        return new Promise((resolve) => {
            try {
                const tx = db.transaction(RESUMABLE_STORE_NAME, 'readwrite');
                tx.objectStore(RESUMABLE_STORE_NAME).delete(fileHint);
                tx.oncomplete = () => resolve(true);
                tx.onerror = () => resolve(false);
            } catch (_) {
                resolve(false);
            }
        });
    }

    async function listResumableRecords() {
        const db = await openResumableDb();
        if (!db) return [];
        return new Promise((resolve) => {
            try {
                const tx = db.transaction(RESUMABLE_STORE_NAME, 'readonly');
                const req = tx.objectStore(RESUMABLE_STORE_NAME).getAll();
                req.onsuccess = () => resolve(req.result || []);
                req.onerror = () => resolve([]);
            } catch (_) {
                resolve([]);
            }
        });
    }

    /**
     * Initialize upload configs from server mount data.
     * Called when mount response includes upload configuration.
     */
    function setUploadConfigs(configs) {
        uploadConfigs = configs || {};
        if (globalThis.djustDebug) console.log('[Upload] Configs loaded:', Object.keys(uploadConfigs));
    }

    /**
     * Generate a UUID v4 as bytes (Uint8Array of 16 bytes).
     */
    function uuidBytes() {
        const bytes = new Uint8Array(16);
        crypto.getRandomValues(bytes);
        bytes[6] = (bytes[6] & 0x0f) | 0x40; // version 4
        bytes[8] = (bytes[8] & 0x3f) | 0x80; // variant 1
        return bytes;
    }

    /**
     * Convert UUID bytes to string format.
     */
    function uuidToString(bytes) {
        const hex = Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
        return [
            hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16),
            hex.slice(16, 20), hex.slice(20)
        ].join('-');
    }

    /**
     * Build a binary upload frame.
     * @param {number} frameType - FRAME_CHUNK, FRAME_COMPLETE, or FRAME_CANCEL
     * @param {Uint8Array} refBytes - 16-byte UUID
     * @param {ArrayBuffer|null} payload - Chunk data (for FRAME_CHUNK)
     * @param {number} chunkIndex - Chunk index (for FRAME_CHUNK)
     * @returns {ArrayBuffer}
     */
    function buildFrame(frameType, refBytes, payload, chunkIndex) {
        if (frameType === FRAME_CHUNK && payload) {
            const header = new Uint8Array(21); // 1 + 16 + 4
            header[0] = frameType;
            header.set(refBytes, 1);
            const view = new DataView(header.buffer);
            view.setUint32(17, chunkIndex, false); // big-endian
            const frame = new Uint8Array(21 + payload.byteLength);
            frame.set(header);
            frame.set(new Uint8Array(payload), 21);
            return frame.buffer;
        } else {
            // COMPLETE or CANCEL: just type + ref
            const frame = new Uint8Array(17);
            frame[0] = frameType;
            frame.set(refBytes, 1);
            return frame.buffer;
        }
    }

    /**
     * Parse a UUID string back into its 16-byte representation.
     * Used when resuming an upload — we have the ref string but need
     * the bytes for binary frames.
     */
    function uuidStringToBytes(s) {
        const hex = String(s || '').replace(/-/g, '');
        if (hex.length !== 32) return null;
        const bytes = new Uint8Array(16);
        for (let i = 0; i < 16; i++) {
            bytes[i] = parseInt(hex.substr(i * 2, 2), 16);
        }
        return bytes;
    }

    /**
     * Upload a single file via chunked binary WebSocket frames.
     *
     * Options (all optional):
     *   - resumeRef / resumeRefBytes — previously saved upload_id to
     *     resume. When present, the client skips `upload_register` and
     *     sends `upload_resume` first; the server replies with
     *     bytes_received and the client seeks past the already-
     *     uploaded chunks.
     *   - resumable — when true, the client persists a record to
     *     IndexedDB so it can resume after a tab reload.
     */
    async function uploadFile(ws, uploadName, file, config, opts) {
        opts = opts || {};
        const chunkSize = (config && config.chunk_size) || DEFAULT_CHUNK_SIZE;
        const isResumable = !!(opts.resumable || (config && config.resumable));

        let refBytes;
        let ref;
        let startOffset = 0;
        let startChunkIndex = 0;

        if (opts.resumeRef) {
            ref = opts.resumeRef;
            refBytes = uuidStringToBytes(ref) || uuidBytes();
        } else {
            refBytes = uuidBytes();
            ref = uuidToString(refBytes);
        }

        const fileHint = fileHintKey(file);

        // Resumable path: try to resume first.
        if (opts.resumeRef) {
            const resumePayload = await sendResumeAndWait(ws, ref);
            if (resumePayload && resumePayload.status === 'resumed') {
                startOffset = Number(resumePayload.bytes_received || 0);
                if (startOffset > file.size) startOffset = file.size;
                startChunkIndex = Math.floor(startOffset / chunkSize);
                if (globalThis.djustDebug) {
                    console.log('[Upload] Resuming %s at %d bytes', String(file.name), startOffset);
                }
            } else {
                // not_found or locked — fall back to a fresh register.
                if (fileHint) deleteResumableRecord(fileHint);
                refBytes = uuidBytes();
                ref = uuidToString(refBytes);
            }
        }

        // Fresh (or fallback) registration.
        if (startOffset === 0) {
            ws.sendMessage({
                type: 'upload_register',
                upload_name: uploadName,
                ref: ref,
                client_name: file.name,
                client_type: file.type,
                client_size: file.size,
                resumable: isResumable,
            });
        }

        if (isResumable && fileHint) {
            saveResumableRecord({
                fileHint,
                ref,
                uploadName,
                clientName: file.name,
                clientSize: file.size,
                savedAt: Date.now(),
            });
        }

        return new Promise((resolve, reject) => {
            activeUploads.set(ref, {
                file, config, uploadName, refBytes, resolve, reject,
                chunkIndex: startChunkIndex, sent: startOffset,
                resumable: isResumable, fileHint,
            });

            // Read file and send chunks
            const reader = new FileReader();
            reader.onload = function() {
                const buffer = reader.result;
                let offset = startOffset;
                let chunkIndex = startChunkIndex;

                function sendNextChunk() {
                    if (offset >= buffer.byteLength) {
                        // All chunks sent — send complete frame
                        const completeFrame = buildFrame(FRAME_COMPLETE, refBytes);
                        ws.ws.send(completeFrame);
                        return;
                    }

                    const end = Math.min(offset + chunkSize, buffer.byteLength);
                    const chunk = buffer.slice(offset, end);
                    const frame = buildFrame(FRAME_CHUNK, refBytes, chunk, chunkIndex);
                    ws.ws.send(frame);

                    const upload = activeUploads.get(ref);
                    if (upload) {
                        upload.sent = end;
                        upload.chunkIndex = chunkIndex + 1;
                    }

                    offset = end;
                    chunkIndex++;

                    // Small delay to avoid overwhelming the WebSocket
                    if (ws.ws.bufferedAmount > chunkSize * 4) {
                        setTimeout(sendNextChunk, 10);
                    } else {
                        sendNextChunk();
                    }
                }

                sendNextChunk();
            };

            reader.onerror = function() {
                reject(new Error('Failed to read file: ' + file.name));
                activeUploads.delete(ref);
            };

            reader.readAsArrayBuffer(file);
        });
    }

    /**
     * Pending promises for in-flight `upload_resume` handshakes —
     * resolved when the server replies with `upload_resumed`.
     */
    const pendingResumes = new Map();

    /**
     * Send `upload_resume` and return a Promise that resolves with the
     * server's `upload_resumed` payload. Times out after 5 s so a
     * silent server doesn't stall the upload forever.
     */
    function sendResumeAndWait(ws, ref) {
        return new Promise((resolve) => {
            let done = false;
            const timer = setTimeout(() => {
                if (done) return;
                done = true;
                pendingResumes.delete(ref);
                resolve(null);
            }, 5000);
            pendingResumes.set(ref, (payload) => {
                if (done) return;
                done = true;
                clearTimeout(timer);
                pendingResumes.delete(ref);
                resolve(payload);
            });
            try {
                ws.sendMessage({ type: 'upload_resume', ref });
            } catch (err) {
                if (!done) {
                    done = true;
                    clearTimeout(timer);
                    pendingResumes.delete(ref);
                    resolve(null);
                }
            }
        });
    }

    /**
     * Handle `upload_resumed` messages delivered by the WS frame
     * handler. Exposed as `window.djust.uploads.handleResumed` so
     * 03-websocket.js can call it without creating a circular import.
     *
     * Security: `data` arrives from the WebSocket wire, so `data.ref`
     * is attacker-controlled. We only dispatch when:
     *   1. `ref` is a non-empty string (no lookup-by-object tricks);
     *   2. `pending` is a function we placed in the Map ourselves
     *      (validated via `typeof` — a Map.get() that misses returns
     *      undefined, never a prototype method from the Map class).
     * This prevents a malicious server from steering `pending(data)`
     * into an unintended dispatch.
     */
    function handleUploadResumed(data) {
        if (!data || typeof data.ref !== 'string' || data.ref.length === 0) {
            return;
        }
        const pending = pendingResumes.get(data.ref);
        if (typeof pending === 'function') {
            pending(data);
        }
    }

    /**
     * Upload a single file DIRECTLY to a pre-signed URL (e.g. S3 PUT).
     *
     * Used by the "mode: 'presigned'" flow (#820): the server signs a
     * PUT URL and returns it as an upload spec; the client streams
     * bytes straight to the object store, bypassing the djust
     * WebSocket entirely. Progress is reported via XHR upload events
     * (fetch streams still don't expose upload progress in 2026).
     *
     * @param {Object} spec - { url, key, fields } from server
     * @param {File}   file - the file to PUT
     * @param {Object} hooks - { onProgress(percent), onComplete(result), onError(err) }
     * @returns {Promise<{ key: string, status: number }>}
     */
    function uploadFilePresigned(spec, file, hooks) {
        return new Promise((resolve, reject) => {
            const xhr = new XMLHttpRequest();
            xhr.open('PUT', spec.url, true);

            // Apply server-specified fields as request headers. Content-Type
            // MUST match what was passed at presign time or S3 returns 403.
            const fields = (spec && spec.fields) || {};
            for (const name of Object.keys(fields)) {
                try { xhr.setRequestHeader(name, fields[name]); } catch (_) { /* ignore */ }
            }

            if (xhr.upload) {
                xhr.upload.onprogress = (ev) => {
                    if (!ev.lengthComputable) return;
                    const pct = Math.round((ev.loaded / ev.total) * 100);
                    if (hooks && typeof hooks.onProgress === 'function') {
                        hooks.onProgress(pct);
                    }
                    // Also dispatch the same public event as the WS path
                    // so app code doesn't care which transport we used.
                    window.dispatchEvent(new CustomEvent('djust:upload:progress', {
                        detail: { ref: spec.key, progress: pct, status: 'uploading', mode: 'presigned' }
                    }));
                };
            }

            xhr.onload = () => {
                if (xhr.status >= 200 && xhr.status < 300) {
                    const result = { key: spec.key, status: xhr.status };
                    if (hooks && typeof hooks.onComplete === 'function') {
                        hooks.onComplete(result);
                    }
                    window.dispatchEvent(new CustomEvent('djust:upload:progress', {
                        detail: { ref: spec.key, progress: 100, status: 'complete', mode: 'presigned' }
                    }));
                    resolve(result);
                } else {
                    const err = new Error('Presigned PUT failed: HTTP ' + xhr.status);
                    if (hooks && typeof hooks.onError === 'function') hooks.onError(err);
                    window.dispatchEvent(new CustomEvent('djust:upload:error', {
                        detail: { file: file.name, error: err.message, mode: 'presigned' }
                    }));
                    reject(err);
                }
            };

            xhr.onerror = () => {
                const err = new Error('Presigned PUT network failure');
                if (hooks && typeof hooks.onError === 'function') hooks.onError(err);
                window.dispatchEvent(new CustomEvent('djust:upload:error', {
                    detail: { file: file.name, error: err.message, mode: 'presigned' }
                }));
                reject(err);
            };

            xhr.send(file);
        });
    }

    /**
     * Cancel an active upload.
     */
    function cancelUpload(ws, ref) {
        const upload = activeUploads.get(ref);
        if (upload) {
            const cancelFrame = buildFrame(FRAME_CANCEL, upload.refBytes);
            ws.ws.send(cancelFrame);
            activeUploads.delete(ref);
            if (upload.reject) {
                upload.reject(new Error('Upload cancelled'));
            }
        }
    }

    /**
     * Handle upload progress message from server.
     */
    function handleUploadProgress(data) {
        const { ref, progress, status } = data;
        const upload = activeUploads.get(ref);

        // Update progress bars in DOM
        document.querySelectorAll(`[data-upload-ref="${ref}"] .upload-progress-bar`).forEach(bar => {
            bar.style.width = progress + '%';
            bar.setAttribute('aria-valuenow', progress);
        });

        // Update progress text
        document.querySelectorAll(`[data-upload-ref="${ref}"] .upload-progress-text`).forEach(el => {
            el.textContent = progress + '%';
        });

        // Dispatch custom event for app-level handling
        window.dispatchEvent(new CustomEvent('djust:upload:progress', {
            detail: { ref, progress, status, uploadName: upload ? upload.uploadName : null }
        }));

        if (status === 'complete') {
            if (upload && upload.resolve) {
                upload.resolve({ ref, status: 'complete' });
            }
            if (upload && upload.fileHint) {
                // Clear the IndexedDB resumable record — upload is done.
                deleteResumableRecord(upload.fileHint);
            }
            activeUploads.delete(ref);
        } else if (status === 'error') {
            if (upload && upload.reject) {
                upload.reject(new Error('Upload failed on server'));
            }
            if (upload && upload.fileHint) {
                deleteResumableRecord(upload.fileHint);
            }
            activeUploads.delete(ref);
        }
    }

    /**
     * Generate image preview for a file.
     * @returns {Promise<string>} Data URL
     */
    function generatePreview(file) {
        return new Promise((resolve, reject) => {
            if (!file.type.startsWith('image/')) {
                reject(new Error('Not an image'));
                return;
            }
            const reader = new FileReader();
            reader.onload = () => resolve(reader.result);
            reader.onerror = () => reject(reader.error);
            reader.readAsDataURL(file);
        });
    }

    /**
     * Show previews in a dj-upload-preview container.
     */
    async function showPreviews(uploadName, files) {
        const containers = document.querySelectorAll(`[dj-upload-preview="${uploadName}"]`);
        if (containers.length === 0) return;

        for (const container of containers) {
            container.innerHTML = '';

            for (const file of files) {
                const wrapper = document.createElement('div');
                wrapper.className = 'upload-preview-item';

                if (file.type.startsWith('image/')) {
                    try {
                        const dataUrl = await generatePreview(file);
                        const img = document.createElement('img');
                        img.src = dataUrl;
                        img.alt = file.name;
                        img.className = 'upload-preview-image';
                        wrapper.appendChild(img);
                    } catch (e) {
                        // Fall through to filename display
                    }
                }

                const info = document.createElement('span');
                info.className = 'upload-preview-name';
                info.textContent = file.name;
                wrapper.appendChild(info);

                const size = document.createElement('span');
                size.className = 'upload-preview-size';
                size.textContent = formatSize(file.size);
                wrapper.appendChild(size);

                container.appendChild(wrapper);
            }
        }
    }

    function formatSize(bytes) {
        if (bytes < 1024) return bytes + ' B';
        if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
        return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
    }

    /**
     * Handle file selection from dj-upload input.
     */
    async function handleFileSelect(input, uploadName) {
        const files = Array.from(input.files);
        if (files.length === 0) return;

        const config = uploadConfigs[uploadName];

        // Show previews
        await showPreviews(uploadName, files);

        // Auto-upload if configured (default)
        if (!config || config.auto_upload !== false) {
            if (!isWSConnected()) {
                console.error('[Upload] WebSocket not connected');
                return;
            }

            for (const file of files) {
                // Validate client-side
                if (config) {
                    if (file.size > config.max_file_size) {
                        console.warn('[Upload] File too large: %s (%d > %d)', String(file.name), file.size, config.max_file_size);
                        window.dispatchEvent(new CustomEvent('djust:upload:error', {
                            detail: { file: file.name, error: 'File too large' }
                        }));
                        continue;
                    }
                }

                try {
                    const result = await uploadFile(liveViewWS, uploadName, file, config);
                    if (globalThis.djustDebug) console.log('[Upload] Complete: %s %o', String(file.name), result);
                } catch (err) {
                    console.error('[Upload] Failed: %s %o', String(file.name), err);
                }
            }
        }
    }

    /**
     * Bind upload-related event handlers.
     * Called after DOM updates (mount, patch, etc.)
     */
    function bindUploadHandlers() {
        // File inputs with dj-upload
        document.querySelectorAll('[dj-upload]').forEach(input => {
            if (input._djUploadBound) return;
            input._djUploadBound = true;

            const uploadName = input.getAttribute('dj-upload');

            // Set accept attribute from config
            const config = uploadConfigs[uploadName];
            if (config && config.accept && !input.getAttribute('accept')) {
                input.setAttribute('accept', config.accept);
            }
            if (config && config.max_entries > 1 && !input.hasAttribute('multiple')) {
                input.setAttribute('multiple', '');
            }

            input.addEventListener('change', () => handleFileSelect(input, uploadName));
        });

        // Drop zones with dj-upload-drop
        document.querySelectorAll('[dj-upload-drop]').forEach(zone => {
            if (zone._djDropBound) return;
            zone._djDropBound = true;

            const uploadName = zone.getAttribute('dj-upload-drop');

            zone.addEventListener('dragover', (e) => {
                e.preventDefault();
                e.stopPropagation();
                zone.classList.add('upload-dragover');
            });

            zone.addEventListener('dragleave', (e) => {
                e.preventDefault();
                e.stopPropagation();
                zone.classList.remove('upload-dragover');
            });

            zone.addEventListener('drop', async (e) => {
                e.preventDefault();
                e.stopPropagation();
                zone.classList.remove('upload-dragover');

                const files = Array.from(e.dataTransfer.files);
                if (files.length === 0) return;

                const config = uploadConfigs[uploadName];
                await showPreviews(uploadName, files);

                if (!isWSConnected()) {
                    console.error('[Upload] WebSocket not connected');
                    return;
                }

                for (const file of files) {
                    if (config && file.size > config.max_file_size) {
                        window.dispatchEvent(new CustomEvent('djust:upload:error', {
                            detail: { file: file.name, error: 'File too large' }
                        }));
                        continue;
                    }
                    try {
                        await uploadFile(liveViewWS, uploadName, file, config);
                    } catch (err) {
                        console.error('[Upload] Drop upload failed: %s %o', String(file.name), err);
                    }
                }
            });
        });
    }

    // ========================================================================
    // Exports
    // ========================================================================

    /**
     * Route a FileList obtained from `ClipboardEvent.clipboardData.files`
     * (the `dj-paste` flow) through the same upload pipeline used by
     * file-input and drag-drop uploads. Expects the target element to
     * have a `dj-upload="<slot_name>"` attribute whose slot has been
     * configured via `setUploadConfigs`.
     *
     * Returns a promise that resolves once every pasted file has been
     * uploaded (or rejected client-side). Errors for individual files
     * are dispatched as `djust:upload:error` events; they do not throw.
     */
    async function queueClipboardFiles(element, fileList) {
        if (!fileList || fileList.length === 0) return;
        const uploadName = element && element.getAttribute && element.getAttribute('dj-upload');
        if (!uploadName) return;

        const config = uploadConfigs[uploadName];
        const files = Array.from(fileList);

        await showPreviews(uploadName, files);

        if (!isWSConnected()) {
            console.error('[Upload] WebSocket not connected');
            return;
        }

        for (const file of files) {
            if (config && file.size > config.max_file_size) {
                window.dispatchEvent(new CustomEvent('djust:upload:error', {
                    detail: { file: file.name, error: 'File too large' }
                }));
                continue;
            }
            try {
                await uploadFile(liveViewWS, uploadName, file, config);
            } catch (err) {
                console.error('[Upload] Paste upload failed: %s %o', String(file.name), err);
            }
        }
    }

    window.djust.uploads = {
        setConfigs: setUploadConfigs,
        handleProgress: handleUploadProgress,
        bindHandlers: bindUploadHandlers,
        cancelUpload: (ref) => cancelUpload(liveViewWS, ref),
        activeUploads: activeUploads,
        queueClipboardFiles: queueClipboardFiles,
        // v0.5.7 (#820) — pre-signed S3 PUT: client-direct upload to
        // object storage. Server returns { mode: 'presigned', url, key,
        // fields }; app code calls uploadPresigned(spec, file, hooks).
        uploadPresigned: uploadFilePresigned,
        // v0.5.7 (#821) — resumable uploads across WS disconnects.
        handleResumed: handleUploadResumed,
        openResumableDb,
        saveResumableRecord,
        loadResumableRecord,
        deleteResumableRecord,
        listResumableRecords,
        fileHintKey,
        uuidStringToBytes,
        uploadFile: (file, uploadName, opts) => {
            const cfg = uploadConfigs[uploadName];
            return uploadFile(liveViewWS, uploadName, file, cfg, opts || {});
        },
    };

})();
