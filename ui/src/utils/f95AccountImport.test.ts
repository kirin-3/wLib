// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test, { mock } from "node:test";

import { MockF95Account } from "../services/mock/f95Account.ts";
import type { F95AccountReadStatus } from "../services/api.ts";
import type { F95AccountClient } from "./f95AccountImport.ts";
import {
  F95AccountImport,
  createF95AccountPolling,
  f95AccountErrorMessage,
  f95AccountPreviewNote,
  f95AccountProgressText,
  f95AccountResultText,
} from "./f95AccountImport.ts";

const store = new Map<string, string>();
(globalThis as { localStorage?: unknown }).localStorage = {
  getItem: (key: string) => store.get(key) ?? null,
  setItem: (key: string, value: string) => void store.set(key, value),
  removeItem: (key: string) => void store.delete(key),
};

const mockClient = (mode = ""): F95AccountClient => {
  if (mode) store.set("wlib-mock-f95-account", mode);
  else store.delete("wlib-mock-f95-account");
  const mock = new MockF95Account(1);
  const call = async <T>(method: string, ...args: unknown[]) => mock.respond(method, args) as T;
  return {
    startF95AccountRead: () => call("start_f95_account_read"),
    getF95AccountReadStatus: () => call("get_f95_account_read_status"),
    cancelF95AccountRead: () => call("cancel_f95_account_read"),
    importF95AccountLists: (readId: string) => call("import_f95_account_lists", readId),
  };
};

const pollUntilDone = async (state: F95AccountImport, seen: string[] = []) => {
  for (let i = 0; i < 100; i += 1) {
    await new Promise((resolve) => setTimeout(resolve, 3));
    const running = await state.poll();
    seen.push(f95AccountProgressText(state.progress));
    if (!running) return;
  }
  assert.fail("read did not finish");
};

test("the tab reads, previews and imports against the mocks", async () => {
  const state = new F95AccountImport(mockClient());
  assert.equal(await state.start(), true);
  assert.equal(state.phase, "reading");
  assert.equal(state.busy, true);
  const seen: string[] = [];
  await pollUntilDone(state, seen);
  assert.equal(seen.at(-1), "Bookmarks, page 1. Found 28 of 28 games.");
  assert.equal(state.phase, "preview");
  assert.deepEqual(state.preview?.lists.watched, { found: 25, new: 21, existing: 4 });
  assert.equal(state.preview?.total, 27);
  assert.equal(f95AccountPreviewNote(state.preview), "");
  assert.equal(state.canImport, true);
  assert.equal(await state.importLists(), true);
  assert.equal(state.phase, "done");
  assert.equal(f95AccountResultText(state.result ?? {}), "23 added, 4 tagged");
});

test("a preview with missing thread tags gives the count and ways to fill them", async () => {
  const state = new F95AccountImport(mockClient("missing_tags"));
  await state.start();
  await pollUntilDone(state);
  assert.match(f95AccountPreviewNote(state.preview), /6 new games have no F95 tags yet/);
  assert.match(f95AccountPreviewNote(state.preview), /catalog or an update check/);
  assert.match(f95AccountPreviewNote(state.preview), /Maintenance > "Refresh F95 info"/);
  assert.equal(await state.importLists(), true);
  assert.equal(state.result?.without_tags, 6);
});

test("a preview names the disabled catalog", async () => {
  const state = new F95AccountImport(mockClient("catalog_off"));
  await state.start();
  await pollUntilDone(state);
  assert.match(f95AccountPreviewNote(state.preview), /23 new games have no F95 tags yet/);
  assert.match(f95AccountPreviewNote(state.preview), /The game catalog is off/);
});

test("the result appends the missing-tag count only when positive", () => {
  assert.equal(
    f95AccountResultText({ added: 7, tagged: 5, without_tags: 4 }),
    '7 added, 5 tagged. 4 games have no F95 tags yet. Use Maintenance > "Refresh F95 info" in the library to add them now.',
  );
  assert.match(f95AccountResultText({ added: 1, without_tags: 1 }), /1 game has no F95 tags yet/);
  assert.equal(f95AccountResultText({ added: 7, tagged: 5, without_tags: 0 }), "7 added, 5 tagged");
  assert.equal(f95AccountResultText({ added: 7, tagged: 5 }), "7 added, 5 tagged");
});

test("a failed save keeps the preview so Import can be retried", async () => {
  const client = mockClient();
  let failNext = true;
  const importOnce = client.importF95AccountLists;
  client.importF95AccountLists = async (readId: string) => {
    if (!failNext) return importOnce(readId);
    failNext = false;
    return { success: false, error: "database is locked", error_code: "import_failed" };
  };
  const state = new F95AccountImport(client);
  assert.equal(await state.start(), true);
  await pollUntilDone(state);
  assert.equal(await state.importLists(), false);
  assert.equal(state.phase, "preview");
  assert.equal(state.canImport, true);
  assert.match(state.error, /library was not changed/);
  assert.equal(await state.importLists(), true);
  assert.equal(state.phase, "done");
  assert.equal(state.error, "");
});

test("cancel stops the read", async () => {
  const state = new F95AccountImport(mockClient());
  await state.start();
  await state.cancel();
  assert.equal(await state.poll(), false);
  assert.equal(state.phase, "cancelled");
  assert.equal(state.canImport, false);
});

test("a remounted tab reattaches to a running read and then shows its preview", async () => {
  const client = mockClient();
  const first = new F95AccountImport(client);
  assert.equal(await first.start(), true);
  // The user leaves the page; a new view starts idle against the same backend read.
  const second = new F95AccountImport(client);
  assert.equal(await second.attach(), true);
  assert.equal(second.phase, "reading");
  assert.equal(second.readId, first.readId);
  await pollUntilDone(second);
  assert.equal(second.phase, "preview");
  assert.equal(second.canImport, true);
  // Leaving and returning again shows the held preview without a new read.
  const third = new F95AccountImport(client);
  assert.equal(await third.attach(), false);
  assert.equal(third.phase, "preview");
  assert.equal(third.readId, first.readId);
  assert.equal(await third.importLists(), true);
});

test("a remounted tab stays idle for idle, cancelled, expired, imported and failed reads", async () => {
  const statuses: F95AccountReadStatus[] = [
    { success: true, read_id: "", state: "idle", progress: {} },
    { success: false, read_id: "r1", state: "cancelled", error_code: "cancelled", error: "Cancelled." },
    { success: false, read_id: "r1", state: "expired", error_code: "expired", error: "Expired." },
    { success: true, read_id: "r1", state: "imported", progress: {} },
    { success: false, read_id: "r1", state: "error", error_code: "blocked", error: "Blocked." },
    // A finished read whose preview is gone is not shown as a preview.
    { success: true, read_id: "r1", state: "done", progress: {} },
  ];
  for (const status of statuses) {
    const client = { ...mockClient(), getF95AccountReadStatus: async () => status };
    const state = new F95AccountImport(client);
    assert.equal(await state.attach(), false, status.state);
    assert.equal(state.phase, "idle", status.state);
    assert.equal(state.preview, null, status.state);
    assert.equal(state.error, "", status.state);
  }
  const broken = new F95AccountImport({ ...mockClient(), getF95AccountReadStatus: async () => { throw new Error("gone"); } });
  assert.equal(await broken.attach(), false);
  assert.equal(broken.phase, "idle");
});

test("a late reattach answer never replaces a read the user started", async () => {
  let answer: (status: F95AccountReadStatus) => void = () => {};
  const client = mockClient();
  const getStatus = client.getF95AccountReadStatus;
  let first = true;
  client.getF95AccountReadStatus = () => {
    if (!first) return getStatus();
    first = false;
    return new Promise((resolve) => { answer = resolve; });
  };
  const state = new F95AccountImport(client);
  const attached = state.attach();
  assert.equal(await state.start(), true);
  answer({ success: true, read_id: "old", state: "done", progress: {}, preview: { lists: { watched: { found: 1, new: 1, existing: 0 }, bookmarks: { found: 0, new: 0, existing: 0 } }, total: 1, new: 1, existing: 0, entries: [] } });
  assert.equal(await attached, false);
  assert.equal(state.phase, "reading");
  assert.equal(state.preview, null);
});

test("after the view unmounts, an answer in flight schedules no more polls", async (t) => {
  mock.timers.enable({ apis: ["setTimeout"] });
  t.after(() => mock.timers.reset());
  let polls = 0;
  let answer: (running: boolean) => void = () => {};
  const account = {
    start: async () => true,
    attach: async () => true,
    poll: () => { polls += 1; return new Promise<boolean>((resolve) => { answer = resolve; }); },
  };
  const polling = createF95AccountPolling(account, 500);
  await polling.attach();
  mock.timers.tick(500);
  assert.equal(polls, 1);
  // The user leaves while the status call is in flight.
  polling.dispose();
  answer(true);
  await new Promise((resolve) => setImmediate(resolve));
  mock.timers.tick(5000);
  assert.equal(polls, 1);
  // A start that answers after unmount schedules nothing either.
  await polling.read();
  mock.timers.tick(5000);
  assert.equal(polls, 1);
});

test("one poll loop runs at a time", async (t) => {
  mock.timers.enable({ apis: ["setTimeout"] });
  t.after(() => mock.timers.reset());
  let polls = 0;
  const account = { start: async () => true, attach: async () => true, poll: async () => { polls += 1; return polls < 3; } };
  const polling = createF95AccountPolling(account, 500);
  await polling.attach();
  await polling.read();
  for (let i = 0; i < 10; i += 1) {
    mock.timers.tick(500);
    await new Promise((resolve) => setImmediate(resolve));
  }
  assert.equal(polls, 3);
  polling.dispose();
});

const errorCases: [string, RegExp][] = [
  ["no_extension", /Install or pair the wLib extension, keep the browser open/],
  ["extension_outdated", /Update the extension/],
  ["login_required", /Sign in to F95Zone in the browser/],
  ["blocked", /Open f95zone\.to in the browser/],
  ["not_a_list", /did not return a watched-threads list page/],
];

for (const [code, pattern] of errorCases) {
  test(`error ${code} names the fix`, async () => {
    const state = new F95AccountImport(mockClient(code));
    const running = await state.start();
    if (running) await pollUntilDone(state);
    assert.equal(state.phase, "error");
    assert.equal(state.errorCode, code);
    assert.match(state.error, pattern);
    assert.equal(state.canImport, false);
  });
}

test("empty lists show a message and no Import", async () => {
  const state = new F95AccountImport(mockClient("empty"));
  await state.start();
  await pollUntilDone(state);
  assert.equal(state.phase, "preview");
  assert.equal(state.emptyLists, true);
  assert.equal(state.canImport, false);
  assert.match(f95AccountErrorMessage("empty"), /No watched or bookmarked games were found/);
  assert.equal(await state.importLists(), false);
});

test("unknown errors keep the backend text", () => {
  assert.equal(f95AccountErrorMessage("other", "Backend text"), "Backend text");
  assert.equal(f95AccountProgressText({}), "Starting...");
  assert.equal(f95AccountProgressText({ list_label: "Bookmarks", page: 1, found: 3, total: null }), "Bookmarks, page 1. Found 3 games.");
});

test("the tab styles use only theme tokens, so light and dark themes both work", () => {
  const source = readFileSync(new URL("../views/LibraryBackupView.vue", import.meta.url), "utf8");
  const style = source.slice(source.indexOf("<style scoped>"));
  const rules = style.split("}").filter((rule) => rule.includes(".f95-account"));
  assert.match(style, /\.f95-account-note\s*\{\s*color: var\(--text-secondary\)/);
  assert.ok(rules.length > 0);
  for (const rule of rules) {
    assert.doesNotMatch(rule, /#[0-9a-f]{3,8}\b|rgb\(|hsl\(/i, rule);
  }
});
