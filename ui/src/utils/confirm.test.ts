// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import test from "node:test";
import { confirmAction, confirmQueue, resolveConfirmation } from "./confirm.ts";

test("confirmation requests resolve in FIFO order", async () => {
  const first = confirmAction({ title: "First", message: "One", confirmLabel: "Delete" });
  const second = confirmAction({ title: "Second", message: "Two", confirmLabel: "Discard", danger: true });
  assert.equal(confirmQueue[0]?.title, "First");
  assert.equal(confirmQueue.length, 2);
  resolveConfirmation(false);
  assert.equal(await first, false);
  assert.equal(confirmQueue[0]?.title, "Second");
  resolveConfirmation(true);
  assert.equal(await second, true);
  assert.equal(confirmQueue.length, 0);
});
