/**
 * djust - TypeScript Definitions
 *
 * Type definitions for the djust client-side JavaScript API.
 * Provides autocomplete and type checking for TypeScript users.
 *
 * @version 0.6.0
 * @license MIT
 */

// ============================================================================
// Global Window Augmentation
// ============================================================================

declare global {
  interface Window {
    /** Main djust client namespace */
    djust: DjustClient;

    /** Phoenix LiveView-compatible hook registry (alternative to djust.hooks) */
    DjustHooks?: Record<string, HookDefinition>;

    /** Debug mode flag - enables verbose logging and dev overlays */
    DEBUG_MODE?: boolean;

    /** Debug mode flag (alternative) */
    djustDebug?: boolean;

    /** Debug panel instance (available when DJUST_DEBUG_INFO is set) */
    djustDebugPanel?: DjustDebugPanel;

    /** Debug info injected by server (when debug mode is enabled) */
    DJUST_DEBUG_INFO?: DjustDebugInfo;

    /** LiveViewWebSocket class (for extending/wrapping) */
    LiveViewWebSocket: typeof LiveViewWebSocket;

    /** Global liveview instance (deprecated, use djust.liveViewInstance) */
    liveview?: LiveViewWebSocket;

    /** Initialization complete flag */
    djustInitialized?: boolean;
  }
}

// ============================================================================
// DjustClient - Main Namespace
// ============================================================================

/**
 * Main djust client namespace exposed on window.djust
 */
interface DjustClient {
  /** WebSocket connection instance */
  liveViewInstance: LiveViewWebSocket | null;

  /** LiveViewWebSocket class constructor */
  LiveViewWebSocket: typeof LiveViewWebSocket;

  /** Registered hooks (user-defined) */
  hooks?: Record<string, HookDefinition>;

  // ---- Hook Management ----
  /** Scan DOM for dj-hook elements and mount their hooks */
  mountHooks(root?: Document | Element): void;

  /** Notify hooks before DOM update */
  beforeUpdateHooks(root?: Document | Element): void;

  /** Called after DOM patch to update/mount/destroy hooks */
  updateHooks(root?: Document | Element): void;

  /** Notify all hooks of WebSocket disconnect */
  notifyHooksDisconnected(): void;

  /** Notify all hooks of WebSocket reconnect */
  notifyHooksReconnected(): void;

  /** Dispatch server push_event to registered hook handlers */
  dispatchPushEventToHooks(eventName: string, payload: unknown): void;

  /** Destroy all active hooks */
  destroyAllHooks(): void;

  /** Internal map of active hook instances (for debugging) */
  _activeHooks: Map<string, ActiveHookEntry>;

  // ---- Streaming ----
  /** Handle incoming stream message from WebSocket */
  handleStreamMessage(data: DjustStreamMessage): void;

  /** Get info about currently active streams */
  getActiveStreams(): Record<string, StreamInfo>;

  // ---- Uploads ----
  uploads?: DjustUploads;

  // ---- Navigation ----
  navigation?: DjustNavigation;

  // ---- Optimistic Updates ----
  optimistic?: DjustOptimistic;
}

// ============================================================================
// LiveViewWebSocket - WebSocket Connection
// ============================================================================

/**
 * WebSocket connection manager for LiveView
 */
declare class LiveViewWebSocket {
  /** Raw WebSocket instance */
  ws: WebSocket | null;

  /** Server-assigned session ID */
  sessionId: string | null;

  /** Current reconnection attempt count */
  reconnectAttempts: number;

  /** Maximum reconnection attempts before giving up */
  maxReconnectAttempts: number;

  /** Base delay between reconnection attempts (ms) */
  reconnectDelay: number;

  /** Whether the view has been mounted */
  viewMounted: boolean;

  /** Whether WebSocket is enabled (false = HTTP fallback only) */
  enabled: boolean;

  /** Last event name sent (for loading state tracking) */
  lastEventName: string | null;

  /** Last trigger element (for scoped loading states) */
  lastTriggerElement: HTMLElement | null;

  /** WebSocket statistics */
  stats: WebSocketStats;

  /** Heartbeat interval ID */
  heartbeatInterval: ReturnType<typeof setInterval> | null;

  /** Skip HTML replacement on mount (content pre-rendered) */
  skipMountHtml: boolean;

  constructor();

  /**
   * Connect to the WebSocket server
   * @param url - WebSocket URL (default: auto-detect from location)
   */
  connect(url?: string): void;

  /**
   * Cleanly disconnect the WebSocket (for TurboNav navigation)
   */
  disconnect(): void;

  /**
   * Mount a LiveView at the given path
   * @param viewPath - View path to mount (e.g., "myapp.views.CounterView")
   * @param params - Initial params to pass to the view
   * @returns true if mount message sent, false if WebSocket unavailable
   */
  mount(viewPath: string, params?: Record<string, unknown>): boolean;

  /**
   * Auto-mount the view found in DOM container
   */
  autoMount(): void;

  /**
   * Send an event to the server
   * @param eventName - Event name (e.g., "increment", "form_submit")
   * @param params - Event parameters
   * @param triggerElement - Element that triggered the event (for loading states)
   * @returns true if event sent, false if WebSocket unavailable
   */
  sendEvent(
    eventName: string,
    params?: Record<string, unknown>,
    triggerElement?: HTMLElement | null
  ): boolean;

  /**
   * Send a raw message to the WebSocket
   * @param data - Data to send (will be JSON stringified)
   */
  sendMessage(data: Record<string, unknown>): void;

  /**
   * Handle an incoming WebSocket message
   * @param data - Parsed message data
   */
  handleMessage(data: DjustServerMessage): void;

  /**
   * Handle scoped HTML update for an embedded child LiveView
   */
  handleEmbeddedUpdate(data: EmbeddedUpdateMessage): void;

  /**
   * Start the heartbeat interval
   * @param interval - Heartbeat interval in milliseconds (default: 30000)
   */
  startHeartbeat(interval?: number): void;

  /**
   * Track a WebSocket message in the history
   */
  trackMessage(message: TrackedMessage): void;
}

// ============================================================================
// Hook System Types
// ============================================================================

/**
 * Hook definition provided by users
 */
interface HookDefinition {
  /**
   * Called when the element is first mounted in the DOM.
   * `this.el` is available and refers to the DOM element.
   */
  mounted?(): void;

  /**
   * Called before the DOM is updated (before patches applied).
   * Use to save state that might be lost during update.
   */
  beforeUpdate?(): void;

  /**
   * Called after the DOM is updated (after patches applied).
   * `this.el` reference is updated if the element was replaced.
   */
  updated?(): void;

  /**
   * Called when the element is removed from the DOM.
   * Use for cleanup (remove event listeners, cancel timers, etc.)
   */
  destroyed?(): void;

  /**
   * Called when the WebSocket connection is lost.
   */
  disconnected?(): void;

  /**
   * Called when the WebSocket connection is restored.
   */
  reconnected?(): void;
}

/**
 * Hook instance - extends HookDefinition with runtime properties
 */
interface HookInstance extends HookDefinition {
  /** The DOM element this hook is attached to */
  el: HTMLElement;

  /** The LiveView name (from closest data-djust-view) */
  viewName: string;

  /**
   * Send an event to the server
   * @param event - Event name
   * @param payload - Event payload
   */
  pushEvent(event: string, payload?: Record<string, unknown>): void;

  /**
   * Register a callback for server-pushed events
   * @param eventName - Event name to listen for
   * @param callback - Callback function
   */
  handleEvent(eventName: string, callback: (payload: unknown) => void): void;

  /** Internal event handlers registry */
  _eventHandlers: Record<string, Array<(payload: unknown) => void>>;
}

/**
 * Internal active hook entry
 */
interface ActiveHookEntry {
  hookName: string;
  instance: HookInstance;
  el: HTMLElement;
}

// ============================================================================
// WebSocket Message Types
// ============================================================================

/**
 * Base server message type
 */
interface DjustBaseMessage {
  type: string;
}

/**
 * Connect message - received after WebSocket connection established
 */
interface DjustConnectMessage extends DjustBaseMessage {
  type: 'connect';
  session_id: string;
}

/**
 * Mount message - received after view is mounted
 */
interface DjustMountMessage extends DjustBaseMessage {
  type: 'mount';
  view: string;
  html?: string;
  version?: number;
  has_ids?: boolean;
  cache_config?: Record<string, CacheConfig>;
  upload_configs?: Record<string, UploadConfig>;
}

/**
 * Patch message - DOM diff updates
 */
interface DjustPatchMessage extends DjustBaseMessage {
  type: 'patch';
  patches: DjustPatch[];
  version?: number;
  hotreload?: boolean;
  file?: string;
  performance?: PerformanceData;
  timing?: TimingData;
}

/**
 * HTML update message - full HTML replacement
 */
interface DjustHtmlUpdateMessage extends DjustBaseMessage {
  type: 'html_update';
  html: string;
  selector?: string;
}

/**
 * Error message from server
 */
interface DjustErrorMessage extends DjustBaseMessage {
  type: 'error';
  error: string;
  traceback?: string;
  event?: string;
  validation_details?: Record<string, unknown>;
}

/**
 * Pong message (heartbeat response)
 */
interface DjustPongMessage extends DjustBaseMessage {
  type: 'pong';
}

/**
 * Upload progress message
 */
interface DjustUploadProgressMessage extends DjustBaseMessage {
  type: 'upload_progress';
  ref: string;
  progress: number;
  upload_name: string;
}

/**
 * Upload registered message
 */
interface DjustUploadRegisteredMessage extends DjustBaseMessage {
  type: 'upload_registered';
  ref: string;
  upload_name: string;
}

/**
 * Stream message - partial DOM updates for real-time content
 */
interface DjustStreamMessage extends DjustBaseMessage {
  type: 'stream';
  stream: string;
  ops: StreamOperation[];
}

/**
 * Push event message - server-initiated event for JS hooks
 */
interface DjustPushEventMessage extends DjustBaseMessage {
  type: 'push_event';
  event: string;
  payload: unknown;
}

/**
 * Embedded update message - scoped update for child LiveView
 */
interface EmbeddedUpdateMessage extends DjustBaseMessage {
  type: 'embedded_update';
  view_id: string;
  html: string;
}

/**
 * Rate limit exceeded message
 */
interface DjustRateLimitMessage extends DjustBaseMessage {
  type: 'rate_limit_exceeded';
  message?: string;
}

/**
 * Navigation message (live_patch / live_redirect)
 */
interface DjustNavigationMessage extends DjustBaseMessage {
  type: 'navigation';
  path?: string;
  params?: Record<string, string | number | null>;
  replace?: boolean;
}

/**
 * Reload message (hot reload)
 */
interface DjustReloadMessage extends DjustBaseMessage {
  type: 'reload';
  file: string;
}

/**
 * Union of all server message types
 */
type DjustServerMessage =
  | DjustConnectMessage
  | DjustMountMessage
  | DjustPatchMessage
  | DjustHtmlUpdateMessage
  | DjustErrorMessage
  | DjustPongMessage
  | DjustUploadProgressMessage
  | DjustUploadRegisteredMessage
  | DjustStreamMessage
  | DjustPushEventMessage
  | EmbeddedUpdateMessage
  | DjustRateLimitMessage
  | DjustNavigationMessage
  | DjustReloadMessage;

// ============================================================================
// Patch Types
// ============================================================================

/**
 * DOM patch operation
 */
interface DjustPatch {
  /** Patch operation type */
  op: 'replace' | 'insert' | 'delete' | 'move' | 'attr' | 'text';

  /** Target element path or selector */
  path?: string;

  /** Target element ID (data-dj-id) */
  id?: string;

  /** HTML content for insert/replace operations */
  html?: string;

  /** Text content for text operations */
  text?: string;

  /** Attribute name for attr operations */
  attr?: string;

  /** Attribute value for attr operations (null to remove) */
  value?: string | null;

  /** Position for insert operations */
  position?: 'before' | 'after' | 'prepend' | 'append';

  /** Reference element for insert operations */
  ref?: string;
}

// ============================================================================
// Stream Types
// ============================================================================

/**
 * Stream operation
 */
interface StreamOperation {
  /** Operation type */
  op: 'replace' | 'append' | 'prepend' | 'delete' | 'text' | 'error' | 'done' | 'start';

  /** CSS selector for target element */
  target: string;

  /** HTML content (for replace/append/prepend) */
  html?: string;

  /** Text content (for text op) */
  text?: string;

  /** Insertion mode for text (append/replace/prepend) */
  mode?: 'append' | 'replace' | 'prepend';

  /** Error message (for error op) */
  error?: string;
}

/**
 * Active stream info
 */
interface StreamInfo {
  started: number;
  errorCount: number;
}

// ============================================================================
// Upload Types
// ============================================================================

/**
 * Upload configuration from server
 */
interface UploadConfig {
  /** Maximum file size in bytes */
  max_size?: number;

  /** Allowed MIME types */
  accept?: string[];

  /** Maximum number of files */
  max_entries?: number;

  /** Chunk size for binary upload */
  chunk_size?: number;

  /** Whether to auto-upload on selection */
  auto_upload?: boolean;
}

/**
 * Upload module interface
 */
interface DjustUploads {
  /** Set upload configurations from server */
  setConfigs(configs: Record<string, UploadConfig>): void;

  /** Handle upload progress message */
  handleProgress(data: DjustUploadProgressMessage): void;

  /** Upload a file */
  uploadFile(
    ws: LiveViewWebSocket,
    uploadName: string,
    file: File,
    config?: UploadConfig
  ): Promise<void>;

  /** Cancel an upload */
  cancelUpload(ref: string): void;

  /** Bind upload inputs in the DOM */
  bindUploadInputs(root?: Document | Element): void;
}

// ============================================================================
// Navigation Types
// ============================================================================

/**
 * Navigation module interface
 */
interface DjustNavigation {
  /** Handle navigation command from server */
  handleNavigation(data: DjustNavigationMessage): void;

  /**
   * Programmatic live_patch (update URL without remounting)
   * @param path - New path (optional)
   * @param params - Query parameters
   * @param replace - Use replaceState instead of pushState
   */
  livePatch(
    path?: string,
    params?: Record<string, string | number | null>,
    replace?: boolean
  ): void;

  /**
   * Programmatic live_redirect (navigate to new view)
   * @param path - New path
   * @param params - Query parameters
   * @param replace - Use replaceState instead of pushState
   */
  liveRedirect(
    path: string,
    params?: Record<string, string | number | null>,
    replace?: boolean
  ): void;
}

// ============================================================================
// Optimistic Updates Types
// ============================================================================

/**
 * Optimistic updates module interface
 */
interface DjustOptimistic {
  /** Clear an optimistic update (server confirmed) */
  clearOptimisticUpdate(updateId: string): void;

  /** Revert an optimistic update (server rejected) */
  revertOptimisticUpdate(updateId: string): void;

  /** Apply an optimistic update */
  applyOptimisticUpdate(
    updateId: string,
    element: HTMLElement,
    changes: OptimisticChanges
  ): void;
}

/**
 * Optimistic update changes
 */
interface OptimisticChanges {
  /** Text content change */
  text?: string;

  /** HTML content change */
  html?: string;

  /** Attribute changes */
  attrs?: Record<string, string | null>;

  /** Class additions */
  addClass?: string | string[];

  /** Class removals */
  removeClass?: string | string[];

  /** Style changes */
  style?: Record<string, string>;
}

// ============================================================================
// Statistics & Debugging Types
// ============================================================================

/**
 * WebSocket statistics
 */
interface WebSocketStats {
  /** Total messages sent */
  sent: number;

  /** Total messages received */
  received: number;

  /** Total bytes sent */
  sentBytes: number;

  /** Total bytes received */
  receivedBytes: number;

  /** Number of reconnections */
  reconnections: number;

  /** Recent message history (last 50) */
  messages: TrackedMessage[];

  /** Timestamp of current connection */
  connectedAt: number | null;
}

/**
 * Tracked message in history
 */
interface TrackedMessage {
  direction: 'sent' | 'received';
  type: string;
  size: number;
  timestamp: number;
  data: Record<string, unknown>;
}

/**
 * Cache configuration for an event
 */
interface CacheConfig {
  /** Time-to-live in seconds */
  ttl?: number;

  /** Parameters to include in cache key */
  key_params?: string[];
}

/**
 * Server timing data
 */
interface TimingData {
  /** Server-side processing time (ms) */
  server?: number;

  /** Database query time (ms) */
  db?: number;

  /** Template render time (ms) */
  render?: number;
}

/**
 * Performance data from server
 */
interface PerformanceData {
  /** Total server time (ms) */
  total_ms?: number;

  /** Handler execution time (ms) */
  handler_ms?: number;

  /** Render time (ms) */
  render_ms?: number;

  /** Diff time (ms) */
  diff_ms?: number;

  /** Number of patches generated */
  patch_count?: number;
}

/**
 * Debug info injected by server
 */
interface DjustDebugInfo {
  view_name?: string;
  view_path?: string;
  state?: Record<string, unknown>;
  handlers?: string[];
  [key: string]: unknown;
}

/**
 * Debug panel instance
 */
interface DjustDebugPanel {
  /** Process debug info from server */
  processDebugInfo(info: DjustDebugInfo): void;

  /** Log an event to the debug panel */
  logEvent(
    eventName: string,
    params: Record<string, unknown>,
    result: unknown,
    duration: number,
    elementInfo?: ElementInfo | null
  ): void;

  /** Log patches to the debug panel */
  logPatches(
    patches: DjustPatch[],
    timing?: { client: number; server?: number },
    performance?: PerformanceData | null
  ): void;

  /** Destroy the debug panel */
  destroy(): void;
}

/**
 * Element info for debug logging
 */
interface ElementInfo {
  tagName: string;
  id: string | null;
  className: string | null;
  text: string | null;
  attributes: Record<string, string>;
}

// ============================================================================
// Event Types
// ============================================================================

/**
 * Custom event: djust:error
 */
interface DjustErrorEventDetail {
  error: string;
  traceback: string | null;
  event: string | null;
  validation_details: Record<string, unknown> | null;
}

/**
 * Custom event: djust:push_event
 */
interface DjustPushEventDetail {
  event: string;
  payload: unknown;
}

/**
 * Stream event detail
 */
interface StreamEventDetail {
  stream: string;
  op?: string;
  text?: string;
  mode?: string;
  error?: string;
}

// Augment global CustomEvent types
declare global {
  interface WindowEventMap {
    'djust:error': CustomEvent<DjustErrorEventDetail>;
    'djust:push_event': CustomEvent<DjustPushEventDetail>;
  }

  interface HTMLElementEventMap {
    'stream:update': CustomEvent<StreamEventDetail>;
    'stream:text': CustomEvent<StreamEventDetail>;
    'stream:remove': CustomEvent<StreamEventDetail>;
    'stream:error': CustomEvent<StreamEventDetail>;
    'stream:done': CustomEvent<StreamEventDetail>;
    'stream:start': CustomEvent<StreamEventDetail>;
  }
}

// ============================================================================
// Global Functions
// ============================================================================

/**
 * Main event handler function (global)
 */
declare function handleEvent(
  eventName: string,
  params?: Record<string, unknown>
): Promise<void>;

/**
 * Apply DOM patches (global)
 */
declare function applyPatches(
  patches: DjustPatch[],
  targetSelector?: string
): void;

/**
 * Bind LiveView events to DOM elements (global)
 */
declare function bindLiveViewEvents(root?: Document | Element): void;

// Export empty object to make this a module
export {};
