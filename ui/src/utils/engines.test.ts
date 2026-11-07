// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import test from "node:test";
import { ENGINE_OPTIONS, isEngine } from "./engines.ts";

test("engine matching ignores case and spacing", () => {
  assert.equal(isEngine(" ren'py ", "Ren'Py"), true);
  assert.equal(isEngine("", "Unity"), false);
  assert.equal(isEngine(null, "Unity"), false);
  assert.ok(ENGINE_OPTIONS.includes("Others"));
});
