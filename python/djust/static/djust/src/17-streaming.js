
// ============================================================================
// Streaming — Real-time partial DOM updates (LLM chat, live feeds, etc.)
// ============================================================================

/**
 * Handle a "stream" WebSocket message by applying DOM operations directly.
 *
 * Message format:
 *   { type: "stream", stream: "messages", ops: [
 *       { op: "replace", target: "#message-list", html: "..." },
 *       { op: "append",  target: "#message-list", html: "<div>...</div>" },
 *       { op: "prepend", target: "#message-list", html: "<div>...</div>" },
 *       { op: "delete",  target: "#msg-42" },
 *       { op: "text",    target: "#output",       text: "partial token" },
 *       { op: "error",   target: "#output",       error: "Something failed" },
 *   ]}
 *
 * DOM attributes:
 *   dj-stream="stream_name"           — marks an element as a stream target
 *   dj-stream-mode="append|replace|prepend" — default insertion mode for text ops
 */

// Track active streams for error recovery and state
const _activeStreams = new Map();

function handleStreamMessage(data) {
    const ops = data.ops;
    if (!ops || !Array.isArray(ops)) return;

    const streamName = data.stream || '__default__';

    // Track stream as active
    if (!_activeStreams.has(streamName)) {
        _activeStreams.set(streamName, { started: Date.now(), errorCount: 0 });
    }

    for (const op of ops) {
        try {
            _applyStreamOp(op, streamName);
        } catch (err) {
            console.error('[LiveView:stream] Error applying op:', op, err);
            const info = _activeStreams.get(streamName);
            if (info) info.errorCount++;
        }
    }

    // Re-bind events on new DOM content
    if (typeof bindLiveViewEvents === 'function') {
        bindLiveViewEvents();
    }
}

/**
 * Apply a single stream operation to the DOM.
 */
function _applyStreamOp(op, streamName) {
    const target = op.target;
    if (!target) return;

    // Resolve target element(s)
    const el = document.querySelector(target);
    if (!el) {
        if (globalThis.djustDebug) {
            console.warn('[LiveView:stream] Target not found:', target);
        }
        return;
    }

    switch (op.op) {
        case 'replace':
            el.innerHTML = op.html || ''; // lgtm[js/xss-through-dom] -- html is server-rendered by the trusted Django/Rust template engine
            _removeStreamError(el);
            _dispatchStreamEvent(el, 'stream:update', { op: 'replace', stream: streamName });
            break;

        case 'append': {
            const frag = _htmlToFragment(op.html);
            el.appendChild(frag);
            _autoScroll(el);
            _removeStreamError(el);
            _dispatchStreamEvent(el, 'stream:update', { op: 'append', stream: streamName });
            break;
        }

        case 'prepend': {
            const frag = _htmlToFragment(op.html);
            el.insertBefore(frag, el.firstChild);
            _removeStreamError(el);
            _dispatchStreamEvent(el, 'stream:update', { op: 'prepend', stream: streamName });
            break;
        }

        case 'delete':
            _dispatchStreamEvent(el, 'stream:remove', { stream: streamName });
            el.remove();
            break;

        case 'text': {
            // Streaming text content — respects dj-stream-mode attribute
            const text = op.text || '';
            const mode = op.mode || el.getAttribute('dj-stream-mode') || 'append';
            _applyTextOp(el, text, mode);
            _autoScroll(el);
            _removeStreamError(el);
            _dispatchStreamEvent(el, 'stream:text', { text, mode, stream: streamName });
            break;
        }

        case 'error': {
            // Show error state while preserving partial content
            const errorMsg = op.error || 'Stream error';
            _showStreamError(el, errorMsg);
            _dispatchStreamEvent(el, 'stream:error', { error: errorMsg, stream: streamName });
            break;
        }

        case 'done': {
            // Stream completed
            _activeStreams.delete(streamName);
            el.removeAttribute('data-stream-active');
            _dispatchStreamEvent(el, 'stream:done', { stream: streamName });
            break;
        }

        case 'start': {
            // Stream started — set active state
            el.setAttribute('data-stream-active', 'true');
            _removeStreamError(el);
            _dispatchStreamEvent(el, 'stream:start', { stream: streamName });
            break;
        }

        default:
            console.warn('[LiveView:stream] Unknown op:', op.op);
    }
}

/**
 * Apply text content with append/replace/prepend modes.
 */
function _applyTextOp(el, text, mode) {
    switch (mode) {
        case 'replace':
            el.textContent = text;
            break;
        case 'prepend':
            el.textContent = text + el.textContent;
            break;
        case 'append':
        default:
            el.textContent += text;
            break;
    }
    el.setAttribute('data-stream-active', 'true');
}

/**
 * Show an error indicator on a stream target, preserving existing content.
 */
function _showStreamError(el, message) {
    el.setAttribute('data-stream-error', 'true');
    el.removeAttribute('data-stream-active');

    // Add error element if not already present
    let errorEl = el.querySelector('.dj-stream-error');
    if (!errorEl) {
        errorEl = document.createElement('div');
        errorEl.className = 'dj-stream-error';
        errorEl.setAttribute('role', 'alert');
        el.appendChild(errorEl);
    }
    errorEl.textContent = message;
}

/**
 * Remove error state from a stream target.
 */
function _removeStreamError(el) {
    el.removeAttribute('data-stream-error');
    const errorEl = el.querySelector('.dj-stream-error');
    if (errorEl) errorEl.remove();
}

/**
 * Dispatch a custom event on a stream target element.
 */
function _dispatchStreamEvent(el, eventName, detail) {
    el.dispatchEvent(new CustomEvent(eventName, {
        bubbles: true,
        detail: detail,
    }));
}

/**
 * Parse an HTML string into a DocumentFragment.
 */
function _htmlToFragment(html) {
    const template = document.createElement('template');
    template.innerHTML = html || ''; // lgtm[js/xss-through-dom] -- html is server-rendered by the trusted Django/Rust template engine
    return template.content;
}

/**
 * Auto-scroll a container to the bottom if the user is near the bottom.
 * "Near" = within 100px of the bottom before the update.
 */
function _autoScroll(el) {
    const threshold = 100;
    const isNearBottom = (el.scrollHeight - el.scrollTop - el.clientHeight) < threshold;
    if (isNearBottom) {
        requestAnimationFrame(() => {
            el.scrollTop = el.scrollHeight;
        });
    }
}

/**
 * Get info about active streams (for debugging).
 */
function getActiveStreams() {
    const result = {};
    for (const [name, info] of _activeStreams) {
        result[name] = { ...info };
    }
    return result;
}

// Expose for WebSocket handler
window.djust = window.djust || {};
window.djust.handleStreamMessage = handleStreamMessage;
window.djust.getActiveStreams = getActiveStreams;
