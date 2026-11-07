// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import test from "node:test";
import { confirmAction, confirmChoice, confirmQueue, resolveConfirmation } from "./confirm.ts";

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

test("a confirmation with a checkbox returns the answer and the checkbox", async () => {
  const asked = confirmChoice({ title: "Seed?", message: "Keep seeding?", confirmLabel: "Seed", cancelLabel: "Don't seed", checkboxLabel: "Don't ask again" });
  assert.equal(confirmQueue[0]?.cancelLabel, "Don't seed");
  resolveConfirmation(false, true);
  assert.deepEqual(await asked, { confirmed: false, checked: true });
  const plain = confirmChoice({ title: "Sure?", message: "m", confirmLabel: "Yes" });
  resolveConfirmation(true, true);
  assert.deepEqual(await plain, { confirmed: true, checked: false });
});
