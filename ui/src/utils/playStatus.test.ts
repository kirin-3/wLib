import test from "node:test";
import assert from "node:assert/strict";
import { getPlayStatusMeta, getPlayStatusOptions, normalizePlayStatus, validateCustomPlayStatuses } from "./playStatus.ts";

test("custom status names that normalize to a built-in status are rejected", () => {
  assert.equal(validateCustomPlayStatuses(["Backlog"]), "");
  assert.match(validateCustomPlayStatuses(["Replaying"]), /reserved/);
  assert.match(validateCustomPlayStatuses(["in_progress"]), /reserved/);
  assert.match(validateCustomPlayStatuses(["playing"]), /already exists/);
});

test("custom statuses survive legacy fallback and share a presentation", () => {
  assert.equal(normalizePlayStatus("  Backlog  ", "completed"), "Backlog");
  assert.equal(normalizePlayStatus("x".repeat(50)), "x".repeat(40));
  assert.equal(normalizePlayStatus(""), "Not Started");
  assert.equal(normalizePlayStatus("playing"), "Playing");
  assert.equal(normalizePlayStatus("On Hold", "waiting_update"), "Waiting For Update");
  assert.equal(getPlayStatusMeta("Backlog").toneClass, "ui-status-tone-custom");
  assert.equal(getPlayStatusMeta("Backlog").icon, getPlayStatusMeta("Replay").icon);
  assert.equal(normalizePlayStatus("constructor"), "constructor");
  assert.equal(getPlayStatusMeta("__proto__").toneClass, "ui-status-tone-custom");
  assert.deepEqual(getPlayStatusOptions(["Backlog", "backlog", "Playing", "Replay"]).slice(7).map((option) => option.value), ["Backlog", "Replay"]);
});
