// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  createOnboardingController,
  importActionLabel,
  type OnboardingApiClient,
  type PendingImport,
} from "../utils/onboarding.ts";
import type { OnboardingFinishStatus, OnboardingStateResponse, OnboardingStepId } from "../services/api.ts";

function makeMockClient(initial: OnboardingStateResponse = { success: true, status: "", step: "", catalog_held: true }) {
  const calls: Array<{ name: string; arg?: unknown }> = [];
  const client: OnboardingApiClient = {
    async getOnboardingState() {
      calls.push({ name: "getOnboardingState" });
      return initial;
    },
    async setOnboardingStep(step: OnboardingStepId) {
      calls.push({ name: "setOnboardingStep", arg: step });
      return { success: true, step };
    },
    async finishOnboarding(status: OnboardingFinishStatus) {
      calls.push({ name: "finishOnboarding", arg: status });
      return { success: true, status, catalog_started: true };
    },
    async releaseCatalogHold() {
      calls.push({ name: "releaseCatalogHold" });
      return { success: true, released: true, started: true };
    },
    async getSettings() {
      return { catalog_enabled: true, lc_enabled: false };
    },
    async getCatalogStatus() {
      return { success: true, enabled: true, installed: false };
    },
    async getExtensionPairing() {
      return { success: true, paired: [{ id: "ABCD-12", browser: "Firefox", origin: "moz-extension://x" }] };
    },
  };
  return { client, calls };
}

test("first-run saves steps, releases catalog hold on Next from catalog, and calls finishOnboarding", async () => {
  const { client, calls } = makeMockClient({ success: true, status: "", step: "", catalog_held: true });
  const onboarding = createOnboardingController(client);

  assert.equal(await onboarding.start(), true);
  assert.equal(onboarding.open.value, true);
  assert.equal(onboarding.mode.value, "first-run");
  assert.equal(onboarding.step.value, "welcome");

  // Back on Welcome is a no-op and does not close or write.
  assert.equal(await onboarding.back(), false);
  assert.equal(onboarding.open.value, true);
  assert.equal(onboarding.step.value, "welcome");

  // Welcome -> Catalog
  assert.equal(await onboarding.next(), true);
  assert.equal(onboarding.step.value, "catalog");
  assert.equal(onboarding.direction.value, "forward");
  assert.deepEqual(calls.filter((c) => c.name === "releaseCatalogHold"), []);

  // Catalog -> LC releases the catalog hold
  assert.equal(await onboarding.next(), true);
  assert.equal(onboarding.step.value, "lc");
  assert.equal(calls.filter((c) => c.name === "releaseCatalogHold").length, 1);

  // Back from LC -> Catalog
  assert.equal(await onboarding.back(), true);
  assert.equal(onboarding.step.value, "catalog");
  assert.equal(onboarding.direction.value, "backward");

  // Finish saves done and closes
  assert.equal(await onboarding.finish(), true);
  assert.equal(onboarding.open.value, false);
  assert.deepEqual(
    calls.filter((c) => c.name === "finishOnboarding"),
    [{ name: "finishOnboarding", arg: "done" }],
  );
});

test("skip calls finishOnboarding('skipped') and drains queued extension requests in FIFO order", async () => {
  const { client, calls } = makeMockClient();
  const onboarding = createOnboardingController(client);
  await onboarding.start();

  const order: string[] = [];
  onboarding.enqueueExtensionRequest(() => {
    order.push("first");
  });
  onboarding.enqueueExtensionRequest(async () => {
    await Promise.resolve();
    order.push("second");
  });
  onboarding.enqueueExtensionRequest(() => {
    order.push("third");
  });

  // Busy blocks skip/next/back
  onboarding.setBusy(true);
  assert.equal(await onboarding.next(), false);
  assert.equal(await onboarding.back(), false);
  assert.equal(await onboarding.skip(), false);
  assert.equal(onboarding.open.value, true);
  assert.deepEqual(order, []);

  onboarding.setBusy(false);
  assert.equal(await onboarding.skip(), true);
  assert.equal(onboarding.open.value, false);
  assert.deepEqual(order, ["first", "second", "third"]);
  assert.deepEqual(
    calls.filter((c) => c.name === "finishOnboarding"),
    [{ name: "finishOnboarding", arg: "skipped" }],
  );
});

test("replay mode opens on welcome with current state and writes nothing to onboarding API", async () => {
  const { client, calls } = makeMockClient({ success: true, status: "done", step: "", catalog_held: false });
  const onboarding = createOnboardingController(client);

  // Already finished -> start() does not open
  assert.equal(await onboarding.start(), false);
  assert.equal(onboarding.open.value, false);

  calls.length = 0;
  await onboarding.replay();
  assert.equal(onboarding.open.value, true);
  assert.equal(onboarding.mode.value, "replay");
  assert.equal(onboarding.step.value, "welcome");
  assert.deepEqual(onboarding.pairedBrowsers.value, ["Firefox"]);

  await onboarding.next(); // catalog
  await onboarding.next(); // lc
  await onboarding.back(); // catalog
  await onboarding.skip();
  assert.equal(onboarding.open.value, false);

  const writeCalls = calls.filter((c) =>
    ["setOnboardingStep", "finishOnboarding", "releaseCatalogHold"].includes(c.name),
  );
  assert.deepEqual(writeCalls, []);
});

test("a second Next while the catalog hold release is pending does not skip a step", async () => {
  const { client, calls } = makeMockClient({ success: true, status: "", step: "catalog", catalog_held: true });
  let release: () => void = () => {};
  client.releaseCatalogHold = () => {
    calls.push({ name: "releaseCatalogHold" });
    return new Promise((resolve) => { release = () => resolve({ success: true, released: true, started: true }); });
  };
  const onboarding = createOnboardingController(client);
  assert.equal(await onboarding.start(), true);
  assert.equal(onboarding.step.value, "catalog");

  const first = onboarding.next();
  assert.equal(await onboarding.next(), false);
  assert.equal(await onboarding.back(), false);
  assert.equal(await onboarding.skip(), false);
  release();
  assert.equal(await first, true);
  assert.equal(onboarding.step.value, "lc");
  assert.equal(calls.filter((c) => c.name === "releaseCatalogHold").length, 1);
  assert.equal(onboarding.open.value, true);
});

test("Done twice finishes once", async () => {
  const { client, calls } = makeMockClient({ success: true, status: "", step: "done", catalog_held: false });
  const onboarding = createOnboardingController(client);
  assert.equal(await onboarding.start(), true);
  const [a, b] = await Promise.all([onboarding.finish(), onboarding.finish()]);
  assert.deepEqual([a, b], [true, false]);
  assert.equal(calls.filter((c) => c.name === "finishOnboarding").length, 1);
});

test("a waiting import blocks Next; advance runs it and stays on the step", async () => {
  const { client } = makeMockClient({ success: true, status: "", step: "import", catalog_held: false });
  const onboarding = createOnboardingController(client);
  assert.equal(await onboarding.start(), true);
  assert.equal(onboarding.step.value, "import");

  let runs = 0;
  const pending: PendingImport = {
    count: 3,
    run: async () => {
      runs++;
      // The panel sets busy while it imports and clears the request on success.
      onboarding.setBusy(true);
      await Promise.resolve();
      onboarding.recordImport({ success: true, created: 3, updated: 0 });
      onboarding.setPendingImport(null);
      onboarding.setBusy(false);
      return true;
    },
  };
  onboarding.setPendingImport(pending);

  // Plain Next must not drop the preview.
  assert.equal(await onboarding.next(), false);
  assert.equal(onboarding.step.value, "import");

  // advance() (footer primary and RB) runs the import and stays on the step.
  assert.equal(await onboarding.advance(), true);
  assert.equal(runs, 1);
  assert.equal(onboarding.step.value, "import");
  assert.equal(onboarding.importedCount.value, 3);
  assert.equal(onboarding.pendingImport.value, null);

  // With no waiting import, advance() moves on like Next.
  assert.equal(await onboarding.advance(), true);
  assert.equal(onboarding.step.value, "extension");
});

test("advance does nothing while busy, and a failed import keeps the preview", async () => {
  const { client } = makeMockClient({ success: true, status: "", step: "import", catalog_held: false });
  const onboarding = createOnboardingController(client);
  await onboarding.start();
  let runs = 0;
  onboarding.setPendingImport({ count: 2, run: async () => { runs++; return false; } });

  onboarding.setBusy(true);
  assert.equal(await onboarding.advance(), false);
  assert.equal(runs, 0);
  onboarding.setBusy(false);

  assert.equal(await onboarding.advance(), false);
  assert.equal(runs, 1);
  assert.equal(onboarding.step.value, "import");
  assert.notEqual(onboarding.pendingImport.value, null);
  assert.equal(onboarding.importedCount.value, 0);
});

test("skipImport drops the preview and moves on without importing", async () => {
  const { client, calls } = makeMockClient({ success: true, status: "", step: "import", catalog_held: false });
  const onboarding = createOnboardingController(client);
  await onboarding.start();
  let runs = 0;
  onboarding.setPendingImport({ count: 5, run: async () => { runs++; return true; } });

  assert.equal(await onboarding.skipImport(), true);
  assert.equal(runs, 0);
  assert.equal(onboarding.step.value, "extension");
  assert.equal(onboarding.pendingImport.value, null);
  assert.equal(onboarding.importedCount.value, 0);
  assert.ok(calls.some((c) => c.name === "setOnboardingStep" && c.arg === "extension"));

  // skipImport is only for the import step.
  assert.equal(await onboarding.skipImport(), false);
  assert.equal(onboarding.step.value, "extension");
});

test("Back from the import step clears the waiting import", async () => {
  const { client } = makeMockClient({ success: true, status: "", step: "import", catalog_held: false });
  const onboarding = createOnboardingController(client);
  await onboarding.start();
  onboarding.setPendingImport({ count: 1, run: async () => true });
  assert.equal(await onboarding.back(), true);
  assert.equal(onboarding.step.value, "lc");
  assert.equal(onboarding.pendingImport.value, null);
  // Next from LC is not blocked.
  assert.equal(await onboarding.next(), true);
  assert.equal(onboarding.step.value, "import");
});

test("importActionLabel counts games", () => {
  assert.equal(importActionLabel(1), "Import 1 game");
  assert.equal(importActionLabel(146), "Import 146 games");
});

test("goTo jumps back to an earlier step, saves it, and Next walks forward again", async () => {
  const { client, calls } = makeMockClient({ success: true, status: "", step: "done", catalog_held: false });
  const onboarding = createOnboardingController(client);
  assert.equal(await onboarding.start(), true);
  assert.equal(onboarding.step.value, "done");

  // A forward jump, or a jump to the same step, does nothing.
  assert.equal(await onboarding.goTo("done"), false);

  assert.equal(await onboarding.goTo("import"), true);
  assert.equal(onboarding.step.value, "import");
  assert.equal(onboarding.direction.value, "backward");
  assert.deepEqual(calls.filter((c) => c.name === "setOnboardingStep").at(-1), { name: "setOnboardingStep", arg: "import" });
  assert.equal(await onboarding.goTo("extension"), false);
  assert.equal(onboarding.step.value, "import");

  assert.equal(await onboarding.next(), true);
  assert.equal(onboarding.step.value, "extension");
  assert.equal(onboarding.direction.value, "forward");
  assert.equal(await onboarding.next(), true);
  assert.equal(onboarding.step.value, "done");
  // Passing the import step on the way back does not touch the catalog hold.
  assert.equal(calls.filter((c) => c.name === "releaseCatalogHold").length, 0);
});

test("goTo does nothing while busy, clears a waiting import, and saves nothing in replay", async () => {
  const { client } = makeMockClient({ success: true, status: "", step: "import", catalog_held: false });
  const onboarding = createOnboardingController(client);
  await onboarding.start();
  onboarding.setPendingImport({ count: 2, run: async () => true });

  onboarding.setBusy(true);
  assert.equal(await onboarding.goTo("welcome"), false);
  assert.equal(onboarding.step.value, "import");
  onboarding.setBusy(false);

  assert.equal(await onboarding.goTo("catalog"), true);
  assert.equal(onboarding.pendingImport.value, null);

  const replay = makeMockClient();
  const replayed = createOnboardingController(replay.client);
  await replayed.replay();
  await replayed.next();
  await replayed.next();
  assert.equal(await replayed.goTo("welcome"), true);
  assert.equal(replayed.step.value, "welcome");
  assert.equal(replay.calls.filter((c) => c.name === "setOnboardingStep").length, 0);
});
