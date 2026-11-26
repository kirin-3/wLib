// SPDX-License-Identifier: GPL-3.0-or-later
import test from "node:test";
import assert from "node:assert/strict";
import { PENDING_FIELD_AREA, pendingAreas, unsavedSummary } from "./gameDetailPending.ts";

const saved = {
  title: "Aurora",
  exe_path: "/games/aurora/game.sh",
  progress: "# Notes",
  note_links: [{ url: "https://example.com", label: "" }],
  collections: ["Favorites"],
  gamemode: "default",
};

test("an unchanged form has no pending areas", () => {
  assert.deepEqual(pendingAreas({ ...saved }, saved), []);
});

test("each changed field marks the area where it is edited", () => {
  assert.deepEqual(pendingAreas({ ...saved, title: "Aurora 2" }, saved), ["Info"]);
  assert.deepEqual(pendingAreas({ ...saved, gamemode: "on" }, saved), ["Launch"]);
  assert.deepEqual(pendingAreas({ ...saved, collections: [] }, saved), ["Collections"]);
});

test("nested values compare by content, not identity", () => {
  const current = { ...saved, note_links: [{ url: "https://example.com", label: "" }] };
  assert.deepEqual(pendingAreas(current, saved), []);
  current.note_links.push({ url: "https://example.org", label: "Guide" });
  assert.deepEqual(pendingAreas(current, saved), ["Notes"]);
});

test("pending areas follow the tab order, then collections", () => {
  const current = { ...saved, collections: [], progress: "", title: "", exe_path: "" };
  assert.deepEqual(pendingAreas(current, saved), ["Launch", "Info", "Notes", "Collections"]);
});

test("instant-save fields are not part of the pending set", () => {
  const fields = Object.keys(PENDING_FIELD_AREA);
  for (const field of ["play_status", "is_favorite", "tags", "rating_graphics", "rating_story", "rating_fappability", "rating_gameplay"]) {
    assert.equal(fields.includes(field), false, `${field} saves at once`);
  }
});

test("the footer names what Save changes covers", () => {
  assert.equal(unsavedSummary([]), "Unsaved changes");
  assert.equal(unsavedSummary(["Info"]), "Unsaved changes in Info");
  assert.equal(unsavedSummary(["Launch", "Info"]), "Unsaved changes in Launch and Info");
  assert.equal(unsavedSummary(["Launch", "Notes", "Collections"]), "Unsaved changes in Launch, Notes and collections");
});
