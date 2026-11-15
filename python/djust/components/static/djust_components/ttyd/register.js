/**
 * Registers the TtydTerminal hook (dj-hook="TtydTerminal").
 *
 * Loaded by ``djust_components/ttyd_terminal.html`` as
 * ``<script type="module" src="...">`` from djust's own static files, so a
 * strict Content-Security-Policy (``script-src 'self'``, no 'unsafe-inline')
 * runs it without a nonce (#4139). It used to be an inline module script. It
 * takes no parameters: the hook reads its own from the element's data
 * attributes, and the import is relative to this file.
 */
import { TtydTerminalHook } from "./ttyd_terminal.js";

window.djust = window.djust || {};
window.djust.hooks = window.djust.hooks || {};
window.djust.hooks.TtydTerminal = TtydTerminalHook;
