// SPDX-License-Identifier: GPL-3.0-or-later
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const source = name => readFileSync(join(__dirname,'../extension',name),'utf8');
const flush = () => new Promise(resolve=>setImmediate(resolve));

test('resolver rejects other URLs, owns only its tab, and closes once', async () => {
    const messages = [], removed = [], created = [], listeners = [];
    const chrome = {
        runtime:{onMessage:{addListener(fn){listeners.push(fn);}}},
        permissions:{contains:async()=>false},
        windows:{create:async options=>{created.push(options);return {id:7,tabs:[{id:9}]};},update:async()=>{},remove:async id=>{removed.push(id);},onRemoved:{addListener(){}}},
    };
    const context = vm.createContext({chrome,console,URL,AbortController,setTimeout,clearTimeout});
    vm.runInContext(source('background.js'),context);
    vm.runInContext('bridgeSocket = {readyState:1,send: value => output.push(JSON.parse(value))}',Object.assign(context,{output:messages}));
    await vm.runInContext('bridgeHandleResolve({id:"bad",url:"https://evil.test/dl/1/?x",host:"mega"})',context);
    assert.equal(created.length,0);
    await vm.runInContext('bridgeHandleResolve({id:"one",url:"https://f95zone.to/dl/1/?u=fake",host:"mega"})',context);
    assert.equal(created.length,1);
    let reply;
    listeners[0]({type:'dl-owned?'},{tab:{id:8}},value=>reply=value);
    assert.equal(reply.owned,false);
    listeners[0]({type:'dl-owned?'},{tab:{id:9}},value=>reply=value);
    assert.equal(reply.owned,true);
    listeners[0]({type:'dl-result',ok:true,url:'https://mega.nz/file/a#key'},{tab:{id:9}},()=>{});
    listeners[0]({type:'dl-result',ok:true,url:'https://mega.nz/file/a#key'},{tab:{id:9}},()=>{});
    assert.equal(messages.filter(m=>m.id==='one' && m.type==='resolve_result').length,1);
    assert.deepEqual(removed,[7]);
    assert.equal(messages.at(-1).url,'https://mega.nz/file/a#key');
});

test('content script acts only on owned tabs and reports valid handoff', async () => {
    async function run(owned, handoff) {
        const sent = []; let clicks = 0, observer;
        const button = {disabled:false,click(){clicks++;}};
        const document = {readyState:'complete',querySelector(selector){
            if (selector.startsWith('[data-xf-init')) return handoff ? {getAttribute:()=>handoff} : null;
            if (selector === '.js-f95dlContinue') return button;
            return null;
        }};
        class Observer {constructor(cb){observer=cb;} observe(){}}
        const context = {document,location:{href:'https://f95zone.to/dl/1/?x'},URL,MutationObserver:Observer,
            setTimeout:()=>0,setInterval:()=>0,clearInterval(){},chrome:{runtime:{sendMessage:async message=>{sent.push(message);return message.type==='dl-owned?' ? {owned,host:'mega'} : {};}}}};
        vm.runInNewContext(source('dl-resolve.js'),context);
        await flush(); await flush();
        if (observer) observer();
        return {sent,clicks};
    }
    assert.equal((await run(false,'')).clicks,0);
    assert.equal((await run(true,'')).clicks,1);
    const result = await run(true,'https://mega.nz/file/id#key');
    assert.equal(result.sent.find(m=>m.type==='dl-result').url,'https://mega.nz/file/id#key');
    assert.equal((await run(true,'https://f95zone.to/threads/1/')).sent.find(m=>m.type==='dl-result').ok,false);
});

test('manifest keeps existing required permissions and registers resolver at document start', () => {
    const manifest = JSON.parse(source('manifest.json'));
    assert.deepEqual(manifest.host_permissions,['http://localhost:8183/*','https://f95zone.to/*']);
    for (const permission of ['tabs','cookies','downloads','webNavigation']) assert.ok(!manifest.permissions.includes(permission));
    assert.ok(manifest.content_scripts.some(script=>script.matches.includes('https://f95zone.to/dl/*') && script.run_at==='document_start'));
});

test('captcha requests user intervention in an owned tab', async () => {
    const sent = [];
    const document = {readyState:'complete',querySelector(selector){return selector.startsWith('iframe[') ? {} : null;}};
    class Observer {observe(){}}
    vm.runInNewContext(source('dl-resolve.js'),{document,URL,location:{href:'https://f95zone.to/dl/1/?x'},MutationObserver:Observer,setTimeout:()=>0,setInterval:()=>0,clearInterval(){},
        chrome:{runtime:{sendMessage:async message=>{sent.push(message);return {owned:true};}}}});
    await flush(); await flush();
    assert.ok(sent.some(message=>message.type==='dl-progress' && message.stage==='needs_user'));
});

test('Buzzheavier makes the page download request and reports its redirect', async () => {
    const sent = [], requests = [];
    const document = {querySelector:()=>({getAttribute:()=>'/abc/download?t=fake'})};
    class Observer {observe(){}}
    vm.runInNewContext(source('bzzhr-resolve.js'),{document,URL,location:{href:'https://buzzheavier.com/abc',origin:'https://buzzheavier.com'},MutationObserver:Observer,
        fetch:async (url,options)=>{requests.push({url,options});return {headers:{get:()=> 'https://files.buzzheavier.com/game.zip'}};},
        chrome:{runtime:{sendMessage:async message=>{sent.push(message);return {owned:true};}}}});
    await flush(); await flush();
    assert.equal(requests[0].options.headers['HX-Request'],'true');
    assert.equal(sent.find(m=>m.type==='dl-result').url,'https://files.buzzheavier.com/game.zip');
});

test('a third Continue press in the same tab goes to the user instead', async () => {
    const sent = []; let clicks = 0;
    const store = new Map([['wlibDlPresses','2']]);
    const sessionStorage = {getItem:key=>store.get(key) ?? null,setItem:(key,value)=>store.set(key,value)};
    const button = {disabled:false,click(){clicks++;}};
    const document = {readyState:'complete',querySelector:selector=>selector === '.js-f95dlContinue' ? button : null};
    class Observer {observe(){}}
    vm.runInNewContext(source('dl-resolve.js'),{document,sessionStorage,URL,location:{href:'https://f95zone.to/dl/1/?x'},MutationObserver:Observer,setTimeout:()=>0,setInterval:()=>0,clearInterval(){},
        chrome:{runtime:{sendMessage:async message=>{sent.push(message);return {owned:true};}}}});
    await flush(); await flush();
    assert.equal(clicks,0);
    assert.ok(sent.some(message=>message.type==='dl-progress' && message.stage==='needs_user'));
});


test('Continue waits for the page to load and for F95 to fill its check code', async () => {
    async function run(readyState, codeValue) {
        let clicks = 0;
        const button = {disabled:false,click(){clicks++;}};
        const page = {getAttribute:name=>name === 'data-code' ? 'cFAKE' : null};
        const code = {value:codeValue};
        const document = {readyState,querySelector(selector){
            if (selector === '.js-f95dlContinue') return button;
            if (selector.startsWith('[data-xf-init~="f95dl-page"]')) return page;
            if (selector === '.js-f95dlCode') return code;
            return null;
        }};
        class Observer {observe(){}}
        vm.runInNewContext(source('dl-resolve.js'),{document,URL,location:{href:'https://f95zone.to/dl/1/?x'},MutationObserver:Observer,
            setTimeout:()=>0,setInterval:()=>0,clearInterval(){},chrome:{runtime:{sendMessage:async()=>({owned:true})}}});
        await flush(); await flush();
        return clicks;
    }
    assert.equal(await run('interactive','filled'),0);
    assert.equal(await run('complete',''),0);
    assert.equal(await run('complete','filled'),1);
});

test('a tab that loaded the requested URL is claimed even if the window reported another tab', async () => {
    const messages = [], removedTabs = [], removedWindows = [], listeners = [];
    const chrome = {
        runtime:{onMessage:{addListener(fn){listeners.push(fn);}}},
        permissions:{contains:async()=>false},
        tabs:{remove:async id=>{removedTabs.push(id);},update:async()=>{},onRemoved:{addListener(){}}},
        windows:{create:async()=>({id:7,state:'minimized',tabs:[{id:9}]}),update:async()=>{},remove:async id=>{removedWindows.push(id);},onRemoved:{addListener(){}}},
    };
    const context = vm.createContext({chrome,console,URL,AbortController,setTimeout,clearTimeout});
    vm.runInContext(source('background.js'),context);
    vm.runInContext('bridgeSocket = {readyState:1,send: value => output.push(JSON.parse(value))}',Object.assign(context,{output:messages}));
    const url = 'https://f95zone.to/dl/1/?u=fake&k=a&r=1&t=b';
    await vm.runInContext(`bridgeHandleResolve({id:"zen",url:${JSON.stringify(url)},host:"pixeldrain"})`,context);
    let reply;
    listeners[0]({type:'dl-owned?',url:'https://f95zone.to/dl/1/?u=other'},{tab:{id:20,windowId:3}},value=>reply=value);
    assert.equal(reply.owned,false);
    listeners[0]({type:'dl-owned?',url},{tab:{id:21,windowId:3}},value=>reply=value);
    assert.equal(reply.owned,true);
    listeners[0]({type:'dl-owned?',url},{tab:{id:22,windowId:3}},value=>reply=value);
    assert.equal(reply.owned,false, 'a claimed request is not handed to a second tab');
    listeners[0]({type:'dl-result',ok:true,url:'https://pixeldrain.com/u/abc'},{tab:{id:21,windowId:3}},()=>{});
    assert.equal(messages.at(-1).url,'https://pixeldrain.com/u/abc');
    assert.deepEqual([...new Set(removedTabs)].sort(),[21,22], 'the working tab and the duplicate are both closed');
    assert.ok(removedTabs.indexOf(22) < removedTabs.indexOf(21), 'the duplicate is closed as soon as it shows up');
    assert.deepEqual(removedWindows,[7]);
    listeners[0]({type:'dl-owned?',url},{tab:{id:23,windowId:3}},value=>reply=value);
    assert.equal(reply.owned,false);
    assert.ok(removedTabs.includes(23), 'a copy that loads after the request ended is closed too');
});
