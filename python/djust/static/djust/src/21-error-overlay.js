// ============================================================================
// Developer Error Overlay
// ============================================================================
// Rich error overlay for development mode, inspired by Next.js/Phoenix.
// Shows Python tracebacks with syntax highlighting when the server sends errors.
// Only active when document.body has data-debug attribute or window.DEBUG_MODE is truthy.

(function () {
    const OVERLAY_ID = 'djust-error-overlay';
    const STYLE_ID = 'djust-error-overlay-styles';

    // Python keywords for basic syntax highlighting
    const PY_KEYWORDS = new Set([
        'False', 'None', 'True', 'and', 'as', 'assert', 'async', 'await',
        'break', 'class', 'continue', 'def', 'del', 'elif', 'else', 'except',
        'finally', 'for', 'from', 'global', 'if', 'import', 'in', 'is',
        'lambda', 'nonlocal', 'not', 'or', 'pass', 'raise', 'return',
        'try', 'while', 'with', 'yield',
    ]);

    function isDebugMode() {
        return !!(
            window.DEBUG_MODE ||
            (document.body && document.body.hasAttribute('data-debug'))
        );
    }

    function injectStyles() {
        if (document.getElementById(STYLE_ID)) return;
        const style = document.createElement('style');
        style.id = STYLE_ID;
        style.textContent = `
#${OVERLAY_ID} {
    position: fixed; inset: 0; z-index: 999999;
    display: flex; align-items: center; justify-content: center;
    background: rgba(0, 0, 0, 0.75);
    backdrop-filter: blur(4px);
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    color: #e2e8f0;
    animation: djust-overlay-fade-in 0.15s ease-out;
}
@keyframes djust-overlay-fade-in {
    from { opacity: 0; }
    to { opacity: 1; }
}
#${OVERLAY_ID} * { box-sizing: border-box; }
.djust-eo-panel {
    background: #1a1a2e; border-radius: 12px;
    width: 90vw; max-width: 960px; max-height: 90vh;
    display: flex; flex-direction: column;
    box-shadow: 0 25px 60px rgba(0,0,0,0.5);
    border: 1px solid #2d2d4a;
    overflow: hidden;
}
.djust-eo-header {
    padding: 20px 24px; display: flex; align-items: flex-start; gap: 14px;
    border-bottom: 1px solid #2d2d4a; flex-shrink: 0;
}
.djust-eo-icon { font-size: 28px; line-height: 1; flex-shrink: 0; }
.djust-eo-title-area { flex: 1; min-width: 0; }
.djust-eo-exception {
    font-size: 18px; font-weight: 700; color: #f87171;
    margin: 0 0 6px; word-break: break-word;
}
.djust-eo-message {
    font-size: 14px; color: #cbd5e1; line-height: 1.5;
    word-break: break-word;
}
.djust-eo-event-badge {
    display: inline-block; background: #312e81; color: #a5b4fc;
    padding: 2px 8px; border-radius: 4px; font-size: 12px;
    font-family: monospace; margin-top: 6px;
}
.djust-eo-actions { display: flex; gap: 8px; flex-shrink: 0; }
.djust-eo-btn {
    background: #2d2d4a; border: 1px solid #3d3d5c; color: #94a3b8;
    padding: 6px 12px; border-radius: 6px; cursor: pointer;
    font-size: 13px; transition: all 0.15s;
    display: flex; align-items: center; gap: 5px;
}
.djust-eo-btn:hover { background: #3d3d5c; color: #e2e8f0; }
.djust-eo-close-btn {
    background: none; border: none; color: #64748b;
    font-size: 22px; cursor: pointer; padding: 4px 8px; line-height: 1;
}
.djust-eo-close-btn:hover { color: #e2e8f0; }
.djust-eo-body {
    overflow-y: auto; padding: 0; flex: 1;
}
.djust-eo-traceback {
    padding: 16px 0; font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', Menlo, Consolas, monospace;
    font-size: 13px; line-height: 1.7;
}
.djust-eo-frame {
    border-bottom: 1px solid #1e1e38;
}
.djust-eo-frame:last-child { border-bottom: none; }
.djust-eo-frame-header {
    padding: 8px 24px; color: #64748b; font-size: 12px;
    cursor: default; user-select: text;
}
.djust-eo-frame-file { color: #a5b4fc; }
.djust-eo-frame-lineno { color: #facc15; }
.djust-eo-frame-func { color: #94a3b8; font-style: italic; }
.djust-eo-frame-code {
    padding: 0; margin: 0; background: #12121f;
}
.djust-eo-code-line {
    padding: 1px 24px; display: block; white-space: pre-wrap;
    word-break: break-all;
}
.djust-eo-code-line.error-line {
    background: rgba(239, 68, 68, 0.15);
    border-left: 3px solid #ef4444;
    padding-left: 21px;
}
.djust-eo-code-line .lineno {
    display: inline-block; width: 48px; color: #4a4a6a;
    text-align: right; margin-right: 16px; user-select: none;
}
/* Syntax highlighting */
.djust-eo-kw { color: #c084fc; }
.djust-eo-str { color: #86efac; }
.djust-eo-num { color: #fcd34d; }
.djust-eo-comment { color: #4a4a6a; font-style: italic; }
.djust-eo-builtin { color: #67e8f9; }
.djust-eo-raw-tb {
    padding: 16px 24px; white-space: pre-wrap; word-break: break-all;
    font-family: 'SF Mono', Menlo, Consolas, monospace; font-size: 13px;
    line-height: 1.7; color: #cbd5e1; background: #12121f;
}
.djust-eo-footer {
    padding: 12px 24px; border-top: 1px solid #2d2d4a;
    font-size: 11px; color: #475569; flex-shrink: 0;
    display: flex; justify-content: space-between;
}
`;
        document.head.appendChild(style);
    }

    /**
     * Parse a Python traceback string into structured frames.
     * Returns { frames: [{file, lineno, func, code}], exceptionLine, errorLine }
     */
    function parseTraceback(tb) {
        const lines = tb.split('\n');
        const frames = [];
        let exceptionLine = '';

        for (let i = 0; i < lines.length; i++) {
            const line = lines[i];
            // Match: File "path", line N, in func
            const fileMatch = line.match(/^\s*File "(.+?)", line (\d+), in (.+)/);
            if (fileMatch) {
                const frame = {
                    file: fileMatch[1],
                    lineno: parseInt(fileMatch[2], 10),
                    func: fileMatch[3],
                    code: [],
                };
                // Collect code lines following the File line
                while (i + 1 < lines.length && lines[i + 1].match(/^\s{4,}/) && !lines[i + 1].match(/^\s*File "/)) {
                    i++;
                    frame.code.push(lines[i].replace(/^\s{4}/, ''));
                }
                frames.push(frame);
            }
        }

        // Last non-empty line is usually the exception
        for (let i = lines.length - 1; i >= 0; i--) {
            if (lines[i].trim()) {
                exceptionLine = lines[i].trim();
                break;
            }
        }

        return { frames, exceptionLine };
    }

    /**
     * Basic Python syntax highlighting for a code string.
     */
    function highlightPython(code) {
        // Escape HTML
        let s = code.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

        // Comments
        s = s.replace(/(#.*)$/gm, '<span class="djust-eo-comment">$1</span>');

        // Strings (simple — single/double quotes, not multiline)
        s = s.replace(/((?:f|r|b|u)?(?:"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'))/g, '<span class="djust-eo-str">$1</span>');

        // Numbers
        s = s.replace(/\b(\d+\.?\d*)\b/g, '<span class="djust-eo-num">$1</span>');

        // Keywords
        s = s.replace(/\b([A-Za-z_]+)\b/g, (m, word) => {
            if (PY_KEYWORDS.has(word)) return `<span class="djust-eo-kw">${word}</span>`;
            if (['self', 'cls', 'print', 'len', 'range', 'type', 'isinstance', 'str', 'int', 'float', 'list', 'dict', 'set', 'tuple', 'super', 'Exception', 'ValueError', 'TypeError', 'KeyError', 'AttributeError', 'RuntimeError', 'IndexError'].includes(word)) {
                return `<span class="djust-eo-builtin">${word}</span>`;
            }
            return word;
        });

        return s;
    }

    /**
     * Parse the error string into exception class and message.
     */
    function parseError(errorStr) {
        // Try to extract "ExceptionClass: message" from the error string
        // The error may be prefixed with "Error in ViewClass.event(): "
        const prefixMatch = errorStr.match(/^Error in .+?:\s*(.+)/);
        const core = prefixMatch ? prefixMatch[1] : errorStr;
        const colonIdx = core.indexOf(': ');
        if (colonIdx > 0 && /^[A-Z]/.test(core)) {
            return { cls: core.slice(0, colonIdx), message: core.slice(colonIdx + 2) };
        }
        return { cls: 'Error', message: errorStr };
    }

    function buildFrameHtml(frame) {
        const codeHtml = frame.code.map((line, idx) => {
            const isErrorLine = idx === frame.code.length - 1; // last code line in frame is usually the culprit
            const highlighted = highlightPython(line);
            const cls = isErrorLine && frame.code.length === 1 ? 'djust-eo-code-line error-line' : 'djust-eo-code-line';
            const ln = frame.lineno + idx;
            return `<span class="${cls}"><span class="lineno">${ln}</span>${highlighted}</span>`;
        }).join('');

        return `
<div class="djust-eo-frame">
    <div class="djust-eo-frame-header">
        <span class="djust-eo-frame-file">${escHtml(frame.file)}</span>:<span class="djust-eo-frame-lineno">${frame.lineno}</span>
        in <span class="djust-eo-frame-func">${escHtml(frame.func)}</span>
    </div>
    ${codeHtml ? `<div class="djust-eo-frame-code">${codeHtml}</div>` : ''}
</div>`;
    }

    function escHtml(s) {
        return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    function showOverlay(detail) {
        if (!isDebugMode()) return;

        // Remove existing overlay
        const existing = document.getElementById(OVERLAY_ID);
        if (existing) existing.remove();

        injectStyles();

        const { error, traceback: tb, event: eventName } = detail;
        const { cls, message } = parseError(error || 'Unknown error');

        let tracebackHtml = '';
        let rawTb = tb || '';

        if (tb) {
            const parsed = parseTraceback(tb);
            if (parsed.frames.length > 0) {
                // Show frames in reverse (most relevant last = at top)
                const reversedFrames = [...parsed.frames].reverse();
                tracebackHtml = `<div class="djust-eo-traceback">${reversedFrames.map(buildFrameHtml).join('')}</div>`;
            } else {
                // Couldn't parse — show raw
                tracebackHtml = `<div class="djust-eo-raw-tb">${escHtml(tb)}</div>`;
            }
        }

        const eventBadge = eventName ? `<div class="djust-eo-event-badge">event: ${escHtml(eventName)}</div>` : '';

        const overlay = document.createElement('div');
        overlay.id = OVERLAY_ID;
        overlay.innerHTML = `
<div class="djust-eo-panel">
    <div class="djust-eo-header">
        <span class="djust-eo-icon">🔴</span>
        <div class="djust-eo-title-area">
            <div class="djust-eo-exception">${escHtml(cls)}</div>
            <div class="djust-eo-message">${escHtml(message)}</div>
            ${eventBadge}
        </div>
        <div class="djust-eo-actions">
            <button class="djust-eo-btn" data-action="copy" title="Copy traceback">📋 Copy</button>
            <button class="djust-eo-close-btn" data-action="close" title="Dismiss">&times;</button>
        </div>
    </div>
    <div class="djust-eo-body">
        ${tracebackHtml}
    </div>
    <div class="djust-eo-footer">
        <span>djust dev error overlay</span>
        <span>Press <kbd>Esc</kbd> to dismiss</span>
    </div>
</div>`;

        // Event handlers
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) dismissOverlay();
            const action = e.target.closest('[data-action]');
            if (!action) return;
            if (action.dataset.action === 'close') dismissOverlay();
            if (action.dataset.action === 'copy') {
                navigator.clipboard.writeText(rawTb || error).then(() => {
                    action.textContent = '✓ Copied';
                    setTimeout(() => { action.innerHTML = '📋 Copy'; }, 1500);
                });
            }
        });

        document.body.appendChild(overlay);

        // Esc to dismiss
        const onKey = (e) => {
            if (e.key === 'Escape') { dismissOverlay(); document.removeEventListener('keydown', onKey); }
        };
        document.addEventListener('keydown', onKey);
    }

    function dismissOverlay() {
        const el = document.getElementById(OVERLAY_ID);
        if (el) el.remove();
    }

    // Listen for djust:error events dispatched by the WebSocket handler
    window.addEventListener('djust:error', (e) => {
        showOverlay(e.detail);
    });

    // Expose for programmatic use
    window.djust = window.djust || {};
    window.djust.showErrorOverlay = showOverlay;
    window.djust.dismissErrorOverlay = dismissOverlay;
})();
