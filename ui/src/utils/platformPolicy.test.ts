// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import test from "node:test";

import { conservativePlatformCapabilities } from "./platformPolicy.ts";

test("capability fallback exposes no platform-specific mutation actions", () => {
  const capabilities = conservativePlatformCapabilities();

  assert.equal(capabilities.platform, "unsupported");
  assert.equal(capabilities.wine_proton, false);
  assert.equal(capabilities.runtime_installers, false);
  assert.equal(capabilities.wayland, false);
  assert.equal(capabilities.rpgmaker_linux, false);
  assert.equal(capabilities.cheat_engine_injection, false);
  assert.deepEqual(capabilities.launch_modes, ["auto"]);
});

test("capability fallback returns an isolated launch mode list", () => {
  const first = conservativePlatformCapabilities();
  first.launch_modes.push("native");

  assert.deepEqual(conservativePlatformCapabilities().launch_modes, ["auto"]);
});
