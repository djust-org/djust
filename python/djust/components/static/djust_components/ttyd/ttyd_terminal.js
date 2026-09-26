// ttyd_terminal.js — djust hook for xterm.js + ttyd WebSocket
// xterm is djust's vendored build (ADR-040): the template renders a
// <link rel="modulepreload" integrity> for it and passes its URL here.
//
// ttyd binary protocol:
//   Client→server: [0x00, ...stdin_bytes] | [0x01, ...resize_json_bytes]
//   Server→client: [0x00, ...stdout_bytes] | [0x01, title] | [0x02, prefs]
//
// ttyd must be run with --check-origin=false (or same origin) to allow
// WebSocket connections.

export const TtydTerminalHook = {
  async mounted() {
    const el = this.el;
    const url  = el.dataset.ttydUrl || "ws://localhost:7681";
    const rows = parseInt(el.dataset.rows || "24", 10);
    const cols = parseInt(el.dataset.cols || "80", 10);
    let theme  = {};
    try { theme = JSON.parse(el.dataset.theme || "{}"); } catch (_) {}

    const { Terminal, FitAddon } = await import(el.dataset.xtermSrc);

    this._term = new Terminal({ rows, cols, theme, convertEol: true });
    this._fit  = new FitAddon();
    this._term.loadAddon(this._fit);
    this._term.open(el);
    this._fit.fit();

    const encoder = new TextEncoder();

    this._ws = new WebSocket(url);
    this._ws.binaryType = "arraybuffer";

    this._ws.addEventListener("open", () => {
      this._sendResize(this._term.rows, this._term.cols);
      this.pushEvent("ttyd_connect", {
        timestamp: new Date().toISOString(),
        user_agent: navigator.userAgent,
      });
    });

    this._ws.addEventListener("message", ({ data }) => {
      const view = new Uint8Array(data);
      if (view[0] === 0) {       // 0x00 = stdout
        this._term.write(view.slice(1));
      }
      // 0x01 (title) and 0x02 (prefs) ignored in v1
    });

    this._ws.addEventListener("close", ({ code, reason }) => {
      this._term.write("\r\n\x1b[31m[Connection closed]\x1b[0m\r\n");
      this.pushEvent("ttyd_disconnect", {
        timestamp: new Date().toISOString(),
        code,
        reason: reason || "",
      });
    });

    this._ws.addEventListener("error", () => {
      this._term.write("\r\n\x1b[31m[Connection error]\x1b[0m\r\n");
    });

    this._term.onData((data) => {
      if (this._ws.readyState !== WebSocket.OPEN) return;
      const bytes = encoder.encode(data);
      const msg   = new Uint8Array(1 + bytes.length);
      msg[0] = 0;  // 0x00 = stdin
      msg.set(bytes, 1);
      this._ws.send(msg.buffer);
    });

    // Resize terminal when container resizes
    this._ro = new ResizeObserver(() => {
      this._fit.fit();
      this._sendResize(this._term.rows, this._term.cols);
    });
    this._ro.observe(el);

    // Store encoder for use in _sendResize
    this._encoder = encoder;
  },

  _sendResize(rows, cols) {
    if (!this._ws || this._ws.readyState !== WebSocket.OPEN) return;
    const encoder = this._encoder || new TextEncoder();
    const payload = encoder.encode(JSON.stringify({ columns: cols, rows }));
    const msg     = new Uint8Array(1 + payload.length);
    msg[0] = 1;   // 0x01 = resize
    msg.set(payload, 1);
    this._ws.send(msg.buffer);
  },

  destroyed() {
    this._ro?.disconnect();
    this._ws?.close();
    this._term?.dispose();
  },
};
