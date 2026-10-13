// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import test from "node:test";
import { normalizeTextSizePreference } from "./textSizePreference.ts";

test("normalizeTextSizePreference keeps the four sizes", () => {
  assert.equal(normalizeTextSizePreference("small"), "small");
  assert.equal(normalizeTextSizePreference("default"), "default");
  assert.equal(normalizeTextSizePreference("large"), "large");
  assert.equal(normalizeTextSizePreference("xlarge"), "xlarge");
  assert.equal(normalizeTextSizePreference("extra large"), "xlarge");
  assert.equal(normalizeTextSizePreference("extra-large"), "xlarge");
});

test("normalizeTextSizePreference falls back to default", () => {
  for (const bad of [null, undefined, "", "  ", "medium", "huge", "14px", "DEFAULT "] as const) {
    if (bad === "DEFAULT ") {
      assert.equal(normalizeTextSizePreference(bad), "default");
      continue;
    }
    if (bad === null || bad === undefined || bad === "" || bad === "  ") {
      assert.equal(normalizeTextSizePreference(bad), "default");
      continue;
    }
    assert.equal(normalizeTextSizePreference(bad), "default");
  }
});
