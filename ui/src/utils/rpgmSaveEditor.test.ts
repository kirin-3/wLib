// SPDX-License-Identifier: GPL-3.0-or-later
import test from "node:test";
import assert from "node:assert/strict";

import type { RpgmPatch } from "../services/api.ts";
import {
  buildPatchList,
  clearPatch,
  filterPicker,
  filterRows,
  formatLabel,
  isDirty,
  isEditableRaw,
  isRgssFormat,
  leafKey,
  parseRawInput,
  parseRgssRawInput,
  pathKey,
  rawChildren,
  refKey,
  setPatch,
  validateBonus,
  validateCount,
  validateGold,
  validateLevel,
  validateVariable,
  type PatchMap,
} from "./rpgmSaveEditor.ts";
import {
  countChanges,
  formatChangeValue,
  rawPathLabel,
  rgssNodeLabel,
  saveButtonLabel,
  summarizeChanges,
  type ChangeMetaMap,
} from "./rpgmSaveEditor.ts";
import {
  countInvalid,
  draftError,
  draftText,
  fieldDomId,
  fieldErrorId,
  firstInvalidTab,
  fixFieldsLabel,
  invalidByTab,
  rawFieldId,
  rgssFieldId,
  setDraft,
  settleDraft,
  tabErrorText,
  type DraftMap,
  type EditorTab,
} from "./rpgmSaveEditor.ts";
import type { RgssRawNodeData } from "../services/api.ts";

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

test("rgss ref keys collapse curated and raw edits of the same leaf", () => {
  assert.equal(refKey(42), "ref:42");
  assert.equal(leafKey(42, ["party", "_gold"]), "ref:42");
  assert.equal(leafKey(null, ["party", "_gold"]), pathKey(["party", "_gold"]));
  assert.equal(leafKey(undefined, ["party", "_gold"]), pathKey(["party", "_gold"]));

  let map: PatchMap = new Map();
  const key = leafKey(10, ["party", "_gold"]);
  map = setPatch(map, key, { op: "set_node", node: 10, value: 500 });
  map = setPatch(map, refKey(10), { op: "set_node", node: 10, value: 900 });
  assert.equal(map.size, 1);
  assert.deepEqual(buildPatchList(map), [{ op: "set_node", node: 10, value: 900 }]);
});

test("rgss raw input keeps strict Marshal scalar types", () => {
  assert.deepEqual(parseRgssRawInput("int", "42"), { ok: true, value: 42 });
  assert.deepEqual(parseRgssRawInput("int", "-7"), { ok: true, value: -7 });
  assert.equal(parseRgssRawInput("int", "abc").ok, false);
  assert.equal(parseRgssRawInput("int", "1.5").ok, false);
  assert.equal(parseRgssRawInput("int", "99999999999999999999").ok, false);

  assert.deepEqual(parseRgssRawInput("float", "1.5"), { ok: true, value: 1.5 });
  assert.deepEqual(parseRgssRawInput("float", "2"), { ok: true, value: 2 });
  assert.equal(parseRgssRawInput("float", "abc").ok, false);
  assert.equal(parseRgssRawInput("float", "").ok, false);
  assert.equal(parseRgssRawInput("float", "Infinity").ok, false);

  assert.deepEqual(parseRgssRawInput("bool", "true"), { ok: true, value: true });
  assert.deepEqual(parseRgssRawInput("bool", "FALSE"), { ok: true, value: false });
  assert.equal(parseRgssRawInput("bool", "1").ok, false);

  assert.deepEqual(parseRgssRawInput("str", "Clärís"), { ok: true, value: "Clärís" });
  assert.deepEqual(parseRgssRawInput("str", ""), { ok: true, value: "" });

  assert.equal(isRgssFormat("xp"), true);
  assert.equal(isRgssFormat("vx"), true);
  assert.equal(isRgssFormat("vxace"), true);
  assert.equal(isRgssFormat("mv"), false);
  assert.equal(formatLabel("xp"), "XP");
  assert.equal(formatLabel("vx"), "VX");
  assert.equal(formatLabel("vxace"), "VX Ace");
});

test("save button and change count use singular and plural", () => {
  assert.equal(saveButtonLabel(0), "Save");
  assert.equal(saveButtonLabel(1), "Save 1 change");
  assert.equal(saveButtonLabel(3), "Save 3 changes");
  assert.equal(countChanges(1200), "1,200 changes");
});

test("change values read the way the editor shows them", () => {
  assert.equal(formatChangeValue(true, "switch"), "ON");
  assert.equal(formatChangeValue(false, "switch"), "OFF");
  assert.equal(formatChangeValue(false), "false");
  assert.equal(formatChangeValue(1200), "1,200");
  assert.equal(formatChangeValue(1.5), "1.5");
  assert.equal(formatChangeValue(""), '""');
  assert.equal(formatChangeValue("Harold"), '"Harold"');
  assert.equal(formatChangeValue(null), "(none)");
});

test("summarizeChanges lists every pending patch in edit order with old and new values", () => {
  let patches = setPatch(new Map(), "gold", { op: "set", path: ["party", "_gold"], value: 5000 });
  patches = setPatch(patches, "sw", { op: "set_switch", id: 3, value: true });
  patches = setPatch(patches, "unlabelled", { op: "set_count", kind: "item", id: 7, count: 2 });
  const meta: ChangeMetaMap = new Map([
    ["gold", { label: "Gold", before: 1200 }],
    ["sw", { label: "Switch #3 · Door", before: false, kind: "switch" as const }],
    ["stale", { label: "Not pending", before: 1 }],
  ]);
  assert.deepEqual(summarizeChanges(patches, meta), [
    { key: "gold", label: "Gold", before: "1,200", after: "5,000" },
    { key: "sw", label: "Switch #3 · Door", before: "OFF", after: "ON" },
    { key: "unlabelled", label: "unlabelled", before: "(none)", after: "2" },
  ]);
});

test("rawPathLabel joins the path of a Raw edit", () => {
  assert.equal(rawPathLabel(["party", "_questLog", "points"]), "party › _questLog › points");
  assert.equal(rawPathLabel(["actors", "_data", 1, "_name"]), "actors › _data › 1 › _name");
});

test("rgssNodeLabel finds where a Raw node sits in the dumps", () => {
  const gold: RgssRawNodeData = { id: 9, t: "int", v: 500, editable: true };
  const contents: RgssRawNodeData = {
    id: 2,
    t: "hash",
    pairs: [
      [
        { id: 3, t: "sym", v: "party" },
        { id: 4, t: "object", class: "Game_Party", ivars: [["@gold", gold], ["@items", { id: 5, t: "array", items: [{ id: 6, t: "int", v: 1, editable: true }] }]] },
      ],
    ],
  };
  const entries = [
    { label: "Header", node: { id: 1, t: "nil" } as RgssRawNodeData },
    { label: "Contents", node: contents },
  ];
  assert.equal(rgssNodeLabel(entries, 9), "Contents › :party › @gold");
  assert.equal(rgssNodeLabel(entries, 6), "Contents › :party › @items › [0]");
  assert.equal(rgssNodeLabel(entries, 404), "Node #404");
});

test("a field keeps invalid text and clears the error once the text is valid again", () => {
  let drafts: DraftMap = new Map();
  drafts = setDraft(drafts, "count:item:1", "abc", "party", validateCount("abc"));
  assert.equal(draftText(drafts, "count:item:1", 5), "abc");
  assert.equal(draftError(drafts, "count:item:1"), "Count must be a whole number");
  assert.equal(countInvalid(drafts), 1);
  // Typing the original value again clears the error at once.
  drafts = setDraft(drafts, "count:item:1", "5", "party", validateCount("5"));
  assert.equal(draftError(drafts, "count:item:1"), "");
  assert.equal(countInvalid(drafts), 0);
  // On blur a valid draft goes, so the field shows the saved form of the value.
  drafts = settleDraft(drafts, "count:item:1");
  assert.equal(drafts.size, 0);
  assert.equal(draftText(drafts, "count:item:1", 5), "5");
  assert.equal(draftText(drafts, "missing", null), "");
});

test("settleDraft keeps an invalid draft", () => {
  const drafts = setDraft(new Map(), "gold", "-1", "party", validateGold("-1"));
  assert.equal(settleDraft(drafts, "gold"), drafts);
  assert.equal(draftText(settleDraft(drafts, "gold"), "gold", 10), "-1");
});

test("invalid fields are counted per tab and named in the footer hint", () => {
  const tabs: { id: EditorTab; label: string }[] = [
    { id: "party", label: "Party" },
    { id: "actors", label: "Actors" },
    { id: "variables", label: "Variables" },
    { id: "raw", label: "Raw" },
  ];
  let drafts: DraftMap = new Map();
  assert.equal(fixFieldsLabel(drafts, tabs), "");
  assert.equal(firstInvalidTab(drafts, ["party", "actors"]), null);
  drafts = setDraft(drafts, "actor:1:level", "0", "actors", validateLevel("0"));
  assert.equal(fixFieldsLabel(drafts, tabs), "Fix 1 field on Actors");
  drafts = setDraft(drafts, "gold", "x", "party", validateGold("x"));
  drafts = setDraft(drafts, "count:item:2", "7", "party", validateCount("7"));
  drafts = setDraft(drafts, "raw:x", "y", "raw", { ok: false, error: "Enter a number" });
  assert.deepEqual(invalidByTab(drafts), { actors: 1, party: 1, raw: 1 });
  assert.equal(firstInvalidTab(drafts, ["party", "actors", "raw"]), "party");
  assert.equal(fixFieldsLabel(drafts, tabs), "Fix 3 fields on Party, Actors and Raw");
  drafts = setDraft(drafts, "count:item:2", "z", "party", validateCount("z"));
  assert.deepEqual(invalidByTab(drafts), { actors: 1, party: 2, raw: 1 });
  assert.equal(tabErrorText(1), "1 field to fix");
  assert.equal(tabErrorText(2), "2 fields to fix");
});

test("field DOM ids are plain and unique per field", () => {
  const raw = rawFieldId(["party", "_gold"]);
  assert.equal(raw, `raw:${pathKey(["party", "_gold"])}`);
  assert.equal(rgssFieldId(9), "rgss:9");
  assert.match(fieldDomId(raw), /^[A-Za-z0-9_-]+$/);
  assert.match(fieldErrorId(raw), /^[A-Za-z0-9_-]+$/);
  assert.notEqual(fieldDomId("a:b"), fieldDomId("a_b"));
  assert.notEqual(fieldDomId(raw), fieldErrorId(raw));
});
