
// DraftManager for localStorage-based draft saving
class DraftManager {
    constructor() {
        this.saveTimers = new Map();
        // Latest data handed to saveDraft() whose debounced write has not run
        // yet: a field restored in that window must see it, not the older
        // stored copy (#3351).
        this.pendingData = new Map();
        this.saveDelay = 500;
    }

    saveDraft(draftKey, data) {
        if (this.saveTimers.has(draftKey)) {
            clearTimeout(this.saveTimers.get(draftKey));
        }

        this.pendingData.set(draftKey, data);
        const timerId = setTimeout(() => {
            this.pendingData.delete(draftKey);
            try {
                const draftData = {
                    data,
                    timestamp: Date.now()
                };
                localStorage.setItem(`djust_draft_${draftKey}`, JSON.stringify(draftData));

                if (globalThis.djustDebug) {
                    djLog(`[DraftMode] Saved draft: ${draftKey}`, data);
                }
            } catch (error) {
                console.error(`[DraftMode] Failed to save draft ${draftKey}:`, error);
            }
            this.saveTimers.delete(draftKey);
        }, this.saveDelay);

        this.saveTimers.set(draftKey, timerId);
    }

    loadDraft(draftKey) {
        try {
            const stored = localStorage.getItem(`djust_draft_${draftKey}`);
            if (!stored) {
                return null;
            }

            const draftData = JSON.parse(stored);

            if (globalThis.djustDebug) {
                const age = Math.round((Date.now() - draftData.timestamp) / 1000);
                djLog(`[DraftMode] Loaded draft: ${draftKey} (${age}s old)`, draftData.data);
            }

            return draftData.data;
        } catch (error) {
            console.error(`[DraftMode] Failed to load draft ${draftKey}:`, error);
            return null;
        }
    }

    clearDraft(draftKey) {
        if (this.saveTimers.has(draftKey)) {
            clearTimeout(this.saveTimers.get(draftKey));
            this.saveTimers.delete(draftKey);
        }
        this.pendingData.delete(draftKey);

        try {
            localStorage.removeItem(`djust_draft_${draftKey}`);

            if (globalThis.djustDebug) {
                djLog(`[DraftMode] Cleared draft: ${draftKey}`);
            }
        } catch (error) {
            console.error(`[DraftMode] Failed to clear draft ${draftKey}:`, error);
        }
    }

    getAllDraftKeys() {
        const keys = [];
        try {
            for (let i = 0; i < localStorage.length; i++) {
                const key = localStorage.key(i);
                if (key && key.startsWith('djust_draft_')) {
                    keys.push(key.replace('djust_draft_', ''));
                }
            }
        } catch (error) {
            console.error('[DraftMode] Failed to get draft keys:', error);
        }
        return keys;
    }

    clearAllDrafts() {
        const keys = this.getAllDraftKeys();
        keys.forEach(key => this.clearDraft(key));

        if (globalThis.djustDebug) {
            djLog(`[DraftMode] Cleared all ${keys.length} drafts`);
        }
    }
}

const globalDraftManager = new DraftManager();

// Draft fields are tracked per element with JS-side state, never DOM
// attributes: a morph rewrites an element's attributes to the server's markup,
// so a marker kept there is lost and the next pass would treat the field as new.
//
// _draftRestored: field -> the name it was restored under. A field the page
//   renders after init (a patch, a lazily hydrated view, a live_redirect) is
//   restored once when it first shows up, and never again for as long as the
//   element lives. A morph can keep an element and change its name (a wizard
//   step reusing the input), which makes it a new field: a new name restores
//   again.
// _draftEdited: field -> the name the user typed it under. A restore never
//   overwrites an edited field.
const _draftRestored = new WeakMap();
const _draftEdited = new WeakMap();

// Saving is ONE delegated listener per event type on the document, installed
// once. Binding per field closed over the fields present at init, so a field
// inserted later saved nothing, a field replaced by a new element stopped
// saving, and a draft root inside a lazily hydrated view was never wired
// (#3351). `input` and `change` both bubble.
let _draftListenersInstalled = false;

/**
 * Where `field` saves to: the draft root it sits in (its fields are collected
 * from that root alone), else, for a marker placed away from its fields, the
 * page's first draft root with every field in the document.
 */
function _draftTargetFor(field) {
    const own = field.closest('[data-draft-enabled]');
    if (own) return { root: own, scope: own };
    const first = document.querySelector('[data-draft-enabled]');
    return first ? { root: first, scope: document } : null;
}

function _collectDraftData(scope) {
    const draftData = {};
    scope.querySelectorAll('[data-draft="true"]').forEach(f => {
        // Prevent prototype pollution attacks. A file input has no value a
        // draft could put back (setting one throws).
        if (f.name && !UNSAFE_KEYS.includes(f.name) && f.type !== 'file') {
            if (f.type === 'checkbox') {
                draftData[f.name] = f.checked;
            } else {
                draftData[f.name] = f.value;
            }
        }
    });
    return draftData;
}

/**
 * The draft to store: what is on the page now over what is already stored for
 * the key (the unwritten save if one is pending, else storage). A name the
 * page does not hold at the moment, such as a wizard's earlier step, keeps its
 * saved value until `clear_draft()` removes the draft.
 */
function _mergeDraftData(draftKey, current) {
    const previous = globalDraftManager.pendingData.get(draftKey) || globalDraftManager.loadDraft(draftKey);
    const merged = {};
    if (previous && typeof previous === 'object') {
        Object.keys(previous).forEach(name => {
            // eslint-disable-next-line security/detect-object-injection
            if (!UNSAFE_KEYS.includes(name)) merged[name] = previous[name];
        });
    }
    Object.keys(current).forEach(name => {
        // eslint-disable-next-line security/detect-object-injection
        merged[name] = current[name];
    });
    return merged;
}

function _onDraftFieldChange(event) {
    const target = event.target;
    if (!target || typeof target.closest !== 'function') return;
    const field = target.closest('[data-draft="true"]');
    if (!field) return;
    const place = _draftTargetFor(field);
    if (!place) return;
    const draftKey = place.root.getAttribute('data-draft-key');
    if (!draftKey) return;
    _draftEdited.set(field, field.name);
    globalDraftManager.saveDraft(draftKey, _mergeDraftData(draftKey, _collectDraftData(place.scope)));
}

function _installDraftListeners() {
    if (_draftListenersInstalled) return;
    _draftListenersInstalled = true;
    document.addEventListener('input', _onDraftFieldChange);
    document.addEventListener('change', _onDraftFieldChange);
}

/**
 * Wire and restore the draft fields currently on the page. Safe to call after
 * every DOM update (reinitAfterDOMUpdate, #3351): the listeners are installed
 * once and each field is restored once, when it first appears.
 *
 * A field that appears after init is never given a saved value over what the
 * user is typing: one that is focused or already edited is skipped (and still
 * counts as restored, so a saved value cannot land on it later). `atInit`
 * restores every field, as the page-load restore always did.
 */
function syncDraftFields(atInit) {
    if (!document.querySelector('[data-draft-enabled]')) return;
    _installDraftListeners();
    const savedByKey = new Map();
    document.querySelectorAll('[data-draft="true"]').forEach(field => {
        if (_draftRestored.get(field) === field.name) return;
        _draftRestored.set(field, field.name);
        const place = _draftTargetFor(field);
        const draftKey = place && place.root.getAttribute('data-draft-key');
        if (!draftKey || !field.name || UNSAFE_KEYS.includes(field.name)) return;
        if (field.type === 'file') return;
        if (!savedByKey.has(draftKey)) {
            // A debounced save still waiting to be written is newer than storage.
            savedByKey.set(
                draftKey,
                globalDraftManager.pendingData.get(draftKey) || globalDraftManager.loadDraft(draftKey)
            );
        }
        const saved = savedByKey.get(draftKey);
        if (!saved || !Object.prototype.hasOwnProperty.call(saved, field.name)) return;
        if (!atInit && (_draftEdited.get(field) === field.name || field === document.activeElement)) return;
        if (field.type === 'checkbox') {
            field.checked = saved[field.name];
        } else {
            field.value = saved[field.name];
        }
    });
}

/**
 * Restore the saved draft again into the fields of `scope` that were already
 * restored.
 *
 * A page-load mount morphs the HTTP-prerendered DOM against the server's HTML
 * (#1610), and that morph writes the server's value into every field the user
 * is not in, so a restore done at init is undone by it. Called right after that
 * morph, this puts the draft back. It is scoped to the container the morph
 * rewrote: a draft is applied when a field first appears or at page load, never
 * again to fields of an unrelated container (a lazy view mounting later must not
 * revert a value the server has set since). A field the user has already typed
 * in or is focused in keeps its value.
 */
function restoreDraftFields(scope) {
    if (!scope || typeof scope.querySelectorAll !== 'function') return;
    if (!document.querySelector('[data-draft-enabled]')) return;
    const fields = scope.querySelectorAll('[data-draft="true"]');
    if (fields.length === 0) return;
    fields.forEach(field => _draftRestored.delete(field));
    syncDraftFields(false);
}

function initDraftMode() {
    // Check if draft mode is enabled on this page
    const draftRoot = document.querySelector('[data-draft-enabled]');
    if (!draftRoot) return;

    const draftKey = draftRoot.getAttribute('data-draft-key');
    if (!draftKey) {
        console.warn('[DraftMode] Draft enabled but no draft-key found');
        return;
    }

    if (globalThis.djustDebug) console.log(`[DraftMode] Initializing draft mode with key: ${draftKey}`);

    // Restore the saved draft into the fields and save from here on
    syncDraftFields(true);

    // Check for draft clear flag
    if (draftRoot.hasAttribute('data-draft-clear')) {
        if (globalThis.djustDebug) console.log('[DraftMode] Draft clear flag detected, clearing draft...');
        globalDraftManager.clearDraft(draftKey);
        draftRoot.removeAttribute('data-draft-clear');
    }
}

/**
 * Clear the draft of every draft root carrying `data-draft-clear`.
 * `DraftModeMixin.clear_draft()` sets it on the NEXT render, which usually
 * arrives as a patch after an event (a successful submit), not as a page load,
 * so this runs after every DOM update (reinitAfterDOMUpdate) (#2971). The
 * attribute is left in place: the server's VDOM still has it, and removing it
 * here would keep a later render that carries it again from patching it back.
 * The server drops it on its next render.
 */
// Roots whose current data-draft-clear flag was already applied: an unrelated
// DOM update (a stream chunk, a child-view patch) while the flag is still on
// the page must not wipe a draft the user started after the submit.
const _draftClearApplied = new WeakSet();

function applyDraftClearFlag() {
    document.querySelectorAll('[data-draft-enabled]').forEach(function (root) {
        if (!root.hasAttribute('data-draft-clear')) {
            _draftClearApplied.delete(root);
            return;
        }
        if (_draftClearApplied.has(root)) return;
        _draftClearApplied.add(root);
        const key = root.getAttribute('data-draft-key');
        if (key) globalDraftManager.clearDraft(key);
    });
}

// Over a live connection clear_draft() also pushes `djust:draft-clear`
// (#2971), which reaches the page even when the render carries no patch.
if (typeof window !== 'undefined') {
    window.addEventListener('djust:push_event', function (e) {
        if (!e || !e.detail || e.detail.event !== 'djust:draft-clear') return;
        const payload = e.detail.payload || {};
        if (typeof payload.key === 'string' && payload.key) {
            globalDraftManager.clearDraft(payload.key);
        }
    });
}
