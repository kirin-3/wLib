// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import test from "node:test";

import { dismissToast, notify, toasts } from "./toast.ts";

test("action toasts keep their action until dismissed", () => {
  const before = toasts.length;
  let ran = false;
  notify("Eternum 0.8 is waiting in Downloads.", "info", "Update ready", {
    label: "Install update",
    run: () => {
      ran = true;
    },
  });
  const toast = toasts[toasts.length - 1];
  assert.equal(toasts.length, before + 1);
  assert.equal(toast?.action?.label, "Install update");
  toast?.action?.run();
  assert.equal(ran, true);
  // Plain toasts carry no action.
  notify("Plain message.", "info");
  assert.equal(toasts[toasts.length - 1]?.action, undefined);
  if (toast) dismissToast(toast.id);
});

test("dismissToast removes only the matching toast", () => {
  notify("First", "info");
  notify("Second", "info");
  const first = toasts[0];
  const second = toasts[toasts.length - 1];
  if (first) dismissToast(first.id);
  assert.ok(!toasts.some((toast) => toast.id === first?.id));
  assert.ok(toasts.some((toast) => toast.id === second?.id));
  if (second) dismissToast(second.id);
});

test("undo expiry pauses while held and restarts on release", async (context) => {
  const { notifyUndo, setUndoHeld } = await import("./toast.ts");
  context.mock.timers.enable({ apis: ["setTimeout"] });
  let expired = 0;
  notifyUndo("Changed", async () => {}, { onExpire: () => { expired++; } });
  const toast = toasts[toasts.length - 1]!;
  context.mock.timers.tick(7000);
  setUndoHeld(toast.id, true);
  context.mock.timers.tick(10000);
  assert.ok(toasts.some((item) => item.id === toast.id));
  setUndoHeld(toast.id, false);
  context.mock.timers.tick(7999);
  assert.equal(expired, 0);
  context.mock.timers.tick(1);
  assert.equal(expired, 1);
  dismissToast(toast.id);
  assert.equal(expired, 1);
});

test("close expires undo, while undo runs without expiring", async (context) => {
  const { notifyUndo, runNewestUndo } = await import("./toast.ts");
  context.mock.timers.enable({ apis: ["setTimeout"] });
  let expired = 0;
  let undone = 0;
  notifyUndo("First", async () => { undone++; }, { onExpire: () => { expired++; } });
  const first = toasts[toasts.length - 1]!;
  notifyUndo("Second", async () => { undone++; }, { onExpire: () => { expired++; } });
  await runNewestUndo();
  assert.equal(undone, 1);
  assert.equal(expired, 0);
  dismissToast(first.id);
  context.mock.timers.tick(8000);
  assert.equal(expired, 1);
});

test("the same error twice gives one toast", () => {
  const before = toasts.length;
  const first = notify("Sync failed.", "error", "Sync needs attention");
  const second = notify("Sync failed.", "error", "Sync needs attention");
  assert.notEqual(first, second);
  assert.equal(toasts.length, before + 1);
  assert.equal(toasts[toasts.length - 1]?.id, second);
  dismissToast(second);
});

test("a repeated info toast restarts its timer", (context) => {
  context.mock.timers.enable({ apis: ["setTimeout"] });
  const before = toasts.length;
  notify("Saved.", "info");
  context.mock.timers.tick(3000);
  const second = notify("Saved.", "info");
  assert.equal(toasts.length, before + 1);
  context.mock.timers.tick(2000);
  assert.ok(toasts.some((toast) => toast.id === second));
  context.mock.timers.tick(3000);
  assert.ok(!toasts.some((toast) => toast.id === second));
  assert.equal(toasts.length, before);
});

test("the same title with different text gives two toasts", () => {
  const before = toasts.length;
  const first = notify("One.", "error", "Sync");
  const second = notify("Two.", "error", "Sync");
  assert.equal(toasts.length, before + 2);
  dismissToast(first);
  dismissToast(second);
});

test("two Undo toasts with the same text both stay", async (context) => {
  const { notifyUndo } = await import("./toast.ts");
  context.mock.timers.enable({ apis: ["setTimeout"] });
  const before = toasts.length;
  notifyUndo("Removed", async () => {});
  notifyUndo("Removed", async () => {});
  assert.equal(toasts.length, before + 2);
  notify("Removed", "success");
  assert.equal(toasts.length, before + 3);
  for (const toast of toasts.slice(before)) dismissToast(toast.id);
});
