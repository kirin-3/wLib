// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import test from "node:test";
import { loadMotionPreference, saveMotionPreference, motionEnabled } from "./motionPreference.ts";

test("loadMotionPreference safely handles missing or throwing storage", () => {
  // Calling without arguments in node environment where localStorage is undefined
  assert.equal(typeof loadMotionPreference(), "boolean");

  // Throwing storage
  const throwingStorage = {
    getItem() {
      throw new Error("SecurityError: Access is denied");
    },
    setItem() {
      throw new Error("SecurityError: Access is denied");
    },
  } as unknown as Storage;

  assert.equal(loadMotionPreference(throwingStorage), true);
  assert.equal(motionEnabled.value, true);

  // Valid storage
  const map = new Map<string, string>();
  const mockStorage = {
    getItem(k: string) {
      return map.get(k) ?? null;
    },
    setItem(k: string, v: string) {
      map.set(k, v);
    },
  } as unknown as Storage;

  saveMotionPreference(false, mockStorage);
  assert.equal(motionEnabled.value, false);
  assert.equal(loadMotionPreference(mockStorage), false);

  saveMotionPreference(true, mockStorage);
  assert.equal(motionEnabled.value, true);
  assert.equal(loadMotionPreference(mockStorage), true);
});
