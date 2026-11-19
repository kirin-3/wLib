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
    const lcDownloads = {checked:false,disabled:true,addEventListener:(_,change)=>callbacks.set('lc-downloads',change)};
    const onRemoved = [];
    for (const site of ['dlsite','vndb','ryuugames','lewdcorner']) inputs.push({dataset:{site},checked:false,disabled:false,addEventListener:(_,callback)=>callbacks.set(site,callback)});
    const permissionName = ({origins, permissions}) => origins?.[0] || permissions[0];
    const permissions = {
        contains:(permission,callback)=>callback(granted.has(permissionName(permission))),
        request:(permission,callback)=>{
            const name = permissionName(permission);
            if (name === 'webRequest') granted.add(name);
            callback(name === 'webRequest');
        },
        remove:(permission,callback)=>{granted.delete(permissionName(permission));callback(true);},
        onAdded:{addListener(){}},
        onRemoved:{addListener(listener){onRemoved.push(listener);}},
    };
    const document = {querySelectorAll:()=>inputs,getElementById:id=>id === 'lc-downloads' ? lcDownloads : status};
    vm.runInNewContext(readFileSync(path.join(__dirname,'../extension/options.js'),'utf8'), {document,chrome:{permissions,runtime:{}}});
    inputs[0].checked=true; callbacks.get('dlsite')();
    assert.equal(inputs[0].checked,false);
    assert.equal(inputs[0].disabled,false);
    granted.add('https://vndb.org/*'); inputs[1].checked=false; callbacks.get('vndb')();
    assert.equal(granted.size,0);
    assert.equal(lcDownloads.disabled,true);
    granted.add('https://lewdcorner.com/*');
    for (const notify of onRemoved) notify();
    assert.equal(lcDownloads.disabled,false);
    lcDownloads.checked=true; callbacks.get('lc-downloads')();
    assert.equal(granted.has('webRequest'),true);
    lcDownloads.checked=false; callbacks.get('lc-downloads')();
    assert.equal(granted.has('webRequest'),false);
    granted.add('webRequest');
    granted.delete('https://lewdcorner.com/*');
    for (const notify of onRemoved) notify();
    assert.equal(lcDownloads.checked,false);
    assert.equal(lcDownloads.disabled,true);
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

test('LewdCorner thread widget sends the cover and the full-size screenshot URLs', () => {
    // Shaped like a real LC first post: the cover is a full attachment image,
    // each screenshot a /data/attachments/ thumbnail inside a link to the full image.
    const attachment = name => `https://lewdcorner.com/attachments/${name}`;
    const img = (src, href = '', currentSrc = '') => ({src, currentSrc, closest: selector => (selector === 'a[href]' && href ? {href} : null)});
    const images = [
        img(attachment('cover-png.891887/')),
        img('https://lewdcorner.com/data/attachments/888/888308-c9.jpg?hash=a', attachment('shot1-png.891888/')),
        img('https://lewdcorner.com/data/attachments/888/888309-fe.jpg?hash=b', attachment('shot2-png.891889/')),
        img('https://lewdcorner.com/data/attachments/888/888308-c9.jpg?hash=a', attachment('shot1-png.891888/')),
        img('http://insecure.example/a.png'),
        img('https://i.imgur.com/x.png', '', 'https://i.imgur.com/x-current.png'),
        img('https://lewdcorner.com/data/attachments/1/1.jpg', 'https://lewdcorner.com/threads/other.5/'),
    ];
    const text = value => ({textContent: value});
    const heading = {cloneNode: () => ({textContent: 'BreedVille [v0.10.0] [Redsky]', querySelectorAll: () => []}), querySelectorAll: () => []};
    const post = {
        querySelector: selector => (selector.includes('Developer') ? {textContent: 'Redsky'} : selector.includes('version') ? {textContent: 'v0.10.0'} : null),
        querySelectorAll: selector => (selector === '.message-userContent img' ? images : []),
    };
    let shown = null;
    const context = vm.createContext({
        location: {pathname: '/threads/breedville.25000/'},
        document: {
            title: 'BreedVille',
            querySelector: selector => (selector === 'h1.p-title-value' ? heading : selector.startsWith('article.message') ? post : null),
            querySelectorAll: selector => (selector === '.tagList .tagItem' ? [text('3dcg')] : []),
        },
        WLibSiteWidget: {show: (_page, _extra, metadata) => { shown = metadata; }},
        globalThis: {},
    });
    vm.runInContext(readFileSync(path.join(__dirname, '../extension/sites/lewdcorner.js'), 'utf8'), context);
    assert.equal(shown.url, 'https://lewdcorner.com/threads/25000/');
    assert.equal(shown.coverImage, attachment('cover-png.891887/'));
    assert.deepEqual(Array.from(shown.screenshots), [
        attachment('shot1-png.891888/'),
        attachment('shot2-png.891889/'),
        'https://i.imgur.com/x-current.png',
        'https://lewdcorner.com/data/attachments/1/1.jpg',
    ]);
});

test('LewdCorner thread widget sends title prefixes as names with their label link ids beside them', () => {
    const label = (name, href) => ({textContent: name, closest: selector => (selector === 'a.labelLink' && href ? {href} : null)});
    const labels = [
        label('Other', 'https://lewdcorner.com/forums/games.119/?prefix_id=29'),
        label("Ren'Py", 'https://lewdcorner.com/forums/games.119/?prefix_id=9'),
        label('Completed'),
    ];
    const heading = {cloneNode: () => ({textContent: 'Game', querySelectorAll: () => []}), querySelectorAll: selector => (selector === '.label' ? labels : [])};
    let shown = null;
    const context = vm.createContext({
        location: {pathname: '/threads/game.623/'},
        document: {title: 'Game', querySelector: selector => (selector === 'h1.p-title-value' ? heading : null), querySelectorAll: () => []},
        WLibSiteWidget: {show: (_page, _extra, metadata) => { shown = metadata; }},
        globalThis: {},
    });
    vm.runInContext(readFileSync(path.join(__dirname, '../extension/sites/lewdcorner.js'), 'utf8'), context);
    // Plain strings, so an older wLib still reads the names.
    assert.deepEqual(JSON.parse(JSON.stringify(shown.prefixes)), ['Other', "Ren'Py", 'Completed']);
    assert.deepEqual(JSON.parse(JSON.stringify(shown.prefix_ids)), [29, 9, null]);
});

test('widget sizes are fixed px, so the host root font size does not change them', () => {
    const css = readFileSync(path.join(__dirname,'../extension/content.css'),'utf8');
    assert.doesNotMatch(css, /\d(\.\d+)?rem\b/);
    assert.doesNotMatch(css, /\.wlib-site-widget/);
    const widths = [...css.matchAll(/--wlib-widget-width:\s*([^;]+);/g)].map(match=>match[1]);
    assert.ok(widths.length >= 1);
    widths.forEach(width=>assert.match(width, /^min\(185px,/));
    const container = css.match(/^\.wlib-add-container \{([\s\S]*?)^\}/m)[1];
    assert.match(container, /box-shadow: 0 4px 12px rgba\(0, 0, 0, 0\.35\), 0 0 0 1px rgba\(255, 255, 255, 0\.04\) inset;/);
});
