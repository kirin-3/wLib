import { strict as assert } from "node:assert";
import { test } from "node:test";
import { showSteamGameControl, steamIneligibleNote, steamStateLabel } from "./steam.ts";

test("Steam shortcut states have clear labels", () => {
  assert.equal(steamStateLabel("linked"), "In Steam");
  assert.equal(steamStateLabel("pending_add"), "Waiting for Steam to close");
  assert.equal(steamStateLabel("pending_update"), "Waiting for Steam to close");
  assert.equal(steamStateLabel("pending_removal"), "Waiting to be removed");
  assert.equal(steamStateLabel("other"), "Not in Steam");
});

test("game control shows on Linux for non-wishlisted games", () => {
  assert.equal(showSteamGameControl("linux", { success: true, reason: "" }), true);
  assert.equal(showSteamGameControl("linux", { success: true, reason: "missing_file" }), true);
  assert.equal(showSteamGameControl("linux", { success: true, reason: "wishlisted" }), false);
  assert.equal(showSteamGameControl("linux", {}), false);
  assert.equal(showSteamGameControl("windows", { success: true }), false);
  assert.match(steamIneligibleNote("missing_file"), /not found/);
  assert.equal(steamIneligibleNote(""), "");
});
