// SPDX-License-Identifier: GPL-3.0-or-later
import { test } from "node:test";
import assert from "node:assert/strict";
import { useSaveStatus } from "./useSaveStatus.ts";

test("shows saving, then saved, then goes back to idle", async () => {
  const status = useSaveStatus(10);
  let release!: () => void;
  const pending = status.run(() => new Promise<void>(resolve => { release = resolve; }));
  assert.equal(status.state.value, "saving");
  release();
  assert.equal(await pending, true);
  assert.equal(status.state.value, "saved");
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.equal(status.state.value, "idle");
});

test("a failed save shows a readable reason and returns false", async () => {
  const status = useSaveStatus();
  assert.equal(await status.run(async () => { throw new Error("Error: database locked"); }), false);
  assert.equal(status.state.value, "error");
  assert.equal(status.error.value, "Database locked.");
  status.clear();
  assert.equal(status.state.value, "idle");
  assert.equal(status.error.value, "");
});

test("an older save cannot replace the state of a newer one", async () => {
  const status = useSaveStatus();
  let failOld!: (error: Error) => void;
  const old = status.run(() => new Promise<void>((_resolve, reject) => { failOld = reject; }));
  const newer = status.run(async () => {});
  assert.equal(await newer, true);
  failOld(new Error("late failure"));
  assert.equal(await old, false);
  assert.equal(status.state.value, "saved");
  assert.equal(status.error.value, "");
});
