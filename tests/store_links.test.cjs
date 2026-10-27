// SPDX-License-Identifier: GPL-3.0-or-later
const {readFileSync} = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const context = vm.createContext({URL});
vm.runInContext(readFileSync(path.join(__dirname,'../extension/store-links.js'),'utf8'), context);
const {parseStoreLink} = context.WLibStoreLinks;
for (const row of JSON.parse(readFileSync(path.join(__dirname,'fixtures/store_links.json'),'utf8'))) {
    test(`store URL: ${row.url}`, () => {
        const link = parseStoreLink(row.url);
        if (row.source === null) assert.equal(link,null);
        else {
            assert.equal(link.source,row.source);
            assert.equal(link.id,row.id);
            assert.equal(new URL(link.url).hash,'');
            assert.equal(JSON.stringify(parseStoreLink(link.url)),JSON.stringify(link));
        }
    });
}
