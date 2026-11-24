// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import { test } from "node:test";
import { extensionAddQuery, extensionGameId, extensionOpenQuery, gamePageUrl, libraryOpenQuery, readQueryScreenshots, readStoreLinks } from "./storeLinks.ts";
const link = {source:"vndb", id:"v1", url:"https://vndb.org/v1"};
test("extension link-only import and game-ID open survive query transport", () => {
  const query = extensionAddQuery({title:"Novel", links:[link], tags:["Fantasy"]});
  assert.equal(query.f95url, "");
  assert.deepEqual(readStoreLinks(query.links), [link]);
  assert.deepEqual(extensionOpenQuery({gameId:12}), {action:"open", f95url:"", gameId:"12"});
  assert.equal(extensionGameId(["12"]), 12);
  for (const invalid of ["0", "-1", "1.5", "NaN", "1e3", "9007199254740992"]) assert.equal(extensionGameId(invalid), undefined);
});
test("library open requests identify store-link-only games by id", () => {
  // A game with no F95 thread must still be openable from the Updates page.
  const storeOnly = libraryOpenQuery({ id: 30, f95_url: "" });
  assert.deepEqual(storeOnly, { action: "open", f95url: "", gameId: "30" });
  assert.equal(extensionGameId(storeOnly.gameId), 30);
  // Games with a thread keep it for older handlers, and the id wins in the library.
  assert.deepEqual(libraryOpenQuery({ id: 31, f95_url: "https://f95zone.to/threads/normal.31/" }), {
    action: "open",
    f95url: "https://f95zone.to/threads/normal.31/",
    gameId: "31",
  });
});
test("links reject unsafe URLs, invalid values and duplicates", () => {
  assert.deepEqual(readStoreLinks([link, link, { ...link, url:"javascript:alert(1)"}, null]), [link]);
  assert.deepEqual(readStoreLinks("bad JSON"), []);
  assert.equal(gamePageUrl({links:[link]}), link.url);
  assert.equal(gamePageUrl({f95_url:"https://f95zone.to/threads/1/", links:[link]}), "https://f95zone.to/threads/1/");
  assert.equal(gamePageUrl({}), "");
});

test("LC thread identity survives extension add and takes priority over store links", () => {
  const lc = "https://lewdcorner.com/threads/123/";
  const f95 = "https://f95zone.to/threads/456/";
  const query = extensionAddQuery({ lc_url: lc, lc_suggested: lc, title: "Game", thread_completed: false });
  assert.equal(query.lcurl, lc);
  assert.equal(query.lcSuggested, lc);
  assert.equal(query.threadCompleted, "false");
  assert.equal(gamePageUrl({ f95_url: f95, lc_url: lc, links: [link] }), f95);
  assert.equal(gamePageUrl({ lc_url: lc, links: [link] }), lc);
  assert.deepEqual(readStoreLinks([{ source: "other", id: lc, url: lc }]), []);
});

test("LC page screenshots travel through the add query", () => {
  const shots = ["https://lewdcorner.com/attachments/a-png.1/", "https://lewdcorner.com/attachments/b-png.2/"];
  const query = extensionAddQuery({ lc_url: "https://lewdcorner.com/threads/123/", screenshots: shots });
  assert.deepEqual(readQueryScreenshots(query.screenshots), shots);
  assert.equal(extensionAddQuery({ url: "https://f95zone.to/threads/1/", screenshots: shots }).screenshots, undefined);
  assert.deepEqual(readQueryScreenshots("not json"), []);
  assert.deepEqual(readQueryScreenshots(JSON.stringify(["http://x/", 3, "https://ok/"])), ["https://ok/"]);
});

test("original_title travels through extensionAddQuery when set", () => {
  const query = extensionAddQuery({
    title: "Summer Witches!",
    original_title: "なつまじょ！",
    links: [{ source: "dlsite", id: "RJ01609862", url: "https://www.dlsite.com/maniax/work/=/product_id/RJ01609862.html" }],
  });
  assert.equal(query.title, "Summer Witches!");
  assert.equal(query.originalTitle, "なつまじょ！");
  assert.equal(extensionAddQuery({ title: "JP only", original_title: "" }).originalTitle, undefined);
});
