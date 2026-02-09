// ============================================================================
// CursorOverlay — Built-in Hook for Collaborative Cursor Display
// ============================================================================
//
// Renders colored carets showing where remote users' cursors are positioned
// in a textarea. Uses the mirror-div technique to map character index to
// pixel coordinates.
//
// Usage:
//   <div dj-hook="CursorOverlay">
//     <textarea dj-input="update_content">{{ content }}</textarea>
//   </div>
//
// The hook auto-discovers the first <textarea> child and dynamically creates
// an overlay div (for rendering carets) and a hidden mirror div (for
// measuring cursor positions). No extra markup is needed in the template.
//
// Server contract:
//   - Hook sends: pushEvent("update_cursor", {position: <int>})
//   - Hook listens: handleEvent("cursor_positions", {cursors: {uid: {position, color, name, emoji}, ...}})
//
// ============================================================================

window.djust.hooks = window.djust.hooks || {};

window.djust.hooks.CursorOverlay = {
    mounted: function() {
        var self = this;

        // Discover textarea
        this.textarea = this.el.querySelector('textarea');
        if (!this.textarea) {
            console.warn('[CursorOverlay] No <textarea> found inside hook element');
            return;
        }

        // Create overlay div (visible, pointer-events:none)
        this.overlay = document.createElement('div');
        this.overlay.setAttribute('dj-update', 'ignore');
        this.overlay.style.cssText = 'position:absolute; inset:0; pointer-events:none; overflow:hidden;';
        // Copy textarea padding so carets align with text
        var cs = window.getComputedStyle(this.textarea);
        this.overlay.style.padding = cs.paddingTop + ' ' + cs.paddingRight + ' ' + cs.paddingBottom + ' ' + cs.paddingLeft;
        this.overlay.style.fontFamily = cs.fontFamily;
        this.overlay.style.fontSize = cs.fontSize;
        this.overlay.style.lineHeight = cs.lineHeight;
        this.el.appendChild(this.overlay);

        // Create hidden mirror div (for measurement)
        this.mirror = document.createElement('div');
        this.mirror.setAttribute('aria-hidden', 'true');
        this.mirror.style.cssText = 'position:absolute; visibility:hidden; white-space:pre-wrap; word-wrap:break-word; overflow-wrap:break-word;';
        this.el.appendChild(this.mirror);

        this._carets = {};       // {userId: caretElement}
        this._lastCursors = {};  // cached cursor data for repositioning on updated()
        this._debounceTimer = null;

        // Copy computed styles from textarea to mirror for accurate measurement
        this._syncMirrorStyles();

        // Debounced cursor position reporter
        this._sendCursorPosition = function() {
            clearTimeout(self._debounceTimer);
            self._debounceTimer = setTimeout(function() {
                var pos = self.textarea.selectionStart;
                self.pushEvent('update_cursor', { position: pos });
            }, 100);
        };

        // Bind cursor movement listeners
        this.textarea.addEventListener('keyup', this._sendCursorPosition);
        this.textarea.addEventListener('click', this._sendCursorPosition);
        this.textarea.addEventListener('select', this._sendCursorPosition);

        // Scroll sync: reposition carets when textarea scrolls
        this._onScroll = function() {
            self._repositionAll();
        };
        this.textarea.addEventListener('scroll', this._onScroll);

        // Listen for server push events with cursor positions
        this.handleEvent('cursor_positions', function(payload) {
            self._lastCursors = payload.cursors || {};
            self._renderCursors(self._lastCursors);
        });
    },

    updated: function() {
        // Text content may have changed — reposition all active carets
        if (this.textarea) {
            this._repositionAll();
        }
    },

    destroyed: function() {
        clearTimeout(this._debounceTimer);
        if (this.textarea) {
            this.textarea.removeEventListener('keyup', this._sendCursorPosition);
            this.textarea.removeEventListener('click', this._sendCursorPosition);
            this.textarea.removeEventListener('select', this._sendCursorPosition);
            this.textarea.removeEventListener('scroll', this._onScroll);
        }
        if (this.overlay && this.overlay.parentNode) {
            this.overlay.parentNode.removeChild(this.overlay);
        }
        if (this.mirror && this.mirror.parentNode) {
            this.mirror.parentNode.removeChild(this.mirror);
        }
    },

    _syncMirrorStyles: function() {
        var cs = window.getComputedStyle(this.textarea);
        var props = [
            'fontFamily', 'fontSize', 'fontWeight', 'lineHeight', 'letterSpacing',
            'wordSpacing', 'textIndent', 'wordWrap', 'overflowWrap', 'whiteSpace',
            'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft',
            'borderTopWidth', 'borderRightWidth', 'borderBottomWidth', 'borderLeftWidth'
        ];
        for (var i = 0; i < props.length; i++) {
            this.mirror.style[props[i]] = cs[props[i]];
        }
        // Use content-box width matching the textarea's actual text area
        // (clientWidth excludes scrollbar and border; subtract padding for content)
        var padL = parseFloat(cs.paddingLeft) || 0;
        var padR = parseFloat(cs.paddingRight) || 0;
        this.mirror.style.boxSizing = 'content-box';
        this.mirror.style.width = (this.textarea.clientWidth - padL - padR) + 'px';
    },

    _measureCursorPosition: function(charIndex) {
        // Mirror-div technique: fill mirror with text up to cursor, measure marker offset
        var text = this.textarea.value.substring(0, charIndex);
        this.mirror.textContent = '';
        var textNode = document.createTextNode(text);
        var marker = document.createElement('span');
        marker.textContent = '\u200b';  // zero-width space
        this.mirror.appendChild(textNode);
        this.mirror.appendChild(marker);

        var mirrorRect = this.mirror.getBoundingClientRect();
        var markerRect = marker.getBoundingClientRect();

        return {
            left: markerRect.left - mirrorRect.left,
            top: markerRect.top - mirrorRect.top - this.textarea.scrollTop
        };
    },

    _renderCursors: function(cursors) {
        var activeIds = {};

        for (var uid in cursors) {
            activeIds[uid] = true;
            var data = cursors[uid];
            var pos = this._measureCursorPosition(data.position);

            var caret = this._carets[uid];
            if (!caret) {
                // Create new caret element
                caret = document.createElement('div');
                caret.className = 'remote-cursor';
                caret.style.cssText = 'position:absolute; transition:left 0.15s ease, top 0.15s ease; pointer-events:none;';

                var line = document.createElement('div');
                line.style.cssText = 'width:2px; height:1.2em; border-radius:1px;';
                line.style.backgroundColor = data.color;
                caret.appendChild(line);

                var label = document.createElement('div');
                label.style.cssText = 'position:absolute; bottom:100%; left:0; color:#fff; font-size:10px; padding:1px 4px; border-radius:3px; white-space:nowrap; font-family:system-ui,sans-serif;';
                label.style.backgroundColor = data.color;
                label.textContent = (data.emoji || '') + ' ' + (data.name || '');
                caret.appendChild(label);

                this.overlay.appendChild(caret);
                this._carets[uid] = caret;
            }

            caret.style.left = pos.left + 'px';
            caret.style.top = pos.top + 'px';
        }

        // Remove carets for users who are no longer present
        for (var id in this._carets) {
            if (!activeIds[id]) {
                this._carets[id].parentNode.removeChild(this._carets[id]);
                delete this._carets[id];
            }
        }
    },

    _repositionAll: function() {
        // Re-sync mirror width in case textarea resized or scrollbar appeared
        var cs = window.getComputedStyle(this.textarea);
        var padL = parseFloat(cs.paddingLeft) || 0;
        var padR = parseFloat(cs.paddingRight) || 0;
        this.mirror.style.width = (this.textarea.clientWidth - padL - padR) + 'px';

        // Reposition using cached cursor data
        if (Object.keys(this._lastCursors).length > 0) {
            this._renderCursors(this._lastCursors);
        }
    }
};
