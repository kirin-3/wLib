// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import test from "node:test";
import { actionForKey, CONTROLLER_SETTLE_MS, readController, startControllerInput } from "./controllerInput.ts";
const pad = (pressed: number[] = [], axes = [0, 0, 0, 0], mapping = "standard") => ({ mapping, axes, buttons: Array.from({ length: 16 }, (_, i) => ({ pressed: pressed.includes(i), value: pressed.includes(i) ? 1 : 0, touched: false })) });
test("standard buttons map to actions without repeat", () => {
  const first = readController([pad([0, 1, 2, 3, 4, 5, 6, 7, 8])], true, 0, {});
  assert.deepEqual(first.actions.map(a => a.type), ["activate", "back", "play", "search", "prevTab", "nextTab", "pageUp", "pageDown", "menu"]);
  assert.deepEqual(readController([pad([0])], true, 500, first.state).actions, []);
});
test("directions repeat after 400ms and then every 90ms", () => {
  const p = pad([12], [0.6, 0, 0, -0.8]);
  const first = readController([p], true, 0, {});
  assert.deepEqual(first.actions, [{ type: "move", direction: "up" }, { type: "move", direction: "right" }, { type: "scroll", x: 0, y: -0.8 }]);
  assert.equal(readController([p], true, 399, first.state).actions.length, 1);
  const repeat = readController([p], true, 400, first.state);
  assert.equal(repeat.actions.length, 3);
  assert.equal(readController([p], true, 489, repeat.state).actions.length, 1);
  assert.equal(readController([p], true, 490, repeat.state).actions.length, 3);
});
test("ignores unfocused and nonstandard pads", () => {
  assert.deepEqual(readController([pad([0])], false, 0, {}).actions, []);
  assert.deepEqual(readController([pad([0], [], "")], true, 0, {}).actions, []);
});
test("controls held while unfocused do not fire until released", () => {
  const away = readController([pad([0, 13])], false, 0, {});
  assert.deepEqual(readController([pad([0, 13])], true, 1000, away.state).actions, []);
  const released = readController([pad()], true, 1100, away.state);
  assert.deepEqual(readController([pad([0])], true, 1200, released.state).actions, [{ type: "activate" }]);
});
type Kind = "none" | "menu" | "text" | "number" | "select" | "tablist";
const event = (key: string, kind: Kind = "none", prevented = false) => {
  const element = { matches: (selector: string) => kind !== "number" || !selector.includes("number") };
  const closest = (selector: string) => {
    const hit = kind === "menu" ? selector.includes('role="menu"') : kind === "text" || kind === "number" ? selector.includes("textarea") : kind === "select" ? selector === "select" : kind === "tablist" ? selector.includes("tablist") : false;
    return hit ? element : null;
  };
  return { key, defaultPrevented: prevented, target: { closest } } as unknown as KeyboardEvent;
};
test("keyboard maps navigation keys and respects handled events and menus", () => {
  assert.deepEqual(actionForKey(event("ArrowDown")), { type: "move", direction: "down" });
  assert.deepEqual(actionForKey(event("Escape")), { type: "back" });
  assert.equal(actionForKey(event("ArrowDown", "menu")), null);
  assert.equal(actionForKey(event("Escape", "none", true)), null);
});
test("text fields keep typing keys but let Up and Down leave single-line fields", () => {
  assert.equal(actionForKey(event("ArrowLeft", "text")), null);
  assert.equal(actionForKey(event("Escape", "text")), null);
  assert.deepEqual(actionForKey(event("ArrowDown", "text")), { type: "move", direction: "down" });
  assert.equal(actionForKey(event("ArrowUp", "number")), null);
});
test("selects move with arrows and open with Enter", () => {
  assert.deepEqual(actionForKey(event("ArrowDown", "select")), { type: "move", direction: "down" });
  assert.deepEqual(actionForKey(event("Enter", "select")), { type: "activate" });
});
test("tab strips keep Left and Right but not Up and Down", () => {
  assert.equal(actionForKey(event("ArrowRight", "tablist")), null);
  assert.deepEqual(actionForKey(event("ArrowDown", "tablist")), { type: "move", direction: "down" });
});
test("listens for gamepads only while focused and ignores controls held across focus", () => {
  const g = globalThis as Record<string, unknown>;
  const saved = ["window", "document", "navigator", "requestAnimationFrame", "cancelAnimationFrame", "performance"].map(k => [k, Object.getOwnPropertyDescriptor(g, k)] as const);
  const win = new EventTarget(), doc = new EventTarget();
  let focused = true, now = 0, pads: ReturnType<typeof pad>[] = [];
  const frames = new Map<number, () => void>(); let nextFrame = 1;
  const define = (key: string, value: unknown) => Object.defineProperty(g, key, { value, configurable: true, writable: true });
  define("window", win);
  define("document", Object.assign(doc, { hasFocus: () => focused }));
  define("navigator", { getGamepads: () => pads });
  define("requestAnimationFrame", (fn: () => void) => { const id = nextFrame++; frames.set(id, fn); return id; });
  define("cancelAnimationFrame", (id: number) => frames.delete(id));
  define("performance", { now: () => now });
  const runFrames = () => { const due = [...frames.values()]; frames.clear(); due.forEach(fn => fn()); };
  const actions: string[] = [];
  try {
    const stop = startControllerInput(action => actions.push(action.type));
    assert.equal(frames.size, 1, "starts a short settle poll on startup");
    now = CONTROLLER_SETTLE_MS; runFrames();
    assert.equal(frames.size, 0, "no pads: polling stops after settling");
    focused = false; win.dispatchEvent(new Event("blur"));
    win.dispatchEvent(new Event("gamepadconnected"));
    assert.equal(frames.size, 0, "unfocused: the gamepadconnected listener is gone");
    pads = [pad([0])];
    focused = true; now = 1000; win.dispatchEvent(new Event("focus"));
    runFrames(); now = 1000 + CONTROLLER_SETTLE_MS; runFrames();
    assert.deepEqual(actions, [], "a button held while focus returns does not fire");
    pads = [pad()]; now += 16; runFrames();
    pads = [pad([0])]; now += 16; runFrames();
    assert.deepEqual(actions, ["activate"]);
    stop();
    win.dispatchEvent(new Event("gamepadconnected"));
    assert.equal(frames.size, 0, "stopped: nothing polls and nothing listens");
  } finally {
    for (const [key, descriptor] of saved) { if (descriptor) Object.defineProperty(g, key, descriptor); else delete g[key]; }
  }
});
