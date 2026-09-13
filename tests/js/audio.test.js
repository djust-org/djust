import { describe, it, expect, vi } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';
const code = fs.readFileSync('./python/djust/static/djust/audio.js', 'utf8');
const loader = fs.readFileSync('./python/djust/static/djust/src/51-audio-loader.js', 'utf8');
const scope = 'a'.repeat(32);
const cfg = (id = scope) => ({ version: 1, scope: id, origins: [], banks: { snake: { maxVoices: 2, sounds: { eat: {url:'/eat.wav', volume:0.5} } } } });
function env(config = cfg()) {
    const dom = new JSDOM('<div dj-root><div dj-audio><div data-audio-controls><button data-audio-toggle>Enable sound</button><input data-audio-volume><span data-audio-status></span></div></div></div>', {url:'http://localhost/', runScripts:'dangerously', pretendToBeVisual:true});
    const w = dom.window, doc = w.document;
    doc.querySelector('[dj-audio]').setAttribute('dj-audio', JSON.stringify(config));
    let click;
    const listen = doc.addEventListener.bind(doc);
    doc.addEventListener = (name, fn, ...rest) => { if (name === 'click') click = fn; return listen(name, fn, ...rest); };
    const sources = [], contexts = [];
    w.AudioContext = class {
        constructor() { this.state='running'; this.destination={}; contexts.push(this); }
        resume = vi.fn(async () => { this.state='running'; });
        close = vi.fn(async () => { this.state='closed'; });
        decodeAudioData = vi.fn(async () => ({length:100,numberOfChannels:1,duration:0.1}));
        createGain() { return {gain:{value:1},connect:vi.fn(),disconnect:vi.fn()}; }
        createBufferSource() { const s = {connect:vi.fn(),disconnect:vi.fn(),start:vi.fn(),stop:vi.fn()}; sources.push(s); return s; }
    };
    w.fetch = vi.fn(async () => { let read = false; return {ok:true, headers:{get:()=>null},body:{getReader:()=>({read:async()=>read ? {done:true} : (read=true,{value:new Uint8Array(4),done:false}),cancel:vi.fn()})}}; });
    w.eval(code);
    const tick = () => new Promise(resolve => setTimeout(resolve, 0));
    const enable = async () => { click({target:doc.querySelector('button'),isTrusted:true}); await tick(); };
    const send = (events, overrides={}) => w.dispatchEvent(new w.CustomEvent('djust:push_event', {detail:{event:'djust:audio',payload:{version:1,op:'play',scope,bank:'snake',events,...overrides}}}));
    return {w,doc,dom,enable,send,tick,sources,contexts,click: () => click};
}
const cue = id => ({id,sound:'eat'});
describe('framework audio', () => {
    it('does not create a context or fetch assets before opt in; consumes disabled cues', async () => {
        const e=env(); e.send([cue('old')]); expect(e.contexts).toHaveLength(0); expect(e.w.fetch).not.toHaveBeenCalled();
        await e.enable(); e.send([cue('old'),cue('new')]); expect(e.sources).toHaveLength(1);
        expect(e.sources[0].start).toHaveBeenCalledOnce(); e.dom.window.close();
    });
    it('rejects synthetic activation, unknown scope and unknown sounds', async () => {
        const e=env(); e.doc.querySelector('button').click(); await e.tick(); expect(e.contexts).toHaveLength(0);
        await e.enable(); e.send([cue('wrong')],{scope:'b'.repeat(32)}); e.send([{id:'x',sound:'oops'}]);
        expect(e.sources).toHaveLength(0); e.dom.window.close();
    });
    it('deduplicates and bounds concurrent voices and oversized batches', async () => {
        const e=env(); await e.enable(); e.send([cue('1'),cue('1'),cue('2'),cue('3')]); expect(e.sources).toHaveLength(2);
        e.sources[0].onended(); e.send([cue('3')]); expect(e.sources).toHaveLength(2);
        e.send(Array.from({length:33},(_,i)=>cue(String(i+10)))); expect(e.sources).toHaveLength(2);
        e.send([cue('4')]); expect(e.sources).toHaveLength(3); e.dom.window.close();
    });
    it('mute stops voices and does not replay cues on unmute', async () => {
        const e=env(); await e.enable(); e.send([cue('1')]); await e.enable();
        expect(e.sources[0].stop).toHaveBeenCalled(); e.send([cue('2')]); await e.enable(); e.send([cue('2')]);
        expect(e.sources).toHaveLength(1); e.dom.window.close();
    });
    it('hidden tabs stop voices and consume new cues', async () => {
        const e=env(); await e.enable(); e.send([cue('1')]);
        Object.defineProperty(e.doc,'hidden',{configurable:true,value:true}); e.doc.dispatchEvent(new e.w.Event('visibilitychange'));
        e.send([cue('2')]); expect(e.sources[0].stop).toHaveBeenCalled();
        Object.defineProperty(e.doc,'hidden',{configurable:true,value:false}); e.send([cue('2')]); expect(e.sources).toHaveLength(1); e.dom.window.close();
    });
    it('preserves mute and volume through control replacement, closes on root removal', async () => {
        const e=env(); await e.enable(); const input=e.doc.querySelector('input'); input.value='0.2'; input.dispatchEvent(new e.w.Event('input',{bubbles:true}));
        await e.enable(); const marker=e.doc.querySelector('[dj-audio]'); marker.innerHTML=marker.innerHTML; e.w.djustAudio.sync();
        expect(e.doc.querySelector('button').textContent).toBe('Unmute sound'); expect(e.doc.querySelector('input').value).toBe('0.2');
        e.doc.querySelector('[dj-root]').remove(); e.w.djustAudio.sync(); expect(e.contexts[0].close).toHaveBeenCalledOnce(); e.dom.window.close();
    });
    it('fails closed for foreign URLs and rejects download errors without enabling', async () => {
        const c=cfg(); c.banks.snake.sounds.eat.url='https://other.example/eat.wav'; const e=env(c); await e.enable(); expect(e.w.fetch).not.toHaveBeenCalled(); e.dom.window.close();
        const f=env(); f.w.fetch.mockRejectedValue(Error('offline')); await f.enable(); expect(f.doc.querySelector('[data-audio-status]').textContent).toContain('unavailable'); f.send([cue('1')]); expect(f.sources).toHaveLength(0); f.dom.window.close();
    });
    it('reports blocked resume and drops events for a previous scope', async () => {
        const e=env(); e.w.AudioContext.prototype.resume = undefined;
        e.w.AudioContext = class { state='suspended'; resume=()=>Promise.reject(Error('blocked')); close=async()=>{}; };
        await e.enable(); expect(e.doc.querySelector('button').textContent).toBe('Retry sound'); e.dom.window.close();
        const f=env(); await f.enable(); f.contexts[0].decodeAudioData.mockResolvedValue({length:100,numberOfChannels:1,duration:11});
        f.doc.querySelector('[dj-audio]').setAttribute('dj-audio', JSON.stringify(cfg('b'.repeat(32)))); f.w.djustAudio.sync();
        // A new scope owns a new player; old events cannot reach it.
        f.send([cue('x')]); expect(f.sources).toHaveLength(0); f.dom.window.close();
    });
    it('does not load optional audio on pages without a manifest', async () => {
        const dom=new JSDOM('<body><div dj-root></div></body>',{url:'http://localhost',runScripts:'dangerously'});
        dom.window.eval(loader);
        await new Promise(resolve => setTimeout(resolve, 0));
        expect(dom.window.document.querySelector('script')).toBeNull();
        dom.window.dispatchEvent(new dom.window.Event('pagehide')); dom.window.close();
    });
});

describe('audio resource and root boundaries', () => {
    it('rejects an oversized decoded asset before enabling', async () => {
        const e=env(); const Base=e.w.AudioContext;
        e.w.AudioContext=class extends Base { constructor(){ super(); this.decodeAudioData.mockResolvedValue({length:100,numberOfChannels:1,duration:11}); } };
        await e.enable(); expect(e.doc.querySelector('[data-audio-status]').textContent).toContain('unavailable');
        e.send([cue('x')]); expect(e.sources).toHaveLength(0); e.dom.window.close();
    });
    it('rejects oversized downloads and aborts pending fetches on teardown', async () => {
        const e=env(); const cancel=vi.fn();
        e.w.fetch.mockResolvedValue({ok:true,headers:{get:()=>null},body:{getReader:()=>({read:async()=>({done:false,value:new Uint8Array(1024*1024+1)}),cancel})}});
        await e.enable(); expect(cancel).toHaveBeenCalled(); expect(e.sources).toHaveLength(0); e.dom.window.close();
        const f=env(); let signal;
        f.w.fetch.mockImplementation((_url, options)=>new Promise((_resolve,reject)=>{signal=options.signal; signal.addEventListener('abort',()=>reject(Error('abort')));}));
        f.click()({target:f.doc.querySelector('button'),isTrusted:true}); await f.tick();
        f.doc.querySelector('[dj-root]').remove(); f.w.djustAudio.sync(); expect(signal.aborted).toBe(true); await f.tick(); f.dom.window.close();
    });
    it('routes identical bank names to their own nested root', async () => {
        const e=env(); const nested=e.doc.querySelector('[dj-root]').cloneNode(true);
        nested.querySelector('[dj-audio]').setAttribute('dj-audio',JSON.stringify(cfg('b'.repeat(32))));
        e.doc.querySelector('[dj-root]').appendChild(nested); e.w.djustAudio.sync();
        await e.enable(); e.send([cue('child')],{scope:'b'.repeat(32)}); expect(e.sources).toHaveLength(0);
        e.click()({target:nested.querySelector('button'),isTrusted:true}); await e.tick();
        e.send([cue('new-child')],{scope:'b'.repeat(32)}); expect(e.sources).toHaveLength(1);
        e.send([cue('parent')]); expect(e.sources).toHaveLength(2); expect(e.contexts).toHaveLength(1); e.dom.window.close();
    });
});
