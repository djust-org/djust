
// dj-sticky-scroll — auto-scroll preservation (v0.6.0)
//
// Keeps a scrollable container pinned to the bottom when new content is
// appended, but backs off when the user scrolls up to read history.
// Resumes auto-scroll when the user scrolls back to the bottom.
//
// Usage:
//   <div dj-sticky-scroll style="overflow-y: auto; height: 400px">
//       {% for msg in messages %}
//       <div>{{ msg.text }}</div>
//       {% endfor %}
//   </div>
//
// Use cases: chat messages, log viewers, terminal output, live feeds.
// Replaces the custom dj-hook authors otherwise wrote with ~30 lines of
// scroll-position math.

const _djStickyObservers = new WeakMap();
// 1px tolerance for sub-pixel scroll math (clientHeight and scrollHeight
// can round differently in some layouts).
const _STICKY_TOLERANCE = 1;

function _isAtBottom(el) {
    return el.scrollTop + el.clientHeight >= el.scrollHeight - _STICKY_TOLERANCE;
}

function _scrollToBottom(el) {
    el.scrollTop = el.scrollHeight;
}

function _installDjStickyScrollFor(el) {
    if (_djStickyObservers.has(el)) return;

    // Seed: assume we start at bottom so the first append scrolls.
    el._djStickyAtBottom = true;
    // #881: Deliberately scroll-to-bottom on install regardless of current
    // position. Matches Phoenix's phx-auto-scroll / Ember's scroll-into-view
    // behavior — sticky-scroll is an "opt into bottom-pinning" attribute,
    // and authors typically want the initial view to show the most recent
    // content (chat, log output).
    _scrollToBottom(el);

    function onScroll() {
        el._djStickyAtBottom = _isAtBottom(el);
    }
    el.addEventListener('scroll', onScroll, { passive: true });

    const observer = new MutationObserver(function () {
        if (el._djStickyAtBottom) {
            _scrollToBottom(el);
        }
    });
    observer.observe(el, { childList: true, subtree: true });

    _djStickyObservers.set(el, { observer: observer, onScroll: onScroll });
}

function _tearDownDjStickyScroll(el) {
    const entry = _djStickyObservers.get(el);
    if (!entry) return;
    entry.observer.disconnect();
    el.removeEventListener('scroll', entry.onScroll);
    _djStickyObservers.delete(el);
    delete el._djStickyAtBottom;
}

function _installDjStickyScrollObserver() {
    document.querySelectorAll('[dj-sticky-scroll]').forEach(_installDjStickyScrollFor);

    const rootObserver = new MutationObserver(function (mutations) {
        mutations.forEach(function (m) {
            // #879: if the dj-sticky-scroll attribute itself is removed from
            // an already-observed element, tear down the observer so we
            // don't leave a stale MutationObserver + scroll listener attached.
            if (m.type === 'attributes' && m.attributeName === 'dj-sticky-scroll') {
                const target = m.target;
                if (target && target.nodeType === 1 && !target.hasAttribute('dj-sticky-scroll')) {
                    if (_djStickyObservers.has(target)) _tearDownDjStickyScroll(target);
                }
                return;
            }
            m.addedNodes.forEach(function (node) {
                if (node.nodeType !== 1) return;
                if (node.hasAttribute && node.hasAttribute('dj-sticky-scroll')) {
                    _installDjStickyScrollFor(node);
                }
                if (node.querySelectorAll) {
                    node.querySelectorAll('[dj-sticky-scroll]').forEach(_installDjStickyScrollFor);
                }
            });
            m.removedNodes.forEach(function (node) {
                if (node.nodeType !== 1) return;
                if (_djStickyObservers.has(node)) _tearDownDjStickyScroll(node);
                if (node.querySelectorAll) {
                    node.querySelectorAll('[dj-sticky-scroll]').forEach(_tearDownDjStickyScroll);
                }
            });
        });
    });
    rootObserver.observe(document.documentElement, {
        subtree: true,
        childList: true,
        attributes: true,
        attributeFilter: ['dj-sticky-scroll'],
    });
}

if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', _installDjStickyScrollObserver);
    } else {
        _installDjStickyScrollObserver();
    }
}

globalThis.djust = globalThis.djust || {};
globalThis.djust.djStickyScroll = {
    _installDjStickyScrollFor,
    _tearDownDjStickyScroll,
    _isAtBottom,
};
