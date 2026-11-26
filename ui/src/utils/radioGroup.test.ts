// SPDX-License-Identifier: GPL-3.0-or-later
import test from "node:test";
import assert from "node:assert/strict";

import { radioKeyTarget, radioTabStop } from "./radioGroup.ts";

test("the checked radio is the tab stop, else the first enabled radio", () => {
  assert.equal(radioTabStop(2, [false, false, false]), 2);
  assert.equal(radioTabStop(-1, [false, false, false]), 0);
  assert.equal(radioTabStop(-1, [true, false, false]), 1);
  // A checked radio that is disabled cannot take focus.
  assert.equal(radioTabStop(0, [true, false]), 1);
  assert.equal(radioTabStop(-1, [true, true]), -1);
  assert.equal(radioTabStop(-1, []), -1);
});

test("arrow keys move forward and back with wrap-around", () => {
  const enabled = [false, false, false];
  assert.equal(radioKeyTarget("ArrowDown", 0, enabled), 1);
  assert.equal(radioKeyTarget("ArrowRight", 1, enabled), 2);
  assert.equal(radioKeyTarget("ArrowDown", 2, enabled), 0);
  assert.equal(radioKeyTarget("ArrowUp", 0, enabled), 2);
  assert.equal(radioKeyTarget("ArrowLeft", 2, enabled), 1);
  assert.equal(radioKeyTarget("Home", 2, enabled), 0);
  assert.equal(radioKeyTarget("End", 0, enabled), 2);
});

test("disabled radios are skipped", () => {
  const flags = [false, true, false, true];
  assert.equal(radioKeyTarget("ArrowDown", 0, flags), 2);
  assert.equal(radioKeyTarget("ArrowDown", 2, flags), 0);
  assert.equal(radioKeyTarget("ArrowUp", 0, flags), 2);
  assert.equal(radioKeyTarget("End", 0, flags), 2);
  assert.equal(radioKeyTarget("Home", 2, [true, false, false]), 1);
});

test("a single enabled radio stays put and other keys do nothing", () => {
  assert.equal(radioKeyTarget("ArrowDown", 1, [true, false, true]), 1);
  assert.equal(radioKeyTarget("ArrowDown", 0, [true, true]), null);
  assert.equal(radioKeyTarget("Enter", 0, [false, false]), null);
  assert.equal(radioKeyTarget(" ", 0, [false, false]), null);
  assert.equal(radioKeyTarget("ArrowDown", 0, []), null);
});
