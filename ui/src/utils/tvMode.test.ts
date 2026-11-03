// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import test from "node:test";
import { activeForPreference, loadTvMode, parseTvPreference, readGamingMode, setTvPreference, tvModeActive } from "./tvMode.ts";
import { interfaceScale, uiScaleFactor } from "./interfaceScalePreference.ts";
test("TV preference and Gaming Mode URL", () => {
  assert.equal(parseTvPreference("garbage"), "auto"); assert.equal(parseTvPreference("on"), "on");
  assert.equal(activeForPreference("auto", true), true); assert.equal(activeForPreference("auto", false), false);
  assert.equal(activeForPreference("off", true), false); assert.equal(readGamingMode("?gamingMode=1"), true); assert.equal(readGamingMode(""), false);
});
test("TV mode overrides scale without changing the saved preference", () => {
  const storage = new Map<string, string>();
  Object.defineProperty(globalThis, "localStorage", { configurable: true, value: { getItem: (k: string) => storage.get(k) ?? null, setItem: (k: string, v: string) => storage.set(k, v) } });
  interfaceScale.value = 110;
  loadTvMode("?gamingMode=1"); assert.equal(tvModeActive.value, true); assert.equal(uiScaleFactor(), 1.5); assert.equal(interfaceScale.value, 110);
  setTvPreference("off"); assert.equal(uiScaleFactor(), 1.1); assert.equal(storage.get("wlib-tv-mode"), "off");
  interfaceScale.value = 100; Reflect.deleteProperty(globalThis, "localStorage");
});
