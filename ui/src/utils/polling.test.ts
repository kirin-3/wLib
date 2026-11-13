// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import test from "node:test";
import { INACTIVE_POLL_MS, pollDelay, startPoller } from "./polling.ts";

test("polls slow down only while the window is inactive", () => {
  assert.equal(pollDelay(5000, true), 5000);
  assert.equal(pollDelay(5000, false), INACTIVE_POLL_MS);
  assert.equal(pollDelay(60_000, false), 60_000);
});

test("the poller waits between runs, runs now on request, and stops", (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let runs = 0, active = true;
  const poller = startPoller(() => { runs++; }, () => 5000, () => active);
  t.mock.timers.tick(4999);
  assert.equal(runs, 0);
  t.mock.timers.tick(1);
  assert.equal(runs, 1);
  return Promise.resolve().then(() => {
    active = false;
    t.mock.timers.tick(5000);
    assert.equal(runs, 2, "the delay is chosen when the wait starts");
  }).then(() => new Promise<void>((resolve) => setImmediate(resolve))).then(() => {
    t.mock.timers.tick(INACTIVE_POLL_MS - 1);
    assert.equal(runs, 2, "inactive: waits the long delay");
    poller.runNow();
    assert.equal(runs, 3, "runNow skips the wait");
    poller.stop();
    t.mock.timers.tick(INACTIVE_POLL_MS * 2);
    assert.equal(runs, 3);
  });
});

test("runNow does not start a second run while one is in flight", async () => {
  let runs = 0;
  let finish: () => void = () => {};
  const poller = startPoller(() => { runs++; return new Promise<void>((resolve) => { finish = resolve; }); }, () => 60_000, () => true);
  poller.runNow();
  poller.runNow();
  assert.equal(runs, 1);
  finish();
  poller.stop();
});
