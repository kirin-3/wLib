// SPDX-License-Identifier: GPL-3.0-or-later
const {readFileSync} = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');

test('optional sites register, inject into existing tabs, and unregister on permission changes', async () => {
    const granted = new Set(), registered = new Map(), injections = [], listeners = {added:[],removed:[]};
    const contains = ({origins}, callback) => {const allowed = origins.every(origin=>granted.has(origin)); return callback ? callback(allowed) : Promise.resolve(allowed);};
    const context = vm.createContext({console,AbortController,setTimeout,clearTimeout,URL,chrome:{
        runtime:{onMessage:{addListener(){}},onStartup:{addListener(){}},onInstalled:{addListener(){}}},
        permissions:{contains,onAdded:{addListener(fn){listeners.added.push(fn);}},onRemoved:{addListener(fn){listeners.removed.push(fn);}}},
        tabs:{query:async()=>[{id:10}]},
        scripting:{getRegisteredContentScripts:async()=>[...registered.values()],registerContentScripts:async scripts=>scripts.forEach(script=>registered.set(script.id,script)),unregisterContentScripts:async({ids})=>ids.forEach(id=>registered.delete(id)),insertCSS:async()=>{},executeScript:async options=>injections.push(options)},
    }});
    vm.runInContext(readFileSync(path.join(__dirname,'../extension/background.js'),'utf8'),context);
    await vm.runInContext('siteRegistrationQueue',context);
    assert.equal(registered.size,0);
    granted.add('https://www.dlsite.com/*');
    listeners.added.forEach(fn=>fn());
    await vm.runInContext('siteRegistrationQueue',context);
    assert.deepEqual([...registered.keys()],['wlib-dlsite']);
    assert.equal(injections[0].files.at(-1),'sites/dlsite.js');
    assert.deepEqual(Array.from(injections[0].files),['store-links.js','widget-placement.js','site-widget.js','sites/dlsite.js']);
    registered.get('wlib-dlsite').js = ['store-links.js','site-widget.js','sites/dlsite.js'];
    listeners.added.forEach(fn=>fn());
    await vm.runInContext('siteRegistrationQueue',context);
    assert.deepEqual(Array.from(registered.get('wlib-dlsite').js),['store-links.js','widget-placement.js','site-widget.js','sites/dlsite.js']);
    const current = registered.get('wlib-dlsite');
    current.js = current.js.map(file=>`moz-extension://uuid/${file}`);
    listeners.added.forEach(fn=>fn());
    await vm.runInContext('siteRegistrationQueue',context);
    assert.equal(registered.get('wlib-dlsite'),current);
    assert.equal(registered.has('wlib-lewdcorner'),false);
    granted.add('https://lewdcorner.com/*');
    listeners.added.forEach(fn=>fn());
    await vm.runInContext('siteRegistrationQueue',context);
    assert.deepEqual(Array.from(registered.get('wlib-lewdcorner').matches),['https://lewdcorner.com/*']);
    assert.equal(registered.get('wlib-lewdcorner').js.at(-1),'sites/lewdcorner.js');
    for (const origin of ['https://bzzhr.to/*','https://www.bzzhr.to/*','https://buzzheavier.com/*','https://www.buzzheavier.com/*']) granted.add(origin);
    listeners.added.forEach(fn=>fn());
    await vm.runInContext('siteRegistrationQueue',context);
    assert.equal([...registered.keys()].filter(id=>id.startsWith('wlib-buzz-resolve-')).length,4);
    granted.clear(); listeners.removed.forEach(fn=>fn());
    await vm.runInContext('siteRegistrationQueue',context);
    assert.equal(registered.size,0);
});

test('optional site settings reset a declined request and revoke granted permission', () => {
    const granted = new Set(), inputs = [], callbacks = new Map(), status = {textContent:''};
    for (const site of ['dlsite','vndb','ryuugames']) inputs.push({dataset:{site},checked:false,disabled:false,addEventListener:(_,callback)=>callbacks.set(site,callback)});
    const permissions = {
        contains:({origins},callback)=>callback(granted.has(origins[0])),
        request:(_,callback)=>callback(false),
        remove:({origins},callback)=>{granted.delete(origins[0]);callback(true);},
    };
    vm.runInNewContext(readFileSync(path.join(__dirname,'../extension/options.js'),'utf8'), {document:{querySelectorAll:()=>inputs,getElementById:()=>status},chrome:{permissions,runtime:{}}});
    inputs[0].checked=true; callbacks.get('dlsite')();
    assert.equal(inputs[0].checked,false);
    assert.equal(inputs[0].disabled,false);
    granted.add('https://vndb.org/*'); inputs[1].checked=false; callbacks.get('vndb')();
    assert.equal(granted.size,0);
});

test('new sites remain optional and F95 scripts load the shared URL rules first', () => {
    const manifest = JSON.parse(readFileSync(path.join(__dirname,'../extension/manifest.json'),'utf8'));
    assert.ok(manifest.permissions.includes('scripting'));
    assert.equal(manifest.optional_host_permissions.length,8);
    for (const origin of manifest.optional_host_permissions) {
        assert.ok(!manifest.host_permissions.includes(origin));
        assert.ok(!manifest.content_scripts.some(script=>script.matches.includes(origin)));
    }
    assert.deepEqual(manifest.content_scripts[0].js,['store-links.js','widget-placement.js','content.js']);
    assert.ok(manifest.web_accessible_resources[0].resources.includes('arrow-right.svg'));
});

test('LewdCorner latest-updates badges sit on the card image, once, and follow page changes', async () => {
    const makeNode = (extra = {}) => {
        const node = {children:[], classList:{names:new Set(),add(name){this.names.add(name);}}, isConnected:true,
            append(child){child.parent = node; node.children.push(child);},
            remove(){if (node.parent) node.parent.children = node.parent.children.filter(child=>child!==node);},
            querySelector(selector){
                if (selector === '.lcCardMedia') return node.media || null;
                if (selector === '.wlib-library-badge') return [node, ...(node.media ? [node.media] : [])]
                    .flatMap(owner=>owner.children).find(child=>child.className === 'wlib-library-badge') || null;
                return null;
            }, ...extra};
        return node;
    };
    const tile = id => { const media = makeNode(); const card = makeNode({dataset:{cardId:id}, media}); card.append(media); return card; };
    let tiles = [tile('25455'), tile('25456'), tile('not-a-number')];
    let observerCallback = null; const calls = [];
    const timers = [];
    const context = vm.createContext({
        location:{pathname:'/latest-updates/'},
        setTimeout:fn=>timers.push(fn),
        MutationObserver:class { constructor(fn){observerCallback = fn;} observe(){} },
        document:{querySelectorAll:()=>tiles, createElement:()=>makeNode()},
        chrome:{runtime:{lastError:null, sendMessage(message, reply){
            calls.push(Array.from(message.threadIds));
            reply({success:true, matches:{'https://lewdcorner.com/threads/25455/':{}, 'https://lewdcorner.com/threads/7/':{}}});
        }}},
        globalThis:{},
    });
    vm.runInContext(readFileSync(path.join(__dirname,'../extension/sites/lewdcorner.js'),'utf8'),context);
    assert.deepEqual(calls,[['25455','25456']]);
    const badges = card => card.media.children.filter(child=>child.className === 'wlib-library-badge');
    assert.equal(badges(tiles[0]).length,1);
    assert.equal(tiles[0].children.filter(child=>child.className === 'wlib-library-badge').length,0);
    assert.equal(tiles[0].classList.names.has('wlib-badge-host'),false);
    assert.equal(badges(tiles[1]).length,0);
    // A mutation with no new cards checks nothing again and adds no second badge.
    observerCallback(); timers.shift()();
    assert.equal(calls.length,1);
    assert.equal(badges(tiles[0]).length,1);
    // The page swaps in the next page's cards: one batched check for them.
    tiles = [tile('7'), tile('8')];
    observerCallback(); timers.shift()();
    assert.deepEqual(calls[1],['7','8']);
    assert.equal(badges(tiles[0]).length,1);
});
