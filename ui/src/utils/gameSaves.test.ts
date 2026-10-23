// SPDX-License-Identifier: GPL-3.0-or-later
import test from "node:test";
import assert from "node:assert/strict";

import { formatBackupLabel, getSaveActionState } from "./gameSaves.ts";

const base = { effective: "/saves", effectiveExists: true, eligibleDetected: 1, detected: 1, busy: false };

test("all actions are available for an existing saves folder", () => {
  assert.deepEqual(getSaveActionState(base), {
    canOpen: true,
    canBackup: true,
    canImport: true,
    canRestore: true,
    message: "",
  });
});

test("busy disables every action", () => {
  const state = getSaveActionState({ ...base, busy: true });
  assert.deepEqual([state.canOpen, state.canBackup, state.canImport, state.canRestore], [false, false, false, false]);
  assert.match(state.message, /running/);
});

test("without an effective folder the message tells the user what to do", () => {
  const several = getSaveActionState({ ...base, effective: "", effectiveExists: false, eligibleDetected: 3, detected: 3 });
  assert.equal(several.canBackup || several.canImport, false);
  assert.match(several.message, /Several folders/);
  const nothing = getSaveActionState({ ...base, effective: "", effectiveExists: false, eligibleDetected: 0, detected: 0 });
  assert.match(nothing.message, /No saves folder/);
  // Only the game's own folder was found: it is listed, so don't claim nothing was found.
  const leadOnly = getSaveActionState({ ...base, effective: "", effectiveExists: false, eligibleDetected: 0, detected: 1 });
  assert.equal(leadOnly.canBackup || leadOnly.canImport, false);
  assert.match(leadOnly.message, /Use this/);
});

test("a folder that does not exist yet can only be imported into", () => {
  const state = getSaveActionState({ ...base, effectiveExists: false });
  assert.deepEqual(
    [state.canOpen, state.canBackup, state.canImport, state.canRestore],
    [false, false, true, true],
  );
  assert.match(state.message, /does not exist/);
});

test("backup names become readable labels", () => {
  assert.equal(formatBackupLabel("2026-10-23_12-00-05"), "2026-10-23 12:00:05");
  assert.equal(formatBackupLabel("2026-10-23_12-00-05-1"), "2026-10-23 12:00:05 (2)");
  assert.equal(formatBackupLabel("notes"), "notes");
});
