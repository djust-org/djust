/**
 * Tests for Django Rust Live client-side runtime
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { readFileSync } from 'fs';
import { join } from 'path';

// Mock WebSocket before loading client code
global.WebSocket = class WebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;

  constructor(url) {
    this.url = url;
    this.readyState = WebSocket.CONNECTING;
  }

  send(data) {}
  close() {}
};

// Load the client.js file
const clientCode = readFileSync(
  join(process.cwd(), 'python/djust/static/djust/client.js'),
  'utf-8'
);

// Execute the client code in the test environment
eval(clientCode);

describe('DjangoRustLive', () => {
  let client;

  beforeEach(() => {
    // Reset the DOM
    document.body.innerHTML = '';

    // Create a fresh instance
    client = new window.DjangoRustLive.constructor();
  });

  describe('Constructor', () => {
    it('should initialize with default values', () => {
      expect(client.ws).toBeNull();
      expect(client.sessionId).toBeNull();
      expect(client.reconnectAttempts).toBe(0);
      expect(client.maxReconnectAttempts).toBe(5);
      expect(client.reconnectDelay).toBe(1000);
      expect(client.eventHandlers).toBeInstanceOf(Map);
    });
  });

  describe('getElementByPath', () => {
    beforeEach(() => {
      document.body.innerHTML = `
        <div id="root">
          <div class="level1">
            <span>Text 1</span>
            <span>Text 2</span>
          </div>
          <div class="level1-2">
            <p>Paragraph</p>
          </div>
        </div>
      `;
    });

    it('should return body for empty path', () => {
      const element = client.getElementByPath([]);
      expect(element).toBe(document.body);
    });

    it('should find element by path', () => {
      const element = client.getElementByPath([0, 0, 1]);
      expect(element.textContent).toBe('Text 2');
      expect(element.tagName).toBe('SPAN');
    });

    it('should return null for invalid path', () => {
      const element = client.getElementByPath([0, 0, 10]);
      expect(element).toBeNull();
    });

    it('should navigate deep paths', () => {
      const element = client.getElementByPath([0, 1, 0]);
      expect(element.textContent).toBe('Paragraph');
      expect(element.tagName).toBe('P');
    });
  });

  describe('vnodeToElement', () => {
    it('should create text node', () => {
      const vnode = { tag: '#text', text: 'Hello World' };
      const element = client.vnodeToElement(vnode);

      expect(element.nodeType).toBe(Node.TEXT_NODE);
      expect(element.textContent).toBe('Hello World');
    });

    it('should create element with tag', () => {
      const vnode = { tag: 'div', attrs: {}, children: [] };
      const element = client.vnodeToElement(vnode);

      expect(element.tagName).toBe('DIV');
    });

    it('should create element with attributes', () => {
      const vnode = {
        tag: 'button',
        attrs: { id: 'test-btn', class: 'btn primary' },
        children: []
      };
      const element = client.vnodeToElement(vnode);

      expect(element.id).toBe('test-btn');
      expect(element.className).toBe('btn primary');
    });

    it('should create element with children', () => {
      const vnode = {
        tag: 'div',
        attrs: {},
        children: [
          { tag: '#text', text: 'Text content' },
          { tag: 'span', attrs: {}, children: [] }
        ]
      };
      const element = client.vnodeToElement(vnode);

      expect(element.childNodes.length).toBe(2);
      expect(element.childNodes[0].textContent).toBe('Text content');
      expect(element.childNodes[1].tagName).toBe('SPAN');
    });

    it('should handle nested children', () => {
      const vnode = {
        tag: 'div',
        attrs: {},
        children: [
          {
            tag: 'ul',
            attrs: {},
            children: [
              { tag: 'li', attrs: {}, children: [{ tag: '#text', text: 'Item 1' }] },
              { tag: 'li', attrs: {}, children: [{ tag: '#text', text: 'Item 2' }] }
            ]
          }
        ]
      };
      const element = client.vnodeToElement(vnode);

      expect(element.querySelector('ul')).toBeTruthy();
      expect(element.querySelectorAll('li').length).toBe(2);
      expect(element.querySelectorAll('li')[0].textContent).toBe('Item 1');
    });
  });

  describe('Patch Operations', () => {
    beforeEach(() => {
      document.body.innerHTML = `
        <div id="container">
          <span class="item">Original</span>
          <button id="btn">Click</button>
        </div>
      `;
    });

    describe('patchSetText', () => {
      it('should update element text content', () => {
        const element = document.querySelector('.item');
        client.patchSetText(element, 'Updated Text');

        expect(element.textContent).toBe('Updated Text');
      });
    });

    describe('patchSetAttr', () => {
      it('should set attribute on element', () => {
        const element = document.getElementById('btn');
        client.patchSetAttr(element, 'disabled', 'true');

        expect(element.getAttribute('disabled')).toBe('true');
      });

      it('should update existing attribute', () => {
        const element = document.getElementById('btn');
        client.patchSetAttr(element, 'id', 'new-btn');

        expect(element.id).toBe('new-btn');
      });
    });

    describe('patchRemoveAttr', () => {
      it('should remove attribute from element', () => {
        const element = document.querySelector('.item');
        client.patchRemoveAttr(element, 'class');

        expect(element.hasAttribute('class')).toBe(false);
      });
    });

    describe('patchInsertChild', () => {
      it('should append child at end', () => {
        const container = document.getElementById('container');
        const vnode = { tag: 'p', attrs: {}, children: [{ tag: '#text', text: 'New' }] };

        client.patchInsertChild(container, 10, vnode);

        expect(container.children.length).toBe(3);
        expect(container.children[2].textContent).toBe('New');
      });

      it('should insert child at specific index', () => {
        const container = document.getElementById('container');
        const vnode = { tag: 'div', attrs: { class: 'inserted' }, children: [] };

        client.patchInsertChild(container, 1, vnode);

        expect(container.children[1].className).toBe('inserted');
      });
    });

    describe('patchRemoveChild', () => {
      it('should remove child at index', () => {
        const container = document.getElementById('container');
        const initialCount = container.children.length;

        client.patchRemoveChild(container, 0);

        expect(container.children.length).toBe(initialCount - 1);
        expect(container.querySelector('.item')).toBeNull();
      });

      it('should handle invalid index gracefully', () => {
        const container = document.getElementById('container');
        const initialCount = container.children.length;

        client.patchRemoveChild(container, 10);

        expect(container.children.length).toBe(initialCount);
      });
    });

    describe('patchMoveChild', () => {
      beforeEach(() => {
        document.body.innerHTML = `
          <div id="container">
            <div class="item-1">1</div>
            <div class="item-2">2</div>
            <div class="item-3">3</div>
          </div>
        `;
      });

      it('should move child from one position to another', () => {
        const container = document.getElementById('container');

        client.patchMoveChild(container, 0, 2);

        // Moving element at index 0 to index 2:
        // Before: [item-1, item-2, item-3]
        // After insertBefore at index 2: [item-2, item-1, item-3]
        expect(container.children[0].className).toBe('item-2');
        expect(container.children[1].className).toBe('item-1');
        expect(container.children[2].className).toBe('item-3');
      });

      it('should move to end if index too large', () => {
        const container = document.getElementById('container');

        client.patchMoveChild(container, 0, 10);

        expect(container.children[2].className).toBe('item-1');
      });
    });

    describe('patchReplace', () => {
      it('should replace element with new element', () => {
        const element = document.querySelector('.item');
        const vnode = {
          tag: 'p',
          attrs: { class: 'replacement' },
          children: [{ tag: '#text', text: 'Replaced' }]
        };

        client.patchReplace(element, vnode);

        expect(document.querySelector('.item')).toBeNull();
        expect(document.querySelector('.replacement')).toBeTruthy();
        expect(document.querySelector('.replacement').textContent).toBe('Replaced');
      });
    });
  });

  describe('sendEvent', () => {
    it('should not send if websocket not connected', () => {
      const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

      client.sendEvent('test_event', { key: 'value' });

      expect(consoleSpy).toHaveBeenCalledWith('[LiveView] WebSocket not connected');
      consoleSpy.mockRestore();
    });

    it('should send event with correct format', () => {
      // Mock WebSocket
      client.ws = {
        readyState: WebSocket.OPEN,
        send: vi.fn()
      };

      client.sendEvent('increment', { amount: 5 });

      expect(client.ws.send).toHaveBeenCalledWith(
        JSON.stringify({
          type: 'event',
          event: 'increment',
          params: { amount: 5 }
        })
      );
    });

    it('should send event without params', () => {
      client.ws = {
        readyState: WebSocket.OPEN,
        send: vi.fn()
      };

      client.sendEvent('reset');

      expect(client.ws.send).toHaveBeenCalledWith(
        JSON.stringify({
          type: 'event',
          event: 'reset',
          params: {}
        })
      );
    });
  });

  describe('applyPatches', () => {
    beforeEach(() => {
      document.body.innerHTML = `
        <div id="root">
          <span>Test</span>
        </div>
      `;

      // Mock bindEvents to avoid WebSocket errors
      client.bindEvents = vi.fn();
    });

    it('should handle empty patches', () => {
      const consoleSpy = vi.spyOn(console, 'log').mockImplementation(() => {});

      client.applyPatches([]);

      expect(consoleSpy).not.toHaveBeenCalled();
      consoleSpy.mockRestore();
    });

    it('should apply SetText patch', () => {
      const patches = [
        { type: 'SetText', path: [0, 0], text: 'New Text' }
      ];

      client.applyPatches(patches);

      expect(document.querySelector('span').textContent).toBe('New Text');
    });

    it('should apply SetAttr patch', () => {
      const patches = [
        { type: 'SetAttr', path: [0, 0], key: 'class', value: 'active' }
      ];

      client.applyPatches(patches);

      expect(document.querySelector('span').className).toBe('active');
    });

    it('should call bindEvents after patching', () => {
      const patches = [
        { type: 'SetText', path: [0, 0], text: 'Updated' }
      ];

      client.applyPatches(patches);

      expect(client.bindEvents).toHaveBeenCalled();
    });

    it('should warn for invalid path', () => {
      const consoleSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});

      const patches = [
        { type: 'SetText', path: [10, 20], text: 'Test' }
      ];

      client.applyPatches(patches);

      expect(consoleSpy).toHaveBeenCalledWith(
        '[LiveView] Element not found for path:',
        [10, 20]
      );
      consoleSpy.mockRestore();
    });
  });

  describe('Event Binding', () => {
    beforeEach(() => {
      // Mock WebSocket
      client.ws = {
        readyState: WebSocket.OPEN,
        send: vi.fn()
      };
    });

    it('should bind @click events', () => {
      const button = document.createElement('button');
      button.setAttribute('@click', 'handleClick');
      button.setAttribute('data-id', '123');
      button.textContent = 'Click Me';
      document.body.appendChild(button);

      // Mock querySelectorAll to return elements with @ attributes
      const originalQuerySelectorAll = document.querySelectorAll;
      document.querySelectorAll = vi.fn(() => [button]);

      client.bindEvents();

      // Restore original
      document.querySelectorAll = originalQuerySelectorAll;

      button.click();

      // Note: "123" is parsed as number 123 by JSON.parse
      expect(client.ws.send).toHaveBeenCalledWith(
        JSON.stringify({
          type: 'event',
          event: 'handleClick',
          params: { id: 123 }
        })
      );
    });

    it('should bind @input events', () => {
      const input = document.createElement('input');
      input.setAttribute('@input', 'onInput');
      input.type = 'text';
      document.body.appendChild(input);

      // Mock querySelectorAll
      const originalQuerySelectorAll = document.querySelectorAll;
      document.querySelectorAll = vi.fn(() => [input]);

      client.bindEvents();

      document.querySelectorAll = originalQuerySelectorAll;

      input.value = 'test input';
      input.dispatchEvent(new Event('input'));

      expect(client.ws.send).toHaveBeenCalledWith(
        JSON.stringify({
          type: 'event',
          event: 'onInput',
          params: { value: 'test input' }
        })
      );
    });

    it('should bind @change events', () => {
      const select = document.createElement('select');
      select.setAttribute('@change', 'onChange');
      select.innerHTML = `
        <option value="1">Option 1</option>
        <option value="2">Option 2</option>
      `;
      document.body.appendChild(select);

      // Mock querySelectorAll
      const originalQuerySelectorAll = document.querySelectorAll;
      document.querySelectorAll = vi.fn(() => [select]);

      client.bindEvents();

      document.querySelectorAll = originalQuerySelectorAll;

      select.value = '2';
      select.dispatchEvent(new Event('change'));

      expect(client.ws.send).toHaveBeenCalledWith(
        JSON.stringify({
          type: 'event',
          event: 'onChange',
          params: { value: '2' }
        })
      );
    });

    it('should bind @submit events', () => {
      const form = document.createElement('form');
      form.setAttribute('@submit', 'onSubmit');
      form.innerHTML = `
        <input name="email" value="test@example.com" />
        <input name="password" value="secret123" />
      `;
      document.body.appendChild(form);

      // Mock querySelectorAll
      const originalQuerySelectorAll = document.querySelectorAll;
      document.querySelectorAll = vi.fn(() => [form]);

      client.bindEvents();

      document.querySelectorAll = originalQuerySelectorAll;

      form.dispatchEvent(new Event('submit'));

      expect(client.ws.send).toHaveBeenCalledWith(
        JSON.stringify({
          type: 'event',
          event: 'onSubmit',
          params: { email: 'test@example.com', password: 'secret123' }
        })
      );
    });

    it('should parse JSON in data attributes', () => {
      const button = document.createElement('button');
      button.setAttribute('@click', 'handler');
      button.setAttribute('data-config', '{"enabled":true,"count":5}');
      button.textContent = 'Button';
      document.body.appendChild(button);

      // Mock querySelectorAll
      const originalQuerySelectorAll = document.querySelectorAll;
      document.querySelectorAll = vi.fn(() => [button]);

      client.bindEvents();

      document.querySelectorAll = originalQuerySelectorAll;

      button.click();

      expect(client.ws.send).toHaveBeenCalledWith(
        JSON.stringify({
          type: 'event',
          event: 'handler',
          params: { config: { enabled: true, count: 5 } }
        })
      );
    });
  });
});
