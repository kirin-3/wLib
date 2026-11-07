// SPDX-License-Identifier: GPL-3.0-or-later
import test from "node:test";
import assert from "node:assert/strict";
import { applyPlaytimeTick, refreshRunningGames, runningGameIds } from "./useRunningGames.ts";

test("ticks add unknown games and final ticks remove them", () => {
  runningGameIds.value = new Set();
  applyPlaytimeTick({ gameId: 4, isFinal: false });
  assert.deepEqual([...runningGameIds.value], [4]);
  applyPlaytimeTick({ gameId: 4, isFinal: false });
  assert.equal(runningGameIds.value.size, 1);
  applyPlaytimeTick({ gameId: "x" });
  applyPlaytimeTick(null);
  assert.equal(runningGameIds.value.size, 1);
  applyPlaytimeTick({ gameId: 4, isFinal: true });
  assert.equal(runningGameIds.value.size, 0);
});

test("refresh replaces the set, ignores errors, and a final tick beats a stale answer", async () => {
  await refreshRunningGames(async () => [1, 2]);
  assert.deepEqual([...runningGameIds.value].sort(), [1, 2]);
  const originalError = console.error;
  console.error = () => {};
  await refreshRunningGames(async () => { throw new Error("offline"); });
  console.error = originalError;
  assert.equal(runningGameIds.value.size, 2);
  const pending = refreshRunningGames(async () => [1, 2]);
  applyPlaytimeTick({ gameId: 2, isFinal: true });
  await pending;
  assert.deepEqual([...runningGameIds.value], [1]);
});
