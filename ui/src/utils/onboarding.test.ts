// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  ONBOARDING_STEPS,
  buildDoneSummary,
  nextStep,
  normalizeStep,
  prevStep,
  stepAnnouncement,
  stepNumber,
} from "./onboarding.ts";

test("ONBOARDING_STEPS defines the six steps in the fixed order", () => {
  assert.deepEqual(
    ONBOARDING_STEPS.map((step) => step.id),
    ["welcome", "catalog", "lc", "import", "extension", "done"],
  );
  assert.deepEqual(
    ONBOARDING_STEPS.map((step) => step.label),
    [
      "Welcome",
      "Game catalog",
      "LewdCorner",
      "Import from other apps",
      "Browser extension",
      "Done",
    ],
  );
});

test("normalizeStep validates known steps and defaults unknown values to welcome", () => {
  assert.equal(normalizeStep("extension"), "extension");
  assert.equal(normalizeStep(" CATALOG "), "catalog");
  assert.equal(normalizeStep(""), "welcome");
  assert.equal(normalizeStep("unknown"), "welcome");
  assert.equal(normalizeStep(null), "welcome");
  assert.equal(normalizeStep(42), "welcome");
});

test("nextStep and prevStep move step by step and stop at the ends", () => {
  assert.equal(prevStep("welcome"), null);
  assert.equal(nextStep("welcome"), "catalog");
  assert.equal(nextStep("catalog"), "lc");
  assert.equal(nextStep("lc"), "import");
  assert.equal(nextStep("import"), "extension");
  assert.equal(nextStep("extension"), "done");
  assert.equal(nextStep("done"), null);
  assert.equal(prevStep("done"), "extension");
  assert.equal(prevStep("catalog"), "welcome");
});

test("stepNumber and stepAnnouncement format the live-region message", () => {
  assert.equal(stepNumber("welcome"), 1);
  assert.equal(stepNumber("done"), 6);
  assert.equal(stepAnnouncement("catalog"), "Step 2 of 6, Game catalog");
  assert.equal(stepAnnouncement("import"), "Step 4 of 6, Import from other apps");
});

test("buildDoneSummary reflects catalog, LewdCorner, import count and extension pairing", () => {
  assert.deepEqual(
    buildDoneSummary({
      catalogEnabled: true,
      lcEnabled: false,
      importedCount: 0,
      pairedBrowsers: [],
    }),
    [
      { id: "catalog", icon: "book", label: "Game catalog", value: "On", tone: "brand" },
      { id: "lc", icon: "world", label: "LewdCorner", value: "Off", tone: "muted" },
      { id: "import", icon: "import", label: "Import from other apps", value: "None imported", tone: "muted" },
      { id: "extension", icon: "puzzle", label: "Browser extension", value: "Not paired", tone: "muted" },
    ],
  );

  assert.deepEqual(
    buildDoneSummary({
      catalogEnabled: false,
      lcEnabled: true,
      importedCount: 12,
      pairedBrowsers: ["Firefox", "Firefox", "Chrome"],
    }),
    [
      { id: "catalog", icon: "book", label: "Game catalog", value: "Off", tone: "muted" },
      { id: "lc", icon: "world", label: "LewdCorner", value: "On", tone: "brand" },
      { id: "import", icon: "import", label: "Import from other apps", value: "12 games imported", tone: "success" },
      { id: "extension", icon: "puzzle", label: "Browser extension", value: "Paired with Firefox, Chrome", tone: "success" },
    ],
  );

  const single = buildDoneSummary({
    catalogEnabled: true,
    lcEnabled: true,
    importedCount: 1,
    pairedBrowsers: ["Chromium"],
  });
  assert.equal(single[2]?.value, "1 game imported");
  assert.equal(single[3]?.value, "Paired with Chromium");
});
