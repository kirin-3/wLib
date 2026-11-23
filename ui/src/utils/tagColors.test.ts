// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test, { after } from "node:test";
import { createServer } from "vite";
const originalWindow = Object.getOwnPropertyDescriptor(globalThis, "window");
Object.defineProperty(globalThis, "window", { configurable: true, value: { addEventListener() {} } });
const server = await createServer({ server: { middlewareMode: true, hmr: false }, appType: "custom" });
const { createTagColors, normalizeTagName, TAG_COLOR_SWATCHES } = await server.ssrLoadModule("/src/utils/tagColors.ts");
after(async () => {
  await server.close();
  if (originalWindow) Object.defineProperty(globalThis, "window", originalWindow);
  else Reflect.deleteProperty(globalThis, "window");
});

function luminance(hex: string) {
  const channels = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map(c => c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  return channels[0]! * 0.2126 + channels[1]! * 0.7152 + channels[2]! * 0.0722;
}
test("every swatch has AA text contrast in both themes", () => {
  const css = readFileSync(new URL("../style.css", import.meta.url), "utf8");
  for (const theme of [":root", ".light"]) {
    const block = css.slice(css.indexOf(`${theme} {`)).split("}")[0]!;
    for (const swatch of TAG_COLOR_SWATCHES) {
      const token = (part: string) => {
        const value = block.match(new RegExp(`--tag-${swatch}-${part}:\\s*(#[0-9a-f]{6})`))?.[1];
        assert.ok(value, `${theme} ${swatch} ${part}`);
        return luminance(value);
      };
      const text = token("text"), bg = token("bg"), border = token("border");
      assert.ok((Math.max(text, bg) + 0.05) / (Math.min(text, bg) + 0.05) >= 4.5, `${theme} ${swatch} text`);
      assert.ok((Math.max(border, bg) + 0.05) / (Math.min(border, bg) + 0.05) >= 3, `${theme} ${swatch} border`);
    }
  }
});
test("normalization, load once, lookup and refresh", async () => {
  assert.equal(normalizeTagName(" Sandbox "), "sandbox");
  let calls = 0;
  const state = createTagColors(async () => ({
    getSettings: async () => { calls++; return { tag_colors: { " Sandbox ": "green", bad: "unknown" } }; },
    setTagColor: async () => ({ success: true }),
  }));
  await state.load(); await state.load();
  assert.equal(calls, 1);
  assert.equal(state.colorForTag("SANDBOX"), "green");
  assert.equal(state.colorForTag("bad"), undefined);
  assert.equal(state.colorForTag("constructor"), undefined);
  await state.load(true);
  assert.equal(calls, 2);
});

test("failed queued saves restore the last confirmed color", async () => {
  const state = createTagColors(async () => ({
    getSettings: async () => ({ tag_colors: { sandbox: "green" } }),
    setTagColor: async () => ({ success: false }),
  }));
  await state.load();
  const first = state.set("sandbox", "blue");
  const second = state.set("sandbox", "pink");
  const results = await Promise.allSettled([first, second]);
  assert.ok(results.every(result => result.status === "rejected"));
  assert.equal(state.colorForTag("sandbox"), "green");
});
test("optimistic save, clear and rollback on failure", async () => {
  let complete: (result: { success: boolean; tag_colors?: Record<string, string> }) => void = () => {};
  const state = createTagColors(async () => ({
    getSettings: async () => ({ tag_colors: { sandbox: "green" } }),
    setTagColor: () => new Promise(resolve => { complete = resolve; }),
  }));
  await state.load();
  const pending = state.set("Sandbox", "blue");
  assert.equal(state.colorForTag("sandbox"), "blue");
  await new Promise(resolve => setTimeout(resolve, 0));
  complete({ success: false });
  await assert.rejects(pending);
  assert.equal(state.colorForTag("sandbox"), "green");
  const clear = state.set("sandbox", null);
  assert.equal(state.colorForTag("sandbox"), undefined);
  await new Promise(resolve => setTimeout(resolve, 0));
  complete({ success: true, tag_colors: {} });
  await clear;
  assert.equal(state.colorForTag("sandbox"), undefined);
});

test("a refresh during a pending save neither undoes the save nor resurrects a cleared color", async () => {
  let stored: Record<string, string> = { sandbox: "green", rpg: "red" };
  const saves: Array<() => void> = [];
  // When set, the next settings read takes its snapshot now and answers later.
  let heldLoad: ((release: () => void) => void) | null = null;
  const state = createTagColors(async () => ({
    getSettings: () => {
      const snapshot = { tag_colors: { ...stored } };
      if (!heldLoad) return Promise.resolve(snapshot);
      const hold = heldLoad;
      heldLoad = null;
      return new Promise(resolve => hold(() => resolve(snapshot)));
    },
    // The save commits only when the test lets it.
    setTagColor: (tag: string, swatch: string | null) => new Promise(resolve => {
      saves.push(() => {
        const next = { ...stored };
        if (swatch) next[tag] = swatch; else delete next[tag];
        stored = next;
        resolve({ success: true, tag_colors: { ...stored } });
      });
    }),
  }));
  const tick = () => new Promise(resolve => setTimeout(resolve, 0));
  await state.load();

  // A library refresh reads and applies settings before the save commits.
  const blue = state.set("sandbox", "blue");
  await tick();
  stored = { ...stored, puzzle: "teal" };
  await state.load(true);
  assert.equal(state.colorForTag("sandbox"), "blue", "the pending edit stays on screen");
  assert.equal(state.colorForTag("puzzle"), "teal", "refreshed colors for other tags apply");
  saves.shift()!();
  await blue;
  assert.equal(state.colorForTag("sandbox"), "blue", "the successful save shows");

  // A refresh that read settings before a clear committed answers after it.
  const clear = state.set("rpg", null);
  await tick();
  let releaseLoad: () => void = () => {};
  heldLoad = release => { releaseLoad = release; };
  const staleLoad = state.load(true);
  await tick();
  saves.shift()!();
  await clear;
  releaseLoad();
  await staleLoad;
  assert.equal(state.colorForTag("rpg"), undefined, "the cleared color is not resurrected");
  assert.equal(state.colorForTag("sandbox"), "blue");
  assert.equal(state.colorForTag("puzzle"), "teal");

  // A newer edit survives an older save that succeeds after a refresh.
  const pink = state.set("sandbox", "pink");
  const purple = state.set("sandbox", "purple");
  await tick();
  await state.load(true);
  saves.shift()!();
  await pink;
  assert.equal(state.colorForTag("sandbox"), "purple", "the newer edit stays");
  await tick();
  saves.shift()!();
  await purple;
  assert.equal(state.colorForTag("sandbox"), "purple");
});
