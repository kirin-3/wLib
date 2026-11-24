// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import test from "node:test";
import { discardAfterPickerClose, discoverActions, discoverDate } from "./discoverActions.ts";

test("a game not in the library offers Wishlist and Download", () => {
  const actions = discoverActions({ library: null, download: { available: true, reason: "" } });
  assert.equal(actions.wishlist, true);
  assert.deepEqual(actions.download, { shown: true, enabled: true, reason: "" });
  assert.equal(actions.openInLibrary, false);
  assert.equal(actions.libraryLabel, "");
});

test("a DLsite or VNDB game offers Wishlist and no Download", () => {
  const actions = discoverActions({ library: null, download: { available: false, reason: "" } });
  assert.equal(actions.wishlist, true);
  assert.equal(actions.download.shown, false);
});

test("an LC Plus thread without LC Plus shows a disabled Download", () => {
  const actions = discoverActions({ library: null, download: { available: false, reason: "Needs LC Plus" } });
  assert.deepEqual(actions.download, { shown: true, enabled: false, reason: "Needs LC Plus" });
});

test("a library game offers Open in library and no Wishlist", () => {
  const actions = discoverActions({ library: { game_id: 4, state: "wishlisted" }, download: { available: true, reason: "" } });
  assert.equal(actions.wishlist, false);
  assert.equal(actions.openInLibrary, true);
  assert.equal(actions.libraryLabel, "Wishlisted");
  assert.equal(discoverActions({ library: { game_id: 4, state: "installed" }, download: { available: true, reason: "" } }).libraryLabel, "In library");
});

test("only the game this Download added is discarded, after its picker closes", () => {
  assert.equal(discardAfterPickerClose(7, { id: 7 }, null), 7);
  // Still open, or another game's picker.
  assert.equal(discardAfterPickerClose(7, null, { id: 7 }), null);
  assert.equal(discardAfterPickerClose(7, { id: 3 }, null), null);
  // Download used for a game that was already in the library.
  assert.equal(discardAfterPickerClose(null, { id: 7 }, null), null);
  // The picker moved on to another game.
  assert.equal(discardAfterPickerClose(7, { id: 7 }, { id: 9 }), 7);
});

test("dates from Unix seconds", () => {
  assert.equal(discoverDate(1710000000), "2024-03-09");
  assert.equal(discoverDate(0), "");
});
