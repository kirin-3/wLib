// SPDX-License-Identifier: GPL-3.0-or-later
import test from "node:test";
import assert from "node:assert/strict";

import type { RpgmPatch } from "../services/api.ts";
import {
  buildPatchList,
  clearPatch,
  filterPicker,
  filterRows,
  isDirty,
  isEditableRaw,
  parseRawInput,
  pathKey,
  rawChildren,
  setPatch,
  validateBonus,
  validateCount,
  validateGold,
  validateLevel,
  validateVariable,
  type PatchMap,
} from "./rpgmSaveEditor.ts";

test("the last edit of a target wins and curated/raw share a key", () => {
  let map: PatchMap = new Map();
  const key = pathKey(["party", "_gold"]);
  map = setPatch(map, key, { op: "set", path: ["party", "_gold"], value: 5 });
  map = setPatch(map, key, { op: "set", path: ["party", "_gold"], value: 9 });
  assert.equal(map.size, 1);
  assert.deepEqual(buildPatchList(map), [{ op: "set", path: ["party", "_gold"], value: 9 }]);
});

test("dirty state follows the patch map", () => {
  let map: PatchMap = new Map();
  assert.equal(isDirty(map), false);
  map = setPatch(map, "a", { op: "set_switch", id: 1, value: true });
  assert.equal(isDirty(map), true);
  assert.equal(isDirty(clearPatch(map, "a")), false);
});

test("level patches are ordered before other edits", () => {
  let map: PatchMap = new Map();
  const exp: RpgmPatch = { op: "set_actor", id: 1, field: "exp", value: 5 };
  const level: RpgmPatch = { op: "set_actor", id: 1, field: "level", value: 9 };
  map = setPatch(map, "exp", exp);
  map = setPatch(map, "level", level);
  assert.deepEqual(buildPatchList(map), [level, exp]);
});

test("numeric validators enforce whole numbers and ranges", () => {
  assert.deepEqual(validateGold("50000"), { ok: true, value: 50000 });
  assert.equal(validateGold("10000000").ok, false);
  assert.equal(validateCount("-3").ok, false);
  assert.equal(validateCount("2.5").ok, false);
  assert.equal(validateCount("0").ok, true);
  assert.equal(validateLevel("0").ok, false);
  assert.equal(validateLevel("12").ok, true);
  assert.deepEqual(validateBonus("-50"), { ok: true, value: -50 });
  assert.equal(validateBonus("abc").ok, false);
});

test("variables keep their type", () => {
  assert.deepEqual(validateVariable("3.5", "number"), { ok: true, value: 3.5 });
  assert.equal(validateVariable("x", "number").ok, false);
  assert.deepEqual(validateVariable("hello", "string"), { ok: true, value: "hello" });
  assert.equal(validateVariable("1", "other").ok, false);
});

test("picker skips blank names and ranks prefix matches first", () => {
  const entries = [
    { id: 1, name: "Iron Sword", kind: "item" as const, description: "" },
    { id: 2, name: "", kind: "item" as const, description: "" },
    { id: 3, name: "Sword of Light", kind: "item" as const, description: "" },
  ];
  assert.deepEqual(filterPicker(entries, "").map((entry) => entry.id), [1, 3]);
  assert.deepEqual(filterPicker(entries, "sword").map((entry) => entry.id), [3, 1]);
  assert.deepEqual(filterPicker(entries, "nothing"), []);
});

test("rows filter by id or name", () => {
  const rows = [
    { id: 1, name: "Gold seen" },
    { id: 12, name: "Met the king" },
  ];
  assert.deepEqual(filterRows(rows, "gold").map((row) => row.id), [1]);
  assert.deepEqual(filterRows(rows, "12").map((row) => row.id), [12]);
  assert.equal(filterRows(rows, "").length, 2);
});

test("raw editability is scalar and not an @ key", () => {
  assert.equal(isEditableRaw("points", 3), true);
  assert.equal(isEditableRaw("@", "Game_Actor"), false);
  assert.equal(isEditableRaw("@c", 5), false);
  assert.equal(isEditableRaw("list", [1]), false);
  assert.equal(isEditableRaw(0, null), false);
});

test("raw children flatten JsonEx array wrappers and mark bookkeeping", () => {
  const children = rawChildren({ "@c": 4, "@a": [null, { x: 1 }] });
  assert.deepEqual(children.map((child) => [child.key, child.meta]), [["@c", true], [0, false], [1, false]]);
  const object = rawChildren({ "@": "Game_Party", gold: 5, nested: {} });
  assert.deepEqual(object.map((child) => [child.key, child.editable]), [["@", false], ["gold", true], ["nested", false]]);
});

test("raw input keeps the type of the original value", () => {
  assert.equal(parseRawInput(3, "abc").ok, false);
  assert.deepEqual(parseRawInput(3, "10"), { ok: true, value: 10 });
  assert.equal(parseRawInput(3, "1.5").ok, false);
  assert.deepEqual(parseRawInput(1.5, "2.5"), { ok: true, value: 2.5 });
  assert.deepEqual(parseRawInput(false, "TRUE"), { ok: true, value: true });
  assert.equal(parseRawInput(false, "yes").ok, false);
  assert.deepEqual(parseRawInput("a", "b c"), { ok: true, value: "b c" });
});
