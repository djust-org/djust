# TypeScript Support

djust provides TypeScript definitions for the client-side JavaScript API, giving you autocomplete, type checking, and better IDE support when writing hooks and client-side code.

## Installation

### Option 1: Using npm/pnpm/yarn (Recommended)

If you have a `package.json` in your project:

```bash
npm install djust --save-dev
# or
pnpm add -D djust
# or
yarn add -D djust
```

### Option 2: Reference Types Directly

If you're including djust's JavaScript via Django's static files, you can reference the type definitions directly in your TypeScript config.

Add to your `tsconfig.json`:

```json
{
  "compilerOptions": {
    "types": ["./path/to/djust/types"]
  }
}
```

Or use a triple-slash directive in your TypeScript files:

```typescript
/// <reference types="djust" />
```

## Example tsconfig.json

Here's a recommended `tsconfig.json` for Django projects using djust:

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "module": "ESNext",
    "moduleResolution": "bundler",
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "strict": true,
    "noEmit": true,
    "skipLibCheck": true,
    "esModuleInterop": true,
    "allowSyntheticDefaultImports": true,
    "forceConsistentCasingInFileNames": true
  },
  "include": [
    "static/**/*.ts",
    "templates/**/*.ts"
  ]
}
```

## Usage Examples

### Defining Hooks

With TypeScript, you get full autocomplete and type checking for hook lifecycle methods:

```typescript
// hooks.ts

// Types are automatically available on window.djust
window.djust.hooks = {
  Chart: {
    mounted() {
      // this.el is typed as HTMLElement
      const canvas = this.el as HTMLCanvasElement;
      const data = this.el.dataset.values?.split(',').map(Number);
      
      // Initialize your chart library
      this.chart = new Chart(canvas, { data });
      
      // Register for server events
      this.handleEvent('update_data', (payload) => {
        // payload is typed as unknown - narrow it yourself
        const newData = payload as { values: number[] };
        this.chart.update(newData.values);
      });
    },
    
    updated() {
      // Called after server re-renders the element
      const newData = this.el.dataset.values?.split(',').map(Number);
      this.chart.update(newData);
    },
    
    destroyed() {
      // Cleanup
      this.chart?.destroy();
    },
    
    disconnected() {
      // WebSocket lost - show offline indicator
      this.el.classList.add('offline');
    },
    
    reconnected() {
      // WebSocket restored
      this.el.classList.remove('offline');
    }
  },
  
  InfiniteScroll: {
    mounted() {
      this.observer = new IntersectionObserver((entries) => {
        if (entries[0].isIntersecting) {
          // Send event to server - fully typed!
          this.pushEvent('load_more', { page: this.page++ });
        }
      });
      this.observer.observe(this.el);
      this.page = 1;
    },
    
    destroyed() {
      this.observer?.disconnect();
    }
  }
};

// Extend HookInstance for custom properties
declare module 'djust' {
  interface HookInstance {
    chart?: Chart;
    observer?: IntersectionObserver;
    page?: number;
  }
}
```

### Phoenix LiveView Compatible Hooks

You can also use the `DjustHooks` global (Phoenix LiveView style):

```typescript
window.DjustHooks = {
  MyHook: {
    mounted() {
      console.log('Element mounted:', this.el);
    }
  }
};
```

### Working with WebSocket Events

Listen for custom events pushed from the server:

```typescript
// Listen for push_event messages
window.addEventListener('djust:push_event', (event) => {
  // event.detail is typed as DjustPushEventDetail
  const { event: eventName, payload } = event.detail;
  
  if (eventName === 'notification') {
    showNotification(payload as { message: string });
  }
});

// Listen for errors
window.addEventListener('djust:error', (event) => {
  // event.detail is typed as DjustErrorEventDetail
  const { error, traceback, validation_details } = event.detail;
  console.error('Server error:', error);
});
```

### Stream Events

Handle real-time streaming updates:

```typescript
const streamContainer = document.querySelector('#chat-messages');

streamContainer?.addEventListener('stream:text', (event) => {
  // event.detail is typed as StreamEventDetail
  const { text, mode, stream } = event.detail;
  console.log(`Received text on stream "${stream}":`, text);
});

streamContainer?.addEventListener('stream:done', (event) => {
  console.log('Stream completed:', event.detail.stream);
});
```

### Accessing the LiveView Instance

```typescript
// Access the WebSocket connection
const liveView = window.djust.liveViewInstance;

if (liveView) {
  // Check connection stats
  console.log('Messages sent:', liveView.stats.sent);
  console.log('Messages received:', liveView.stats.received);
  
  // Check if view is mounted
  if (liveView.viewMounted) {
    // Send an event programmatically
    liveView.sendEvent('my_event', { key: 'value' });
  }
}

// Get active streams
const streams = window.djust.getActiveStreams();
console.log('Active streams:', streams);
```

### Navigation

```typescript
// Programmatic navigation (if navigation module is available)
window.djust.navigation?.livePatch('/items', { filter: 'active' });

// Navigate to different view
window.djust.navigation?.liveRedirect('/other-view', { id: 123 });
```

## Type Definitions Reference

### Main Interfaces

| Interface | Description |
|-----------|-------------|
| `DjustClient` | Main namespace on `window.djust` |
| `LiveViewWebSocket` | WebSocket connection manager |
| `HookDefinition` | User-defined hook (mounted, updated, etc.) |
| `HookInstance` | Runtime hook with `el`, `pushEvent`, `handleEvent` |

### Message Types

| Type | Description |
|------|-------------|
| `DjustPatchMessage` | DOM diff patches from server |
| `DjustStreamMessage` | Real-time streaming updates |
| `DjustPushEventMessage` | Server-pushed events for hooks |
| `DjustErrorMessage` | Server error with optional traceback |

### Event Types

| Event | Detail Type | Description |
|-------|-------------|-------------|
| `djust:error` | `DjustErrorEventDetail` | Server error occurred |
| `djust:push_event` | `DjustPushEventDetail` | Server pushed event |
| `stream:*` | `StreamEventDetail` | Stream operations |

## IDE Setup

### VS Code

For best experience in VS Code:

1. Install the [TypeScript](https://marketplace.visualstudio.com/items?itemName=ms-vscode.vscode-typescript-next) extension
2. Ensure `tsconfig.json` is in your project root
3. Types should be picked up automatically

### PyCharm / WebStorm

1. Go to **Preferences** → **Languages & Frameworks** → **TypeScript**
2. Enable TypeScript language service
3. Point to your `tsconfig.json`

## Troubleshooting

### Types not recognized

Make sure your `tsconfig.json` includes the path to djust types:

```json
{
  "compilerOptions": {
    "typeRoots": ["./node_modules/@types", "./path/to/djust/types"]
  }
}
```

### Strict null checks

The types use strict null checking. If you get errors about possibly null values:

```typescript
// Instead of this (error)
const el = document.querySelector('#my-el');
el.classList.add('active');

// Do this
const el = document.querySelector('#my-el');
el?.classList.add('active');
// or
if (el) {
  el.classList.add('active');
}
```

### Extending types

To add custom properties to hooks:

```typescript
declare global {
  interface HookInstance {
    myCustomProperty?: string;
    myChart?: Chart;
  }
}
```
