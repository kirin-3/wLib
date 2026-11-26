// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import { test } from "node:test";
import { createOnboardingController, type OnboardingApiClient } from "../utils/onboarding.ts";
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
