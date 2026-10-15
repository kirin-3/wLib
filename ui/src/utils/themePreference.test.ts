// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import test from "node:test";

// themePreference.ts touches the DOM, matchMedia and localStorage inside its
// helpers, so the shims have to exist before the module is imported.
const store = new Map<string, string>();
const rootClasses = new Set<string>();
const rootProperties = new Map<string, string>();
const windowListeners = new Map<string, Set<() => void>>();
const systemListeners = new Set<() => void>();
let systemPrefersLight = true;

globalThis.localStorage = {
  getItem: (key: string) => (store.has(key) ? store.get(key)! : null),
  setItem: (key: string, value: string) => void store.set(key, String(value)),
  removeItem: (key: string) => void store.delete(key),
} as unknown as Storage;

globalThis.document = {
  // Vue's runtime-dom probes `document.createElement("template")` on import.
  createElement: () => ({ content: null, setAttribute() {}, style: {} }),
  createElementNS: () => ({ setAttribute() {}, style: {} }),
  documentElement: {
    classList: {
      toggle: (name: string, on: boolean) => (on ? rootClasses.add(name) : rootClasses.delete(name)),
      contains: (name: string) => rootClasses.has(name),
    },
    style: {
      removeProperty: (name: string) => void rootProperties.delete(name),
      setProperty: (name: string, value: string) => void rootProperties.set(name, value),
    },
    setAttribute: () => {},
    removeAttribute: () => {},
  },
} as unknown as Document;

globalThis.window = {
  matchMedia: () => ({
    get matches() {
      return systemPrefersLight;
    },
    addEventListener: (_type: string, fn: () => void) => void systemListeners.add(fn),
  }),
  addEventListener: (type: string, fn: () => void) => {
    const listeners = windowListeners.get(type) ?? new Set<() => void>();
    listeners.add(fn);
    windowListeners.set(type, listeners);
  },
} as unknown as Window & typeof globalThis;

const { setThemePreference, loadThemePreference, normalizeThemePreference } = await import(
  "./themePreference.ts"
);

const reset = () => {
  store.clear();
  rootClasses.clear();
  rootProperties.clear();
};

test("normalizeThemePreference keeps known values and falls back to system", () => {
  assert.equal(normalizeThemePreference("light"), "light");
  assert.equal(normalizeThemePreference("dark"), "dark");
  assert.equal(normalizeThemePreference("system"), "system");
  for (const bad of [null, undefined, "Light", "DARK", "", "sepia"]) {
    assert.equal(normalizeThemePreference(bad), "system");
  }
});

test("a theme choice persists and toggles the light root class", () => {
  reset();

  setThemePreference("light");
  assert.equal(store.get("wlib-theme"), "light");
  assert.equal(rootClasses.has("light"), true);

  setThemePreference("dark");
  assert.equal(store.get("wlib-theme"), "dark");
  assert.equal(rootClasses.has("light"), false);
});

test("loadThemePreference restores the saved choice and tracks the system", () => {
  reset();
  store.set("wlib-theme", "light");

  loadThemePreference();
  assert.equal(store.get("wlib-theme"), "light");
  assert.equal(rootClasses.has("light"), true);
  // The system change listener is registered once per load.
  assert.equal(systemListeners.size, 1);

  reset();
  loadThemePreference();
  assert.equal(store.has("wlib-theme"), false);
  // No saved choice: the OS preference (light) wins.
  assert.equal(rootClasses.has("light"), true);

  systemPrefersLight = false;
  for (const listener of systemListeners) listener();
  assert.equal(rootClasses.has("light"), false);
});
