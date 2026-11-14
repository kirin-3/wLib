// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import test from "node:test";
import { isNavAutoCollapsed } from "./navLayout.ts";

test("the sidebar collapses on the Library while the filters pane is open", () => {
  assert.equal(isNavAutoCollapsed("library", true, false), true);
});

test("the sidebar does not collapse when the pane is closed", () => {
  assert.equal(isNavAutoCollapsed("library", false, false), false);
});

test("the user override keeps the sidebar expanded", () => {
  assert.equal(isNavAutoCollapsed("library", true, true), false);
});

test("other pages never collapse the sidebar automatically", () => {
  for (const name of ["settings", "updates", undefined, null]) {
    assert.equal(isNavAutoCollapsed(name, true, false), false);
  }
});
