// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import test from "node:test";

import { initialMenuIndex, menuKeyAction } from "./menuKeyboard.ts";

const none = [false, false, false, false];

test("Down and Up move one item and wrap around", () => {
  assert.deepEqual(menuKeyAction("ArrowDown", 0, none), { type: "focus", index: 1 });
  assert.deepEqual(menuKeyAction("ArrowDown", 3, none), { type: "focus", index: 0 });
  assert.deepEqual(menuKeyAction("ArrowUp", 2, none), { type: "focus", index: 1 });
  assert.deepEqual(menuKeyAction("ArrowUp", 0, none), { type: "focus", index: 3 });
});

test("Down and Up with no focused item go to the first and last item", () => {
  assert.deepEqual(menuKeyAction("ArrowDown", -1, none), { type: "focus", index: 0 });
  assert.deepEqual(menuKeyAction("ArrowUp", -1, none), { type: "focus", index: 3 });
});

test("Home and End go to the first and last enabled item", () => {
  const disabled = [true, false, false, true];
  assert.deepEqual(menuKeyAction("Home", 2, disabled), { type: "focus", index: 1 });
  assert.deepEqual(menuKeyAction("End", 1, disabled), { type: "focus", index: 2 });
});

test("disabled items are skipped, also across the wrap", () => {
  const disabled = [false, true, false, true];
  assert.deepEqual(menuKeyAction("ArrowDown", 0, disabled), { type: "focus", index: 2 });
  assert.deepEqual(menuKeyAction("ArrowDown", 2, disabled), { type: "focus", index: 0 });
  assert.deepEqual(menuKeyAction("ArrowUp", 0, disabled), { type: "focus", index: 2 });
});

test("a menu with only disabled items or no items does not move focus", () => {
  assert.equal(menuKeyAction("ArrowDown", -1, [true, true]), null);
  assert.equal(menuKeyAction("Home", -1, []), null);
});

test("Escape closes and returns focus, Tab closes without it", () => {
  assert.deepEqual(menuKeyAction("Escape", 1, none), { type: "close", returnFocus: true });
  assert.deepEqual(menuKeyAction("Tab", 1, none), { type: "close", returnFocus: false });
  assert.deepEqual(menuKeyAction("Escape", -1, []), { type: "close", returnFocus: true });
});

test("other keys do nothing", () => {
  assert.equal(menuKeyAction("a", 0, none), null);
  assert.equal(menuKeyAction("Enter", 0, none), null);
});

test("initial index prefers the enabled checked item", () => {
  assert.equal(initialMenuIndex(none, 2), 2);
  assert.equal(initialMenuIndex([false, false, true], 2), 0);
  assert.equal(initialMenuIndex([true, false], -1), 1);
  assert.equal(initialMenuIndex([true, true]), -1);
});
