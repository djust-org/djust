
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
 *   ]}
 */
function handleStreamMessage(data) {
    const ops = data.ops;
    if (!ops || !Array.isArray(ops)) return;

    for (const op of ops) {
        const target = op.target;
        if (!target) continue;

        const el = document.querySelector(target);
        if (!el) {
            if (globalThis.djustDebug) {
                console.warn('[LiveView:stream] Target not found:', target);
            }
            continue;
        }

        switch (op.op) {
            case 'replace':
                el.innerHTML = op.html || '';
                break;

            case 'append': {
                const frag = _htmlToFragment(op.html);
                el.appendChild(frag);
                // Auto-scroll for chat-like containers
                _autoScroll(el);
                break;
            }

            case 'prepend': {
                const frag = _htmlToFragment(op.html);
                el.insertBefore(frag, el.firstChild);
                break;
            }

            case 'delete':
                el.remove();
                break;

            default:
                console.warn('[LiveView:stream] Unknown op:', op.op);
        }
    }

    // Re-bind events on new DOM content
    if (typeof bindLiveViewEvents === 'function') {
        bindLiveViewEvents();
    }
}

/**
 * Parse an HTML string into a DocumentFragment.
 */
function _htmlToFragment(html) {
    const template = document.createElement('template');
    template.innerHTML = html || '';
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

// Expose for WebSocket handler
window.djust = window.djust || {};
window.djust.handleStreamMessage = handleStreamMessage;
