// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import test from "node:test";

import type { LibrarySyncState } from "../services/api.ts";
import { trackSyncProblem } from "./syncProblem.ts";

const sequence = (states: [LibrarySyncState, string?][]) => {
  let key = "";
  return states.map(([state, lastError]) => {
    const update = trackSyncProblem(key, { success: true, state, last_error: lastError ?? "" });
    key = update.key;
    return update;
  });
};

test("a retry of the same error shows one toast", () => {
  const updates = sequence([["error", "Gone"], ["syncing"], ["error", "Gone"], ["syncing"], ["error", "Gone"]]);
  assert.deepEqual(updates.map((update) => update.show), ["Gone", undefined, undefined, undefined, undefined]);
});

test("syncing keeps attention", () => {
  const updates = sequence([["error", "Gone"], ["syncing"]]);
  assert.equal(updates[1]?.attention, true);
  assert.equal(updates[1]?.close, false);
  assert.equal(sequence([["idle"], ["syncing"]])[1]?.attention, false);
});

test("recovery closes the toast and the same error then shows again", () => {
  const updates = sequence([["error", "Gone"], ["idle"], ["error", "Gone"]]);
  assert.equal(updates[1]?.close, true);
  assert.equal(updates[1]?.attention, false);
  assert.equal(updates[2]?.show, "Gone");
});

test("a different error shows a new toast", () => {
  const updates = sequence([["error", "Gone"], ["error", "Full"]]);
  assert.equal(updates[1]?.show, "Full");
});

test("offline and paused show no toast and no attention", () => {
  for (const state of ["offline", "paused"] as const) {
    const updates = sequence([["error", "Gone"], [state, "The sync folder is unavailable."]]);
    assert.equal(updates[1]?.show, undefined);
    assert.equal(updates[1]?.attention, false);
    assert.equal(updates[1]?.close, true);
  }
});

test("choice and passphrase states have their own text", () => {
  assert.equal(sequence([["needs_choice"]])[0]?.show, "Sync is waiting for you to choose which library to keep.");
  assert.equal(sequence([["needs_passphrase"]])[0]?.show, "Sync needs the passphrase for the sync file.");
  assert.equal(sequence([["error"]])[0]?.show, "Sync needs attention.");
});
