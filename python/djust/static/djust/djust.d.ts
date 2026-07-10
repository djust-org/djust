/**
 * djust client API — ambient type declarations (ADR-025).
 *
 * Covers the PUBLIC, documented surface of `window.djust`: hook definitions,
 * the hook instance API, JS command chains, and the custom-command registry.
 * Internal underscore-prefixed members are deliberately not declared.
 *
 * Usage (no build step required — editors pick this up for autocomplete):
 *   Add this file's path to your tsconfig.json or jsconfig.json.
 *   Locate the installed path with:
 *   python -c "import djust, pathlib; print(pathlib.Path(djust.__file__).parent / 'static/djust/djust.d.ts')"
 */

export {};

declare global {
    /** Options accepted by every command: at most ONE of to/inner/closest. */
    interface DjustTargetOptions {
        /** Absolute CSS selector (document.querySelectorAll). */
        to?: string;
        /** Scoped to the origin element's descendants. */
        inner?: string;
        /** Walk up from the origin element. */
        closest?: string;
    }

    interface DjustShowOptions extends DjustTargetOptions {
        display?: string;
        transition?: string;
        time?: number;
    }

    interface DjustTransitionOptions extends DjustTargetOptions {
        time?: number;
    }

    interface DjustDispatchOptions extends DjustTargetOptions {
        detail?: Record<string, unknown>;
        bubbles?: boolean;
    }

    interface DjustPushOptions {
        value?: Record<string, unknown>;
        target?: string;
        page_loading?: boolean;
    }

    /** A chain of JS Command operations (immutable — every method returns a new chain). */
    interface DjustJSChain {
        show(selector?: string, options?: DjustShowOptions): DjustJSChain;
        hide(selector?: string, options?: DjustShowOptions): DjustJSChain;
        toggle(selector?: string, options?: DjustShowOptions): DjustJSChain;
        addClass(names: string, options?: DjustTargetOptions): DjustJSChain;
        removeClass(names: string, options?: DjustTargetOptions): DjustJSChain;
        transition(names: string, options?: DjustTransitionOptions): DjustJSChain;
        setAttr(name: string, value: string, options?: DjustTargetOptions): DjustJSChain;
        removeAttr(name: string, options?: DjustTargetOptions): DjustJSChain;
        focus(selector?: string, options?: DjustTargetOptions): DjustJSChain;
        dispatch(event: string, options?: DjustDispatchOptions): DjustJSChain;
        push(event: string, options?: DjustPushOptions): DjustJSChain;
        /** Append a user-registered custom command op (ADR-025). */
        ext(name: string, args?: DjustTargetOptions & Record<string, unknown>): DjustJSChain;
        /** Run the chain against originEl (default: document.body). */
        exec(originEl?: Element): Promise<void>;
        toString(): string;
    }

    /** Chain factory: `djust.js.show('#modal')` starts a new chain. */
    interface DjustJSFactory {
        chain(): DjustJSChain;
        show(selector?: string, options?: DjustShowOptions): DjustJSChain;
        hide(selector?: string, options?: DjustShowOptions): DjustJSChain;
        toggle(selector?: string, options?: DjustShowOptions): DjustJSChain;
        addClass(names: string, options?: DjustTargetOptions): DjustJSChain;
        removeClass(names: string, options?: DjustTargetOptions): DjustJSChain;
        transition(names: string, options?: DjustTransitionOptions): DjustJSChain;
        setAttr(name: string, value: string, options?: DjustTargetOptions): DjustJSChain;
        removeAttr(name: string, options?: DjustTargetOptions): DjustJSChain;
        focus(selector?: string, options?: DjustTargetOptions): DjustJSChain;
        dispatch(event: string, options?: DjustDispatchOptions): DjustJSChain;
        push(event: string, options?: DjustPushOptions): DjustJSChain;
        ext(name: string, args?: DjustTargetOptions & Record<string, unknown>): DjustJSChain;
    }

    /**
     * Implementation contract for a user-registered custom command (ADR-025).
     * `targets` is the resolved element list (to/inner/closest, default
     * [originEl]); a returned Promise is awaited before the next op runs.
     */
    type DjustCommandFn = (
        targets: Element[],
        args: Record<string, unknown>,
        originEl: Element | null
    ) => void | Promise<void>;

    interface DjustCommands {
        /**
         * Register a custom command. Throws if `name` is a djust built-in,
         * contains a dot, or `fn` is not a function. Re-registering an ext
         * name overwrites (last wins).
         */
        register(name: string, fn: DjustCommandFn): void;
    }

    /** `this` inside dj-hook lifecycle methods. */
    interface DjustHookInstance {
        /** The element carrying dj-hook. */
        el: Element;
        /** The owning LiveView's dj-view name ('' when detached). */
        viewName: string;
        /**
         * Typed values from dj-hook-value-* attributes (ADR-025).
         * JSON-first coercion, raw-string fallback; LIVE reads (post-morph
         * attribute changes are visible immediately); read-only.
         */
        readonly values: Readonly<Record<string, unknown>>;
        /** First descendant with dj-hook-target="name", or null (ADR-025). */
        target(name: string): Element | null;
        /** All descendants with dj-hook-target="name" (ADR-025). */
        targets(name: string): Element[];
        /** Send a custom event to the server over the LiveView WebSocket. */
        pushEvent(event: string, payload?: Record<string, unknown>): void;
        /** Register a callback for server-pushed events. */
        handleEvent(event: string, callback: (payload: unknown) => void): void;
        /** Start a JS Command chain (exec() defaults to this hook's element). */
        js(): DjustJSChain | null;
    }

    /** A hook definition registered under window.djust.hooks. */
    interface DjustHookDefinition {
        mounted?(this: DjustHookInstance & Record<string, unknown>): void | Promise<void>;
        updated?(this: DjustHookInstance & Record<string, unknown>): void | Promise<void>;
        destroyed?(this: DjustHookInstance & Record<string, unknown>): void | Promise<void>;
        disconnected?(this: DjustHookInstance & Record<string, unknown>): void | Promise<void>;
        reconnected?(this: DjustHookInstance & Record<string, unknown>): void | Promise<void>;
        [key: string]: unknown;
    }

    interface DjustGlobal {
        /** User hook registry: window.djust.hooks = { MyHook: {...} }. */
        hooks?: Record<string, DjustHookDefinition>;
        /** JS Command chain factory. */
        js: DjustJSFactory;
        /** Custom-command registry (ADR-025). */
        commands: DjustCommands;
        /** Mount hooks under root (default document). */
        mountHooks(root?: ParentNode): void;
        /** Re-scan hooks after DOM updates under root. */
        updateHooks(root?: ParentNode): void;
    }

    interface Window {
        djust: DjustGlobal;
        /** Phoenix LiveView-compatible alias for the hook registry. */
        DjustHooks?: Record<string, DjustHookDefinition>;
        /** True when Django DEBUG=True (set by the djust template tags). */
        DEBUG_MODE?: boolean;
    }
}
