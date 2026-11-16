// Browser-grade oracle for the safe-markup guard's tests (#3864): parse each HTML string inside
// <body> with jsdom (parse5, the WHATWG tokenizer and tree builder) and report how a payload got
// out: an element or attribute the developer did not write, a script-scheme URL, code a browser
// runs (an event handler, a script body; a JSON data block is never run), or a CSS block opened.
// Reads a JSON array of strings on stdin, writes a JSON array of reason lists.
const { JSDOM } = require('jsdom');
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const URLA = new Set(['href', 'src', 'srcset', 'action', 'formaction', 'xlink:href', 'data', 'poster',
  'background', 'cite', 'codebase', 'ping', 'icon', 'manifest', 'longdesc', 'usemap', 'classid',
  'archive', 'profile', 'lowsrc', 'dynsrc']);
function runsOutside(code) {
  let q = '';
  for (let i = 0; i < code.length; i++) {
    const c = code[i];
    if (q) { if (c === '\\') { i++; continue; } if (c === q) q = ''; }
    else if ("'\"`".includes(c)) q = c;
    else if (code.startsWith('INJ(', i)) return true;
  }
  return false;
}
function scheme(v) {
  const s = v.replace(/[\t\n\r]/g, '').replace(/^[\x00-\x20]+/, '').toLowerCase();
  const m = s.match(/^([a-z][a-z0-9+.\-]*):/);
  return m ? m[1] : null;
}
const JS_TYPES = new Set(['', 'module', 'text/javascript', 'application/javascript',
  'application/ecmascript', 'application/x-ecmascript', 'application/x-javascript',
  'text/ecmascript', 'text/jscript', 'text/livescript', 'text/x-ecmascript', 'text/x-javascript',
  'text/javascript1.0', 'text/javascript1.1', 'text/javascript1.2', 'text/javascript1.3',
  'text/javascript1.4', 'text/javascript1.5']);
function runsJs(el) {
  // a script runs only for a JavaScript type (or none); any other type is a data block
  if (!el.hasAttribute('type')) return true;
  return JS_TYPES.has(el.getAttribute('type').trim().toLowerCase());
}
const SHARED = new JSDOM('<!doctype html><html><head></head><body></body></html>');
function check(html) {
  const reasons = [];
  const srcdocs = [];
  const doc = SHARED.window.document;
  doc.body.innerHTML = html;
  const walk = (root) => {
    for (const el of root.querySelectorAll('*')) {
      const name = el.localName;
      if (name === 'x') reasons.push('element x');
      for (const a of el.attributes) {
        const an = a.name.toLowerCase(), v = a.value;
        if (an === 'onmouseover') reasons.push('attr onmouseover=' + JSON.stringify(v));
        else if (an.startsWith('on') && runsOutside(v)) reasons.push('handler ' + an);
        if (URLA.has(an) || an.endsWith('href') || an.endsWith('-src') || an.endsWith('-url')) {
          const s = scheme(v);
          if (s === 'javascript' || s === 'vbscript' || (s === 'data' && /text\/html/i.test(v)))
            reasons.push('url ' + an + '=' + JSON.stringify(v));
        }
        if (an === 'srcdoc') srcdocs.push(v);
        if (an === 'style' && (v.includes('INJ{') || v.includes('INJ('))) reasons.push('style attr');
      }
      if (name === 'script' && runsJs(el) && runsOutside(el.textContent)) reasons.push('script code');
      if (name === 'style' && (el.textContent.includes('INJ{') || el.textContent.includes('INJ(')))
        reasons.push('style body');
      if (name === 'template' && el.content) walk(el.content);
    }
  };
  walk(doc.body);
  for (const v of srcdocs) reasons.push(...check(v).map((r) => 'srcdoc:' + r));
  return reasons;
}
const out = input.map((h) => { try { return check(h); } catch (e) { return ['ERR ' + e.message]; } });
process.stdout.write(JSON.stringify(out));
