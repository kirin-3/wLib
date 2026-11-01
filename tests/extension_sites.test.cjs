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
    assert.equal(manifest.optional_host_permissions.length,3);
    for (const origin of manifest.optional_host_permissions) {
        assert.ok(!manifest.host_permissions.includes(origin));
        assert.ok(!manifest.content_scripts.some(script=>script.matches.includes(origin)));
    }
    assert.deepEqual(manifest.content_scripts[0].js,['store-links.js','widget-placement.js','content.js']);
    assert.ok(manifest.web_accessible_resources[0].resources.includes('arrow-right.svg'));
});
