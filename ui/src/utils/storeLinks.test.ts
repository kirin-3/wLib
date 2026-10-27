// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import { test } from "node:test";
import { extensionAddQuery, extensionGameId, extensionOpenQuery, gamePageUrl, readStoreLinks } from "./storeLinks.ts";
const link = {source:"vndb", id:"v1", url:"https://vndb.org/v1"};
test("extension link-only import and game-ID open survive query transport", () => {
  const query = extensionAddQuery({title:"Novel", links:[link], tags:["Fantasy"]});
  assert.equal(query.f95url, "");
  assert.deepEqual(readStoreLinks(query.links), [link]);
  assert.deepEqual(extensionOpenQuery({gameId:12}), {action:"open", f95url:"", gameId:"12"});
  assert.equal(extensionGameId(["12"]), 12);
  for (const invalid of ["0", "-1", "1.5", "NaN", "1e3", "9007199254740992"]) assert.equal(extensionGameId(invalid), undefined);
});
test("links reject unsafe URLs, invalid values and duplicates", () => {
  assert.deepEqual(readStoreLinks([link, link, { ...link, url:"javascript:alert(1)"}, null]), [link]);
  assert.deepEqual(readStoreLinks("bad JSON"), []);
  assert.equal(gamePageUrl({links:[link]}), link.url);
  assert.equal(gamePageUrl({f95_url:"https://f95zone.to/threads/1/", links:[link]}), "https://f95zone.to/threads/1/");
  assert.equal(gamePageUrl({}), "");
});
