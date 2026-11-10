// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import test from "node:test";
import { createGamesStore, nextRenderLimit, reconcileGames, RENDER_BATCH } from "./gamesStore.ts";

interface Game { id: number; title: string; tags?: string[] }

const library = (): Game[] => [
  { id: 1, title: "Alpha", tags: ["a"] },
  { id: 2, title: "Beta" },
];

test("unchanged games keep their object and changed ones are replaced", () => {
  const snapshots = new WeakMap<object, string>();
  const first = reconcileGames<Game>([], library(), snapshots);
  const changed = library();
  changed[1].title = "Beta 2";
  const second = reconcileGames(first, changed, snapshots);
  assert.equal(second[0], first[0]);
  assert.notEqual(second[1], first[1]);
  assert.equal(second[1].title, "Beta 2");
});

test("refresh calls in one tick share one load", async () => {
  let loads = 0;
  const store = createGamesStore<Game>(async () => { loads++; return library(); });
  const results = await Promise.all([store.refresh(), store.refresh(), store.refresh()]);
  assert.deepEqual(results, [true, true, true]);
  assert.equal(loads, 1);
  assert.equal(store.loaded.value, true);
  assert.equal(store.games.value.length, 2);
});

test("a refresh during a load gets one more load after it", async () => {
  let loads = 0;
  let release: () => void = () => {};
  const store = createGamesStore<Game>(async () => {
    loads++;
    if (loads === 1) await new Promise<void>((resolve) => { release = resolve; });
    return [{ id: 1, title: `load ${loads}` }];
  });
  const first = store.refresh();
  await Promise.resolve();
  await Promise.resolve();
  const second = store.refresh();
  const third = store.refresh();
  assert.equal(second, third);
  release();
  await Promise.all([first, second]);
  assert.equal(loads, 2);
  assert.equal(store.games.value[0].title, "load 2");
});

test("an identical reload keeps the same array, so nothing renders again", async () => {
  const store = createGamesStore<Game>(async () => library());
  await store.refresh();
  const before = store.games.value;
  await store.refresh();
  assert.equal(store.games.value, before);
});

test("a failed load keeps the old list and reports false", async () => {
  let fail = false;
  const store = createGamesStore<Game>(async () => { if (fail) throw new Error("down"); return library(); });
  await store.refresh();
  fail = true;
  const originalError = console.error;
  console.error = () => {};
  try {
    assert.equal(await store.refresh(), false);
  } finally {
    console.error = originalError;
  }
  assert.equal(store.games.value.length, 2);
});

test("patch replaces one game object and the next load restores server data", async () => {
  const store = createGamesStore<Game>(async () => library());
  await store.refresh();
  const [alpha, beta] = store.games.value;
  store.patch(1, { title: "Local" });
  assert.equal(store.games.value[0].title, "Local");
  assert.notEqual(store.games.value[0], alpha);
  assert.equal(store.games.value[1], beta);
  await store.refresh();
  assert.equal(store.games.value[0].title, "Alpha");
});

test("the loaded callback gets the current list", async () => {
  const seen: number[][] = [];
  const store = createGamesStore<Game>(async () => library(), (games) => seen.push(games.map((game) => game.id)));
  await store.refresh();
  assert.deepEqual(seen, [[1, 2]]);
});

test("the render limit grows only near the end and never passes the total", () => {
  assert.equal(nextRenderLimit(RENDER_BATCH, 1000, 5000, 800), RENDER_BATCH);
  assert.equal(nextRenderLimit(RENDER_BATCH, 1000, 1000, 800), RENDER_BATCH * 2);
  assert.equal(nextRenderLimit(RENDER_BATCH, RENDER_BATCH + 5, 0, 800), RENDER_BATCH + 5);
  assert.equal(nextRenderLimit(50, 50, 0, 800), 50);
});
