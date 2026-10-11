// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import test from "node:test";
import { normalizeAccentPreference, onAccentColor } from "./accentPreference.ts";

test("normalizeAccentPreference keeps presets and valid hex colors", () => {
  for (const preset of ["magenta", "violet", "blue", "cyan", "rose", "orange"]) {
    assert.equal(normalizeAccentPreference(preset), preset);
  }
  assert.equal(normalizeAccentPreference("#1e3a8a"), "#1e3a8a");
  assert.equal(normalizeAccentPreference("#FFFF66"), "#ffff66");
});

test("normalizeAccentPreference falls back to magenta", () => {
  for (const bad of [null, undefined, "", "green", "#fff", "#12345g", "red; x", "#1e3a8a00"]) {
    assert.equal(normalizeAccentPreference(bad), "magenta");
  }
});

test("onAccentColor picks the higher-contrast label", () => {
  assert.equal(onAccentColor("#ffff66"), "#0f0f14");
  assert.equal(onAccentColor("#1e3a8a"), "#ffffff");
  assert.equal(onAccentColor("#d152d1"), "#0f0f14");
  assert.equal(onAccentColor("#b93ab9"), "#ffffff");
});
