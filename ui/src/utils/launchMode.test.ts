// SPDX-License-Identifier: GPL-3.0-or-later
import test from "node:test";
import assert from "node:assert/strict";

import {
  LAUNCH_MODE_OPTIONS,
  getLaunchModeOptions,
  normalizeLaunchMode,
  resolveLaunchRuntimeOverrides,
  usesWineProtonControls,
} from "./launchMode.ts";

test("launch mode options include user-facing runtime choices", () => {
  assert.deepEqual(
    LAUNCH_MODE_OPTIONS.map((option) => option.value),
    ["auto", "native", "wine_proton", "rpgmaker_linux"],
  );
});

test("normalizeLaunchMode defaults unsupported values to auto", () => {
  assert.equal(normalizeLaunchMode("native"), "native");
  assert.equal(normalizeLaunchMode("wine_proton"), "wine_proton");
  assert.equal(normalizeLaunchMode("rpgmaker_linux"), "rpgmaker_linux");
  assert.equal(normalizeLaunchMode(""), "auto");
  assert.equal(normalizeLaunchMode("unknown"), "auto");
  assert.equal(normalizeLaunchMode(null), "auto");
});

test("host-native modes hide Wine and Proton controls", () => {
  assert.equal(usesWineProtonControls("native"), false);
  assert.equal(usesWineProtonControls("rpgmaker_linux"), false);
  assert.equal(usesWineProtonControls("auto"), true);
  assert.equal(usesWineProtonControls("wine_proton"), true);
  assert.equal(usesWineProtonControls("unsupported"), true);
});

test("Windows only offers auto detect", () => {
  const options = getLaunchModeOptions(
    { launch_modes: ["auto"], rpgmaker_linux: false },
    false,
  );

  assert.deepEqual(options.map((option) => option.value), ["auto"]);
});

test("an imported unsupported mode remains visible and unavailable", () => {
  const options = getLaunchModeOptions(
    { launch_modes: ["auto"], rpgmaker_linux: false },
    false,
    "wine_proton",
  );

  assert.deepEqual(options.map((option) => option.value), ["auto", "wine_proton"]);
  assert.match(options[1]?.label ?? "", /Unavailable on this platform/);
});

test("Linux runner mode requires an available runner", () => {
  const capabilities = {
    launch_modes: ["auto", "native", "wine_proton", "rpgmaker_linux"] as const,
    rpgmaker_linux: true,
  };

  assert.equal(
    getLaunchModeOptions(capabilities, false).some(
      (option) => option.value === "rpgmaker_linux",
    ),
    false,
  );
  assert.equal(
    getLaunchModeOptions(capabilities, true).some(
      (option) => option.value === "rpgmaker_linux",
    ),
    true,
  );
});

test("Linux immediate launch clears disabled Wine and Proton overrides", () => {
  const overrides = resolveLaunchRuntimeOverrides({
    wineProtonSupported: true,
    usesWineProtonRuntime: true,
    useCustomPrefix: false,
    customPrefix: "/new/prefix",
    protonVersion: "/new/proton",
    storedCustomPrefix: "/old/prefix",
    storedProtonVersion: "/old/proton",
  });

  assert.deepEqual(overrides, { custom_prefix: "", proton_version: "" });
});

test("unsupported hosts preserve stored Wine and Proton overrides", () => {
  const overrides = resolveLaunchRuntimeOverrides({
    wineProtonSupported: false,
    usesWineProtonRuntime: false,
    useCustomPrefix: false,
    customPrefix: "",
    protonVersion: "",
    storedCustomPrefix: "/old/prefix",
    storedProtonVersion: "/old/proton",
  });

  assert.deepEqual(overrides, {
    custom_prefix: "/old/prefix",
    proton_version: "/old/proton",
  });
});
