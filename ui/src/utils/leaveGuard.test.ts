// SPDX-License-Identifier: GPL-3.0-or-later
import test from "node:test";
import assert from "node:assert/strict";
import { addLeaveGuard, confirmLeave } from "./leaveGuard.ts";

test("leaving waits for every guard and stops at a refusal", async () => {
  assert.equal(await confirmLeave(), true);
  let allow = false;
  const remove = addLeaveGuard(async () => allow);
  assert.equal(await confirmLeave(), false);
  allow = true;
  assert.equal(await confirmLeave(), true);
  allow = false;
  remove();
  assert.equal(await confirmLeave(), true);
});
