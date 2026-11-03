// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import test from "node:test";
import { actionForKey, readController } from "./controllerInput.ts";
const pad = (pressed: number[] = [], axes = [0, 0, 0, 0], mapping = "standard") => ({ mapping, axes, buttons: Array.from({ length: 16 }, (_, i) => ({ pressed: pressed.includes(i), value: pressed.includes(i) ? 1 : 0, touched: false })) });
test("standard buttons map to actions without repeat", () => {
  const first = readController([pad([0, 1, 2, 3, 4, 5, 6, 7, 8])], true, 0, {});
  assert.deepEqual(first.actions.map(a => a.type), ["activate", "back", "play", "search", "prevTab", "nextTab", "pageUp", "pageDown", "menu"]);
  assert.deepEqual(readController([pad([0])], true, 500, first.state).actions, []);
});
test("directions repeat after 400ms and then every 90ms", () => {
  const p = pad([12], [0.6, 0, 0, -0.8]);
  const first = readController([p], true, 0, {});
  assert.deepEqual(first.actions, [{ type: "move", direction: "up" }, { type: "move", direction: "right" }, { type: "scroll", x: 0, y: -0.8 }]);
  assert.equal(readController([p], true, 399, first.state).actions.length, 1);
  const repeat = readController([p], true, 400, first.state);
  assert.equal(repeat.actions.length, 3);
  assert.equal(readController([p], true, 489, repeat.state).actions.length, 1);
  assert.equal(readController([p], true, 490, repeat.state).actions.length, 3);
});
test("ignores unfocused and nonstandard pads", () => {
  assert.deepEqual(readController([pad([0])], false, 0, {}).actions, []);
  assert.deepEqual(readController([pad([0], [], "")], true, 0, {}).actions, []);
});
test("controls held while unfocused do not fire until released", () => {
  const away = readController([pad([0, 13])], false, 0, {});
  assert.deepEqual(readController([pad([0, 13])], true, 1000, away.state).actions, []);
  const released = readController([pad()], true, 1100, away.state);
  assert.deepEqual(readController([pad([0])], true, 1200, released.state).actions, [{ type: "activate" }]);
});
type Kind = "none" | "menu" | "text" | "number" | "select" | "tablist";
const event = (key: string, kind: Kind = "none", prevented = false) => {
  const element = { matches: (selector: string) => kind !== "number" || !selector.includes("number") };
  const closest = (selector: string) => {
    const hit = kind === "menu" ? selector.includes('role="menu"') : kind === "text" || kind === "number" ? selector.includes("textarea") : kind === "select" ? selector === "select" : kind === "tablist" ? selector.includes("tablist") : false;
    return hit ? element : null;
  };
  return { key, defaultPrevented: prevented, target: { closest } } as unknown as KeyboardEvent;
};
test("keyboard maps navigation keys and respects handled events and menus", () => {
  assert.deepEqual(actionForKey(event("ArrowDown")), { type: "move", direction: "down" });
  assert.deepEqual(actionForKey(event("Escape")), { type: "back" });
  assert.equal(actionForKey(event("ArrowDown", "menu")), null);
  assert.equal(actionForKey(event("Escape", "none", true)), null);
});
test("text fields keep typing keys but let Up and Down leave single-line fields", () => {
  assert.equal(actionForKey(event("ArrowLeft", "text")), null);
  assert.equal(actionForKey(event("Escape", "text")), null);
  assert.deepEqual(actionForKey(event("ArrowDown", "text")), { type: "move", direction: "down" });
  assert.equal(actionForKey(event("ArrowUp", "number")), null);
});
test("selects move with arrows and open with Enter", () => {
  assert.deepEqual(actionForKey(event("ArrowDown", "select")), { type: "move", direction: "down" });
  assert.deepEqual(actionForKey(event("Enter", "select")), { type: "activate" });
});
test("tab strips keep Left and Right but not Up and Down", () => {
  assert.equal(actionForKey(event("ArrowRight", "tablist")), null);
  assert.deepEqual(actionForKey(event("ArrowDown", "tablist")), { type: "move", direction: "down" });
});
