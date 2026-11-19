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
  assert.doesNotThrow(() => getPlayStatusOptions(undefined as unknown as string[]));
  assert.equal(validateCustomPlayStatuses([null as unknown as string, 123 as unknown as string]), "Play status names must contain 1–40 characters.");
});

test("the former Abandoned status reads as Archived with the archive icon", async () => {
  const { IconArchive } = await import("@tabler/icons-vue");
  assert.equal(normalizePlayStatus("abandoned"), "Archived");
  assert.equal(normalizePlayStatus("Abandoned"), "Archived");
  assert.equal(normalizePlayStatus("archived"), "Archived");
  assert.equal(normalizePlayStatus("On Hold", "abandoned"), "Archived");
  const meta = getPlayStatusMeta("Abandoned");
  assert.equal(meta.value, "Archived");
  assert.equal(meta.icon, IconArchive);
  assert.equal(meta.toneClass, "ui-status-tone-archived");
  assert.equal(validateCustomPlayStatuses(["abandoned"]), "Play status 'abandoned' is reserved.");
});
