// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import { test } from "node:test";
import { createServer } from "vite";

test("game saves preserve order, isolate games, and recover after failures", async () => {
  const calls = [];
  globalThis.window = {
    pywebview: { api: { update_game: (id, fields) => new Promise((resolve, reject) => {
      calls.push({ id, fields, resolve, reject });
    }) } },
    dispatchEvent() {},
  };
  const server = await createServer({ server: { middlewareMode: true }, appType: "custom" });
  try {
    const { api } = await server.ssrLoadModule("/src/services/api.ts");
    const first = api.updateGame(1, { is_favorite: 1 });
    const fields = { is_favorite: 0 };
    const second = api.updateGame(1, fields);
    fields.is_favorite = 1;
    const other = api.updateGame(2, { play_status: "Playing" });
    await new Promise(setImmediate);
    assert.deepEqual(calls.map(call => call.id), [1, 2]);
    calls[1].resolve({ success: true });
    await other;
    calls[0].resolve({ success: true });
    await first;
    await new Promise(setImmediate);
    assert.equal(calls[2].fields.is_favorite, 0);
    const third = api.updateGame(1, { user_rating: 5 });
    const rejected = assert.rejects(second, /offline/);
    calls[2].reject(new Error("offline"));
    await rejected;
    await new Promise(setImmediate);
    assert.equal(calls[3].fields.user_rating, 5);
    calls[3].resolve({ success: true });
    await third;
    assert.equal(api.gameSaves.size, 0);
  } finally {
    await server.close();
    delete globalThis.window;
  }
});
