// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import test from "node:test";
import {
  discardAfterPickerClose,
  discoverActions,
  discoverDate,
  discoverEntryTitle,
  discoverFocusAfterWishlist,
  discoverLoadError,
  discoverRatingNote,
} from "./discoverActions.ts";

test("the rating note names F95, the review count and the thread's own rating, in short words", () => {
  assert.equal(discoverRatingNote({ wrating: 4.1, rating: 4.3, rating_count: 1234 }), "F95, adjusted for 1,234 reviews (thread: 4.3)");
  assert.equal(discoverRatingNote({ wrating: 3.4, rating: 4, rating_count: 1 }), "F95, adjusted for 1 review (thread: 4.0)");
  assert.equal(discoverRatingNote({ wrating: null, rating: 4.3, rating_count: null }), "F95, review count not known yet");
});

test("a Found on row shows the site's title only when it is not the dialog's title", () => {
  assert.equal(discoverEntryTitle("Summer Night 3", "Summer Night 3"), "");
  assert.equal(discoverEntryTitle("  summer  night 3 ", "Summer Night 3"), "");
  assert.equal(discoverEntryTitle("", "Summer Night 3"), "");
  assert.equal(discoverEntryTitle("プレビューのゲーム", "Summer Night 3"), "プレビューのゲーム");
  assert.equal(discoverEntryTitle("Summer Night 3: Remastered", "Summer Night 3"), "Summer Night 3: Remastered");
});

test("a game not in the library offers Add to wishlist and Download game, and says Download adds it", () => {
  const actions = discoverActions({ library: null, download: { available: true, reason: "" } });
  assert.equal(actions.wishlist, true);
  assert.deepEqual(actions.download, {
    shown: true,
    enabled: true,
    label: "Download game",
    note: "Adds the game to your library once the download starts.",
    reason: "",
  });
  assert.equal(actions.openInLibrary, false);
  assert.equal(actions.libraryLabel, "");
});

test("a DLsite or VNDB game offers Add to wishlist and no Download", () => {
  const actions = discoverActions({ library: null, download: { available: false, reason: "" } });
  assert.equal(actions.wishlist, true);
  assert.equal(actions.download.shown, false);
  assert.equal(actions.download.note, "");
});

test("an LC Plus thread without LC Plus shows a disabled Download that explains LC Plus", () => {
  const actions = discoverActions({ library: null, download: { available: false, reason: "Needs LC Plus" } });
  assert.equal(actions.download.shown, true);
  assert.equal(actions.download.enabled, false);
  assert.equal(actions.download.note, "");
  assert.match(actions.download.reason, /^Needs LC Plus, LewdCorner's paid membership/);
  assert.match(actions.download.reason, /set LC Plus to Yes in Settings\.$/);
  // A reason the frontend does not know shows as the backend wrote it.
  assert.equal(discoverActions({ library: null, download: { available: false, reason: "Other" } }).download.reason, "Other");
});

test("a library game uses the Library's words: Wishlist or Installed, with Download game or Download update", () => {
  const wishlisted = discoverActions({ library: { game_id: 4, state: "wishlisted" }, download: { available: true, reason: "" } });
  assert.equal(wishlisted.wishlist, false);
  assert.equal(wishlisted.openInLibrary, true);
  assert.equal(wishlisted.libraryLabel, "Wishlist");
  assert.equal(wishlisted.download.label, "Download game");
  assert.equal(wishlisted.download.note, "");
  const installed = discoverActions({ library: { game_id: 4, state: "installed" }, download: { available: true, reason: "" } });
  assert.equal(installed.libraryLabel, "Installed");
  assert.equal(installed.download.label, "Download update");
  assert.equal(installed.download.note, "");
});

test("a load error says what failed and why", () => {
  assert.deepEqual(discoverLoadError({ error: "This game is no longer in the catalog", error_code: "unknown_game" }), {
    title: "This game is no longer in the catalog.",
    detail: "The catalog was updated. Close this window and search again.",
  });
  assert.equal(discoverLoadError({ error: "The game catalog is still loading", error_code: "not_ready" }).title, "The game catalog is still loading.");
  assert.equal(discoverLoadError({ error: "The game catalog is turned off", error_code: "catalog_off" }).title, "The game catalog is turned off.");
  assert.deepEqual(discoverLoadError({ error: "database is locked" }), {
    title: "Could not load this game.",
    detail: "wLib could not read the game catalog: database is locked.",
  });
  assert.equal(discoverLoadError({ error: "Could not read the catalog" }).detail, "wLib could not read the game catalog.");
  assert.equal(discoverLoadError({}).detail, "wLib could not read the game catalog.");
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

test("after Add to wishlist, focus goes to Download, else Open in library, else the dialog", () => {
  const withDownload = discoverActions({ library: { game_id: 4, state: "wishlisted" }, download: { available: true, reason: "" } });
  assert.equal(discoverFocusAfterWishlist(withDownload), "download");
  // An unavailable Download is still a stop: it explains why it is off.
  const plusOnly = discoverActions({ library: { game_id: 4, state: "wishlisted" }, download: { available: false, reason: "Needs LC Plus" } });
  assert.equal(discoverFocusAfterWishlist(plusOnly), "download");
  const noThread = discoverActions({ library: { game_id: 4, state: "wishlisted" }, download: { available: false, reason: "" } });
  assert.equal(discoverFocusAfterWishlist(noThread), "openInLibrary");
  assert.equal(discoverFocusAfterWishlist(null), "dialog");
});
