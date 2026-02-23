/**
 * djust TypeScript Definitions
 *
 * Provides type declarations for the djust client-side JavaScript API:
 *   - window.djust namespace
 *   - dj-hook lifecycle interface
 *   - Transport classes (WebSocket and SSE)
 *   - dj-model binding types
 *   - Streaming API types
 *   - File upload progress event types
 *
 * Usage — reference in your TypeScript project:
 *
 *   /// <reference path="path/to/djust.d.ts" />
 *
 * Or add to tsconfig.json:
 *
 *   { "files": ["node_modules/djust/static/djust/djust.d.ts"] }
 *
 * @version 0.3.2
 * @see https://djust.org
 */

// =============================================================================
// Hook System
// =============================================================================

/**
 * Context available as `this` inside dj-hook lifecycle callbacks.
 *
 * @example
 * window.djust.hooks = {
 *   MyChart: {
 *     mounted(this: DjustHookContext) {
 *       this.chart = new Chart(this.el);
 *       this.handleEvent("update_data", (data) => this.chart.update(data));
 *     },
 *     destroyed(this: DjustHookContext) { this.chart.destroy(); }
 *   }
 * };
 */
interface DjustHookContext {
  /** The DOM element the hook is mounted on. */
  el: Element;

  /**
   * The name of the LiveView that owns this element.
   * Sourced from the nearest ancestor `[dj-view]` attribute value.
   */
  viewName: string;

  /**
   * Send a custom event to the server-side LiveView handler.
   *
   * @param event - The event name (must match a `@event_handler` on the server).
   * @param payload - Optional parameters to send with the event.
   */
  pushEvent(event: string, payload?: Record<string, unknown>): void;

  /**
   * Register a callback for server-pushed events (from `push_event()` in Python).
   *
   * Multiple callbacks for the same event name are all invoked.
   *
   * @param eventName - The event name to listen for.
   * @param callback - Called when the server pushes this event.
   */
  handleEvent(eventName: string, callback: (payload: unknown) => void): void;
}

/**
 * Definition object for a dj-hook.
 *
 * All lifecycle methods are optional. Inside each method, `this` is a
 * `DjustHookContext` merged with the hook definition itself, so you can
 * store instance state directly on `this` (e.g., `this.chart = ...`).
 *
 * @example
 * const AutoFocus: DjustHook = {
 *   mounted() { this.el.focus(); }
 * };
 */
interface DjustHook {
  /**
   * Called when the element first appears in the DOM after the LiveView mounts.
   * Use this to initialize third-party widgets, set up observers, etc.
   */
  mounted?(this: DjustHookContext & this): void;

  /**
   * Called before the DOM is patched following a server update.
   * Use this to save state that would be lost during patching (e.g., scroll position).
   */
  beforeUpdate?(this: DjustHookContext & this): void;

  /**
   * Called after the DOM has been patched following a server update.
   * Use this to re-initialize or update third-party widgets with new data.
   */
  updated?(this: DjustHookContext & this): void;

  /**
   * Called when the element is removed from the DOM.
   * Use this to clean up subscriptions, destroy third-party instances, etc.
   */
  destroyed?(this: DjustHookContext & this): void;

  /**
   * Called when the WebSocket/SSE connection is lost.
   * Use this to show offline indicators or pause animations.
   */
  disconnected?(this: DjustHookContext & this): void;

  /**
   * Called when the WebSocket/SSE connection is restored after a disconnect.
   * Use this to resume updates or hide offline indicators.
   */
  reconnected?(this: DjustHookContext & this): void;

  /** Allow arbitrary additional properties for hook instance state. */
  [key: string]: unknown;
}

/**
 * Registry of named hook definitions.
 *
 * @example
 * window.djust.hooks = {
 *   MyChart: { mounted() { ... } },
 *   InfiniteScroll: { mounted() { ... }, updated() { ... } },
 * };
 */
interface DjustHookMap {
  [hookName: string]: DjustHook;
}

// =============================================================================
// WebSocket Stats
// =============================================================================

/** Individual message entry in the WebSocket message history. */
interface DjustMessageEntry {
  /** Direction of the message. */
  direction: "sent" | "received";
  /** Message type (e.g., "event", "patch", "mount"). */
  type: string;
  /** Timestamp (ms since epoch). */
  timestamp: number;
  /** Serialized message size in bytes. */
  bytes: number;
}

/** WebSocket connection statistics (available via `liveViewInstance.stats`). */
interface DjustWebSocketStats {
  /** Total number of messages sent. */
  sent: number;
  /** Total number of messages received. */
  received: number;
  /** Total bytes sent. */
  sentBytes: number;
  /** Total bytes received. */
  receivedBytes: number;
  /** Number of times the connection has been re-established. */
  reconnections: number;
  /** Last 50 messages (sent and received). */
  messages: DjustMessageEntry[];
  /** Timestamp of the current connection open (ms since epoch), or null if disconnected. */
  connectedAt: number | null;
}

// =============================================================================
// Transport Classes
// =============================================================================

/**
 * Primary WebSocket transport for djust LiveView connections.
 *
 * Instantiated automatically by `djustInit()` and stored as
 * `window.djust.liveViewInstance`. Direct instantiation is rare but possible
 * for testing or advanced setups.
 */
declare class LiveViewWebSocket {
  /** The underlying WebSocket connection, or null when disconnected. */
  ws: WebSocket | null;

  /** Session ID assigned by the server on first mount. */
  sessionId: string | null;

  /** Whether a view has been successfully mounted in this session. */
  viewMounted: boolean;

  /** Maximum number of automatic reconnection attempts before giving up. */
  maxReconnectAttempts: number;

  /** Whether this transport is enabled (false to force HTTP-only fallback). */
  enabled: boolean;

  /** WebSocket connection statistics. */
  stats: DjustWebSocketStats;

  /**
   * Optional callback invoked when all reconnect attempts are exhausted.
   * When set, replaces the built-in error overlay with custom handling
   * (e.g., switching to the SSE fallback transport).
   */
  onTransportFailed: (() => void) | null;

  /**
   * Establish a WebSocket connection.
   *
   * @param url - WebSocket URL. Defaults to `ws[s]://<host>/ws/live/`.
   */
  connect(url?: string | null): void;

  /**
   * Cleanly close the WebSocket (e.g., before Turbo navigation).
   * Suppresses the error overlay and clears reconnect state.
   */
  disconnect(): void;

  /**
   * Send an event to the server-side LiveView handler.
   *
   * @param eventName - Event name matching a `@event_handler` on the server.
   * @param params - Event parameters.
   * @param triggerElement - Optional DOM element that triggered the event (for loading states).
   * @returns Whether the message was successfully queued.
   */
  sendEvent(
    eventName: string,
    params?: Record<string, unknown>,
    triggerElement?: Element | null
  ): boolean;

  /**
   * Send a raw message object over the WebSocket.
   *
   * @param data - Message object (will be JSON-serialized).
   */
  sendMessage(data: Record<string, unknown>): void;
}

/**
 * SSE (Server-Sent Events) fallback transport.
 *
 * Used automatically when WebSocket connections are blocked (e.g., by corporate
 * proxies). Provides the same high-level API as `LiveViewWebSocket` but uses
 * HTTP POST for outgoing events and SSE for incoming server pushes.
 *
 * Note: SSE does not support binary file uploads, presence tracking, or
 * multi-actor sessions. Use WebSocket for full feature support.
 */
declare class LiveViewSSE {
  /** The underlying EventSource connection, or null when disconnected. */
  eventSource: EventSource | null;

  /** Client-generated session ID (UUIDv4). */
  sessionId: string | null;

  /** Whether a view has been successfully mounted in this session. */
  viewMounted: boolean;

  /**
   * Establish an SSE connection and mount the LiveView.
   *
   * @param viewPath - Python import path to the LiveView class.
   * @param params - Optional mount parameters.
   */
  connect(viewPath?: string | null, params?: Record<string, unknown>): void;

  /**
   * Send an event to the server via HTTP POST.
   *
   * @param eventName - Event name matching a `@event_handler` on the server.
   * @param params - Event parameters.
   * @param triggerElement - Optional DOM element that triggered the event.
   * @returns Whether the POST was initiated.
   */
  sendEvent(
    eventName: string,
    params?: Record<string, unknown>,
    triggerElement?: Element | null
  ): boolean;

  /** Clean up the EventSource connection. */
  disconnect(): void;
}

// =============================================================================
// Navigation Types
// =============================================================================

/** Data payload for navigation commands sent from the server. */
interface DjustNavigationData {
  /** Navigation action type. */
  action?: "live_patch" | "live_redirect";
  /** URL path to navigate to. */
  path?: string;
  /** Query parameters to set. */
  params?: Record<string, string | number | null | undefined>;
  /** If true, uses `history.replaceState` instead of `pushState`. */
  replace?: boolean;
}

/** Navigation module exposed on `window.djust.navigation`. */
interface DjustNavigation {
  /**
   * Process a navigation command from the server.
   * Dispatches to `live_patch` or `live_redirect` handling.
   */
  handleNavigation(data: DjustNavigationData): void;

  /** Bind `dj-patch` and `dj-navigate` directives to click handlers. */
  bindDirectives(): void;

  /**
   * Resolve a URL pathname to a registered LiveView path.
   *
   * @param pathname - URL pathname to look up (e.g., "/items/42/").
   * @returns The LiveView Python path, or null if not found.
   */
  resolveViewPath(pathname: string): string | null;
}

// =============================================================================
// Upload Types
// =============================================================================

/** Server-provided configuration for an upload slot. */
interface DjustUploadConfig {
  /** Accepted MIME types or file extensions (e.g., ["image/*", ".pdf"]). */
  accept: string[];
  /** Maximum number of files for this slot. */
  max_entries: number;
  /** Maximum file size in bytes. */
  max_file_size: number;
  /** Upload slot name (matches `allow_upload("name", ...)` on the server). */
  name?: string;
}

/** Per-file upload entry tracked during an active upload. */
interface DjustUploadEntry {
  /** Unique upload reference (UUIDv4). */
  ref: string;
  /** Original file name. */
  name: string;
  /** File size in bytes. */
  size: number;
  /** MIME type. */
  type: string;
  /** Upload progress (0–100). */
  progress: number;
  /** Upload slot name this file belongs to. */
  uploadName: string;
  /** Whether this upload has completed successfully. */
  done: boolean;
  /** Server-reported validation errors. */
  errors: string[];
}

/** Detail payload for the `djust:upload:progress` custom DOM event. */
interface DjustUploadProgressEventDetail {
  /** Upload reference UUID. */
  ref: string;
  /** Progress percentage (0–100). */
  progress: number;
  /** Current status reported by the server. */
  status: "uploading" | "complete" | "error" | string;
  /** Upload slot name, or null if unknown. */
  uploadName: string | null;
}

/** Upload module exposed on `window.djust.uploads`. */
interface DjustUploads {
  /**
   * Initialize upload slot configurations from the server mount response.
   *
   * @param configs - Map of upload slot name to config.
   */
  setConfigs(configs: Record<string, DjustUploadConfig> | null): void;

  /**
   * Handle an upload progress message from the server.
   *
   * @param data - Progress data from the server.
   */
  handleProgress(data: { ref: string; progress: number; status: string }): void;

  /** Bind upload input, drop zone, and preview handlers to the DOM. */
  bindHandlers(): void;

  /**
   * Cancel an in-progress upload.
   *
   * @param ref - The upload reference UUID to cancel.
   */
  cancelUpload(ref: string): void;

  /** Map of currently active uploads, keyed by upload reference UUID. */
  activeUploads: Map<string, DjustUploadEntry>;
}

// =============================================================================
// Streaming Types
// =============================================================================

/** A single DOM operation in a stream message. */
interface DjustStreamOp {
  /** CSS selector for the target element. */
  target: string;
  /** Operation to perform. */
  op: "append" | "prepend" | "replace" | "delete" | "text" | "error";
  /** HTML content for append/prepend/replace. */
  html?: string;
  /** Plain text content for the "text" op (used for LLM token streaming). */
  text?: string;
  /** Error message for the "error" op. */
  error?: string;
}

/** Stream message from the server (sent for `StreamingMixin` views). */
interface DjustStreamMessage {
  /** Message type identifier. */
  type: "stream";
  /** Stream name (matches `stream("name", ...)` on the server). */
  stream: string;
  /** Array of DOM operations to apply in order. */
  ops: DjustStreamOp[];
}

/** Active stream tracking entry. */
interface DjustStreamInfo {
  /** Timestamp when the stream started (ms since epoch). */
  started: number;
  /** Count of errors encountered during this stream. */
  errorCount: number;
}

// =============================================================================
// Custom DOM Events
// =============================================================================

/**
 * Detail for the `djust:upload:progress` event.
 * Dispatched on `window` when upload progress is received from the server.
 *
 * @example
 * window.addEventListener("djust:upload:progress", (e: CustomEvent<DjustUploadProgressEventDetail>) => {
 *   console.log(e.detail.progress + "%");
 * });
 */
interface DjustUploadProgressEvent extends CustomEvent<DjustUploadProgressEventDetail> {
  type: "djust:upload:progress";
}

/**
 * Detail for server-pushed events dispatched to dj-hook `handleEvent` listeners.
 * Also fired as a DOM CustomEvent for integration with non-hook code.
 */
interface DjustPushEventDetail {
  /** Event name (matches the first argument to `push_event()` on the server). */
  event: string;
  /** Payload from the server (arbitrary JSON-serializable value). */
  payload: unknown;
}

// =============================================================================
// Main Djust Interface (window.djust)
// =============================================================================

/**
 * The djust client API, exposed as `window.djust`.
 *
 * All properties are populated during initialization (`djustInit()`).
 * Some are null or undefined before the page has mounted a LiveView.
 */
interface Djust {
  // -------------------------------------------------------------------------
  // Transport
  // -------------------------------------------------------------------------

  /** The `LiveViewWebSocket` constructor (for advanced use / testing). */
  LiveViewWebSocket: typeof LiveViewWebSocket;

  /** The `LiveViewSSE` constructor (for advanced use / testing). */
  LiveViewSSE: typeof LiveViewSSE;

  /**
   * The active transport instance (WebSocket or SSE).
   * Null before `djustInit()` completes or after deliberate disconnect.
   */
  liveViewInstance: LiveViewWebSocket | LiveViewSSE | null;

  // -------------------------------------------------------------------------
  // Hook System
  // -------------------------------------------------------------------------

  /**
   * User-defined hook registry.
   *
   * Set this before page load (or after `djustInit()`) to register hooks:
   *
   * @example
   * window.djust.hooks = { MyChart: { mounted() { ... } } };
   */
  hooks: DjustHookMap;

  /**
   * Scan `root` (default: `document`) for `[dj-hook]` elements and mount
   * any unregistered hooks. Called automatically on init and after DOM patches.
   *
   * @param root - Root element to search within.
   */
  mountHooks(root?: Element | Document): void;

  /**
   * Notify mounted hooks that a DOM update is about to occur.
   * Calls `beforeUpdate()` on all hooks in `root`.
   *
   * @param root - Root element to search within.
   */
  beforeUpdateHooks(root?: Element | Document): void;

  /**
   * Update hooks after a DOM patch: call `updated()` on existing hooks,
   * mount new elements, and destroy removed elements.
   *
   * @param root - Root element to search within.
   */
  updateHooks(root?: Element | Document): void;

  /** Notify all active hooks that the connection was lost (`disconnected()`). */
  notifyHooksDisconnected(): void;

  /** Notify all active hooks that the connection was restored (`reconnected()`). */
  notifyHooksReconnected(): void;

  /**
   * Dispatch a server-pushed event to all hooks that registered a listener
   * via `this.handleEvent(eventName, ...)`.
   *
   * @param eventName - Event name pushed by the server via `push_event()`.
   * @param payload - Payload from the server.
   */
  dispatchPushEventToHooks(eventName: string, payload: unknown): void;

  /** Call `destroyed()` on all active hooks and clear the hook registry. */
  destroyAllHooks(): void;

  /**
   * Map of currently active hook instances, keyed by an internal element ID.
   * Read-only; managed by the hook system.
   */
  readonly _activeHooks: Map<string, { hookName: string; instance: DjustHook & DjustHookContext; el: Element }>;

  // -------------------------------------------------------------------------
  // Event Handling
  // -------------------------------------------------------------------------

  /**
   * Send an event to the server-side LiveView.
   *
   * Tries WebSocket first; falls back to HTTP if WebSocket is unavailable.
   *
   * @param eventName - Event handler name on the server.
   * @param params - Optional event parameters.
   */
  handleEvent(eventName: string, params?: Record<string, unknown>): void;

  // -------------------------------------------------------------------------
  // Navigation
  // -------------------------------------------------------------------------

  /** URL routing and browser history management. */
  navigation: DjustNavigation;

  /**
   * Internal route map populated by `live_session()` for `live_redirect` support.
   * Maps URL pathnames (or `:param` patterns) to LiveView Python paths.
   */
  _routeMap: Record<string, string>;

  // -------------------------------------------------------------------------
  // Streaming
  // -------------------------------------------------------------------------

  /**
   * Apply a streaming DOM update message.
   * Called automatically for `type: "stream"` server messages.
   *
   * @param data - Stream message from the server.
   */
  handleStreamMessage(data: DjustStreamMessage): void;

  /**
   * Get all currently active stream states.
   *
   * @returns Map of stream name to stream info.
   */
  getActiveStreams(): Map<string, DjustStreamInfo>;

  // -------------------------------------------------------------------------
  // File Uploads
  // -------------------------------------------------------------------------

  /** File upload management module. */
  uploads: DjustUploads;

  // -------------------------------------------------------------------------
  // Model Binding
  // -------------------------------------------------------------------------

  /**
   * Bind `dj-model` attributes in `root` to send `update_model` events.
   * Called automatically on init and after DOM patches.
   *
   * @param root - Root element to search within. Defaults to `document`.
   */
  bindModelElements(root?: Element | Document): void;

  // -------------------------------------------------------------------------
  // Accessibility
  // -------------------------------------------------------------------------

  /** Accessibility helpers for announcements and focus management. */
  accessibility: {
    /**
     * Process ARIA live region announcements from the server.
     *
     * @param announcements - Array of announcement strings.
     */
    processAnnouncements(announcements: string[]): void;

    /**
     * Move focus to the specified element.
     *
     * @param args - Tuple of [CSS selector, focus options].
     */
    processFocus(args: [selector: string, options?: FocusOptions]): void;
  };

  // -------------------------------------------------------------------------
  // Internal / Advanced
  // -------------------------------------------------------------------------

  /**
   * Switch from WebSocket to SSE fallback transport.
   * Called automatically when the WebSocket connection cannot be established.
   */
  _switchToSSETransport(): void;
}

// =============================================================================
// Global Declarations
// =============================================================================

declare global {
  interface Window {
    /**
     * The djust LiveView client API.
     *
     * Available after the djust client script has loaded and `djustInit()` has run.
     */
    djust: Djust;

    /**
     * Phoenix LiveView-compatible hook registration alias.
     *
     * Hooks registered here are merged with `window.djust.hooks`, allowing
     * codebases migrating from Phoenix LiveView to use familiar hook syntax.
     *
     * @example
     * window.DjustHooks = { MyHook: { mounted() { ... } } };
     */
    DjustHooks: DjustHookMap | undefined;

    /**
     * Guards against double-loading the client script.
     * Set to `true` when the client initializes; checked on re-load.
     */
    _djustClientLoaded: boolean | undefined;
  }

  /**
   * Enable verbose debug logging for all djust client modules.
   *
   * Set before script load or at any point during runtime:
   *
   * @example
   * globalThis.djustDebug = true;
   */
  var djustDebug: boolean | undefined;

  // Custom DOM events
  interface WindowEventMap {
    /**
     * Fired on `window` when upload progress is received from the server.
     *
     * @example
     * window.addEventListener("djust:upload:progress", (e) => {
     *   console.log(e.detail.progress + "%");
     * });
     */
    "djust:upload:progress": CustomEvent<DjustUploadProgressEventDetail>;
  }
}

// Force this file to be treated as an ambient module (not a script file)
// so that `declare global` works correctly even in module-aware environments.
export {};
