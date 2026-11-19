// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { MockF95Account } from "../services/mock/f95Account.ts";
import type { F95AccountClient } from "./f95AccountImport.ts";
import {
  F95AccountImport,
  f95AccountErrorMessage,
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
  assert.equal(state.canImport, true);
  assert.equal(await state.importLists(), true);
  assert.equal(state.phase, "done");
  assert.equal(f95AccountResultText(state.result ?? {}), "23 added, 4 tagged");
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
  assert.ok(rules.length > 0);
  for (const rule of rules) {
    assert.doesNotMatch(rule, /#[0-9a-f]{3,8}\b|rgb\(|hsl\(/i, rule);
  }
});
