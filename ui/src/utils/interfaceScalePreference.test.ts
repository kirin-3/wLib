// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import test from "node:test";
import { INTERFACE_SCALE_OPTIONS, interfaceScale, normalizeInterfaceScale, toCssPx, uiScaleFactor, loadInterfaceScale, setInterfaceScale, INTERFACE_SCALE_STORAGE_KEY } from "./interfaceScalePreference.ts";

test("interface scale accepts supported percentages only", () => {
  for (const value of INTERFACE_SCALE_OPTIONS) {
    assert.equal(normalizeInterfaceScale(value), value);
    assert.equal(normalizeInterfaceScale(String(value)), value);
  }
  for (const value of [null, undefined, "", "75", "155", "105", "abc", "1.2", {}, true]) {
    assert.equal(normalizeInterfaceScale(value), 100);
  }
});

test("viewport coordinates convert to CSS pixels at every scale", () => {
  try {
    for (const value of INTERFACE_SCALE_OPTIONS) {
      interfaceScale.value = value;
      assert.equal(uiScaleFactor(), value / 100);
      assert.equal(toCssPx(300), 300 / (value / 100));
      assert.equal(toCssPx(0), 0);
    }
  } finally { interfaceScale.value = 100; }
});


test("scale applies to the root, persists, and falls back when storage is unreadable", () => {
  const oldStorage = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
  const oldDocument = Object.getOwnPropertyDescriptor(globalThis, "document");
  const warn = console.warn;
  const values = new Map<string, string>();
  const properties = new Map<string, string>();
  try {
    console.warn = () => {};
    Object.defineProperty(globalThis, "localStorage", { configurable: true, value: {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
    } });
    Object.defineProperty(globalThis, "document", { configurable: true, value: {
      documentElement: { style: { setProperty: (key: string, value: string) => properties.set(key, value) } },
    } });
    setInterfaceScale(130);
    assert.equal(values.get(INTERFACE_SCALE_STORAGE_KEY), "130");
    assert.equal(properties.get("--ui-scale"), "1.3");
    values.set(INTERFACE_SCALE_STORAGE_KEY, "120");
    loadInterfaceScale();
    assert.equal(interfaceScale.value, 120);
    assert.equal(properties.get("--ui-scale"), "1.2");
    Object.defineProperty(globalThis, "localStorage", { configurable: true, get: () => { throw new Error("Unavailable"); } });
    loadInterfaceScale();
    assert.equal(interfaceScale.value, 100);
    assert.equal(properties.get("--ui-scale"), "1");
    setInterfaceScale(150);
    assert.equal(properties.get("--ui-scale"), "1.5");
  } finally {
    if (oldStorage) Object.defineProperty(globalThis, "localStorage", oldStorage);
    else Reflect.deleteProperty(globalThis, "localStorage");
    if (oldDocument) Object.defineProperty(globalThis, "document", oldDocument);
    else Reflect.deleteProperty(globalThis, "document");
    console.warn = warn;
    interfaceScale.value = 100;
  }
});
