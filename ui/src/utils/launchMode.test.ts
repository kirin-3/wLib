import test from "node:test";
import assert from "node:assert/strict";

import {
  LAUNCH_MODE_OPTIONS,
  normalizeLaunchMode,
  usesWineProtonControls,
} from "./launchMode.ts";

test("launch mode options include user-facing runtime choices", () => {
  assert.deepEqual(
    LAUNCH_MODE_OPTIONS.map((option) => option.value),
    ["auto", "native", "wine_proton"],
  );
});

test("normalizeLaunchMode defaults unsupported values to auto", () => {
  assert.equal(normalizeLaunchMode("native"), "native");
  assert.equal(normalizeLaunchMode("wine_proton"), "wine_proton");
  assert.equal(normalizeLaunchMode(""), "auto");
  assert.equal(normalizeLaunchMode("unknown"), "auto");
  assert.equal(normalizeLaunchMode(null), "auto");
});

test("native mode hides Wine and Proton controls", () => {
  assert.equal(usesWineProtonControls("native"), false);
  assert.equal(usesWineProtonControls("auto"), true);
  assert.equal(usesWineProtonControls("wine_proton"), true);
  assert.equal(usesWineProtonControls("unsupported"), true);
});
