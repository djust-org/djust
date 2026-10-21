/**
 * combobox / rich_select as form fields: the form submits the chosen option's
 * value, not the visible text.
 *
 * The visible combobox input used to carry the field's name, so a form
 * submitted whatever was typed. It now has no name; a hidden `.combobox-value`
 * input carries the selected value, which the client bundle updates on a pick
 * (src/53-component-behaviors.js). Typed text that matches no option is
 * discarded on change unless the combobox allows free text. The markup is
 * what the tags render.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function combobox({ value = '', shown = '', freeText = false } = {}) {
    return `
<form id="f"><div class="form-group">
  <div class="combobox" id="id_run-city-combobox"${freeText ? ' data-allow-free-text' : ''}>
    <input type="hidden" class="combobox-value" id="id_run-city-value" name="run-city" value="${value}">
    <input class="combobox-input form-input" type="text" id="id_run-city" placeholder="Search" value="${shown}"
           role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="id_run-city-listbox" autocomplete="off">
    <div class="combobox-dropdown" id="id_run-city-listbox" role="listbox">
      <div class="combobox-option" role="option" aria-selected="false" data-value="a">Alpha</div>
      <div class="combobox-option" role="option" aria-selected="false" data-value="b">Beta</div>
    </div>
  </div>
</div></form>`;
}

const RICH = `
<form id="f"><div class="rich-select" id="id_city">
  <input type="hidden" name="city" value="">
  <div class="rich-select-trigger" tabindex="0" role="combobox" aria-controls="id_city-listbox" aria-expanded="false"><span class="rich-select-placeholder">Select...</span><span class="rich-select-chevron" aria-hidden="true">&#9662;</span></div>
  <div class="rich-select-dropdown" role="listbox" id="id_city-listbox">
    <div class="rich-select-option" data-value="a" role="option" aria-selected="false"><span class="rich-select-option-text"><span class="rich-select-option-label">Alpha</span></span></div>
    <div class="rich-select-option" data-value="b" role="option" aria-selected="false"><span class="rich-select-option-text"><span class="rich-select-option-label">Beta</span></span></div>
  </div>
</div></form>`;

function createDom(bodyHtml) {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><head></head><body><div dj-root dj-view="app.View">${bodyHtml}</div></body></html>`,
        { runScripts: 'dangerously', url: 'http://localhost/', pretendToBeVisual: true }
    );
    const win = dom.window;
    win.WebSocket = class { constructor() { this.readyState = 1; } send() {} close() {} };
    win.console = { log() {}, error() {}, warn() {}, debug() {}, info() {} };
    try { win.eval(clientCode); } catch (_) { /* ignore missing DOM APIs */ }
    win.document.dispatchEvent(new win.Event('DOMContentLoaded'));
    return dom;
}

function click(dom, el) {
    el.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true, cancelable: true }));
}

function type(dom, input, text) {
    input.focus();
    input.value = text;
    input.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
    input.dispatchEvent(new dom.window.Event('change', { bubbles: true }));
}

function submitted(dom) {
    return Array.from(new dom.window.FormData(dom.window.document.getElementById('f')).entries());
}

describe('combobox submits the selected value', () => {
    it('a pick puts the option value in the hidden input and its label in the text box', () => {
        const dom = createDom(combobox());
        const doc = dom.window.document;
        click(dom, doc.querySelector('[data-value="b"]'));
        expect(doc.querySelector('.combobox-value').value).toBe('b');
        expect(doc.querySelector('.combobox-input').value).toBe('Beta');
        expect(doc.querySelector('[data-value="b"]').getAttribute('aria-selected')).toBe('true');
        expect(doc.querySelector('[data-value="a"]').getAttribute('aria-selected')).toBe('false');
        expect(submitted(dom)).toEqual([['run-city', 'b']]);
    });

    it('free text that matches no option is discarded and the previous choice returns', () => {
        const dom = createDom(combobox({ value: 'a', shown: 'Alpha' }));
        const doc = dom.window.document;
        type(dom, doc.querySelector('.combobox-input'), 'Gamma');
        expect(doc.querySelector('.combobox-input').value).toBe('Alpha');
        expect(submitted(dom)).toEqual([['run-city', 'a']]);
    });

    it('typing an option label exactly picks that option', () => {
        const dom = createDom(combobox());
        const doc = dom.window.document;
        type(dom, doc.querySelector('.combobox-input'), 'beta');
        expect(submitted(dom)).toEqual([['run-city', 'b']]);
        expect(doc.querySelector('.combobox-input').value).toBe('Beta');
    });

    it('clearing the text clears the value', () => {
        const dom = createDom(combobox({ value: 'a', shown: 'Alpha' }));
        type(dom, dom.window.document.querySelector('.combobox-input'), '');
        expect(submitted(dom)).toEqual([['run-city', '']]);
    });

    it('with allow_free_text the typed text is the value', () => {
        const dom = createDom(combobox({ freeText: true }));
        type(dom, dom.window.document.querySelector('.combobox-input'), 'Gamma');
        expect(submitted(dom)).toEqual([['run-city', 'Gamma']]);
    });
});

describe('rich_select submits the selected value', () => {
    it('a pick updates the hidden input, the trigger and aria-selected', () => {
        const dom = createDom(RICH);
        const doc = dom.window.document;
        click(dom, doc.querySelector('.rich-select-trigger'));
        click(dom, doc.querySelector('[data-value="b"]'));
        expect(submitted(dom)).toEqual([['city', 'b']]);
        const trigger = doc.querySelector('.rich-select-trigger');
        expect(trigger.textContent).toContain('Beta');
        expect(trigger.querySelector('.rich-select-placeholder')).toBeNull();
        expect(trigger.querySelector('.rich-select-chevron')).not.toBeNull();
        expect(doc.querySelector('[data-value="b"]').getAttribute('aria-selected')).toBe('true');
        expect(doc.querySelector('.rich-select').classList.contains('rich-select--open')).toBe(false);
    });
});
