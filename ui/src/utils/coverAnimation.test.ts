// SPDX-License-Identifier: GPL-3.0-or-later
import test from "node:test";
import assert from "node:assert/strict";
import {
  coverAnimation,
  effectiveCoverAnimation,
  loadCoverAnimation,
  saveCoverAnimation,
} from "./coverAnimation.ts";

const memoryStorage = (initial?: string) => {
  const map = new Map<string, string>(initial ? [["wlib-cover-animation", initial]] : []);
  return {
    getItem: (key: string) => map.get(key) ?? null,
    setItem: (key: string, value: string) => void map.set(key, value),
  } as unknown as Storage;
};

test("defaults to hover and survives broken storage", () => {
  assert.equal(loadCoverAnimation(memoryStorage()), "hover");
  const throwing = { getItem() { throw new Error("denied"); }, setItem() { throw new Error("denied"); } } as unknown as Storage;
  const originalWarn = console.warn;
  console.warn = () => {};
  assert.equal(loadCoverAnimation(throwing), "hover");
  saveCoverAnimation("always", throwing);
  console.warn = originalWarn;
  assert.equal(coverAnimation.value, "always");
});

test("choice persists and invalid stored values fall back", () => {
  const storage = memoryStorage();
  saveCoverAnimation("always", storage);
  assert.equal(loadCoverAnimation(storage), "always");
  assert.equal(loadCoverAnimation(memoryStorage("sometimes")), "hover");
});

test("motion off and running games force still covers", () => {
  assert.equal(effectiveCoverAnimation("always", true, false), "always");
  assert.equal(effectiveCoverAnimation("hover", true, false), "hover");
  assert.equal(effectiveCoverAnimation("never", true, false), "still");
  assert.equal(effectiveCoverAnimation("always", false, false), "still");
  assert.equal(effectiveCoverAnimation("hover", true, true), "still");
});
