// SPDX-License-Identifier: GPL-3.0-or-later
import test from "node:test";
import assert from "node:assert/strict";

import {
  shouldAdoptBackgroundCheck,
  shouldScanDownloadsAfterCheck,
  updateCheckProgressLabel,
} from "./updateCheck.ts";

test("finished update checks only show Complete when they actually completed", () => {
  assert.equal(updateCheckProgressLabel(true, "running"), "");
  assert.equal(updateCheckProgressLabel(false, "completed"), "✓ Complete");
  assert.equal(updateCheckProgressLabel(false, "cancelled"), "Stopped");
  assert.equal(updateCheckProgressLabel(false, "failed"), "Failed");
  assert.equal(updateCheckProgressLabel(false, "idle"), "✓ Complete");
  assert.equal(updateCheckProgressLabel(false, undefined), "✓ Complete");
});

test("a background check is adopted unless the view is already polling", () => {
  assert.equal(
    shouldAdoptBackgroundCheck(false, { running: true, checked: 0, outcome: "running" }),
    true,
  );
  // A short check can finish between the view's own status reads.
  assert.equal(
    shouldAdoptBackgroundCheck(false, { running: false, checked: 3, outcome: "completed" }),
    true,
  );
  assert.equal(
    shouldAdoptBackgroundCheck(true, { running: true, checked: 1, outcome: "running" }),
    false,
  );
  assert.equal(
    shouldAdoptBackgroundCheck(false, { running: false, checked: 0, outcome: "idle" }),
    false,
  );
});

test("download offers are scanned for a finished check even if running was missed", () => {
  const finished = { running: false, checked: 2, outcome: "completed" };
  // The normal case: the poll saw the check running, then saw it finish.
  assert.equal(shouldScanDownloadsAfterCheck(finished, true, true), true);
  // A short check started and finished between polls; the results still changed.
  assert.equal(shouldScanDownloadsAfterCheck(finished, false, true), true);
  // Nothing new to scan for once that status has been handled.
  assert.equal(shouldScanDownloadsAfterCheck(finished, false, false), false);
  // A running check or an idle/empty one is not a finished check.
  assert.equal(
    shouldScanDownloadsAfterCheck({ running: true, checked: 1, outcome: "running" }, false, true),
    false,
  );
  assert.equal(
    shouldScanDownloadsAfterCheck({ running: false, checked: 0, outcome: "idle" }, true, true),
    false,
  );
});
