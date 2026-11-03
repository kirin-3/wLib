// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import test from "node:test";
import { scheduleRemoval, isPendingRemoval, settleRemovals } from "./pendingRemovals.ts";
import { runNewestUndo, toasts, dismissToast } from "./toast.ts";
const game = { id: 42, title: "Test", exe_path: "" };
test("undo cancels removal before deletion", async () => {
  let deletes = 0;
  scheduleRemoval([game], async () => { deletes++; return { success: true }; });
  assert.equal(isPendingRemoval(42), true);
  await runNewestUndo();
  assert.equal(isPendingRemoval(42), false);
  assert.equal(deletes, 0);
});
test("expiry deletes and failure returns the game", async (context) => {
  context.mock.timers.enable({ apis: ["setTimeout"] });
  let deletes = 0;
  scheduleRemoval([game], async () => { deletes++; return { success: false }; });
  context.mock.timers.tick(8000);
  await new Promise<void>((resolve) => queueMicrotask(resolve));
  await new Promise<void>((resolve) => queueMicrotask(resolve));
  assert.equal(deletes, 1);
  assert.equal(isPendingRemoval(42), false);
  assert.ok(toasts.some((toast) => toast.type === "error" && toast.message.includes("Could not remove")));
  for (const toast of [...toasts]) dismissToast(toast.id);
});

test("expiry commits a successful removal", async (context) => {
  context.mock.timers.enable({ apis: ["setTimeout"] });
  const deleted: number[] = [];
  scheduleRemoval([game], async (id) => { deleted.push(id); return { success: true }; });
  context.mock.timers.tick(8000);
  await new Promise<void>((resolve) => queueMicrotask(resolve));
  await new Promise<void>((resolve) => queueMicrotask(resolve));
  assert.deepEqual(deleted, [42]);
  // A list fetched before the delete still has the game; keep it hidden.
  settleRemovals([41, 42]);
  assert.equal(isPendingRemoval(42), true);
  settleRemovals([41]);
  assert.equal(isPendingRemoval(42), false);
  for (const toast of [...toasts]) dismissToast(toast.id);
});
