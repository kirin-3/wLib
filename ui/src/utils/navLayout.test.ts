// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import test from "node:test";
import { filtersPaneMode, isNavAutoCollapsed, LIBRARY_FILTERS_INLINE_MIN, wantsNavAutoCollapse } from "./navLayout.ts";

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

test("narrow windows collapse the sidebar on every page", () => {
  for (const name of ["library", "settings", undefined]) {
    assert.equal(isNavAutoCollapsed(name, false, false, 899), true);
    assert.equal(isNavAutoCollapsed(name, false, false, 900), false);
  }
  assert.equal(isNavAutoCollapsed("settings", false, true, 640), false);
});

test("the filters pane sits beside the games only in wide windows", () => {
  assert.equal(filtersPaneMode(LIBRARY_FILTERS_INLINE_MIN), "inline");
  assert.equal(filtersPaneMode(1280), "inline");
  assert.equal(filtersPaneMode(LIBRARY_FILTERS_INLINE_MIN - 1), "drawer");
  assert.equal(filtersPaneMode(640), "drawer");
});

test("the override resets once nothing wants the sidebar collapsed", () => {
  assert.equal(wantsNavAutoCollapse("library", false, 1280), false);
  assert.equal(wantsNavAutoCollapse("library", true, 1280), true);
  assert.equal(wantsNavAutoCollapse("discover", false, 700), true);
});
