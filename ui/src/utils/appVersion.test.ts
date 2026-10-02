// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import test from "node:test";

import { isNewerVersion } from "./appVersion.ts";

test("isNewerVersion only reports strictly newer releases", () => {
  assert.equal(isNewerVersion("v0.4.0", "v0.3.5"), true);
  assert.equal(isNewerVersion("v0.3.10", "v0.3.9"), true);
  assert.equal(isNewerVersion("v1.0", "v0.9.9"), true);
  assert.equal(isNewerVersion("v0.3.5", "v0.3.5"), false);
  assert.equal(isNewerVersion("v0.3.5", "v0.4.0"), false);
  assert.equal(isNewerVersion("0.3.5", "v0.3.5"), false);
  assert.equal(isNewerVersion("v0.3.5", ""), true);
});
