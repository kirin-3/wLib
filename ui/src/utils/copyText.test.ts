// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import test from "node:test";
import { copyText } from "./copyText.ts";

test("clipboard is preferred, legacy copy follows rejection, false reveals manual fallback", async () => {
  const originalNavigator = Object.getOwnPropertyDescriptor(globalThis, "navigator");
  const originalDocument = Object.getOwnPropertyDescriptor(globalThis, "document");
  const calls: string[] = [];
  let clipboardFails = false, clipboardPending = false, legacySucceeds = true;
  const textarea = { value: "", readOnly: false, style: { cssText: "" }, select: () => calls.push("select"), remove: () => calls.push("remove") };
  Object.defineProperty(globalThis, "navigator", { configurable: true, value: { clipboard: { writeText: async (text: string) => { calls.push(`clipboard:${text}`); if (clipboardPending) await new Promise(() => {}); if (clipboardFails) throw new Error("denied"); } } } });
  Object.defineProperty(globalThis, "document", { configurable: true, value: {
    activeElement: { focus: () => calls.push("focus") },
    createElement: () => textarea,
    body: { appendChild: () => calls.push("append") },
    execCommand: (command: string) => { calls.push(command); return legacySucceeds; },
  } });
  try {
    assert.equal(await copyText("report"), true);
    assert.deepEqual(calls, ["clipboard:report"]);
    clipboardFails = true; calls.length = 0;
    assert.equal(await copyText("report"), true);
    assert.deepEqual(calls, ["clipboard:report", "append", "select", "copy", "remove", "focus"]);
    assert.equal(textarea.value, "report");
    legacySucceeds = false;
    assert.equal(await copyText("report"), false);
    clipboardPending = true;
    legacySucceeds = true;
    calls.length = 0;
    assert.equal(await copyText("report", 0), true);
    assert.deepEqual(calls, ["clipboard:report", "append", "select", "copy", "remove", "focus"]);
  } finally {
    if (originalNavigator) Object.defineProperty(globalThis, "navigator", originalNavigator);
    else Reflect.deleteProperty(globalThis, "navigator");
    if (originalDocument) Object.defineProperty(globalThis, "document", originalDocument);
    else Reflect.deleteProperty(globalThis, "document");
  }
});
