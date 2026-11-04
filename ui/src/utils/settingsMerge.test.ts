// SPDX-License-Identifier: GPL-3.0-or-later
import { test } from "node:test";
import assert from "node:assert/strict";
import { mergeSettingsFields } from "./settingsMerge.ts";

test("preserves focused and subsequently edited fields while applying persisted values", () => {
  assert.deepEqual(mergeSettingsFields(
    { focused: "normalized", edited: "server", other: "server" },
    { focused: "typing", edited: "new edit", other: "before" },
    { focused: "typing", edited: "before", other: "before" },
    "focused",
  ), { focused: "typing", edited: "new edit", other: "server" });
});
