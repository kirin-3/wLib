// SPDX-License-Identifier: GPL-3.0-or-later
const {readFileSync} = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const source = readFileSync(require('node:path').join(__dirname,'../extension/widget-placement.js'),'utf8');
function setup(chrome) {
    const context = vm.createContext({chrome,console,requestAnimationFrame: callback => callback()});
    vm.runInContext(source,context);
    return context.WLibWidgetPlacement;
}
const plain = value => JSON.parse(JSON.stringify(value));
test('loads defaults and normalizes stored values and read failures', async () => {
    assert.doesNotThrow(() => vm.runInNewContext(source,{}));
    for (const [get, expected, error] of [
        [(_,cb) => cb({}), {side:'right',collapsed:false}],
        [(_,cb) => cb({widgetPlacement:{side:'bad',collapsed:'yes'}}), {side:'right',collapsed:false}],
        [() => {throw Error('failed');}, {side:'right',collapsed:false}],
        [(_,cb) => cb({widgetPlacement:{side:'left',collapsed:true}}), {side:'right',collapsed:false}, {message:'failed'}],
    ]) {
        const helper = setup({storage:{local:{get}},runtime:{lastError:error}});
        assert.deepEqual(plain(await helper.load()),expected);
    }
});
test('saves one key and watches only local placement changes', () => {
    let written, listener; const received = [];
    const helper = setup({storage:{local:{set:(value,cb) => {written = value; cb();}},onChanged:{addListener:fn => {listener = fn;}}},runtime:{}});
    helper.save({side:'left',collapsed:true});
    assert.deepEqual(plain(written),{widgetPlacement:{side:'left',collapsed:true}});
    helper.watch(value => received.push(plain(value)));
    listener({other:{newValue:1}},'local'); listener({widgetPlacement:{newValue:{side:'left'}}},'sync');
    listener({widgetPlacement:{newValue:{side:'left',collapsed:'bad'}}},'local');
    assert.deepEqual(received,[{side:'left',collapsed:false}]);
});
test('applies side, state, labels and tab order', () => {
    const helper = setup(); const attributes = {}, classes = new Set();
    const buttons = Object.fromEntries(['side-btn','collapse-btn','collapsed-toggle'].map(name => [`.wlib-${name}`,{setAttribute:(key,value) => {attributes[key] = value;}}]));
    const root = {setAttribute:(key,value) => {attributes[key] = value;},querySelector:selector => buttons[selector],classList:{add:key => classes.add(key),remove:key => classes.delete(key),toggle:(key,on) => on ? classes.add(key) : classes.delete(key)}};
    helper.apply(root,{side:'right',collapsed:true},{animate:false});
    assert.equal(attributes['data-side'],'right'); assert.equal(attributes['data-state'],'collapsed');
    assert.equal(attributes['aria-label'],'Move wLib widget to the left'); assert.ok(classes.has('is-collapsed'));
    assert.equal(buttons['.wlib-side-btn'].tabIndex,-1); assert.equal(buttons['.wlib-collapse-btn'].tabIndex,-1); assert.equal(buttons['.wlib-collapsed-toggle'].tabIndex,0);
    helper.apply(root,{side:'left',collapsed:false});
    assert.equal(attributes['aria-label'],'Move wLib widget to the right'); assert.equal(buttons['.wlib-side-btn'].tabIndex,0); assert.equal(buttons['.wlib-collapsed-toggle'].tabIndex,-1);
});
