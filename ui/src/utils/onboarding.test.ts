// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  ONBOARDING_STEPS,
  buildDoneSummary,
  catalogStepCopy,
  doneHeadline,
  leftForLaterText,
  nextStep,
  normalizeStep,
  prevStep,
  railStepStates,
  stepAnnouncement,
  stepIsSet,
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
      { id: "catalog", icon: "book", label: "Game catalog", value: "On", tone: "brand", set: true, action: null, note: null },
      { id: "lc", icon: "world", label: "LewdCorner", value: "Off", tone: "muted", set: true, action: null, note: null },
      {
        id: "import", icon: "import", label: "Import from other apps", value: "None imported", tone: "muted",
        set: false, action: { step: "import", label: "Import games" }, note: null,
      },
      {
        id: "extension", icon: "puzzle", label: "Browser extension", value: "Not paired", tone: "muted",
        set: false, action: { step: "extension", label: "Pair now" }, note: null,
      },
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
      { id: "catalog", icon: "book", label: "Game catalog", value: "Off", tone: "muted", set: true, action: null, note: null },
      { id: "lc", icon: "world", label: "LewdCorner", value: "On", tone: "brand", set: true, action: null, note: null },
      {
        id: "import", icon: "import", label: "Import from other apps", value: "12 games imported", tone: "success",
        set: true, action: null, note: null,
      },
      {
        id: "extension", icon: "puzzle", label: "Browser extension", value: "Paired with Firefox, Chrome", tone: "success",
        set: true, action: null, note: null,
      },
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

test("buildDoneSummary in Gaming Mode gives no Pair now button for the extension", () => {
  const rows = buildDoneSummary({
    catalogEnabled: true,
    lcEnabled: false,
    importedCount: 0,
    pairedBrowsers: [],
    gamingMode: true,
  });
  const extension = rows.find((row) => row.id === "extension");
  assert.equal(extension?.action, null);
  assert.equal(extension?.note, "Later, in Desktop Mode");
  // Import still works in Gaming Mode.
  assert.deepEqual(rows.find((row) => row.id === "import")?.action, { step: "import", label: "Import games" });

  const paired = buildDoneSummary({
    catalogEnabled: true,
    lcEnabled: false,
    importedCount: 0,
    pairedBrowsers: ["Firefox"],
    gamingMode: true,
  }).find((row) => row.id === "extension");
  assert.equal(paired?.note, null);
  assert.equal(paired?.action, null);
});

test("stepIsSet: catalog and LewdCorner choices count; import and extension need a result", () => {
  const none = { importedCount: 0, pairedBrowsers: [] };
  const all = { importedCount: 2, pairedBrowsers: ["Chrome"] };
  for (const id of ["welcome", "catalog", "lc", "done"] as const) {
    assert.equal(stepIsSet(id, none), true, id);
  }
  assert.equal(stepIsSet("import", none), false);
  assert.equal(stepIsSet("extension", none), false);
  assert.equal(stepIsSet("import", all), true);
  assert.equal(stepIsSet("extension", all), true);
  // Blank browser names do not count as paired.
  assert.equal(stepIsSet("extension", { importedCount: 0, pairedBrowsers: ["  "] }), false);
});

test("import counts as set when the library has games, so a restart keeps it truthful", () => {
  // The run's import count resets to 0 on reload; the library size does not.
  const afterRestart = { importedCount: 0, pairedBrowsers: [], libraryCount: 3 };
  assert.equal(stepIsSet("import", afterRestart), true);
  assert.equal(stepIsSet("import", { ...afterRestart, libraryCount: 0 }), false);
  assert.equal(railStepStates("done", afterRestart).find((item) => item.id === "import")?.state, "done");
  assert.equal(doneHeadline(afterRestart).leftCount, 1);

  const rows = buildDoneSummary({ ...afterRestart, catalogEnabled: true, lcEnabled: false });
  assert.deepEqual(rows.find((row) => row.id === "import"), {
    id: "import", icon: "import", label: "Import from other apps", value: "3 games in your library", tone: "success",
    set: true, action: null, note: null,
  });
  const one = buildDoneSummary({ ...afterRestart, libraryCount: 1, catalogEnabled: true, lcEnabled: false });
  assert.equal(one.find((row) => row.id === "import")?.value, "1 game in your library");
  // This run's import count wins when there is one.
  const both = buildDoneSummary({ ...afterRestart, importedCount: 2, catalogEnabled: true, lcEnabled: false });
  assert.equal(both.find((row) => row.id === "import")?.value, "2 games imported");
});

test("railStepStates marks passed steps done or not set, and names each marker", () => {
  const states = (step: Parameters<typeof railStepStates>[0], input: Parameters<typeof railStepStates>[1]) =>
    railStepStates(step, input).map((item) => item.state);

  assert.deepEqual(states("welcome", { importedCount: 0, pairedBrowsers: [] }), [
    "current", "upcoming", "upcoming", "upcoming", "upcoming", "upcoming",
  ]);
  assert.deepEqual(states("extension", { importedCount: 0, pairedBrowsers: [] }), [
    "done", "done", "done", "not-set", "current", "upcoming",
  ]);
  assert.deepEqual(states("done", { importedCount: 0, pairedBrowsers: [] }), [
    "done", "done", "done", "not-set", "not-set", "current",
  ]);
  assert.deepEqual(states("done", { importedCount: 3, pairedBrowsers: ["Firefox"] }), [
    "done", "done", "done", "done", "done", "current",
  ]);
  assert.deepEqual(states("done", { importedCount: 3, pairedBrowsers: [] }), [
    "done", "done", "done", "done", "not-set", "current",
  ]);

  assert.deepEqual(
    railStepStates("done", { importedCount: 0, pairedBrowsers: ["Firefox"] }).map((item) => item.srLabel),
    [
      "Welcome, done",
      "Game catalog, done",
      "LewdCorner, done",
      "Import from other apps, not set",
      "Browser extension, done",
      "Done",
    ],
  );
  assert.equal(railStepStates("lc", { importedCount: 0, pairedBrowsers: [] })[3]?.srLabel, "Import from other apps, not started");
});

test("doneHeadline is confident only when everything is set and counts what is left", () => {
  assert.deepEqual(doneHeadline({ importedCount: 1, pairedBrowsers: ["Firefox"] }), {
    allSet: true,
    leftCount: 0,
    title: "You're all set",
    leftText: "",
    lead: "Here is a summary of your setup. You can change any of these choices at any time in Settings.",
  });
  assert.deepEqual(doneHeadline({ importedCount: 0, pairedBrowsers: [] }), {
    allSet: false,
    leftCount: 2,
    title: "Setup is finished",
    leftText: "2 things are left for later.",
    lead: "You can set them up now or at a later time.",
  });
  assert.deepEqual(doneHeadline({ importedCount: 5, pairedBrowsers: [] }), {
    allSet: false,
    leftCount: 1,
    title: "Setup is finished",
    leftText: "1 thing is left for later.",
    lead: "You can set it up now or at a later time.",
  });
  assert.equal(doneHeadline({ importedCount: 0, pairedBrowsers: ["Chrome"] }).leftCount, 1);
  assert.equal(leftForLaterText(0), "");
  assert.equal(leftForLaterText(3), "3 things are left for later.");
});

test("catalogStepCopy lists what the catalog gives when on", () => {
  const copy = catalogStepCopy(true, true);
  assert.equal(copy.heading, "What you get");
  assert.equal(copy.tone, "brand");
  assert.equal(copy.items.length, 4);
  assert.ok(copy.items.every((item) => !item.startsWith("No ")));
});

test("catalogStepCopy lists the same items as missing when off", () => {
  const on = catalogStepCopy(true, false);
  const off = catalogStepCopy(false, false);
  assert.equal(off.heading, "Without the catalog");
  assert.equal(off.tone, "muted");
  assert.equal(off.items.length, on.items.length);
  assert.ok(off.items.every((item) => item.startsWith("No ")));
  assert.match(off.items[0] ?? "", /Discover/);
});

test("catalogStepCopy names the deletion only when a local copy exists", () => {
  const installed = catalogStepCopy(true, true).help;
  const none = catalogStepCopy(true, false).help;
  assert.match(installed, /When off, wLib deletes its local copy\./);
  assert.doesNotMatch(none, /delete/i);
  assert.match(none, /When off, wLib does not download it\./);
  for (const help of [installed, none]) {
    assert.match(help, /^When on, wLib checks for a new catalog at most twice a day\./);
    assert.match(help, /Settings → Sources\.$/);
    assert.doesNotMatch(help, /deleted|!/);
  }
});
