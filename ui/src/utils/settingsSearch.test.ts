// SPDX-License-Identifier: GPL-3.0-or-later
import { test } from "node:test";
import assert from "node:assert/strict";
import { matchSettings } from "./settingsSearch.ts";

const sections = [
  { id: "appearance", title: "Appearance", rows: [{ id: "theme", label: "Theme", keywords: "light dark" }] },
  { id: "renpy", title: "Ren'Py", rows: [{ id: "unpack", label: "Unpack", keywords: "archive" }, { id: "other", label: "Other", keywords: "" }] },
];

test("matches labels and keywords without case sensitivity", () => {
  assert.deepEqual(matchSettings(sections, "DARK"), { sectionIds: ["appearance"], rowIds: ["theme"] });
  assert.deepEqual(matchSettings(sections, "unpack"), { sectionIds: ["renpy"], rowIds: ["unpack"] });
});
test("a matching title shows all rows and empty results are possible", () => {
  assert.deepEqual(matchSettings(sections, "REN'PY"), { sectionIds: ["renpy"], rowIds: ["unpack", "other"] });
  assert.deepEqual(matchSettings(sections, "missing"), { sectionIds: [], rowIds: [] });
  assert.equal(matchSettings(sections, " ").rowIds.length, 3);
});
